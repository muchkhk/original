import fs from "node:fs";
import test, { after, before, beforeEach } from "node:test";
import {
  assertFails,
  assertSucceeds,
  initializeTestEnvironment,
} from "@firebase/rules-unit-testing";

const PROJECT_ID = "seri-v2-test";
const CODE = "ABC234";
const HOST = "host-uid-0001";
const PLAYER2 = "player-uid-0002";
const PLAYER3 = "player-uid-0003";
const PLAYER4 = "player-uid-0004";
const SPECTATOR = "spectator-uid-0004";
let env;

function db(uid) {
  return uid ? env.authenticatedContext(uid).database() : env.unauthenticatedContext().database();
}

function room(database, path = "") {
  return database.ref(`seriRoomsV2/${CODE}${path ? `/${path}` : ""}`);
}

function meta(phase = "playing") {
  return {
    hostUid: HOST,
    phase,
    n: 3,
    rounds: 6,
    money: 30,
    nc: 3,
    rule: "current",
    version: "2.0",
    createdAt: 1,
  };
}

function game() {
  return {
    round: 0,
    phase: "auction",
    price: 0,
    curSeat: 1,
    starter: 0,
    live: "0,1,2",
    lastActionId: "",
  };
}

function privateData(uid, seat, lens, includeLots = false) {
  const data = {
    ownerUid: uid,
    seat,
    lens,
    see: { 0: 1, 1: 2, 2: 3, 3: 4, 4: 5, 5: 6 },
  };
  if (includeLots) {
    data.lots = {
      0: { 0: 1, 1: 2, 2: 3 },
      1: { 0: 2, 1: 3, 2: 4 },
      2: { 0: 3, 1: 4, 2: 5 },
      3: { 0: 4, 1: 5, 2: 6 },
      4: { 0: 5, 1: 6, 2: -2 },
      5: { 0: 6, 1: -2, 2: -1 },
    };
  }
  return data;
}

before(async () => {
  env = await initializeTestEnvironment({
    projectId: PROJECT_ID,
    database: { rules: fs.readFileSync("firebase/seri_v2_rules.json", "utf8") },
  });
});

beforeEach(async () => {
  await env.clearDatabase();
  await env.withSecurityRulesDisabled(async context => {
    await room(context.database()).set({
      meta: meta(),
      seats: {
        0: { uid: HOST, name: "Host", lastSeen: 1 },
        1: { uid: PLAYER2, name: "P2", lastSeen: 1 },
        2: { uid: PLAYER3, name: "P3", lastSeen: 1 },
      },
      private: {
        [HOST]: privateData(HOST, 0, 0, true),
        [PLAYER2]: privateData(PLAYER2, 1, 1),
        [PLAYER3]: privateData(PLAYER3, 2, 2),
      },
      money: { 0: 30, 1: 30, 2: 30 },
      game: game(),
    });
  });
});

after(async () => {
  if (env) await env.cleanup();
});

test("未認証ユーザーはv2ルームを読めない", async () => {
  await assertFails(room(db(), "meta").get());
});

test("未認証ユーザーはv2ルームを作れない", async () => {
  await env.clearDatabase();
  await assertFails(room(db(), "meta").set(meta("lobby")));
});

test("認証済みホストは6文字コードでルームを作成できる", async () => {
  await env.clearDatabase();
  await assertSucceeds(room(db(HOST), "meta").set(meta("lobby")));
  await assertFails(db(HOST).ref("seriRoomsV2/AB12/meta").set(meta("lobby")));
});

test("認証済み観戦者は共有情報だけを読める", async () => {
  const spectator = db(SPECTATOR);
  await assertSucceeds(room(spectator, "meta").get());
  await assertSucceeds(room(spectator, "seats").get());
  await assertSucceeds(room(spectator, "game").get());
  await assertSucceeds(room(spectator, "money").get());
  await assertSucceeds(room(spectator, "history").get());
});

test("自分のprivateだけを読める", async () => {
  await assertSucceeds(room(db(PLAYER2), `private/${PLAYER2}`).get());
  await assertFails(room(db(PLAYER2), `private/${PLAYER3}`).get());
  await assertFails(room(db(SPECTATOR), `private/${PLAYER2}`).get());
  await assertSucceeds(room(db(HOST), "private").get());
});

test("空席への着席は本人UIDだけができる", async () => {
  await env.withSecurityRulesDisabled(async context => room(context.database(), "meta/phase").set("lobby"));
  const seat = { uid: SPECTATOR, name: "Watcher", lastSeen: 2 };
  await assertFails(room(db(PLAYER2), "seats/3").set(seat));
  await assertSucceeds(room(db(SPECTATOR), "seats/3").set(seat));
});

test("開始済みルームへの途中参加を拒否する", async () => {
  const seat = { uid: SPECTATOR, name: "Watcher", lastSeen: 2 };
  await assertFails(room(db(SPECTATOR), "seats/3").set(seat));
});

test("別UIDによる席の乗っ取りとUID上書きを拒否する", async () => {
  await env.withSecurityRulesDisabled(async context => room(context.database(), "meta/phase").set("lobby"));
  await assertFails(room(db(PLAYER3), "seats/1/name").set("hijacked"));
  await assertFails(room(db(PLAYER2), "seats/1/uid").set(PLAYER3));
  await assertFails(room(db(PLAYER2), "seats/3").set({ uid: PLAYER2, name: "duplicate", lastSeen: 2 }));
});

test("着席プレイヤーは自席actionだけを書ける", async () => {
  const action = { id: "player-2-1", type: "bid", round: 0, price: 0, createdAt: 2 };
  await assertSucceeds(room(db(PLAYER2), "actions/1").set(action));
  await assertFails(room(db(PLAYER2), "actions/1").set({ ...action, id: "player-2-2", price: 1 }));
  await assertFails(room(db(PLAYER2), "actions/2").set(action));
  await assertFails(room(db(SPECTATOR), "actions/1").set(action));
});

test("観戦者と一般プレイヤーはgameを書けない", async () => {
  await assertFails(room(db(SPECTATOR), "game/price").set(10));
  await assertFails(room(db(PLAYER2), "game/price").set(10));
  await assertSucceeds(room(db(HOST), "game/price").set(1));
});

test("money改ざんと範囲外値を拒否する", async () => {
  await assertFails(room(db(PLAYER2), "money/1").set(40));
  await assertFails(room(db(HOST), "money/1").set(41));
  await assertFails(room(db(HOST), "money/1").set("30"));
});

test("不正なゲーム状態遷移と未知フィールドを拒否する", async () => {
  await assertFails(room(db(HOST), "game/phase").set("cheat"));
  await assertFails(room(db(HOST), "game/price").set(1000));
  await assertFails(room(db(HOST), "game/admin").set(true));
});

test("historyへのHTML文字列と上書きを拒否する", async () => {
  const valid = { winnerSeat: 1, price: 4, values: { 0: 1, 1: 2, 2: 3 }, createdAt: 2 };
  await assertFails(room(db(PLAYER2), "history/0").set(valid));
  await assertFails(room(db(HOST), "history/0").set({ ...valid, values: { 0: "<img onerror=alert(1)>", 1: 2, 2: 3 } }));
  await assertSucceeds(room(db(HOST), "history/0").set(valid));
  await assertFails(room(db(HOST), "history/0/price").set(5));
});

test("巨大文字列、型違い、未知フィールドを拒否する", async () => {
  await env.withSecurityRulesDisabled(async context => room(context.database(), "meta/phase").set("lobby"));
  await assertFails(room(db(PLAYER2), "seats/1/name").set("x".repeat(10000)));
  await assertFails(room(db(HOST), `private/${PLAYER2}/see/0`).set("1"));
  await assertFails(room(db(HOST), `private/${PLAYER2}/unknown`).set(true));
});

test("privateの所有者偽装と値域外の秘匿値を拒否する", async () => {
  await assertFails(room(db(HOST), `private/${PLAYER2}/ownerUid`).set(PLAYER3));
  await assertFails(room(db(HOST), `private/${PLAYER2}/lens`).set(9));
  await assertFails(room(db(HOST), `private/${PLAYER2}/see/0`).set(99));
});

test("revealは終了後のみ公開されホストだけが作成できる", async () => {
  const reveal = { lenses: { 0: 0, 1: 1, 2: 2 }, createdAt: 3 };
  await assertFails(room(db(SPECTATOR), "reveal").get());
  await assertFails(room(db(HOST), "reveal").set(reveal));
  await env.withSecurityRulesDisabled(async context => room(context.database(), "meta/phase").set("ended"));
  await assertFails(room(db(SPECTATOR), "reveal").set(reveal));
  await assertFails(room(db(HOST), "reveal").set({ lenses: { 0: 0, 1: 1 }, createdAt: 3 }));
  await assertSucceeds(room(db(HOST), "reveal").set(reveal));
  await assertSucceeds(room(db(SPECTATOR), "reveal").get());
});

test("ルーム全体の削除を拒否する", async () => {
  await assertFails(room(db(PLAYER2)).remove());
  await assertFails(room(db(HOST)).remove());
});

test("4人・8ラウンドの正規初期化を許可する", async () => {
  await env.clearDatabase();
  const meta4 = { ...meta("lobby"), n: 4, rounds: 8, money: 34 };
  await assertSucceeds(room(db(HOST), "meta").set(meta4));
  const players = [HOST, PLAYER2, PLAYER3, PLAYER4];
  for (let i = 0; i < players.length; i++) {
    await assertSucceeds(room(db(players[i]), `seats/${i}`).set({ uid: players[i], name: `P${i + 1}`, lastSeen: 2 }));
  }
  const lots8 = {};
  for (let r = 0; r < 8; r++) lots8[r] = { 0: r % 7, 1: (r + 1) % 7, 2: (r + 2) % 7 };
  for (let i = 0; i < players.length; i++) {
    const p = privateData(players[i], i, i % 3, i === 0);
    p.see[6] = 1;
    p.see[7] = 2;
    if (i === 0) p.lots = lots8;
    await assertSucceeds(room(db(HOST), `private/${players[i]}`).set(p));
    await assertSucceeds(room(db(HOST), `money/${i}`).set(34));
  }
  await assertSucceeds(room(db(HOST), "game").set({ ...game(), live: "0,1,2,3" }));
  await assertSucceeds(room(db(HOST), "meta/phase").set("playing"));
});

test("C1の3人・4人構成だけを許可する", async () => {
  await env.clearDatabase();
  await assertSucceeds(room(db(HOST), "meta").set({ ...meta("lobby"), n: 3, rounds: 6, money: 28, nc: 3, rule: "c1" }));
  await assertFails(room(db(HOST), "meta/money").set(30));
  await env.clearDatabase();
  await assertSucceeds(room(db(HOST), "meta").set({ ...meta("lobby"), n: 4, rounds: 8, money: 34, nc: 4, rule: "c1" }));
  await assertFails(room(db(HOST), "meta/nc").set(3));
});

test("C1の盲点表示、4面原石、4面履歴、revealを許可し不正形を拒否する", async () => {
  await env.clearDatabase();
  const players = [HOST, PLAYER2, PLAYER3, PLAYER4];
  await env.withSecurityRulesDisabled(async context => {
    await room(context.database()).set({
      meta: { ...meta("playing"), n: 4, rounds: 8, money: 34, nc: 4, rule: "c1" },
      seats: Object.fromEntries(players.map((uid, seat) => [seat, { uid, name: `P${seat + 1}`, lastSeen: 2 }])),
    });
  });
  const see = Object.fromEntries(Array.from({ length: 8 }, (_, round) => [round, `${round % 2},x,2,3`]));
  const lots = Object.fromEntries(Array.from({ length: 8 }, (_, round) => [round, { 0: round % 7, 1: 1, 2: 2, 3: 3 }]));
  await assertSucceeds(room(db(HOST), `private/${HOST}`).set({ ownerUid: HOST, seat: 0, lens: 1, see, lots }));
  await assertFails(room(db(HOST), `private/${PLAYER2}`).set({ ownerUid: PLAYER2, seat: 1, lens: 0, see: { ...see, 0: "1,2,3,4" } }));
  await assertSucceeds(room(db(HOST), "history/0").set({ winnerSeat: 0, price: 1, values: { 0: 1, 1: 2, 2: 3, 3: 4 }, createdAt: 3 }));
  await assertFails(room(db(HOST), "history/1").set({ winnerSeat: 0, price: 1, values: { 0: 1, 1: 2, 2: 3 }, createdAt: 3 }));
  await env.withSecurityRulesDisabled(async context => room(context.database(), "meta/phase").set("ended"));
  await assertSucceeds(room(db(HOST), "reveal").set({ lenses: { 0: 0, 1: 1, 2: 2, 3: 3 }, createdAt: 4 }));
});
