import fs from "node:fs";
import test, { after, before, beforeEach } from "node:test";
import {
  assertFails,
  assertSucceeds,
  initializeTestEnvironment,
} from "@firebase/rules-unit-testing";

const PROJECT_ID = "witch-rules-test";
const CODE = "ABC234";
const HOST = "host-uid";
const PLAYER2 = "player-2-uid";
const PLAYER3 = "player-3-uid";
const SPECTATOR = "spectator-uid";
const VALID_RECIPES = ["pair_満星", "solo_火", "H_火2蛇無"];

let env;

function db(uid) {
  return uid ? env.authenticatedContext(uid).database() : env.unauthenticatedContext().database();
}

function room(database, path = "") {
  return database.ref(`roomsV2/${CODE}${path ? `/${path}` : ""}`);
}

function baseGame() {
  return {
    n: 3,
    rounds: 6,
    round: 1,
    cutter: 0,
    teamTotal: 0,
    advanced: false,
    phase: "cut",
    band: [0, 1, 2, 3, 4, 5, 0, 1, 2],
    chooseIdx: 0,
  };
}

before(async () => {
  env = await initializeTestEnvironment({
    projectId: PROJECT_ID,
    database: { rules: fs.readFileSync("firebase/witch_rules.json", "utf8") },
  });
});

beforeEach(async () => {
  await env.clearDatabase();
  await env.withSecurityRulesDisabled(async context => {
    await room(context.database()).set({
      meta: { host: HOST, phase: "playing", createdAt: 1 },
      seats: {
        0: { uid: HOST, name: "Host", lastSeen: 1, online: true },
        1: { uid: PLAYER2, name: "P2", lastSeen: 1, online: true },
        2: { uid: PLAYER3, name: "P3", lastSeen: 1, online: true },
      },
      privateRecipes: {
        [HOST]: VALID_RECIPES,
        [PLAYER2]: ["pair_火霜", "solo_星", "abs_影"],
        [PLAYER3]: ["pair_影蛇", "solo_霜", "abs_蛇"],
      },
      game: baseGame(),
    });
  });
});

after(async () => {
  if (env) await env.cleanup();
});

test("未認証ではroomsV2を読めない", async () => {
  await assertFails(room(db(), "meta").get());
});

test("未認証ではroomsV2へ書けない", async () => {
  await assertFails(room(db(), "meta/phase").set("ended"));
});

test("匿名認証済みユーザーは共有情報を読める", async () => {
  const spectator = db(SPECTATOR);
  await assertSucceeds(room(spectator, "meta").get());
  await assertSucceeds(room(spectator, "seats").get());
  await assertSucceeds(room(spectator, "game").get());
  await assertSucceeds(room(spectator, "reveal").get());
});

test("自席プレイヤーは自分のprivateRecipesを読める", async () => {
  await assertSucceeds(room(db(PLAYER2), `privateRecipes/${PLAYER2}`).get());
});

test("他プレイヤーのprivateRecipesは読めない", async () => {
  await assertFails(room(db(PLAYER2), `privateRecipes/${PLAYER3}`).get());
});

test("観戦者はprivateRecipesを読めない", async () => {
  await assertFails(room(db(SPECTATOR), `privateRecipes/${PLAYER2}`).get());
});

test("ホストは全privateRecipesを読める", async () => {
  await assertSucceeds(room(db(HOST), "privateRecipes").get());
});

test("観戦者はgameを書けない", async () => {
  await assertFails(room(db(SPECTATOR), "game/teamTotal").set(1));
});

test("着席プレイヤーは許可されたgame操作を書ける", async () => {
  await assertSucceeds(room(db(PLAYER2), "game/teamTotal").set(1));
});

test("開始とprivateRecipes配布はホストだけが行える", async () => {
  await env.withSecurityRulesDisabled(async context => {
    await room(context.database(), "game").remove();
    await room(context.database(), "privateRecipes").remove();
  });
  await assertFails(room(db(PLAYER2), "game").set(baseGame()));
  await assertSucceeds(room(db(HOST), "game").set(baseGame()));
  await assertFails(room(db(PLAYER2), `privateRecipes/${PLAYER2}`).set(VALID_RECIPES));
  await assertSucceeds(room(db(HOST), `privateRecipes/${PLAYER2}`).set(VALID_RECIPES));
});

test("空席への初回着席は本人UIDだけが行える", async () => {
  const seat = { uid: SPECTATOR, name: "Watcher", lastSeen: 1, online: true };
  await assertFails(room(db(PLAYER2), "seats/3").set(seat));
  await assertSucceeds(room(db(SPECTATOR), "seats/3").set(seat));
});

test("他人の席を上書きできない", async () => {
  await assertFails(room(db(PLAYER2), "seats/2/name").set("hijacked"));
});

test("revealは終了前に書けない", async () => {
  await assertFails(room(db(HOST), "reveal").set({ 0: VALID_RECIPES }));
});

test("revealはホストだけが終了後に書ける", async () => {
  await env.withSecurityRulesDisabled(async context => {
    await room(context.database(), "game/phase").set("end");
  });
  const reveal = {
    0: VALID_RECIPES,
    1: ["pair_火霜", "solo_星", "abs_影"],
    2: ["pair_影蛇", "solo_霜", "abs_蛇"],
  };
  await assertFails(room(db(SPECTATOR), "reveal").set(reveal));
  await assertSucceeds(room(db(HOST), "reveal").set(reveal));
});

test("不正な好みID・余分なフィールド・範囲外数値を拒否する", async () => {
  await assertFails(room(db(HOST), `privateRecipes/${PLAYER2}`).set(["pair_満星", "存在しない好み", "solo_火"]));
  await assertFails(room(db(HOST), `privateRecipes/${PLAYER2}`).set([...VALID_RECIPES, "abs_蛇"]));
  await assertFails(room(db(PLAYER2), "game/n").set(99));
  await assertFails(room(db(PLAYER2), "game/totals").set([1, 2, 3]));
});
