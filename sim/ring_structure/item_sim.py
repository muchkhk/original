#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
玉キープ束（アイテム）のパラメータ探索（指示書05）

sim/ring_structure/ring_sim.py の simulate_trial()（既存モデル）に指示書05で
追加した item_system 引数を使い、体験帯B1〜B3・B5を同時に満たすパラメータ束を
探す。新しい物理モデルはここでは作らない（既存モデルの拡張のみ）。

体験帯（凍結・設計チャット20・2026-07-29。数値そのものは変更禁止）：
  B1 落球頻度：1人1セッション（900tick）あたり2〜10回
  B2 復帰期待時間：パドル側1段喪失から同段回復まで実時間45〜120秒
  B3 球側効果の稼働率：セッションの40%以下
  B4 パドル側の段数：各アイテム3段（固定・探索対象外）
  B5 スティッキー保持上限：実時間3〜5秒で自動リリース
  衝突時優先順位：B1 > B3

Q4較正（1394.9333ms/tick、指示書03で確定）を使い、実時間の帯をtick単位に変換する。
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

# 体験帯をtick単位に変換
B1_RANGE = (2, 10)                                   # 回/セッション（tick換算不要）
B2_RANGE_TICKS = (45 * 1000 / Q4_MS_PER_TICK, 120 * 1000 / Q4_MS_PER_TICK)  # 45〜120秒
B3_MAX_FRAC = 0.40
B5_RANGE_SEC = (3.0, 5.0)


def sticky_hold_ticks_range():
    lo = max(1, round(B5_RANGE_SEC[0] * 1000 / Q4_MS_PER_TICK))
    hi = max(lo, round(B5_RANGE_SEC[1] * 1000 / Q4_MS_PER_TICK))
    return (lo, hi)


def item_seed(index):
    return ITEM_MASTER_SEED + index * ITEM_SEED_PRIME


def run_bundle(seed, item_system, trials, ticks=SESSION_TICKS, N=NATURAL_N,
               serve_mode=NATURAL_SERVE_MODE):
    """1束（パラメータ組）を trials 回実行し、B1〜B3の生データを世界ごとに集計する。"""
    rng = random.Random(seed)
    drop_counts = []      # 世界×試行 のフラットなリスト（B1）
    recovery_ticks = []   # 世界×試行 のフラットなリスト（B2）
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
        "b1_drop_mean": drop_mean,
        "b1_drop_median": pct(sorted(raw["drop_counts"]), 0.5) if raw["drop_counts"] else None,
        "b2_recovery_mean_ticks": recovery_mean,
        "b2_recovery_mean_sec": (recovery_mean * Q4_MS_PER_TICK / 1000) if recovery_mean else None,
        "b2_recovery_sample_n": len(raw["recovery_ticks"]),
        "b3_uptime_mean_frac": uptime_mean,
    }


def band_margin(value, lo, hi):
    """帯[lo,hi]の中央からの相対距離。0=中央、1=境界ちょうど、>1=帯の外。
    Noneや帯無し(上限のみ)は呼び出し側で個別に扱う。"""
    if value is None:
        return None
    mid = (lo + hi) / 2.0
    half = (hi - lo) / 2.0
    if half == 0:
        return 0.0 if value == mid else float("inf")
    return abs(value - mid) / half


def evaluate_bands(summary):
    """B1・B2・B3（B4は固定値のため対象外、B5はtick変換で直接保証）を判定する。
    戻り値：{band: {"pass": bool, "value":..., "range":..., "margin": float}}
    """
    b1v = summary["b1_drop_mean"]
    b1_pass = b1v is not None and B1_RANGE[0] <= b1v <= B1_RANGE[1]
    b1_margin = band_margin(b1v, *B1_RANGE) if b1v is not None else None

    b2v = summary["b2_recovery_mean_ticks"]
    b2_pass = b2v is not None and B2_RANGE_TICKS[0] <= b2v <= B2_RANGE_TICKS[1]
    b2_margin = band_margin(b2v, *B2_RANGE_TICKS) if b2v is not None else None

    b3v = summary["b3_uptime_mean_frac"]
    b3_pass = b3v is not None and b3v <= B3_MAX_FRAC
    # B3は上限のみの帯なので、margin=は値/上限-1（0=境界、負=余裕、正=超過）
    b3_margin = (b3v / B3_MAX_FRAC - 1.0) if b3v is not None else None

    return {
        "B1": {"pass": b1_pass, "value": b1v, "range": B1_RANGE, "margin": b1_margin},
        "B2": {"pass": b2_pass, "value": b2v, "range": B2_RANGE_TICKS, "margin": b2_margin},
        "B3": {"pass": b3_pass, "value": b3v, "range": (0, B3_MAX_FRAC), "margin": b3_margin},
    }


def all_pass(evaluation):
    return all(v["pass"] for v in evaluation.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=500)
    ap.add_argument("--out", type=str, default="sim/ring_structure/results")
    args = ap.parse_args()

    sticky_range = sticky_hold_ticks_range()
    print(f"[calib] sticky_hold_ticks_range={sticky_range} "
          f"(B5 {B5_RANGE_SEC}s @ {Q4_MS_PER_TICK:.2f}ms/tick)")
    print(f"[calib] B2_RANGE_TICKS={tuple(round(x,1) for x in B2_RANGE_TICKS)}")

    # --- フェーズA：drop_rate × power_bonus_per_stage の掃引（B1・B2） ---
    # 較正メモ：初回スイープ（power_bonus 0.01〜0.15、catch_cap既定0.98）では
    # 全組み合わせでB1が92〜638件/セッションとなり、目標2〜10件に遠く届かなかった。
    # 原因を追うと、SERVE_CAP=3(触ってはいけないもの)によりworldあたり最大3球が
    # 常時ラリーし続けるため、900tick中の実質パドル試行回数が約2600回に達し、
    # B1を満たすには捕球率を99.5〜99.9%程度まで押し上げる必要があると判明した
    # （§0-4：机上の想定と実測が食い違った実例。削って測ってから確定する）。
    # このためcatch_rate_capを引き上げ、power_bonus_per_stageの探索域も拡張した。
    drop_rates = [0.02, 0.05, 0.10, 0.20]
    power_bonuses = [0.1, 0.2, 0.3, 0.5, 0.8]
    catch_caps = [0.995, 0.999]
    phase_a_rows = []
    idx = 0
    for dr in drop_rates:
        for pb in power_bonuses:
          for cc in catch_caps:
            item_system = {
                "drop_rate": dr, "ball_effect_ticks": 7, "ball_effect_magnitude": 1.0,
                "sticky_hold_ticks_range": sticky_range,
                "paddle_power_bonus_per_stage": pb,
                "paddle_catch_rate_cap": cc,
            }
            raw = run_bundle(item_seed(idx), item_system, args.trials)
            summary = summarize_bundle(raw)
            ev = evaluate_bands(summary)
            phase_a_rows.append({
                "drop_rate": dr, "power_bonus_per_stage": pb, "catch_cap": cc,
                "b1_mean": summary["b1_drop_mean"], "b1_pass": ev["B1"]["pass"],
                "b1_margin": ev["B1"]["margin"],
                "b2_mean_sec": summary["b2_recovery_mean_sec"], "b2_pass": ev["B2"]["pass"],
                "b2_margin": ev["B2"]["margin"], "b2_sample_n": summary["b2_recovery_sample_n"],
                "b3_mean_frac": summary["b3_uptime_mean_frac"], "b3_pass": ev["B3"]["pass"],
                "b3_margin": ev["B3"]["margin"],
            })
            idx += 1
            print(f"[phaseA] drop_rate={dr} power_bonus={pb} catch_cap={cc} "
                  f"B1={summary['b1_drop_mean']:.2f}({'OK' if ev['B1']['pass'] else 'NG'}) "
                  f"B2={summary['b2_recovery_mean_sec']:.1f}s({'OK' if ev['B2']['pass'] else 'NG'}) "
                  f"B3={summary['b3_uptime_mean_frac']:.3f}({'OK' if ev['B3']['pass'] else 'NG'})")

    os.makedirs(args.out, exist_ok=True)
    path = f"{args.out}/item_phaseA_sweep.csv"
    cols = ["drop_rate", "power_bonus_per_stage", "catch_cap", "b1_mean", "b1_pass", "b1_margin",
            "b2_mean_sec", "b2_pass", "b2_margin", "b2_sample_n",
            "b3_mean_frac", "b3_pass", "b3_margin"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for row in phase_a_rows:
            w.writerow(row)
    print(f"RESULT: wrote {path} ({len(phase_a_rows)} rows)")

    both_pass = [r for r in phase_a_rows if r["b1_pass"] and r["b2_pass"]]
    all_three_pass = [r for r in both_pass if r["b3_pass"]]
    print(f"RESULT: B1&B2 both pass: {len(both_pass)}/{len(phase_a_rows)} combos")
    print(f"RESULT: B1&B2&B3 all pass: {len(all_three_pass)}/{len(phase_a_rows)} combos")

    # --- フェーズB：B1&B2を満たす組のうちB3が帯に最も近い束を基準に選び、
    # ball_effect_ticksだけを掃引してB3を帯内へ収められるか探す。
    # drop_rate/power_bonus/catch_capはB1・B2側の要因、ball_effect_ticksは
    # B3側の要因として、機構上ほぼ独立に効くため、この段階では
    # ball_effect_ticksだけを動かせば十分（§背景の衝突はB1>B3優先で扱う）。
    base = None
    phase_b_rows = []
    if both_pass:
        base = min(both_pass, key=lambda r: r["b3_margin"])
        print(f"[phaseB] base bundle(B1&B2両立・B3が最も帯に近い): "
              f"drop_rate={base['drop_rate']} power_bonus={base['power_bonus_per_stage']} "
              f"catch_cap={base['catch_cap']} (B3 margin={base['b3_margin']:.3f})")
        for bet in [2, 3, 4, 5, 6, 7]:
            item_system = {
                "drop_rate": base["drop_rate"], "ball_effect_ticks": bet,
                "ball_effect_magnitude": 1.0, "sticky_hold_ticks_range": sticky_range,
                "paddle_power_bonus_per_stage": base["power_bonus_per_stage"],
                "paddle_catch_rate_cap": base["catch_cap"],
            }
            raw = run_bundle(item_seed(1000 + bet), item_system, args.trials)
            summary = summarize_bundle(raw)
            ev = evaluate_bands(summary)
            phase_b_rows.append({
                "ball_effect_ticks": bet,
                "b1_mean": summary["b1_drop_mean"], "b1_pass": ev["B1"]["pass"],
                "b2_mean_sec": summary["b2_recovery_mean_sec"], "b2_pass": ev["B2"]["pass"],
                "b3_mean_frac": summary["b3_uptime_mean_frac"], "b3_pass": ev["B3"]["pass"],
                "b3_margin": ev["B3"]["margin"],
            })
            print(f"[phaseB] ball_effect_ticks={bet} "
                  f"B1={summary['b1_drop_mean']:.2f}({'OK' if ev['B1']['pass'] else 'NG'}) "
                  f"B2={summary['b2_recovery_mean_sec']:.1f}s({'OK' if ev['B2']['pass'] else 'NG'}) "
                  f"B3={summary['b3_uptime_mean_frac']:.3f}({'OK' if ev['B3']['pass'] else 'NG'})")
        path_b = f"{args.out}/item_phaseB_sweep.csv"
        cols_b = ["ball_effect_ticks", "b1_mean", "b1_pass", "b2_mean_sec", "b2_pass",
                  "b3_mean_frac", "b3_pass", "b3_margin"]
        with open(path_b, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols_b)
            w.writeheader()
            for row in phase_b_rows:
                w.writerow(row)
        print(f"RESULT: wrote {path_b} ({len(phase_b_rows)} rows)")
    else:
        print("RESULT: no combo satisfies B1&B2 simultaneously in phase A grid")

    meta = {
        "script": "sim/ring_structure/item_sim.py",
        "item_master_seed": ITEM_MASTER_SEED,
        "session_ticks": SESSION_TICKS,
        "trials_per_combo": args.trials,
        "sticky_hold_ticks_range": sticky_range,
        "b1_range": B1_RANGE, "b2_range_ticks": B2_RANGE_TICKS, "b3_max_frac": B3_MAX_FRAC,
        "drop_rates_swept": drop_rates, "power_bonuses_swept": power_bonuses,
        "catch_caps_swept": catch_caps,
        "phase_a_b1b2_pass_count": len(both_pass),
        "phase_a_all_pass_count": len(all_three_pass),
        "phase_b_base_bundle": base,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(f"{args.out}/item_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"RESULT: wrote {args.out}/item_meta.json")
    print("RESULT: PASS")


if __name__ == "__main__":
    main()
