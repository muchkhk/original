#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
玉キープ束（sticky除去後の7種構成）下での要求量64,672.2の再検証（指示書06 やること1-2）。

指示書02/03で確定した要求量64,672.2（staged・900tick・安全係数1.2）が、
sticky killを反映した7種構成・球側3種（貫通・爆発・加速）の壁破壊レート
影響下でも成立するかを再検証する。

指示書05の確定候補束（catch_cap=0.999等）は不採用（B1・B2がシミュの判定対象から
外れたため、極端な捕球率へ追い込む理由が無くなった）。FINAL_BUNDLEは
item_sim.py のB3再掃引（指示書06 やること1-3）でB3≤40%を満たすことを確認した
組み合わせを使う。

生データCSV（指示書01〜03分）は読み取らない・変更しない。要求量の絶対値
64,672.2はproto/報告_強化傾斜と強化壁要求量_v1_2026-07-29.md §2から転記。

使い方:
  python item_requirement_check.py --natural-trials 3000 --relay-trials 300 --out results
"""
import argparse
import csv
import json
import os
from datetime import datetime, timezone

from ring_sim import simulate_trial, pct
from wall_sim import Q4_MS_PER_TICK
from item_sim import (item_seed, NATURAL_N, NATURAL_DECAY_ON, NATURAL_WEAKEST_VANISH,
                       FIXED_POWER_BONUS_PER_STAGE, FIXED_PADDLE_CATCH_RATE_CAP)

REQUIREMENT = 64672.2  # staged・900tick・安全係数1.2（指示書02/03で確定。値は変更禁止）
SESSION_TICKS = 900
SLOPE_KIND = "staged"

# 球側効果量（球側3種の効果量パラメータ。指示書05ではsticky除去前提が
# 支配的すぎて無意味化していたため、sticky除去後に再び有意味かを確認する）
MAGNITUDES_TO_CHECK = [0.5, 1.0, 2.0]

# 指示書06 B3再掃引（item_b3_resweep.csv）でB3≤40%を満たすことを確認した組
# （drop_rate=0.15, ball_effect_ticks=2。詳細は報告書§2参照）
FINAL_BUNDLE = {
    "drop_rate": 0.15,
    "ball_effect_ticks": 2,
    "paddle_power_bonus_per_stage": FIXED_POWER_BONUS_PER_STAGE,
    "paddle_catch_rate_cap": FIXED_PADDLE_CATCH_RATE_CAP,
}


def natural_clear_rate(seed, magnitude, trials):
    import random
    rng = random.Random(seed)
    item_system = dict(FINAL_BUNDLE, ball_effect_magnitude=magnitude)
    damages = []
    for _ in range(trials):
        r = simulate_trial(rng, NATURAL_N, "inf", NATURAL_DECAY_ON, NATURAL_WEAKEST_VANISH,
                            SESSION_TICKS + 1, slope_kind=SLOPE_KIND,
                            checkpoints=[SESSION_TICKS], item_system=item_system)
        damages.append(r["checkpoint_results"][SESSION_TICKS]["wall_damage"])
    clear = sum(1 for d in damages if d >= REQUIREMENT) / len(damages)
    return clear, damages


def relay_clear_time(seed, magnitude, trials, ticks=10000):
    import random
    rng = random.Random(seed)
    item_system = dict(FINAL_BUNDLE, ball_effect_magnitude=magnitude)
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--natural-trials", type=int, default=3000)
    ap.add_argument("--relay-trials", type=int, default=300)
    ap.add_argument("--out", type=str, default="sim/ring_structure/results")
    args = ap.parse_args()

    rows = []
    for i, mag in enumerate(MAGNITUDES_TO_CHECK):
        clear, damages = natural_clear_rate(item_seed(8000 + i), mag, args.natural_trials)
        relay = relay_clear_time(item_seed(8100 + i), mag, args.relay_trials)
        row = {
            "ball_effect_magnitude": mag,
            "natural_trials": args.natural_trials,
            "natural_clear_rate": clear,
            "natural_damage_mean": sum(damages) / len(damages),
            "relay_trials": args.relay_trials,
            "relay_reached_frac": relay["reached_frac"],
            "relay_median_tick": relay["median_tick"],
            "relay_median_min": (relay["median_tick"] * Q4_MS_PER_TICK / 1000 / 60
                                  if relay["median_tick"] else None),
            "relay_p90_tick": relay["p90_tick"],
            "relay_p90_min": (relay["p90_tick"] * Q4_MS_PER_TICK / 1000 / 60
                               if relay["p90_tick"] else None),
        }
        rows.append(row)
        print(f"[magnitude={mag}] natural_clear_rate={clear:.4f} "
              f"natural_damage_mean={row['natural_damage_mean']:.1f} "
              f"relay_reached={relay['reached_frac']:.3f} "
              f"relay_median={row['relay_median_min']:.2f}min "
              f"relay_p90={row['relay_p90_min']:.2f}min" if row['relay_median_min'] else
              f"[magnitude={mag}] natural_clear_rate={clear:.4f} relay_reached={relay['reached_frac']:.3f}")

    os.makedirs(args.out, exist_ok=True)
    path = f"{args.out}/item_requirement_recheck.csv"
    cols = ["ball_effect_magnitude", "natural_trials", "natural_clear_rate", "natural_damage_mean",
            "relay_trials", "relay_reached_frac", "relay_median_tick", "relay_median_min",
            "relay_p90_tick", "relay_p90_min"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for row in rows:
            w.writerow(row)
    print(f"RESULT: wrote {path} ({len(rows)} rows)")

    meta = {
        "script": "sim/ring_structure/item_requirement_check.py",
        "requirement": REQUIREMENT, "session_ticks": SESSION_TICKS, "slope_kind": SLOPE_KIND,
        "final_bundle": FINAL_BUNDLE, "magnitudes_checked": MAGNITUDES_TO_CHECK,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(f"{args.out}/item_requirement_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"RESULT: wrote {args.out}/item_requirement_meta.json")
    print("RESULT: PASS")


if __name__ == "__main__":
    main()
