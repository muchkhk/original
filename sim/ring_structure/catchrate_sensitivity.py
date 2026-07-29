#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
リレー成立性のcatch_rate感度検証（指示書03）。

wall_sim.py の run_relay() は、これまで paddle_catch_rate=1.0（球を絶対に落とさない
理想協調プレイ）を「意図的リレー」の代理条件としていた。これは実卓のプレイヤーが
出せる上限に近い、楽観的な推定値である。指示書03は、実卓相当のcatch_rate
（0.85/0.90/0.95）でもこの上限推定から大きく崩れないかを検証する。

要求量の絶対値は、指示書02で既に計測済みの自然プレイ分布（staged, checkpoint=900,
percentile=0.5, serve_mode=inf; sim/ring_structure/results/wall_q3_requirements_fine.csv）
から**読み取るだけ**とし、新たに自然プレイのシミュレーションは実行しない
（指示書03の「触ってはいけないもの」＝指示書01・02の生データCSVは読み取りのみ、を
そのままrequirementの導出根拠にも適用した）。

使い方:
  python catchrate_sensitivity.py --trials 300 --ticks 3000 --out results
"""
import argparse
import csv
import json
import os
from datetime import datetime, timezone

from wall_sim import run_relay, wall_seed

SLOPE_KIND = "staged"
CHECKPOINT = 900
SAFETY_FACTORS = [1.1, 1.2]
CATCH_RATES = [0.85, 0.90, 0.95]
SOURCE_CSV = "sim/ring_structure/results/wall_q3_requirements_fine.csv"


def load_base_damage(source_csv, slope_kind, checkpoint, percentile=0.5):
    with open(source_csv, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if (row["slope_kind"] == slope_kind
                    and int(row["checkpoint_ticks"]) == checkpoint
                    and abs(float(row["percentile"]) - percentile) < 1e-9
                    and abs(float(row["safety_factor"]) - 1.0) < 1e-9):
                return float(row["base_damage"])
    raise ValueError(f"base_damage not found in {source_csv} for "
                      f"{slope_kind}/{checkpoint}/{percentile}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=300)
    ap.add_argument("--ticks", type=int, default=3000)
    ap.add_argument("--out", type=str, default="sim/ring_structure/results")
    args = ap.parse_args()

    base_damage = load_base_damage(SOURCE_CSV, SLOPE_KIND, CHECKPOINT)
    print(f"[source] base_damage(median, {SLOPE_KIND}/{CHECKPOINT}/serve=inf) "
          f"= {base_damage} (from {SOURCE_CSV}, read-only)")

    rows = []
    idx = 0
    for sf in SAFETY_FACTORS:
        requirement = base_damage * sf
        for cr in CATCH_RATES:
            summary = run_relay(wall_seed(2000 + idx), SLOPE_KIND, [requirement],
                                 args.trials, args.ticks, paddle_catch_rate=cr)
            s = summary[requirement]
            rows.append({
                "slope_kind": SLOPE_KIND, "checkpoint_ticks": CHECKPOINT,
                "safety_factor": sf, "requirement": requirement,
                "paddle_catch_rate": cr, "trials": args.trials, "ticks": args.ticks,
                "reached_frac": s["reached_frac"],
                "median_tick": s["median_tick"], "p90_tick": s["p90_tick"],
            })
            print(f"[catchrate] sf={sf} catch_rate={cr} -> "
                  f"reached_frac={s['reached_frac']:.3f} median={s['median_tick']} p90={s['p90_tick']}")
            idx += 1
        # 参考として、既存のcatch_rate=1.0（理想協調）も同じ乱数系列の枠組みで併記する
        summary_ideal = run_relay(wall_seed(2000 + idx), SLOPE_KIND, [requirement],
                                   args.trials, args.ticks, paddle_catch_rate=1.0)
        s = summary_ideal[requirement]
        rows.append({
            "slope_kind": SLOPE_KIND, "checkpoint_ticks": CHECKPOINT,
            "safety_factor": sf, "requirement": requirement,
            "paddle_catch_rate": 1.0, "trials": args.trials, "ticks": args.ticks,
            "reached_frac": s["reached_frac"],
            "median_tick": s["median_tick"], "p90_tick": s["p90_tick"],
        })
        print(f"[catchrate] sf={sf} catch_rate=1.0(参考) -> "
              f"reached_frac={s['reached_frac']:.3f} median={s['median_tick']} p90={s['p90_tick']}")
        idx += 1

    os.makedirs(args.out, exist_ok=True)
    path = f"{args.out}/wall_catchrate_sensitivity.csv"
    cols = ["slope_kind", "checkpoint_ticks", "safety_factor", "requirement",
            "paddle_catch_rate", "trials", "ticks", "reached_frac", "median_tick", "p90_tick"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for row in rows:
            w.writerow(row)
    print(f"RESULT: wrote {path} ({len(rows)} rows)")

    meta = {
        "script": "sim/ring_structure/catchrate_sensitivity.py",
        "source_csv": SOURCE_CSV, "base_damage": base_damage,
        "slope_kind": SLOPE_KIND, "checkpoint": CHECKPOINT,
        "safety_factors": SAFETY_FACTORS, "catch_rates": CATCH_RATES,
        "trials": args.trials, "ticks": args.ticks,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(f"{args.out}/wall_catchrate_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"RESULT: wrote {args.out}/wall_catchrate_meta.json")
    print("RESULT: PASS")


if __name__ == "__main__":
    main()
