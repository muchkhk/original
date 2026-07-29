#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ring_sim.py の検証スクリプトの検品（指示書01 §やること 0 / 指示書02 §やること 0）。

CLAUDE.md §9-c「検証コマンドは、成功パスと失敗パスが必ず別の出力になることを
確認してから実行する」を満たすことを、実例で示す。

各チェックは PASS/FAIL を明示し、1つでもFAILがあれば最後に
`RESULT: FAIL` と非ゼロ終了コードを返す（成功時は `RESULT: PASS`）。
tools/check_firebase_auth.mjs と同じ命名規則（FAIL:/RESULT: PASS）。

--inject-population-bug を付けると、実際に過去踏んだ「1world単位の
供給上限チェックが越境流入を防げず、球数が際限なく増殖する」バグを
意図的に再現し、check_population_conservation が確実にFAILすることを示す
（成功パスと失敗パスが別の出力になることの実例）。

--inject-wall-bug を付けると、指示書02の新規ロジック（strage傾斜の
has_looped判定）を意図的に壊し、check_staged_slope_jumps_after_loopが
確実にFAILすることを示す。

使い方:
  python selfcheck.py                       # 正常系。RESULT: PASS を期待
  python selfcheck.py --inject-population-bug  # 異常系。RESULT: FAIL を期待
  python selfcheck.py --inject-wall-bug        # 異常系。RESULT: FAIL を期待
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


def check_wall_backward_compat():
    """
    slope_kind=None（指示書01時点のデフォルト）のとき、強化壁の集計が
    一切走らないこと（指示書01の再現性を壊していないこと）を確認する。
    """
    import random
    r = rs.simulate_trial(random.Random(9), N=4, serve_mode="inf", decay_on=True,
                           weakest_vanish=False, ticks=300)
    check("wall_backward_compat: slope_kind未指定ならwall_damage_totalは0",
          r["wall_damage_total"] == 0.0, f"got {r['wall_damage_total']}")
    check("wall_backward_compat: slope_kind未指定ならreinforced_hits_totalは0",
          r["reinforced_hits_total"] == 0, f"got {r['reinforced_hits_total']}")


def check_wall_damage_function():
    """wall_damage_for_level() の入出力を、代表値で直接検証する。"""
    check("wall_damage_fn: level=0は常に0ダメージ",
          rs.wall_damage_for_level(0, False, "linear") == 0.0)
    check("wall_damage_fn: linear level=3はBASE*3",
          rs.wall_damage_for_level(3, False, "linear") == rs.BASE_WALL_DMG * 3)
    check("wall_damage_fn: staged 未周回はSTAGE_SMALL",
          rs.wall_damage_for_level(1, False, "staged") == rs.STAGE_SMALL_WALL_DMG)
    check("wall_damage_fn: staged 周回済みはSTAGE_JUMP",
          rs.wall_damage_for_level(1, True, "staged") == rs.STAGE_JUMP_WALL_DMG)
    check("wall_damage_fn: STAGE_JUMPはSTAGE_SMALLより大きい（別格になる、の前提）",
          rs.STAGE_JUMP_WALL_DMG > rs.STAGE_SMALL_WALL_DMG)


def check_staged_slope_jumps_after_loop(inject_bug=False):
    """
    「跨ぎ1回で疑問が生まれ、一周で別格になる」という傾斜の設計意図が、
    staged実装で実際に成立しているかを、スクリプト化したシナリオで確認する。
    f_max=0.999, g_max=0.0 でN=3を数tickのうちに周回させ、周回前後で
    1ヒットあたりの平均ダメージが明確に増える（＝STAGE_JUMPへ切り替わる）ことを見る。
    """
    import random

    if inject_bug:
        # 過去に踏んだわけではないが、指示書02の新規ロジックに対する回帰テストとして、
        # has_looped判定を無視する壊れた実装を意図的に注入し、FAIL経路を実例で示す。
        orig_fn = rs.wall_damage_for_level

        def broken_wall_damage_for_level(level, has_looped, slope_kind):
            if slope_kind == "staged":
                return rs.STAGE_SMALL_WALL_DMG if level >= 1 else 0.0  # has_loopedを無視
            return orig_fn(level, has_looped, slope_kind)

        rs.wall_damage_for_level = broken_wall_damage_for_level
        try:
            r = rs.simulate_trial(random.Random(5), N=3, serve_mode="inf", decay_on=True,
                                   weakest_vanish=False, ticks=30, f_max=0.999, g_max=0.0,
                                   slope_kind="staged")
        finally:
            rs.wall_damage_for_level = orig_fn
    else:
        r = rs.simulate_trial(random.Random(5), N=3, serve_mode="inf", decay_on=True,
                               weakest_vanish=False, ticks=30, f_max=0.999, g_max=0.0,
                               slope_kind="staged")

    check("staged_slope: 周回が実際に発生している（前提条件）",
          len(r["loop_completions"]) >= 1, f"loop_completions={r['loop_completions'][:3]}")
    avg_dmg_per_hit = (r["wall_damage_total"] / r["reinforced_hits_total"]
                       if r["reinforced_hits_total"] else 0.0)
    # 周回が最速数tickで起きるシナリオなので、大半のヒットがSTAGE_JUMP側になり、
    # 平均ダメージはSTAGE_SMALLより明確に大きくなるはず
    check("staged_slope: 周回後は平均ダメージ/ヒットがSTAGE_SMALLより明確に大きい",
          avg_dmg_per_hit > rs.STAGE_SMALL_WALL_DMG * 1.5,
          f"avg_dmg_per_hit={avg_dmg_per_hit:.3f} STAGE_SMALL={rs.STAGE_SMALL_WALL_DMG}")


def check_checkpoint_and_wall_target_consistency():
    """
    checkpoints（tick→値）とwall_targets（値→tick）が、同一試行内で
    矛盾しないことを確認する。checkpoint時点の値以上のtickでのみ、
    その値に対応するwall_targetが到達しているはず。
    """
    import random
    ticks = 400
    cp = 200
    r = rs.simulate_trial(random.Random(11), N=4, serve_mode="inf", decay_on=True,
                           weakest_vanish=False, ticks=ticks, slope_kind="linear",
                           checkpoints=[cp], wall_targets=[])
    cp_damage = r["checkpoint_results"][cp]["wall_damage"]

    r2 = rs.simulate_trial(random.Random(11), N=4, serve_mode="inf", decay_on=True,
                            weakest_vanish=False, ticks=ticks, slope_kind="linear",
                            checkpoints=[], wall_targets=[cp_damage] if cp_damage > 0 else [])
    if cp_damage > 0:
        target_tick = r2["wall_target_ticks"].get(cp_damage)
        check("checkpoint_wall_target_consistency: checkpoint値と同じ乱数系列でwall_targetを引くと、"
              "到達tickがcheckpoint tick以下",
              target_tick is not None and target_tick <= cp,
              f"checkpoint={cp}(damage={cp_damage}) target_tick={target_tick}")
    else:
        check("checkpoint_wall_target_consistency: checkpoint時点でダメージ0（スキップ）", True)


def check_checkpoint_reaches_final_tick():
    """
    wall_sim.py実行時に実際に踏んだoff-by-oneバグの回帰テスト：
    checkpointがticksの最終値ちょうどの場合、range(ticks)はtick=ticks-1までしか
    回らないため「ticks==checkpointの呼び出しでは、そのcheckpointは記録されない」。
    wall_sim.py側はnatural_ticks=max(checkpoints)+1で回避しているが、simulate_trial
    自体の「tick==checkpointでのみ記録する」という仕様がそもそも一発で分かりにくい
    ため、呼び出し側がこの境界条件を正しく扱っているかをここで固定する。
    """
    import random
    # ticks==checkpointの場合：境界に到達しない（呼び出し側は+1が必要、という仕様の確認）
    r_bad = rs.simulate_trial(random.Random(3), N=4, serve_mode="inf", decay_on=True,
                               weakest_vanish=False, ticks=100, slope_kind="linear",
                               checkpoints=[100])
    check("checkpoint_boundary: ticks==checkpointだと、そのcheckpointは記録されない"
          "（呼び出し側はticks=checkpoint+1にする必要がある、という既知の仕様）",
          100 not in r_bad["checkpoint_results"],
          f"checkpoint_results keys={list(r_bad['checkpoint_results'].keys())}")

    # ticks==checkpoint+1の場合：正しく記録される（wall_sim.pyが実際に使っている回避策）
    r_good = rs.simulate_trial(random.Random(3), N=4, serve_mode="inf", decay_on=True,
                                weakest_vanish=False, ticks=101, slope_kind="linear",
                                checkpoints=[100])
    check("checkpoint_boundary: ticks==checkpoint+1なら、そのcheckpointが記録される",
          100 in r_good["checkpoint_results"],
          f"checkpoint_results keys={list(r_good['checkpoint_results'].keys())}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inject-population-bug", action="store_true",
                     help="過去に実在した越境流入バグを意図的に再現し、FAIL経路を実例で示す")
    ap.add_argument("--inject-wall-bug", action="store_true",
                     help="指示書02の強化壁ロジック(has_looped判定)を意図的に壊し、FAIL経路を実例で示す")
    args = ap.parse_args()

    check_ring_math()
    check_reproducibility()
    check_loop_detection_scripted()
    check_population_conservation(inject_bug=args.inject_population_bug)
    check_wall_backward_compat()
    check_wall_damage_function()
    check_staged_slope_jumps_after_loop(inject_bug=args.inject_wall_bug)
    check_checkpoint_and_wall_target_consistency()
    check_checkpoint_reaches_final_tick()

    if FAILURES:
        print(f"RESULT: FAIL ({len(FAILURES)} check(s) failed: {', '.join(FAILURES)})")
        sys.exit(1)
    else:
        print("RESULT: PASS")
        sys.exit(0)


if __name__ == "__main__":
    main()
