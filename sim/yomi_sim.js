"use strict";
/* ============================================================
   影の競り 検証シミュ:「読み」は均衡上報われるのか(2026-07 指示書)
   - 独立スクリプト。seri.html には触らない。
   - ルール数値は seri.html v1.4 の4人卓固定:
     3面 / 原石8 / 資金34 / TASTE=2 / 面の値域 -2..+6 一様 / 競り上げ式・降り不可逆
   条件:
     A  盲目閾値(自面+残り面の期待値のみ。現行NPCの基礎) vs 現行標準NPC×3
     B  素朴追随(再現確認用)                              vs 現行標準NPC×3
     C  降り値推論(履歴レンズ推定+競り中ベイズ更新)        vs 現行標準NPC×3
     C' Cの呪い控除を事後分散でスケール(補助)              vs 現行標準NPC×3
     D  目公開世界(レンズ全公開で C を実行)                vs 現行標準NPC×3
     E  目公開世界のブラフ(深さ2/4/6)                      vs D型NPC×3
     allD  全員D(対称)                                     … Eの対照 + 呪い率予備確認
     allNPC 全員現行NPC(対称)                              … 競りの過熱の基準線
   ============================================================ */

const P = 4, NC = 3, ROUNDS = 8, MONEY = 34, CMIN = -2, CMAX = 6, TASTE = 2;
const E0 = (CMIN + CMAX) / 2;      // 2
const NV = CMAX - CMIN + 1;        // 9
// seri.html の PROF。ソロ卓の NPC は席1..3 = PROF[1..3]
const PROF = [
  { opt: 1.0,  read: 1,   curse: 1   },
  { opt: 1.0,  read: 1,   curse: 1   },
  { opt: 0.85, read: 1,   curse: 1.4 },
  { opt: 1.1,  read: 0.3, curse: 0.3 },
];
const N_GAMES = 20000;

/* ---------- RNG(再現可能) ---------- */
function mulberry32(a) {
  return function () {
    a |= 0; a = a + 0x6D2B79F5 | 0;
    let t = Math.imul(a ^ a >>> 15, 1 | a);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  };
}

/* ---------- 数学 ---------- */
const sig = x => 1 / (1 + Math.exp(-x));
function erf(x) {
  const s = x < 0 ? -1 : 1; x = Math.abs(x);
  const t = 1 / (1 + 0.3275911 * x);
  const y = 1 - ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x);
  return s * y;
}
const normCdf = z => 0.5 * (1 + erf(z / Math.SQRT2));

/* ============================================================
   現行標準NPC(seri.html npcTurn の忠実移植)
   ============================================================ */
function inferLensNpc(game, q) {
  const sc = new Array(NC).fill(0);
  for (const h of game.hist) {
    const bids = h.bidLog.filter(b => b.p === q);
    const top = bids.length ? bids[bids.length - 1].price : 0;
    if (top === 0 && !h.bidLog.some(b => b.p === q)) continue;
    const zeal = top - (h.total / 2);
    for (let i = 0; i < NC; i++) sc[i] += zeal * (h.v[i] - 2);
  }
  const mx = Math.max(...sc), ex = sc.map(x => Math.exp((x - mx) / 12));
  const sum = ex.reduce((a, b) => a + b, 0);
  return ex.map(x => x / sum);
}
function npcPolicy(cfg) {
  return {
    limit(game, auc, p) {
      const myLens = game.lens[p], mySeen = auc.v[myLens];
      let totalEst = mySeen + E0 * (NC - 1);
      // ①「降りた」情報を読む
      const dropped = [];
      for (let q = 0; q < P; q++) if (q !== p && !auc.live.includes(q)) dropped.push(q);
      if (cfg.read > 0 && dropped.length > 0) {
        let implied = Infinity;
        for (const q of dropped) {
          const top = auc.per[q].top;
          const est = top - TASTE * E0;
          if (est < implied) implied = est;
        }
        const naive = mySeen + E0 * (NC - 1);
        if (implied < naive) totalEst = naive - (naive - implied) * 0.5 * cfg.read * (dropped.length / (P - 1));
      }
      // ② レンズ推定 × 入札解釈
      const rivals = auc.live.filter(q => q !== p);
      if (cfg.read > 0 && game.hist.length >= 2 && rivals.length > 0) {
        let info = 0, wsum = 0;
        for (const q of rivals) {
          const top = auc.per[q].top;
          if (!top) continue;
          const pr = inferLensNpc(game, q);
          const same = pr[myLens];
          const newInfo = 1 - same;
          info += (top - TASTE * E0) * newInfo;
          wsum += newInfo;
        }
        if (wsum > 0.2) {
          const implied = info / wsum;
          const k = implied < totalEst ? 0.45 : 0.15;
          totalEst = totalEst + (implied - totalEst) * k * cfg.read;
        }
      }
      // ③ 呪いの警戒
      const nRiv = auc.live.length - 1;
      const curse = cfg.curse * (nRiv > 0 ? nRiv / (P - 1) : 1) * E0 * (NC - 1) * 0.42;
      const est = totalEst + TASTE * mySeen - curse;
      return Math.round(est * cfg.opt);
    },
  };
}

/* ============================================================
   B: 素朴追随(旧・追随型NPCの再構成。元スクリプトはリポジトリに無いため
      「相手の入札が示唆する総額へ、上方向は k=1.0 で寄せる」で再現)
   ============================================================ */
function naiveFollowPolicy() {
  return {
    limit(game, auc, p) {
      const myLens = game.lens[p], mySeen = auc.v[myLens];
      let totalEst = mySeen + E0 * (NC - 1);
      let info = 0, w = 0;
      for (const q of auc.live) {
        if (q === p) continue;
        const top = auc.per[q].top;
        if (!top) continue;
        info += top - TASTE * E0; w++;
      }
      if (w > 0) {
        const implied = info / w;
        const k = implied > totalEst ? 1.0 : 0.45;   // 上方追随が「素朴」
        totalEst += (implied - totalEst) * k;
      }
      const nRiv = auc.live.length - 1;
      const curse = (nRiv > 0 ? nRiv / (P - 1) : 1) * E0 * (NC - 1) * 0.42;
      return Math.round(totalEst + TASTE * mySeen - curse);
    },
  };
}

/* ============================================================
   C / D / E: ベイズ推論エージェント
   - レンズ事後: 履歴の(全面公開値 × 降り値/落札値)の打ち切り尤度から(C)。
     D/E ではレンズは真値(目公開世界)。
   - 競り中: 各他者の「降りた価格 → 見ている面の上限」「生きて入札 → 下限」を
     ロジスティック軟化した境界として、面ごとの事後分布(9値)を更新。
   - 同レンズの相手の情報は、自面の値が既知(デルタ)なので自動的に価値ゼロ。
   近似(報告事項):
   - 相手の上限式を「盲目閾値 + 固定呪い控除 CURSE_FIX + ノイズ」でモデル化
     (実際の相手は読み①②で上限を動かすため、その分はノイズ σ に吸収)
   - 面ごとの事後は相手ごとの独立因子分解(レンズ混合の平均場近似)
   - 競り中のレンズ事後は更新しない(履歴のみ)
   ============================================================ */
/* 診断: 被験(席0)の総額推定の精度と、レンズ推定の的中率 */
const DIAG = { naiveAbs: 0, postAbs: 0, n: 0, lensHit: 0, lensN: 0 };

function bayesPolicy(opts) {
  // opts: {knownLens, curseMode:'fixed'|'scaled', bluffDepth:0, bluffProb:0, negOnly:false}
  const SIGL = 2.5;       // 履歴レンズ推定: 上限値ノイズ(ロジスティック尺度)
  const SIGV = 0.9;       // 面値へ逆算したときのノイズ
  const CURSE_FIX = 1.1;  // 相手の呪い控除の代表値(逆算用)
  let llk = null, bluffing = false, lensP = null;
  return {
    init(game, seat) {
      llk = [...Array(P)].map(() => new Float64Array(NC));
    },
    lotStart(game, auc, seat, rng) {
      lensP = [];
      for (let q = 0; q < P; q++) {
        if (q === seat) { lensP.push(null); continue; }
        const a = new Float64Array(NC);
        if (opts.knownLens) { a[game.lens[q]] = 1; }
        else {
          const mx = Math.max(...llk[q]); let s = 0;
          for (let j = 0; j < NC; j++) { a[j] = Math.exp(llk[q][j] - mx); s += a[j]; }
          for (let j = 0; j < NC; j++) a[j] /= s;
        }
        lensP.push(a);
      }
      bluffing = (opts.bluffDepth || 0) > 0 && auc.v[game.lens[seat]] <= 0 && rng() < opts.bluffProb;
      if (seat === 0) auc._bluff0 = bluffing;
    },
    limit(game, auc, p) {
      const myLens = game.lens[p], myV = auc.v[myLens];
      let totalEst = myV, varSum = 0;
      for (let j = 0; j < NC; j++) {
        if (j === myLens) continue;
        const w = new Float64Array(NV).fill(1);
        for (let q = 0; q < P; q++) {
          if (q === p) continue;
          const per = auc.per[q];
          if (per.forced) continue;
          const pq = lensP[q][j];
          if (pq < 1e-9) continue;
          const alive = auc.live.includes(q);
          if (!alive && per.refused != null) {
            // 価格 r を拒んで降りた → 上限 <= r-1 → 面値の上界
            const cUp = (per.refused - 1 + CURSE_FIX - E0 * (NC - 1)) / (1 + TASTE);
            const cLo = per.top > 0 ? (per.top + CURSE_FIX - E0 * (NC - 1)) / (1 + TASTE) : null;
            for (let k = 0; k < NV; k++) {
              const v = CMIN + k;
              let f = sig((cUp - v) / SIGV);
              if (cLo != null) f *= sig((v - cLo) / SIGV);
              w[k] *= pq * f + (1 - pq);
            }
          } else if (alive && per.top > 0 && !opts.negOnly) {
            // まだ生きていて top まで入札済み → 下限
            const cLo = (per.top + CURSE_FIX - E0 * (NC - 1)) / (1 + TASTE);
            for (let k = 0; k < NV; k++) {
              const v = CMIN + k;
              const f = sig((v - cLo) / SIGV);
              w[k] *= pq * f + (1 - pq);
            }
          }
        }
        let s = 0, m = 0, m2 = 0;
        for (let k = 0; k < NV; k++) {
          const v = CMIN + k;
          s += w[k]; m += w[k] * v; m2 += w[k] * v * v;
        }
        m /= s; m2 /= s;
        totalEst += m; varSum += m2 - m * m;
      }
      if (p === 0) {
        const trueTotal = auc.v.reduce((a, b) => a + b, 0);
        DIAG.naiveAbs += Math.abs(myV + E0 * (NC - 1) - trueTotal);
        DIAG.postAbs += Math.abs(totalEst - trueTotal);
        DIAG.n++;
      }
      const nRiv = auc.live.length - 1;
      let curse = (nRiv > 0 ? nRiv / (P - 1) : 1) * E0 * (NC - 1) * 0.42;
      if (opts.curseMode === 'scaled') {
        const priorVar = (NC - 1) * (NV * NV - 1) / 12;   // 未知2面ぶんの事前分散
        curse *= Math.sqrt(varSum / priorVar);
      }
      let lim = Math.round(totalEst + TASTE * myV - curse);
      if (bluffing) lim = Math.max(lim, 0) + opts.bluffDepth;
      return lim;
    },
    afterLot(game, rec, seat) {
      if (opts.knownLens) return;
      for (let q = 0; q < P; q++) {
        if (q === seat) continue;
        const per = rec.per[q];
        if (per.forced) continue;
        for (let j = 0; j < NC; j++) {
          const Lhat = (1 + TASTE) * rec.v[j] + E0 * (NC - 1) - CURSE_FIX;
          let ll;
          if (rec.w === q) {
            ll = Math.log(sig((Lhat - rec.price + 0.5) / SIGL) + 1e-12);        // 上限 >= 落札価格
          } else if (per.refused != null) {
            const Fhi = sig((per.refused - 0.5 - Lhat) / SIGL);                 // 上限 <= 拒否価格-1
            const Flo = per.top > 0 ? sig((per.top - 0.5 - Lhat) / SIGL) : 0;   // 上限 >= 自分の最終入札
            ll = Math.log(Math.max(Fhi - Flo, 1e-12));
          } else if (per.top > 0) {
            ll = Math.log(sig((Lhat - per.top + 0.5) / SIGL) + 1e-12);          // 生存: 上限 >= top
          } else continue;
          llk[q][j] += ll;
        }
      }
      if (seat === 0 && game.hist.length === ROUNDS) {
        for (let q = 1; q < P; q++) {
          let best = 0;
          for (let j = 1; j < NC; j++) if (llk[q][j] > llk[q][best]) best = j;
          if (best === game.lens[q]) DIAG.lensHit++;
          DIAG.lensN++;
        }
      }
    },
  };
}

/* ============================================================
   エンジン(seri.html の競り進行を移植)
   ============================================================ */
function playGame(policies, rng, stats) {
  const game = {
    lens: [...Array(P)].map(() => Math.floor(rng() * NC)),
    money: new Array(P).fill(MONEY),
    won: [...Array(P)].map(() => []),
    hist: [],
  };
  for (let i = 0; i < P; i++) policies[i].init && policies[i].init(game, i);
  for (let round = 0; round < ROUNDS; round++) {
    if (game.money.every(m => m < 1)) break;
    const v = [...Array(NC)].map(() => CMIN + Math.floor(rng() * NV));
    const auc = {
      v, price: 0, lastBidder: -1,
      live: [...Array(P).keys()].filter(p => game.money[p] >= 1),
      per: [...Array(P)].map(() => ({ top: 0, refused: null, forced: false })),
      bidLog: [],
    };
    for (let i = 0; i < P; i++) policies[i].lotStart && policies[i].lotStart(game, auc, i, rng);
    if (auc.live.length > 0) {
      let cur = auc.live[Math.floor(rng() * auc.live.length)];
      while (true) {
        const p = cur;
        const lim = policies[p].limit(game, auc, p);
        if (auc.price + 1 <= lim && auc.price + 1 <= game.money[p]) {
          auc.price++; auc.lastBidder = p;
          auc.per[p].top = auc.price;
          auc.bidLog.push({ p, price: auc.price });
        } else {
          if (auc.price + 1 > game.money[p]) auc.per[p].forced = true;
          auc.per[p].refused = auc.price + 1;
          auc.live = auc.live.filter(x => x !== p);
        }
        if (auc.live.length <= 1) break;
        do { cur = (cur + 1) % P; } while (!auc.live.includes(cur));
        if (cur === auc.lastBidder) break;
      }
    }
    let w = auc.live.length === 1 ? auc.live[0] : auc.lastBidder;
    if (w === undefined || w < 0 || auc.price === 0) w = -1;
    const rec = {
      v, total: v.reduce((a, b) => a + b, 0), w,
      price: w >= 0 ? auc.price : 0, bidLog: auc.bidLog, per: auc.per,
    };
    if (w >= 0) {
      game.money[w] -= auc.price;
      game.won[w].push({ v, total: rec.total, price: auc.price });
      const net = rec.total + TASTE * v[game.lens[w]] - auc.price;
      stats.hammered++; if (net < 0) stats.cursed++;
      stats.priceSum += auc.price;
      if (w === 0) {
        stats.subjWonLots++; if (net < 0) stats.subjCursedLots++;
        stats.subjPay += auc.price; stats.subjNet += net;
      }
      if (auc._bluff0) {
        stats.bluffLots++;
        if (w === 0) { stats.bluffStuck++; stats.bluffStuckNet += net; }
      }
    } else {
      stats.passed++;
      if (auc._bluff0) stats.bluffLots++;
    }
    stats.lots++;
    game.hist.push(rec);
    for (let i = 0; i < P; i++) policies[i].afterLot && policies[i].afterLot(game, rec, i);
  }
  // 決算
  const sc = game.money.map((m, p) =>
    m + game.won[p].reduce((a, c) => a + c.total + TASTE * c.v[game.lens[p]], 0));
  const mx = Math.max(...sc);
  const winners = sc.reduce((a, s) => a + (s === mx ? 1 : 0), 0);
  for (let p = 0; p < P; p++) {
    if (sc[p] === mx) stats.wins[p] += 1 / winners;
    stats.scoreSumSeat[p] += sc[p];
  }
  stats.scoreSum += sc[0];
  stats.scoreSum2 += sc[0] * sc[0];
  stats.games++;
}

function newStats() {
  return {
    games: 0, wins: new Array(P).fill(0), scoreSum: 0, scoreSum2: 0,
    scoreSumSeat: new Array(P).fill(0),
    lots: 0, hammered: 0, cursed: 0, passed: 0, priceSum: 0,
    subjWonLots: 0, subjCursedLots: 0, subjPay: 0, subjNet: 0,
    bluffLots: 0, bluffStuck: 0, bluffStuckNet: 0,
  };
}

/* ---------- 条件定義 ---------- */
const npcRivals = () => [npcPolicy(PROF[1]), npcPolicy(PROF[2]), npcPolicy(PROF[3])];
const dNpc = () => bayesPolicy({ knownLens: true, curseMode: 'fixed' });
const CONDS = [
  { key: 'A',      label: 'A 盲目閾値 vs 現行NPC',        seed: 101, mk: () => [npcPolicy({ opt: 1, read: 0, curse: 1 }), ...npcRivals()] },
  { key: 'B',      label: 'B 素朴追随 vs 現行NPC',        seed: 102, mk: () => [naiveFollowPolicy(), ...npcRivals()] },
  { key: 'C',      label: 'C 降り値推論 vs 現行NPC',      seed: 103, mk: () => [bayesPolicy({ knownLens: false, curseMode: 'fixed' }), ...npcRivals()] },
  { key: 'Cs',     label: "C' 同(呪い控除を分散スケール)", seed: 104, mk: () => [bayesPolicy({ knownLens: false, curseMode: 'scaled' }), ...npcRivals()] },
  { key: 'Cn',     label: 'C- 負のシグナルのみ(降り値だけ)', seed: 112, mk: () => [bayesPolicy({ knownLens: false, curseMode: 'fixed', negOnly: true }), ...npcRivals()] },
  { key: 'Dn',     label: 'D- 目公開+負のシグナルのみ',     seed: 113, mk: () => [bayesPolicy({ knownLens: true, curseMode: 'fixed', negOnly: true }), ...npcRivals()] },
  { key: 'D',      label: 'D 目公開ベイズ vs 現行NPC',    seed: 105, mk: () => [dNpc(), ...npcRivals()] },
  { key: 'Ds',     label: "D' 同(呪い控除スケール)",       seed: 106, mk: () => [bayesPolicy({ knownLens: true, curseMode: 'scaled' }), ...npcRivals()] },
  { key: 'E2',     label: 'E ブラフ深さ2 vs D型×3',       seed: 107, mk: () => [bayesPolicy({ knownLens: true, curseMode: 'fixed', bluffDepth: 2, bluffProb: 1 }), dNpc(), dNpc(), dNpc()] },
  { key: 'E4',     label: 'E ブラフ深さ4 vs D型×3',       seed: 108, mk: () => [bayesPolicy({ knownLens: true, curseMode: 'fixed', bluffDepth: 4, bluffProb: 1 }), dNpc(), dNpc(), dNpc()] },
  { key: 'E6',     label: 'E ブラフ深さ6 vs D型×3',       seed: 109, mk: () => [bayesPolicy({ knownLens: true, curseMode: 'fixed', bluffDepth: 6, bluffProb: 1 }), dNpc(), dNpc(), dNpc()] },
  { key: 'allD',   label: '全員D(対称・Eの対照)',          seed: 110, mk: () => [dNpc(), dNpc(), dNpc(), dNpc()] },
  { key: 'allNPC', label: '全員現行NPC(対称・基準線)',     seed: 111, mk: () => [npcPolicy(PROF[0]), ...npcRivals()] },
];

/* ---------- 実行 ---------- */
const results = {};
for (const c of CONDS) {
  const t0 = Date.now();
  const rng = mulberry32(c.seed);
  const policies = c.mk();
  const st = newStats();
  const d0 = { ...DIAG };
  for (let g = 0; g < N_GAMES; g++) playGame(policies, rng, st);
  const dd = { naiveAbs: DIAG.naiveAbs - d0.naiveAbs, postAbs: DIAG.postAbs - d0.postAbs, n: DIAG.n - d0.n, lensHit: DIAG.lensHit - d0.lensHit, lensN: DIAG.lensN - d0.lensN };
  const n = st.games;
  const p0 = st.wins[0] / n;
  const se = Math.sqrt(p0 * (1 - p0) / n);
  const mean = st.scoreSum / n;
  const sd = Math.sqrt(st.scoreSum2 / n - mean * mean);
  results[c.key] = {
    label: c.label, n,
    winRate: p0, winCI: 1.96 * se,
    meanScore: mean, scoreSD: sd, scoreCI: 1.96 * sd / Math.sqrt(n),
    npcWins: st.wins.slice(1).map(w => w / n),
    curseAll: st.cursed / Math.max(st.hammered, 1),
    curseSubj: st.subjCursedLots / Math.max(st.subjWonLots, 1),
    subjLotsPerGame: st.subjWonLots / n,
    subjAvgPay: st.subjPay / Math.max(st.subjWonLots, 1),
    subjAvgNet: st.subjNet / Math.max(st.subjWonLots, 1),
    passRate: st.passed / st.lots,
    avgPrice: st.priceSum / Math.max(st.hammered, 1),
    bluffLotsPerGame: st.bluffLots / n,
    bluffStuckRate: st.bluffStuck / Math.max(st.bluffLots, 1),
    bluffStuckNet: st.bluffStuckNet / Math.max(st.bluffStuck, 1),
    seatScores: st.scoreSumSeat.map(s => s / n),
    estErrNaive: dd.n ? dd.naiveAbs / dd.n : null,
    estErrPost: dd.n ? dd.postAbs / dd.n : null,
    lensAcc: dd.lensN ? dd.lensHit / dd.lensN : null,
    ms: Date.now() - t0,
  };
  console.error(`done ${c.key} (${results[c.key].ms}ms)`);
}

/* ---------- 出力 ---------- */
const pct = (x, d = 1) => (100 * x).toFixed(d);
console.log('\n=== 勝率表(被験=席0、95%CI) ===');
console.log('条件 | 勝率 | 95%CI | 平均得点±CI | 呪い率(被験) | 呪い率(卓全体) | 落札数/戦 | 平均支払 | 流札率');
for (const c of CONDS) {
  const r = results[c.key];
  console.log(
    `${r.label} | ${pct(r.winRate)}% | ±${pct(r.winCI)}pt | ${r.meanScore.toFixed(2)}±${r.scoreCI.toFixed(2)} | ` +
    `${pct(r.curseSubj)}% | ${pct(r.curseAll)}% | ${r.subjLotsPerGame.toFixed(2)} | ${r.subjAvgPay.toFixed(2)} | ${pct(r.passRate)}%`
  );
}

function zTestWin(a, b) {
  const ra = results[a], rb = results[b];
  const se = Math.sqrt(ra.winRate * (1 - ra.winRate) / ra.n + rb.winRate * (1 - rb.winRate) / rb.n);
  const z = (ra.winRate - rb.winRate) / se;
  return { diff: ra.winRate - rb.winRate, z, p: 2 * (1 - normCdf(Math.abs(z))) };
}
function tTestScore(a, b) {
  const ra = results[a], rb = results[b];
  const se = Math.sqrt(ra.scoreSD ** 2 / ra.n + rb.scoreSD ** 2 / rb.n);
  const t = (ra.meanScore - rb.meanScore) / se;
  return { diff: ra.meanScore - rb.meanScore, t, p: 2 * (1 - normCdf(Math.abs(t))) };
}
console.log('\n=== 有意判定 ===');
for (const [a, b, note] of [
  ['C', 'A', '判定基準: C<=A なら「この構造で読みは報われない」'],
  ['Cs', 'A', '補助: 呪い控除スケール版'],
  ['Cn', 'A', '補助: 負のシグナル(降り値)のみ'],
  ['Dn', 'A', '補助: 目公開+負のシグナルのみ'],
  ['D', 'A', '目公開の理論上界プロキシ'],
  ['Ds', 'A', '補助'],
  ['B', 'A', '再現確認: 追随は負けるはず'],
  ['E2', 'allD', 'E vs 対称D(=素のD同士の被験)。E>D ならブラフに報酬'],
  ['E4', 'allD', ''],
  ['E6', 'allD', ''],
]) {
  const w = zTestWin(a, b), s = tTestScore(a, b);
  console.log(
    `${a} vs ${b}: Δ勝率 ${(100 * w.diff).toFixed(2)}pt (z=${w.z.toFixed(2)}, p=${w.p.toExponential(2)}) / ` +
    `Δ得点 ${s.diff.toFixed(2)} (t=${s.t.toFixed(2)}, p=${s.p.toExponential(2)})  ${note}`
  );
}

console.log('\n=== ブラフ診断(E条件) ===');
for (const k of ['E2', 'E4', 'E6']) {
  const r = results[k];
  console.log(`${r.label}: ブラフ機会 ${r.bluffLotsPerGame.toFixed(2)}回/戦, 掴まされ率 ${pct(r.bluffStuckRate)}%, 掴んだ時の平均損益 ${r.bluffStuckNet.toFixed(2)}`);
}

console.log('\n=== 推論の実装検証(被験の総額推定 平均絶対誤差 / レンズ的中率) ===');
for (const c of CONDS) {
  const r = results[c.key];
  if (r.estErrPost == null) continue;
  console.log(`${r.label}: 素朴推定誤差 ${r.estErrNaive.toFixed(3)} → 事後推定誤差 ${r.estErrPost.toFixed(3)}${r.lensAcc != null ? ` / 8戦後レンズ的中 ${pct(r.lensAcc)}%(当てずっぽう33%)` : ''}`);
}

console.log('\n=== 席別平均得点(ブラフの外部効果の確認) ===');
for (const k of ['E2', 'E4', 'E6', 'allD']) {
  const r = results[k];
  console.log(`${r.label}: [${r.seatScores.map(s => s.toFixed(1)).join(', ')}]`);
}

console.log('\n=== 対称構成の定性(競りの過熱/沈黙) ===');
for (const k of ['allD', 'allNPC']) {
  const r = results[k];
  console.log(`${r.label}: 平均落札価格 ${r.avgPrice.toFixed(2)}, 流札率 ${pct(r.passRate)}%, 呪い率 ${pct(r.curseAll)}%, 席別勝率 [${[r.winRate, ...r.npcWins].map(x => pct(x)).join(', ')}]%, 平均得点(席0) ${r.meanScore.toFixed(2)}`);
}

require('fs').writeFileSync(__dirname + '/yomi_sim_results.json', JSON.stringify(results, null, 2));
console.log('\nresults -> sim/yomi_sim_results.json');
