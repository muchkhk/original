#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
玉キープ束（アイテム）のB3再掃引（指示書06。指示書05からの改訂）

sim/ring_structure/ring_sim.py の simulate_trial()（既存モデル）に指示書05で
追加した item_system 引数を使う。新しい物理モデルはここでは作らない
（既存モデルの拡張のみ）。

【指示書06での器具再割当（設計チャット20）】
B1（落球頻度）・B2（復帰期待時間）・B5（スティッキー保持）は、このシミュでは
パドル試行のペース（実時間との対応）を表現できないため、判定器具から外れた。
最終確認は較正用プロトタイプ（`proto/ring_keep_calibration.html`）とプレイテストで行う。
このスクリプトは **B3（球側効果の稼働率）のみを判定**し、B1・B2は参考情報として
値だけを記録する（PASS/FAIL形式では出力しない。実卓と乖離した器具の判定は
偽の確信になるため＝指示書06完了条件1）。

体験帯（凍結・設計チャット20。数値そのものは変更禁止）：
  B1 落球頻度：1人1セッション（900tick）あたり2〜10回　※参考値のみ。判定はプロト側
  B2 復帰期待時間：パドル側1段喪失から同段回復まで実時間45〜120秒　※参考値のみ
  B3 球側効果の稼働率：セッションの40%以下　※このスクリプトで判定
  B4 パドル側の段数：各アイテム3段（固定・探索対象外）
  B5 スティッキー保持上限：対象（sticky）がkillされ消滅
  衝突時優先順位：B1 > B3（B1はもはやこのスクリプトの判定対象ではないが、事前登録は維持）

Q4較正（1394.9333ms/tick、指示書03で確定）を使い、B2の参考値をtick↔秒変換する。
"""
import argparse
import csv
import json
import os
import random
import statistics
from datetime import datetime, timezone

from ring_sim import simulate_trial, pct, MASTER_SEED
from wall_sim import Q4_MS_PER_TICK

ITEM_MASTER_SEED = MASTER_SEED + 2  # 指示書02(+1)と衝突しないよう+2
ITEM_SEED_PRIME = 1_000_037

SESSION_TICKS = 900  # 「1セッション相当」確定値（指示書03 Q4較正・900tick≈21分）
NATURAL_N = 4
NATURAL_DECAY_ON = True
NATURAL_WEAKEST_VANISH = False
NATURAL_SERVE_MODE = "inf"

# B3のみ判定対象。B1・B2は参考値として記録するのみ（PASS/FAIL出力なし）
B3_MAX_FRAC = 0.40

# パドル側の経済性（power_bonus・catch_cap）は、B1・B2がシミュの判定対象から
# 外れたため、このスクリプトでは特定の値へ追い込む理由が無い。デフォルト値を
# 固定して使う（B3の判定に対する影響は間接的・小さい）。
FIXED_POWER_BONUS_PER_STAGE = 0.3
FIXED_PADDLE_CATCH_RATE_CAP = 0.999


def item_seed(index):
    return ITEM_MASTER_SEED + index * ITEM_SEED_PRIME


def run_bundle(seed, item_system, trials, ticks=SESSION_TICKS, N=NATURAL_N,
               serve_mode=NATURAL_SERVE_MODE):
    """1束（パラメータ組）を trials 回実行し、B1〜B3の生データを世界ごとに集計する。"""
    rng = random.Random(seed)
    drop_counts = []      # 世界×試行 のフラットなリスト（B1・参考値）
    recovery_ticks = []   # 世界×試行 のフラットなリスト（B2・参考値）
    uptime_fracs = []     # 世界×試行 のフラットなリスト（B3）

    for _ in range(trials):
        r = simulate_trial(rng, N, serve_mode, NATURAL_DECAY_ON, NATURAL_WEAKEST_VANISH,
                            ticks, item_system=item_system)
        drop_counts.extend(r["drop_events_per_world"])
        for w in range(N):
            recovery_ticks.extend(r["recovery_times_per_world"][w])
        uptime_fracs.extend(r["ball_effect_active_frac_per_world"])

    return {
        "drop_counts": drop_counts,
        "recovery_ticks": recovery_ticks,
        "uptime_fracs": uptime_fracs,
        "trials": trials, "ticks": ticks, "N": N,
    }


def summarize_bundle(raw):
    drop_mean = statistics.mean(raw["drop_counts"]) if raw["drop_counts"] else None
    recovery_mean = statistics.mean(raw["recovery_ticks"]) if raw["recovery_ticks"] else None
    uptime_mean = statistics.mean(raw["uptime_fracs"]) if raw["uptime_fracs"] else None
    return {
        "b1_drop_mean_REFERENCE_ONLY": drop_mean,
        "b2_recovery_mean_sec_REFERENCE_ONLY": (
            recovery_mean * Q4_MS_PER_TICK / 1000) if recovery_mean else None,
        "b2_recovery_sample_n": len(raw["recovery_ticks"]),
        "b3_uptime_mean_frac": uptime_mean,
    }


def evaluate_b3(summary):
    """B3（球側効果稼働率）だけを判定する。B1・B2はこのスクリプトの判定対象外
    （指示書06：器具再割当。参考値はsummaryに残すが、pass/failは付けない）。"""
    b3v = summary["b3_uptime_mean_frac"]
    b3_pass = b3v is not None and b3v <= B3_MAX_FRAC
    b3_margin = (b3v / B3_MAX_FRAC - 1.0) if b3v is not None else None  # 0=境界、負=余裕、正=超過
    return {"pass": b3_pass, "value": b3v, "range": (0, B3_MAX_FRAC), "margin": b3_margin}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=800)
    ap.add_argument("--out", type=str, default="sim/ring_structure/results")
    args = ap.parse_args()

    # 指示書06 やること1-3：7種均等抽選・ball_effect_ticks∈{2,3,4,5,6}でB3の成立域を更新
    drop_rates = [0.05, 0.10, 0.15, 0.20, 0.30]
    ball_effect_ticks_list = [2, 3, 4, 5, 6]
    rows = []
    idx = 0
    for dr in drop_rates:
        for bet in ball_effect_ticks_list:
            item_system = {
                "drop_rate": dr, "ball_effect_ticks": bet, "ball_effect_magnitude": 1.0,
                "paddle_power_bonus_per_stage": FIXED_POWER_BONUS_PER_STAGE,
                "paddle_catch_rate_cap": FIXED_PADDLE_CATCH_RATE_CAP,
            }
            raw = run_bundle(item_seed(idx), item_system, args.trials)
            summary = summarize_bundle(raw)
            b3 = evaluate_b3(summary)
            rows.append({
                "drop_rate": dr, "ball_effect_ticks": bet,
                "b3_mean_frac": summary["b3_uptime_mean_frac"],
                "b3_pass": b3["pass"], "b3_margin": b3["margin"],
                "b1_drop_mean_reference_only": summary["b1_drop_mean_REFERENCE_ONLY"],
                "b2_recovery_sec_reference_only": summary["b2_recovery_mean_sec_REFERENCE_ONLY"],
            })
            idx += 1
            print(f"[B3sweep] drop_rate={dr} ball_effect_ticks={bet} "
                  f"B3={summary['b3_uptime_mean_frac']:.3f}({'OK' if b3['pass'] else 'NG'}) "
                  f"[参考]B1={summary['b1_drop_mean_REFERENCE_ONLY']:.2f}回 "
                  f"B2={summary['b2_recovery_mean_sec_REFERENCE_ONLY']}")

    os.makedirs(args.out, exist_ok=True)
    path = f"{args.out}/item_b3_resweep.csv"
    cols = ["drop_rate", "ball_effect_ticks", "b3_mean_frac", "b3_pass", "b3_margin",
            "b1_drop_mean_reference_only", "b2_recovery_sec_reference_only"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for row in rows:
            w.writerow(row)
    print(f"RESULT: wrote {path} ({len(rows)} rows)")

    b3_pass_rows = [r for r in rows if r["b3_pass"]]
    print(f"RESULT: B3 pass: {len(b3_pass_rows)}/{len(rows)} combos")

    meta = {
        "script": "sim/ring_structure/item_sim.py",
        "note": "指示書06でB1/B2/B5をシミュの判定対象から除外し、B3専用の再掃引に改訂した",
        "item_master_seed": ITEM_MASTER_SEED,
        "session_ticks": SESSION_TICKS,
        "trials_per_combo": args.trials,
        "b3_max_frac": B3_MAX_FRAC,
        "drop_rates_swept": drop_rates,
        "ball_effect_ticks_swept": ball_effect_ticks_list,
        "fixed_power_bonus_per_stage": FIXED_POWER_BONUS_PER_STAGE,
        "fixed_paddle_catch_rate_cap": FIXED_PADDLE_CATCH_RATE_CAP,
        "b3_pass_count": len(b3_pass_rows),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(f"{args.out}/item_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"RESULT: wrote {args.out}/item_meta.json")
    print("RESULT: PASS")


if __name__ == "__main__":
    main()
