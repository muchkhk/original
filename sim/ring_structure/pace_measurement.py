#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
シム実測ペースの抽出（指示書14 作業2 3-1）

確定束（v1.4値）・serve_mode=interval（設計帯・SERVE_INTERVAL=40tick≈56秒相当）で、
1世界あたりの下降跨ぎ・上昇跨ぎ・アイテム越境（取りこぼし）の到来間隔分布と、
世界内同時球数の分布を実測する。較正用プロトタイプ（指示書14 作業1・3-2〜3-5）の
模擬発生器デフォルト値を、この実測値へ整合させるための基礎データを得る。

【最重要の設計制約：ring_sim.py本体は変更しない（0差分）】
指示書14 §0の明記どおり、`ring_sim.py`は本作業を通じて一切変更しない
（`git diff`で確認・報告書に記載）。一方、`simulate_trial()`は「跨ぎ（越境）が
どのworldへ・どのtickで発生したか」というtick単位のイベント列を返り値として
公開していない（集計値・fractionのみ）。到来間隔の分布を実測するにはtick単位の
イベント列が必要であり、これは既存の公開APIだけでは取得できない。

そこで、指示書11で追加した`record_first_events`引数と同じ非干渉の設計思想
（読み取り専用・新規rng呼び出しを一切追加しない・既定で無効）を踏襲しつつ、
**ring_sim.pyのファイル自体は書き換えず**、そのソーステキストをメモリ上でのみ
読み込み、上記3イベントの発生箇所（`b["world"] = (w ± 1) % N`と
`item_pickup_miss_total[w] += 1`）の直後に、モジュール変数`_CROSSING_TRACK`
（既定None）へ`(world, tick)`を追記するだけの1〜2行を挿入した「計装済みクローン」を
`exec()`でオンザフライ生成し、そのクローンだけをこのスクリプト内で使う
（`sim/ring_structure/ring_sim.py`というファイルは一度も書き込みを受けない）。
挿入するコードは既存のRNG消費順・力学に一切影響しない（新規`rng.random()`呼び出しを
追加しない）。非干渉は§verify_non_interferenceで実測により証明する
（同一seedでのオリジナルモジュールとの完全一致、および`_CROSSING_TRACK=None`時と
`simulate_trial()`の既定動作の完全一致の両方）。

使い方:
  python pace_measurement.py --trials 1000 --out results
"""
import argparse
import csv
import json
import os
import random
import statistics
import sys
import types
from datetime import datetime, timezone

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_RING_SIM_PATH = os.path.join(_THIS_DIR, "ring_sim.py")

# ring_sim.py本体を素の状態でimport（laser較正等、既存スクリプトの再利用のため）
sys.path.insert(0, _THIS_DIR)
import ring_sim as ring_sim_original  # noqa: E402
from item_sim import (item_seed, NATURAL_N, NATURAL_DECAY_ON, NATURAL_WEAKEST_VANISH,
                       SESSION_TICKS, FIXED_POWER_BONUS_PER_STAGE,
                       FIXED_PADDLE_CATCH_RATE_CAP)  # noqa: E402
from item_requirement_check import SLOPE_KIND  # noqa: E402
from item_requirement_rederive_sweep import (  # noqa: E402
    FIXED_DROP_RATE, FIXED_WEAKEN_MULTIPLIER, FIXED_LASER_TARGET_FRAC,
    FIXED_BALL_EFFECT_TICKS, FIXED_ITEM_WALL_BRICKS, DEFAULT_PICKUP_MISS_RATE_BASE)
from item_compound_sweep import calibrate_laser_dps  # noqa: E402
from wall_sim import Q4_MS_PER_TICK  # noqa: E402

DESIGN_SERVE_MODE = "interval"  # 指示書14 3-1：設計帯（SERVE_INTERVAL=40tick≈56秒相当）


def load_instrumented_ring_sim():
    """ring_sim.pyのソースをメモリ上でのみ読み込み、跨ぎ・アイテム取りこぼしの直後に
    `_CROSSING_TRACK`（既定None）への読み取り専用の追記を挿入したクローンをexec()で
    生成する。ring_sim.pyというファイルは一切書き換えない（disk上0差分）。"""
    with open(_RING_SIM_PATH, encoding="utf-8") as f:
        src = f.read()

    patches = [
        ('item_pickup_miss_total[w] += 1\n',
         'item_pickup_miss_total[w] += 1\n'
         '                if _CROSSING_TRACK is not None:\n'
         '                    _CROSSING_TRACK["item_miss"][w].append(tick)\n'),
        ('b["world"] = (w + 1) % N\n',
         'b["world"] = (w + 1) % N\n'
         '                    if _CROSSING_TRACK is not None:\n'
         '                        _CROSSING_TRACK["up"][b["world"]].append(tick)\n'),
        ('b["world"] = (w - 1) % N\n',
         'b["world"] = (w - 1) % N\n'
         '                    if _CROSSING_TRACK is not None:\n'
         '                        _CROSSING_TRACK["down"][b["world"]].append(tick)\n'),
    ]
    for marker, replacement in patches:
        count = src.count(marker)
        if count != 1:
            raise RuntimeError(
                f"計装パッチの前提が崩れている（ring_sim.pyが変更された可能性）: "
                f"マーカー{marker!r}の出現数={count}（期待値1）。"
                f"停止則：弄り続けず報告して停止する。")
        src = src.replace(marker, replacement, 1)

    src = "_CROSSING_TRACK = None\n" + src

    mod = types.ModuleType("ring_sim_instrumented")
    mod.__file__ = _RING_SIM_PATH + " (in-memory instrumented clone; ring_sim.py自体は無変更)"
    exec(compile(src, "<ring_sim_instrumented>", "exec"), mod.__dict__)
    return mod


def verify_non_interference(instrumented, seed=999_983, trials=200):
    """非干渉の実証：
    (a) オリジナルring_sim.simulate_trial()と、計装クローンで_CROSSING_TRACK=None
        （既定）のsimulate_trial()が、同一seed・同一引数でbit-for-bit一致すること
    (b) 計装クローンで_CROSSING_TRACK有効時とNone時で、damage等の力学的出力が
        一致すること（トラッキング自体が新規rng呼び出しを持たないことの実証）
    """
    item_system = build_confirmed_bundle_no_laser()

    def run(mod, seed_, track):
        mod._CROSSING_TRACK = track
        rng = random.Random(seed_)
        results = []
        for _ in range(trials):
            r = mod.simulate_trial(
                rng, NATURAL_N, DESIGN_SERVE_MODE, NATURAL_DECAY_ON, NATURAL_WEAKEST_VANISH,
                SESSION_TICKS, slope_kind=SLOPE_KIND, item_system=item_system)
            results.append(r["wall_damage_total"])
        mod._CROSSING_TRACK = None
        return results

    original_damages = run(ring_sim_original, seed, None)
    cloned_off_damages = run(instrumented, seed, None)
    track = {"up": [[] for _ in range(NATURAL_N)], "down": [[] for _ in range(NATURAL_N)],
             "item_miss": [[] for _ in range(NATURAL_N)]}
    cloned_on_damages = run(instrumented, seed, track)

    a_identical = original_damages == cloned_off_damages
    b_identical = cloned_off_damages == cloned_on_damages
    any_tracked = any(track[k][w] for k in track for w in range(NATURAL_N))

    return {
        "a_original_vs_clone_off_identical": a_identical,
        "b_clone_off_vs_on_identical": b_identical,
        "tracking_actually_recorded_events": any_tracked,
        "trials": trials,
    }


def build_confirmed_bundle_no_laser():
    return {
        "drop_rate": FIXED_DROP_RATE, "ball_effect_ticks": FIXED_BALL_EFFECT_TICKS,
        "ball_effect_magnitude": 1.0,
        "paddle_power_bonus_per_stage": FIXED_POWER_BONUS_PER_STAGE,
        "paddle_catch_rate_cap": FIXED_PADDLE_CATCH_RATE_CAP,
        "pickup_miss_rate_base": DEFAULT_PICKUP_MISS_RATE_BASE,
        "weaken_multiplier": FIXED_WEAKEN_MULTIPLIER,
        "item_wall_bricks": FIXED_ITEM_WALL_BRICKS,
    }


def pct(sorted_vals, p):
    if not sorted_vals:
        return None
    idx = min(len(sorted_vals) - 1, max(0, int(round(p * (len(sorted_vals) - 1)))))
    return sorted_vals[idx]


def intervals_from_arrivals(arrival_ticks_per_world):
    """world毎の到来tickリストから、隣接差分（到来間隔）を全world・全試行分プールして返す。"""
    out = []
    for per_trial in arrival_ticks_per_world:
        for w_ticks in per_trial:
            w_ticks_sorted = sorted(w_ticks)
            for i in range(1, len(w_ticks_sorted)):
                out.append(w_ticks_sorted[i] - w_ticks_sorted[i - 1])
    return out


def summarize_intervals(intervals):
    if not intervals:
        return {"n": 0, "median_tick": None, "p10_tick": None, "p90_tick": None,
                "median_sec": None, "p10_sec": None, "p90_sec": None}
    s = sorted(intervals)
    med, p10, p90 = pct(s, 0.5), pct(s, 0.10), pct(s, 0.90)
    to_sec = lambda t: (t * Q4_MS_PER_TICK / 1000) if t is not None else None
    return {"n": len(s), "median_tick": med, "p10_tick": p10, "p90_tick": p90,
            "median_sec": to_sec(med), "p10_sec": to_sec(p10), "p90_sec": to_sec(p90)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=1000)
    ap.add_argument("--calib-trials", type=int, default=200)
    ap.add_argument("--out", type=str, default="results")
    ap.add_argument("--seed-base", type=int, default=50_010)
    args = ap.parse_args()

    instrumented = load_instrumented_ring_sim()

    print("[verify] 非干渉の実証を実行中...")
    interference = verify_non_interference(instrumented)
    print(f"[verify] (a) オリジナル vs 計装クローン(トラッキング無効): "
          f"{'完全一致' if interference['a_original_vs_clone_off_identical'] else '不一致(異常)'}")
    print(f"[verify] (b) 計装クローン トラッキング無効 vs 有効: "
          f"{'完全一致' if interference['b_clone_off_vs_on_identical'] else '不一致(異常)'}")
    print(f"[verify] トラッキングが実際にイベントを記録したか: "
          f"{interference['tracking_actually_recorded_events']}")
    if not (interference["a_original_vs_clone_off_identical"]
            and interference["b_clone_off_vs_on_identical"]
            and interference["tracking_actually_recorded_events"]):
        print("RESULT: FAIL (非干渉の実証に失敗。ring_sim.pyが変更された可能性、または"
              "計装パッチが力学に影響している可能性。停止則に従い停止する)")
        sys.exit(1)

    # 確定束（laser較正込み。計算自体はオリジナルモジュール経由＝計装クローンに依存しない）
    bundle_no_laser = build_confirmed_bundle_no_laser()
    dps, laser_frac, baseline_mean = calibrate_laser_dps(
        5000, bundle_no_laser, FIXED_LASER_TARGET_FRAC, args.calib_trials)
    item_system = dict(bundle_no_laser, laser_dps_per_stage=dps)
    print(f"[calib] laser_dps_per_stage={dps:.4f} achieved_frac={laser_frac:.4f} "
          f"baseline_mean(no laser)={baseline_mean:.1f}")

    rng = random.Random(item_seed(args.seed_base))
    up_arrivals_all, down_arrivals_all, item_miss_arrivals_all = [], [], []
    pop_max_per_trial, pop_mean_per_trial, overflow_frac_per_trial = [], [], []
    OVERFLOW_THRESHOLD = ring_sim_original.SERVE_CAP

    for _ in range(args.trials):
        track = {"up": [[] for _ in range(NATURAL_N)], "down": [[] for _ in range(NATURAL_N)],
                  "item_miss": [[] for _ in range(NATURAL_N)]}
        instrumented._CROSSING_TRACK = track
        r = instrumented.simulate_trial(
            rng, NATURAL_N, DESIGN_SERVE_MODE, NATURAL_DECAY_ON, NATURAL_WEAKEST_VANISH,
            SESSION_TICKS, slope_kind=SLOPE_KIND, item_system=item_system)
        instrumented._CROSSING_TRACK = None

        up_arrivals_all.append(track["up"])
        down_arrivals_all.append(track["down"])
        item_miss_arrivals_all.append(track["item_miss"])

        for w in range(NATURAL_N):
            counts = r["simultaneous_counts"][w]
            pop_max_per_trial.append(max(counts))
            pop_mean_per_trial.append(sum(counts) / len(counts))
            overflow_frac_per_trial.append(
                sum(1 for c in counts if c >= OVERFLOW_THRESHOLD) / len(counts))

    up_intervals = intervals_from_arrivals(up_arrivals_all)
    down_intervals = intervals_from_arrivals(down_arrivals_all)
    item_miss_intervals = intervals_from_arrivals(item_miss_arrivals_all)

    up_summary = summarize_intervals(up_intervals)
    down_summary = summarize_intervals(down_intervals)
    item_summary = summarize_intervals(item_miss_intervals)

    pop_summary = {
        "mean_pop_mean": statistics.mean(pop_mean_per_trial),
        "mean_pop_max": statistics.mean(pop_max_per_trial),
        "overflow_frac_mean": statistics.mean(overflow_frac_per_trial),
    }

    print(f"[result] 上昇跨ぎ到来間隔（=噴出ペース） median={up_summary['median_tick']}tick"
          f"({up_summary['median_sec']:.1f}s) p10={up_summary['p10_tick']}tick "
          f"p90={up_summary['p90_tick']}tick (n={up_summary['n']})")
    print(f"[result] 下降跨ぎ到来間隔（=マルチボールペース） median={down_summary['median_tick']}tick"
          f"({down_summary['median_sec']:.1f}s) p10={down_summary['p10_tick']}tick "
          f"p90={down_summary['p90_tick']}tick (n={down_summary['n']})")
    print(f"[result] アイテム取りこぼし到来間隔（=アイテム越境ペースの代理指標） "
          f"median={item_summary['median_tick']}tick({item_summary['median_sec']:.1f}s) "
          f"p10={item_summary['p10_tick']}tick p90={item_summary['p90_tick']}tick "
          f"(n={item_summary['n']})")
    print(f"[result] 同時球数：平均={pop_summary['mean_pop_mean']:.2f} "
          f"最大平均={pop_summary['mean_pop_max']:.2f} "
          f"球過多割合={pop_summary['overflow_frac_mean']*100:.1f}%")

    os.makedirs(args.out, exist_ok=True)
    path = f"{args.out}/pace_measurement.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["event", "interval_tick"])
        for t in up_intervals:
            w.writerow(["up_crossing", t])
        for t in down_intervals:
            w.writerow(["down_crossing", t])
        for t in item_miss_intervals:
            w.writerow(["item_miss", t])
    print(f"RESULT: wrote {path} ({len(up_intervals)+len(down_intervals)+len(item_miss_intervals)} rows)")

    meta = {
        "script": "sim/ring_structure/pace_measurement.py",
        "design_serve_mode": DESIGN_SERVE_MODE,
        "serve_interval_ticks": ring_sim_original.SERVE_INTERVAL,
        "N": NATURAL_N, "session_ticks": SESSION_TICKS, "trials": args.trials,
        "ms_per_tick": Q4_MS_PER_TICK,
        "fixed_bundle": {
            "drop_rate": FIXED_DROP_RATE, "weaken_multiplier": FIXED_WEAKEN_MULTIPLIER,
            "laser_target_frac": FIXED_LASER_TARGET_FRAC, "laser_dps_per_stage": dps,
            "ball_effect_ticks": FIXED_BALL_EFFECT_TICKS, "pierce_hits": 3,
            "item_wall_bricks": FIXED_ITEM_WALL_BRICKS,
            "pickup_miss_rate_base": DEFAULT_PICKUP_MISS_RATE_BASE,
            "requirement": 68700,
        },
        "non_interference": interference,
        "up_crossing_interval": up_summary,
        "down_crossing_interval": down_summary,
        "item_miss_interval": item_summary,
        "population": pop_summary,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    meta_path = f"{args.out}/pace_measurement_meta.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"RESULT: wrote {meta_path}")
    print("RESULT: PASS")


if __name__ == "__main__":
    main()
