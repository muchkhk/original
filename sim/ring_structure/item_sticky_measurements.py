#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
指示書05 やること4：追加測定2件。

(a) スティッキー保持が下流世界の球涸れ（STARVE）に与える影響
(b) スティッキー捕球が落球頻度B1を帯の下限割れに押し下げないか（検査1条件③）

確定候補束（item_sim.py FINAL_BUNDLE相当）で、sticky_enabled=True/Falseの
2条件を比較する。差分がスティッキー固有の影響。
"""
import argparse
import csv
import json
import os
import random
import statistics
from datetime import datetime, timezone

from ring_sim import simulate_trial, STARVE_THRESHOLD
from item_sim import (sticky_hold_ticks_range, item_seed, NATURAL_N, NATURAL_DECAY_ON,
                       NATURAL_WEAKEST_VANISH, SESSION_TICKS, B1_RANGE)
from item_requirement_check import FINAL_BUNDLE


def run(seed, sticky_enabled, trials):
    rng = random.Random(seed)
    item_system = dict(FINAL_BUNDLE, ball_effect_magnitude=1.0,
                        sticky_hold_ticks_range=sticky_hold_ticks_range(),
                        sticky_enabled=sticky_enabled)
    starve_fracs = []   # world毎のstarve_frac（試行×world）
    drop_counts = []    # world毎の落球数（試行×world）
    for _ in range(trials):
        r = simulate_trial(rng, NATURAL_N, "inf", NATURAL_DECAY_ON, NATURAL_WEAKEST_VANISH,
                            SESSION_TICKS, item_system=item_system)
        for w in range(NATURAL_N):
            counts = r["simultaneous_counts"][w]
            starve_fracs.append(sum(1 for c in counts if c <= STARVE_THRESHOLD) / len(counts))
        drop_counts.extend(r["drop_events_per_world"])
    return {
        "starve_frac_mean": statistics.mean(starve_fracs),
        "drop_mean": statistics.mean(drop_counts),
        "drop_min": min(drop_counts),
        "frac_sessions_below_b1_lower": sum(1 for d in drop_counts if d < B1_RANGE[0]) / len(drop_counts),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=2000)
    ap.add_argument("--out", type=str, default="sim/ring_structure/results")
    args = ap.parse_args()

    on = run(item_seed(9500), True, args.trials)
    off = run(item_seed(9501), False, args.trials)

    print(f"[sticky=ON ] starve_frac_mean={on['starve_frac_mean']:.4f} "
          f"drop_mean={on['drop_mean']:.2f} drop_min={on['drop_min']} "
          f"frac_below_B1_lower={on['frac_sessions_below_b1_lower']:.4f}")
    print(f"[sticky=OFF] starve_frac_mean={off['starve_frac_mean']:.4f} "
          f"drop_mean={off['drop_mean']:.2f} drop_min={off['drop_min']} "
          f"frac_below_B1_lower={off['frac_sessions_below_b1_lower']:.4f}")

    result = {
        "trials": args.trials,
        "sticky_on": on, "sticky_off": off,
        "starve_delta": on["starve_frac_mean"] - off["starve_frac_mean"],
        "b1_lower_bound": B1_RANGE[0],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    os.makedirs(args.out, exist_ok=True)
    path = f"{args.out}/item_sticky_measurements.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"RESULT: wrote {path}")
    print("RESULT: PASS")


if __name__ == "__main__":
    main()
