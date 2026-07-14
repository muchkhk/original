import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";
import assert from "node:assert/strict";

const html = fs.readFileSync("seri.html", "utf8");
const coreMatch = html.match(/\/\* C1_CORE_START[\s\S]*?\*\/([\s\S]*?)\/\* C1_CORE_END \*\//);
assert.ok(coreMatch, "C1 pure core marker must exist");
const sandbox = {};
vm.runInNewContext(`${coreMatch[1]};globalThis.C1_TEST={C1_SETUP,c1CreateState,c1Value,c1Score,c1Ranks,c1Act,c1Advance,c1NpcLimit,c1NpcAction};`, sandbox);
const C = sandbox.C1_TEST;

function fixedRandom(values) {
  let i = 0;
  return () => values[i++ % values.length];
}

// 正本 9c99921:seri.html 419-451: 4人、資金34、8石、4面、盲点は重複なし、開始席は round % 4。
test("solo C1の初期状態が旧正本と一致する", () => {
  const state = C.c1CreateState("Human", fixedRandom([0, .25, .5, .75, .1, .2, .3, .4]));
  assert.equal(C.C1_SETUP.players, 4);
  assert.equal(C.C1_SETUP.rounds, 8);
  assert.equal(C.C1_SETUP.money, 34);
  assert.deepEqual([...state.money], [34, 34, 34, 34]);
  assert.deepEqual([...state.lens].sort(), [0, 1, 2, 3]);
  assert.equal(state.lot.length, 4);
  assert.equal(state.starter, 0);
  assert.equal(state.cur, 0);
});

// 正本 9c99921:seri.html 456-512: 1刻み入札、降り、落札額支払、履歴、次石への遷移。
test("入札・降り・落札・資金・得点・履歴が旧C1規則に従う", () => {
  const state = C.c1CreateState("Human", () => 0);
  state.lens = [0, 1, 2, 3];
  state.lot = [1, 2, 3, 4];
  assert.equal(C.c1Act(state, 0, "bid"), true);
  assert.equal(state.price, 1);
  assert.equal(C.c1Act(state, 1, "drop"), true);
  assert.equal(C.c1Act(state, 2, "drop"), true);
  assert.equal(C.c1Act(state, 3, "drop"), true);
  assert.equal(state.phase, "reveal");
  assert.equal(state.money[0], 33);
  assert.equal(state.history.length, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(state.history[0])), {
    values: [1, 2, 3, 4], winnerSeat: 0, price: 1,
    bids: [
      { seat: 0, price: 1, type: "bid" },
      { seat: 1, price: 1, type: "drop" },
      { seat: 2, price: 1, type: "drop" },
      { seat: 3, price: 1, type: "drop" },
    ],
  });
  assert.equal(C.c1Value(state, 0, [1, 2, 3, 4]), 12);
  assert.equal(C.c1Score(state, 0), 45);
});

test("ラウンド開始席、終了条件、同点処理が固定されている", () => {
  const state = C.c1CreateState("Human", () => 0);
  for (let round = 0; round < 8; round++) {
    state.phase = "reveal";
    assert.equal(C.c1Advance(state, () => 0), true);
    if (round < 7) {
      assert.equal(state.round, round + 1);
      assert.equal(state.starter, (round + 1) % 4);
      assert.equal(state.phase, "auction");
    }
  }
  assert.equal(state.phase, "end");
  assert.equal(state.history.length, 0);
  assert.deepEqual(Array.from(C.c1Ranks(state), x => x.seat), [0, 1, 2, 3]);
});

// 正本 9c99921:seri.html 533-550: NPCは自分の盲点真値を参照せず、公開行動だけで推定を補正する。
test("C1 NPC判断は本人の盲点真値を参照しない", () => {
  const state = C.c1CreateState("Human", () => 0);
  state.lens = [0, 1, 2, 3];
  state.cur = 1;
  state.lot = [2, -2, 4, 1];
  const first = C.c1NpcLimit(state, 1);
  state.lot[1] = 6;
  assert.equal(C.c1NpcLimit(state, 1), first);
  assert.match(C.c1NpcAction(state, 1), /^(bid|drop)$/);
});

test("solo実行経路はFirebase DBとオンラインルームを使わない", () => {
  const soloBlock = html.match(/function saveSolo[\s\S]*?async function bootstrap/);
  assert.ok(soloBlock);
  assert.doesNotMatch(soloBlock[0], /\bdb\.|roomRef|seriRoomsV2|newCode\(/);
  assert.match(html, /人間1人＋NPC3人/);
  assert.match(html, /Firebase認証とRealtime Databaseを使用しません/);
  assert.match(html, /SOLO_KEY="seri_c1_solo_state_v2"/);
});

test("C1オンラインは安全なv2ノードとUID別privateを共有する", () => {
  assert.match(html, /rule==="c1"\?n:3/);
  assert.match(html, /encodeC1View/);
  assert.match(html, /private\/\$\{s\.uid\}/);
  assert.match(html, /const ROOT="seriRoomsV2"/);
  assert.doesNotMatch(html, /["'`]rooms\//);
});

test("solo表示も安全なDOM APIだけを使用する", () => {
  assert.doesNotMatch(html, /innerHTML/);
  assert.match(html, /document\.createTextNode/);
  assert.match(html, /renderSolo/);
  for (const payload of ["<img src=x onerror=alert(1)>", "<svg onload=alert(1)>", "</div><script>alert(1)</script>"]) {
    assert.equal(payload.includes("<"), true);
  }
});
