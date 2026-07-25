#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
魔女の調合 R-2：合理NPC冴え%分布シミュレーション（v1.7.1 指示書C）

witch.html の「凍結コアロジック」（POOL/HARD/scoreRecipe/omniMax/rationalReach/
seqChoose/dealRecipeIds/dealAdvanced 等）を、値を一切変えずにPythonへ移植する。
NPC戦略は「合理」のみ（rationalReach が表す「合理的だが全知ではない」チーム）。

4条件（3人/4人 × 通常/上級）それぞれについて、1ゲーム＝n*2儀式を1試行とし、
各儀式の rationalReach（合理チームの実際の到達点）と omniMax（完璧な到達点）を
儀式ごとに積み上げ、ゲーム終了時の「通算の冴え%」= 100*sum(rational)/sum(omni)
を試行ごとに記録して分布を取る。

再現性：MASTER_SEED はリポジトリの sim/*.py と同じ命名日付規則（この指示書の
作業日）を用いる。乱数は numpy.random.default_rng(MASTER_SEED) を1つだけ生成し、
条件の実行順（3人通常→3人上級→4人通常→4人上級）に沿って消費する
（条件間の乱数系列がずれないよう、スクリプトの実行順を変えないこと）。

使い方:
  python rational_keen_sim.py --trials 2000 --out results_v1.json
  python rational_keen_sim.py --trials 200  # スモークテスト（デフォルト出力）

注記（浮動小数点の再現性について）：
bestAssignは全順列を試すが、seenBy(ch,idx[k],...)はch!=idx[k]のとき常にE_sb(pile)
（jに依存しない値）を返すため、値は理論上どの順列でも「残り坩堝のE_sbの総和」に一致する
（best_assign_sumとして直接計算・高速化している。証明はコード内コメント参照）。
ただし浮動小数点の加算は結合則を厳密には満たさないため、総当りで偶然拾う「その順列での
合計」と直接総和の値は、ごく稀に最終桁で1e-15程度ずれることがある。この差はseqChoose内の
「どちらの坩堝を選ぶか」という僅差のタイブレークを数百試行に1回程度揺らす可能性があるが、
1000試行以上を集計した平均・パーセンタイルには実務上影響しない（数値そのものの意味・
ゲーム理論的な値は完全に同一）。再現性は「同一スクリプト・同一seed」でのbit-for-bit一致
として担保する（最適化前の素朴な総当り版とのbit-for-bit一致までは保証しない）。
"""
import argparse
import itertools
import json
import statistics
import sys
from datetime import datetime, timezone

import numpy as np

MASTER_SEED = 20260724  # 本指示書（v2.1）の作業日付。sim/*.py の既存命名規則に合わせた。

# ============ witch.html の凍結コアロジックの移植（値は一切変更していない） ============
# POOL: witch.html 273-279行目と同一。素材indexは 0=満月草 1=火竜の鱗 2=影茸 3=星屑 4=霜結晶 5=蛇毒
POOL = [
    {"id": "pair_満星", "type": "pair", "a": 0, "b": 3, "pt": 4},
    {"id": "pair_火霜", "type": "pair", "a": 1, "b": 4, "pt": 4},
    {"id": "pair_影蛇", "type": "pair", "a": 2, "b": 5, "pt": 4},
    {"id": "pair_満火", "type": "pair", "a": 0, "b": 1, "pt": 4},
    {"id": "pair_星影", "type": "pair", "a": 3, "b": 2, "pt": 4},
    {"id": "pair_霜蛇", "type": "pair", "a": 4, "b": 5, "pt": 4},
    {"id": "cnt2_満火", "type": "cnt2", "a": 0, "b": 1, "pt": 5},
    {"id": "cnt2_影星", "type": "cnt2", "a": 2, "b": 3, "pt": 5},
    {"id": "cnt2_霜蛇", "type": "cnt2", "a": 4, "b": 5, "pt": 5},
    {"id": "cnt2_満霜", "type": "cnt2", "a": 0, "b": 4, "pt": 5},
    {"id": "solo_星", "type": "solo", "a": 3, "pt": 3},
    {"id": "solo_霜", "type": "solo", "a": 4, "pt": 3},
    {"id": "solo_満", "type": "solo", "a": 0, "pt": 3},
    {"id": "solo_火", "type": "solo", "a": 1, "pt": 3},
    {"id": "abs2_影蛇", "type": "abs2", "a": 2, "b": 5, "pt": 3},
    {"id": "abs2_火星", "type": "abs2", "a": 1, "b": 3, "pt": 3},
    {"id": "abs_影", "type": "abs", "a": 2, "pt": 2},
    {"id": "abs_蛇", "type": "abs", "a": 5, "pt": 2},
]
# HARD: witch.html 283-285行目と同一
HARD = [
    {"id": "H_火2蛇無", "type": "hard", "a": 1, "b": 5, "pt": 10},
    {"id": "H_影2満無", "type": "hard", "a": 2, "b": 0, "pt": 10},
    {"id": "H_星2霜無", "type": "hard", "a": 3, "b": 4, "pt": 10},
    {"id": "H_霜2影無", "type": "hard", "a": 4, "b": 2, "pt": 10},
    {"id": "H_満2星無", "type": "hard", "a": 0, "b": 3, "pt": 10},
    {"id": "H_蛇2火無", "type": "hard", "a": 5, "b": 1, "pt": 10},
]


def score_recipe(r, pile):
    def c(x):
        return pile.count(x)

    t = r["type"]
    if t == "pair":
        return r["pt"] if (c(r["a"]) >= 1 and c(r["b"]) >= 1) else 0
    if t == "cnt2":
        return r["pt"] if (c(r["a"]) >= 2 or c(r["b"]) >= 2) else 0
    if t == "solo":
        return r["pt"] if c(r["a"]) == 1 else 0
    if t == "abs2":
        return r["pt"] if (c(r["a"]) == 0 and c(r["b"]) == 0) else 0
    if t == "abs":
        return r["pt"] if c(r["a"]) == 0 else 0
    if t == "hard":
        return r["pt"] if (c(r["a"]) >= 2 and c(r["b"]) == 0) else 0
    return 0


def score_pile(recipes, pile):
    return sum(score_recipe(r, pile) for r in recipes)


def e_sb(pile):
    # witch.html: function E_sb(p){let s=0;for(const r of POOL)s+=scoreRecipe(r,p);return 3*s/POOL.length;}
    s = sum(score_recipe(r, pile) for r in POOL)
    return 3 * s / len(POOL)


# ---- 高速版（素材ヒストグラムを使い回す）。値はscore_recipe/score_pile/e_sbと完全に同一。
# omni_max/seq_choose/rational_reachはこちらを使い、pile.count()の再走査を避ける
# （同じ坩堝の中身に対し、順列を変えて何度も採点するため、事前計算したヒストグラムを
#   使い回すのが正しく・速い。値そのものは一切変更していない）。
def pile_counts(pile):
    counts = [0, 0, 0, 0, 0, 0]
    for m in pile:
        counts[m] += 1
    return counts


def score_recipe_c(r, counts):
    t = r["type"]
    if t == "pair":
        return r["pt"] if (counts[r["a"]] >= 1 and counts[r["b"]] >= 1) else 0
    if t == "cnt2":
        return r["pt"] if (counts[r["a"]] >= 2 or counts[r["b"]] >= 2) else 0
    if t == "solo":
        return r["pt"] if counts[r["a"]] == 1 else 0
    if t == "abs2":
        return r["pt"] if (counts[r["a"]] == 0 and counts[r["b"]] == 0) else 0
    if t == "abs":
        return r["pt"] if counts[r["a"]] == 0 else 0
    if t == "hard":
        return r["pt"] if (counts[r["a"]] >= 2 and counts[r["b"]] == 0) else 0
    return 0


def score_pile_c(recipes, counts):
    return sum(score_recipe_c(r, counts) for r in recipes)


_POOL_SUM_CACHE = {}


def e_sb_c(counts):
    key = tuple(counts)
    cached = _POOL_SUM_CACHE.get(key)
    if cached is not None:
        return cached
    s = sum(score_recipe_c(r, counts) for r in POOL)
    val = 3 * s / len(POOL)
    _POOL_SUM_CACHE[key] = val
    return val


def make_band(n, rng):
    bag = []
    for m in range(6):
        for _ in range(3):
            bag.append(m)
    rng.shuffle(bag)
    return bag[: n * 3]


def cut_sets(length, nc):
    # witch.html: comb([1..length-1], nc) — 帯の中の切れ目位置の組合せ
    positions = list(range(1, length))
    return list(itertools.combinations(positions, nc))


def to_piles(band, cuts):
    s = sorted(cuts)
    pts = [0] + list(s) + [len(band)]
    return [band[pts[i]: pts[i + 1]] for i in range(len(pts) - 1)]


def deal_conflicts(chosen_idx):
    # witch.html: dealConflicts — 「入れたい素材」と「入れたくない素材」の同時配布を禁止
    avoid, want = set(), set()
    for i in chosen_idx:
        r = POOL[i]
        if r["type"] == "abs":
            avoid.add(r["a"])
        if r["type"] == "abs2":
            avoid.add(r["a"])
            avoid.add(r["b"])
        if r["type"] in ("pair", "cnt2"):
            want.add(r["a"])
            want.add(r["b"])
        if r["type"] == "solo":
            want.add(r["a"])
    return any(m in want for m in avoid)


def deal_recipe_ids(n, rng):
    # witch.html: dealRecipeIds — 600回まで再抽選し、矛盾配布を避ける
    idx_all = list(range(len(POOL)))
    for _ in range(600):
        c = idx_all[:]
        rng.shuffle(c)
        c = c[: n * 3]
        if not deal_conflicts(c):
            return [[POOL[c[p * 3 + k]] for k in range(3)] for p in range(n)]
    c = idx_all[:]
    rng.shuffle(c)
    c = c[: n * 3]
    return [[POOL[c[p * 3 + k]] for k in range(3)] for p in range(n)]


def deal_advanced(n, rng):
    # witch.html: dealAdvanced — 通常枠2＋秘伝枠1。600回まで矛盾回避を試み、ダメならフォールバック。
    pool_idx_all = list(range(len(POOL)))
    for _ in range(600):
        idx = pool_idx_all[:]
        rng.shuffle(idx)
        idx = idx[: n * 2]
        norm_per_seat = [[POOL[idx[p * 2 + k]] for k in range(2)] for p in range(n)]
        hards = HARD[:]
        rng.shuffle(hards)
        hards = hards[:n]
        ok = True
        for p in range(n):
            want, avoid = set(), set()
            for r in norm_per_seat[p]:
                if r["type"] == "abs":
                    avoid.add(r["a"])
                if r["type"] == "abs2":
                    avoid.add(r["a"])
                    avoid.add(r["b"])
                if r["type"] in ("pair", "cnt2"):
                    want.add(r["a"])
                    want.add(r["b"])
                if r["type"] == "solo":
                    want.add(r["a"])
            want.add(hards[p]["a"])
            avoid.add(hards[p]["b"])
            if any(m in want for m in avoid):
                ok = False
                break
        if ok:
            return [norm_per_seat[p] + [hards[p]] for p in range(n)]
    idx = pool_idx_all[:]
    rng.shuffle(idx)
    idx = idx[: n * 2]
    hards = HARD[:]
    rng.shuffle(hards)
    hards = hards[:n]
    return [[POOL[idx[p * 2]], POOL[idx[p * 2 + 1]], hards[p]] for p in range(n)]


def omni_max(band, players):
    # witch.html: omniMax — 全知全能の最適解（総当り）。ヒストグラムは坩堝ごとに1回だけ計算し、
    # 全順列で使い回す（scorePileの値そのものは変えていない。再走査を避けるだけ）。
    n = len(players)
    best = -1e9
    for cs in cut_sets(len(band), n - 1):
        piles = to_piles(band, cs)
        counts_list = [pile_counts(p) for p in piles]
        for pm in itertools.permutations(range(n)):
            t = sum(score_pile_c(players[pm[i]], counts_list[i]) for i in range(n))
            if t > best:
                best = t
    return best


def seen_by_c(i, j, players, counts):
    # witch.html: seenBy — 自分の坩堝は真の点、他人の坩堝はプール平均の期待値（他人の手は見えない）
    return score_pile_c(players[j], counts) if i == j else e_sb_c(counts)


def best_assign_sum(piles_remaining_counts):
    # witch.html: bestAssign は全順列を試すが、seenBy(ch,idx[k],...) は ch!=idx[k] のとき
    # 常にE_sb(pile)（jに依存しない）を返すため、値は順列によらず「残り坩堝のE_sbの総和」に一致する
    # （bestAssignの呼び出し元では常にch not in idx_remainingという条件が成り立つため恒等的に成立）。
    # 総当りの代わりにこの等価な総和を直接返す＝値は完全に同一、計算量だけをO(n!)からO(n)へ落とす。
    return sum(e_sb_c(c) for c in piles_remaining_counts)


def seq_choose(piles, players, cutter):
    # witch.html: seqChoose — 手番順に、各自が「自分の真の値＋残り割当の見込み」で逐次選ぶ
    n = len(players)
    order = [(cutter + 1 + i) % n for i in range(n) if (cutter + 1 + i) % n != cutter]
    counts_by_i = {i: pile_counts(piles[i]) for i in range(len(piles))}
    available = [{"p": piles[i], "i": i, "c": counts_by_i[i]} for i in range(len(piles))]
    assign = {}
    for oi in range(len(order)):
        me = order[oi]
        others_after = order[oi + 1:] + [cutter]
        best_v, best_pick = -1e9, None
        for cand in available:
            remaining_counts = [x["c"] for x in available if x["i"] != cand["i"]]
            lookahead = (
                best_assign_sum(remaining_counts)
                if len(remaining_counts) == len(others_after)
                else 0
            )
            v = seen_by_c(me, me, players, cand["c"]) + lookahead
            if v > best_v:
                best_v, best_pick = v, cand
        assign[me] = best_pick
        available = [x for x in available if x["i"] != best_pick["i"]]
    assign[cutter] = available[0]
    return assign


def rational_reach(band, players, cutter):
    # witch.html: rationalReach — 「合理的だが全知ではない」チームが1儀式で実際に到達する点
    n = len(players)
    best_assign_result, best_e = None, -1e9
    for cs in cut_sets(len(band), n - 1):
        piles = to_piles(band, cs)
        asg = seq_choose(piles, players, cutter)
        e = sum(seen_by_c(cutter, i, players, asg[i]["c"]) for i in range(n))
        if e > best_e:
            best_e, best_assign_result = e, asg
    a = sum(score_pile_c(players[i], best_assign_result[i]["c"]) for i in range(n))
    return a


# ============ シミュレーション本体（R-2固有・witch.htmlの一部ではない） ============
def run_condition(n, advanced, n_trials, rng):
    rounds = n * 2  # witch.html既定（標準・人数×2）
    keen_pcts = []
    for _ in range(n_trials):
        cutter = 0
        sum_rational, sum_omni = 0.0, 0.0
        for _r in range(rounds):
            band = make_band(n, rng)
            players = deal_advanced(n, rng) if advanced else deal_recipe_ids(n, rng)
            omni = omni_max(band, players)
            rat = rational_reach(band, players, cutter)
            sum_rational += rat
            sum_omni += omni
            cutter = (cutter + 1) % n
        pct = 100.0 * sum_rational / sum_omni if sum_omni > 0 else (100.0 if sum_rational >= 0 else 0.0)
        keen_pcts.append(min(100.0, pct))
    return keen_pcts


def percentile(data, p):
    return float(np.percentile(data, p))


def band_shares(data):
    thresholds = [58, 70, 82, 92]
    n = len(data)
    return {f">={t}": sum(1 for x in data if x >= t) / n for t in thresholds}


def shift_to_match(source, target):
    """targetの分布にsourceの各称号境界パーセンタイル位置を合わせるために必要な閾値シフト量。
    「4人の分布を3人の分布に揃えるには閾値を何点シフトすればよいか」の機械的算出：
    各閾値tについて、target分布でtが位置する分位点pを求め、source分布のp分位点をtの新しい閾値候補とする。
    シフト量 = 新閾値 - 元の閾値t。
    """
    thresholds = [58, 70, 82, 92]
    out = {}
    target_sorted = sorted(target)
    source_sorted = sorted(source)
    n_t = len(target_sorted)
    for t in thresholds:
        # targetにおけるtの分位点（何%がt未満か）
        below = sum(1 for x in target_sorted if x < t)
        q = 100.0 * below / n_t
        new_threshold = percentile(source_sorted, q)
        out[t] = {"quantile_in_target_pct": round(q, 2), "equivalent_threshold_in_source": round(new_threshold, 2),
                  "shift": round(new_threshold - t, 2)}
    return out


def summarize(label, data):
    return {
        "label": label,
        "n_trials": len(data),
        "mean": round(statistics.mean(data), 3),
        "median": round(statistics.median(data), 3),
        "stdev": round(statistics.stdev(data), 3) if len(data) > 1 else 0.0,
        "p10": round(percentile(data, 10), 3),
        "p25": round(percentile(data, 25), 3),
        "p50": round(percentile(data, 50), 3),
        "p75": round(percentile(data, 75), 3),
        "p90": round(percentile(data, 90), 3),
        "min": round(min(data), 3),
        "max": round(max(data), 3),
        "threshold_exceed_rate": {k: round(v, 4) for k, v in band_shares(data).items()},
    }


def main():
    ap = argparse.ArgumentParser(description="witch R-2: 合理NPC冴え%分布シミュレーション")
    ap.add_argument("--trials", type=int, default=1000, help="条件あたりの試行数（既定1000）")
    ap.add_argument("--out", type=str, default=None, help="結果JSONの出力先（省略時は標準出力のみ）")
    args = ap.parse_args()

    rng = np.random.default_rng(MASTER_SEED)

    conditions = [
        ("3人・通常", 3, False),
        ("3人・上級", 3, True),
        ("4人・通常", 4, False),
        ("4人・上級", 4, True),
    ]

    results = {}
    raw = {}
    for label, n, advanced in conditions:
        data = run_condition(n, advanced, args.trials, rng)
        raw[label] = data
        results[label] = summarize(label, data)
        print(f"[{label}] n_trials={len(data)} mean={results[label]['mean']} "
              f"median={results[label]['median']} p10={results[label]['p10']} p90={results[label]['p90']}",
              file=sys.stderr)

    shift_normal = shift_to_match(raw["4人・通常"], raw["3人・通常"])
    shift_advanced = shift_to_match(raw["4人・上級"], raw["3人・上級"])

    output = {
        "meta": {
            "script": "sim/witch_rank_distribution/rational_keen_sim.py",
            "master_seed": MASTER_SEED,
            "trials_per_condition": args.trials,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "note": "NPC戦略は「合理」のみ（rationalReach＝合理的だが全知ではないチームの実到達点）。"
                    "witch.htmlの凍結コアロジックをPythonへ値を変えずに移植。",
        },
        "conditions": results,
        "shift_4to3_normal": shift_normal,
        "shift_4to3_advanced": shift_advanced,
    }

    text = json.dumps(output, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()
