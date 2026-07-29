#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
9種構成（指示書07）下での要求量64,672.2の再検証。

指示書02/03で確定した要求量64,672.2（staged・900tick・安全係数1.2）が、
指示書07のアイテムセット改訂（clone廃止・magnet/laser/weaken追加・9種均等抽選）
影響下でも成立するかを、掃引B（レーザー強度）→FINAL_BUNDLE確定→単発検証の
順で再検証する。要求量そのものは凍結値であり変更しない。

【掃引A→掃引Bの引き継ぎ】
掃引A（item_sim.py・B3再掃引）の結果、drop_rate=0.15, ball_effect_ticks=2が
B3=0.237（上限0.40に対し明確な余裕）で「有望束」として選ばれた（詳細は報告書§2）。
掃引Bはこの束を土台に、laser_dps_per_stageを3水準・weaken_multiplier=2.0固定で
振り、natural_clear_rate・damage_mean・relay系の反応を見る。

【laser_dps_per_stageの3水準（裁量・報告書に明記）】
事前のベースライン測定（drop_rate=0.15, ball_effect_ticks=2, laser無効時の
natural_damage_mean≈62,247）を基準に、全体破壊量への寄与が概ね5%/10%/20%程度に
なる桁を実測で探索し、0.3（+5.1%）・0.6（+10.3%）・1.2（+20.6%）を採用した。

生データCSV（指示書01〜03分）は読み取らない・変更しない。要求量の絶対値
64,672.2はproto/報告_強化傾斜と強化壁要求量_v1_2026-07-29.md §2から転記。

使い方:
  python item_requirement_check.py --mode sweepB --trials 300 --out results
  python item_requirement_check.py --mode final --natural-trials 3000 --relay-trials 300 --out results
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
                       FIXED_POWER_BONUS_PER_STAGE, FIXED_PADDLE_CATCH_RATE_CAP)

REQUIREMENT = 64672.2  # staged・900tick・安全係数1.2（指示書02/03で確定。値は変更禁止）
SESSION_TICKS = 900
SLOPE_KIND = "staged"

# 掃引A（item_sim.py）で選んだ有望束（B3=0.237、上限0.40に明確な余裕）
SWEEP_A_WINNER = {"drop_rate": 0.15, "ball_effect_ticks": 2}

# 掃引B：laser_dps_per_stageの3水準（寄与約5%/10%/20%。裁量・上記docstring参照）
LASER_DPS_LEVELS = [0.3, 0.6, 1.2]
WEAKEN_MULTIPLIER_FIXED = 2.0  # 掃引Bはweaken倍率を2.0固定（指示書07明記）
BALL_EFFECT_MAGNITUDE_FIXED = 1.0  # 指示書06で有意味化しなかったため既定値で固定

# FINAL_BUNDLE：掃引Bの実行結果、laser_dps_per_stage>0の3水準（0.3/0.6/1.2）は
# いずれも合格基準を満たさなかった（natural_clear_rateが0%から大きく外れ、
# relay_medianも23-24分の枠から下振れした。300試行）。laserがwall_damageへ
# 単調に加算される設計上、laser_dps_per_stageを上げるほど両基準からさらに
# 遠ざかる（natural_clear_rateは上振れ・relay_medianは下振れ）ため、掃引B内で
# パラメータをさらに探索しても改善しない。laser_dps_per_stage=0（無効）の
# 基準測定でもnatural_clear_rateは0%ではなく1.33%（n=300）で、relay_medianも
# 21.6分と目標23-24分を下回っていた。指示書07完了条件の指示
# 「基準を満たす束が存在しない場合はパラメータを弄り続けず、その事実と最も
# 近い束を報告して停止する」に従い、探索した中で最も基準に近いlaser無効
# （laser_dps_per_stage=0.0）をFINAL_BUNDLEとして報告する（詳細は報告書§3）。
FINAL_BUNDLE = dict(
    SWEEP_A_WINNER,
    ball_effect_magnitude=BALL_EFFECT_MAGNITUDE_FIXED,
    paddle_power_bonus_per_stage=FIXED_POWER_BONUS_PER_STAGE,
    paddle_catch_rate_cap=FIXED_PADDLE_CATCH_RATE_CAP,
    laser_dps_per_stage=0.0,
    weaken_multiplier=WEAKEN_MULTIPLIER_FIXED,
)


def natural_clear_rate(seed, item_system, trials):
    rng = random.Random(seed)
    damages = []
    for _ in range(trials):
        r = simulate_trial(rng, NATURAL_N, "inf", NATURAL_DECAY_ON, NATURAL_WEAKEST_VANISH,
                            SESSION_TICKS + 1, slope_kind=SLOPE_KIND,
                            checkpoints=[SESSION_TICKS], item_system=item_system)
        damages.append(r["checkpoint_results"][SESSION_TICKS]["wall_damage"])
    clear = sum(1 for d in damages if d >= REQUIREMENT) / len(damages)
    return clear, damages


def relay_clear_time(seed, item_system, trials, ticks=10000):
    rng = random.Random(seed)
    reach = []
    for _ in range(trials):
        r = simulate_trial(rng, NATURAL_N, "inf", NATURAL_DECAY_ON, NATURAL_WEAKEST_VANISH,
                            ticks, slope_kind=SLOPE_KIND, paddle_catch_rate=1.0,
                            wall_targets=[REQUIREMENT], item_system=item_system)
        t = r["wall_target_ticks"].get(REQUIREMENT)
        if t is not None:
            reach.append(t)
    return {
        "reached_frac": len(reach) / trials,
        "median_tick": pct(sorted(reach), 0.5) if reach else None,
        "p90_tick": pct(sorted(reach), 0.9) if reach else None,
    }


def run_sweep_b(args):
    rows = []
    for i, dps in enumerate(LASER_DPS_LEVELS):
        item_system = dict(
            SWEEP_A_WINNER,
            ball_effect_magnitude=BALL_EFFECT_MAGNITUDE_FIXED,
            paddle_power_bonus_per_stage=FIXED_POWER_BONUS_PER_STAGE,
            paddle_catch_rate_cap=FIXED_PADDLE_CATCH_RATE_CAP,
            laser_dps_per_stage=dps,
            weaken_multiplier=WEAKEN_MULTIPLIER_FIXED,
        )
        clear, damages = natural_clear_rate(item_seed(8200 + i), item_system, args.trials)
        relay = relay_clear_time(item_seed(8300 + i), item_system, max(30, args.trials // 3))
        row = {
            "laser_dps_per_stage": dps,
            "weaken_multiplier": WEAKEN_MULTIPLIER_FIXED,
            "natural_trials": args.trials,
            "natural_clear_rate": clear,
            "natural_damage_mean": statistics.mean(damages),
            "relay_reached_frac": relay["reached_frac"],
            "relay_median_tick": relay["median_tick"],
            "relay_median_min": (relay["median_tick"] * Q4_MS_PER_TICK / 1000 / 60
                                  if relay["median_tick"] else None),
            "relay_p90_tick": relay["p90_tick"],
            "relay_p90_min": (relay["p90_tick"] * Q4_MS_PER_TICK / 1000 / 60
                               if relay["p90_tick"] else None),
        }
        rows.append(row)
        print(f"[sweepB] laser_dps_per_stage={dps} natural_clear_rate={clear:.4f} "
              f"damage_mean={row['natural_damage_mean']:.1f} "
              f"relay_reached={relay['reached_frac']:.3f} "
              f"relay_median={row['relay_median_min']}")

    os.makedirs(args.out, exist_ok=True)
    path = f"{args.out}/item_sweepB_laser.csv"
    cols = ["laser_dps_per_stage", "weaken_multiplier", "natural_trials",
            "natural_clear_rate", "natural_damage_mean", "relay_reached_frac",
            "relay_median_tick", "relay_median_min", "relay_p90_tick", "relay_p90_min"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for row in rows:
            w.writerow(row)
    print(f"RESULT: wrote {path} ({len(rows)} rows)")
    print("RESULT: PASS")


def run_final(args):
    clear, damages = natural_clear_rate(item_seed(8400), FINAL_BUNDLE, args.natural_trials)
    relay = relay_clear_time(item_seed(8401), FINAL_BUNDLE, args.relay_trials)
    relay_median_min = relay["median_tick"] * Q4_MS_PER_TICK / 1000 / 60 if relay["median_tick"] else None
    relay_p90_min = relay["p90_tick"] * Q4_MS_PER_TICK / 1000 / 60 if relay["p90_tick"] else None

    # 完了条件（指示書07 作業4）：natural_clear_rate=0.0%維持、relay中央値23〜24分帯、
    # reached_frac=1.000。1つでも満たさなければ、その事実と最も近い束を報告して停止する
    # （数値束の組み直しは設計チャット側の判断）。
    meets_natural = clear == 0.0
    meets_reached = relay["reached_frac"] == 1.0
    meets_median = relay_median_min is not None and 23.0 <= relay_median_min <= 24.0
    all_pass = meets_natural and meets_reached and meets_median

    print(f"[final] FINAL_BUNDLE={FINAL_BUNDLE}")
    print(f"[final] natural_clear_rate={clear:.4f}({'OK' if meets_natural else 'NG'}) "
          f"damage_mean={statistics.mean(damages):.1f}")
    print(f"[final] relay_reached_frac={relay['reached_frac']:.4f}({'OK' if meets_reached else 'NG'}) "
          f"relay_median={relay_median_min}min({'OK' if meets_median else 'NG'}) "
          f"relay_p90={relay_p90_min}min")
    print(f"[final] 合格基準（natural=0%・reached=100%・median 23-24分）を全て満たすか: "
          f"{'PASS' if all_pass else 'FAIL（最も近い束として報告・停止）'}")

    os.makedirs(args.out, exist_ok=True)
    path = f"{args.out}/item_requirement_recheck.csv"
    row = {
        "laser_dps_per_stage": FINAL_BUNDLE["laser_dps_per_stage"],
        "weaken_multiplier": FINAL_BUNDLE["weaken_multiplier"],
        "ball_effect_magnitude": FINAL_BUNDLE["ball_effect_magnitude"],
        "natural_trials": args.natural_trials,
        "natural_clear_rate": clear,
        "natural_damage_mean": statistics.mean(damages),
        "relay_trials": args.relay_trials,
        "relay_reached_frac": relay["reached_frac"],
        "relay_median_tick": relay["median_tick"],
        "relay_median_min": relay_median_min,
        "relay_p90_tick": relay["p90_tick"],
        "relay_p90_min": relay_p90_min,
        "meets_natural_zero": meets_natural,
        "meets_reached_full": meets_reached,
        "meets_median_23_24min": meets_median,
        "all_criteria_pass": all_pass,
    }
    cols = list(row.keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerow(row)
    print(f"RESULT: wrote {path} (1 row)")

    meta = {
        "script": "sim/ring_structure/item_requirement_check.py",
        "requirement": REQUIREMENT, "session_ticks": SESSION_TICKS, "slope_kind": SLOPE_KIND,
        "sweep_a_winner": SWEEP_A_WINNER, "final_bundle": FINAL_BUNDLE,
        "laser_dps_levels_swept": LASER_DPS_LEVELS,
        "all_criteria_pass": all_pass,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(f"{args.out}/item_requirement_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"RESULT: wrote {args.out}/item_requirement_meta.json")
    print("RESULT: PASS")  # スクリプト自体は正常終了（合否判定はall_criteria_passで別掲）


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["sweepB", "final"], required=True)
    ap.add_argument("--trials", type=int, default=300, help="sweepB用の自然試行数")
    ap.add_argument("--natural-trials", type=int, default=3000, help="final用の自然試行数")
    ap.add_argument("--relay-trials", type=int, default=300, help="final用のリレー試行数")
    ap.add_argument("--out", type=str, default="sim/ring_structure/results")
    args = ap.parse_args()

    if args.mode == "sweepB":
        run_sweep_b(args)
    else:
        run_final(args)


if __name__ == "__main__":
    main()
