#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
要求量再導出掃引と頑健性（指示書10）

報告09（0/18・relay中央値のみ全組未達）の照合により、凍結リストの内部矛盾――
要求量64,672.2（旧束のセッション与ダメ×1.2の導出値）と安全係数1.2が、アイテム導入後の
与ダメ増（damage_mean≒60,900）のもとで両立しない（実効係数≒1.06）――が確認された
（設計チャット23 #1）。要求量の絶対値凍結を解除し、安全係数原則の側から再導出する。

【束は全作業で固定（変更禁止）】報告09の最良束：
  drop_rate=0.05 / weaken_multiplier=1.3 / laser配分目標5%（許容±1.5pt）/
  ball_effect_ticks=2 / 貫通=3ヒット（PIERCE_HITS） / アイテム壁=各陣2個
【保護数字】pickup_miss_rate_base=0.20（作業2の頑健性でのみ変動）/ 貫通=3ヒット

【laser較正は1回のみ】REQUIREMENT（要求量）はsimulate_trial()の力学に一切影響しない
（natural_clear_rate算出とrelay wall_targetsの閾値としてのみ使われる、外部比較値）ため、
laser_dps_per_stageの較正はbaseline damage_meanを基準に1回だけ行い、4点で共通利用する。
これはコード上の事実（REQUIREMENT/要求量が item_system・simulate_trial のいずれの引数にも
現れない）から導かれる設計判断であり、4点それぞれのdamage_mean実測値が互いに近い範囲に
収まること自体が、この設計判断の経験的な裏付けにもなる（§停止則③参照）。

使い方:
  python item_requirement_rederive_sweep.py --mode sweep --out results
  python item_requirement_rederive_sweep.py --mode robustness --out results
"""
import argparse
import csv
import json
import math
import os
import statistics
from datetime import datetime, timezone

from ring_sim import simulate_trial, pct
from wall_sim import Q4_MS_PER_TICK
from item_sim import (item_seed, NATURAL_N, NATURAL_DECAY_ON, NATURAL_WEAKEST_VANISH,
                       NATURAL_SERVE_MODE, SESSION_TICKS, FIXED_POWER_BONUS_PER_STAGE,
                       FIXED_PADDLE_CATCH_RATE_CAP, run_bundle, summarize_bundle, evaluate_b3)
from item_requirement_check import SLOPE_KIND
from item_budget_sweep import measure_natural_damage
from item_compound_sweep import calibrate_laser_dps as _calibrate_laser_dps_impl

# ============ 束固定（報告09最良束。変更禁止） ============
FIXED_DROP_RATE = 0.05
FIXED_WEAKEN_MULTIPLIER = 1.3
FIXED_LASER_TARGET_FRAC = 0.05
FIXED_BALL_EFFECT_TICKS = 2
FIXED_ITEM_WALL_BRICKS = 2
FIXED_BALL_EFFECT_MAGNITUDE = 1.0
DEFAULT_PICKUP_MISS_RATE_BASE = 0.20

# ============ 要求量4点（設計チャット23 #1・実効安全係数対応） ============
REQUIREMENT_POINTS = [67300, 68700, 71000, 73100]
# 事前登録仮説：relay中央値の予測値（各±0.3分）
PREREG_RELAY_MEDIAN_PRED_MIN = {67300: 23.0, 68700: 23.5, 71000: 24.3, 73100: 25.0}
PREREG_TOL_MIN = 0.3

ROBUSTNESS_MISS_RATES = [0.10, 0.20, 0.30]


def base_item_system(pickup_miss_rate_base=DEFAULT_PICKUP_MISS_RATE_BASE):
    return {
        "drop_rate": FIXED_DROP_RATE, "ball_effect_ticks": FIXED_BALL_EFFECT_TICKS,
        "ball_effect_magnitude": FIXED_BALL_EFFECT_MAGNITUDE,
        "paddle_power_bonus_per_stage": FIXED_POWER_BONUS_PER_STAGE,
        "paddle_catch_rate_cap": FIXED_PADDLE_CATCH_RATE_CAP,
        "pickup_miss_rate_base": pickup_miss_rate_base,
        "weaken_multiplier": FIXED_WEAKEN_MULTIPLIER,
        "item_wall_bricks": FIXED_ITEM_WALL_BRICKS,
    }


def wilson_ci(k, n, z=1.959963985):
    """95%信頼区間（Wilson score interval）。二項比率の小標本・境界近傍でも安定。"""
    if n == 0:
        return (0.0, 0.0)
    phat = k / n
    denom = 1 + z * z / n
    center = phat + z * z / (2 * n)
    margin = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n))
    lo = (center - margin) / denom
    hi = (center + margin) / denom
    return (max(0.0, lo), min(1.0, hi))


def measure_relay_for_target(seed, item_system, target, trials, ticks=10000):
    import random
    rng = random.Random(seed)
    reach = []
    for _ in range(trials):
        r = simulate_trial(rng, NATURAL_N, NATURAL_SERVE_MODE, NATURAL_DECAY_ON,
                            NATURAL_WEAKEST_VANISH, ticks, slope_kind=SLOPE_KIND,
                            paddle_catch_rate=1.0, wall_targets=[target], item_system=item_system)
        t = r["wall_target_ticks"].get(target)
        if t is not None:
            reach.append(t)
    return {
        "reached_frac": len(reach) / trials,
        "median_tick": pct(sorted(reach), 0.5) if reach else None,
        "p90_tick": pct(sorted(reach), 0.9) if reach else None,
    }


def measure_point(seed_base, item_system, target, natural_trials, relay_trials, b3_trials):
    damages = measure_natural_damage(item_seed(seed_base), item_system, natural_trials)
    k = sum(1 for d in damages if d >= target)
    clear_rate = k / len(damages)
    ci_lo, ci_hi = wilson_ci(k, len(damages))

    relay = measure_relay_for_target(item_seed(seed_base + 1), item_system, target, relay_trials)
    relay_median_min = relay["median_tick"] * Q4_MS_PER_TICK / 1000 / 60 if relay["median_tick"] else None
    relay_p90_min = relay["p90_tick"] * Q4_MS_PER_TICK / 1000 / 60 if relay["p90_tick"] else None

    raw = run_bundle(item_seed(seed_base + 2), item_system, b3_trials)
    summary = summarize_bundle(raw)
    b3 = evaluate_b3(summary)

    return {
        "natural_clear_rate": clear_rate, "natural_clear_k": k, "natural_clear_n": len(damages),
        "natural_clear_ci_lo": ci_lo, "natural_clear_ci_hi": ci_hi,
        "damage_mean": statistics.mean(damages), "damage_stdev": statistics.stdev(damages),
        "relay_reached_frac": relay["reached_frac"],
        "relay_median_min": relay_median_min, "relay_p90_min": relay_p90_min,
        "b3_mean_frac": summary["b3_uptime_mean_frac"], "b3_pass": b3["pass"],
        "b1_ref": summary["b1_drop_mean_REFERENCE_ONLY"], "b2_ref_sec": summary["b2_recovery_mean_sec_REFERENCE_ONLY"],
        "weaken_uptime_ref": summary["weaken_uptime_mean_frac_REFERENCE_ONLY"],
    }


def run_sweep(args):
    # laser較正は1回のみ（束固定・要求量非依存。docstring参照）
    bundle_no_laser = base_item_system()
    dps, laser_frac, baseline_mean = _calibrate_laser_dps_impl(
        5000, bundle_no_laser, FIXED_LASER_TARGET_FRAC, args.calib_trials)
    item_system = dict(bundle_no_laser, laser_dps_per_stage=dps)
    print(f"[calib] laser_dps_per_stage={dps:.4f} achieved_frac={laser_frac:.4f} "
          f"baseline_mean(no laser)={baseline_mean:.1f}")

    rows = []
    for i, req in enumerate(REQUIREMENT_POINTS):
        seed_base = 20000 + i * 10
        res = measure_point(seed_base, item_system, req,
                             args.natural_trials, args.relay_trials, args.b3_trials)
        row = {"requirement": req, "laser_dps_per_stage": dps, "laser_frac_achieved": laser_frac}
        row.update(res)
        rows.append(row)
        print(f"[sweep] requirement={req} natural={res['natural_clear_rate']:.4f} "
              f"(k={res['natural_clear_k']}/{res['natural_clear_n']}, "
              f"CI=[{res['natural_clear_ci_lo']:.4f},{res['natural_clear_ci_hi']:.4f}]) "
              f"damage_mean={res['damage_mean']:.1f} relay_med={res['relay_median_min']} "
              f"reached={res['relay_reached_frac']:.3f} B3={res['b3_mean_frac']:.3f} "
              f"B2ref={res['b2_ref_sec']}")

    os.makedirs(args.out, exist_ok=True)
    path = f"{args.out}/item_requirement_rederive_sweep.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for row in rows:
            w.writerow(row)
    print(f"RESULT: wrote {path} ({len(rows)} rows)")

    # 停止則の判定
    all_natural_over = all(r["natural_clear_rate"] > 0.003 for r in rows)
    medians = [r["relay_median_min"] for r in rows]
    monotonic = all(medians[i] <= medians[i + 1] for i in range(len(medians) - 1)
                     if medians[i] is not None and medians[i + 1] is not None)
    dm = [r["damage_mean"] for r in rows]
    dm_mean = statistics.mean(dm)
    dm_spread_pct = (max(dm) - min(dm)) / dm_mean * 100
    dm_within_2pct = all(abs(d - dm_mean) / dm_mean <= 0.02 for d in dm)

    print(f"RESULT: stop-rule-1(全点natural>0.3%)={all_natural_over}")
    print(f"RESULT: stop-rule-2(relay中央値の単調性崩壊)={not monotonic}")
    print(f"RESULT: stop-rule-3(damage_mean 4点間スプレッド)={dm_spread_pct:.2f}% "
          f"within_2pct_of_mean={dm_within_2pct}")

    # 事前登録仮説の検証
    print("RESULT: prereg-hypothesis (relay median vs prediction):")
    for row in rows:
        req = row["requirement"]
        pred = PREREG_RELAY_MEDIAN_PRED_MIN[req]
        actual = row["relay_median_min"]
        diff = (actual - pred) if actual is not None else None
        within_tol = diff is not None and abs(diff) <= PREREG_TOL_MIN
        print(f"  requirement={req} predicted={pred:.1f}min actual={actual} "
              f"diff={diff} within_tol(±{PREREG_TOL_MIN})={within_tol}")

    meta = {
        "script": "sim/ring_structure/item_requirement_rederive_sweep.py", "mode": "sweep",
        "requirement_points": REQUIREMENT_POINTS,
        "fixed_bundle": {
            "drop_rate": FIXED_DROP_RATE, "weaken_multiplier": FIXED_WEAKEN_MULTIPLIER,
            "laser_target_frac": FIXED_LASER_TARGET_FRAC, "laser_dps_per_stage": dps,
            "ball_effect_ticks": FIXED_BALL_EFFECT_TICKS, "pierce_hits": 3,
            "item_wall_bricks": FIXED_ITEM_WALL_BRICKS,
            "pickup_miss_rate_base": DEFAULT_PICKUP_MISS_RATE_BASE,
        },
        "natural_trials": args.natural_trials, "relay_trials": args.relay_trials,
        "b3_trials": args.b3_trials,
        "stop_rule_1_all_natural_over_0.3pct": all_natural_over,
        "stop_rule_2_monotonic_broken": not monotonic,
        "stop_rule_3_damage_mean_spread_pct": dm_spread_pct,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(f"{args.out}/item_requirement_rederive_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"RESULT: wrote {args.out}/item_requirement_rederive_meta.json")
    print("RESULT: PASS")


def run_robustness(args):
    bundle_no_laser_default = base_item_system()
    dps_default, _, _ = _calibrate_laser_dps_impl(
        5000, bundle_no_laser_default, FIXED_LASER_TARGET_FRAC, args.calib_trials)

    rows = []
    idx = 0
    for req in REQUIREMENT_POINTS:
        for miss in ROBUSTNESS_MISS_RATES:
            seed_base = 21000 + idx * 10
            bundle_no_laser = base_item_system(pickup_miss_rate_base=miss)
            # miss_rate変更でbaselineが変わるため、laserは束ごとに再較正
            dps, laser_frac, baseline_mean = _calibrate_laser_dps_impl(
                seed_base, bundle_no_laser, FIXED_LASER_TARGET_FRAC, args.calib_trials)
            item_system = dict(bundle_no_laser, laser_dps_per_stage=dps)
            res = measure_point(seed_base + 3, item_system, req,
                                 args.natural_trials, args.relay_trials, args.b3_trials)
            row = {"requirement": req, "pickup_miss_rate_base": miss,
                   "laser_dps_per_stage": dps, "laser_frac_achieved": laser_frac}
            row.update(res)
            rows.append(row)
            idx += 1
            print(f"[robustness] requirement={req} miss={miss} "
                  f"natural={res['natural_clear_rate']:.4f} relay_med={res['relay_median_min']} "
                  f"B3={res['b3_mean_frac']:.3f}")

    os.makedirs(args.out, exist_ok=True)
    path = f"{args.out}/item_requirement_rederive_robustness.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for row in rows:
            w.writerow(row)
    print(f"RESULT: wrote {path} ({len(rows)} rows)")

    meta = {
        "script": "sim/ring_structure/item_requirement_rederive_sweep.py", "mode": "robustness",
        "requirement_points": REQUIREMENT_POINTS, "miss_rates": ROBUSTNESS_MISS_RATES,
        "natural_trials": args.natural_trials, "relay_trials": args.relay_trials,
        "b3_trials": args.b3_trials,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(f"{args.out}/item_requirement_rederive_robustness_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"RESULT: wrote {args.out}/item_requirement_rederive_robustness_meta.json")
    print("RESULT: PASS")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["sweep", "robustness"], required=True)
    ap.add_argument("--calib-trials", type=int, default=200)
    ap.add_argument("--natural-trials", type=int, default=2000)
    ap.add_argument("--relay-trials", type=int, default=200)
    ap.add_argument("--b3-trials", type=int, default=800)
    ap.add_argument("--out", type=str, default="sim/ring_structure/results")
    args = ap.parse_args()
    if args.mode == "sweep":
        run_sweep(args)
    else:
        run_robustness(args)


if __name__ == "__main__":
    main()
