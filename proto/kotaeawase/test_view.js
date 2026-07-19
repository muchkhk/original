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

/* あるビューに登場する「点数らしきオブジェクト」を全部集めて、
 * それが公開entries由来かどうかを判定するために、まず公開集合を作る。 */
function collectScoreSigs(obj, path, sink) {
  if (obj === null || typeof obj !== 'object') return;
  // score形状 {base, mod} を検出
  if (typeof obj.base === 'number' && ('mod' in obj) && Object.keys(obj).length <= 2) {
    sink.push({ path, sig: `${obj.base}|${obj.mod}` });
    return;
  }
  for (const k of Object.keys(obj)) collectScoreSigs(obj[k], path + '.' + k, sink);
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
  runGame(300 + g, (state) => {
    for (const seat of PLAYERS) {
      const view = E.viewFor(state, seat);
      // cardMeta には score があってはならない
      const metaSigs = [];
      collectScoreSigs(view.cardMeta, 'cardMeta', metaSigs);
      ok(metaSigs.length === 0, `g${g} ${state.phase} cardMetaに点数混入`);
      // 公開集合: roundResult.entries, history[].entries, decision.result.entries の score のみ
      const allowed = [];
      const gather = (r) => { if (r && r.entries) for (const e of r.entries) if (e.score) allowed.push(`${e.score.base}|${e.score.mod}`); };
      gather(view.roundResult);
      (view.history || []).forEach(gather);
      gather(view.decision?.result);
      // ビュー全体から点数シグネチャ収集（gm欄は非GMには無い）
      const all = [];
      collectScoreSigs({ committedCards: view.committedCards, roundResult: view.roundResult, history: view.history, decision: view.decision, myHand: view.myHand, myReserved: view.myReserved }, 'v', all);
      // gm欄が非GMビューに存在しないこと
      ok(view.gm === null, `g${g} ${state.phase} 非GMビューにgm欄`);
      // 全点数が公開集合に含まれる（多重集合として: 各シグが allowed に存在すればOK）
      const allowedSet = new Set(allowed);
      const leak = all.find((x) => !allowedSet.has(x.sig));
      ok(!leak, `g${g} ${state.phase} 未公開点数リーク: ${leak ? JSON.stringify(leak) : ''}`);
      phaseChecks++;
    }
  });
}
console.log(`  フェーズ点検 ${phaseChecks} 回`);

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
