#!/usr/bin/env python3
"""
思考課金ポーカー v0-C（v0-Bの経済 ＋ FB二山土台 ＋ 覗きのみ）モンテカルロ検証シミュレータ

指示書: 思考課金ポーカー v0-C シミュレータ実装・実行・機械判定 v1
事前登録: sim/thinking_tax/事前登録_v0C_kill基準.md（実行前に凍結・単独コミット済み）

v0-B (simulator_v0b.py) をベースに、指示書で明記された変更点のみを適用する：
  (a) 土台の差し替え(FB二山、唯一の設計変更)
  (b) 素朴推定(EV計算)をFB混合分布向けに更新
  (c) 晒しアクションの削除
  (d) それ以外(覗きの価格・回数制限・公開性、エスカレーティング・アンテ、プール25、
      レイズ単位、タイムアウト条件)はv0-B据え置き
  (e) 戦略ロスターをS1/S2/S3/S4'/S6/S10の6本に差し替え(S1・S10はv0-B実装据え置き)
  (f) 判断転換率の計測装置(覗き直前の最適行動 vs 覗き後の実際の行動)
  (g) 乱数シード固定・記録

「ディール」の単位は事前登録文書内で不整合があったため、design chat/人間に確認のうえ
「1ラウンド＝1回の手札配り」として実装した(詳細は報告に記載)。
"""

import csv
import json
import math
import os
import random
import time
from functools import lru_cache
from itertools import combinations

# ---------------------------------------------------------------------------
# ノブ (v0-Bから不変。変更禁止 = 判別実験の純度の条件)
# ---------------------------------------------------------------------------

KNOBS = {
    "pool_start": 25,
    "raise_cap": 4,
    "peek_cost": 2,
    "max_rounds": 30,
}

ANTE_SCHEDULE = [(4, 1), (8, 2), (12, 4), (float("inf"), 6)]


def ante_for_round(round_num):
    for last_round, amt in ANTE_SCHEDULE:
        if round_num <= last_round:
            return amt
    return ANTE_SCHEDULE[-1][1]


def bet_unit_for_round(round_num):
    return max(ante_for_round(round_num), 2)


BASE_SEED = 20260716  # 指示書発行日
ROUNDS_PER_PAIR_MIN = 10_000  # n要件(事前登録文書「n要件」節)
K2_MIN_PEEK_N = 500

CALL_MARGIN = 0.05  # v0-A/v0-Bを踏襲(指示書に明記が無いため独自設定。据え置き対象の一部)

# ---------------------------------------------------------------------------
# FB(二山)土台
# ---------------------------------------------------------------------------

FB_H_DECK = (4, 5, 6, 7, 8, 9)
FB_L_DECK = (1, 2, 3, 4, 5, 6)
FB_DECKS = {"H": FB_H_DECK, "L": FB_L_DECK}


def deal_hand_fb(rng):
    """1プレイヤー分の(山名, 手札3枚)を返す。50/50独立抽選・毎ラウンド再抽選。"""
    deck_name = rng.choice(("H", "L"))
    hand = tuple(rng.sample(FB_DECKS[deck_name], 3))
    return deck_name, hand


def fb_deck_posterior(known_values):
    """既知の相手手札値(累積)から、相手の山がH/Lである事後確率を返す。
    事前50/50、両山とも6値中3枚のドローなので、既知値がどちらの山にも
    含まれうる限り事後は50/50のまま動かず、片方の山にしか無い値が
    1つでも観測された瞬間に100%/0%へ飛ぶ(構造上、中間値は生じない)。"""
    known = set(known_values)
    legal_h = known.issubset(FB_H_DECK)
    legal_l = known.issubset(FB_L_DECK)
    if legal_h and legal_l:
        return 0.5, 0.5
    if legal_h:
        return 1.0, 0.0
    if legal_l:
        return 0.0, 1.0
    return 0.5, 0.5  # 到達しないはず(両山とも矛盾は起こりえない)


@lru_cache(maxsize=None)
def _fb_opponent_sum_distribution(known_values):
    """known_values: 相手の手札のうち既知の値のfrozenset。
    戻り値: {sum: probability} 相手の手札合計の分布(H/L 50/50混合で厳密周辺化)。"""
    known = set(known_values)
    scenarios = []
    for deck_values in (FB_H_DECK, FB_L_DECK):
        if not known.issubset(deck_values):
            continue
        remaining = [v for v in deck_values if v not in known]
        need = 3 - len(known)
        base = sum(known)
        if need == 0:
            scenarios.append((0.5, base))
            continue
        combos = list(combinations(remaining, need))
        w = 0.5 / len(combos)
        for c in combos:
            scenarios.append((w, base + sum(c)))
    total_w = sum(w for w, _ in scenarios)
    dist = {}
    for w, s in scenarios:
        dist[s] = dist.get(s, 0.0) + w
    for s in dist:
        dist[s] /= total_w
    return dist


def fb_win_tie_lose(my_sum, known_opponent_values):
    dist = _fb_opponent_sum_distribution(frozenset(known_opponent_values))
    win = tie = lose = 0.0
    for s, p in dist.items():
        if my_sum > s:
            win += p
        elif my_sum == s:
            tie += p
        else:
            lose += p
    return win, tie, lose


def fb_naive_win_prob(my_sum, known_opponent_values):
    """(b) 素朴推定の更新: 相手手札は H/L 50/50混合分布から3枚、という無料推定。"""
    w, t, _ = fb_win_tie_lose(my_sum, known_opponent_values)
    return w + 0.5 * t


# ---------------------------------------------------------------------------
# 補助データ構造 (v0-B据え置き。revealedは常に空集合=(c)晒し削除の結果)
# ---------------------------------------------------------------------------


class PlayerRoundState:
    __slots__ = ("hand", "deck", "revealed", "peeked_known")

    def __init__(self, hand, deck):
        self.hand = hand
        self.deck = deck  # 'H'/'L' (情報用。勝敗計算には使わない = 手札合計のみで決まる)
        self.revealed = set()  # 常に空(晒し削除のため)。known_of_opponentとの互換のため残す
        self.peeked_known = set()

    @property
    def hand_sum(self):
        return sum(self.hand)

    def known_of_opponent(self, opp_revealed):
        return self.peeked_known | opp_revealed


class DecisionContext:
    def __init__(self, rng, me, opp, me_pool, opp_pool, pot, call_amount,
                 bet_level, raises_used, round_history, facing_bet, bet_unit=2):
        self.rng = rng
        self.me = me
        self.opp = opp
        self.me_pool = me_pool
        self.opp_pool = opp_pool
        self.pot = pot
        self.call_amount = call_amount
        self.bet_level = bet_level
        self.raises_used = raises_used
        self.round_history = round_history
        self.facing_bet = facing_bet
        self.bet_unit = bet_unit

    def naive_win_prob(self, extra_known=frozenset()):
        known = self.me.known_of_opponent(self.opp.revealed) | extra_known
        return fb_naive_win_prob(self.me.hand_sum, known)


# ---------------------------------------------------------------------------
# ベッティング判断の共通ロジック (v0-B据え置き・無変更)
# ---------------------------------------------------------------------------


def baseline_bet_decision(win_prob, facing_bet, call_amount, pot, raises_used, threshold_adjust=0.0):
    if not facing_bet:
        return "bet" if win_prob > 0.55 else "check"
    if win_prob > 0.55:
        return "raise" if raises_used < KNOBS["raise_cap"] else "call"
    breakeven = call_amount / (pot + call_amount) if (pot + call_amount) > 0 else 1.0
    threshold = breakeven + CALL_MARGIN + threshold_adjust
    return "call" if win_prob > threshold else "fold"


def no_assist(ctx):
    return ("none", None)


# ---------------------------------------------------------------------------
# 戦略定義 (事前登録文書のロスターに一致させる)
# ---------------------------------------------------------------------------

# --- S1: 覗かない素朴EV追従 (v0-B実装据え置き。素朴推定のみFB向けに差し替わる) ---

def s1_assist(ctx):
    return no_assist(ctx)


def s1_bet(ctx):
    wp = ctx.naive_win_prob()
    return baseline_bet_decision(wp, ctx.facing_bet, ctx.call_amount, ctx.pot, ctx.raises_used)


# --- S2: 常時覗き (ルール・資金上可能な機会すべてで覗く。facing_bet不問) ---

def s2_assist(ctx):
    if ctx.me_pool >= KNOBS["peek_cost"]:
        return ("peek", None)
    return no_assist(ctx)


def s2_bet(ctx):
    wp = ctx.naive_win_prob()
    return baseline_bet_decision(wp, ctx.facing_bet, ctx.call_amount, ctx.pot, ctx.raises_used)


# --- S3: 限界帯覗き (素朴推定の勝率が40〜60%のときのみ。facing_bet不問) ---

def s3_assist(ctx):
    if ctx.me_pool < KNOBS["peek_cost"]:
        return no_assist(ctx)
    wp = ctx.naive_win_prob()
    if 0.40 <= wp <= 0.60:
        return ("peek", None)
    return no_assist(ctx)


def s3_bet(ctx):
    wp = ctx.naive_win_prob()
    return baseline_bet_decision(wp, ctx.facing_bet, ctx.call_amount, ctx.pot, ctx.raises_used)


# --- S4': 無作為覗き (各機会30%の固定確率) ---

def s4p_assist(ctx):
    if ctx.me_pool >= KNOBS["peek_cost"] and ctx.rng.random() < 0.3:
        return ("peek", None)
    return no_assist(ctx)


def s4p_bet(ctx):
    wp = ctx.naive_win_prob()
    return baseline_bet_decision(wp, ctx.facing_bet, ctx.call_amount, ctx.pot, ctx.raises_used)


# --- S6: 山割れ後停止覗き (事後確率が80%を超えたら同一ラウンド内で以後覗かない) ---

def s6_assist(ctx):
    if ctx.me_pool < KNOBS["peek_cost"]:
        return no_assist(ctx)
    p_h, p_l = fb_deck_posterior(frozenset(ctx.me.peeked_known))
    if max(p_h, p_l) > 0.8:
        return no_assist(ctx)
    return ("peek", None)


def s6_bet(ctx):
    wp = ctx.naive_win_prob()
    return baseline_bet_decision(wp, ctx.facing_bet, ctx.call_amount, ctx.pot, ctx.raises_used)


# --- S10: 窮鼠プッシュ (v0-B実装据え置き = 旧S6_ShortStackPush)。 ---
# v0-BのShortStackPushは pool>=8 のとき MarginalPeek(旧S3: 0.35-0.65帯・facing_bet/call>=3条件)
# に委譲していた。v0-C の公開戦略「S3」(40-60%帯・facing_bet不問)とは別物なので、
# v0-Bのオリジナル閾値のまま内部専用ヘルパーとして複製する(取り違えるとS10がv0-B据え置きに
# ならなくなるため、独立させた)。

def _v0b_marginal_peek_assist(ctx):
    if not ctx.facing_bet or ctx.call_amount < 3 or ctx.me_pool < KNOBS["peek_cost"]:
        return no_assist(ctx)
    wp = ctx.naive_win_prob()
    if 0.35 <= wp <= 0.65:
        return ("peek", None)
    return no_assist(ctx)


def _v0b_marginal_peek_bet(ctx):
    wp = ctx.naive_win_prob()
    return baseline_bet_decision(wp, ctx.facing_bet, ctx.call_amount, ctx.pot, ctx.raises_used)


def s10_assist(ctx):
    if ctx.me_pool < 8:
        return no_assist(ctx)
    return _v0b_marginal_peek_assist(ctx)


def s10_bet(ctx):
    if ctx.me_pool < 8:
        if ctx.me.hand_sum >= 11:
            return "allin"
        return "fold" if ctx.facing_bet else "check"
    return _v0b_marginal_peek_bet(ctx)


STRATEGIES = {
    "S1": (s1_assist, s1_bet),
    "S2": (s2_assist, s2_bet),
    "S3": (s3_assist, s3_bet),
    "S4p": (s4p_assist, s4p_bet),
    "S6": (s6_assist, s6_bet),
    "S10": (s10_assist, s10_bet),
}
STRATEGY_NAMES = ["S1", "S2", "S3", "S4p", "S6", "S10"]
PEEK_STRATEGIES = {"S2", "S3", "S6"}  # 事前登録文書「覗き系」の定義集合(S4'は含めない)

# ---------------------------------------------------------------------------
# 統計収集
# ---------------------------------------------------------------------------


class Stats:
    def __init__(self):
        self.peek_exec = {nm: 0 for nm in STRATEGY_NAMES}
        self.peek_flip = {nm: 0 for nm in STRATEGY_NAMES}
        self.misfire_peek = {nm: 0 for nm in STRATEGY_NAMES}  # 開示札が4-6(不発)
        self.misfire_total = {nm: 0 for nm in STRATEGY_NAMES}
        self.swing_sum = {nm: 0.0 for nm in STRATEGY_NAMES}   # |wp_after-wp_before| 合計
        self.g_sum = {nm: 0.0 for nm in STRATEGY_NAMES}       # G(改善量, v2と同じ定義) 合計
        # 記録項目3(G検算)専用: 「勝敗の見立て(wp>0.5かどうか)が反転したか」= 工程1v2のM2'と同じ側の反転定義。
        # K2の判断転換率(peek_flip: 実際に選んだベット行動が変わったか)とは別の指標であり、混同しないこと。
        self.side_flip_sum = {nm: 0.0 for nm in STRATEGY_NAMES}
        self.side_flip_n = {nm: 0 for nm in STRATEGY_NAMES}
        self.loop_guard_triggers = []


def compute_g(wp_before, wp_after):
    """v2(見立ての改善量)と同じ定義。wp=このプレイヤーの勝率(0.5=五分)。"""
    if wp_before > 0.5:
        a_pre = wp_after
    elif wp_before < 0.5:
        a_pre = 1.0 - wp_after
    else:
        a_pre = 0.5
    a_post = max(wp_after, 1.0 - wp_after)
    return a_post - a_pre


# ---------------------------------------------------------------------------
# ラウンド実装 (v0-B踏襲。晒し分岐を削除し、判断転換率の計測を全戦略へ拡張)
# ---------------------------------------------------------------------------


def _relabel(ev, me):
    e = dict(ev)
    e["actor"] = "SELF" if ev["actor"] == me else "OPP"
    return e


def _legalize_and_apply(intent, p, pools, commits, bet_level, raises_used, call_amount, facing_bet, bet_unit):
    """v0-B据え置き・無変更。"""
    pool = pools[p]

    def pay(amount):
        amount = min(amount, pool)
        pools[p] -= amount
        commits[p] += amount
        return amount, (pools[p] == 0)

    if intent == "check":
        if facing_bet:
            intent = "call"
        else:
            return ("check", bet_level, raises_used, False), False, False

    if intent == "fold":
        if not facing_bet:
            return ("check", bet_level, raises_used, False), False, False
        return ("fold", bet_level, raises_used, False), True, False

    if intent == "bet":
        if facing_bet:
            intent = "raise"
        else:
            amt, went_allin = pay(bet_unit)
            new_level = commits[p]
            if went_allin and amt < bet_unit:
                if new_level > bet_level:
                    return ("allin_raise", new_level, raises_used, True), False, True
                else:
                    return ("allin", new_level, raises_used, True), True, False
            return ("bet", new_level, raises_used, went_allin), False, True

    if intent == "raise":
        if not facing_bet:
            amt, went_allin = pay(bet_unit)
            new_level = commits[p]
            return ("bet", new_level, raises_used, went_allin), False, True
        if raises_used >= KNOBS["raise_cap"]:
            intent = "call"
        else:
            target = bet_level + bet_unit
            needed = target - commits[p]
            amt, went_allin = pay(needed)
            new_level = commits[p]
            if amt < needed:
                if new_level > bet_level:
                    return ("allin_raise", new_level, raises_used, True), False, True
                else:
                    return ("allin", new_level, raises_used, True), True, False
            return ("raise", new_level, raises_used + 1, went_allin), False, True

    if intent == "call":
        needed = call_amount
        amt, went_allin = pay(needed)
        return ("call", bet_level, raises_used, went_allin), True, False

    if intent == "allin":
        pre = pools[p]
        pools[p] = 0
        commits[p] += pre
        new_level = commits[p]
        if new_level > bet_level:
            return ("allin_raise", new_level, raises_used, True), False, True
        else:
            return ("allin", new_level, raises_used, True), True, False

    raise ValueError(f"unknown intent {intent}")


def play_round(rng, pools, strat_names, first_actor, stats, pair_key, round_num):
    hand_a_deck, hand_a = deal_hand_fb(rng)
    hand_b_deck, hand_b = deal_hand_fb(rng)
    state = {
        "A": PlayerRoundState(hand_a, hand_a_deck),
        "B": PlayerRoundState(hand_b, hand_b_deck),
    }

    ante_amt = ante_for_round(round_num)
    bet_unit = bet_unit_for_round(round_num)

    ante_pot = 0
    for who in ("A", "B"):
        pay = min(ante_amt, pools[who])
        pools[who] -= pay
        ante_pot += pay

    commits = {"A": 0, "B": 0}
    bet_level = 0
    raises_used = 0
    round_history = []
    prev_action = None
    to_act = first_actor
    other = {"A": "B", "B": "A"}

    def pot_now():
        return ante_pot + commits["A"] + commits["B"]

    winner = None
    safety_counter = 0

    while True:
        safety_counter += 1
        if safety_counter > 60:
            stats.loop_guard_triggers.append(
                f"pair={pair_key} round betting exceeded 60 turns; forced showdown")
            winner = None
            break

        p = to_act
        opp = other[p]
        facing_bet = commits[opp] > commits[p]
        call_amount = commits[opp] - commits[p]
        strat_name = strat_names[p]
        assist_fn, bet_fn = STRATEGIES[strat_name]

        ctx = DecisionContext(
            rng=rng, me=state[p], opp=state[opp], me_pool=pools[p], opp_pool=pools[opp],
            pot=pot_now(), call_amount=call_amount, bet_level=bet_level, raises_used=raises_used,
            round_history=[_relabel(ev, p) for ev in round_history], facing_bet=facing_bet,
            bet_unit=bet_unit,
        )
        assist_type, assist_val = assist_fn(ctx)

        did_peek = False
        pre_peek_action = None
        pre_wp = None
        if assist_type == "peek" and pools[p] >= KNOBS["peek_cost"]:
            # (c) 晒し削除により revealed は常に空 = nonpublic は常に相手の手札全体
            nonpublic = list(state[opp].hand)
            pre_wp = ctx.naive_win_prob()
            pre_peek_action = baseline_bet_decision(
                pre_wp, facing_bet, call_amount, pot_now(), raises_used)
            pools[p] -= KNOBS["peek_cost"]  # 焼却(ポットに入らない。v0-B据え置き)
            peeked_card = rng.choice(nonpublic)
            state[p].peeked_known.add(peeked_card)
            did_peek = True
            round_history.append({"actor": p, "phase": "assist", "type": "peek"})
            stats.peek_exec[strat_name] += 1
            stats.misfire_total[strat_name] += 1
            if 4 <= peeked_card <= 6:
                stats.misfire_peek[strat_name] += 1

        ctx2 = DecisionContext(
            rng=rng, me=state[p], opp=state[opp], me_pool=pools[p], opp_pool=pools[opp],
            pot=pot_now(), call_amount=call_amount, bet_level=bet_level, raises_used=raises_used,
            round_history=[_relabel(ev, p) for ev in round_history], facing_bet=facing_bet,
            bet_unit=bet_unit,
        )
        intent = bet_fn(ctx2)

        if did_peek:
            post_wp = ctx2.naive_win_prob()
            stats.swing_sum[strat_name] += abs(post_wp - pre_wp)
            g = compute_g(pre_wp, post_wp)
            stats.g_sum[strat_name] += g
            # K2用: 覗き直前の最適行動 vs 覗き後に実際に選んだ行動(ベット閾値0.55等も含む行動ベース)
            if intent != pre_peek_action:
                stats.peek_flip[strat_name] += 1
            # 記録項目3用: 勝敗の見立て側(wp>0.5かどうか)が反転したか(工程1v2のM2'と同じ定義)
            side_before = 1 if pre_wp > 0.5 else (-1 if pre_wp < 0.5 else 0)
            side_after = 1 if post_wp > 0.5 else (-1 if post_wp < 0.5 else 0)
            if side_before != side_after or side_before == 0:
                stats.side_flip_sum[strat_name] += g
                stats.side_flip_n[strat_name] += 1

        pre_action_pool = pools[p]
        resolved, ends_betting, is_raise_like = _legalize_and_apply(
            intent, p, pools, commits, bet_level, raises_used, call_amount, facing_bet, bet_unit)
        action_type, new_bet_level, new_raises_used, resolved_allin = resolved
        bet_level = new_bet_level
        raises_used = new_raises_used

        round_history.append({"actor": p, "phase": "bet", "type": action_type})

        if action_type == "check" and prev_action == "check":
            ends_betting = True
        if action_type == "fold":
            winner = opp
            break
        if ends_betting:
            winner = None
            break
        prev_action = action_type
        to_act = opp

    if winner == "A" or winner == "B":
        pot = ante_pot + commits["A"] + commits["B"]
        pools[winner] += pot
        round_winner = winner
    else:
        eff = min(commits["A"], commits["B"])
        if commits["A"] > eff:
            pools["A"] += commits["A"] - eff
        elif commits["B"] > eff:
            pools["B"] += commits["B"] - eff
        pot = ante_pot + 2 * eff
        sa, sb = state["A"].hand_sum, state["B"].hand_sum
        if sa > sb:
            pools["A"] += pot
            round_winner = "A"
        elif sb > sa:
            pools["B"] += pot
            round_winner = "B"
        else:
            half = pot // 2
            pools["A"] += half
            pools["B"] += half
            round_winner = "split"

    return round_winner


def play_game(rng, strat_A, strat_B, first_actor, stats, pair_key, round_tally):
    """1ゲーム(プールが尽きるかR30まで)を実行し、各ラウンドの勝者をround_tallyに積む。
    round_tally: dict strat_name -> [win_credit, n]  (A/Bをそのラウンドの戦略名に変換して加算)"""
    pools = {"A": KNOBS["pool_start"], "B": KNOBS["pool_start"]}
    strat_names = {"A": strat_A, "B": strat_B}
    first = first_actor
    other = {"A": "B", "B": "A"}
    rounds_played = 0

    for rnd in range(1, KNOBS["max_rounds"] + 1):
        rounds_played = rnd
        round_winner = play_round(rng, pools, strat_names, first, stats, pair_key, rnd)

        for side in ("A", "B"):
            nm = strat_names[side]
            round_tally[nm][1] += 1
            if round_winner == side:
                round_tally[nm][0] += 1.0
            elif round_winner == "split":
                round_tally[nm][0] += 0.5

        if pools["A"] <= 0 or pools["B"] <= 0:
            break
        first = other[first]

    return rounds_played


# ---------------------------------------------------------------------------
# トーナメント実行 (「ディール」= 1ラウンド。各ペア>=10,000ラウンドまでゲームを継続)
# ---------------------------------------------------------------------------


def make_seed(i, j, game_idx, first_flag):
    return BASE_SEED * 1_000_000_000 + i * 10_000_000 + j * 100_000 + game_idx * 2 + first_flag


def run_pair(i, j, name_i, name_j, stats, min_rounds=ROUNDS_PER_PAIR_MIN, start_game_idx=0):
    """strategy i vs strategy j。ラウンド単位の勝率(五分=0.5)をmin_rounds以上蓄積するまでゲームを継続する。
    戻り値: (win_rate_i, total_rounds, games_played, next_game_idx)"""
    pair_key = f"{name_i}_vs_{name_j}"
    round_tally = {name_i: [0.0, 0], name_j: [0.0, 0]}
    game_idx = start_game_idx
    games_played = 0

    while round_tally[name_i][1] < min_rounds:
        first_is_i = (game_idx % 2 == 0)
        seed = make_seed(i, j, game_idx, 0 if first_is_i else 1)
        rng = random.Random(seed)
        first_actor = "A" if first_is_i else "B"
        # A=name_i, B=name_jで固定し、先手のみ交互にする(手番の偏りを均すため)。
        play_game(rng, name_i, name_j, first_actor, stats, pair_key, round_tally)
        games_played += 1
        game_idx += 1

    total_rounds = round_tally[name_i][1]
    win_rate_i = round_tally[name_i][0] / total_rounds
    return win_rate_i, total_rounds, games_played, game_idx


def run_tournament():
    stats = Stats()
    n = len(STRATEGY_NAMES)
    matrix = [[None] * n for _ in range(n)]
    round_counts = [[None] * n for _ in range(n)]
    t0 = time.time()
    pair_count = 0
    for i in range(n):
        for j in range(i + 1, n):
            name_i, name_j = STRATEGY_NAMES[i], STRATEGY_NAMES[j]
            wr_i, total_rounds, games, _ = run_pair(i, j, name_i, name_j, stats)
            matrix[i][j] = wr_i
            matrix[j][i] = 1.0 - wr_i
            round_counts[i][j] = total_rounds
            round_counts[j][i] = total_rounds
            pair_count += 1
    elapsed = time.time() - t0
    return matrix, round_counts, stats, elapsed, pair_count


def field_averages(matrix):
    """自己対戦を含まない(対角なし)対フィールド平均勝率。"""
    n = len(STRATEGY_NAMES)
    return [sum(matrix[i][j] for j in range(n) if j != i) / (n - 1) for i in range(n)]


# ---------------------------------------------------------------------------
# Wilson信頼区間 (K1判定用)
# ---------------------------------------------------------------------------


def wilson_ci(successes, n, z=1.959963984540054):
    if n == 0:
        return 0.0, 1.0
    p_hat = successes / n
    denom = 1 + z * z / n
    center = (p_hat + z * z / (2 * n)) / denom
    margin = (z * math.sqrt(p_hat * (1 - p_hat) / n + z * z / (4 * n * n))) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


# ---------------------------------------------------------------------------
# 機械判定 (事前登録文書のK1/K2/K3をそのまま適用)
# ---------------------------------------------------------------------------


def idx(name):
    return STRATEGY_NAMES.index(name)


def pick_best_peek_strategy(matrix, field_avg, stats):
    """事前登録文書のタイブレーク規則: 対S1勝率最大 -> 対フィールド平均勝率(小数第3位まで同値時) -> 総覗き回数が少ない方。"""
    s1 = idx("S1")
    candidates = []
    for nm in sorted(PEEK_STRATEGIES):
        i = idx(nm)
        wr_vs_s1 = matrix[i][s1]
        candidates.append((nm, wr_vs_s1, field_avg[i], stats.peek_exec[nm]))
    max_wr = max(round(c[1], 3) for c in candidates)
    tied = [c for c in candidates if round(c[1], 3) == max_wr]
    if len(tied) > 1:
        max_fa = max(c[2] for c in tied)
        tied = [c for c in tied if c[2] == max_fa]
    if len(tied) > 1:
        min_peek = min(c[3] for c in tied)
        tied = [c for c in tied if c[3] == min_peek]
    best = max(candidates, key=lambda c: c[1]) if len(tied) != 1 else tied[0]
    return best[0], candidates


def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)


def build_report(matrix, round_counts, stats, elapsed, pair_count, out_dir, md_path):
    names = STRATEGY_NAMES
    n = len(names)
    s1 = idx("S1")
    fa = field_averages(matrix)

    best_name, peek_candidates = pick_best_peek_strategy(matrix, fa, stats)
    best_i = idx(best_name)

    # --- K1 ---
    wr_vs_s1 = matrix[best_i][s1]
    n_vs_s1 = round_counts[best_i][s1]
    successes = wr_vs_s1 * n_vs_s1
    k1_ci_lo, k1_ci_hi = wilson_ci(successes, n_vs_s1)
    k1_kill = k1_ci_lo <= 0.500

    # --- K2 (覗き系最良の、全対戦相手合算の判断転換率) ---
    k2_n = stats.peek_exec[best_name]
    k2_flip = stats.peek_flip[best_name]
    k2_rate = k2_flip / k2_n if k2_n > 0 else None
    k2_n_ok = k2_n >= K2_MIN_PEEK_N
    k2_kill = (k2_rate is not None) and (k2_rate < 0.15)

    # --- K3 ---
    s2_i = idx("S2")
    field_rank = sorted(range(n), key=lambda i: -fa[i])
    s2_is_top_field = field_rank[0] == s2_i
    k3_triggered = (best_name == "S2") and s2_is_top_field

    # --- 記録のみ1: 不発の覗き率(全戦略合算) ---
    total_misfire = sum(stats.misfire_peek.values())
    total_peek = sum(stats.misfire_total.values())
    misfire_rate_overall = total_misfire / total_peek if total_peek else None
    misfire_by_strat = {
        nm: (stats.misfire_peek[nm] / stats.misfire_total[nm] if stats.misfire_total[nm] else None)
        for nm in names
    }

    # --- 記録のみ2: S6とS2の成績差 ---
    s6_i, s2_i2 = idx("S6"), idx("S2")
    s6_vs_s2 = matrix[s6_i][s2_i2]
    s6_field = fa[s6_i]
    s2_field = fa[s2_i2]

    # --- 記録のみ3: 動き幅(swing)と改善量(G)の乖離 ---
    # 「反転」は工程1v2のM2'と同じ側基準(wp>0.5かどうか)。K2の行動ベース転換率(peek_flip)とは別物。
    swing_g_rows = []
    for nm in names:
        if stats.peek_exec[nm] == 0:
            swing_g_rows.append((nm, None, None, None, None))
            continue
        mean_swing = stats.swing_sum[nm] / stats.peek_exec[nm]
        mean_g = stats.g_sum[nm] / stats.peek_exec[nm]
        side_flip_rate = stats.side_flip_n[nm] / stats.peek_exec[nm]
        avg_g_given_side_flip = (stats.side_flip_sum[nm] / stats.side_flip_n[nm]
                                  if stats.side_flip_n[nm] else 0.0)
        swing_g_rows.append((nm, mean_swing, mean_g, side_flip_rate, avg_g_given_side_flip))

    # --- Keep/Kill 総合 ---
    if k1_kill:
        overall = "K1発動 -> kill(蘇生手続き全体を終了・埋葬)"
    elif k2_kill:
        overall = "K2発動 -> kill"
    elif k3_triggered:
        overall = "K3発動 -> Adjust-P(救済要否・実施はdesign chat/人間の判断)"
    else:
        overall = "Keep(工程3へ)"

    # --- CSV出力 ---
    write_csv(os.path.join(out_dir, "win_rate_matrix.csv"),
              ["strategy"] + names,
              [[names[i]] + [f"{matrix[i][j]:.6f}" if matrix[i][j] is not None else "" for j in range(n)]
               for i in range(n)])
    write_csv(os.path.join(out_dir, "round_counts.csv"),
              ["strategy"] + names,
              [[names[i]] + [round_counts[i][j] if round_counts[i][j] is not None else "" for j in range(n)]
               for i in range(n)])
    write_csv(os.path.join(out_dir, "field_average.csv"),
              ["strategy", "field_avg_winrate_round_level"],
              [[names[i], f"{fa[i]:.6f}"] for i in range(n)])
    write_csv(os.path.join(out_dir, "peek_stats.csv"),
              ["strategy", "peek_exec", "peek_flip_action_based(K2)", "action_flip_rate(K2)",
               "misfire_peek", "misfire_rate",
               "mean_swing", "mean_G", "side_flip_rate(record3)", "avg_G_given_side_flip(record3)"],
              [[nm, stats.peek_exec[nm], stats.peek_flip[nm],
                f"{(stats.peek_flip[nm]/stats.peek_exec[nm]):.6f}" if stats.peek_exec[nm] else "",
                stats.misfire_peek[nm],
                f"{misfire_by_strat[nm]:.6f}" if misfire_by_strat[nm] is not None else "",
                f"{r[1]:.6f}" if r[1] is not None else "",
                f"{r[2]:.6f}" if r[2] is not None else "",
                f"{r[3]:.6f}" if r[3] is not None else "",
                f"{r[4]:.6f}" if r[4] is not None else ""]
               for nm, r in zip(names, swing_g_rows)])

    # --- results_v0c.md ---
    lines = []
    lines.append("# 思考課金ポーカー v0-C モンテカルロ検証 結果・機械判定\n")
    lines.append(f"- 実行日時(BASE_SEED基準): {BASE_SEED}")
    lines.append(f"- 総ペア数: {pair_count} (6戦略の自己対戦を除く組合せ, {n}C2)")
    lines.append(f"- 「ディール」の単位: 1ラウンド(1回の手札配り)。各ペア{ROUNDS_PER_PAIR_MIN}ラウンド以上")
    lines.append(f"- 実行時間: {elapsed:.1f}秒")
    lines.append(f"- 無限ループガード発動回数: {len(stats.loop_guard_triggers)}\n")

    lines.append("## 1. 勝率マトリクス(行 vs 列、ラウンド単位。引き分け0.5勝)\n")
    header = "| strategy | " + " | ".join(names) + " |"
    sep = "|---" * (n + 1) + "|"
    lines.append(header)
    lines.append(sep)
    for i in range(n):
        row = "| " + names[i] + " | " + " | ".join(
            f"{matrix[i][j]:.4f}" if matrix[i][j] is not None else "-" for j in range(n)) + " |"
        lines.append(row)
    lines.append("")

    lines.append("## 2. 対フィールド平均勝率(自己対戦を除く5戦略との平均)\n")
    lines.append("| strategy | field_avg_winrate |")
    lines.append("|---|---|")
    for i, nm in enumerate(names):
        lines.append(f"| {nm} | {fa[i]:.6f} |")
    lines.append("")

    lines.append("## 3. 覗き系最良の特定過程(タイブレーク適用)\n")
    lines.append("| 覗き系候補 | 対S1勝率(点推定) | 対フィールド平均勝率 | 総覗き回数 |")
    lines.append("|---|---|---|---|")
    for nm, wr, favg, pk in peek_candidates:
        marker = " **<- 最良**" if nm == best_name else ""
        lines.append(f"| {nm} | {wr:.6f} | {favg:.6f} | {pk}{marker} |")
    lines.append(f"\n**覗き系最良 = {best_name}**（対S1勝率の点推定が最大）。\n")

    lines.append("## 4. K1(市場不在・構造kill)\n")
    lines.append(f"- 対象: {best_name}(覗き系最良) の対S1勝率")
    lines.append(f"- n(対戦ラウンド数) = {n_vs_s1}")
    lines.append(f"- 勝率点推定 = {wr_vs_s1:.6f}")
    lines.append(f"- Wilson法95%CI = [{k1_ci_lo:.6f}, {k1_ci_hi:.6f}]")
    lines.append(f"- 判定基準: CI下限 <= 0.500 -> kill")
    lines.append(f"- **判定結果: {'K1発動(kill)' if k1_kill else 'K1非発動'}**\n")

    lines.append("## 5. K2(経費化・構造kill)\n")
    lines.append(f"- 対象: {best_name}(覗き系最良) の判断転換率(全対戦相手合算)")
    lines.append(f"- 覗き実行機会 n = {k2_n} (n>=500要件: {'満たす' if k2_n_ok else '**未達**'})")
    lines.append(f"- 転換回数 = {k2_flip}")
    lines.append(f"- 転換率 = {k2_rate:.6f}" if k2_rate is not None else "- 転換率 = n/a(n=0)")
    lines.append(f"- 判定基準: 転換率 < 15% -> kill")
    lines.append(f"- **判定結果: {'K2発動(kill)' if k2_kill else 'K2非発動'}**\n")

    lines.append("## 6. K3(決断消滅・調整グレー)\n")
    lines.append(f"- 覗き系最良 = {best_name}(S2か: {'はい' if best_name == 'S2' else 'いいえ'})")
    lines.append(f"- 対フィールド平均勝率の6戦略中の順位: "
                  f"{'S2が首位' if s2_is_top_field else f'S2は首位でない(首位={names[field_rank[0]]})'}")
    lines.append(f"- 発動条件: 覗き系最良がS2 かつ S2が対フィールド平均勝率で6戦略中首位")
    lines.append(f"- **判定結果: {'K3発動(Adjust-P対象)' if k3_triggered else 'K3非発動'}**\n")

    lines.append("## 7. 総合判定\n")
    lines.append(f"**{overall}**\n")
    lines.append("(判定結果に対する解釈・救済提案は指示書により記述しない。解釈・最終判断はdesign chatが行う。)\n")

    lines.append("## 8. 記録のみ(判定外・観察項目)\n")
    lines.append("### 8-1. 不発の覗き率(開示札が4〜6で相手の山の事後がほぼ動かなかった率)\n")
    lines.append(f"- 全戦略合算: {misfire_rate_overall:.6f} (n={total_peek})\n" if misfire_rate_overall is not None else "- n/a\n")
    lines.append("| strategy | misfire_rate | n(peek_exec) |")
    lines.append("|---|---|---|")
    for nm in names:
        r = misfire_by_strat[nm]
        lines.append(f"| {nm} | {f'{r:.6f}' if r is not None else 'n/a'} | {stats.peek_exec[nm]} |")
    lines.append("")

    lines.append("### 8-2. S6とS2の成績差(山割れ後停止覗きの実測価値)\n")
    lines.append(f"- S6 vs S2 直接対戦勝率(S6視点): {s6_vs_s2:.6f}")
    lines.append(f"- S6 対フィールド平均勝率: {s6_field:.6f}")
    lines.append(f"- S2 対フィールド平均勝率: {s2_field:.6f}")
    lines.append(f"- 差(S6-S2、対フィールド平均): {s6_field - s2_field:.6f}\n")

    lines.append("### 8-3. 動き幅(swing)と改善量(G)の乖離の兆候\n")
    lines.append("swing = 覗き前後の素朴勝率|Δwp|の平均。G = 見立ての改善量(工程1v2と同一定義)の平均。"
                  "この表の「反転」はK2の判断転換率(実際に選んだベット行動が変わったか)とは別の指標で、"
                  "工程1v2のM2'と同じ「勝敗の見立て側(wp>0.5かどうか)が反転したか」を基準にしている"
                  "(K2はベット閾値0.55等も絡む行動ベースのため、この節の反転定義とは一致しない)。"
                  "side_flip_rate×avg_G\\|side_flipはGの検算(理論上mean_Gに一致)。\n")
    lines.append("| strategy | mean_swing | mean_G | side_flip_rate | avg_G\\|side_flip | 検算(積) |")
    lines.append("|---|---|---|---|---|---|")
    for nm, mean_swing, mean_g, flip_rate, avg_g_flip in swing_g_rows:
        if mean_swing is None:
            lines.append(f"| {nm} | n/a | n/a | n/a | n/a | n/a |")
        else:
            check = flip_rate * avg_g_flip
            lines.append(f"| {nm} | {mean_swing:.6f} | {mean_g:.6f} | {flip_rate:.6f} | "
                          f"{avg_g_flip:.6f} | {check:.6f} |")
    lines.append("")

    lines.append("## 9. 乱数シード\n")
    lines.append(f"- BASE_SEED = {BASE_SEED}")
    lines.append(f"- ペア(i,j)のゲームkにおけるシード = "
                  f"BASE_SEED*1_000_000_000 + i*10_000_000 + j*100_000 + k*2 + first_flag")
    lines.append("")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return {
        "best_name": best_name,
        "k1_kill": k1_kill,
        "k1_ci": (k1_ci_lo, k1_ci_hi),
        "k2_kill": k2_kill,
        "k2_rate": k2_rate,
        "k2_n": k2_n,
        "k2_n_ok": k2_n_ok,
        "k3_triggered": k3_triggered,
        "overall": overall,
    }


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------


def main():
    base_dir = os.path.dirname(__file__)
    out_dir = os.path.join(base_dir, "results_v0c")  # CSV等の補助出力置き場
    os.makedirs(out_dir, exist_ok=True)
    md_path = os.path.join(base_dir, "results_v0c.md")  # 指示書指定の正式パス(sim/thinking_tax/直下)

    matrix, round_counts, stats, elapsed, pair_count = run_tournament()
    summary = build_report(matrix, round_counts, stats, elapsed, pair_count, out_dir, md_path)

    with open(os.path.join(out_dir, "run_log.txt"), "w", encoding="utf-8") as f:
        f.write(f"done in {elapsed:.1f}s, pairs={pair_count}, base_seed={BASE_SEED}\n")
        f.write(f"best_peek_strategy={summary['best_name']}\n")
        f.write(f"K1_kill={summary['k1_kill']} ci={summary['k1_ci']}\n")
        f.write(f"K2_kill={summary['k2_kill']} rate={summary['k2_rate']} n={summary['k2_n']} n_ok={summary['k2_n_ok']}\n")
        f.write(f"K3_triggered={summary['k3_triggered']}\n")
        f.write(f"overall={summary['overall']}\n")

    print(f"done in {elapsed:.1f}s, pairs={pair_count}")
    print(f"best_peek_strategy={summary['best_name']}")
    print(f"K1_kill={summary['k1_kill']} K2_kill={summary['k2_kill']} K3_triggered={summary['k3_triggered']}")
    print(f"overall={summary['overall']}")
    return summary


if __name__ == "__main__":
    main()
