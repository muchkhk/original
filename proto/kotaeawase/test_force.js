/* forceComplete テスト: 未提出プレイヤーがいてもGMがフェーズを前進できる */
'use strict';
const E = require('./engine.js');
const N = require('./net.js');

let passed = 0, failed = 0;
function ok(c, m) { if (c) passed++; else { failed++; console.error('  FAIL:', m); } }
const tick = () => new Promise((r) => setTimeout(r, 0));
const PLAYERS = ['からあげ', 'てかさ', '逆廻'], GM = 'マッチ';
function lcg(s) { s = s >>> 0; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 2 ** 32); }
function mkScore(rng) { const base = 1 + Math.floor(rng() * 5); const r = rng(); const mod = (base < 5 && r < .15) ? .5 : (base > 1 && r > .85) ? -.5 : 0; return { base, mod }; }
function mkDeck(n, pfx, rng) { const cs = []; for (let i = 0; i < n; i++) { const scores = { zen: {}, kou: {} }; for (const h of ['zen', 'kou']) for (const p of [...PLAYERS, GM]) scores[h][p] = mkScore(rng); const overall = { zen: [...PLAYERS, GM].reduce((a, p) => a + scores.zen[p].base + scores.zen[p].mod, 0) / 4, kou: [...PLAYERS, GM].reduce((a, p) => a + scores.kou[p].base + scores.kou[p].mod, 0) / 4 }; const comments = {}; for (const p of [...PLAYERS, GM]) comments[p] = `c${pfx}${i}`; cs.push({ id: `${pfx}${i}`, name: `札${pfx}${i}`, cost: i % 4, type: 'スキル', effect: 'fx', char: 'X', scores, overall, comments }); } return cs; }

async function makeHost(seed) {
  const rng = lcg(seed);
  const db = N.createMockDb();
  const code = 'FT' + seed;
  const decks = [mkDeck(24, 'A', rng), mkDeck(24, 'B', rng), mkDeck(24, 'C', rng)];
  await N.createRoom(db, code, { gmUid: 'uGM', gmName: GM, deckKey: 'test', now: 1 });
  for (let i = 0; i < PLAYERS.length; i++) { await N.claimSeat(db, code, PLAYERS[i], 'u' + i, PLAYERS[i]); await N.setPresence(db, code, 'u' + i); }
  const host = N.RoomHost(db, code, { engine: E, deckSets: decks, gmName: GM, now: 1 });
  const clients = {};
  for (const seat of PLAYERS) { const c = N.RoomClient(db, code, seat); c.onView(() => {}); clients[seat] = c; }
  await host.start(); await tick();
  return { db, code, host, clients, rng };
}

/* ---- テスト1: commitフェーズで2人だけ提出 → forceCompleteで3人目を埋め faces へ ---- */
async function testPartialCommit() {
  const { db, code, host, clients } = await makeHost(1234);
  // basis → commit
  ok(host.state.phase === 'basis', 'T1 開始はbasis');
  await host.announceBasis(host.state.config.sets[host.state.setIndex].fixedHalf || 'zen');
  ok(host.state.phase === 'commit', 'T1 commitへ');
  // 3人中2人だけコミット意図を送る（逆廻は送らない）
  const submitters = PLAYERS.filter((p) => p !== '逆廻');
  for (const seat of submitters) {
    const v = await db.ref(`rooms/${code}/views/${seat}`).get();
    await clients[seat].sendIntent('commit', { cardId: v.myHand[0] });
    await tick();
  }
  ok(host.state.phase === 'commit', 'T1 2人提出でまだcommit');
  ok(!host.state.committed['逆廻'], 'T1 逆廻は未コミット');
  const changed = await host.forceComplete();
  ok(changed === true, 'T1 forceCompleteがtrueを返す');
  ok(host.state.phase === 'faces', 'T1 forceComplete後 facesへ前進 (実:' + host.state.phase + ')');
  ok(Object.keys(host.state.committed).length === 3, 'T1 3人全員コミット済み');
  for (const p of PLAYERS) ok(!!host.state.committed[p], `T1 ${p} コミット確定`);
  host.stop(); Object.values(clients).forEach((c) => c.stop());
}

/* ---- テスト2: 逆廻が一切提出せず、GMがforceCompleteで全走行 → final ---- */
async function testFullGameWithDeadPlayer(seed) {
  const { db, code, host, clients, rng } = await makeHost(seed);
  const NEVER = '逆廻';
  const active = PLAYERS.filter((p) => p !== NEVER);
  let usedForce = false, guard = 0;
  while (guard++ < 500) {
    const st = host.state;
    if (st.phase === 'final') break;
    switch (st.phase) {
      case 'basis': await host.announceBasis(st.config.sets[st.setIndex].fixedHalf || (rng() < .5 ? 'zen' : 'kou')); break;
      case 'commit': {
        for (const seat of active) { const v = await db.ref(`rooms/${code}/views/${seat}`).get(); await clients[seat].sendIntent('commit', { cardId: v.myHand[Math.floor(rng() * v.myHand.length)] }); await tick(); }
        ok(st.phase === 'commit' && !host.state.committed[NEVER], `commit: ${NEVER}未提出`);
        const c = await host.forceComplete(); ok(c, 'commit forceComplete changed'); usedForce = true;
        break;
      }
      case 'faces': await host.revealFaces(); break;
      case 'declare': {
        for (const seat of active) { const others = PLAYERS.filter((q) => q !== seat); await clients[seat].sendIntent('declare', { self: rng() < .7 ? 1 + Math.floor(rng() * 5) : null, side: rng() < .5 ? { target: others[Math.floor(rng() * 2)], value: 1 + Math.floor(rng() * 5) } : null }); await tick(); }
        await host.forceComplete();
        break;
      }
      case 'scoresReady': await host.revealScores(); break;
      case 'roundEnd': await host.next(); break;
      case 'decisionPick': {
        for (const seat of active) { const v = await db.ref(`rooms/${code}/views/${seat}`).get(); await clients[seat].sendIntent('decisionPick', { cardId: v.myReserved[Math.floor(rng() * v.myReserved.length)] }); await tick(); }
        await host.forceComplete();
        break;
      }
      case 'decisionBasis': await host.decisionAnnounce(rng() < .5 ? 'zen' : 'kou'); break;
      case 'decisionDeclare': {
        for (const seat of active) await clients[seat].sendIntent('decisionGuess', { guess: rng() < .8 ? 1 + Math.floor(rng() * 5) : null });
        await tick();
        await host.forceComplete();
        break;
      }
      case 'decisionReady': await host.decisionReveal(); break;
      default: throw new Error('未処理: ' + st.phase);
    }
    await tick();
  }
  ok(host.state.phase === 'final', `seed${seed} deadプレイヤーでも final到達 (実:${host.state.phase})`);
  ok(usedForce, `seed${seed} forceComplete使用`);
  const rounds = host.state.history.filter((h) => h.kind === 'round').length;
  const decs = host.state.history.filter((h) => h.kind === 'decision').length;
  ok(rounds === 18, `seed${seed} 18ラウンド (実:${rounds})`);
  ok(decs === 1, `seed${seed} 決着1 (実:${decs})`);
  // 逆廻もスコアを持ち、最終ビューがホストと一致
  ok(typeof host.state.scores[NEVER] === 'number', `seed${seed} ${NEVER}のスコア存在`);
  for (const seat of PLAYERS) { const v = await db.ref(`rooms/${code}/views/${seat}`).get(); ok(JSON.stringify(v.scores) === JSON.stringify(host.state.scores), `seed${seed} ${seat} 最終スコア一致`); }
  host.stop(); Object.values(clients).forEach((c) => c.stop());
}

(async () => {
  console.log('部分コミット→forceComplete:');
  await testPartialCommit();
  console.log('deadプレイヤーでの全走行(forceComplete):');
  for (let s = 0; s < 10; s++) await testFullGameWithDeadPlayer(300 + s);
  console.log(`\n==== force結果: ${passed} passed, ${failed} failed ====`);
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error('FORCE FATAL:', e); process.exit(1); });
