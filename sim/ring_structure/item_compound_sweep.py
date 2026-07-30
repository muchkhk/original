#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
複合掃引（指示書09 作業4）＋頑健性掃引（作業5）＋最終要求量再検証（作業6）

指示書08の掃引失敗（0/8。報告08）を受けた再配分会議（設計チャット22）の決定を実装する。
指示書07/09の実装変更（9種＋抽選枠・guide不活性・貫通3ヒット・アイテム壁確定ドロップ）を
すべて織り込んだ上で、weaken_multiplier・laser配分・drop_rateの3軸を同時に振る。

【設計チャット22の凍結解除・新設定】
  - weaken_multiplier ∈ {1.3, 1.5, 1.7}（×2.0の凍結を解除）
  - laser配分目標 ∈ {5%, 7%}（約10%の凍結を解除。許容±1.5pt）
  - drop_rate ∈ {0.05, 0.08, 0.10}（アイテム壁の確定供給が加わるため下方拡張）
  = 18組。中心候補は drop_rate=0.10, ticks=2。

【固定（保護数字・凍結）】
  - ball_effect_ticks = 2（報告08最良束準拠）
  - 貫通 = 3ヒット（PIERCE_HITS。ring_sim側で固定。掃引軸ではない）
  - pickup_miss_rate_base = 0.20（器具忠実度。作業5の頑健性掃引でのみ変動）
  - アイテム壁 = 各陣2個（item_wall_bricks=2）
  - 要求量 64,672.2 / SERVE定数 / 900tick / 安全係数1.2 / 帯B1〜B3の定義

合格基準（報告08と同一・すべて同時）：
  natural_clear_rate=0.0% / relay中央値23〜24分 / reached_frac=1.000 /
  B3≤0.40（球側3種のみ・定義変更禁止） / laser配分が帯（目標±1.5pt）内。
別掲（判定禁止・参考のみ）：B1参考値・B2参考値の分布、weaken稼働率。

停止則：作業4で合格束がゼロなら、パラメータを弄り続けず、最も基準に近い束と
構造的所見を報告して停止する（作業5・6は実施しない）。

使い方:
  python item_compound_sweep.py --mode sweep --out results
  python item_compound_sweep.py --mode robustness --weaken 1.5 --laser-frac 0.05 --drop-rate 0.08 --out results
  python item_compound_sweep.py --mode final --weaken 1.5 --laser-frac 0.05 --drop-rate 0.08 --out results
"""
import argparse
import csv
import json
import os
import statistics
from datetime import datetime, timezone

from ring_sim import simulate_trial, pct
from wall_sim import Q4_MS_PER_TICK
from item_sim import (item_seed, NATURAL_N, NATURAL_DECAY_ON, NATURAL_WEAKEST_VANISH,
                       NATURAL_SERVE_MODE, SESSION_TICKS, B3_MAX_FRAC,
                       FIXED_POWER_BONUS_PER_STAGE, FIXED_PADDLE_CATCH_RATE_CAP,
                       run_bundle, summarize_bundle, evaluate_b3)
from item_requirement_check import REQUIREMENT, SLOPE_KIND
from item_budget_sweep import measure_natural_damage, measure_relay

# ============ 設計チャット22の設定 ============
WEAKEN_LEVELS = [1.3, 1.5, 1.7]
LASER_FRAC_LEVELS = [0.05, 0.07]
LASER_TOL = 0.015  # 許容±1.5pt
DROP_RATE_LEVELS = [0.05, 0.08, 0.10]

BALL_EFFECT_TICKS_FIXED = 2
PICKUP_MISS_RATE_DEFAULT = 0.20
ITEM_WALL_BRICKS = 2
BALL_EFFECT_MAGNITUDE_FIXED = 1.0

# 事前登録仮説（報告08比）：報告08の最も近い束 drop=0.08/ticks=2 の relay_median=20.88分。
# weaken・laserの引き下げがcatch_rate=1.0のrelay測定にも作用する（壁破壊速度の直接減）ため、
# relay_medianを報告08比+2分以上（≒22.88分以上）押し上げる、と予想。
REPORT08_BEST_RELAY_MEDIAN_MIN = 20.88
PREREG_RELAY_UPLIFT_MIN = 2.0

ROBUSTNESS_MISS_RATES = [0.10, 0.20, 0.30]


def base_item_system(drop_rate, weaken_multiplier, pickup_miss_rate_base=PICKUP_MISS_RATE_DEFAULT):
    return {
        "drop_rate": drop_rate, "ball_effect_ticks": BALL_EFFECT_TICKS_FIXED,
        "ball_effect_magnitude": BALL_EFFECT_MAGNITUDE_FIXED,
        "paddle_power_bonus_per_stage": FIXED_POWER_BONUS_PER_STAGE,
        "paddle_catch_rate_cap": FIXED_PADDLE_CATCH_RATE_CAP,
        "pickup_miss_rate_base": pickup_miss_rate_base,
        "weaken_multiplier": weaken_multiplier,
        "item_wall_bricks": ITEM_WALL_BRICKS,
    }


def calibrate_laser_dps(seed, bundle_no_laser, target_frac, calib_trials, max_iter=9):
    """laser由来ダメージがtarget_fracになるlaser_dps_per_stageを二分探索で較正。
    戻り値: (dps, achieved_frac, baseline_mean)。報告08 calibrate_laser_dps と同手法。"""
    baseline = statistics.mean(measure_natural_damage(
        item_seed(seed), dict(bundle_no_laser, laser_dps_per_stage=0.0), calib_trials))
    lo, hi = 0.0, 3.0
    best_dps, best_frac = 0.0, 0.0
    for i in range(max_iter):
        mid = (lo + hi) / 2
        mean = statistics.mean(measure_natural_damage(
            item_seed(seed + 1000 + i), dict(bundle_no_laser, laser_dps_per_stage=mid), calib_trials))
        frac = (mean - baseline) / baseline if baseline else 0.0
        best_dps, best_frac = mid, frac
        if abs(frac - target_frac) <= LASER_TOL:
            break
        if frac < target_frac:
            lo = mid
        else:
            hi = mid
    return best_dps, best_frac, baseline


def measure_full(seed, item_system, natural_trials, relay_trials, b3_trials):
    damages = measure_natural_damage(item_seed(seed), item_system, natural_trials)
    clear = sum(1 for d in damages if d >= REQUIREMENT) / len(damages)
    relay = measure_relay(item_seed(seed + 1), item_system, relay_trials)
    relay_median_min = relay["median_tick"] * Q4_MS_PER_TICK / 1000 / 60 if relay["median_tick"] else None
    relay_p90_min = relay["p90_tick"] * Q4_MS_PER_TICK / 1000 / 60 if relay["p90_tick"] else None
    raw = run_bundle(item_seed(seed + 2), item_system, b3_trials)
    summary = summarize_bundle(raw)
    b3 = evaluate_b3(summary)
    return {
        "natural_clear_rate": clear, "damage_mean": statistics.mean(damages),
        "relay_reached_frac": relay["reached_frac"],
        "relay_median_min": relay_median_min, "relay_p90_min": relay_p90_min,
        "b3_mean_frac": summary["b3_uptime_mean_frac"], "b3_pass": b3["pass"],
        "b1_ref": summary["b1_drop_mean_REFERENCE_ONLY"],
        "b2_ref_sec": summary["b2_recovery_mean_sec_REFERENCE_ONLY"],
        "weaken_uptime_ref": summary["weaken_uptime_mean_frac_REFERENCE_ONLY"],
    }


def evaluate_pass(result, laser_frac, target_frac):
    meets_natural = result["natural_clear_rate"] == 0.0
    meets_reached = result["relay_reached_frac"] == 1.0
    meets_median = result["relay_median_min"] is not None and 23.0 <= result["relay_median_min"] <= 24.0
    meets_b3 = result["b3_pass"]
    meets_laser = abs(laser_frac - target_frac) <= LASER_TOL
    return {
        "meets_natural_zero": meets_natural, "meets_reached_full": meets_reached,
        "meets_median_23_24min": meets_median, "meets_b3": meets_b3,
        "meets_laser_band": meets_laser,
        "all_pass": meets_natural and meets_reached and meets_median and meets_b3 and meets_laser,
    }


def run_sweep(args):
    rows = []
    idx = 0
    for dr in DROP_RATE_LEVELS:
        for wk in WEAKEN_LEVELS:
            for lf in LASER_FRAC_LEVELS:
                seed_base = 10000 + idx * 20
                bundle_no_laser = base_item_system(dr, wk)
                dps, laser_frac, baseline = calibrate_laser_dps(
                    seed_base, bundle_no_laser, lf, args.calib_trials)
                item_system = dict(bundle_no_laser, laser_dps_per_stage=dps)
                res = measure_full(seed_base + 5, item_system,
                                    args.natural_trials, args.relay_trials, args.b3_trials)
                verdict = evaluate_pass(res, laser_frac, lf)
                row = {
                    "drop_rate": dr, "weaken_multiplier": wk, "laser_frac_target": lf,
                    "baseline_damage_mean_no_laser": baseline,
                    "laser_dps_per_stage": dps, "laser_frac_achieved": laser_frac,
                    "natural_clear_rate": res["natural_clear_rate"], "damage_mean": res["damage_mean"],
                    "relay_reached_frac": res["relay_reached_frac"],
                    "relay_median_min": res["relay_median_min"], "relay_p90_min": res["relay_p90_min"],
                    "b3_mean_frac": res["b3_mean_frac"],
                    "b1_ref_drops_per_session": res["b1_ref"], "b2_ref_recovery_sec": res["b2_ref_sec"],
                    "weaken_uptime_ref": res["weaken_uptime_ref"],
                    "meets_natural_zero": verdict["meets_natural_zero"],
                    "meets_reached_full": verdict["meets_reached_full"],
                    "meets_median_23_24min": verdict["meets_median_23_24min"],
                    "meets_b3": verdict["meets_b3"], "meets_laser_band": verdict["meets_laser_band"],
                    "all_pass": verdict["all_pass"],
                }
                rows.append(row)
                idx += 1
                print(f"[sweep] dr={dr} wk={wk} lf={lf} laser_dps={dps:.3f}(ach={laser_frac:.3f}) "
                      f"natural={res['natural_clear_rate']:.4f} relay_med={res['relay_median_min']} "
                      f"B3={res['b3_mean_frac']:.3f} B2ref={res['b2_ref_sec']} ALL={verdict['all_pass']}")

    os.makedirs(args.out, exist_ok=True)
    path = f"{args.out}/item_compound_sweep.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for row in rows:
            w.writerow(row)
    print(f"RESULT: wrote {path} ({len(rows)} rows)")

    pass_rows = [r for r in rows if r["all_pass"]]
    print(f"RESULT: {len(pass_rows)}/{len(rows)} bundles satisfy all criteria")

    # 事前登録仮説の検証：relay_medianが報告08比+2分以上か
    medians = [r["relay_median_min"] for r in rows if r["relay_median_min"] is not None]
    if medians:
        mn, mx = min(medians), max(medians)
        uplift_min = mn - REPORT08_BEST_RELAY_MEDIAN_MIN
        uplift_max = mx - REPORT08_BEST_RELAY_MEDIAN_MIN
        threshold = REPORT08_BEST_RELAY_MEDIAN_MIN + PREREG_RELAY_UPLIFT_MIN
        n_above = sum(1 for m in medians if m >= threshold)
        print(f"RESULT: prereg-hypothesis relay_median range=[{mn:.2f},{mx:.2f}]min "
              f"uplift_vs_report08=[{uplift_min:+.2f},{uplift_max:+.2f}]min "
              f"({n_above}/{len(medians)} bundles >= +2min threshold {threshold:.2f}min)")

    meta = {
        "script": "sim/ring_structure/item_compound_sweep.py", "mode": "sweep",
        "requirement": REQUIREMENT, "weaken_levels": WEAKEN_LEVELS,
        "laser_frac_levels": LASER_FRAC_LEVELS, "laser_tol": LASER_TOL,
        "drop_rate_levels": DROP_RATE_LEVELS, "ball_effect_ticks_fixed": BALL_EFFECT_TICKS_FIXED,
        "pickup_miss_rate_base": PICKUP_MISS_RATE_DEFAULT, "item_wall_bricks": ITEM_WALL_BRICKS,
        "natural_trials": args.natural_trials, "relay_trials": args.relay_trials,
        "b3_trials": args.b3_trials, "pass_count": len(pass_rows),
        "report08_best_relay_median_min": REPORT08_BEST_RELAY_MEDIAN_MIN,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(f"{args.out}/item_compound_sweep_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"RESULT: wrote {args.out}/item_compound_sweep_meta.json")
    print("RESULT: PASS")


def run_robustness(args):
    _require_bundle(args)
    rows = []
    for i, miss in enumerate(ROBUSTNESS_MISS_RATES):
        seed_base = 12000 + i * 20
        bundle_no_laser = base_item_system(args.drop_rate, args.weaken, pickup_miss_rate_base=miss)
        # laser_dpsは束固有に再較正する（miss変更でbaselineが変わるため）
        dps, laser_frac, baseline = calibrate_laser_dps(
            seed_base, bundle_no_laser, args.laser_frac, args.calib_trials)
        item_system = dict(bundle_no_laser, laser_dps_per_stage=dps)
        res = measure_full(seed_base + 5, item_system,
                            args.natural_trials, args.relay_trials, args.b3_trials)
        verdict = evaluate_pass(res, laser_frac, args.laser_frac)
        rows.append({
            "pickup_miss_rate_base": miss, "drop_rate": args.drop_rate,
            "weaken_multiplier": args.weaken, "laser_frac_target": args.laser_frac,
            "laser_dps_per_stage": dps, "laser_frac_achieved": laser_frac,
            "natural_clear_rate": res["natural_clear_rate"], "damage_mean": res["damage_mean"],
            "relay_reached_frac": res["relay_reached_frac"], "relay_median_min": res["relay_median_min"],
            "b3_mean_frac": res["b3_mean_frac"], "b2_ref_recovery_sec": res["b2_ref_sec"],
            "all_pass": verdict["all_pass"],
        })
        print(f"[robustness] miss={miss} natural={res['natural_clear_rate']:.4f} "
              f"relay_med={res['relay_median_min']} B3={res['b3_mean_frac']:.3f} ALL={verdict['all_pass']}")

    os.makedirs(args.out, exist_ok=True)
    path = f"{args.out}/item_compound_robustness.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for row in rows:
            w.writerow(row)
    print(f"RESULT: wrote {path} ({len(rows)} rows)")
    robust = all(r["all_pass"] for r in rows)
    print(f"RESULT: robustness {'PASS' if robust else 'FAIL'} "
          f"({sum(1 for r in rows if r['all_pass'])}/{len(rows)} 水準で合格維持)")
    print("RESULT: PASS")


def run_final(args):
    _require_bundle(args)
    seed_base = 14000
    bundle_no_laser = base_item_system(args.drop_rate, args.weaken)
    dps, laser_frac, baseline = calibrate_laser_dps(
        seed_base, bundle_no_laser, args.laser_frac, args.calib_trials)
    item_system = dict(bundle_no_laser, laser_dps_per_stage=dps)
    res = measure_full(seed_base + 5, item_system, args.natural_trials, args.relay_trials, args.b3_trials)
    verdict = evaluate_pass(res, laser_frac, args.laser_frac)
    print(f"[final] dr={args.drop_rate} wk={args.weaken} lf={args.laser_frac} laser_dps={dps:.3f}")
    print(f"[final] natural={res['natural_clear_rate']:.4f}({'OK' if verdict['meets_natural_zero'] else 'NG'}) "
          f"relay_med={res['relay_median_min']}min({'OK' if verdict['meets_median_23_24min'] else 'NG'}) "
          f"reached={res['relay_reached_frac']:.3f} B3={res['b3_mean_frac']:.3f}({'OK' if verdict['meets_b3'] else 'NG'})")
    print(f"[final] 全基準合格: {'PASS' if verdict['all_pass'] else 'FAIL'}")

    os.makedirs(args.out, exist_ok=True)
    path = f"{args.out}/item_compound_final.csv"
    row = dict(drop_rate=args.drop_rate, weaken_multiplier=args.weaken,
               laser_frac_target=args.laser_frac, laser_dps_per_stage=dps,
               laser_frac_achieved=laser_frac, **{k: res[k] for k in res}, **verdict)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        w.writeheader()
        w.writerow(row)
    print(f"RESULT: wrote {path} (1 row)")
    print("RESULT: PASS")


def _require_bundle(args):
    if args.weaken is None or args.laser_frac is None or args.drop_rate is None:
        raise SystemExit("robustness/finalモードには --weaken --laser-frac --drop-rate が必須です")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["sweep", "robustness", "final"], required=True)
    ap.add_argument("--weaken", type=float, default=None)
    ap.add_argument("--laser-frac", type=float, default=None)
    ap.add_argument("--drop-rate", type=float, default=None)
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
