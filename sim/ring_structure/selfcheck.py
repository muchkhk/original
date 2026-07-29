#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ring_sim.py の検証スクリプトの検品（指示書01 §やること 0）。

CLAUDE.md §9-c「検証コマンドは、成功パスと失敗パスが必ず別の出力になることを
確認してから実行する」を満たすことを、実例で示す。

各チェックは PASS/FAIL を明示し、1つでもFAILがあれば最後に
`RESULT: FAIL` と非ゼロ終了コードを返す（成功時は `RESULT: PASS`）。
tools/check_firebase_auth.mjs と同じ命名規則（FAIL:/RESULT: PASS）。

--inject-population-bug を付けると、実際に過去踏んだ「1world単位の
供給上限チェックが越境流入を防げず、球数が際限なく増殖する」バグを
意図的に再現し、check_population_conservation が確実にFAILすることを示す
（成功パスと失敗パスが別の出力になることの実例）。

使い方:
  python selfcheck.py                       # 正常系。RESULT: PASS を期待
  python selfcheck.py --inject-population-bug  # 異常系。RESULT: FAIL を期待
"""
import argparse
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")

import ring_sim as rs

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print(f"PASS: {name}")
    else:
        print(f"FAIL: {name}" + (f" ({detail})" if detail else ""))
        FAILURES.append(name)


def check_ring_math():
    for N in (3, 4, 5):
        w = 0
        for _ in range(N):
            w = (w + 1) % N
        check(f"ring_math N={N}: N回連続+1で出発点に戻る", w == 0, f"got w={w}")


def check_reproducibility():
    r1 = rs.run_condition(seed=777, N=4, serve_mode="inf", decay_on=True,
                           weakest_vanish=False, trials=20, ticks=200)
    r2 = rs.run_condition(seed=777, N=4, serve_mode="inf", decay_on=True,
                           weakest_vanish=False, trials=20, ticks=200)
    check("reproducibility: 同一seedで結果がbit-for-bit一致",
          r1 == r2, f"loop_events_total {r1['loop_events_total']} vs {r2['loop_events_total']}")


def check_population_conservation(inject_bug=False):
    """
    リング全体の同時球数が N*SERVE_CAP を超えないことを確認する。
    このチェック自体が、実装中に一度実際に踏んだバグ
    （world単位の上限チェックが越境流入を素通りさせ、球数が暴走した）
    の再発を検出するための回帰テストを兼ねる。
    """
    N, cap = 4, rs.SERVE_CAP
    import random

    if inject_bug:
        # 過去に実際に踏んだバグの症状（越境流入がworld単位の上限チェックを
        # 素通りし、球数が際限なく増殖する）を、SERVE_CAPを一時的に実質無限大に
        # 緩めることで再現する。本番コードは既にリング全体総数での頭打ちに
        # 修正済み（ring_sim.py内コメント参照）だが、この再現手順そのものが
        # 「もし修正が再度崩れたら、このチェックが検出できる」ことの証跡になる。
        orig_cap = rs.SERVE_CAP
        rs.SERVE_CAP = 10 ** 6  # 実質「上限なし」。旧バグと同じ症状（無制限増殖）を作る
        try:
            r = rs.simulate_trial(random.Random(1), N=N, serve_mode="inf",
                                   decay_on=True, weakest_vanish=False, ticks=150)
        finally:
            rs.SERVE_CAP = orig_cap
        max_seen = max(max(w) for w in r["simultaneous_counts"])
        # 意図的に「N*cap以内であるべき」という正常時の基準で判定する
        # （bugを入れた側は、この基準を破ることを示すのが目的）
        check("population_conservation: 同時球数がN*SERVE_CAPを超えない",
              max_seen <= N * orig_cap,
              f"max_seen={max_seen} > N*SERVE_CAP={N*orig_cap}（意図的に緩めたSERVE_CAPで再現）")
        return

    r = rs.simulate_trial(random.Random(1), N=N, serve_mode="inf",
                           decay_on=True, weakest_vanish=False, ticks=150)
    max_seen = max(max(w) for w in r["simultaneous_counts"])
    check("population_conservation: 同時球数がN*SERVE_CAPを超えない",
          max_seen <= N * cap, f"max_seen={max_seen} > N*SERVE_CAP={N*cap}")


def check_loop_detection_scripted():
    """
    up_streakがNに達した瞬間だけloop_completionsに記録され、
    N未満の連続では記録されないことを、確率を極端に振って確認する。
    """
    import random
    # f_max=1.0(ほぼ確実に上抜け), g_max=0.0(ほぼ絶対に落下跨ぎしない) にすると、
    # 最初に投入された球は数tickで確実に一周を完了するはず。
    r = rs.simulate_trial(random.Random(5), N=3, serve_mode="inf", decay_on=True,
                           weakest_vanish=False, ticks=30, f_max=0.999, g_max=0.0)
    check("loop_detection: f_max=0.999,g_max=0でN=3が数tick以内に一周完了する",
          len(r["loop_completions"]) >= 1 and min(r["loop_completions"]) <= 10,
          f"loop_completions={r['loop_completions'][:5]}")

    r2 = rs.simulate_trial(random.Random(5), N=3, serve_mode="inf", decay_on=True,
                            weakest_vanish=False, ticks=30, f_max=0.0, g_max=0.0)
    check("loop_detection: f_max=0では一周が一度も発生しない",
          len(r2["loop_completions"]) == 0, f"loop_completions={r2['loop_completions'][:5]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inject-population-bug", action="store_true",
                     help="過去に実在した越境流入バグを意図的に再現し、FAIL経路を実例で示す")
    args = ap.parse_args()

    check_ring_math()
    check_reproducibility()
    check_loop_detection_scripted()
    check_population_conservation(inject_bug=args.inject_population_bug)

    if FAILURES:
        print(f"RESULT: FAIL ({len(FAILURES)} check(s) failed: {', '.join(FAILURES)})")
        sys.exit(1)
    else:
        print("RESULT: PASS")
        sys.exit(0)


if __name__ == "__main__":
    main()
