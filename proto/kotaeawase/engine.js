/* =========================================================
 * 『答え合わせ』(仮) ゲームエンジン v0.1
 * 純粋状態機械。UI/同期層から独立。ローカル試遊版・Firebase版で共用。
 *
 * 用語:
 *  half: 'zen'(前半) | 'kou'(後半)
 *  score: {base: 1..5, mod: -0.5 | 0 | +0.5}   // 4+ => {base:4, mod:+0.5}
 *  数値強度 = base + mod。宣言的中判定は base のみ比較（+/-は問わない）。
 * ========================================================= */
'use strict';

const ENGINE_VERSION = '0.3.0';

/* ---------- 設定（調整ノブは全部ここ） ---------- */
const DEFAULT_CONFIG = {
  players: ['からあげ', 'てかさ', '逆廻'],
  gmName: 'マッチ',
  sets: [
    { label: '第1章', winPoints: 3, fixedHalf: 'zen' },
    { label: '第2章', winPoints: 4, fixedHalf: 'kou' },
    { label: '第3章', winPoints: 5, fixedHalf: null }, // null = ラウンドごとにGMが宣言
  ],
  roundsPerSet: 6,
  handSize: 8,           // 8枚配布・6枚使用・2枚温存
  hitSelfPoints: 2,      // 自分宣言的中
  hitSidePoints: 2,      // 横予想的中
  decision: {
    enabled: true,
    label: '決着',
    winPoints: 6,        // 推奨値（v2の8から調整）
    hitPoints: 3,        // マッチ点数予想的中
  },
};

/* ---------- スコア表現ユーティリティ ---------- */
function numeric(score) {
  if (!score || typeof score.base !== 'number') return null;
  return score.base + (score.mod || 0);
}
function scoreLabel(score) {
  if (!score) return '—';
  const m = score.mod > 0 ? '+' : score.mod < 0 ? '-' : '';
  return `${score.base}${m}`;
}
/* 生文字列("4+","５-",3 等) → score オブジェクト。データ取込時に使用 */
function parseScoreStr(v) {
  if (v === null || v === undefined) return null;
  const s = String(v).trim().replace(/[０-９]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xfee0)).replace(/＋/g, '+').replace(/－/g, '-');
  const m = s.match(/^([1-5])(?:\.(5))?\s*([+-])?$/);
  if (!m) return null;
  let base = parseInt(m[1], 10);
  let mod = 0;
  if (m[3] === '+') mod = 0.5;
  else if (m[3] === '-') mod = -0.5;
  else if (m[2] === '5') { mod = 0.5; } // 3.5 のような数値表現は +0.5 とみなし base は整数部
  return { base, mod };
}

/* ---------- カード形状 ----------
 * card = {
 *   id, name, cost, type, effect,
 *   scores: { zen: {<player|gm>: score}, kou: {...} },
 *   overall: { zen: number, kou: number },   // 4人平均（タイブレーク用）
 *   comments: { <player|gm>: string }
 * }
 */

/* ---------- 状態 ---------- */
function createGame(deckSets, decisionPoolIndependent, config) {
  const cfg = deepMerge(structuredCloneSafe(DEFAULT_CONFIG), config || {});
  if (deckSets.length !== cfg.sets.length) throw new Error(`deckSets数(${deckSets.length})がconfig.sets数(${cfg.sets.length})と不一致`);
  deckSets.forEach((deck, i) => {
    const need = cfg.players.length * cfg.handSize;
    if (deck.length < need) throw new Error(`セット${i + 1}のデッキが${deck.length}枚しかない（必要${need}枚）`);
    validateDeck(deck, cfg, i);
  });
  return {
    engineVersion: ENGINE_VERSION,
    config: cfg,
    cardsById: indexCards(deckSets.flat()),
    deckSets: deckSets.map((d) => d.map((c) => c.id)),
    phase: 'lobby', // lobby → deal → basis → commit → faces → declare → scores → roundEnd → (setEnd) → decisionPick → decisionDeclare → decisionReveal → final
    setIndex: -1,
    roundIndex: -1,          // セット内 0-origin
    currentHalf: null,
    hands: {},               // player -> [cardId]（未使用の手札）
    reserved: {},            // player -> [cardId]（温存として残った札; セット終了時に確定）
    committed: {},           // player -> cardId
    declarations: {},        // player -> {self: int|null, side: {target, value}|null}
    scores: {},              // player -> 総得点
    roundResult: null,       // 直近ラウンドの判定詳細
    history: [],             // 全ラウンドのログ（ネタばらし画面用）
    decisionState: null,
    rngLog: [],
  };
}

function validateDeck(deck, cfg, setIdx) {
  const seen = new Set();
  for (const c of deck) {
    if (!c.id) throw new Error(`セット${setIdx + 1}: idなしカード`);
    if (seen.has(c.id)) throw new Error(`セット${setIdx + 1}: id重複 ${c.id}`);
    seen.add(c.id);
    for (const half of ['zen', 'kou']) {
      for (const p of cfg.players) {
        const s = c.scores?.[half]?.[p];
        if (!s || typeof s.base !== 'number' || s.base < 1 || s.base > 5) {
          throw new Error(`カード「${c.name}」(${c.id}) の ${half}/${p} の点数が不正: ${JSON.stringify(s)}`);
        }
      }
      if (typeof c.overall?.[half] !== 'number') {
        throw new Error(`カード「${c.name}」(${c.id}) の overall.${half} 欠落`);
      }
      if (cfg.decision.enabled) {
        const g = c.scores?.[half]?.[cfg.gmName];
        if (!g || typeof g.base !== 'number') throw new Error(`カード「${c.name}」(${c.id}) の ${half}/${cfg.gmName}(GM) 点数欠落（決着ラウンドに必要）`);
      }
    }
  }
}

function indexCards(cards) {
  const m = {};
  cards.forEach((c) => { m[c.id] = c; });
  return m;
}

/* ---------- アクション ---------- */
const Actions = {
  START_GAME: 'START_GAME',           // {} lobby → セット1配札
  ANNOUNCE_BASIS: 'ANNOUNCE_BASIS',   // {half} GMが基準宣言（固定セットではhalf省略可）
  COMMIT: 'COMMIT',                   // {player, cardId}
  REVEAL_FACES: 'REVEAL_FACES',       // {} GM操作
  DECLARE: 'DECLARE',                 // {player, self, side}
  REVEAL_SCORES: 'REVEAL_SCORES',     // {} GM操作 → 判定確定
  NEXT: 'NEXT',                       // {} roundEnd → 次ラウンド or 次セット or 決着へ
  DECISION_PICK: 'DECISION_PICK',     // {player, cardId} 温存2枚から1枚
  DECISION_ANNOUNCE: 'DECISION_ANNOUNCE', // {half} 決着の基準宣言 → 表公開
  DECISION_DECLARE: 'DECISION_DECLARE',   // {player, guess: int|null} マッチ点数予想
  DECISION_REVEAL: 'DECISION_REVEAL', // {} 判定確定 → final
};

function reduce(state, action) {
  const s = structuredCloneSafe(state); // 破壊回避（呼び出し側の巻き戻し・保全のため）
  const cfg = s.config;
  const type = action.type;

  const expect = (...phases) => {
    if (!phases.includes(s.phase)) throw new Error(`${type} はフェーズ ${s.phase} では実行不可（許可: ${phases.join(',')}）`);
  };
  const isPlayer = (p) => cfg.players.includes(p);

  switch (type) {
    case Actions.START_GAME: {
      expect('lobby');
      startSet(s, 0);
      return s;
    }

    case Actions.ANNOUNCE_BASIS: {
      expect('basis');
      const fixed = cfg.sets[s.setIndex].fixedHalf;
      const half = fixed || action.half;
      if (half !== 'zen' && half !== 'kou') throw new Error(`基準が不正: ${action.half}`);
      if (fixed && action.half && action.half !== fixed) throw new Error(`このセットの基準は${fixed}固定`);
      s.currentHalf = half;
      s.committed = {};
      s.declarations = {};
      s.phase = 'commit';
      return s;
    }

    case Actions.COMMIT: {
      expect('commit');
      const { player, cardId } = action;
      if (!isPlayer(player)) throw new Error(`不明なプレイヤー: ${player}`);
      if (s.committed[player]) throw new Error(`${player} はコミット済み`);
      const hand = s.hands[player];
      const idx = hand.indexOf(cardId);
      if (idx < 0) throw new Error(`${player} の手札に ${cardId} がない`);
      s.committed[player] = cardId;
      if (Object.keys(s.committed).length === cfg.players.length) s.phase = 'faces';
      return s;
    }

    case Actions.REVEAL_FACES: {
      expect('faces');
      s.phase = 'declare';
      return s;
    }

    case Actions.DECLARE: {
      expect('declare');
      const { player } = action;
      if (!isPlayer(player)) throw new Error(`不明なプレイヤー: ${player}`);
      if (s.declarations[player]) throw new Error(`${player} は宣言済み`);
      const decl = { self: null, side: null };
      if (action.self !== null && action.self !== undefined) {
        const v = Number(action.self);
        if (!Number.isInteger(v) || v < 1 || v > 5) throw new Error(`自分宣言は1〜5の整数: ${action.self}`);
        decl.self = v;
      }
      if (action.side) {
        const { target, value } = action.side;
        if (!isPlayer(target) || target === player) throw new Error(`横予想の対象が不正: ${target}`);
        const v = Number(value);
        if (!Number.isInteger(v) || v < 1 || v > 5) throw new Error(`横予想は1〜5の整数: ${value}`);
        decl.side = { target, value: v };
      }
      s.declarations[player] = decl;
      if (Object.keys(s.declarations).length === cfg.players.length) s.phase = 'scoresReady';
      return s;
    }

    case Actions.REVEAL_SCORES: {
      expect('scoresReady');
      s.roundResult = judgeRound(s);
      applyRoundResult(s, s.roundResult);
      s.phase = 'roundEnd';
      return s;
    }

    case Actions.NEXT: {
      expect('roundEnd');
      // 手札から使用済みを除去
      for (const p of cfg.players) {
        s.hands[p] = s.hands[p].filter((id) => id !== s.committed[p]);
      }
      const lastRoundOfSet = s.roundIndex + 1 >= cfg.roundsPerSet;
      if (!lastRoundOfSet) {
        s.roundIndex += 1;
        s.currentHalf = null;
        s.committed = {};
        s.declarations = {};
        s.roundResult = null;
        s.phase = 'basis';
        return s;
      }
      // セット終了: 残り手札を温存として記録
      for (const p of cfg.players) {
        s.reserved[p] = (s.reserved[p] || []).concat(s.hands[p]);
        s.hands[p] = [];
      }
      const lastSet = s.setIndex + 1 >= cfg.sets.length;
      if (!lastSet) {
        startSet(s, s.setIndex + 1);
        return s;
      }
      if (cfg.decision.enabled) {
        s.decisionState = { picks: {}, guesses: {}, half: null, result: null };
        s.phase = 'decisionPick';
      } else {
        s.phase = 'final';
      }
      return s;
    }

    case Actions.DECISION_PICK: {
      expect('decisionPick');
      const { player, cardId } = action;
      if (!isPlayer(player)) throw new Error(`不明なプレイヤー: ${player}`);
      if (s.decisionState.picks[player]) throw new Error(`${player} は選択済み`);
      if (!s.reserved[player].includes(cardId)) throw new Error(`${player} の温存札に ${cardId} がない`);
      s.decisionState.picks[player] = cardId;
      if (Object.keys(s.decisionState.picks).length === cfg.players.length) s.phase = 'decisionBasis';
      return s;
    }

    case Actions.DECISION_ANNOUNCE: {
      expect('decisionBasis');
      const half = action.half;
      if (half !== 'zen' && half !== 'kou') throw new Error(`基準が不正: ${half}`);
      s.decisionState.half = half;
      s.phase = 'decisionDeclare';
      return s;
    }

    case Actions.DECISION_DECLARE: {
      expect('decisionDeclare');
      const { player } = action;
      if (!isPlayer(player)) throw new Error(`不明なプレイヤー: ${player}`);
      if (player in s.decisionState.guesses) throw new Error(`${player} は予想済み`);
      let g = null;
      if (action.guess !== null && action.guess !== undefined) {
        const v = Number(action.guess);
        if (!Number.isInteger(v) || v < 1 || v > 5) throw new Error(`予想は1〜5の整数: ${action.guess}`);
        g = v;
      }
      s.decisionState.guesses[player] = g;
      if (Object.keys(s.decisionState.guesses).length === cfg.players.length) s.phase = 'decisionReady';
      return s;
    }

    case Actions.DECISION_REVEAL: {
      expect('decisionReady');
      const res = judgeDecision(s);
      s.decisionState.result = res;
      for (const [p, pts] of Object.entries(res.pointsAwarded)) s.scores[p] += pts;
      s.history.push({ kind: 'decision', ...res });
      s.phase = 'final';
      return s;
    }

    default:
      throw new Error(`未知のアクション: ${type}`);
  }
}

function startSet(s, setIndex) {
  const cfg = s.config;
  s.setIndex = setIndex;
  s.roundIndex = 0;
  s.currentHalf = null;
  s.committed = {};
  s.declarations = {};
  s.roundResult = null;
  // 配札: deckSets[setIndex] の並び順どおりに配る（シャッフルは呼び出し側の責務。再現性のため）
  const ids = s.deckSets[setIndex].slice();
  const need = cfg.players.length * cfg.handSize;
  const dealt = ids.slice(0, need);
  cfg.players.forEach((p, i) => {
    s.hands[p] = dealt.filter((_, k) => k % cfg.players.length === i);
  });
  if (setIndex === 0) { s.scores = {}; cfg.players.forEach((p) => { s.scores[p] = 0; }); s.reserved = {}; cfg.players.forEach((p) => { s.reserved[p] = []; }); }
  s.phase = 'basis';
}

/* ---------- 判定 ---------- */
function judgeRound(s) {
  const cfg = s.config;
  const half = s.currentHalf;
  const entries = cfg.players.map((p) => {
    const card = s.cardsById[s.committed[p]];
    const sc = card.scores[half][p];       // 自分基準核: 出した本人の点数
    return { player: p, cardId: card.id, cardName: card.name, score: sc, value: numeric(sc), overall: card.overall[half], comment: card.comments?.[p] ?? null };
  });

  const maxV = Math.max(...entries.map((e) => e.value));
  let top = entries.filter((e) => e.value === maxV);
  let tieBreakUsed = null;
  if (top.length > 1) {
    tieBreakUsed = 'overall';
    const maxO = Math.max(...top.map((e) => e.overall));
    const top2 = top.filter((e) => e.overall === maxO);
    if (top2.length < top.length && top2.length === 1) top = top2;
    else { top = top2; tieBreakUsed = 'split'; }
  }

  const winPoints = cfg.sets[s.setIndex].winPoints;
  const pointsAwarded = {};
  cfg.players.forEach((p) => { pointsAwarded[p] = 0; });
  if (top.length === 1) {
    pointsAwarded[top[0].player] += winPoints;
  } else {
    const each = Math.ceil(winPoints / top.length); // 山分けは切り上げ
    top.forEach((e) => { pointsAwarded[e.player] += each; });
  }

  // 宣言判定（的中＝baseの整数一致。+/-は問わない）
  const declResults = {};
  for (const p of cfg.players) {
    const d = s.declarations[p] || { self: null, side: null };
    const own = entries.find((e) => e.player === p);
    const r = { self: null, side: null };
    if (d.self !== null) {
      const hit = d.self === own.score.base;
      r.self = { value: d.self, hit };
      if (hit) pointsAwarded[p] += cfg.hitSelfPoints;
    }
    if (d.side) {
      const tgt = entries.find((e) => e.player === d.side.target);
      const hit = d.side.value === tgt.score.base;
      r.side = { target: d.side.target, value: d.side.value, hit, actualBase: tgt.score.base };
      if (hit) pointsAwarded[p] += cfg.hitSidePoints;
    }
    declResults[p] = r;
  }

  return {
    kind: 'round',
    set: s.setIndex, round: s.roundIndex, half,
    entries, winners: top.map((e) => e.player), tieBreakUsed,
    declResults, pointsAwarded, winPoints,
  };
}

function applyRoundResult(s, res) {
  for (const [p, pts] of Object.entries(res.pointsAwarded)) s.scores[p] += pts;
  s.history.push(res);
}

function judgeDecision(s) {
  const cfg = s.config;
  const half = s.decisionState.half;
  const gm = cfg.gmName;
  const entries = cfg.players.map((p) => {
    const card = s.cardsById[s.decisionState.picks[p]];
    const sc = card.scores[half][gm];      // 決着: マッチ基準
    return { player: p, cardId: card.id, cardName: card.name, score: sc, value: numeric(sc), overall: card.overall[half], comment: card.comments?.[gm] ?? null };
  });
  const maxV = Math.max(...entries.map((e) => e.value));
  let top = entries.filter((e) => e.value === maxV);
  let tieBreakUsed = null;
  if (top.length > 1) {
    tieBreakUsed = 'overall';
    const maxO = Math.max(...top.map((e) => e.overall));
    const top2 = top.filter((e) => e.overall === maxO);
    if (top2.length === 1) top = top2; else { top = top2; tieBreakUsed = 'split'; }
  }
  const pointsAwarded = {};
  cfg.players.forEach((p) => { pointsAwarded[p] = 0; });
  if (top.length === 1) pointsAwarded[top[0].player] += cfg.decision.winPoints;
  else top.forEach((e) => { pointsAwarded[e.player] += Math.ceil(cfg.decision.winPoints / top.length); });

  const guessResults = {};
  for (const p of cfg.players) {
    const g = s.decisionState.guesses[p];
    if (g === null || g === undefined) { guessResults[p] = null; continue; }
    const own = entries.find((e) => e.player === p);
    const hit = g === own.score.base;
    guessResults[p] = { value: g, hit };
    if (hit) pointsAwarded[p] += cfg.decision.hitPoints;
  }
  return { half, entries, winners: top.map((e) => e.player), tieBreakUsed, guessResults, pointsAwarded };
}

/* ---------- 補助 ---------- */
function structuredCloneSafe(o) {
  return (typeof structuredClone === 'function') ? structuredClone(o) : JSON.parse(JSON.stringify(o));
}
function deepMerge(base, over) {
  if (Array.isArray(over)) return structuredCloneSafe(over);
  if (over && typeof over === 'object') {
    const out = { ...base };
    for (const k of Object.keys(over)) out[k] = deepMerge(base?.[k], over[k]);
    return out;
  }
  return over === undefined ? base : over;
}

/* ---------- 平準化配札 ----------
 * 各プレイヤーの「自分の点数での手札平均」（前後半の平均）の差を targetSpread 以下へ
 * 緩やかに近づける（ほんのり平等）。完全均一化はしない。
 * 戻り値: 配札順に並べ替えたデッキ（reduce側のラウンドロビン配札でこの手札になる）
 */
function balanceDeal(deck, players, handSize, rng, targetSpread = 0.4, maxIter = 400) {
  rng = rng || Math.random;
  const need = players.length * handSize;
  if (deck.length < need) throw new Error(`balanceDeal: デッキ不足 ${deck.length} < ${need}`);
  const pool = deck.slice();
  for (let i = pool.length - 1; i > 0; i--) { const j = Math.floor(rng() * (i + 1)); [pool[i], pool[j]] = [pool[j], pool[i]]; }
  const hands = players.map((_, i) => pool.slice(i * handSize, (i + 1) * handSize));
  const rest = pool.slice(need);
  const val = (card, p) => (numeric(card.scores.zen[p]) + numeric(card.scores.kou[p])) / 2;
  const avg = (i) => hands[i].reduce((a, c) => a + val(c, players[i]), 0) / handSize;
  const spread = () => { const as = players.map((_, i) => avg(i)); return Math.max(...as) - Math.min(...as); };
  for (let it = 0; it < maxIter && spread() > targetSpread; it++) {
    const as = players.map((_, i) => avg(i));
    const hi = as.indexOf(Math.max(...as)), lo = as.indexOf(Math.min(...as));
    // hiの高価値札とloの低価値札を交換候補としてランダムに試す
    const a = Math.floor(rng() * handSize), b = Math.floor(rng() * handSize);
    const ca = hands[hi][a], cb = hands[lo][b];
    const before = spread();
    hands[hi][a] = cb; hands[lo][b] = ca;
    if (spread() >= before) { hands[hi][a] = ca; hands[lo][b] = cb; } // 改善しなければ戻す
  }
  // ラウンドロビン(k % n == i)で hands[i] が再現される並びに編む
  const out = [];
  for (let k = 0; k < handSize; k++) for (let i = 0; i < players.length; i++) out.push(hands[i][k]);
  return out.concat(rest);
}

/* =========================================================
 * viewFor(state, seat): 座席別の秘匿ビュー（オンライン配信用）
 *
 * 目的: 非GM座席のクライアントには「未公開の点数」を一切渡さない。
 *   - カードのメタデータ（名前/コスト/種別/効果=飾り/キャラ）は非秘匿（物理カードに書いてある）
 *   - 点数(scores)・全体評価(overall)は秘匿。公開されるのは判定確定後の
 *     roundResult / history / decisionState.result の中の entries のみ。
 *   - 自分の手札は自分には見える（メタのみ、点数なし）。他人の手札は名前も渡さない
 *     （コミット前）。コミット表公開後は各札のメタ（名前）だけ渡す。
 * GM座席には答えパネル用に committed/picked 札の点数を付与する（GMは信頼されたホスト）。
 * これは純粋関数。テストで「非GMビューに未公開点数が無い」ことを不変条件として検証する。
 * ========================================================= */
function meta(card) {
  return { id: card.id, name: card.name, cost: card.cost, type: card.type, char: card.char || null, effect: card.effect || '' };
}
function publicConfig(cfg) {
  return {
    players: cfg.players.slice(), gmName: cfg.gmName,
    sets: cfg.sets.map((s) => ({ label: s.label, winPoints: s.winPoints, fixedHalf: s.fixedHalf })),
    roundsPerSet: cfg.roundsPerSet, handSize: cfg.handSize,
    hitSelfPoints: cfg.hitSelfPoints, hitSidePoints: cfg.hitSidePoints,
    decision: { enabled: cfg.decision.enabled, label: cfg.decision.label, winPoints: cfg.decision.winPoints, hitPoints: cfg.decision.hitPoints },
  };
}
function viewFor(state, seat) {
  const cfg = state.config;
  const isGM = seat === cfg.gmName;
  const P = cfg.players;
  // Firebase等が空オブジェクト/配列/nullを削除して読み戻すケースへの防御
  const committed = state.committed || {};
  const declarations = state.declarations || {};
  const hands = state.hands || {};
  const reserved = state.reserved || {};
  const phase = state.phase;
  const facesShown = ['faces', 'declare', 'scoresReady', 'roundEnd'].includes(phase);
  const cardMeta = {}; // id -> メタのみ（点数なし）
  const addMeta = (id) => { if (id && !cardMeta[id]) cardMeta[id] = meta(state.cardsById[id]); };

  const v = {
    engineVersion: state.engineVersion,
    seat, isGM,
    config: publicConfig(cfg),
    phase, setIndex: state.setIndex, roundIndex: state.roundIndex, currentHalf: state.currentHalf,
    scores: { ...state.scores },
    committedSeats: {},   // seat -> bool（誰が出したか。札の中身は含めない）
    declaredSeats: {},    // seat -> bool
    committedCards: null, // faces以降のみ: seat -> meta
    roundResult: null,    // roundEndのみ: 公開データ
    history: [],          // 公開済み（全ラウンド）
    myHand: null,         // 自分の手札メタ
    myReserved: null,     // 決着時: 自分の温存メタ
    decision: null,
    gm: null,             // GM専用の答えパネル
  };

  for (const p of P) {
    v.committedSeats[p] = !!committed[p];
    v.declaredSeats[p] = !!declarations[p];
  }
  if (facesShown && Object.keys(committed).length) {
    v.committedCards = {};
    for (const p of P) if (committed[p]) { addMeta(committed[p]); v.committedCards[p] = committed[p]; }
  }
  if (phase === 'roundEnd' && state.roundResult) {
    v.roundResult = sanitizeResult(state.roundResult, cfg, addMeta, state.cardsById);
  }
  // history は全て公開済み（判定確定済のみ push される）
  v.history = state.history.map((h) => h.kind === 'decision' ? sanitizeDecision(h, cfg, addMeta, state.cardsById) : sanitizeResult(h, cfg, addMeta, state.cardsById));

  // 自分の手札（GMは手札なし）
  if (!isGM && hands[seat]) { v.myHand = hands[seat].map((id) => { addMeta(id); return id; }); }
  // 決着
  if (state.decisionState) {
    const ds = state.decisionState;
    const d = { half: ds.half, pickedSeats: {}, guessedSeats: {}, result: null, myPick: null };
    for (const p of P) { d.pickedSeats[p] = !!ds.picks[p]; d.guessedSeats[p] = (p in ds.guesses); }
    if (!isGM && reserved[seat]) v.myReserved = reserved[seat].map((id) => { addMeta(id); return id; });
    if (isGM) v.gmReserved = Object.fromEntries(P.map((p) => [p, (reserved[p] || []).map((id) => { addMeta(id); return id; })]));
    if (!isGM && ds.picks[seat]) { addMeta(ds.picks[seat]); d.myPick = ds.picks[seat]; }
    if (phase === 'final' && ds.result) d.result = sanitizeDecision({ kind: 'decision', ...ds.result }, cfg, addMeta, state.cardsById);
    // 決着の札公開（picks）はfinalでのみ全公開。それ以前は自分のpickのみ。
    if (phase === 'final') { d.picks = {}; for (const p of P) if (ds.picks[p]) { addMeta(ds.picks[p]); d.picks[p] = ds.picks[p]; } }
    v.decision = d;
  }

  // GM専用: いま場に出ている札の答え（点数・コメント・全体評価）
  if (isGM) {
    v.gm = { answers: {}, hands: {} };
    const half = state.currentHalf || state.decisionState?.half;
    const isDec = phase.startsWith('decision') || phase === 'final';
    const committed = isDec ? (state.decisionState?.picks || {}) : state.committed;
    for (const p of P) {
      const id = committed[p]; if (!id) continue;
      const c = state.cardsById[id]; addMeta(id);
      const rater = isDec ? cfg.gmName : p;
      v.gm.answers[p] = { cardId: id, rater, zen: c.scores.zen[rater], kou: c.scores.kou[rater], overallZen: c.overall.zen, overallKou: c.overall.kou, comment: c.comments?.[rater] ?? null };
    }
    // GMは全員の手札も把握（進行補助）。メタのみ＋自分点(=各自の点)を付す。
    for (const p of P) v.gm.hands[p] = (hands[p] || []).map((id) => { addMeta(id); const c = state.cardsById[id]; return { id, zen: c.scores.zen[p], kou: c.scores.kou[p] }; });
  }

  v.cardMeta = cardMeta;
  return v;
}
/* 判定確定済ラウンドの entries は点数・コメント込みで公開してよい。
 * §3-4（v4指示書）: ネタばらし画面での前半/後半併記のため、判定に使った側(score)に加え、
 * 同じ採点者による両半分(scoreZen/scoreKou)も公開する（cardsByIdから直接引く）。 */
function sanitizeResult(r, cfg, addMeta, cardsById) {
  return {
    kind: 'round', set: r.set, round: r.round, half: r.half,
    entries: r.entries.map((e) => {
      addMeta(e.cardId);
      const rater = e.player, card = cardsById[e.cardId];
      return { player: e.player, cardId: e.cardId, cardName: e.cardName, score: e.score, scoreZen: card.scores.zen[rater], scoreKou: card.scores.kou[rater], comment: e.comment };
    }),
    winners: r.winners.slice(), tieBreakUsed: r.tieBreakUsed,
    declResults: r.declResults, pointsAwarded: r.pointsAwarded, winPoints: r.winPoints,
  };
}
function sanitizeDecision(r, cfg, addMeta, cardsById) {
  return {
    kind: 'decision', half: r.half,
    entries: r.entries.map((e) => {
      addMeta(e.cardId);
      const rater = cfg.gmName, card = cardsById[e.cardId];
      return { player: e.player, cardId: e.cardId, cardName: e.cardName, score: e.score, scoreZen: card.scores.zen[rater], scoreKou: card.scores.kou[rater], comment: e.comment };
    }),
    winners: r.winners.slice(), tieBreakUsed: r.tieBreakUsed,
    guessResults: r.guessResults, pointsAwarded: r.pointsAwarded,
  };
}

/* ---------- エクスポート（node / ブラウザ両対応） ---------- */
const EngineAPI = { ENGINE_VERSION, DEFAULT_CONFIG, Actions, createGame, reduce, judgeRound, judgeDecision, parseScoreStr, numeric, scoreLabel, balanceDeal, viewFor };
if (typeof module !== 'undefined' && module.exports) module.exports = EngineAPI;
if (typeof window !== 'undefined') window.Engine = EngineAPI;
