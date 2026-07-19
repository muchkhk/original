/* =========================================================
 * net.js — オンライン対戦のトランスポート層（DOM非依存）
 *
 * 設計:
 *  - ホスト権威モデル。GM(マッチ)のクライアントが唯一の権威=ホスト。
 *  - プレイヤーは「意図(intent)」をDBに書くだけ。ホストが検証してエンジンに畳み込む。
 *  - ホストは座席別の秘匿ビュー(viewFor)をDBに公開。各クライアントは自分のビューだけ購読。
 *  - 畳み込みは冪等（state側で既適用を判定＋stepタグで別フェーズ混入を防止）。
 *  - ホストは復帰用に完全状態(hostState)も保存（答えを含む＝バンドルと同等の信頼前提）。
 *
 * DBアダプタ契約: ref(path) => {
 *   child(sub), set(v):Promise, update(obj):Promise, remove():Promise, get():Promise<value>,
 *   on('value', cb)=>unsub, push(v):Promise<{key}>, onDisconnect()=>{set(v),remove()} }
 * ========================================================= */
'use strict';

/* ---------------- 決定的ID（Date/Math.random非依存でも動く） ---------------- */
function makeId(prefix, seedBox) {
  seedBox.n = (seedBox.n || 0) + 1;
  return prefix + String(seedBox.n).padStart(6, '0');
}

/* ---------------- MockDB: RTDBの必要部分を忠実に模倣 ---------------- */
function createMockDb() {
  const root = {};                 // ツリー（プレーンオブジェクト）
  const listeners = [];            // {path, cb}
  const disconnects = new Map();   // uid相当キー -> [{path, action:'set'|'remove', value}]
  const pushSeed = { n: 0 };
  let pushCounter = 0;

  const clone = (v) => v === undefined ? null : JSON.parse(JSON.stringify(v));
  const parts = (p) => p.split('/').filter(Boolean);
  function getNode(path) { let n = root; for (const k of parts(path)) { if (n == null || typeof n !== 'object') return undefined; n = n[k]; } return n; }
  function setNode(path, value) {
    const ps = parts(path);
    if (ps.length === 0) { for (const k of Object.keys(root)) delete root[k]; if (value && typeof value === 'object') Object.assign(root, clone(value)); return; }
    let n = root;
    for (let i = 0; i < ps.length - 1; i++) { const k = ps[i]; if (n[k] == null || typeof n[k] !== 'object') n[k] = {}; n = n[k]; }
    const last = ps[ps.length - 1];
    if (value === null || value === undefined) delete n[last];
    else n[last] = clone(value);
  }
  function notify(changedPath) {
    const cp = changedPath.replace(/\/+$/, '');
    for (const l of listeners.slice()) {
      // value: リスナpathが変更pathの祖先or同一or子孫なら発火（RTDB近似）
      const lp = l.path.replace(/\/+$/, '');
      if (cp === lp || cp.startsWith(lp + '/') || lp.startsWith(cp + '/') || lp === '' ) {
        try { l.cb(clone(getNode(l.path))); } catch (e) { /* listener error ignored */ }
      }
    }
  }
  function ref(path) {
    return {
      path,
      child(sub) { return ref((path ? path + '/' : '') + sub); },
      async set(v) { setNode(path, v); notify(path); },
      async update(obj) { for (const k of Object.keys(obj)) setNode((path ? path + '/' : '') + k, obj[k]); notify(path); },
      async remove() { setNode(path, null); notify(path); },
      async get() { return clone(getNode(path)); },
      on(event, cb) {
        if (event !== 'value') return () => {};
        const l = { path, cb };
        listeners.push(l);
        Promise.resolve().then(() => { try { cb(clone(getNode(path))); } catch (e) {} }); // 初回発火
        return () => { const i = listeners.indexOf(l); if (i >= 0) listeners.splice(i, 1); };
      },
      async push(v) { const key = 'k' + String(++pushCounter).padStart(8, '0'); setNode((path ? path + '/' : '') + key, v); notify(path); return { key }; },
      onDisconnect() {
        return {
          set: async (v) => { const arr = disconnects.get(path) || []; arr.push({ path, action: 'set', value: v }); disconnects.set(path, arr); },
          remove: async () => { const arr = disconnects.get(path) || []; arr.push({ path, action: 'remove' }); disconnects.set(path, arr); },
        };
      },
    };
  }
  // テスト用: 切断シミュレーション
  ref.__simulateDisconnect = function (pathPrefix) {
    for (const [key, arr] of disconnects) {
      if (!pathPrefix || key.startsWith(pathPrefix)) {
        for (const d of arr) { if (d.action === 'remove') { setNode(d.path, null); } else { setNode(d.path, d.value); } notify(d.path); }
        disconnects.delete(key);
      }
    }
  };
  ref.__dump = () => clone(root);
  return { ref, _isMock: true };
}

/* ---------------- Firebase(compat)アダプタ（ブラウザのみ） ---------------- */
function createFirebaseDb(config) {
  if (typeof firebase === 'undefined') throw new Error('Firebase SDK未読込');
  if (!firebase.apps || !firebase.apps.length) firebase.initializeApp(config);
  const db = firebase.database();
  function wrap(fref) {
    return {
      path: fref.toString(),
      child(sub) { return wrap(fref.child(sub)); },
      set(v) { return fref.set(v); },
      update(obj) { return fref.update(obj); },
      remove() { return fref.remove(); },
      async get() { const s = await fref.once('value'); return s.val(); },
      on(event, cb) { const h = fref.on(event, (snap) => cb(snap.val())); return () => fref.off(event, h); },
      async push(v) { const r = fref.push(); await r.set(v); return { key: r.key }; },
      onDisconnect() { const od = fref.onDisconnect(); return { set: (v) => od.set(v), remove: () => od.remove() }; },
    };
  }
  return { ref: (path) => wrap(db.ref(path)), _isMock: false, _serverTime: firebase.database.ServerValue ? firebase.database.ServerValue.TIMESTAMP : 0 };
}

/* ---------------- Firebase読み戻しの正規化 ----------------
 * RTDBは空オブジェクト/空配列/null値のキーを保存しない。hostStateを読み戻すと
 * committed:{} 等が丸ごと消え、viewFor/reduce が undefined を座席名で索引して落ちる。
 * 復帰時に状態の器を再構築する。 */
function hydrateState(state) {
  if (!state || !state.config) return state;
  const P = state.config.players || [];
  state.committed = state.committed || {};
  state.declarations = state.declarations || {};
  state.hands = state.hands || {};
  state.reserved = state.reserved || {};
  state.scores = state.scores || {};
  state.history = state.history || [];
  for (const p of P) {
    if (!Array.isArray(state.hands[p])) state.hands[p] = state.hands[p] ? Object.values(state.hands[p]) : [];
    if (!Array.isArray(state.reserved[p])) state.reserved[p] = state.reserved[p] ? Object.values(state.reserved[p]) : [];
    if (typeof state.scores[p] !== 'number') state.scores[p] = 0;
  }
  // deckSets は配列の配列。RTDBが疎配列をオブジェクト化した場合に備え正規化
  if (state.deckSets && !Array.isArray(state.deckSets)) state.deckSets = Object.values(state.deckSets);
  if (Array.isArray(state.deckSets)) state.deckSets = state.deckSets.map((d) => Array.isArray(d) ? d : (d ? Object.values(d) : []));
  // 決着フェーズでは decisionState 全体（picks:{},guesses:{},half:null,result:null）が
  // 空になり丸ごと消えることがある。フェーズから必要性を判断して器を復元。
  const inDecision = state.phase && (state.phase.startsWith('decision') || state.phase === 'final');
  if (!state.decisionState && inDecision) state.decisionState = { picks: {}, guesses: {}, half: null, result: null };
  if (state.decisionState) {
    state.decisionState.picks = state.decisionState.picks || {};
    state.decisionState.guesses = state.decisionState.guesses || {};
    if (!('half' in state.decisionState)) state.decisionState.half = null;
    if (!('result' in state.decisionState)) state.decisionState.result = null;
  }
  return state;
}

/* ---------------- ステップタグ（フェーズ混入防止） ---------------- */
function stepOf(state) {
  const p = state.phase;
  if (p.startsWith('decision') || p === 'final') return `D:${p}`;
  return `${state.setIndex}:${state.roundIndex}:${p}`;
}

/* ---------------- ホスト状態のローカル保存（DBには答えを書かない） ----------------
 * hostState（答え全部）はRTDBに書かず、GM端末のlocalStorageにのみ保持する。
 * ブラウザ外（Node）ではlocalStorageが無いため、プロセス内Mapへフォールバックする
 * （＝同一プロセス内でのRoomHost再生成＝ページリロード相当を模倣）。 */
const _memStore = new Map();
function hostStoreKey(code) { return `kotaeawase:host:${code}`; }
function defaultLocalStore() {
  if (typeof localStorage !== 'undefined') {
    return {
      get(k) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : null; } catch (e) { return null; } },
      set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
    };
  }
  return {
    get(k) { return _memStore.has(k) ? JSON.parse(JSON.stringify(_memStore.get(k))) : null; },
    set(k, v) { _memStore.set(k, JSON.parse(JSON.stringify(v))); },
  };
}
/* テスト用: 他と独立した専用ストア（プロセス内Mapを共有させたくない場合） */
function createMemStore() {
  const m = new Map();
  return {
    get(k) { return m.has(k) ? JSON.parse(JSON.stringify(m.get(k))) : null; },
    set(k, v) { m.set(k, JSON.parse(JSON.stringify(v))); },
  };
}

/* ---------------- RoomHost（GMクライアントが所有） ---------------- */
function RoomHost(db, code, opts) {
  const E = opts.engine;
  const base = `rooms/${code}`;
  const storeKey = hostStoreKey(code);
  const self = {
    state: null, undoStack: [], _unsubs: [], _startedAt: opts.now || 0,
    deckSets: opts.deckSets, gmName: opts.gmName, deckKey: opts.deckKey,
    onPublish: opts.onPublish || (() => {}),
    store: opts.store || defaultLocalStore(),
    hostUid: opts.hostUid || null,
  };

  async function loadOrCreate() {
    const saved = self.store.get(storeKey);
    if (saved && saved.phase) { self.state = hydrateState(saved); return 'resumed'; }
    const balanced = self.deckSets.map((d) => E.balanceDeal(d, E.DEFAULT_CONFIG.players, E.DEFAULT_CONFIG.handSize));
    self.state = E.reduce(E.createGame(balanced, null, {}), { type: E.Actions.START_GAME });
    return 'created';
  }

  async function publish() {
    // 二重ホスト防止: 自分がmeta/hostUidでなくなっていたら（別タブが復帰済み）publishしない
    if (self.hostUid) {
      const cur = await db.ref(`${base}/meta/hostUid`).get();
      if (cur && cur !== self.hostUid) { self.stop(); return; }
    }
    const st = self.state;
    const step = stepOf(st);
    const seats = [...st.config.players, st.config.gmName];
    const updates = {};
    for (const seat of seats) {
      const v = E.viewFor(st, seat);
      v.step = step;
      updates[`views/${seat}`] = v;
    }
    updates['meta/step'] = step;
    updates['meta/phase'] = st.phase;
    updates['meta/finished'] = st.phase === 'final';
    await db.ref(base).update(updates);
    self.store.set(storeKey, st);            // 復帰用（答え含む＝信頼前提）はローカル保存のみ
    self.onPublish(st);
  }

  function applyGm(action) {
    const next = E.reduce(self.state, action); // 先にreduce（例外はここで＝mutation前）
    self.undoStack.push(self.state);
    if (self.undoStack.length > 300) self.undoStack.shift();
    self.state = next;
  }

  self.forceComplete = async () => {
    const st = self.state, A = E.Actions; let changed = false;
    if (st.phase === 'commit') { for (const p of st.config.players) if (!st.committed[p]) { self.state = E.reduce(self.state, { type: A.COMMIT, player: p, cardId: st.hands[p][0] }); changed = true; } }
    else if (st.phase === 'declare') { for (const p of st.config.players) if (!st.declarations[p]) { self.state = E.reduce(self.state, { type: A.DECLARE, player: p, self: null, side: null }); changed = true; } }
    else if (st.phase === 'decisionPick') { for (const p of st.config.players) if (!st.decisionState.picks[p]) { self.state = E.reduce(self.state, { type: A.DECISION_PICK, player: p, cardId: st.reserved[p][0] }); changed = true; } }
    else if (st.phase === 'decisionDeclare') { for (const p of st.config.players) if (!(p in st.decisionState.guesses)) { self.state = E.reduce(self.state, { type: A.DECISION_DECLARE, player: p, guess: null }); changed = true; } }
    if (changed) await publish();
    return changed;
  };

  async function foldIntents(intents) {
    if (!intents) return;
    const st = self.state;
    const step = stepOf(st);
    let changed = false;
    const A = E.Actions;
    for (const seat of st.config.players) {
      const it = intents[seat];
      if (!it || it.step !== step) continue;
      try {
        if (it.kind === 'commit' && st.phase === 'commit' && !st.committed[seat]) { self.state = E.reduce(self.state, { type: A.COMMIT, player: seat, cardId: it.cardId }); changed = true; }
        else if (it.kind === 'declare' && st.phase === 'declare' && !st.declarations[seat]) { self.state = E.reduce(self.state, { type: A.DECLARE, player: seat, self: it.self ?? null, side: it.side ?? null }); changed = true; }
        else if (it.kind === 'decisionPick' && st.phase === 'decisionPick' && !st.decisionState.picks[seat]) { self.state = E.reduce(self.state, { type: A.DECISION_PICK, player: seat, cardId: it.cardId }); changed = true; }
        else if (it.kind === 'decisionGuess' && st.phase === 'decisionDeclare' && !(seat in st.decisionState.guesses)) { self.state = E.reduce(self.state, { type: A.DECISION_DECLARE, player: seat, guess: it.guess ?? null }); changed = true; }
      } catch (e) {
        // 不正意図: 無視し、当該座席に軽いフィードバックを残す
        await db.ref(`${base}/errors/${seat}`).set({ step, msg: String(e.message || e) });
      }
    }
    if (changed) { self.undoStack.push(st); await publish(); }
  }

  self.start = async function (preloadedState) {
    let how;
    if (preloadedState) { self.state = hydrateState(preloadedState); how = 'resumed'; }
    else { how = await loadOrCreate(); }
    await publish();
    // フェーズが進むたびに古い意図は publish 内で消える。意図購読開始。
    self._unsubs.push(db.ref(`${base}/intents`).on('value', (intents) => { foldIntents(intents); }));
    return how;
  };
  // GMアクション
  self.announceBasis = async (half) => { applyGm({ type: E.Actions.ANNOUNCE_BASIS, half }); await publish(); };
  self.revealFaces = async () => { applyGm({ type: E.Actions.REVEAL_FACES }); await publish(); };
  self.revealScores = async () => { applyGm({ type: E.Actions.REVEAL_SCORES }); await publish(); };
  self.next = async () => { applyGm({ type: E.Actions.NEXT }); await publish(); };
  self.decisionAnnounce = async (half) => { applyGm({ type: E.Actions.DECISION_ANNOUNCE, half }); await publish(); };
  self.decisionReveal = async () => { applyGm({ type: E.Actions.DECISION_REVEAL }); await publish(); };
  self.undo = async () => { if (!self.undoStack.length) return false; self.state = self.undoStack.pop(); await publish(); return true; };
  self.resync = async () => { await publish(); };
  self.stop = () => { self._unsubs.forEach((u) => u()); self._unsubs = []; };
  return self;
}

/* ---------------- RoomClient（プレイヤー） ---------------- */
function RoomClient(db, code, seat) {
  const base = `rooms/${code}`;
  const c = { seat, _unsubs: [], view: null };
  c.onView = (cb) => { c._unsubs.push(db.ref(`${base}/views/${seat}`).on('value', (v) => { c.view = v; cb(v); })); };
  c.onError = (cb) => { c._unsubs.push(db.ref(`${base}/errors/${seat}`).on('value', (e) => { if (e) cb(e); })); };
  c.sendIntent = async (kind, payload) => {
    const step = c.view && c.view.step;
    await db.ref(`${base}/intents/${seat}`).set(Object.assign({ kind, step }, payload));
  };
  c.stop = () => { c._unsubs.forEach((u) => u()); c._unsubs = []; };
  return c;
}

/* ---------------- ルーム参加・在席・座席 ---------------- */
async function createRoom(db, code, { gmUid, gmName, deckKey, now, ver }) {
  await db.ref(`rooms/${code}/meta`).set({ hostUid: gmUid, gmName, deckKey, engine: 'kotaeawase', createdAt: now || 0, started: false, finished: false, ver: ver || null });
  await claimSeat(db, code, gmName, gmUid, gmName);
  await setPresence(db, code, gmUid);
}
async function claimSeat(db, code, seat, uid, name) {
  const ref = db.ref(`rooms/${code}/seats/${seat}`);
  const cur = await ref.get();
  if (cur && cur.uid && cur.uid !== uid) throw new Error(`座席「${seat}」は使用中`);
  await ref.set({ uid, name: name || seat });
  return true;
}
async function setPresence(db, code, uid) {
  const ref = db.ref(`rooms/${code}/presence/${uid}`);
  await ref.set(true);
  try { await ref.onDisconnect().set(false); } catch (e) {}
}
/* Wi-Fi瞬断・スリープ復帰後にpresenceが false のまま固まらないよう、
 * .info/connected が true に戻るたびに presence を再設定＋onDisconnectを張り直す。
 * .info/connected を購読できない環境（MockDB等）では、従来どおり1回だけsetして例外を投げない。 */
function keepPresence(db, code, uid) {
  const ref = db.ref(`rooms/${code}/presence/${uid}`);
  const setOn = async () => { try { await ref.set(true); await ref.onDisconnect().set(false); } catch (e) {} };
  setOn();
  try {
    const infoRef = db.ref('.info/connected');
    if (infoRef && typeof infoRef.on === 'function') {
      return infoRef.on('value', (connected) => { if (connected) setOn(); });
    }
  } catch (e) { /* .info/connected 非対応: 上のsetOn()一回きりにフォールバック */ }
  return () => {};
}
function watchRoom(db, code, cb) {
  const unsubs = [
    db.ref(`rooms/${code}/seats`).on('value', (seats) => cb({ seats })),
    db.ref(`rooms/${code}/presence`).on('value', (presence) => cb({ presence })),
    db.ref(`rooms/${code}/meta`).on('value', (meta) => cb({ meta })),
  ];
  return () => unsubs.forEach((u) => u());
}
async function startRoom(db, code) { await db.ref(`rooms/${code}/meta/started`).set(true); }

/* ---------------- エクスポート ---------------- */
const NetAPI = { createMockDb, createFirebaseDb, RoomHost, RoomClient, createRoom, claimSeat, setPresence, keepPresence, watchRoom, startRoom, stepOf, makeId, hydrateState, hostStoreKey, defaultLocalStore, createMemStore };
if (typeof module !== 'undefined' && module.exports) module.exports = NetAPI;
if (typeof window !== 'undefined') window.Net = NetAPI;
