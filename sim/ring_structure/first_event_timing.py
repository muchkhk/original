#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
初回越境球到来時刻・初回アイテムドロップ時刻の計測（指示書11 作業2）

体験原則（設計チャット23 #5・逐語）：
  「ブロック崩し自体はありふれてて、プレイヤーはすぐ飽きる。ただのブロック崩しを
  する時間はできるだけ少なくして、PL同士の相互干渉に気がついて面白い！となってから、
  それが消えるまでにゲームが終わるようにしたい」

この「相互干渉に気がつくまでの時間」を観測量化するため、確定束（要求量68,700。
drop_rate=0.05／weaken_multiplier=1.3／laser配分目標5%／ball_effect_ticks=2／
貫通=3ヒット固定／アイテム壁=各陣2個）で、以下2つの分布を計測する。判定はしない
（分布の報告のみ。閾値設定・良否判断は設計チャット預かり）：

  1. 初回越境球到来時刻：各worldについて、他worldからの越境球（上昇跨ぎ・落下跨ぎの
     いずれか）が最初に流入したtick（セッション開始tick=0基準）。world別ではなく
     全world・全試行を合算した分布として中央値・p90・最大値を報告する。
  2. 初回アイテムドロップ時刻（参考記録）：各worldについて、アイテムが最初にドロップ
     した（ランダム落下・アイテム壁いずれか早い方）tick。同じ分布形式で報告する。

【非干渉】ring_sim.simulate_trial()にrecord_first_events引数を追加したが、これは
既に発生済みのイベント（跨ぎ・ドロップ）を記録するだけで、新規のrng呼び出しは
一切追加していない。そのためrecord_first_events=True/Falseで力学・乱数消費順は
変化しない。本スクリプトはこれを2通りの方法で実証する（§verify_non_interference）：
  (a) 報告10のnatural計測（item_seed(20010)・2000試行・要求量68,700点）を
      record_first_events=Falseのまま再現し、damage_meanが報告10の実測値
      60,804.18（表示60,804.2）と一致することを確認する
  (b) 同一seedでrecord_first_eventsをFalse/True双方に切り替えて同数試行を実行し、
      各試行のdamage（checkpoint wall_damage）が要素ごとに完全一致することを確認する

使い方:
  python first_event_timing.py --out results
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
                       NATURAL_SERVE_MODE, SESSION_TICKS)
from item_requirement_check import SLOPE_KIND
from item_requirement_rederive_sweep import base_item_system, FIXED_LASER_TARGET_FRAC
from item_compound_sweep import calibrate_laser_dps

# 本指示書（11）専用のseedオフセット。指示書10（20000/21000台）と衝突しないよう
# 40000台を新設する。
REQUIREMENT_TRIALS = 500
REPRO_SEED_BASE = 20010          # 報告10のrequirement=68700点と同一（item_seed(20010)）
REPRO_NATURAL_TRIALS = 2000      # 同上（args.natural_trials既定値と同一）
REPORT10_DAMAGE_MEAN = 60804.18  # 報告10 §3-4実測値（表示は60,804.2）
AB_CHECK_SEED = 39010
AB_CHECK_TRIALS = 200
MEASURE_SEED = 40010


def ticks_to_min(t):
    if t is None:
        return None
    return t * Q4_MS_PER_TICK / 1000 / 60


def build_confirmed_bundle(calib_trials=200):
    """確定束（要求量以外は固定・報告10と同一）のitem_systemを組み立てる。
    laser較正も報告10と同一手順（seed=5000）で行う。"""
    bundle_no_laser = base_item_system()
    dps, laser_frac, baseline_mean = calibrate_laser_dps(
        5000, bundle_no_laser, FIXED_LASER_TARGET_FRAC, calib_trials)
    item_system = dict(bundle_no_laser, laser_dps_per_stage=dps)
    return item_system, dps, laser_frac, baseline_mean


def run_natural_with_damage(seed, item_system, trials, record_first_events=False):
    """measure_natural_damage()と同一の呼び出し形（ticks=SESSION_TICKS+1・
    checkpoints=[SESSION_TICKS]・slope_kind=SLOPE_KIND）に、record_first_eventsを
    追加しただけの読み取り専用ラッパー。damage（checkpoint wall_damage）は
    record_first_eventsの値に関わらず同一のはず（本スクリプトの検証対象そのもの）。"""
    rng = random.Random(seed)
    damages = []
    cross_ticks = []
    drop_ticks = []
    for _ in range(trials):
        r = simulate_trial(rng, NATURAL_N, NATURAL_SERVE_MODE, NATURAL_DECAY_ON,
                            NATURAL_WEAKEST_VANISH, SESSION_TICKS + 1, slope_kind=SLOPE_KIND,
                            checkpoints=[SESSION_TICKS], item_system=item_system,
                            record_first_events=record_first_events)
        damages.append(r["checkpoint_results"][SESSION_TICKS]["wall_damage"])
        if record_first_events:
            for w in range(NATURAL_N):
                ct = r["first_cross_tick_per_world"][w]
                if ct is not None:
                    cross_ticks.append(ct)
                dt = r["first_item_drop_tick_per_world"][w]
                if dt is not None:
                    drop_ticks.append(dt)
    return damages, cross_ticks, drop_ticks


def verify_non_interference(item_system):
    """(a) 報告10の再現、(b) 同一seedでのFalse/True要素ごと一致、の2通りで
    record_first_eventsが力学・乱数消費順を変えていないことを実証する。"""
    # (a) 報告10 requirement=68700点の再現（record_first_events=False・既定動作の再確認）
    repro_damages, _, _ = run_natural_with_damage(
        item_seed(REPRO_SEED_BASE), item_system, REPRO_NATURAL_TRIALS,
        record_first_events=False)
    repro_mean = statistics.mean(repro_damages)
    repro_diff_pct = abs(repro_mean - REPORT10_DAMAGE_MEAN) / REPORT10_DAMAGE_MEAN * 100

    # (b) 同一seed・同一item_systemで record_first_events=False/True を切り替え、
    #     試行ごとのdamageが要素ごとに完全一致するかを確認する。
    damages_off, _, _ = run_natural_with_damage(
        item_seed(AB_CHECK_SEED), item_system, AB_CHECK_TRIALS, record_first_events=False)
    damages_on, cross_ab, drop_ab = run_natural_with_damage(
        item_seed(AB_CHECK_SEED), item_system, AB_CHECK_TRIALS, record_first_events=True)
    elementwise_identical = damages_off == damages_on

    return {
        "repro_seed_base": REPRO_SEED_BASE, "repro_natural_trials": REPRO_NATURAL_TRIALS,
        "repro_damage_mean": repro_mean, "report10_damage_mean": REPORT10_DAMAGE_MEAN,
        "repro_diff_pct": repro_diff_pct, "repro_within_0.1pct": repro_diff_pct <= 0.1,
        "ab_check_seed": AB_CHECK_SEED, "ab_check_trials": AB_CHECK_TRIALS,
        "ab_elementwise_identical": elementwise_identical,
        "ab_damage_mean_off": statistics.mean(damages_off),
        "ab_damage_mean_on": statistics.mean(damages_on),
    }


def summarize_distribution(vals):
    if not vals:
        return {"n": 0, "median_tick": None, "p90_tick": None, "max_tick": None,
                "median_min": None, "p90_min": None, "max_min": None}
    vs = sorted(vals)
    median_t, p90_t, max_t = pct(vs, 0.5), pct(vs, 0.9), vs[-1]
    return {
        "n": len(vs), "median_tick": median_t, "p90_tick": p90_t, "max_tick": max_t,
        "median_min": ticks_to_min(median_t), "p90_min": ticks_to_min(p90_t),
        "max_min": ticks_to_min(max_t),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=REQUIREMENT_TRIALS)
    ap.add_argument("--calib-trials", type=int, default=200)
    ap.add_argument("--out", type=str, default="results")
    args = ap.parse_args()

    item_system, dps, laser_frac, baseline_mean = build_confirmed_bundle(args.calib_trials)
    print(f"[calib] laser_dps_per_stage={dps:.4f} achieved_frac={laser_frac:.4f} "
          f"baseline_mean(no laser)={baseline_mean:.1f}")

    interference = verify_non_interference(item_system)
    print(f"[non-interference a] repro damage_mean={interference['repro_damage_mean']:.2f} "
          f"vs report10={interference['report10_damage_mean']:.2f} "
          f"diff={interference['repro_diff_pct']:.4f}% "
          f"within_0.1pct={interference['repro_within_0.1pct']}")
    print(f"[non-interference b] elementwise_identical(off vs on, same seed)="
          f"{interference['ab_elementwise_identical']} "
          f"(n={interference['ab_check_trials']}, "
          f"mean_off={interference['ab_damage_mean_off']:.2f}, "
          f"mean_on={interference['ab_damage_mean_on']:.2f})")

    # 本測定：500試行、確定束、record_first_events=True
    damages_500, cross_ticks, drop_ticks = run_natural_with_damage(
        item_seed(MEASURE_SEED), item_system, args.trials, record_first_events=True)
    damage_mean_500 = statistics.mean(damages_500)

    cross_dist = summarize_distribution(cross_ticks)
    drop_dist = summarize_distribution(drop_ticks)

    print(f"[measure] trials={args.trials} damage_mean={damage_mean_500:.2f} "
          f"(参考値。500試行の標本平均であり、報告10の2000試行平均60,804.18とは "
          f"別サンプルのため±0.1%一致は要求しない。非干渉の実証は上記[non-interference]を正とする)")
    print(f"[measure] first_cross: n={cross_dist['n']} "
          f"median={cross_dist['median_tick']}tick({cross_dist['median_min']:.3f}min) "
          f"p90={cross_dist['p90_tick']}tick({cross_dist['p90_min']:.3f}min) "
          f"max={cross_dist['max_tick']}tick({cross_dist['max_min']:.3f}min)")
    print(f"[measure] first_item_drop: n={drop_dist['n']} "
          f"median={drop_dist['median_tick']}tick({drop_dist['median_min']:.3f}min) "
          f"p90={drop_dist['p90_tick']}tick({drop_dist['p90_min']:.3f}min) "
          f"max={drop_dist['max_tick']}tick({drop_dist['max_min']:.3f}min)")

    os.makedirs(args.out, exist_ok=True)

    # 生データ（tick単位。pooled = 全world・全試行を合算した1列）
    path = f"{args.out}/first_event_timing.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["event", "tick"])
        for t in cross_ticks:
            w.writerow(["first_cross", t])
        for t in drop_ticks:
            w.writerow(["first_item_drop", t])
    print(f"RESULT: wrote {path} ({len(cross_ticks) + len(drop_ticks)} rows)")

    meta = {
        "script": "sim/ring_structure/first_event_timing.py",
        "requirement": 68700,
        "fixed_bundle": {
            "drop_rate": item_system["drop_rate"],
            "weaken_multiplier": item_system["weaken_multiplier"],
            "laser_target_frac": FIXED_LASER_TARGET_FRAC,
            "laser_dps_per_stage": dps, "laser_frac_achieved": laser_frac,
            "ball_effect_ticks": item_system["ball_effect_ticks"], "pierce_hits": 3,
            "item_wall_bricks": item_system["item_wall_bricks"],
            "pickup_miss_rate_base": item_system["pickup_miss_rate_base"],
        },
        "N": NATURAL_N, "session_ticks": SESSION_TICKS,
        "trials": args.trials, "measure_seed_base": MEASURE_SEED,
        "ms_per_tick": Q4_MS_PER_TICK,
        "damage_mean_500trials_REFERENCE_ONLY": damage_mean_500,
        "non_interference": interference,
        "first_cross_tick_distribution": cross_dist,
        "first_item_drop_tick_distribution": drop_dist,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    meta_path = f"{args.out}/first_event_timing_meta.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"RESULT: wrote {meta_path}")

    if not interference["ab_elementwise_identical"]:
        print("RESULT: FAIL (non-interference broken: record_first_events changed damage)")
        raise SystemExit(1)
    if not interference["repro_within_0.1pct"]:
        print("RESULT: FAIL (repro damage_mean diverged from report10 by >0.1%)")
        raise SystemExit(1)
    print("RESULT: PASS")


if __name__ == "__main__":
    main()
