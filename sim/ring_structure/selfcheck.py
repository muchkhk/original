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

--inject-catchrate-bug を付けると、指示書03の新規ロジック（run_relayの
paddle_catch_rate引数）を意図的に無視する壊れた実装を注入し、
check_catch_rate_slows_relayが確実にFAILすることを示す。

--inject-band-bug を付けると、指示書05の帯判定ロジック（evaluate_bands）の
B1判定を意図的に反転させ、check_band_evaluation_distinguishes_pass_failが
確実にFAILすることを示す（完了条件2：帯逸脱の成功/失敗パス分離）。

使い方:
  python selfcheck.py                       # 正常系。RESULT: PASS を期待
  python selfcheck.py --inject-population-bug  # 異常系。RESULT: FAIL を期待
  python selfcheck.py --inject-wall-bug        # 異常系。RESULT: FAIL を期待
  python selfcheck.py --inject-catchrate-bug   # 異常系。RESULT: FAIL を期待
  python selfcheck.py --inject-band-bug        # 異常系。RESULT: FAIL を期待
"""
import argparse
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")

import ring_sim as rs
import wall_sim as ws
import item_sim as isim

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


def check_catch_rate_slows_relay(inject_bug=False):
    """
    指示書03の新規ロジック：wall_sim.run_relay()にpaddle_catch_rateを渡せること、
    かつ捕球率が下がるほどリレーのクリアが明確に遅くなる（または到達率が下がる）
    ことを確認する。捕球率が下がれば球を落とす頻度が増え、周回・強化壁ダメージの
    蓄積が遅くなるはず、という単調性のチェック。
    """
    target = 5000.0
    trials, ticks = 60, 2000

    if inject_bug:
        # paddle_catch_rate引数を黙って無視し、常に1.0扱いする壊れた実装を注入する
        orig_simulate_trial = ws.simulate_trial

        def broken_simulate_trial(*args, **kwargs):
            kwargs["paddle_catch_rate"] = 1.0
            return orig_simulate_trial(*args, **kwargs)

        ws.simulate_trial = broken_simulate_trial
        try:
            fast = ws.run_relay(101, "staged", [target], trials, ticks, paddle_catch_rate=1.0)
            slow = ws.run_relay(101, "staged", [target], trials, ticks, paddle_catch_rate=0.5)
        finally:
            ws.simulate_trial = orig_simulate_trial
    else:
        fast = ws.run_relay(101, "staged", [target], trials, ticks, paddle_catch_rate=1.0)
        slow = ws.run_relay(101, "staged", [target], trials, ticks, paddle_catch_rate=0.5)

    fast_med, slow_med = fast[target]["median_tick"], slow[target]["median_tick"]
    fast_reached, slow_reached = fast[target]["reached_frac"], slow[target]["reached_frac"]
    slower_or_worse = (
        (slow_med is None and fast_med is not None) or
        (fast_med is not None and slow_med is not None and slow_med > fast_med) or
        (slow_reached < fast_reached)
    )
    check("catch_rate_slows_relay: catch_rate=0.5はcatch_rate=1.0より明確に遅い（または到達率が下がる）",
          slower_or_worse,
          f"fast_median={fast_med}(reached={fast_reached}) slow_median={slow_med}(reached={slow_reached})")


def check_item_backward_compat():
    """item_system=None（指示書01〜04時点のデフォルト）のとき、アイテム関連の
    集計キーが結果に一切現れないこと（既存の再現性を壊していないこと）を確認する。"""
    import random
    r = rs.simulate_trial(random.Random(21), N=4, serve_mode="inf", decay_on=True,
                           weakest_vanish=False, ticks=300)
    check("item_backward_compat: item_system未指定ならdrop_events_per_worldが無い",
          "drop_events_per_world" not in r, f"keys={list(r.keys())}")


def check_sticky_holds_ball():
    """スティッキー保持中(hold_ticks>0)の球は、ブロック/パドルフェーズの処理を
    受けない（跨ぎ判定そのものを試行しない）ことを、sticky有効/無効の比較で確認する。
    drop_rate=1.0・catch_rate=1.0にして毎ヒットでsticky段が付き、以後の捕球すべてで
    保持が発生するようにすると、sticky有効時は無効時よりupcross_events_totalが
    明確に少なくなるはず（保持中は跨ぎ判定自体が走らないため）。"""
    import random
    item_system = {
        "drop_rate": 1.0, "ball_effect_ticks": 5, "ball_effect_magnitude": 1.0,
        "sticky_hold_ticks_range": (3, 3),
    }
    item_system_off = dict(item_system, sticky_enabled=False)
    r_on = rs.simulate_trial(random.Random(22), N=3, serve_mode="inf", decay_on=True,
                              weakest_vanish=False, ticks=300, paddle_catch_rate=1.0,
                              item_system=item_system)
    r_off = rs.simulate_trial(random.Random(22), N=3, serve_mode="inf", decay_on=True,
                               weakest_vanish=False, ticks=300, paddle_catch_rate=1.0,
                               item_system=item_system_off)
    check("sticky_holds_ball: sticky有効時はupcross_events_totalが無効時より明確に少ない"
          "（保持中は跨ぎ判定を試行しないため）",
          r_on["upcross_events_total"] < r_off["upcross_events_total"] * 0.5,
          f"on={r_on['upcross_events_total']} off={r_off['upcross_events_total']}")


def check_lifo_drop_penalty_order():
    """LIFO落球ペナルティ：最後に取得した段が最初に剥がれることを、
    多数の落球イベントを含む試行で間接的に確認する（drop_events_per_worldが
    実際に発生していること＝ペナルティ経路自体が動いていることの確認）。"""
    import random
    item_system = {
        "drop_rate": 0.3, "ball_effect_ticks": 5, "ball_effect_magnitude": 1.0,
        "sticky_hold_ticks_range": (2, 4), "paddle_power_bonus_per_stage": 0.001,
    }
    r = rs.simulate_trial(random.Random(23), N=3, serve_mode="inf", decay_on=True,
                           weakest_vanish=False, ticks=900, paddle_catch_rate=0.5,
                           item_system=item_system)
    total_drops = sum(r["drop_events_per_world"])
    total_recoveries = sum(len(x) for x in r["recovery_times_per_world"])
    check("lifo_drop_penalty: 低catch_rateで落球イベントが実際に発生する",
          total_drops > 0, f"total_drops={total_drops}")
    check("lifo_drop_penalty: 喪失後に同アイテムを再取得する経路（回復）も発生する",
          total_recoveries > 0, f"total_recoveries={total_recoveries}")


def check_band_evaluation_distinguishes_pass_fail(inject_bug=False):
    """
    指示書05 完了条件2：検証コード（evaluate_bands）自体が、成功パスと失敗パス
    （帯逸脱）で必ず別の出力になることを、実際のシミュレーションを回す前に
    合成データで確認する。

    --inject-band-bug で、B1判定の不等号を意図的に反転させた壊れた実装を注入し、
    「帯内のはずの値がNGと判定される／帯外のはずの値がOKと判定される」ことを示す。
    """
    good_summary = {
        "b1_drop_mean": 5.0,            # B1帯[2,10]の中央付近
        "b2_recovery_mean_ticks": 45.0, # B2帯の中央付近
        "b3_uptime_mean_frac": 0.20,    # B3上限0.40の半分
    }
    bad_summary = {
        "b1_drop_mean": 500.0,          # B1帯を大きく超える（明確な帯逸脱）
        "b2_recovery_mean_ticks": 45.0,
        "b3_uptime_mean_frac": 0.20,
    }

    if inject_bug:
        orig_evaluate_bands = isim.evaluate_bands

        def broken_evaluate_bands(summary):
            ev = orig_evaluate_bands(summary)
            ev["B1"]["pass"] = not ev["B1"]["pass"]  # 判定を反転させる壊れた実装
            return ev

        isim.evaluate_bands = broken_evaluate_bands
        try:
            good_ev = isim.evaluate_bands(good_summary)
            bad_ev = isim.evaluate_bands(bad_summary)
        finally:
            isim.evaluate_bands = orig_evaluate_bands
    else:
        good_ev = isim.evaluate_bands(good_summary)
        bad_ev = isim.evaluate_bands(bad_summary)

    check("band_evaluation: 帯内の合成データはB1が'pass'と判定される",
          good_ev["B1"]["pass"] is True, f"good_ev.B1={good_ev['B1']}")
    check("band_evaluation: 帯を大きく外れた合成データはB1が'pass'でないと判定される",
          bad_ev["B1"]["pass"] is False, f"bad_ev.B1={bad_ev['B1']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inject-population-bug", action="store_true",
                     help="過去に実在した越境流入バグを意図的に再現し、FAIL経路を実例で示す")
    ap.add_argument("--inject-wall-bug", action="store_true",
                     help="指示書02の強化壁ロジック(has_looped判定)を意図的に壊し、FAIL経路を実例で示す")
    ap.add_argument("--inject-catchrate-bug", action="store_true",
                     help="指示書03のpaddle_catch_rate引数を無視する壊れた実装を注入し、FAIL経路を実例で示す")
    ap.add_argument("--inject-band-bug", action="store_true",
                     help="指示書05の帯判定(evaluate_bands)のB1判定を反転させ、FAIL経路を実例で示す")
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
    check_catch_rate_slows_relay(inject_bug=args.inject_catchrate_bug)
    check_item_backward_compat()
    check_sticky_holds_ball()
    check_lifo_drop_penalty_order()
    check_band_evaluation_distinguishes_pass_fail(inject_bug=args.inject_band_bug)

    if FAILURES:
        print(f"RESULT: FAIL ({len(FAILURES)} check(s) failed: {', '.join(FAILURES)})")
        sys.exit(1)
    else:
        print("RESULT: PASS")
        sys.exit(0)


if __name__ == "__main__":
    main()
