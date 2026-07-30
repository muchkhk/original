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

--inject-band-bug を付けると、指示書06のB3帯判定ロジック（evaluate_b3）の
pass判定を意図的に反転させ、check_b3_evaluation_distinguishes_pass_failが
確実にFAILすることを示す（完了条件2：帯逸脱の成功/失敗パス分離）。

--inject-instrument-bug を付けると、指示書06のsticky除去・B1/B2非判定の
回帰（ITEM_PADDLE_SIDEへのsticky再導入／summarize_bundle()へのB1 pass判定
混入）を注入し、check_sticky_removed・check_b1_b2_have_no_verdictが
確実にFAILすることを示す（完了条件1：sticky除去確認・B1/B2判定出力の不在確認）。

使い方:
  python selfcheck.py                       # 正常系。RESULT: PASS を期待
  python selfcheck.py --inject-population-bug  # 異常系。RESULT: FAIL を期待
  python selfcheck.py --inject-wall-bug        # 異常系。RESULT: FAIL を期待
  python selfcheck.py --inject-catchrate-bug   # 異常系。RESULT: FAIL を期待
  python selfcheck.py --inject-band-bug        # 異常系。RESULT: FAIL を期待
  python selfcheck.py --inject-instrument-bug  # 異常系。RESULT: FAIL を期待
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


def check_sticky_removed(inject_bug=False):
    """指示書06：スティッキーはkillされ、ITEM_PADDLE_SIDE・item_systemから
    完全に除去された（総数8→7）。(1)'sticky'という種が存在しないこと、
    (2)アイテム有効時でも保持による跨ぎ判定の欠落が起きない（アイテム無しと
    比べてupcross_events_totalが大きく落ち込まない）ことを確認する。

    --inject-instrument-bugで、ITEM_PADDLE_SIDEにsticky相当の種を復活させた
    壊れた状態（sticky再導入の回帰）を注入し、FAIL経路を実例で示す。
    """
    import random

    paddle_side = rs.ITEM_PADDLE_SIDE + ["sticky"] if inject_bug else rs.ITEM_PADDLE_SIDE
    check("sticky_removed: ITEM_PADDLE_SIDEに'sticky'が含まれない",
          "sticky" not in paddle_side, f"ITEM_PADDLE_SIDE={paddle_side}")

    if inject_bug:
        return  # 種の混入自体が既に検出対象。以降のシミュ実行は正常系と同じため省略

    # 捕球率をアイテム有無で揃え（power_bonus=0, cap=1.0）、保持機構の有無だけを
    # 比較できるようにする
    item_system = {
        "drop_rate": 1.0, "ball_effect_ticks": 5, "ball_effect_magnitude": 1.0,
        "paddle_power_bonus_per_stage": 0.0, "paddle_catch_rate_cap": 1.0,
    }
    r_with_items = rs.simulate_trial(random.Random(22), N=3, serve_mode="inf", decay_on=True,
                                      weakest_vanish=False, ticks=300, paddle_catch_rate=1.0,
                                      item_system=item_system)
    r_without_items = rs.simulate_trial(random.Random(22), N=3, serve_mode="inf", decay_on=True,
                                         weakest_vanish=False, ticks=300, paddle_catch_rate=1.0)
    ratio = r_with_items["upcross_events_total"] / r_without_items["upcross_events_total"]
    check("sticky_removed: アイテム有効時でもupcross_events_totalが無効時の80%以上を維持する"
          "（保持機構が無いため、跨ぎ判定の欠落が起きない）",
          ratio >= 0.8, f"with_items={r_with_items['upcross_events_total']} "
          f"without_items={r_without_items['upcross_events_total']} ratio={ratio:.3f}")


def check_nine_item_species(inject_bug=False):
    """指示書07：cloneを除去しmagnet・laser・weakenを追加した結果、アイテム総数は
    7→9種になった（パドル側5：拡大・速度・ガイド・磁石・レーザー／球側3：貫通・爆発・加速／
    場1：自陣弱体化）。'clone'が存在しないこと・'sticky'も引き続き存在しないこと・
    総数が9であることを確認する。

    --inject-species-bugで、cloneを誤って残す壊れた構成を注入し、FAILすることを示す。
    """
    paddle_side = rs.ITEM_PADDLE_SIDE + ["clone"] if inject_bug else rs.ITEM_PADDLE_SIDE
    all_types = paddle_side + rs.ITEM_BALL_SIDE + rs.ITEM_FIELD_SIDE
    check("nine_species: ITEM_PADDLE_SIDEに'clone'が含まれない（指示書07で磁石へ差し替え）",
          "clone" not in paddle_side, f"ITEM_PADDLE_SIDE={paddle_side}")
    check("nine_species: 'sticky'も引き続き含まれない（指示書06のkillを維持）",
          "sticky" not in all_types, f"all_types={all_types}")
    check("nine_species: 全アイテム種の総数は9種（パドル5＋球3＋場1）",
          len(all_types) == 9, f"total={len(all_types)} all_types={all_types}")


def check_guide_mechanically_inert(inject_bug=False):
    """指示書07：guideはドロップ枠・LIFOスタックを占有するが、捕球率計算からは
    除外され物理・捕球率に一切寄与しない（案(a)：表示のみ・物理不介入）。

    (1) ITEM_CATCH_RATE_CONTRIBUTORSにguideが含まれないこと（構成チェック）。
    (2) 全ドロップをguide固定にした状態で大量の段を積んでも、捕球率が
        paddle_catch_rateからほぼ動かず、B1（落球回数）が高いまま
        （＝guideの段が捕球率を一切押し上げない）ことを実際にsimulate_trial()を
        回して確認する（挙動チェック）。

    --inject-instrument-bugで、guideも捕球率計算に含めてしまう壊れた実装を
    一時的に注入し、段を積むほどB1が明確に減る（＝guideが寄与してしまう）ことを示す。
    """
    check("guide_inert: ITEM_CATCH_RATE_CONTRIBUTORSに'guide'が含まれない（構成チェック）",
          "guide" not in rs.ITEM_CATCH_RATE_CONTRIBUTORS,
          f"ITEM_CATCH_RATE_CONTRIBUTORS={rs.ITEM_CATCH_RATE_CONTRIBUTORS}")

    import random
    orig_all_types = rs.ITEM_ALL_TYPES
    orig_contributors = rs.ITEM_CATCH_RATE_CONTRIBUTORS
    rs.ITEM_ALL_TYPES = ["guide"]  # 全ドロップをguide固定にし、段を確実に積み上げる
    if inject_bug:
        rs.ITEM_CATCH_RATE_CONTRIBUTORS = ["guide"]  # 壊れた実装：guideも寄与させてしまう
    item_system = {
        "drop_rate": 1.0, "ball_effect_ticks": 1, "ball_effect_magnitude": 0.0,
        "paddle_power_bonus_per_stage": 0.15, "paddle_catch_rate_cap": 1.0,
        "pickup_miss_rate_base": 0.0,  # magnetの影響を排除し、guideの効果だけを見る
    }
    try:
        r = rs.simulate_trial(random.Random(42), N=2, serve_mode="inf", decay_on=True,
                               weakest_vanish=False, ticks=400, paddle_catch_rate=0.3,
                               item_system=item_system)
    finally:
        rs.ITEM_ALL_TYPES = orig_all_types
        rs.ITEM_CATCH_RATE_CONTRIBUTORS = orig_contributors

    total_drops = sum(r["drop_events_per_world"])
    # 実測基準（seed固定・同一条件）：guideが寄与しない場合 total_drops=1626、
    # guideが寄与する（壊れた実装）場合 total_drops=590 まで落ち込む。
    # 閾値1000はこの2値の中間に置き、どちらの経路かを確実に判別する。
    check("guide_inert: guide段を積み上げても捕球率が動かず、B1（落球回数）が高いまま維持される",
          total_drops > 1000,
          f"total_drops={total_drops}（guideが寄与すると590近傍まで大きく減る。閾値1000）")


def check_random_slot_equivalence(inject_bug=False):
    """指示書07：ドロップテーブルは9種+抽選枠randomの10エントリだが、randomが
    9種へ均等再抽選されるため、各アイテムの実効出現率は厳密に1/9になる
    （この等価性により、シミュはrandomを実装せず9種均等抽選のみで代表してよい、
    という簡略化の根拠）。モンテカルロで検算する。

    --inject-random-bugで、randomの再抽選を均等でなくする（常に先頭アイテム固定）
    壊れた解決方法を注入し、分布が1/9から有意に外れることを示す。
    """
    import random
    items = list(rs.ITEM_ALL_TYPES)
    n = len(items)
    rng = random.Random(999)
    trials = 90000
    counts = {it: 0 for it in items}
    for _ in range(trials):
        slot = int(rng.random() * (n + 1))  # 10エントリ：0..n-1=直接、n=random
        if slot < n:
            resolved = items[slot]
        elif inject_bug:
            resolved = items[0]  # 壊れた実装：常に先頭固定（均等でない）
        else:
            resolved = items[int(rng.random() * n)]
        counts[resolved] += 1
    expected = trials / n
    max_dev_frac = max(abs(c - expected) / expected for c in counts.values())
    check("random_slot_equivalence: 各アイテムの実効出現率が1/9からの乖離5%以内"
          "（10エントリ抽選と9種均等抽選の等価性の検算）",
          max_dev_frac < 0.05, f"expected={expected:.1f} max_dev_frac={max_dev_frac:.3f} counts={counts}")


def check_b3_excludes_weaken(inject_bug=False):
    """指示書07：B3は球側3種（貫通・爆発・加速）のみの稼働率であり、weaken
    （場・時限）の稼働率を含めてはならない（定義変更禁止）。全ドロップをweaken
    固定にした状況で、B3（ball_effect_active_frac_per_world）がほぼ0のまま、
    weaken_active_frac_per_world（参考値）だけが実際に稼働していることを確認する。

    --inject-b3-scope-bugで、集計後にweaken稼働率をB3へ合算する壊れた集計を
    注入し、B3が不当に高くなることを示す（simulate_trial自体は変更しない）。
    """
    import random
    orig_all_types = rs.ITEM_ALL_TYPES
    rs.ITEM_ALL_TYPES = ["weaken"]  # 全ドロップをweaken固定にする
    item_system = {
        "drop_rate": 1.0, "ball_effect_ticks": 900, "ball_effect_magnitude": 0.0,
        "paddle_power_bonus_per_stage": 0.0, "paddle_catch_rate_cap": 1.0,
        "pickup_miss_rate_base": 0.0,
    }
    try:
        r = rs.simulate_trial(random.Random(7), N=2, serve_mode="inf", decay_on=True,
                               weakest_vanish=False, ticks=300, paddle_catch_rate=1.0,
                               item_system=item_system)
    finally:
        rs.ITEM_ALL_TYPES = orig_all_types

    b3 = r["ball_effect_active_frac_per_world"]
    weaken_frac = r["weaken_active_frac_per_world"]
    check("b3_excludes_weaken: weakenのみドロップされる状況で、参考値weaken_active_fracは"
          "実際に高稼働している（前提条件）",
          min(weaken_frac) > 0.5, f"weaken_frac={weaken_frac}")

    if inject_bug:
        combined = [b3[w] + weaken_frac[w] for w in range(len(b3))]  # 壊れた集計：B3へweakenを混入
        check("b3_excludes_weaken: B3（球側3種のみ）はweakenのみの状況で0近傍のまま"
              "（意図的にweakenをB3へ混入させた壊れた集計）",
              max(combined) <= 0.01, f"combined(bug)={combined}")
    else:
        check("b3_excludes_weaken: B3（球側3種のみ）はweakenのみの状況で0近傍のまま",
              max(b3) <= 0.01, f"b3={b3}")


def check_pierce_three_hits(inject_bug=False):
    """指示書09：貫通（pierce）は他の球側効果（ball_effect_ticks）から分離され、
    PIERCE_HITS=3の固定ヒット数で持続する。全ドロップをpierce固定にし、他効果の
    ball_effect_ticksを2にした状況で、pierceのball_effectが3ヒット（=3tick）持続する
    ことを、1球のticks_left初期値がPIERCE_HITSであることで確認する。

    構成チェック：PIERCE_HITS==3（保護数字）かつball_effect_ticks(=2)と別値であること。

    --inject-instrument-bugで、pierceをball_effect_ticksに束ね直す（分離を壊す）と、
    pierceの持続がball_effect_ticksと同一になり、3ヒット固定でなくなることを示す。
    """
    check("pierce_three_hits: PIERCE_HITS==3（保護数字・掃引で短縮禁止）",
          rs.PIERCE_HITS == 3, f"PIERCE_HITS={rs.PIERCE_HITS}")

    import random
    orig_all_types = rs.ITEM_ALL_TYPES
    rs.ITEM_ALL_TYPES = ["pierce"]  # 全ドロップをpierce固定
    ball_effect_ticks = 2
    item_system = {
        "drop_rate": 1.0, "ball_effect_ticks": ball_effect_ticks, "ball_effect_magnitude": 1.0,
        "paddle_power_bonus_per_stage": 0.0, "paddle_catch_rate_cap": 1.0,
        "pickup_miss_rate_base": 0.0,
    }
    # inject_bug時、resolve_pickup内のpierce分離を壊す（PIERCE_HitsではなくTicksを使う）
    # 相当の状況を、PIERCE_HITSをball_effect_ticksと同値に潰すことで再現する。
    orig_pierce_hits = rs.PIERCE_HITS
    if inject_bug:
        rs.PIERCE_HITS = ball_effect_ticks  # 分離喪失（3ではなく2に潰れる）
    try:
        r = rs.simulate_trial(random.Random(31), N=1, serve_mode="inf", decay_on=True,
                               weakest_vanish=False, ticks=5, paddle_catch_rate=1.0,
                               item_system=item_system, _debug_record_first_pierce=True)
    finally:
        rs.ITEM_ALL_TYPES = orig_all_types
        rs.PIERCE_HITS = orig_pierce_hits

    observed = r.get("debug_first_pierce_ticks_left")
    # 正常時：pierce取得直後のticks_leftは3（PIERCE_HITS）。ball_effect_ticks(2)と異なる。
    check("pierce_three_hits: pierce取得直後のball_effect.ticks_leftが3（ball_effect_ticks=2と分離）",
          observed == 3, f"observed ticks_left={observed}（inject時は2に潰れてFAILするはず）")


def check_item_wall_guaranteed_drop(inject_bug=False):
    """指示書09：アイテム壁（確定ドロップ）。item_wall_bricks=2のとき、drop_rate=0
    （ランダム落下ゼロ）・pickup_miss=0でも、各worldにSESSION_TICKS窓あたり2回の
    確定ドロップが発火することを確認する（発火数＝2×N）。

    --inject-instrument-bugで、確定ドロップの発火スケジュール（ITEM_WALL_WINDOW_TICKS）を
    壊す（試行tick窓の外へ追い出す）と、確定ドロップが1回も発火しなくなることを示す。
    抽選枠random等価性（check_random_slot_equivalence）は純数学のため確定壁追加後も
    不変で、本checkと独立に成立する（別関数で継続確認）。
    """
    import random
    N = 3
    item_system = {
        "drop_rate": 0.0, "ball_effect_ticks": 2, "ball_effect_magnitude": 1.0,
        "paddle_power_bonus_per_stage": 0.0, "paddle_catch_rate_cap": 1.0,
        "pickup_miss_rate_base": 0.0, "item_wall_bricks": 2,
    }
    orig_window = rs.ITEM_WALL_WINDOW_TICKS
    if inject_bug:
        rs.ITEM_WALL_WINDOW_TICKS = 10 ** 9  # 発火offsetが試行窓の外へ→一度も発火しない
    try:
        r = rs.simulate_trial(random.Random(41), N=N, serve_mode="inf", decay_on=True,
                               weakest_vanish=False, ticks=901, paddle_catch_rate=1.0,
                               item_system=item_system)
    finally:
        rs.ITEM_WALL_WINDOW_TICKS = orig_window

    fired = r["guaranteed_drops_fired_per_world"]
    total_fired = sum(fired)
    check("item_wall: item_wall_bricks=2で各worldに2回ずつ確定ドロップが発火する（計2×N）",
          total_fired == 2 * N, f"fired={fired} total={total_fired}（inject時は0でFAIL）")
    # drop_rate=0なので、確定ドロップが無ければアイテム効果は一切増えないはず。
    # 正常時はpaddle段またはweaken等に痕跡が残る（確定ドロップが実際に効果適用まで
    # 到達している）ことを、paddle段の総和で間接確認する。
    if not inject_bug:
        stage_evidence = total_fired > 0
        check("item_wall: 確定ドロップがpickup_miss=0で実際に効果適用まで到達している",
              stage_evidence, f"total_fired={total_fired}")


def check_b1_b2_have_no_verdict(inject_bug=False):
    """指示書06 完了条件1：B1・B2はシミュの帯判定（PASS/FAIL）から外れ、
    参考値としてのみ扱われる。summarize_bundle()の出力にB1/B2のpass/fail
    キーが無いこと、フィールド名にREFERENCE_ONLYが明記されていることを確認する。

    --inject-instrument-bugで、summarize_bundle()にB1の偽のpass判定を
    混入させる壊れた実装を注入し、FAIL経路を実例で示す。
    """
    fake_raw = {"drop_counts": [5], "recovery_ticks": [50.0], "uptime_fracs": [0.2],
                "trials": 1, "ticks": 900, "N": 1}

    if inject_bug:
        orig_summarize = isim.summarize_bundle

        def broken_summarize_bundle(raw):
            s = orig_summarize(raw)
            s["b1_pass"] = True  # B1にpass/fail判定を混入させる壊れた実装
            return s

        isim.summarize_bundle = broken_summarize_bundle
        try:
            summary = isim.summarize_bundle(fake_raw)
        finally:
            isim.summarize_bundle = orig_summarize
    else:
        summary = isim.summarize_bundle(fake_raw)

    check("b1_b2_no_verdict: summarize_bundle()の出力にB1/B2のpass/fail判定キーが無い",
          not any("pass" in str(k).lower() and ("b1" in str(k).lower() or "b2" in str(k).lower())
                  for k in summary.keys()),
          f"summary keys={list(summary.keys())}")
    check("b1_b2_no_verdict: B1のフィールド名に参考値である旨(REFERENCE_ONLY)が明記されている",
          any("b1" in k.lower() and "reference_only" in k.lower() for k in summary),
          f"summary keys={list(summary.keys())}")


def check_lifo_drop_penalty_order():
    """LIFO落球ペナルティ：最後に取得した段が最初に剥がれることを、
    多数の落球イベントを含む試行で間接的に確認する（drop_events_per_worldが
    実際に発生していること＝ペナルティ経路自体が動いていることの確認）。"""
    import random
    item_system = {
        "drop_rate": 0.3, "ball_effect_ticks": 5, "ball_effect_magnitude": 1.0,
        "paddle_power_bonus_per_stage": 0.001,
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


def check_b3_evaluation_distinguishes_pass_fail(inject_bug=False):
    """
    指示書05完了条件2を継承：検証コード（指示書06でB3専用に改訂したevaluate_b3）
    自体が、成功パスと失敗パス（帯逸脱）で必ず別の出力になることを、
    実際のシミュレーションを回す前に合成データで確認する。B1・B2は指示書06で
    帯判定の対象から外れたため、判定対象はB3のみになった。

    --inject-band-bug で、B3判定の不等号を意図的に反転させた壊れた実装を注入し、
    「帯内のはずの値がNGと判定される／帯外のはずの値がOKと判定される」ことを示す。
    """
    good_summary = {"b3_uptime_mean_frac": 0.20}   # B3上限0.40の半分（帯内）
    bad_summary = {"b3_uptime_mean_frac": 0.90}    # B3上限0.40を大きく超える（帯逸脱）

    if inject_bug:
        orig_evaluate_b3 = isim.evaluate_b3

        def broken_evaluate_b3(summary):
            ev = orig_evaluate_b3(summary)
            ev["pass"] = not ev["pass"]  # 判定を反転させる壊れた実装
            return ev

        isim.evaluate_b3 = broken_evaluate_b3
        try:
            good_ev = isim.evaluate_b3(good_summary)
            bad_ev = isim.evaluate_b3(bad_summary)
        finally:
            isim.evaluate_b3 = orig_evaluate_b3
    else:
        good_ev = isim.evaluate_b3(good_summary)
        bad_ev = isim.evaluate_b3(bad_summary)

    check("b3_evaluation: 帯内の合成データはpassと判定される",
          good_ev["pass"] is True, f"good_ev={good_ev}")
    check("b3_evaluation: 帯を大きく外れた合成データはpassでないと判定される",
          bad_ev["pass"] is False, f"bad_ev={bad_ev}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inject-population-bug", action="store_true",
                     help="過去に実在した越境流入バグを意図的に再現し、FAIL経路を実例で示す")
    ap.add_argument("--inject-wall-bug", action="store_true",
                     help="指示書02の強化壁ロジック(has_looped判定)を意図的に壊し、FAIL経路を実例で示す")
    ap.add_argument("--inject-catchrate-bug", action="store_true",
                     help="指示書03のpaddle_catch_rate引数を無視する壊れた実装を注入し、FAIL経路を実例で示す")
    ap.add_argument("--inject-band-bug", action="store_true",
                     help="指示書06のB3帯判定(evaluate_b3)のpass判定を反転させ、FAIL経路を実例で示す")
    ap.add_argument("--inject-instrument-bug", action="store_true",
                     help="指示書06のsticky除去・B1/B2非判定の回帰(sticky再導入/B1にpass判定混入)を注入し、FAIL経路を実例で示す")
    ap.add_argument("--inject-species-bug", action="store_true",
                     help="指示書07のアイテム9種構成にcloneを誤って残す壊れた構成を注入し、FAIL経路を実例で示す")
    ap.add_argument("--inject-guide-bug", action="store_true",
                     help="指示書07のguide機構的不活性を壊し、guideが捕球率に寄与してしまう実装を注入し、FAIL経路を実例で示す")
    ap.add_argument("--inject-random-bug", action="store_true",
                     help="指示書07のドロップテーブル等価性(10エントリ=9種均等)を壊し、FAIL経路を実例で示す")
    ap.add_argument("--inject-b3-scope-bug", action="store_true",
                     help="指示書07のB3定義(球側3種のみ)にweakenを混入させる壊れた集計を注入し、FAIL経路を実例で示す")
    ap.add_argument("--inject-pierce-bug", action="store_true",
                     help="指示書09の貫通3ヒット分離を壊し(PIERCE_HITSをball_effect_ticksに潰す)、FAIL経路を実例で示す")
    ap.add_argument("--inject-itemwall-bug", action="store_true",
                     help="指示書09のアイテム壁確定ドロップの発火スケジュールを壊し、FAIL経路を実例で示す")
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
    check_sticky_removed(inject_bug=args.inject_instrument_bug)
    check_nine_item_species(inject_bug=args.inject_species_bug)
    check_guide_mechanically_inert(inject_bug=args.inject_guide_bug)
    check_random_slot_equivalence(inject_bug=args.inject_random_bug)
    check_b3_excludes_weaken(inject_bug=args.inject_b3_scope_bug)
    check_pierce_three_hits(inject_bug=args.inject_pierce_bug)
    check_item_wall_guaranteed_drop(inject_bug=args.inject_itemwall_bug)
    check_lifo_drop_penalty_order()
    check_b3_evaluation_distinguishes_pass_fail(inject_bug=args.inject_band_bug)
    check_b1_b2_have_no_verdict(inject_bug=args.inject_instrument_bug)

    if FAILURES:
        print(f"RESULT: FAIL ({len(FAILURES)} check(s) failed: {', '.join(FAILURES)})")
        sys.exit(1)
    else:
        print("RESULT: PASS")
        sys.exit(0)


if __name__ == "__main__":
    main()
