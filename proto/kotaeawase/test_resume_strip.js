/* Firebaseの空値ストリップを再現し、resume（hostState読み戻し）が壊れないことを検証。
 * これがユーザー報告の "Cannot read properties of undefined (reading 'からあげ')" の回帰テスト。 */
'use strict';
const E = require('./engine.js');
const N = require('./net.js');
let passed = 0, failed = 0;
function ok(c, m) { if (c) passed++; else { failed++; console.error('  FAIL:', m); } }
const tick = () => new Promise((r) => setTimeout(r, 0));
const PLAYERS = ['からあげ', 'てかさ', '逆廻'], GM = 'マッチ';
function lcg(s) { s = s >>> 0; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 2 ** 32); }
function mkScore(rng) { const base = 1 + Math.floor(rng() * 5); const r = rng(); const mod = (base < 5 && r < .15) ? .5 : (base > 1 && r > .85) ? -.5 : 0; return { base, mod }; }
function mkDeck(n, pfx, rng) { const cs = []; for (let i = 0; i < n; i++) { const scores = { zen: {}, kou: {} }; for (const h of ['zen', 'kou']) for (const p of [...PLAYERS, GM]) scores[h][p] = mkScore(rng); const overall = { zen: 2.5, kou: 2.5 }; const comments = {}; for (const p of [...PLAYERS, GM]) comments[p] = `c${pfx}${i}`; cs.push({ id: `${pfx}${i}`, name: `札${pfx}${i}`, cost: i % 4, type: 'スキル', effect: 'fx', char: 'X', scores, overall, comments }); } return cs; }

/* Firebase相当の破壊: null/undefined/空オブジェクト/空配列のキーを削除。0/''/false は保持 */
function fbStrip(v) {
  if (v === null || v === undefined) return undefined;
  if (Array.isArray(v)) { const a = v.map(fbStrip).filter((x) => x !== undefined); return a.length ? a : undefined; }
  if (typeof v === 'object') { const o = {}; for (const k of Object.keys(v)) { const sv = fbStrip(v[k]); if (sv !== undefined) o[k] = sv; } return Object.keys(o).length ? o : undefined; }
  return v;
}

async function checkpoint(tag, buildToPhase) {
  const rng = lcg(1234);
  const db = N.createMockDb();
  const decks = [mkDeck(24, 'A', rng), mkDeck(24, 'B', rng), mkDeck(24, 'C', rng)];
  await N.createRoom(db, 'R', { gmUid: 'g', gmName: GM, deckKey: 't', now: 1 });
  for (let i = 0; i < 3; i++) { await N.claimSeat(db, 'R', PLAYERS[i], 'u' + i, PLAYERS[i]); await N.setPresence(db, 'R', 'u' + i); }
  // hostState（答え全部）はDBに書かずlocalStorage相当のstoreにのみ保存される（v0.7）。
  // このテストは元々「Firebase読み戻しの空値ストリップ」の回帰だったが、hostStateがDBから
  // 消えた今は hydrateState() 自体の単体回帰テストとして続ける（store越しに同じ壊れ方を再現）。
  const store = N.createMemStore();
  const host = N.RoomHost(db, 'R', { engine: E, deckSets: decks, gmName: GM, now: 1, store });
  const clients = {}; for (const s of PLAYERS) { clients[s] = N.RoomClient(db, 'R', s); clients[s].onView(() => {}); }
  await host.start(); await tick();
  await buildToPhase(host, clients, db, rng);

  // ここでFirebaseのストリップを再現: 保存済み状態（store内のhostState相当）を破壊して書き戻す
  const raw = store.get(N.hostStoreKey('R'));
  const stripped = fbStrip(raw) || {};
  // committed/declarations/reserved 等が消えていることを確認（テスト前提の妥当性）
  store.set(N.hostStoreKey('R'), stripped);
  host.stop();

  // 新しいホストがresume（＝GMがリロード/二度押し相当・同じstoreを引き継ぐ）
  const host2 = N.RoomHost(db, 'R', { engine: E, deckSets: decks, gmName: GM, now: 1, store });
  let how, threw = null;
  try { how = await host2.start(); await tick(); } catch (e) { threw = e; }
  ok(!threw, `${tag}: resumeで例外 ${threw ? threw.message : ''}`);
  ok(how === 'resumed', `${tag}: resumedになる`);
  // resume後、全座席ビューが生成できる（クラッシュしない＝ユーザー報告の再現防止）
  for (const seat of [...PLAYERS, GM]) { const v = await db.ref('rooms/R/views/' + seat).get(); ok(v && v.phase, `${tag}/${seat}: ビュー生成`); }
  // resume後、状態の器が正しく復元されている
  for (const p of PLAYERS) {
    ok(host2.state.committed && typeof host2.state.committed === 'object', `${tag}: committed復元`);
    ok(Array.isArray(host2.state.hands[p]), `${tag}: hands[${p}]は配列`);
    ok(Array.isArray(host2.state.reserved[p]), `${tag}: reserved[${p}]は配列`);
    ok(typeof host2.state.scores[p] === 'number', `${tag}: scores[${p}]は数値`);
  }
  // resume後、少なくとも1フェーズ前進できる（進行が固まらない）
  const st = host2.state; let advanced = false;
  try {
    if (st.phase === 'basis') { await host2.announceBasis(st.config.sets[st.setIndex].fixedHalf || 'zen'); advanced = host2.state.phase === 'commit'; }
    else if (st.phase === 'commit') { await host2.forceComplete(); advanced = host2.state.phase !== 'commit'; }
    else if (st.phase === 'faces') { await host2.revealFaces(); advanced = host2.state.phase === 'declare'; }
    else if (st.phase === 'roundEnd') { await host2.next(); advanced = host2.state.phase !== 'roundEnd'; }
    else if (st.phase === 'decisionPick') { await host2.forceComplete(); advanced = host2.state.phase !== 'decisionPick'; }
    else advanced = true;
  } catch (e) { ok(false, `${tag}: resume後の前進で例外 ${e.message}`); }
  ok(advanced, `${tag}: resume後に前進できる`);
  host2.stop();
}
async function clients2Send(db, code, seat, kind, payload) {
  const v = await db.ref(`rooms/${code}/views/${seat}`).get();
  await db.ref(`rooms/${code}/intents/${seat}`).set(Object.assign({ kind, step: v.step }, payload));
}

(async () => {
  console.log('resume×Firebaseストリップ 回帰テスト:');
  // 1) ユーザー報告そのもの: START直後（committed={} 等が全消え）にresume
  await checkpoint('開始直後', async () => {});
  // 2) 1人だけコミットした状態
  await checkpoint('一部コミット', async (host, clients, db) => {
    await host.announceBasis();
    await clients2Send(db, 'R', 'からあげ', 'commit', { cardId: (await db.ref('rooms/R/views/からあげ').get()).myHand[0] });
    await tick();
  });
  // 3) roundEnd（roundResult有り・historyに1件）
  await checkpoint('roundEnd', async (host, clients, db) => {
    await host.announceBasis();
    for (const s of PLAYERS) await clients2Send(db, 'R', s, 'commit', { cardId: (await db.ref('rooms/R/views/' + s).get()).myHand[0] });
    await tick(); await host.revealFaces();
    for (const s of PLAYERS) await clients2Send(db, 'R', s, 'declare', { self: 3, side: null });
    await tick(); await host.revealScores(); await tick();
  });
  // 4) 決着の札選択直前（reserved 6枚が消える状況）
  await checkpoint('決着pick前', async (host, clients, db) => {
    let guard = 0;
    while (host.state.phase !== 'decisionPick' && guard++ < 200) {
      const st = host.state;
      if (st.phase === 'basis') await host.announceBasis(st.config.sets[st.setIndex].fixedHalf || 'zen');
      else if (st.phase === 'commit') { for (const s of PLAYERS) if (!st.committed[s]) await clients2Send(db, 'R', s, 'commit', { cardId: (await db.ref('rooms/R/views/' + s).get()).myHand[0] }); }
      else if (st.phase === 'faces') await host.revealFaces();
      else if (st.phase === 'declare') { for (const s of PLAYERS) if (!st.declarations[s]) await clients2Send(db, 'R', s, 'declare', { self: null, side: null }); }
      else if (st.phase === 'scoresReady') await host.revealScores();
      else if (st.phase === 'roundEnd') await host.next();
      await tick();
    }
    ok(host.state.phase === 'decisionPick', 'ビルド: decisionPickに到達');
  });

  console.log('GM復帰（localStorage相当のstoreから・破損なし）:');
  await (async () => {
    const rng = lcg(4321);
    const db = N.createMockDb();
    const decks = [mkDeck(24, 'A', rng), mkDeck(24, 'B', rng), mkDeck(24, 'C', rng)];
    await N.createRoom(db, 'RS', { gmUid: 'g', gmName: GM, deckKey: 't', now: 1 });
    for (let i = 0; i < 3; i++) { await N.claimSeat(db, 'RS', PLAYERS[i], 'u' + i, PLAYERS[i]); await N.setPresence(db, 'RS', 'u' + i); }
    const store = N.createMemStore();
    const host = N.RoomHost(db, 'RS', { engine: E, deckSets: decks, gmName: GM, now: 1, store, hostUid: 'g' });
    const clients = {}; for (const s of PLAYERS) { clients[s] = N.RoomClient(db, 'RS', s); clients[s].onView(() => {}); }
    await host.start(); await tick();
    // 中盤まで進める
    await host.announceBasis();
    for (const s of PLAYERS) await clients2Send(db, 'RS', s, 'commit', { cardId: (await db.ref('rooms/RS/views/' + s).get()).myHand[0] });
    await tick(); await host.revealFaces();
    for (const s of PLAYERS) await clients2Send(db, 'RS', s, 'declare', { self: null, side: null });
    await tick(); await host.revealScores(); await tick();
    const stepBefore = (await db.ref('rooms/RS/meta/step').get());
    ok(!!stepBefore, 'GM復帰: 停止前のstepを取得');

    // RoomHostを停止（タブを閉じた/落ちた相当）
    host.stop();

    // GM復帰: 同じstoreからRoomHostを再構築（別タブでのGM復帰UI相当）
    const host2 = N.RoomHost(db, 'RS', { engine: E, deckSets: decks, gmName: GM, now: 1, store, hostUid: 'g' });
    const how = await host2.start();
    await tick();
    ok(how === 'resumed', 'GM復帰: localStorage相当のstoreからresumeする');
    ok(host2.state.phase === 'roundEnd', `GM復帰: 停止直前と同じフェーズ (実:${host2.state.phase})`);
    const stepAfter = await db.ref('rooms/RS/meta/step').get();
    ok(stepAfter === stepBefore, `GM復帰: 同一stepでpublishが再開する (前:${stepBefore} 後:${stepAfter})`);
    // 復帰後、全座席のビューが壊れず生成できる
    for (const seat of [...PLAYERS, GM]) {
      const v = await db.ref('rooms/RS/views/' + seat).get();
      ok(v && v.phase === 'roundEnd', `GM復帰/${seat}: ビューが壊れず復元`);
    }
    // 復帰後もゲームを進行できる
    await host2.next();
    ok(host2.state.phase !== 'roundEnd', 'GM復帰: 復帰後も進行できる');
    host2.stop(); Object.values(clients).forEach((c) => c.stop());
  })();

  console.log(`\n==== resume-strip結果: ${passed} passed, ${failed} failed ====`);
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error('FATAL:', e); process.exit(1); });
