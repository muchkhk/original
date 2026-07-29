#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ブロック崩し環構造（第3案）循環シミュレーション（指示書01）

N世界を向き付きの環で接続したモデルで、環境的に「一周」がどの程度発生するか、
崩壊曲線（ブロック破壊・地面損傷の蓄積）が越境確率をどう押し上げるかを
イベント駆動で検証する。GUI無し・CLI・CSV出力。

モデル（このスクリプトが定義の唯一の出所。指示書01には関数形の指定が無いため、
下記の仮定はすべてこのスクリプトの設計判断であり、報告書側に明記する）:

- N世界を 0..N-1 の環として並べる。「上方向」は常に (w+1) % N、
  「下方向」は常に (w-1) % N（指示書の「向き付きの環」を、上昇は+1方向・
  下降は-1方向に固定する、という一意な向き付けとして解釈した）。
- 各世界は block_remaining（ブロック残量、初期1.0）と ground_damage
  （地面損傷、初期0.0）を持つ。両者とも回復しない一方向の蓄積量とした
  （指示書のテーマである「崩壊曲線」を体現する設計判断。回復を入れると
  世界が定常状態に落ち着き、崩壊曲線という現象自体が測れなくなる）。
- 1tick=1ラリー（ブロック接触1回＋パドル接触1回）という抽象化。実際の
  ブロック崩しは1ラリー中に複数回ブロックに当たるが、抽象モデルとして
  「1tickで内部状態が1段階進む」とみなした（捨象。感度は§報告書参照）。
- 上抜け確率 f(destruction) と 落下越境確率 g(damage) は、指示書が
  「進むほど起きやすくなる」としか規定していないため、最も単純で
  説明可能な仮定として線形関数を採用した: f(x)=F_MIN+(F_MAX-F_MIN)*x
  （destruction = 1-block_remaining）、g(x)=G_MIN+(G_MAX-G_MIN)*x。
- パドル捕球率は全世界共通の定数（プレイヤー技量差は捨象）。
- 「一周」判定：向き付き環でN回連続して+1方向に進めば、mod N の性質上
  必ず出発点に戻る。したがって「連続する上昇跨ぎがN回に達した」ことだけを
  判定すればよく、「出発世界に戻ったか」を別途照合する必要はない
  （ここは近似ではなく環の数学的性質そのもの）。downward crossing を挟むと
  連続カウントは0にリセットする。

再現性：MASTER_SEED はリポジトリの sim/*.py と同じ命名規則（本指示書の作業日）。
標準ライブラリ random.Random(seed) を条件ごとに独立生成し、seedは
MASTER_SEEDと条件インデックスから決定的に導出する（大きな素数オフセットで
条件間の系列衝突を避ける）。これにより条件の実行順を変えても各条件が
独立・再現可能な乱数系列を持つ（rational_keen_sim.py の「実行順を変えない
こと」という制約より頑健な方式）。numpyはこの環境に未インストールのため
使用しない（§9-f：Node/npm/Pythonの実在確認はしたが、numpy等の追加
パッケージが常に入っているとは限らない）。

使い方:
  python ring_sim.py --trials 1000 --ticks 1500 --out results
  python ring_sim.py --trials 50 --ticks 200 --out /tmp/smoke   # スモークテスト
"""
import argparse
import csv
import itertools
import json
import os
import random
import statistics
import sys
from datetime import datetime, timezone

MASTER_SEED = 20260729  # 指示書01の作業日付（2026-07-29）
SEED_PRIME = 1_000_003  # 条件インデックスから子seedを導出する際のオフセット素数


def child_seed(index):
    return MASTER_SEED + index * SEED_PRIME

# ============ モデル定数（すべて設計判断。報告書「捨象した仮定」に転記） ============
# 較正メモ：初期値（HIT_DECREMENT=0.05, F_MAX=0.35等）ではスモークテストで
# 100tick中に平均110周・16球中最大11球が1worldに滞留という明らかな暴走が発生した
# （さらに調べると「1world上限チェックが越境流入を防げていない」別バグも併発して
# いたため、まずそのバグをリング全体総数の上限管理に修正した上で、確率も
# 「世界一周は稀だが到達可能な達成」という設計意図に合わせて1桁下げた）。
HIT_DECREMENT = 0.01        # 1ブロック接触あたりのblock_remaining減少量
F_MIN, F_MAX = 0.002, 0.04  # 上抜け確率の下限/上限（destruction=0 / destruction=1）
DMG_INCREMENT = 0.015       # 1ミスあたりのground_damage増加量
G_MIN, G_MAX = 0.005, 0.05  # 落下越境確率の下限/上限（damage=0 / damage=1）
PADDLE_CATCH_RATE = 0.75    # 全世界共通のパドル捕球率
SERVE_CAP = 3                # リング全体の同時最大球数 = SERVE_CAP*N（世界あたりの「公平配分」の目安値でもある）
SERVE_INTERVAL = 40          # 間隔制限サーブでの投入間隔（tick）
STARVE_THRESHOLD = 0       # 「球涸れ」＝同時球数がこの値以下
OVERFLOW_THRESHOLD = SERVE_CAP  # 「球過多」＝同時球数がこの値以上（=上限に張り付いている）


def f_upcross(destruction):
    return F_MIN + (F_MAX - F_MIN) * destruction


def g_downcross(damage):
    return G_MIN + (G_MAX - G_MIN) * damage


def simulate_trial(rng, N, serve_mode, decay_on, weakest_vanish, ticks,
                    f_max=F_MAX, g_max=G_MAX):
    """1試行を実行し、Q1〜Q3に必要な生データを辞書で返す。"""
    block_remaining = [1.0] * N
    ground_damage = [0.0] * N

    def f_local(x):
        return F_MIN + (f_max - F_MIN) * x

    def g_local(x):
        return G_MIN + (g_max - G_MIN) * x

    # ball: [world, level, up_streak, birth_tick, ever_reinforced(bool), lost(bool)]
    balls = []
    next_ball_id = 0
    balls_by_world = [[] for _ in range(N)]  # world -> list of ball indices into `balls`

    loop_completions = []       # tick差分のリスト（Q1：一周1回ごとの所要時間）
    first_loop_tick = None      # この試行で最初の一周が完了した絶対tick（Q1：短時間セッションでの到達率用）
    simultaneous_counts = [[] for _ in range(N)]  # world毎のtickスナップショット（Q2）
    ever_reinforced_total = 0   # 1回以上上昇した球の総数（参考値）
    upcross_events_total = 0    # 上昇跨ぎイベントの総数（Q3分母：進んだ歩数）
    interrupted_events_total = 0  # up_streak>0の途中で落下跨ぎに遭った回数（Q3分子：挫折した歩数）
    vanished_total = 0

    def spawn(world, tick):
        nonlocal next_ball_id
        b = {
            "world": world, "level": 0, "up_streak": 0,
            "birth_tick": tick, "ever_reinforced": False,
        }
        balls.append(b)
        balls_by_world[world].append(b)
        next_ball_id += 1
        return b

    max_total_balls = N * SERVE_CAP  # リング全体での同時球数の上限（後述バグ修正）

    for tick in range(ticks):
        # --- サーブ供給フェーズ ---
        # 上限はリング全体の総数で管理する（world単位ではない）。
        # world単位で「len(balls_by_world[w]) < SERVE_CAP」だけを見て spawn すると、
        # 跨ぎ（越境）による他worldからの流入はこのチェックを経由しないため、
        # 各worldの球数がSERVE_CAPを際限なく超えて増殖し続けるバグになる
        # （実測：SERVE_CAP=2でも1world最大11球まで増殖し、100tickで118周発生。
        # 崩壊が進むほど越境が増える→流入が増える→さらに崩壊が進む、の正のフィード
        # バックで暴走した）。総数をN*SERVE_CAPで頭打ちにすることで、越境による
        # world間の偏り（球涸れ／球過多）は測れるが、系全体の球が際限なく増える
        # ことは防ぐ。
        total_active = sum(len(balls_by_world[w]) for w in range(N))
        if serve_mode == "inf":
            for w in range(N):
                if total_active >= max_total_balls:
                    break
                spawn(w, tick)
                total_active += 1
        else:  # interval
            if tick % SERVE_INTERVAL == 0:
                for w in range(N):
                    if total_active >= max_total_balls:
                        break
                    spawn(w, tick)
                    total_active += 1

        # --- Q2用スナップショット ---
        for w in range(N):
            simultaneous_counts[w].append(len(balls_by_world[w]))

        # --- 各球のラリー処理 ---
        # 重要：このtickで跨いだ球を、跨いだ先のworldへ即座に混ぜてはいけない。
        # worldをw=0..N-1の順で処理するため、即座に混ぜると「跨いだ直後にもう一度
        # 同じtick内で跨ぎ判定を受ける」cascadeが起き、1tickで複数世界を跨げてしまう
        # （1tick=1ラリーという抽象の前提が崩れる）。next_by_worldへ退避し、
        # 全world処理後にまとめて反映する。
        snapshot = balls_by_world
        next_by_world = [[] for _ in range(N)]
        for w in range(N):
            for b in snapshot[w]:
                # a) ブロックフェーズ
                block_remaining[w] = max(0.0, block_remaining[w] - HIT_DECREMENT)
                destruction = 1.0 - block_remaining[w]
                if rng.random() < f_local(destruction):
                    # 上昇跨ぎ
                    b["level"] += 1
                    if not b["ever_reinforced"]:
                        b["ever_reinforced"] = True
                        ever_reinforced_total += 1
                    upcross_events_total += 1
                    b["up_streak"] += 1
                    b["world"] = (w + 1) % N
                    if b["up_streak"] == N:
                        loop_completions.append(tick - b["birth_tick"])
                        if first_loop_tick is None:
                            first_loop_tick = tick
                        b["up_streak"] = 0
                    next_by_world[b["world"]].append(b)
                    continue  # このtickでの処理は完了

                # b) パドルフェーズ（上昇しなかった場合のみ）
                if rng.random() < PADDLE_CATCH_RATE:
                    next_by_world[w].append(b)  # 捕球。世界に留まる
                    continue

                ground_damage[w] = min(1.0, ground_damage[w] + DMG_INCREMENT)
                if rng.random() < g_local(ground_damage[w]):
                    # 落下跨ぎ：up_streak>0（＝一周に向けて進行中）だった場合、
                    # この一周の試みは失敗として記録する（Q3分子）
                    was_mid_loop = b["up_streak"] > 0
                    if was_mid_loop:
                        interrupted_events_total += 1
                    b["up_streak"] = 0
                    if decay_on:
                        prev_level = b["level"]
                        if weakest_vanish and prev_level <= -1:
                            # 既に「弱い球」の状態でさらに落下跨ぎ＝消滅（残存しない）
                            vanished_total += 1
                            continue
                        b["level"] = max(-1, prev_level - 1)
                    # decay_on=False（対照条件）：落下跨ぎは起きるが段数は変化しない
                    b["world"] = (w - 1) % N
                    next_by_world[b["world"]].append(b)
                    continue

                next_by_world[w].append(b)  # 越境せず、この世界に留まる

        balls_by_world = next_by_world

    total_balls = next_ball_id
    return {
        "loop_completions": loop_completions,
        "first_loop_tick": first_loop_tick,
        "simultaneous_counts": simultaneous_counts,
        "ever_reinforced_total": ever_reinforced_total,
        "upcross_events_total": upcross_events_total,
        "interrupted_events_total": interrupted_events_total,
        "vanished_total": vanished_total,
        "total_balls": total_balls,
    }


def pct(sorted_vals, p):
    if not sorted_vals:
        return None
    idx = min(len(sorted_vals) - 1, max(0, int(round(p * (len(sorted_vals) - 1)))))
    return sorted_vals[idx]


SESSION_LENGTH_THRESHOLDS = [100, 300, 600]  # 「妥当なプレイ」1セッションの目安tick数（複数点）


def run_condition(seed, N, serve_mode, decay_on, weakest_vanish, trials, ticks,
                   f_max=F_MAX, g_max=G_MAX):
    rng = random.Random(seed)
    all_loop_times = []
    first_loop_ticks = []  # None混じり。試行ごとの最初の一周到達tick
    loops_per_trial = []
    trials_with_loop = 0
    starve_frac_per_world = [[] for _ in range(N)]
    overflow_frac_per_world = [[] for _ in range(N)]
    mean_pop_per_world = [[] for _ in range(N)]
    ever_reinforced_sum = 0
    upcross_events_sum = 0
    interrupted_events_sum = 0
    vanished_sum = 0
    total_balls_sum = 0

    for _ in range(trials):
        r = simulate_trial(rng, N, serve_mode, decay_on, weakest_vanish, ticks,
                            f_max=f_max, g_max=g_max)
        all_loop_times.extend(r["loop_completions"])
        first_loop_ticks.append(r["first_loop_tick"])
        loops_per_trial.append(len(r["loop_completions"]))
        if r["loop_completions"]:
            trials_with_loop += 1
        for w in range(N):
            counts = r["simultaneous_counts"][w]
            starve_frac_per_world[w].append(sum(1 for c in counts if c <= STARVE_THRESHOLD) / len(counts))
            overflow_frac_per_world[w].append(sum(1 for c in counts if c >= OVERFLOW_THRESHOLD) / len(counts))
            mean_pop_per_world[w].append(sum(counts) / len(counts))
        ever_reinforced_sum += r["ever_reinforced_total"]
        upcross_events_sum += r["upcross_events_total"]
        interrupted_events_sum += r["interrupted_events_total"]
        vanished_sum += r["vanished_total"]
        total_balls_sum += r["total_balls"]

    all_loop_times_sorted = sorted(all_loop_times)
    threshold_fracs = {}
    for th in SESSION_LENGTH_THRESHOLDS:
        n_within = sum(1 for t in first_loop_ticks if t is not None and t <= th)
        threshold_fracs[f"loop_within_{th}t_frac"] = n_within / trials

    result = {
        "N": N, "serve_mode": serve_mode, "decay_on": decay_on,
        "weakest_vanish": weakest_vanish, "trials": trials, "ticks": ticks,
        # Q1: 一周所要時間の分布
        "loop_events_total": len(all_loop_times),
        "trials_with_loop_frac": trials_with_loop / trials,
        "loops_per_trial_mean": statistics.mean(loops_per_trial),
        "loop_time_mean": statistics.mean(all_loop_times) if all_loop_times else None,
        "loop_time_median": pct(all_loop_times_sorted, 0.5),
        "loop_time_p10": pct(all_loop_times_sorted, 0.10),
        "loop_time_p90": pct(all_loop_times_sorted, 0.90),
        # Q2: 同時球数・球涸れ／球過多
        "mean_pop_avg_over_worlds": statistics.mean(
            [statistics.mean(w) for w in mean_pop_per_world]),
        "starve_frac_avg_over_worlds": statistics.mean(
            [statistics.mean(w) for w in starve_frac_per_world]),
        "overflow_frac_avg_over_worlds": statistics.mean(
            [statistics.mean(w) for w in overflow_frac_per_world]),
        # Q3: 強化球が一周完了前に落下減衰・喪失する率（挫折感の代理指標）。
        # 定義：上昇跨ぎ（歩数）のうち、その後up_streakが完了(=N)に達する前に
        # 落下跨ぎで中断された歩数の割合。ever_reinforced_totalは参考値（球単位の分母）。
        "ever_reinforced_total": ever_reinforced_sum,
        "upcross_events_total": upcross_events_sum,
        "interrupted_events_total": interrupted_events_sum,
        "vanished_total": vanished_sum,
        "interrupted_rate": (interrupted_events_sum / upcross_events_sum) if upcross_events_sum else None,
        "total_balls_served": total_balls_sum,
    }
    result.update(threshold_fracs)
    return result


CONDITION_COLUMNS = [
    "N", "serve_mode", "decay_on", "weakest_vanish", "trials", "ticks",
    "loop_events_total", "trials_with_loop_frac", "loops_per_trial_mean",
    "loop_time_mean", "loop_time_median", "loop_time_p10", "loop_time_p90",
    "loop_within_100t_frac", "loop_within_300t_frac", "loop_within_600t_frac",
    "mean_pop_avg_over_worlds", "starve_frac_avg_over_worlds", "overflow_frac_avg_over_worlds",
    "ever_reinforced_total", "upcross_events_total", "interrupted_events_total",
    "vanished_total", "interrupted_rate", "total_balls_served",
]

SENSITIVITY_COLUMNS = [
    "N", "serve_mode", "decay_on", "weakest_vanish", "f_max", "g_max",
    "trials", "ticks", "loop_events_total", "trials_with_loop_frac",
    "loops_per_trial_mean", "loop_time_mean",
]


def write_csv(path, rows, columns):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for row in rows:
            w.writerow({c: row.get(c) for c in columns})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=1000)
    ap.add_argument("--ticks", type=int, default=1500)
    ap.add_argument("--out", type=str, default="sim/ring_structure/results")
    ap.add_argument("--skip-sensitivity", action="store_true")
    ap.add_argument("--sensitivity-trials", type=int, default=800)
    args = ap.parse_args()

    conditions = list(itertools.product(
        [3, 4],                # N
        ["inf", "interval"],   # serve_mode
        [True, False],         # decay_on
        [True, False],         # weakest_vanish
    ))

    rows = []
    for idx, (N, serve_mode, decay_on, weakest_vanish) in enumerate(conditions):
        row = run_condition(child_seed(idx), N, serve_mode, decay_on, weakest_vanish,
                             args.trials, args.ticks)
        rows.append(row)
        print(f"[condition] N={N} serve={serve_mode} decay={decay_on} "
              f"weakest_vanish={weakest_vanish} -> "
              f"loop_rate={row['trials_with_loop_frac']:.3f} "
              f"loop_time_mean={row['loop_time_mean']}")

    conditions_csv = f"{args.out}/ring_conditions.csv"
    write_csv(conditions_csv, rows, CONDITION_COLUMNS)
    print(f"RESULT: wrote {conditions_csv} ({len(rows)} rows)")

    sens_rows = []
    if not args.skip_sensitivity:
        base_N, base_serve, base_decay, base_weakest = 4, "inf", True, False
        f_variants = [0.15, F_MAX, 0.55]
        g_variants = [0.15, G_MAX, 0.45]
        sens_conditions = (
            [("f_max", v) for v in f_variants] +
            [("g_max", v) for v in g_variants]
        )
        for sens_idx, (param_name, value) in enumerate(sens_conditions):
            f_max = value if param_name == "f_max" else F_MAX
            g_max = value if param_name == "g_max" else G_MAX
            row = run_condition(child_seed(len(conditions) + sens_idx),
                                 base_N, base_serve, base_decay, base_weakest,
                                 args.sensitivity_trials, args.ticks,
                                 f_max=f_max, g_max=g_max)
            row["f_max"] = f_max
            row["g_max"] = g_max
            sens_rows.append(row)
            print(f"[sensitivity] {param_name}={value} -> "
                  f"loop_rate={row['trials_with_loop_frac']:.3f}")

        sens_csv = f"{args.out}/ring_sensitivity.csv"
        write_csv(sens_csv, sens_rows, SENSITIVITY_COLUMNS)
        print(f"RESULT: wrote {sens_csv} ({len(sens_rows)} rows)")

    meta = {
        "script": "sim/ring_structure/ring_sim.py",
        "master_seed": MASTER_SEED,
        "trials_per_condition": args.trials,
        "ticks_per_trial": args.ticks,
        "sensitivity_trials": None if args.skip_sensitivity else args.sensitivity_trials,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "constants": {
            "HIT_DECREMENT": HIT_DECREMENT, "F_MIN": F_MIN, "F_MAX": F_MAX,
            "DMG_INCREMENT": DMG_INCREMENT, "G_MIN": G_MIN, "G_MAX": G_MAX,
            "PADDLE_CATCH_RATE": PADDLE_CATCH_RATE, "SERVE_CAP": SERVE_CAP,
            "SERVE_INTERVAL": SERVE_INTERVAL,
        },
    }
    meta_path = f"{args.out}/ring_meta.json"
    os.makedirs(os.path.dirname(meta_path) or ".", exist_ok=True)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"RESULT: wrote {meta_path}")
    print("RESULT: PASS")


if __name__ == "__main__":
    main()
