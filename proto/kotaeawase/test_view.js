/* viewFor 秘匿不変条件テスト:
 * 「非GM座席のビューに含まれる点数は、公開済entries内のものだけ」を全フェーズで検証。 */
'use strict';
const E = require('./engine.js');
const { Actions } = E;
let passed = 0, failed = 0;
function ok(c, m) { if (c) passed++; else { failed++; console.error('  FAIL:', m); } }

const PLAYERS = ['からあげ', 'てかさ', '逆廻'], GM = 'マッチ';
function mkScore(rng) { const base = 1 + Math.floor(rng() * 5); const r = rng(); const mod = (base < 5 && r < .15) ? .5 : (base > 1 && r > .85) ? -.5 : 0; return { base, mod }; }
function mkDeck(n, pfx, rng) { const cs = []; for (let i = 0; i < n; i++) { const scores = { zen: {}, kou: {} }; for (const h of ['zen', 'kou']) for (const p of [...PLAYERS, GM]) scores[h][p] = mkScore(rng); const overall = { zen: [...PLAYERS, GM].reduce((a, p) => a + scores.zen[p].base + scores.zen[p].mod, 0) / 4, kou: [...PLAYERS, GM].reduce((a, p) => a + scores.kou[p].base + scores.kou[p].mod, 0) / 4 }; const comments = {}; for (const p of [...PLAYERS, GM]) comments[p] = `c${pfx}${i}`; cs.push({ id: `${pfx}${i}`, name: `札${pfx}${i}`, cost: i % 4, type: 'スキル', effect: 'fx', char: 'X', scores, overall, comments }); } return cs; }
function lcg(s) { s = s >>> 0; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 2 ** 32); }

/* あるビューに登場する「点数らしきオブジェクト」を全部集める（値やキー名は問わない・形だけで検出）。 */
function collectScoreSigs(obj, path, sink) {
  if (obj === null || typeof obj !== 'object') return;
  // score形状 {base, mod} を検出
  if (typeof obj.base === 'number' && ('mod' in obj) && Object.keys(obj).length <= 2) {
    sink.push({ path, sig: `${obj.base}|${obj.mod}` });
    return;
  }
  for (const k of Object.keys(obj)) collectScoreSigs(obj[k], path + '.' + k, sink);
}

/* ---------- 秘匿不変条件チェック本体（場所ベース。知見§14-9対応） ----------
 * 「点数が現れてよいのは、判定確定済みentries(roundResult/history[]/decision.result)の
 * score/scoreZen/scoreKouフィールドだけ」という“場所”の条件を検証する。
 * 実装は「その3箇所からscore/scoreZen/scoreKouというフィールドだけを取り除いたコピーを作り、
 * 残った全体を再帰走査して、点数らしき値(base/mod形状)が1つでも残っていれば漏洩」という形。
 * キー名やシグネチャ値による許可リストは使わない（myHand等に紛れ込んだscoreZenも、
 * 名前に関わらずこの走査で拾われる）。 */
function findSecrecyLeak(view) {
  if (!view || view.isGM) return null; // GMは答えを持ってよい
  if (view.gm !== null && view.gm !== undefined) return { path: 'v.gm', sig: 'gm欄が非GMビューに存在' };
  const clone = JSON.parse(JSON.stringify(view));
  const stripEntries = (r) => { if (r && Array.isArray(r.entries)) for (const e of r.entries) { delete e.score; delete e.scoreZen; delete e.scoreKou; } };
  stripEntries(clone.roundResult);
  (clone.history || []).forEach(stripEntries);
  if (clone.decision && clone.decision.result) stripEntries(clone.decision.result);
  delete clone.gm; // 既にnullチェック済み。以降の走査対象から除外（GM欄自体は許可された場所ではないため）
  const leaks = [];
  collectScoreSigs(clone, 'v', leaks);
  return leaks.length ? leaks[0] : null;
}

function runGame(seed, checkEachPhase) {
  const rng = lcg(seed);
  const decks = [mkDeck(24, 'A', rng), mkDeck(24, 'B', rng), mkDeck(24, 'C', rng)];
  let s = E.createGame(decks, null, {});
  s = E.reduce(s, { type: Actions.START_GAME });
  let guard = 0;
  while (s.phase !== 'final' && guard++ < 500) {
    checkEachPhase(s);
    switch (s.phase) {
      case 'basis': s = E.reduce(s, { type: Actions.ANNOUNCE_BASIS, half: s.config.sets[s.setIndex].fixedHalf || (rng() < .5 ? 'zen' : 'kou') }); break;
      case 'commit': for (const p of PLAYERS) s = E.reduce(s, { type: Actions.COMMIT, player: p, cardId: s.hands[p][Math.floor(rng() * s.hands[p].length)] }); break;
      case 'faces': s = E.reduce(s, { type: Actions.REVEAL_FACES }); break;
      case 'declare': for (const p of PLAYERS) s = E.reduce(s, { type: Actions.DECLARE, player: p, self: rng() < .7 ? 1 + Math.floor(rng() * 5) : null, side: rng() < .5 ? { target: PLAYERS.filter(q => q !== p)[Math.floor(rng() * 2)], value: 1 + Math.floor(rng() * 5) } : null }); break;
      case 'scoresReady': s = E.reduce(s, { type: Actions.REVEAL_SCORES }); break;
      case 'roundEnd': s = E.reduce(s, { type: Actions.NEXT }); break;
      case 'decisionPick': for (const p of PLAYERS) s = E.reduce(s, { type: Actions.DECISION_PICK, player: p, cardId: s.reserved[p][Math.floor(rng() * s.reserved[p].length)] }); break;
      case 'decisionBasis': s = E.reduce(s, { type: Actions.DECISION_ANNOUNCE, half: rng() < .5 ? 'zen' : 'kou' }); break;
      case 'decisionDeclare': for (const p of PLAYERS) s = E.reduce(s, { type: Actions.DECISION_DECLARE, player: p, guess: rng() < .8 ? 1 + Math.floor(rng() * 5) : null }); break;
      case 'decisionReady': s = E.reduce(s, { type: Actions.DECISION_REVEAL }); break;
    }
  }
  checkEachPhase(s); // final
  return s;
}

/* 各フェーズで、非GMビュー中の全点数シグネチャが「公開entries集合」に含まれることを確認 */
console.log('秘匿不変条件（非GMビューに未公開点数なし）:');
let phaseChecks = 0;
for (let g = 0; g < 60; g++) {
  const finalState = runGame(300 + g, (state) => {
    for (const seat of PLAYERS) {
      const view = E.viewFor(state, seat);
      // cardMeta には score があってはならない
      const metaSigs = [];
      collectScoreSigs(view.cardMeta, 'cardMeta', metaSigs);
      ok(metaSigs.length === 0, `g${g} ${state.phase} cardMetaに点数混入`);
      // gm欄が非GMビューに存在しないこと
      ok(view.gm === null, `g${g} ${state.phase} 非GMビューにgm欄`);
      // 場所ベースの秘匿チェック（findSecrecyLeak・§2-1〜2-3で故意注入により検出力を証明済み）
      const leak = findSecrecyLeak(view);
      ok(!leak, `g${g} ${state.phase} 未公開点数リーク: ${leak ? JSON.stringify(leak) : ''}`);
      phaseChecks++;
    }
  });
  // 新規（§3-4）: ゲーム完走後、公開済み(history)の全エントリに両半分(scoreZen/scoreKou)が含まれることを1回だけ検証
  const finalView = E.viewFor(finalState, PLAYERS[0]);
  ok(finalView.history.length > 0, `g${g} 完走後history非空`);
  for (const h of finalView.history) for (const e of h.entries) {
    ok(e.scoreZen && typeof e.scoreZen.base === 'number', `g${g} history entry(${h.kind}) に scoreZen`);
    ok(e.scoreKou && typeof e.scoreKou.base === 'number', `g${g} history entry(${h.kind}) に scoreKou`);
  }
}
console.log(`  フェーズ点検 ${phaseChecks} 回`);

/* ---------- 秘匿チェックの存在証明（知見§14-9・故意注入） ----------
 * findSecrecyLeak() が「場所」を見ているか（＝キー名や値ではなく、許可されていない位置に
 * 点数らしき値が現れたら必ず落ちるか）を、実際に壊してから確認する。
 * 発火例5件それぞれ独立に、正常ビューへ注入 → 必ず検出されることを検証する。 */
console.log('秘匿チェックの存在証明（知見§14-9・故意注入）:');
{
  const mkScoreObj = () => ({ base: 3, mod: 0 });

  // 土台: facesフェーズのビュー（committedCardsあり・score類はまだ一切公開されていない）
  const rngBase = lcg(9999);
  const decksBase = [mkDeck(24, 'A', rngBase), mkDeck(24, 'B', rngBase), mkDeck(24, 'C', rngBase)];
  let sBase = E.createGame(decksBase, null, {});
  sBase = E.reduce(sBase, { type: Actions.START_GAME });
  sBase = E.reduce(sBase, { type: Actions.ANNOUNCE_BASIS, half: 'zen' });
  for (const p of PLAYERS) sBase = E.reduce(sBase, { type: Actions.COMMIT, player: p, cardId: sBase.hands[p][0] });
  sBase = E.reduce(sBase, { type: Actions.REVEAL_FACES });
  const baseView = E.viewFor(sBase, 'からあげ');

  // 非発火例（§2-2）: 注入前の正常ビューは通ること
  ok(!findSecrecyLeak(baseView), '非発火例: 注入前の正常ビューは通る');

  // 発火1: myHandの要素にscoreZenを持たせる
  {
    const v = JSON.parse(JSON.stringify(baseView));
    if (v.myHand && v.myHand.length) v.myHand[0] = { id: v.myHand[0], scoreZen: mkScoreObj() };
    ok(!!findSecrecyLeak(v), '発火1: myHand要素へのscoreZen注入が検出される');
  }
  // 発火2: cardMeta[id]にscoreKouを持たせる
  {
    const v = JSON.parse(JSON.stringify(baseView));
    const anyId = Object.keys(v.cardMeta)[0];
    v.cardMeta[anyId] = Object.assign({}, v.cardMeta[anyId], { scoreKou: mkScoreObj() });
    ok(!!findSecrecyLeak(v), '発火2: cardMetaへのscoreKou注入が検出される');
  }
  // 発火3: myReservedの要素にscoreを持たせる（決着フェーズまで進めて取得）
  {
    let capturedDecisionPickView = null;
    runGame(7777, (state) => {
      if (!capturedDecisionPickView && state.phase === 'decisionPick') capturedDecisionPickView = E.viewFor(state, PLAYERS[0]);
    });
    ok(!!capturedDecisionPickView, '発火3準備: decisionPickビューを取得できた');
    const v = JSON.parse(JSON.stringify(capturedDecisionPickView));
    if (v.myReserved && v.myReserved.length) v.myReserved[0] = { id: v.myReserved[0], score: mkScoreObj() };
    ok(!!findSecrecyLeak(v), '発火3: myReserved要素へのscore注入が検出される');
  }
  // 発火4: 未判定ラウンドのcommittedCardsにscoreZenを持たせる（facesフェーズ＝まだ判定前）
  {
    const v = JSON.parse(JSON.stringify(baseView));
    ok(v.roundResult === null, '発火4準備: facesフェーズは未判定（roundResult無し）');
    const anyPlayer = Object.keys(v.committedCards)[0];
    v.committedCards[anyPlayer] = { id: v.committedCards[anyPlayer], scoreZen: mkScoreObj() };
    ok(!!findSecrecyLeak(v), '発火4: 未判定ラウンドのcommittedCardsへのscoreZen注入が検出される');
  }
  // 発火5: gm相当の答えデータが非GM座席のビューに現れる
  {
    const v = JSON.parse(JSON.stringify(baseView));
    v.gm = { answers: { 'からあげ': { cardId: 'x', rater: 'からあげ', zen: mkScoreObj(), kou: mkScoreObj() } }, hands: {} };
    ok(!!findSecrecyLeak(v), '発火5: 非GMビューへのgm欄混入が検出される');
  }
}

/* GMビューには答えが載る（進行に必要） */
console.log('GMビュー:');
{
  const rng = lcg(1);
  const decks = [mkDeck(24, 'A', rng), mkDeck(24, 'B', rng), mkDeck(24, 'C', rng)];
  let s = E.createGame(decks, null, {});
  s = E.reduce(s, { type: Actions.START_GAME });
  s = E.reduce(s, { type: Actions.ANNOUNCE_BASIS, half: 'zen' });
  // 2人だけコミット（phaseはcommitのまま）
  s = E.reduce(s, { type: Actions.COMMIT, player: 'からあげ', cardId: s.hands['からあげ'][0] });
  s = E.reduce(s, { type: Actions.COMMIT, player: 'てかさ', cardId: s.hands['てかさ'][0] });
  ok(s.phase === 'commit', '2人コミットではまだcommit');
  const pv = E.viewFor(s, '逆廻');
  ok(pv.committedSeats['からあげ'] === true, 'コミット済フラグは見える');
  ok(pv.committedCards === null, 'faces前は札の中身は見えない');
  ok(pv.myHand && pv.myHand.length === 8, '自分の手札は見える（未コミットなので8枚）');
  // 3人目コミット→faces→GM答え確認
  s = E.reduce(s, { type: Actions.COMMIT, player: '逆廻', cardId: s.hands['逆廻'][0] });
  const gmv = E.viewFor(s, GM);
  ok(gmv.gm && Object.keys(gmv.gm.answers).length === 3, 'GMビューに3枚の答え');
  ok(gmv.gm.answers['からあげ'].zen && typeof gmv.gm.answers['からあげ'].zen.base === 'number', 'GM答えに点数');
}

/* faces後は他人の札の名前は見えるが点数は見えない */
console.log('faces後の可視性:');
{
  const rng = lcg(2);
  const decks = [mkDeck(24, 'A', rng), mkDeck(24, 'B', rng), mkDeck(24, 'C', rng)];
  let s = E.createGame(decks, null, {});
  s = E.reduce(s, { type: Actions.START_GAME });
  s = E.reduce(s, { type: Actions.ANNOUNCE_BASIS, half: 'zen' });
  for (const p of PLAYERS) s = E.reduce(s, { type: Actions.COMMIT, player: p, cardId: s.hands[p][0] });
  s = E.reduce(s, { type: Actions.REVEAL_FACES });
  const pv = E.viewFor(s, 'からあげ');
  ok(pv.committedCards && Object.keys(pv.committedCards).length === 3, 'faces後は3枚の札IDが見える');
  const anyMetaScore = [];
  collectScoreSigs(pv.cardMeta, 'm', anyMetaScore);
  ok(anyMetaScore.length === 0, 'faces後もメタに点数なし');
  ok(pv.roundResult === null, 'declare前は結果なし＝点数なし');
}

console.log(`\n==== view結果: ${passed} passed, ${failed} failed ====`);
process.exit(failed ? 1 : 0);
