/* net結合テスト: モックDB上で 3プレイヤー+GM の全走行・不正意図・再接続・秘匿検証 */
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
function collectScores(obj, sink) { if (obj === null || typeof obj !== 'object') return; if (typeof obj.base === 'number' && 'mod' in obj && Object.keys(obj).length <= 2) { sink.push(`${obj.base}|${obj.mod}`); return; } for (const k of Object.keys(obj)) collectScores(obj[k], sink); }
function assertViewClean(view, tag) {
  if (!view || view.isGM) return; // GMは答えを持ってよい
  const metaSigs = []; collectScores(view.cardMeta, metaSigs);
  ok(metaSigs.length === 0, `${tag}: cardMetaに点数`);
  const allowed = new Set();
  const gather = (r) => { if (r && r.entries) for (const e of r.entries) if (e.score) allowed.add(`${e.score.base}|${e.score.mod}`); };
  gather(view.roundResult); (view.history || []).forEach(gather); gather(view.decision && view.decision.result);
  const all = []; collectScores({ committedCards: view.committedCards, roundResult: view.roundResult, history: view.history, decision: view.decision, myHand: view.myHand, myReserved: view.myReserved }, all);
  const leak = all.find((x) => !allowed.has(x));
  ok(!leak, `${tag}: 未公開点数リーク ${leak || ''}`);
}

async function playFullGame(seed, { injectIllegal = false, reconnectAt = -1 } = {}) {
  const rng = lcg(seed);
  const db = N.createMockDb();
  const code = 'TEST' + seed;
  const decks = [mkDeck(24, 'A', rng), mkDeck(24, 'B', rng), mkDeck(24, 'C', rng)];
  // 部屋作成 + 参加
  await N.createRoom(db, code, { gmUid: 'uGM', gmName: GM, deckKey: 'test', now: 1 });
  for (let i = 0; i < PLAYERS.length; i++) { await N.claimSeat(db, code, PLAYERS[i], 'u' + i, PLAYERS[i]); await N.setPresence(db, code, 'u' + i); }

  let publishCount = 0;
  let host = N.RoomHost(db, code, { engine: E, deckSets: decks, gmName: GM, now: 1, onPublish: () => { publishCount++; } });
  // publish毎に全座席の公開ビューを検証
  const verifyPublishedViews = async (tag) => {
    for (const seat of PLAYERS) { const v = await db.ref(`rooms/${code}/views/${seat}`).get(); assertViewClean(v, `${tag}/${seat}`); }
  };

  // クライアント購読
  const clients = {};
  for (const seat of PLAYERS) { const c = N.RoomClient(db, code, seat); c.onView(() => {}); clients[seat] = c; }
  await host.start();
  await tick();

  let didIllegal = false, reconnected = false, guard = 0;
  while (guard++ < 500) {
    const st = host.state;
    if (st.phase === 'final') break;

    // 再接続シミュレーション: 指定publish回数でGMホストを落として復帰
    if (reconnectAt >= 0 && !reconnected && publishCount >= reconnectAt) {
      host.stop();
      db.ref.__simulateDisconnect && db.ref.__simulateDisconnect('rooms/' + code + '/presence/uGM');
      host = N.RoomHost(db, code, { engine: E, deckSets: decks, gmName: GM, now: 1, onPublish: () => { publishCount++; } });
      const how = await host.start(); // hostStateから resume するはず
      ok(how === 'resumed', `seed${seed} GM再接続でresume`);
      reconnected = true;
      await tick();
      continue;
    }

    switch (st.phase) {
      case 'basis': await host.announceBasis(st.config.sets[st.setIndex].fixedHalf || (rng() < .5 ? 'zen' : 'kou')); break;
      case 'commit': {
        // 各プレイヤーは自分のビューから手札を見て意図送信
        for (const seat of PLAYERS) {
          const v = await db.ref(`rooms/${code}/views/${seat}`).get();
          const hand = v.myHand;
          if (injectIllegal && !didIllegal && seat === 'からあげ') {
            await clients[seat].sendIntent('commit', { cardId: 'NONEXIST' }); // 不正
            didIllegal = true;
            await tick();
          }
          await clients[seat].sendIntent('commit', { cardId: hand[Math.floor(rng() * hand.length)] });
          await tick();
        }
        break;
      }
      case 'faces': await verifyPublishedViews('faces'); await host.revealFaces(); break;
      case 'declare': {
        for (const seat of PLAYERS) {
          const v = await db.ref(`rooms/${code}/views/${seat}`).get();
          const others = PLAYERS.filter((q) => q !== seat);
          await clients[seat].sendIntent('declare', { self: rng() < .7 ? 1 + Math.floor(rng() * 5) : null, side: rng() < .5 ? { target: others[Math.floor(rng() * 2)], value: 1 + Math.floor(rng() * 5) } : null });
          await tick();
        }
        break;
      }
      case 'scoresReady': await host.revealScores(); await verifyPublishedViews('roundEnd'); break;
      case 'roundEnd': await host.next(); break;
      case 'decisionPick': {
        for (const seat of PLAYERS) { const v = await db.ref(`rooms/${code}/views/${seat}`).get(); await clients[seat].sendIntent('decisionPick', { cardId: v.myReserved[Math.floor(rng() * v.myReserved.length)] }); await tick(); }
        break;
      }
      case 'decisionBasis': await host.decisionAnnounce(rng() < .5 ? 'zen' : 'kou'); break;
      case 'decisionDeclare': { for (const seat of PLAYERS) await clients[seat].sendIntent('decisionGuess', { guess: rng() < .8 ? 1 + Math.floor(rng() * 5) : null }); await tick(); break; }
      case 'decisionReady': await host.decisionReveal(); break;
      default: throw new Error('未処理: ' + st.phase);
    }
    await tick();
  }

  ok(host.state.phase === 'final', `seed${seed} 完走`);
  const rounds = host.state.history.filter((h) => h.kind === 'round').length;
  const decs = host.state.history.filter((h) => h.kind === 'decision').length;
  ok(rounds === 18, `seed${seed} 18ラウンド (実:${rounds})`);
  ok(decs === 1, `seed${seed} 決着1`);
  // 最終ビューのスコアがホストと一致
  for (const seat of PLAYERS) { const v = await db.ref(`rooms/${code}/views/${seat}`).get(); ok(JSON.stringify(v.scores) === JSON.stringify(host.state.scores), `seed${seed} ${seat} 最終スコア一致`); }
  if (injectIllegal) { const err = await db.ref(`rooms/${code}/errors/からあげ`).get(); ok(!!err, `seed${seed} 不正意図でエラー記録`); ok(didIllegal, '不正注入した'); }
  host.stop(); Object.values(clients).forEach((c) => c.stop());
  return host.state;
}

(async () => {
  console.log('全走行 x30:');
  for (let s = 0; s < 30; s++) await playFullGame(700 + s);
  console.log('不正意図の拒否:');
  await playFullGame(9001, { injectIllegal: true });
  console.log('GM再接続（中盤で落として復帰）:');
  await playFullGame(9100, { reconnectAt: 20 });
  await playFullGame(9101, { reconnectAt: 55 });
  console.log('座席競合:');
  {
    const db = N.createMockDb();
    await N.createRoom(db, 'RC', { gmUid: 'g', gmName: GM, deckKey: 't', now: 1 });
    await N.claimSeat(db, 'RC', 'からあげ', 'u1', 'からあげ');
    let threw = false;
    try { await N.claimSeat(db, 'RC', 'からあげ', 'u2', '別人'); } catch (e) { threw = true; }
    ok(threw, '使用中座席の二重取得を拒否');
    // 同一uidの再取得（リロード）は許可
    let ok2 = true; try { await N.claimSeat(db, 'RC', 'からあげ', 'u1', 'からあげ'); } catch (e) { ok2 = false; }
    ok(ok2, '同一uidの再取得（リロード）は許可');
  }
  console.log(`\n==== net結果: ${passed} passed, ${failed} failed ====`);
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error('NET FATAL:', e); process.exit(1); });
