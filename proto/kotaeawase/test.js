/* エンジン自動テスト: 単体 + ランダム全走行での不変条件検証 */
'use strict';
const E = require('./engine.js');
const { Actions } = E;

let passed = 0, failed = 0;
function ok(cond, msg) { if (cond) { passed++; } else { failed++; console.error('  FAIL:', msg); } }
function throws(fn, msg) { try { fn(); failed++; console.error('  FAIL(no throw):', msg); } catch (e) { passed++; } }

/* ---- ダミーデッキ生成 ---- */
function mkScore(rng) {
  const base = 1 + Math.floor(rng() * 5);
  const r = rng();
  const mod = (base < 5 && r < 0.15) ? 0.5 : (base > 1 && r > 0.85) ? -0.5 : 0;
  return { base, mod };
}
function mkDeck(n, prefix, rng, players, gm) {
  const cards = [];
  for (let i = 0; i < n; i++) {
    const scores = { zen: {}, kou: {} };
    for (const h of ['zen', 'kou']) {
      for (const p of [...players, gm]) scores[h][p] = mkScore(rng);
    }
    const overall = {
      zen: [...players, gm].reduce((a, p) => a + scores.zen[p].base + scores.zen[p].mod, 0) / 4,
      kou: [...players, gm].reduce((a, p) => a + scores.kou[p].base + scores.kou[p].mod, 0) / 4,
    };
    const comments = {};
    for (const p of [...players, gm]) comments[p] = `${p}の当時のコメント（${prefix}${i}）`;
    cards.push({ id: `${prefix}${i}`, name: `検証札${prefix}${i}`, cost: i % 4, type: 'スキル', effect: 'テスト用効果テキスト', scores, overall, comments });
  }
  return cards;
}
function lcg(seed) { let s = seed >>> 0; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 2 ** 32); }

const PLAYERS = ['からあげ', 'てかさ', '逆廻'];
const GM = 'マッチ';

/* ---- 1. parseScoreStr ---- */
console.log('parseScoreStr:');
ok(JSON.stringify(E.parseScoreStr('4+')) === JSON.stringify({ base: 4, mod: 0.5 }), '4+');
ok(JSON.stringify(E.parseScoreStr('5-')) === JSON.stringify({ base: 5, mod: -0.5 }), '5-');
ok(JSON.stringify(E.parseScoreStr('３')) === JSON.stringify({ base: 3, mod: 0 }), '全角3');
ok(JSON.stringify(E.parseScoreStr('５－')) === JSON.stringify({ base: 5, mod: -0.5 }), '全角5－');
ok(JSON.stringify(E.parseScoreStr(2)) === JSON.stringify({ base: 2, mod: 0 }), '数値2');
ok(E.parseScoreStr('記載なし') === null, '記載なし→null');
ok(E.parseScoreStr(null) === null, 'null');
ok(E.numeric({ base: 4, mod: -0.5 }) === 3.5, 'numeric 4- = 3.5');
ok(E.scoreLabel({ base: 4, mod: 0.5 }) === '4+', 'label 4+');

/* ---- 2. 宣言的中はbase一致（+/-は問わない）---- */
console.log('宣言判定（base一致）:');
{
  // 4- (=3.5) のカードに「4」宣言 → 的中、「3」宣言 → 外れ を確認
  const rng = lcg(7);
  const decks = [mkDeck(24, 'A', rng, PLAYERS, GM), mkDeck(24, 'B', rng, PLAYERS, GM), mkDeck(24, 'C', rng, PLAYERS, GM)];
  // 全カードのからあげzen点数を 4- に固定
  decks[0].forEach((c) => { c.scores.zen['からあげ'] = { base: 4, mod: -0.5 }; });
  let s = E.createGame(decks, null, {});
  s = E.reduce(s, { type: Actions.START_GAME });
  s = E.reduce(s, { type: Actions.ANNOUNCE_BASIS, half: 'zen' });
  for (const p of PLAYERS) s = E.reduce(s, { type: Actions.COMMIT, player: p, cardId: s.hands[p][0] });
  s = E.reduce(s, { type: Actions.REVEAL_FACES });
  s = E.reduce(s, { type: Actions.DECLARE, player: 'からあげ', self: 4, side: null });
  s = E.reduce(s, { type: Actions.DECLARE, player: 'てかさ', self: null, side: { target: 'からあげ', value: 3 } });
  s = E.reduce(s, { type: Actions.DECLARE, player: '逆廻', self: null, side: { target: 'からあげ', value: 4 } });
  s = E.reduce(s, { type: Actions.REVEAL_SCORES });
  const r = s.roundResult;
  ok(r.declResults['からあげ'].self.hit === true, '4-に自分宣言4は的中');
  ok(r.declResults['てかさ'].side.hit === false, '4-に横予想3は外れ');
  ok(r.declResults['逆廻'].side.hit === true, '4-に横予想4は的中');
}

/* ---- 3. 不正操作の拒否 ---- */
console.log('不正操作:');
{
  const rng = lcg(11);
  const decks = [mkDeck(24, 'A', rng, PLAYERS, GM), mkDeck(24, 'B', rng, PLAYERS, GM), mkDeck(24, 'C', rng, PLAYERS, GM)];
  let s = E.createGame(decks, null, {});
  throws(() => E.reduce(s, { type: Actions.COMMIT, player: 'からあげ', cardId: 'A0' }), 'lobby中のコミット拒否');
  s = E.reduce(s, { type: Actions.START_GAME });
  throws(() => E.reduce(s, { type: Actions.ANNOUNCE_BASIS, half: 'kou' }), '第1章はzen固定、kou宣言拒否');
  s = E.reduce(s, { type: Actions.ANNOUNCE_BASIS });
  ok(s.currentHalf === 'zen', '固定セットはhalf省略で自動zen');
  throws(() => E.reduce(s, { type: Actions.COMMIT, player: 'マッチ', cardId: 'A0' }), 'GMのコミット拒否');
  throws(() => E.reduce(s, { type: Actions.COMMIT, player: 'からあげ', cardId: 'ZZZ' }), '手札にない札の拒否');
  const c0 = s.hands['からあげ'][0];
  s = E.reduce(s, { type: Actions.COMMIT, player: 'からあげ', cardId: c0 });
  throws(() => E.reduce(s, { type: Actions.COMMIT, player: 'からあげ', cardId: s.hands['からあげ'][1] }), '二重コミット拒否');
  for (const p of ['てかさ', '逆廻']) s = E.reduce(s, { type: Actions.COMMIT, player: p, cardId: s.hands[p][0] });
  s = E.reduce(s, { type: Actions.REVEAL_FACES });
  throws(() => E.reduce(s, { type: Actions.DECLARE, player: 'からあげ', self: 6 }), '範囲外宣言拒否');
  throws(() => E.reduce(s, { type: Actions.DECLARE, player: 'からあげ', self: 2.5 }), '非整数宣言拒否');
  throws(() => E.reduce(s, { type: Actions.DECLARE, player: 'からあげ', side: { target: 'からあげ', value: 3 } }), '自分への横予想拒否');
}

/* ---- 4. タイブレーク ---- */
console.log('タイブレーク:');
{
  const rng = lcg(13);
  const decks = [mkDeck(24, 'A', rng, PLAYERS, GM), mkDeck(24, 'B', rng, PLAYERS, GM), mkDeck(24, 'C', rng, PLAYERS, GM)];
  // 3人の1枚目を同値に。overallで からあげ の札だけ高くする
  const firstIds = {};
  let s0 = E.createGame(decks, null, {});
  let s = E.reduce(s0, { type: Actions.START_GAME });
  for (const p of PLAYERS) firstIds[p] = s.hands[p][0];
  for (const p of PLAYERS) {
    const c = s.cardsById[firstIds[p]];
    c.scores.zen[p] = { base: 3, mod: 0 };
    c.overall.zen = (p === 'からあげ') ? 4.0 : 3.0;
  }
  s = E.reduce(s, { type: Actions.ANNOUNCE_BASIS });
  for (const p of PLAYERS) s = E.reduce(s, { type: Actions.COMMIT, player: p, cardId: firstIds[p] });
  s = E.reduce(s, { type: Actions.REVEAL_FACES });
  for (const p of PLAYERS) s = E.reduce(s, { type: Actions.DECLARE, player: p });
  s = E.reduce(s, { type: Actions.REVEAL_SCORES });
  ok(s.roundResult.winners.length === 1 && s.roundResult.winners[0] === 'からあげ', '同値→overallタイブレーク');
  ok(s.roundResult.tieBreakUsed === 'overall', 'tieBreakUsed=overall');

  // 完全同値 → 山分け（切り上げ）
  let t = E.reduce(s0, { type: Actions.START_GAME });
  for (const p of PLAYERS) {
    const c = t.cardsById[t.hands[p][0]];
    c.scores.zen[p] = { base: 3, mod: 0 };
    c.overall.zen = 3.0;
  }
  t = E.reduce(t, { type: Actions.ANNOUNCE_BASIS });
  for (const p of PLAYERS) t = E.reduce(t, { type: Actions.COMMIT, player: p, cardId: t.hands[p][0] });
  t = E.reduce(t, { type: Actions.REVEAL_FACES });
  for (const p of PLAYERS) t = E.reduce(t, { type: Actions.DECLARE, player: p });
  t = E.reduce(t, { type: Actions.REVEAL_SCORES });
  ok(t.roundResult.winners.length === 3, '完全同値→3人山分け');
  ok(Object.values(t.roundResult.pointsAwarded).every((v) => v === 1), '3点山分け=各1(切り上げ)');
}

/* ---- 5. ランダム全走行（500ゲーム）---- */
console.log('ランダム全走行 x500:');
{
  let games = 0, decisions = 0, ties = 0, crashed = 0;
  for (let g = 0; g < 500; g++) {
    const rng = lcg(1000 + g);
    const decks = [mkDeck(24, 'A', rng, PLAYERS, GM), mkDeck(24, 'B', rng, PLAYERS, GM), mkDeck(24, 'C', rng, PLAYERS, GM)];
    try {
      let s = E.createGame(decks, null, {});
      s = E.reduce(s, { type: Actions.START_GAME });
      let guard = 0;
      while (s.phase !== 'final' && guard++ < 500) {
        switch (s.phase) {
          case 'basis': {
            const fixed = s.config.sets[s.setIndex].fixedHalf;
            s = E.reduce(s, { type: Actions.ANNOUNCE_BASIS, half: fixed || (rng() < 0.5 ? 'zen' : 'kou') });
            break;
          }
          case 'commit': {
            for (const p of PLAYERS) {
              const hand = s.hands[p];
              s = E.reduce(s, { type: Actions.COMMIT, player: p, cardId: hand[Math.floor(rng() * hand.length)] });
            }
            break;
          }
          case 'faces': s = E.reduce(s, { type: Actions.REVEAL_FACES }); break;
          case 'declare': {
            for (const p of PLAYERS) {
              const self = rng() < 0.7 ? 1 + Math.floor(rng() * 5) : null;
              let side = null;
              if (rng() < 0.5) {
                const others = PLAYERS.filter((q) => q !== p);
                side = { target: others[Math.floor(rng() * 2)], value: 1 + Math.floor(rng() * 5) };
              }
              s = E.reduce(s, { type: Actions.DECLARE, player: p, self, side });
            }
            break;
          }
          case 'scoresReady': {
            s = E.reduce(s, { type: Actions.REVEAL_SCORES });
            if (s.roundResult.tieBreakUsed) ties++;
            // 不変条件: 勝利点+宣言点の合計が正しい
            const r = s.roundResult;
            const total = Object.values(r.pointsAwarded).reduce((a, b) => a + b, 0);
            const declPts = Object.values(r.declResults).reduce((a, d) => a + (d.self?.hit ? 2 : 0) + (d.side?.hit ? 2 : 0), 0);
            const winPts = r.winners.length === 1 ? r.winPoints : r.winners.length * Math.ceil(r.winPoints / r.winners.length);
            ok(total === declPts + winPts, `g${g} 得点合計整合 (${total} vs ${declPts}+${winPts})`);
            break;
          }
          case 'roundEnd': s = E.reduce(s, { type: Actions.NEXT }); break;
          case 'decisionPick': {
            for (const p of PLAYERS) {
              ok(s.reserved[p].length === 6, `g${g} ${p} 温存札6枚(2x3セット)`);
              s = E.reduce(s, { type: Actions.DECISION_PICK, player: p, cardId: s.reserved[p][Math.floor(rng() * s.reserved[p].length)] });
            }
            break;
          }
          case 'decisionBasis': s = E.reduce(s, { type: Actions.DECISION_ANNOUNCE, half: rng() < 0.5 ? 'zen' : 'kou' }); break;
          case 'decisionDeclare': {
            for (const p of PLAYERS) s = E.reduce(s, { type: Actions.DECISION_DECLARE, player: p, guess: rng() < 0.8 ? 1 + Math.floor(rng() * 5) : null });
            break;
          }
          case 'decisionReady': s = E.reduce(s, { type: Actions.DECISION_REVEAL }); decisions++; break;
          default: throw new Error(`未処理フェーズ ${s.phase}`);
        }
      }
      ok(s.phase === 'final', `g${g} 完走`);
      ok(s.history.filter((h) => h.kind === 'round').length === 18, `g${g} 18ラウンド記録`);
      ok(s.history.filter((h) => h.kind === 'decision').length === 1, `g${g} 決着記録`);
      ok(Object.values(s.scores).every((v) => v >= 0 && Number.isInteger(v)), `g${g} スコア非負整数`);
      // スコア合計 = history の pointsAwarded 総和
      const histTotal = s.history.reduce((a, h) => a + Object.values(h.pointsAwarded).reduce((x, y) => x + y, 0), 0);
      const scoreTotal = Object.values(s.scores).reduce((a, b) => a + b, 0);
      ok(histTotal === scoreTotal, `g${g} 履歴とスコアの一致 (${histTotal} vs ${scoreTotal})`);
      games++;
    } catch (e) {
      crashed++;
      console.error(`  CRASH g${g}:`, e.message);
    }
  }
  ok(crashed === 0, `クラッシュ0 (実際: ${crashed})`);
  console.log(`  完走 ${games}/500, 決着 ${decisions}, タイブレーク発生 ${ties}回`);
}

/* ---- 6. データ欠損の検出 ---- */
console.log('デッキ検証:');
{
  const rng = lcg(99);
  const decks = [mkDeck(24, 'A', rng, PLAYERS, GM), mkDeck(24, 'B', rng, PLAYERS, GM), mkDeck(24, 'C', rng, PLAYERS, GM)];
  delete decks[1][5].scores.kou['てかさ'];
  throws(() => E.createGame(decks, null, {}), '点数欠損デッキの拒否');
  const decks2 = [mkDeck(24, 'A', rng, PLAYERS, GM), mkDeck(23, 'B', rng, PLAYERS, GM), mkDeck(24, 'C', rng, PLAYERS, GM)];
  throws(() => E.createGame(decks2, null, {}), '枚数不足デッキの拒否');
}

/* ---- 7. 平準化配札 ---- */
console.log('平準化配札:');
{
  let worstBefore = 0, worstAfter = 0;
  for (let t = 0; t < 200; t++) {
    const rng = lcg(5000 + t);
    const deck = mkDeck(24, 'Z', rng, PLAYERS, GM);
    const val = (c, p) => (c.scores.zen[p].base + c.scores.zen[p].mod + c.scores.kou[p].base + c.scores.kou[p].mod) / 2;
    const spreadOf = (ordered) => {
      const hands = PLAYERS.map((_, i) => ordered.filter((_, k) => k < 24 && k % 3 === i));
      const avgs = hands.map((h, i) => h.reduce((a, c) => a + val(c, PLAYERS[i]), 0) / 8);
      return Math.max(...avgs) - Math.min(...avgs);
    };
    worstBefore = Math.max(worstBefore, spreadOf(deck));
    const balanced = E.balanceDeal(deck, PLAYERS, 8, rng);
    ok(balanced.length === 24, `t${t} 枚数保存`);
    ok(new Set(balanced.map((c) => c.id)).size === 24, `t${t} 重複なし`);
    worstAfter = Math.max(worstAfter, spreadOf(balanced));
  }
  ok(worstAfter <= 0.75, `平準化後の最悪スプレッド ${worstAfter.toFixed(2)} <= 0.75`);
  console.log(`  平準化前 最悪スプレッド ${worstBefore.toFixed(2)} → 後 ${worstAfter.toFixed(2)}`);
}

console.log(`\n==== 結果: ${passed} passed, ${failed} failed ====`);
process.exit(failed > 0 ? 1 : 0);
