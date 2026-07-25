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

// ============ TTL放置部屋掃除（v1.4） ============
const DAY_MS = 86400000;

test("createdAtは初回作成後に変更できない", async () => {
  await assertFails(room(db(HOST), "meta/createdAt").set(999999));
});

test("24時間以上経過した部屋は誰でも削除できる", async () => {
  // beforeEachのcreatedAt:1は十分に古い
  await assertSucceeds(room(db(SPECTATOR)).remove());
});

test("createdAtを持たない部屋（旧仕様のゴミ）は誰でも削除できる", async () => {
  await env.withSecurityRulesDisabled(async context => {
    await room(context.database(), "meta/createdAt").remove();
  });
  await assertSucceeds(room(db(SPECTATOR)).remove());
});

test("期限内の部屋は他人はもちろんホストもまるごと削除できない", async () => {
  await env.withSecurityRulesDisabled(async context => {
    await room(context.database(), "meta/createdAt").set(Date.now());
  });
  await assertFails(room(db(SPECTATOR)).remove());
  await assertFails(room(db(HOST)).remove());
});

test("未認証では期限切れ部屋も削除できない", async () => {
  await assertFails(room(db()).remove());
});

const INDEX_CODE = "XYZ789";
function index(database, code = INDEX_CODE) {
  return database.ref(`roomIndex/${code}`);
}

test("roomIndexは認証済みなら誰でも読める", async () => {
  await assertSucceeds(index(db(SPECTATOR)).get());
});

test("未認証ではroomIndexを読めない", async () => {
  await assertFails(index(db()).get());
});

test("roomIndexの新規登録は対応する部屋のホストだけが行える", async () => {
  await env.withSecurityRulesDisabled(async context => {
    await context.database().ref(`roomsV2/${INDEX_CODE}/meta`).set({ host: HOST, phase: "lobby", createdAt: Date.now() });
  });
  await assertFails(index(db(PLAYER2)).set(Date.now()));
  await assertSucceeds(index(db(HOST)).set(Date.now()));
});

test("roomIndexの期限切れエントリは誰でも削除できるが、期限内は削除できない", async () => {
  await env.withSecurityRulesDisabled(async context => {
    await index(context.database()).set(Date.now() - DAY_MS - 1000);
  });
  await assertSucceeds(index(db(SPECTATOR)).remove());

  await env.withSecurityRulesDisabled(async context => {
    await index(context.database()).set(Date.now());
  });
  await assertFails(index(db(SPECTATOR)).remove());
});

// ============ 席の引き継ぎ（v1.7） ============
const NEWUID = "newcomer-uid";
const OTHER_NEWUID = "other-newcomer-uid";
const TAKEOVER_REQ = { seat: 1, name: "Newcomer", ts: 1, prevUid: PLAYER2 };

test("申請者は自UID子ノードにのみ書ける（なりすまし申請を拒否）", async () => {
  await assertFails(room(db(NEWUID), `takeoverRequests/${OTHER_NEWUID}`).set(TAKEOVER_REQ));
  await assertSucceeds(room(db(NEWUID), `takeoverRequests/${NEWUID}`).set(TAKEOVER_REQ));
});

test("非ホスト・非申請者は承認/却下の書込（statusの変更）ができない", async () => {
  await env.withSecurityRulesDisabled(async context => {
    await room(context.database(), `takeoverRequests/${NEWUID}`).set(TAKEOVER_REQ);
  });
  await assertFails(room(db(PLAYER3), `takeoverRequests/${NEWUID}/status`).set("rejected"));
  await assertSucceeds(room(db(HOST), `takeoverRequests/${NEWUID}/status`).set("rejected"));
});

test("申請者本人は自分の申請ノードを削除できる", async () => {
  await env.withSecurityRulesDisabled(async context => {
    await room(context.database(), `takeoverRequests/${NEWUID}`).set(TAKEOVER_REQ);
  });
  await assertFails(room(db(PLAYER3), `takeoverRequests/${NEWUID}`).remove());
  await assertSucceeds(room(db(NEWUID), `takeoverRequests/${NEWUID}`).remove());
});

test("観戦者はtakeoverRequestsを読めない（自分の申請以外）", async () => {
  await env.withSecurityRulesDisabled(async context => {
    await room(context.database(), `takeoverRequests/${NEWUID}`).set(TAKEOVER_REQ);
  });
  await assertFails(room(db(SPECTATOR), "takeoverRequests").get());
  await assertFails(room(db(SPECTATOR), `takeoverRequests/${NEWUID}`).get());
  await assertSucceeds(room(db(NEWUID), `takeoverRequests/${NEWUID}`).get());
  await assertSucceeds(room(db(HOST), "takeoverRequests").get());
});

test("不正な申請データ（範囲外の席・余分なフィールド・prevUid欠落）を拒否する", async () => {
  await assertFails(room(db(NEWUID), `takeoverRequests/${NEWUID}`).set({ seat: 9, name: "X", ts: 1, prevUid: PLAYER2 }));
  await assertFails(room(db(NEWUID), `takeoverRequests/${NEWUID}`).set({ seat: 1, name: "X", ts: 1, prevUid: PLAYER2, evil: true }));
  await assertFails(room(db(NEWUID), `takeoverRequests/${NEWUID}`).set({ seat: 1, name: "X", ts: 1 }));
});

test("v1.7.1: 中断状態からの回復＝席は既に申請者UIDだが好みが旧UIDのままの場合、移行だけをやり直せる", async () => {
  await env.withSecurityRulesDisabled(async context => {
    // 「席transaction成功→好み移行」の間で中断された状態を再現：
    // 席は既に新UID、好みはまだ旧UID（PLAYER2）のまま、申請ノードも残っている。
    await room(context.database(), "seats/1").set({ uid: NEWUID, name: "Newcomer", lastSeen: 1, online: true });
    await room(context.database(), `takeoverRequests/${NEWUID}`).set(TAKEOVER_REQ);
  });
  // 席transactionを再実行せず、好み移行＋申請削除だけをupdateでやり直す（ホスト権限で許可されるか）
  const updates = {};
  updates[`privateRecipes/${NEWUID}`] = ["pair_火霜", "solo_星", "abs_影"];
  updates[`privateRecipes/${PLAYER2}`] = null;
  updates[`takeoverRequests/${NEWUID}`] = null;
  await assertSucceeds(room(db(HOST)).update(updates));
});

test("ホストは承認時に席UIDの差し替え・好みの移行・申請ノード削除を1回のupdateで行える", async () => {
  await env.withSecurityRulesDisabled(async context => {
    await room(context.database(), `takeoverRequests/${NEWUID}`).set(TAKEOVER_REQ);
    await room(context.database(), "seats/1/online").set(false); // 引き継ぎ対象はオフライン
  });
  const updates = {
    "seats/1": { uid: NEWUID, name: "Newcomer", lastSeen: 1, online: true },
  };
  await assertSucceeds(room(db(HOST), "seats/1").set(updates["seats/1"]));
  const recipeUpdates = {};
  recipeUpdates[`privateRecipes/${NEWUID}`] = ["pair_火霜", "solo_星", "abs_影"];
  recipeUpdates[`privateRecipes/${PLAYER2}`] = null;
  recipeUpdates[`takeoverRequests/${NEWUID}`] = null;
  await assertSucceeds(room(db(HOST)).update(recipeUpdates));
});

test("非ホストは席UIDの差し替え（引き継ぎ承認）ができない", async () => {
  await assertFails(room(db(PLAYER3), "seats/1").set({ uid: NEWUID, name: "Newcomer", lastSeen: 1, online: true }));
});
