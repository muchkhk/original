#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
束の再設計掃引（指示書08）

報告07（`proto/報告_アイテム9種化と較正プロト改修_v1.md`）で、9種化に伴う
damage_mean膨張（約57,000→約62,400）により要求量64,672.2再検証がFAILしたことを受け、
設計チャット21が確定した設計制約（laser配分約10%固定・weaken_multiplier=2.0固定・
pickup_miss_rate_baseは調整対象外）の下で、drop_rateを主軸に束を再設計する。

【設計チャット21の確定事項（調整禁止）】
  - laserの配分目安＝全体破壊量の約10%（許容幅8〜12%）
  - weaken_multiplier＝2.0固定
  - pickup_miss_rate_baseは調整対象から除外（頑健性掃引でのみ触れる。目標合わせ禁止）
  - 凍結継続：要求量64,672.2・SERVE定数・900tick・安全係数1.2・帯B1〜B3の定義

削減の主軸はdrop_rate引き下げ（報告07のB2参考値28.7秒が帯45〜120秒の下限を
大きく割っていたため、drop_rate引き下げはdamage_mean削減とB2引き上げの両方に
同じ向きで効く）。

3モード：
  --mode sweep       作業2：drop_rate×ball_effect_ticks 8組の束再設計掃引
  --mode robustness   作業3：指定した束でpickup_miss_rate_base 3水準の頑健性掃引
  --mode final        作業4：指定した束で自然3000・リレー300の最終規模検証

使い方:
  python item_budget_sweep.py --mode sweep --out results
  python item_budget_sweep.py --mode robustness --drop-rate 0.10 --ticks 2 --laser-dps 0.35 --out results
  python item_budget_sweep.py --mode final --drop-rate 0.10 --ticks 2 --laser-dps 0.35 --out results
"""
import argparse
import csv
import json
import os
import random
import statistics
from datetime import datetime, timezone

from ring_sim import simulate_trial, pct
from wall_sim import Q4_MS_PER_TICK
from item_sim import (item_seed, NATURAL_N, NATURAL_DECAY_ON, NATURAL_WEAKEST_VANISH,
                       NATURAL_SERVE_MODE, SESSION_TICKS, B3_MAX_FRAC,
                       FIXED_POWER_BONUS_PER_STAGE, FIXED_PADDLE_CATCH_RATE_CAP,
                       run_bundle, summarize_bundle, evaluate_b3)
from item_requirement_check import REQUIREMENT, SLOPE_KIND

# ============ 設計チャット21の確定事項（調整禁止） ============
LASER_TARGET_FRAC = 0.10   # laser配分の目標（全体破壊量の約10%）
LASER_TOL = 0.02           # 許容幅8〜12%（目標±2pt）
WEAKEN_MULTIPLIER_FIXED = 2.0
DEFAULT_PICKUP_MISS_RATE_BASE = 0.20  # ring_sim.pyの既定値と同じ（作業2・4では既定のまま）
BALL_EFFECT_MAGNITUDE_FIXED = 1.0

# 作業2掃引グリッド（中心候補drop_rate=0.10, ticks=2）
DROP_RATES = [0.08, 0.10, 0.12, 0.15]
TICKS_LIST = [2, 3]

# 作業3頑健性掃引
ROBUSTNESS_MISS_RATES = [0.10, 0.20, 0.30]


def base_item_system(drop_rate, ticks, pickup_miss_rate_base=DEFAULT_PICKUP_MISS_RATE_BASE):
    return {
        "drop_rate": drop_rate, "ball_effect_ticks": ticks,
        "ball_effect_magnitude": BALL_EFFECT_MAGNITUDE_FIXED,
        "paddle_power_bonus_per_stage": FIXED_POWER_BONUS_PER_STAGE,
        "paddle_catch_rate_cap": FIXED_PADDLE_CATCH_RATE_CAP,
        "pickup_miss_rate_base": pickup_miss_rate_base,
        "weaken_multiplier": WEAKEN_MULTIPLIER_FIXED,
    }


def measure_natural_damage(seed, item_system, trials):
    rng = random.Random(seed)
    damages = []
    for _ in range(trials):
        r = simulate_trial(rng, NATURAL_N, NATURAL_SERVE_MODE, NATURAL_DECAY_ON,
                            NATURAL_WEAKEST_VANISH, SESSION_TICKS + 1, slope_kind=SLOPE_KIND,
                            checkpoints=[SESSION_TICKS], item_system=item_system)
        damages.append(r["checkpoint_results"][SESSION_TICKS]["wall_damage"])
    return damages


def measure_relay(seed, item_system, trials, ticks=10000):
    rng = random.Random(seed)
    reach = []
    for _ in range(trials):
        r = simulate_trial(rng, NATURAL_N, NATURAL_SERVE_MODE, NATURAL_DECAY_ON,
                            NATURAL_WEAKEST_VANISH, ticks, slope_kind=SLOPE_KIND,
                            paddle_catch_rate=1.0, wall_targets=[REQUIREMENT],
                            item_system=item_system)
        t = r["wall_target_ticks"].get(REQUIREMENT)
        if t is not None:
            reach.append(t)
    return {
        "reached_frac": len(reach) / trials,
        "median_tick": pct(sorted(reach), 0.5) if reach else None,
        "p90_tick": pct(sorted(reach), 0.9) if reach else None,
    }


def calibrate_laser_dps(seed, bundle_no_laser, calib_trials=200, max_iter=7):
    """laser由来ダメージがLASER_TARGET_FRAC（既定10%）になるlaser_dps_per_stageを
    二分探索で較正する（報告07§7の桁選定と同じ実測較正の手法を、二分探索で自動化）。
    戻り値: (dps, achieved_frac, baseline_mean)
    """
    baseline_item_system = dict(bundle_no_laser, laser_dps_per_stage=0.0)
    baseline_damages = measure_natural_damage(item_seed(seed), baseline_item_system, calib_trials)
    baseline_mean = statistics.mean(baseline_damages)
    target_mean = baseline_mean * (1 + LASER_TARGET_FRAC)

    lo, hi = 0.0, 2.0
    best_dps, best_frac = 0.0, 0.0
    for i in range(max_iter):
        mid = (lo + hi) / 2
        item_system = dict(bundle_no_laser, laser_dps_per_stage=mid)
        damages = measure_natural_damage(item_seed(seed + 1000 + i), item_system, calib_trials)
        mean = statistics.mean(damages)
        frac = (mean - baseline_mean) / baseline_mean
        best_dps, best_frac = mid, frac
        if abs(frac - LASER_TARGET_FRAC) <= LASER_TOL:
            break
        if frac < LASER_TARGET_FRAC:
            lo = mid
        else:
            hi = mid
    return best_dps, best_frac, baseline_mean


def measure_full_bundle(seed, item_system, natural_trials, relay_trials, b3_trials):
    """natural・relay・B3の3種を1束についてまとめて測定する。"""
    damages = measure_natural_damage(item_seed(seed), item_system, natural_trials)
    clear = sum(1 for d in damages if d >= REQUIREMENT) / len(damages)
    relay = measure_relay(item_seed(seed + 1), item_system, relay_trials)
    relay_median_min = relay["median_tick"] * Q4_MS_PER_TICK / 1000 / 60 if relay["median_tick"] else None
    relay_p90_min = relay["p90_tick"] * Q4_MS_PER_TICK / 1000 / 60 if relay["p90_tick"] else None

    raw = run_bundle(item_seed(seed + 2), item_system, b3_trials)
    summary = summarize_bundle(raw)
    b3 = evaluate_b3(summary)

    return {
        "natural_clear_rate": clear,
        "damage_mean": statistics.mean(damages),
        "relay_reached_frac": relay["reached_frac"],
        "relay_median_min": relay_median_min,
        "relay_p90_min": relay_p90_min,
        "b3_mean_frac": summary["b3_uptime_mean_frac"],
        "b3_pass": b3["pass"],
        "b1_ref": summary["b1_drop_mean_REFERENCE_ONLY"],
        "b2_ref_sec": summary["b2_recovery_mean_sec_REFERENCE_ONLY"],
        "weaken_uptime_ref": summary["weaken_uptime_mean_frac_REFERENCE_ONLY"],
    }


def evaluate_pass(result, laser_frac):
    meets_natural = result["natural_clear_rate"] == 0.0
    meets_reached = result["relay_reached_frac"] == 1.0
    meets_median = result["relay_median_min"] is not None and 23.0 <= result["relay_median_min"] <= 24.0
    meets_b3 = result["b3_pass"]
    meets_laser = abs(laser_frac - LASER_TARGET_FRAC) <= LASER_TOL
    return {
        "meets_natural_zero": meets_natural, "meets_reached_full": meets_reached,
        "meets_median_23_24min": meets_median, "meets_b3": meets_b3,
        "meets_laser_8_12pct": meets_laser,
        "all_pass": meets_natural and meets_reached and meets_median and meets_b3 and meets_laser,
    }


def run_sweep(args):
    rows = []
    for i, dr in enumerate(DROP_RATES):
        for j, ticks in enumerate(TICKS_LIST):
            idx = i * len(TICKS_LIST) + j
            bundle_no_laser = base_item_system(dr, ticks)
            dps, laser_frac, baseline_mean = calibrate_laser_dps(9000 + idx * 10, bundle_no_laser,
                                                                   calib_trials=args.calib_trials)
            item_system = dict(bundle_no_laser, laser_dps_per_stage=dps)
            result = measure_full_bundle(9000 + idx * 10 + 5, item_system,
                                          args.natural_trials, args.relay_trials, args.b3_trials)
            verdict = evaluate_pass(result, laser_frac)
            row = {
                "drop_rate": dr, "ball_effect_ticks": ticks,
                "baseline_damage_mean_no_laser": baseline_mean,
                "laser_dps_per_stage": dps, "laser_frac_achieved": laser_frac,
                "natural_clear_rate": result["natural_clear_rate"],
                "damage_mean": result["damage_mean"],
                "relay_reached_frac": result["relay_reached_frac"],
                "relay_median_min": result["relay_median_min"],
                "relay_p90_min": result["relay_p90_min"],
                "b3_mean_frac": result["b3_mean_frac"],
                "b1_ref_drops_per_session": result["b1_ref"],
                "b2_ref_recovery_sec": result["b2_ref_sec"],
                "weaken_uptime_ref": result["weaken_uptime_ref"],
                "meets_natural_zero": verdict["meets_natural_zero"],
                "meets_reached_full": verdict["meets_reached_full"],
                "meets_median_23_24min": verdict["meets_median_23_24min"],
                "meets_b3": verdict["meets_b3"],
                "meets_laser_8_12pct": verdict["meets_laser_8_12pct"],
                "all_pass": verdict["all_pass"],
            }
            rows.append(row)
            print(f"[sweep] drop_rate={dr} ticks={ticks} laser_dps={dps:.3f}(frac={laser_frac:.3f}) "
                  f"natural_clear={result['natural_clear_rate']:.4f} "
                  f"relay_median={result['relay_median_min']} "
                  f"B3={result['b3_mean_frac']:.3f}({'OK' if result['b3_pass'] else 'NG'}) "
                  f"B2ref={result['b2_ref_sec']} "
                  f"ALL_PASS={verdict['all_pass']}")

    os.makedirs(args.out, exist_ok=True)
    path = f"{args.out}/item_budget_sweep.csv"
    cols = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for row in rows:
            w.writerow(row)
    print(f"RESULT: wrote {path} ({len(rows)} rows)")

    pass_rows = [r for r in rows if r["all_pass"]]
    print(f"RESULT: {len(pass_rows)}/{len(rows)} bundles satisfy all criteria")

    meta = {
        "script": "sim/ring_structure/item_budget_sweep.py", "mode": "sweep",
        "requirement": REQUIREMENT, "laser_target_frac": LASER_TARGET_FRAC,
        "laser_tol": LASER_TOL, "weaken_multiplier_fixed": WEAKEN_MULTIPLIER_FIXED,
        "drop_rates_swept": DROP_RATES, "ticks_swept": TICKS_LIST,
        "natural_trials": args.natural_trials, "relay_trials": args.relay_trials,
        "b3_trials": args.b3_trials, "pass_count": len(pass_rows),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(f"{args.out}/item_budget_sweep_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"RESULT: wrote {args.out}/item_budget_sweep_meta.json")
    print("RESULT: PASS")


def run_robustness(args):
    if args.drop_rate is None or args.ticks is None or args.laser_dps is None:
        raise SystemExit("robustness/finalモードには --drop-rate --ticks --laser-dps が必須です")

    rows = []
    for i, miss_rate in enumerate(ROBUSTNESS_MISS_RATES):
        bundle = base_item_system(args.drop_rate, args.ticks, pickup_miss_rate_base=miss_rate)
        item_system = dict(bundle, laser_dps_per_stage=args.laser_dps)
        result = measure_full_bundle(9500 + i * 10, item_system,
                                      args.natural_trials, args.relay_trials, args.b3_trials)
        # laser_fracはこの束専用に再計測（miss_rateを変えるとbaselineも変わるため）
        baseline_item_system = dict(bundle, laser_dps_per_stage=0.0)
        baseline_damages = measure_natural_damage(item_seed(9600 + i), baseline_item_system, args.calib_trials)
        baseline_mean = statistics.mean(baseline_damages)
        laser_frac = (result["damage_mean"] - baseline_mean) / baseline_mean
        verdict = evaluate_pass(result, laser_frac)
        row = {
            "pickup_miss_rate_base": miss_rate,
            "drop_rate": args.drop_rate, "ball_effect_ticks": args.ticks,
            "laser_dps_per_stage": args.laser_dps, "laser_frac_achieved": laser_frac,
            "natural_clear_rate": result["natural_clear_rate"],
            "damage_mean": result["damage_mean"],
            "relay_reached_frac": result["relay_reached_frac"],
            "relay_median_min": result["relay_median_min"],
            "b3_mean_frac": result["b3_mean_frac"],
            "b2_ref_recovery_sec": result["b2_ref_sec"],
            "all_pass": verdict["all_pass"],
        }
        rows.append(row)
        print(f"[robustness] miss_rate={miss_rate} natural_clear={result['natural_clear_rate']:.4f} "
              f"relay_median={result['relay_median_min']} B3={result['b3_mean_frac']:.3f} "
              f"ALL_PASS={verdict['all_pass']}")

    os.makedirs(args.out, exist_ok=True)
    path = f"{args.out}/item_budget_robustness.csv"
    cols = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for row in rows:
            w.writerow(row)
    print(f"RESULT: wrote {path} ({len(rows)} rows)")
    robust = all(r["all_pass"] for r in rows)
    print(f"RESULT: robustness {'PASS' if robust else 'FAIL'} "
          f"({sum(1 for r in rows if r['all_pass'])}/{len(rows)} 水準で合格維持)")
    print("RESULT: PASS")


def run_final(args):
    if args.drop_rate is None or args.ticks is None or args.laser_dps is None:
        raise SystemExit("robustness/finalモードには --drop-rate --ticks --laser-dps が必須です")

    bundle = base_item_system(args.drop_rate, args.ticks)
    item_system = dict(bundle, laser_dps_per_stage=args.laser_dps)
    result = measure_full_bundle(9900, item_system, args.natural_trials, args.relay_trials, args.b3_trials)

    baseline_item_system = dict(bundle, laser_dps_per_stage=0.0)
    baseline_damages = measure_natural_damage(item_seed(9901), baseline_item_system, args.calib_trials)
    baseline_mean = statistics.mean(baseline_damages)
    laser_frac = (result["damage_mean"] - baseline_mean) / baseline_mean
    verdict = evaluate_pass(result, laser_frac)

    print(f"[final] bundle=drop_rate={args.drop_rate} ticks={args.ticks} laser_dps={args.laser_dps}")
    print(f"[final] natural_clear_rate={result['natural_clear_rate']:.4f}"
          f"({'OK' if verdict['meets_natural_zero'] else 'NG'}) damage_mean={result['damage_mean']:.1f}")
    print(f"[final] relay_reached_frac={result['relay_reached_frac']:.4f}"
          f"({'OK' if verdict['meets_reached_full'] else 'NG'}) "
          f"relay_median={result['relay_median_min']}min({'OK' if verdict['meets_median_23_24min'] else 'NG'}) "
          f"relay_p90={result['relay_p90_min']}min")
    print(f"[final] B3={result['b3_mean_frac']:.3f}({'OK' if verdict['meets_b3'] else 'NG'}) "
          f"laser_frac={laser_frac:.3f}({'OK' if verdict['meets_laser_8_12pct'] else 'NG'})")
    print(f"[final] B2参考値={result['b2_ref_sec']}秒（帯45〜120秒との比較）")
    print(f"[final] 全基準合格: {'PASS' if verdict['all_pass'] else 'FAIL'}")

    os.makedirs(args.out, exist_ok=True)
    path = f"{args.out}/item_budget_final.csv"
    row = {
        "drop_rate": args.drop_rate, "ball_effect_ticks": args.ticks,
        "laser_dps_per_stage": args.laser_dps, "laser_frac_achieved": laser_frac,
        "natural_trials": args.natural_trials, "natural_clear_rate": result["natural_clear_rate"],
        "damage_mean": result["damage_mean"], "relay_trials": args.relay_trials,
        "relay_reached_frac": result["relay_reached_frac"], "relay_median_min": result["relay_median_min"],
        "relay_p90_min": result["relay_p90_min"], "b3_mean_frac": result["b3_mean_frac"],
        "b1_ref_drops_per_session": result["b1_ref"], "b2_ref_recovery_sec": result["b2_ref_sec"],
        "weaken_uptime_ref": result["weaken_uptime_ref"],
        "meets_natural_zero": verdict["meets_natural_zero"], "meets_reached_full": verdict["meets_reached_full"],
        "meets_median_23_24min": verdict["meets_median_23_24min"], "meets_b3": verdict["meets_b3"],
        "meets_laser_8_12pct": verdict["meets_laser_8_12pct"], "all_pass": verdict["all_pass"],
    }
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        w.writeheader()
        w.writerow(row)
    print(f"RESULT: wrote {path} (1 row)")

    meta = {
        "script": "sim/ring_structure/item_budget_sweep.py", "mode": "final",
        "requirement": REQUIREMENT, "bundle": row, "all_pass": verdict["all_pass"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(f"{args.out}/item_budget_final_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"RESULT: wrote {args.out}/item_budget_final_meta.json")
    print("RESULT: PASS")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["sweep", "robustness", "final"], required=True)
    ap.add_argument("--drop-rate", type=float, default=None)
    ap.add_argument("--ticks", type=int, default=None)
    ap.add_argument("--laser-dps", type=float, default=None)
    ap.add_argument("--calib-trials", type=int, default=200)
    ap.add_argument("--natural-trials", type=int, default=300)
    ap.add_argument("--relay-trials", type=int, default=100)
    ap.add_argument("--b3-trials", type=int, default=800)
    ap.add_argument("--out", type=str, default="sim/ring_structure/results")
    args = ap.parse_args()

    if args.mode == "sweep":
        run_sweep(args)
    elif args.mode == "robustness":
        run_robustness(args)
    else:
        run_final(args)


if __name__ == "__main__":
    main()
