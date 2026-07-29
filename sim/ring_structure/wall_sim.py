#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
強化壁・傾斜の数値設計シミュレーション（指示書02）

sim/ring_structure/ring_sim.py の simulate_trial()（既存の環構造モデル）を
そのまま使い、強化壁・傾斜（指示書02で simulate_trial に追加した slope_kind /
checkpoints / wall_targets / paddle_catch_rate 引数）だけを新規に組み合わせる。
新しい物理モデルはここでは作らない（既存モデルの拡張のみ）。

このファイルの役割は「指示書02専用のCLI・集計」であり、指示書01の
ring_sim.py のCLI・出力（--trials/--ticks/--outのデフォルト挙動）には
一切手を入れていない。

使い方:
  python wall_sim.py --natural-trials 3000 --relay-trials 300 --out results
  python wall_sim.py --natural-trials 50 --relay-trials 20 --out /tmp/smoke  # スモーク
"""
import argparse
import csv
import itertools
import json
import os
import random
import statistics
from datetime import datetime, timezone

from ring_sim import simulate_trial, pct, MASTER_SEED, SEED_PRIME

WALL_MASTER_SEED = MASTER_SEED + 1  # 指示書01のchild_seed系列と衝突しないよう+1しておく
WALL_SEED_PRIME = 1_000_033

# 「1セッション相当」の候補tick数（当初はQ4較正が未実施だったため複数候補を
# 並行報告する設計にしていたが、指示書03でQ4実測（ユーザーが100tick×3回計測）が
# 完了した。900tick(約21分)が「1セッション相当」の最有力候補（詳細は報告書参照）。
WALL_CHECKPOINTS = [300, 900, 1800]

# 指示書03 Q4：tick↔実時間較正の実測値（ユーザー実測、2026-07-29）
# run1=139.15s, run2=147.69s, run3=131.64s（いずれも100tickあたりの経過秒数）
Q4_CALIBRATION_RUNS_SEC_PER_100TICK = [139.15, 147.69, 131.64]
Q4_MS_PER_TICK = (sum(Q4_CALIBRATION_RUNS_SEC_PER_100TICK)
                  / len(Q4_CALIBRATION_RUNS_SEC_PER_100TICK) * 1000 / 100)
PERCENTILES = [0.5, 0.9, 0.99]
SAFETY_FACTORS = [2, 4, 8]
SLOPE_KINDS = ["linear", "staged"]

# 自然プレイ条件（このNとdecay_on/weakest_vanishは指示書02の操作変数ではないため、
# 指示書01のsensitivity基準と同じ値に固定する。serve_modeだけ両方見る）
NATURAL_N = 4
NATURAL_DECAY_ON = True
NATURAL_WEAKEST_VANISH = False
REFERENCE_SERVE_MODE = "inf"  # Q1/Q3の要求量導出に使う基準条件（後述：理由は報告書参照）


def wall_seed(index):
    return WALL_MASTER_SEED + index * WALL_SEED_PRIME


def run_natural(seed, slope_kind, serve_mode, trials, ticks, checkpoints=WALL_CHECKPOINTS):
    """自然プレイ条件（既存の確率モデルそのまま。意図的リレー操作は一切モデル化しない）
    での強化壁への累積ダメージ・周回数を、チェックポイントtickごとに集計する。"""
    rng = random.Random(seed)
    per_cp_loops = {cp: [] for cp in checkpoints}
    per_cp_damage = {cp: [] for cp in checkpoints}
    final_wall_damage_sum = 0.0
    final_reinforced_hits_sum = 0

    for _ in range(trials):
        r = simulate_trial(rng, NATURAL_N, serve_mode, NATURAL_DECAY_ON, NATURAL_WEAKEST_VANISH,
                            ticks, slope_kind=slope_kind, checkpoints=checkpoints)
        for cp in checkpoints:
            cpr = r["checkpoint_results"].get(cp)
            if cpr is not None:
                per_cp_loops[cp].append(cpr["loops"])
                per_cp_damage[cp].append(cpr["wall_damage"])
        final_wall_damage_sum += r["wall_damage_total"]
        final_reinforced_hits_sum += r["reinforced_hits_total"]

    return {
        "slope_kind": slope_kind, "serve_mode": serve_mode, "N": NATURAL_N,
        "trials": trials, "ticks": ticks,
        "per_cp_loops": per_cp_loops, "per_cp_damage": per_cp_damage,
        "hits_per_unit": (final_reinforced_hits_sum / final_wall_damage_sum
                           if final_wall_damage_sum > 0 else None),
    }


def derive_requirements(natural_ref, checkpoint):
    """基準条件(natural_ref)の、あるcheckpointにおける累積ダメージ分布から、
    百分位×安全係数の全組み合わせで要求量候補を導出する。"""
    damages_sorted = sorted(natural_ref["per_cp_damage"][checkpoint])
    reqs = []
    for p in PERCENTILES:
        base = pct(damages_sorted, p)
        if base is None:
            continue
        for sf in SAFETY_FACTORS:
            reqs.append({"percentile": p, "safety_factor": sf,
                         "base_damage": base, "requirement": base * sf})
    return reqs


def incidental_clear_rate(damages, requirement):
    if not damages:
        return None
    return sum(1 for d in damages if d >= requirement) / len(damages)


def run_relay(seed, slope_kind, wall_targets, trials, ticks, paddle_catch_rate=1.0):
    """意図的リレー成立を仮定した場合のクリア所要時間を推定する。
    paddle_catch_rate=1.0（球を絶対に落とさない）+ serve_mode='inf'（最大供給）を
    「協調して球を落とさないよう努める意図的リレー」の代理条件とした
    （実際の協調戦略そのものをモデル化したものではない。捨象した仮定として報告する）。

    paddle_catch_rate<1.0を渡すと、「実卓のプレイヤーはリレー中も一定確率で
    球を落とす」という、より保守的な代理条件になる（指示書03のcatch_rate感度検証用）。
    """
    rng = random.Random(seed)
    reach_ticks = {t: [] for t in wall_targets}
    for _ in range(trials):
        r = simulate_trial(rng, NATURAL_N, "inf", NATURAL_DECAY_ON, NATURAL_WEAKEST_VANISH,
                            ticks, slope_kind=slope_kind, paddle_catch_rate=paddle_catch_rate,
                            wall_targets=wall_targets)
        for t in wall_targets:
            tick = r["wall_target_ticks"].get(t)
            if tick is not None:
                reach_ticks[t].append(tick)
    summary = {}
    for t in wall_targets:
        vals = sorted(reach_ticks[t])
        summary[t] = {
            "reached_frac": len(vals) / trials,
            "median_tick": pct(vals, 0.5),
            "p90_tick": pct(vals, 0.9),
        }
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--natural-trials", type=int, default=3000)
    ap.add_argument("--relay-trials", type=int, default=300)
    ap.add_argument("--relay-ticks", type=int, default=3000)
    ap.add_argument("--out", type=str, default="sim/ring_structure/results")
    args = ap.parse_args()

    # +1: range(ticks)はtick=ticks-1までしか回らないため、+1しないと
    # 最大のcheckpoint（例:1800）がtick==1800判定に一度も到達できず、
    # そのcheckpointの結果がQ1/Q3から静かに欠落するバグになる（実際に1回踏んだ）。
    natural_ticks = max(WALL_CHECKPOINTS) + 1
    natural_results = {}  # (slope_kind, serve_mode) -> run_natural() result
    idx = 0
    for slope_kind in SLOPE_KINDS:
        for serve_mode in ("inf", "interval"):
            r = run_natural(wall_seed(idx), slope_kind, serve_mode, args.natural_trials, natural_ticks)
            natural_results[(slope_kind, serve_mode)] = r
            idx += 1
            print(f"[natural] slope={slope_kind} serve={serve_mode} "
                  f"hits_per_unit={r['hits_per_unit']}")

    # --- Q1: 自然プレイでの偶発周回回数の分布（チェックポイントtickごと） ---
    q1_rows = []
    for (slope_kind, serve_mode), r in natural_results.items():
        for cp in WALL_CHECKPOINTS:
            loops = sorted(r["per_cp_loops"][cp])
            if not loops:
                continue
            q1_rows.append({
                "slope_kind": slope_kind, "serve_mode": serve_mode, "checkpoint_ticks": cp,
                "trials": args.natural_trials,
                "loops_mean": statistics.mean(loops),
                "loops_median": pct(loops, 0.5),
                "loops_p90": pct(loops, 0.9),
                "loops_p99": pct(loops, 0.99),
            })

    # --- Q3: 要求量候補ごとの偶発クリア到達率（基準条件=serve_mode inf） ---
    q3_rows = []
    all_requirements = {}  # slope_kind -> set of requirement values (Q2で使う)
    for slope_kind in SLOPE_KINDS:
        ref = natural_results[(slope_kind, REFERENCE_SERVE_MODE)]
        alt = natural_results[(slope_kind, "interval")]
        all_requirements[slope_kind] = set()
        for cp in WALL_CHECKPOINTS:
            reqs = derive_requirements(ref, cp)
            for req in reqs:
                requirement = req["requirement"]
                all_requirements[slope_kind].add(requirement)
                clear_rate_ref = incidental_clear_rate(ref["per_cp_damage"][cp], requirement)
                clear_rate_alt = incidental_clear_rate(alt["per_cp_damage"][cp], requirement)
                q3_rows.append({
                    "slope_kind": slope_kind, "checkpoint_ticks": cp,
                    "percentile": req["percentile"], "safety_factor": req["safety_factor"],
                    "base_damage": req["base_damage"], "requirement": requirement,
                    "clear_rate_serve_inf": clear_rate_ref,
                    "clear_rate_serve_interval": clear_rate_alt,
                    "under_1pct_both": (clear_rate_ref is not None and clear_rate_ref < 0.01
                                         and clear_rate_alt is not None and clear_rate_alt < 0.01),
                })

    # --- Q3補足：安全係数2/4/8では全組み合わせでclear_rate=0.0だったため、
    # 「1%を下回る最小の安全係数」がどこにあるかを、より細かい刻みで追加調査する
    # （percentile=0.5・全checkpointに対して、追加の乱数試行なしで既存分布から
    # 事後的に算出できる。自然プレイのシミュレーションを再実行する必要はない）。
    SAFETY_FACTORS_FINE = [1.0, 1.05, 1.1, 1.2, 1.5]
    q3_fine_rows = []
    for slope_kind in SLOPE_KINDS:
        ref = natural_results[(slope_kind, REFERENCE_SERVE_MODE)]
        alt = natural_results[(slope_kind, "interval")]
        for cp in WALL_CHECKPOINTS:
            damages_sorted = sorted(ref["per_cp_damage"][cp])
            base = pct(damages_sorted, 0.5)
            if base is None:
                continue
            for sf in SAFETY_FACTORS_FINE:
                requirement = base * sf
                all_requirements[slope_kind].add(requirement)  # Q2のリレー計測対象にも含める
                clear_rate_ref = incidental_clear_rate(ref["per_cp_damage"][cp], requirement)
                clear_rate_alt = incidental_clear_rate(alt["per_cp_damage"][cp], requirement)
                q3_fine_rows.append({
                    "slope_kind": slope_kind, "checkpoint_ticks": cp, "percentile": 0.5,
                    "safety_factor": sf, "base_damage": base, "requirement": requirement,
                    "clear_rate_serve_inf": clear_rate_ref,
                    "clear_rate_serve_interval": clear_rate_alt,
                })

    # --- Q2: リレー成立時のクリア所要時間（要求量ごと） ---
    q2_rows = []
    for slope_kind in SLOPE_KINDS:
        targets = sorted(all_requirements[slope_kind])
        relay_summary = run_relay(wall_seed(1000 + SLOPE_KINDS.index(slope_kind)),
                                   slope_kind, targets, args.relay_trials, args.relay_ticks)
        print(f"[relay] slope={slope_kind} targets={len(targets)} done")
        for t in targets:
            s = relay_summary[t]
            q2_rows.append({
                "slope_kind": slope_kind, "requirement": t,
                "relay_trials": args.relay_trials, "relay_ticks": args.relay_ticks,
                "reached_frac": s["reached_frac"],
                "median_tick": s["median_tick"], "p90_tick": s["p90_tick"],
            })
    q2_by_key = {(r["slope_kind"], r["requirement"]): r for r in q2_rows}

    # Q3にQ2のリレー所要時間を突き合わせて併記（【報告してほしいこと】の両立可否判定用）
    for row in q3_rows:
        key = (row["slope_kind"], row["requirement"])
        q2r = q2_by_key.get(key)
        row["relay_median_tick"] = q2r["median_tick"] if q2r else None
        row["relay_reached_frac"] = q2r["reached_frac"] if q2r else None
    for row in q3_fine_rows:
        key = (row["slope_kind"], row["requirement"])
        q2r = q2_by_key.get(key)
        row["relay_median_tick"] = q2r["median_tick"] if q2r else None
        row["relay_reached_frac"] = q2r["reached_frac"] if q2r else None

    os.makedirs(args.out, exist_ok=True)
    for name, rows, cols in (
        ("wall_q1_natural_loops.csv", q1_rows,
         ["slope_kind", "serve_mode", "checkpoint_ticks", "trials",
          "loops_mean", "loops_median", "loops_p90", "loops_p99"]),
        ("wall_q3_requirements.csv", q3_rows,
         ["slope_kind", "checkpoint_ticks", "percentile", "safety_factor", "base_damage",
          "requirement", "clear_rate_serve_inf", "clear_rate_serve_interval", "under_1pct_both",
          "relay_median_tick", "relay_reached_frac"]),
        ("wall_q2_relay.csv", q2_rows,
         ["slope_kind", "requirement", "relay_trials", "relay_ticks",
          "reached_frac", "median_tick", "p90_tick"]),
        ("wall_q3_requirements_fine.csv", q3_fine_rows,
         ["slope_kind", "checkpoint_ticks", "percentile", "safety_factor", "base_damage",
          "requirement", "clear_rate_serve_inf", "clear_rate_serve_interval",
          "relay_median_tick", "relay_reached_frac"]),
    ):
        path = f"{args.out}/{name}"
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for row in rows:
                w.writerow({c: row.get(c) for c in cols})
        print(f"RESULT: wrote {path} ({len(rows)} rows)")

    hits_per_unit = {f"{sk}/{sm}": natural_results[(sk, sm)]["hits_per_unit"]
                      for sk in SLOPE_KINDS for sm in ("inf", "interval")}
    meta = {
        "script": "sim/ring_structure/wall_sim.py",
        "wall_master_seed": WALL_MASTER_SEED,
        "natural_trials": args.natural_trials, "natural_ticks": natural_ticks,
        "relay_trials": args.relay_trials, "relay_ticks": args.relay_ticks,
        "checkpoints": WALL_CHECKPOINTS, "percentiles": PERCENTILES,
        "safety_factors": SAFETY_FACTORS, "slope_kinds": SLOPE_KINDS,
        "reference_serve_mode": REFERENCE_SERVE_MODE,
        "hits_per_unit": hits_per_unit,
        "q4_calibration": {
            "runs_sec_per_100tick": Q4_CALIBRATION_RUNS_SEC_PER_100TICK,
            "ms_per_tick": Q4_MS_PER_TICK,
            "checkpoint_minutes": {cp: round(cp * Q4_MS_PER_TICK / 1000 / 60, 2)
                                    for cp in WALL_CHECKPOINTS},
            "session_candidate_ticks": 900,
            "note": "指示書03で測定・確定。900tick(約21分)が「1セッション相当」の最有力候補",
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(f"{args.out}/wall_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"RESULT: wrote {args.out}/wall_meta.json")
    print("RESULT: PASS")


if __name__ == "__main__":
    main()
