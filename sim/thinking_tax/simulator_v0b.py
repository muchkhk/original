#!/usr/bin/env python3
"""
思考課金ポーカー v0-B(蘇生版) モンテカルロ検証シミュレータ

指示書: 思考課金ポーカー v0-B(蘇生版) モンテカルロ検証 v2
v0-A (simulator_v0a.py / 指示書 v1) からの差分修正版。
経済パラメータ(初期プール・アンテ・ベット/レイズ単位)のみ変更し、
NPC戦略 S1〜S8 はコードレベルで無変更。S9 (PotAwareBluff) を追加。
判定(kill/keep)は行わない。指標算出のみ。
"""

import csv
import json
import os
import random
import statistics
import time
from collections import defaultdict
from functools import lru_cache
from itertools import combinations

# ---------------------------------------------------------------------------
# ノブ
# v0-A からの変更点: pool_start 20->25、アンテ/ベット・レイズ単位をラウンド帯で
# エスカレーションさせる(固定値の代わりにスケジュール関数を使う)。
# raise_cap / peek_cost / max_rounds は不変。
# ---------------------------------------------------------------------------

KNOBS = {
    "pool_start": 25,
    "raise_cap": 4,
    "peek_cost": 2,  # 将来案: max(2, ceil(pot*0.25)) だが今回は固定2のみ実装 (v0-Aから不変)
    "max_rounds": 30,
}

# (そのラウンド帯の最終ラウンド番号, アンテ額) のテーブル。該当帯を超えたら最後の値を使い続ける。
ANTE_SCHEDULE = [(4, 1), (8, 2), (12, 4), (float("inf"), 6)]


def ante_for_round(round_num):
    for last_round, amt in ANTE_SCHEDULE:
        if round_num <= last_round:
            return amt
    return ANTE_SCHEDULE[-1][1]


def bet_unit_for_round(round_num):
    """ベット/レイズ単位 = 現在のアンテ額 (最低2)"""
    return max(ante_for_round(round_num), 2)


def round_band(round_num):
    if round_num <= 4:
        return "R1-4"
    elif round_num <= 8:
        return "R5-8"
    elif round_num <= 12:
        return "R9-12"
    else:
        return "R13+"


ROUND_BANDS = ["R1-4", "R5-8", "R9-12", "R13+"]

BASE_SEED = 20260715  # 指示書発行日 (design chat が本日日付として扱う値)
GAMES_PER_PAIR = 2000
POT_BANDS = [("2-4", 2, 4), ("5-8", 5, 8), ("9-14", 9, 14), ("15+", 15, float("inf"))]
CALL_MARGIN = 0.05  # ポットオッズ「マージン」の値。指示書に明記が無いため独自設定 (v0-Aを踏襲)
SIGNAL_ADJUST = 0.05  # S7 のシグナル読み取りによる要求勝率の緩和/厳格化幅 (v0-Aを踏襲)

ALL_VALUES = (1, 2, 3, 4, 5, 6)

# ---------------------------------------------------------------------------
# EV計算 (素朴仮定: 相手の非公開カードは既知情報と整合する残り候補から一様ランダム)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=None)
def _opponent_sum_distribution(known_values):
    """known_values: frozenset of values already known to be in opponent's hand.
    Returns dict {sum: probability} over opponent's full 3-card hand sum,
    assuming the remaining (3-len(known)) cards are a uniform random subset
    of the remaining deck values."""
    known = set(known_values)
    remaining = [v for v in ALL_VALUES if v not in known]
    need = 3 - len(known)
    base = sum(known)
    if need == 0:
        return {base: 1.0}
    combos = list(combinations(remaining, need))
    n = len(combos)
    dist = {}
    for c in combos:
        s = base + sum(c)
        dist[s] = dist.get(s, 0) + 1
    for s in dist:
        dist[s] /= n
    return dist


def win_tie_lose(my_sum, known_opponent_values):
    """known_opponent_values: iterable of known opponent card values (public reveals
    plus, for the peeking player, privately peeked cards)."""
    dist = _opponent_sum_distribution(frozenset(known_opponent_values))
    win = tie = lose = 0.0
    for s, p in dist.items():
        if my_sum > s:
            win += p
        elif my_sum == s:
            tie += p
        else:
            lose += p
    return win, tie, lose


def naive_win_prob(my_sum, known_opponent_values):
    w, t, _ = win_tie_lose(my_sum, known_opponent_values)
    return w + 0.5 * t


# ---------------------------------------------------------------------------
# 補助データ構造
# ---------------------------------------------------------------------------


class PlayerRoundState:
    """1ラウンド内の1プレイヤーの状態"""

    __slots__ = ("hand", "revealed", "peeked_known", "pool_ref")

    def __init__(self, hand):
        self.hand = hand  # tuple of 3 ints (固定, ショーダウンまで不変)
        self.revealed = set()  # 自分の手札のうち公開された値
        self.peeked_known = set()  # 相手の手札のうち、覗きで自分だけが知っている値

    @property
    def hand_sum(self):
        return sum(self.hand)

    def known_of_opponent(self, opp_revealed):
        return self.peeked_known | opp_revealed

    def unrevealed_cards(self):
        return [c for c in self.hand if c not in self.revealed]


class DecisionContext:
    """戦略関数に渡す読み取り専用ビュー"""

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
        self.round_history = round_history  # list of event dicts, this round, so far
        self.facing_bet = facing_bet
        self.bet_unit = bet_unit  # 現在のラウンド帯でのベット/レイズ単位 (v0-B; S9 が参照)

    def naive_win_prob(self, extra_known=frozenset()):
        known = self.me.known_of_opponent(self.opp.revealed) | extra_known
        return naive_win_prob(self.me.hand_sum, known)


# ---------------------------------------------------------------------------
# 統計収集
# ---------------------------------------------------------------------------


class Stats:
    def __init__(self):
        self.peek_by_band = defaultdict(lambda: [0, 0])  # (strategy, band) -> [peek_count, opp_count]
        self.peek_flip = defaultdict(lambda: [0, 0])  # strategy -> [flip_count, peek_count]
        self.decision_count = defaultdict(int)  # strategy -> # of bet-action decisions
        self.allin_count = defaultdict(int)  # strategy -> # of resolved all-in actions
        self.allin_outcome = defaultdict(lambda: [0.0, 0])  # strategy -> [win_sum, count] (round outcome after allin)
        self.allin_pool_share = defaultdict(lambda: [0.0, 0])  # strategy -> [sum_share, count]
        self.game_lengths = []  # list of (pair_key, rounds_played)
        self.spiral_records = []  # list of (pair_key, disadvantaged_strategy, outcome) outcome in {1.0,0.5,0.0}
        self.loop_guard_triggers = []  # list of description strings
        # --- v0-B 追加指標 ---
        self.forced_hand = defaultdict(lambda: [0, 0])  # round_band -> [forced_count, total_decision_count]
        self.showdown_pot_by_band = defaultdict(list)  # round_band -> [pot_size, ...] (ショーダウン到達時のみ)
        self.game_end_mode = defaultdict(int)  # 'bust' | 'timeout_win' | 'timeout_draw' -> count

    def pot_band(self, pot):
        for name, lo, hi in POT_BANDS:
            if lo <= pot <= hi:
                return name
        return "<2"  # 想定外(理論上プールが尽きた状態は既にゲーム終了しているため発生しないはず)


# ---------------------------------------------------------------------------
# ベッティング判断の共通ロジック (S1基準)
# ---------------------------------------------------------------------------


def baseline_bet_decision(win_prob, facing_bet, call_amount, pot, raises_used, threshold_adjust=0.0):
    """S1基準の機械的判断。戻り値: 'check'|'bet'|'raise'|'call'|'fold'"""
    if not facing_bet:
        return "bet" if win_prob > 0.55 else "check"
    if win_prob > 0.55:
        return "raise" if raises_used < KNOBS["raise_cap"] else "call"
    breakeven = call_amount / (pot + call_amount) if (pot + call_amount) > 0 else 1.0
    threshold = breakeven + CALL_MARGIN + threshold_adjust
    return "call" if win_prob > threshold else "fold"


def no_assist(ctx):
    return ("none", None)


def highest_unrevealed_card(state):
    cards = state.unrevealed_cards()
    return max(cards) if cards else None


# ---------------------------------------------------------------------------
# シグナル検出 (S7 用)
# ---------------------------------------------------------------------------


def opponent_did_reveal_then_raise(round_history, opp_id):
    """相手が「同一ターン内で 晒す→レイズ」をこのラウンド中に行ったことがあるか"""
    last_assist = None
    last_assist_actor = None
    for ev in round_history:
        if ev["actor"] != opp_id:
            continue
        if ev["phase"] == "assist":
            last_assist_actor = opp_id
            last_assist = ev["type"]
        elif ev["phase"] == "bet":
            if last_assist_actor == opp_id and last_assist == "reveal" and ev["type"] in ("raise", "allin_raise"):
                return True
            last_assist = None
            last_assist_actor = None
    return False


def opponent_did_peek_then_raise(round_history, opp_id):
    """相手が「同一ターン内で 覗く→レイズ(フォールドせず)」をこのラウンド中に行ったことがあるか"""
    last_assist = None
    last_assist_actor = None
    for ev in round_history:
        if ev["actor"] != opp_id:
            continue
        if ev["phase"] == "assist":
            last_assist_actor = opp_id
            last_assist = ev["type"]
        elif ev["phase"] == "bet":
            if last_assist_actor == opp_id and last_assist == "peek" and ev["type"] in ("raise", "allin_raise"):
                return True
            last_assist = None
            last_assist_actor = None
    return False


# ---------------------------------------------------------------------------
# 戦略定義
# 各戦略は (assist_fn, bet_fn) のペア。
#   assist_fn(ctx) -> ("none"|"peek"|"reveal", reveal_value_or_None)
#   bet_fn(ctx) -> "check"|"bet"|"raise"|"call"|"fold"|"allin"
# ---------------------------------------------------------------------------

# --- S1: NeverPeek ---

def s1_assist(ctx):
    return no_assist(ctx)


def s1_bet(ctx):
    wp = ctx.naive_win_prob()
    return baseline_bet_decision(wp, ctx.facing_bet, ctx.call_amount, ctx.pot, ctx.raises_used)


# --- S2: AlwaysPeek ---

def s2_assist(ctx):
    if ctx.facing_bet and ctx.me_pool >= KNOBS["peek_cost"]:
        return ("peek", None)
    return no_assist(ctx)


def s2_bet(ctx):
    wp = ctx.naive_win_prob()
    return baseline_bet_decision(wp, ctx.facing_bet, ctx.call_amount, ctx.pot, ctx.raises_used)


# --- S3: MarginalPeek ---

def s3_assist(ctx):
    if not ctx.facing_bet or ctx.call_amount < 3 or ctx.me_pool < KNOBS["peek_cost"]:
        return no_assist(ctx)
    wp = ctx.naive_win_prob()
    if 0.35 <= wp <= 0.65:
        return ("peek", None)
    return no_assist(ctx)


def s3_bet(ctx):
    wp = ctx.naive_win_prob()
    return baseline_bet_decision(wp, ctx.facing_bet, ctx.call_amount, ctx.pot, ctx.raises_used)


# --- S4: BluffReveal ---

def s4_assist(ctx):
    s = ctx.me.hand_sum
    if s <= 9 and ctx.rng.random() < 0.3:
        card = highest_unrevealed_card(ctx.me)
        if card is not None:
            return ("reveal", card)
        return no_assist(ctx)
    return no_assist(ctx)


def s4_bet(ctx):
    s = ctx.me.hand_sum
    # 直前の assist で晒した(=bluff発動)かどうかは round_history の直前イベントで判定する
    bluffed_this_turn = bool(ctx.round_history) and ctx.round_history[-1]["actor"] == "SELF" and \
        ctx.round_history[-1]["phase"] == "assist" and ctx.round_history[-1]["type"] == "reveal"
    if bluffed_this_turn:
        return "raise" if ctx.facing_bet and ctx.raises_used < KNOBS["raise_cap"] else (
            "call" if ctx.facing_bet else "bet")
    if s >= 13:
        return "raise" if ctx.facing_bet and ctx.raises_used < KNOBS["raise_cap"] else (
            "call" if ctx.facing_bet else "bet")
    wp = ctx.naive_win_prob()
    return baseline_bet_decision(wp, ctx.facing_bet, ctx.call_amount, ctx.pot, ctx.raises_used)


# --- S5: HonestValue ---

def s5_assist(ctx):
    if ctx.me.hand_sum >= 13:
        card = highest_unrevealed_card(ctx.me)
        if card is not None:
            return ("reveal", card)
    return no_assist(ctx)


def s5_bet(ctx):
    if ctx.me.hand_sum >= 13:
        return "raise" if ctx.facing_bet and ctx.raises_used < KNOBS["raise_cap"] else (
            "call" if ctx.facing_bet else "bet")
    wp = ctx.naive_win_prob()
    return baseline_bet_decision(wp, ctx.facing_bet, ctx.call_amount, ctx.pot, ctx.raises_used)


# --- S6: ShortStackPush ---

def s6_assist(ctx):
    if ctx.me_pool < 8:
        return no_assist(ctx)
    return s3_assist(ctx)


def s6_bet(ctx):
    if ctx.me_pool < 8:
        if ctx.me.hand_sum >= 11:
            return "allin"
        return "fold" if ctx.facing_bet else "check"
    return s3_bet(ctx)


# --- S7: SignalReader / S8: SignalBlind (S7から参照部分を無効化したアブレーション) ---

def _s7_core(ctx, use_signals):
    if not ctx.facing_bet or ctx.call_amount < 3 or ctx.me_pool < KNOBS["peek_cost"]:
        peek = False
    else:
        wp0 = ctx.naive_win_prob()
        peek = 0.35 <= wp0 <= 0.65
    if peek:
        return ("peek", None)
    return ("none", None)


def s7_assist(ctx):
    return _s7_core(ctx, use_signals=True)


def s8_assist(ctx):
    return _s7_core(ctx, use_signals=False)


def _s7_bet_core(ctx, use_signals):
    wp = ctx.naive_win_prob()
    adjust = 0.0
    if use_signals and ctx.facing_bet:
        opp_id = "OPP"
        if opponent_did_reveal_then_raise(ctx.round_history, opp_id):
            adjust -= SIGNAL_ADJUST
        if opponent_did_peek_then_raise(ctx.round_history, opp_id):
            adjust += SIGNAL_ADJUST
    return baseline_bet_decision(wp, ctx.facing_bet, ctx.call_amount, ctx.pot, ctx.raises_used, adjust)


def s7_bet(ctx):
    return _s7_bet_core(ctx, use_signals=True)


def s8_bet(ctx):
    return _s7_bet_core(ctx, use_signals=False)


# --- S9: PotAwareBluff (v0-B 新規。S4 の固定30%頻度をポット感応条件に置換) ---

def s9_assist(ctx):
    s = ctx.me.hand_sum
    if s <= 9 and ctx.pot >= 3 * ctx.bet_unit:
        card = highest_unrevealed_card(ctx.me)
        if card is not None:
            return ("reveal", card)
    return no_assist(ctx)


def s9_bet(ctx):
    s = ctx.me.hand_sum
    bluffed_this_turn = bool(ctx.round_history) and ctx.round_history[-1]["actor"] == "SELF" and \
        ctx.round_history[-1]["phase"] == "assist" and ctx.round_history[-1]["type"] == "reveal"
    if bluffed_this_turn:
        return "raise" if ctx.facing_bet and ctx.raises_used < KNOBS["raise_cap"] else (
            "call" if ctx.facing_bet else "bet")
    if s >= 13:
        return "raise" if ctx.facing_bet and ctx.raises_used < KNOBS["raise_cap"] else (
            "call" if ctx.facing_bet else "bet")
    wp = ctx.naive_win_prob()
    return baseline_bet_decision(wp, ctx.facing_bet, ctx.call_amount, ctx.pot, ctx.raises_used)


STRATEGIES = {
    "S1_NeverPeek": (s1_assist, s1_bet),
    "S2_AlwaysPeek": (s2_assist, s2_bet),
    "S3_MarginalPeek": (s3_assist, s3_bet),
    "S4_BluffReveal": (s4_assist, s4_bet),
    "S5_HonestValue": (s5_assist, s5_bet),
    "S6_ShortStackPush": (s6_assist, s6_bet),
    "S7_SignalReader": (s7_assist, s7_bet),
    "S8_SignalBlind": (s8_assist, s8_bet),
    "S9_PotAwareBluff": (s9_assist, s9_bet),
}
STRATEGY_NAMES = list(STRATEGIES.keys())

PEEK_FLIP_TRACKED = {"S2_AlwaysPeek", "S3_MarginalPeek", "S7_SignalReader", "S8_SignalBlind"}

# ---------------------------------------------------------------------------
# ラウンド実装
# ---------------------------------------------------------------------------


def deal_hand(rng):
    return tuple(rng.sample(ALL_VALUES, 3))


def play_round(rng, pools, strat_names, first_actor, stats, pair_key, round_num):
    """1ラウンドを実行し、pools (dict 'A'/'B' -> int) を直接更新する。
    戻り値: ラウンド勝者 'A'|'B'|'split' (フォールド勝利も含む)"""

    hands = {"A": deal_hand(rng), "B": deal_hand(rng)}
    state = {"A": PlayerRoundState(hands["A"]), "B": PlayerRoundState(hands["B"])}

    ante_amt = ante_for_round(round_num)
    bet_unit = bet_unit_for_round(round_num)
    band = round_band(round_num)

    # 1. アンテ (プール残を超える場合は残額全部でアンテ=実質オールイン。両者払えない場合も同処理)
    ante_pot = 0
    for who in ("A", "B"):
        pay = min(ante_amt, pools[who])
        pools[who] -= pay
        ante_pot += pay

    commits = {"A": 0, "B": 0}
    bet_level = 0
    raises_used = 0
    round_history = []  # actor: 'A'/'B', phase: 'assist'/'bet', type: str
    prev_action = None
    to_act = first_actor
    other = {"A": "B", "B": "A"}

    def pot_now():
        return ante_pot + commits["A"] + commits["B"]

    winner = None  # 'A' / 'B' / None(showdown)
    safety_counter = 0
    pending_allin_events = []  # (player, strat_name, pre_action_pool, pre_action_total_chips)

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

        # --- 補助行動フェーズ ---
        if pools[p] >= KNOBS["peek_cost"]:
            stats.peek_by_band[(strat_name, stats.pot_band(pot_now()))][1] += 1

        ctx = DecisionContext(
            rng=rng, me=state[p], opp=state[opp], me_pool=pools[p], opp_pool=pools[opp],
            pot=pot_now(), call_amount=call_amount, bet_level=bet_level, raises_used=raises_used,
            round_history=[_relabel(ev, p) for ev in round_history], facing_bet=facing_bet,
            bet_unit=bet_unit,
        )
        assist_type, assist_val = assist_fn(ctx)

        did_peek = False
        pre_peek_action = None
        nonpublic = [c for c in state[opp].hand if c not in state[opp].revealed]
        if assist_type == "peek" and pools[p] >= KNOBS["peek_cost"] and nonpublic:
            # 相手の手札が既に全て公開済みの場合、覗く対象が無いため覗きは成立しない
            # 転換率計測用: 覗く前の判断を記録
            if strat_name in PEEK_FLIP_TRACKED:
                pre_wp = ctx.naive_win_prob()
                pre_peek_action = baseline_bet_decision(
                    pre_wp, facing_bet, call_amount, pot_now(), raises_used)
            pools[p] -= KNOBS["peek_cost"]  # 焼却 (ポットに入らない)
            peeked_card = rng.choice(nonpublic)
            state[p].peeked_known.add(peeked_card)
            did_peek = True
            round_history.append({"actor": p, "phase": "assist", "type": "peek"})
            stats.peek_by_band[(strat_name, stats.pot_band(pot_now()))][0] += 1
        elif assist_type == "reveal" and assist_val is not None and assist_val not in state[p].revealed:
            state[p].revealed.add(assist_val)
            round_history.append({"actor": p, "phase": "assist", "type": "reveal"})

        # --- ベット行動フェーズ ---
        ctx2 = DecisionContext(
            rng=rng, me=state[p], opp=state[opp], me_pool=pools[p], opp_pool=pools[opp],
            pot=pot_now(), call_amount=call_amount, bet_level=bet_level, raises_used=raises_used,
            round_history=[_relabel(ev, p) for ev in round_history], facing_bet=facing_bet,
            bet_unit=bet_unit,
        )
        intent = bet_fn(ctx2)

        if did_peek and strat_name in PEEK_FLIP_TRACKED:
            stats.peek_flip[strat_name][1] += 1
            if intent != pre_peek_action:
                stats.peek_flip[strat_name][0] += 1

        stats.decision_count[strat_name] += 1
        # 指標8: 強制手 (合法な選択肢が実質{オールイン,フォールド}の二択しかない = コール額≧自プール残)
        stats.forced_hand[band][1] += 1
        if facing_bet and call_amount >= pools[p]:
            stats.forced_hand[band][0] += 1
        # オールイン統計用: ペイメント適用前のプール状態を記録しておく
        pre_action_pool = pools[p]
        pre_action_total_chips = pools["A"] + commits["A"] + pools["B"] + commits["B"] + ante_pot
        resolved, ends_betting, is_raise_like = _legalize_and_apply(
            intent, p, pools, commits, bet_level, raises_used, call_amount, facing_bet, bet_unit)
        action_type, new_bet_level, new_raises_used, resolved_allin = resolved
        bet_level = new_bet_level
        raises_used = new_raises_used

        round_history.append({"actor": p, "phase": "bet", "type": action_type})

        if resolved_allin:
            stats.allin_count[strat_name] += 1
            share = pre_action_pool / pre_action_total_chips if pre_action_total_chips > 0 else 0.0
            pending_allin_events.append((p, strat_name, share))
        if action_type == "check" and prev_action == "check":
            ends_betting = True  # チェック-チェックでベッティング終了 (標準ルール)
        if action_type == "fold":
            winner = opp
            break
        if ends_betting:
            winner = None  # showdown
            break
        prev_action = action_type
        to_act = opp

    # --- ポット精算 ---
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
        stats.showdown_pot_by_band[band].append(pot)  # 指標9: ショーダウン時ポットサイズ (フォールド終了は含まない)
        sa, sb = state["A"].hand_sum, state["B"].hand_sum
        if sa > sb:
            pools["A"] += pot
            round_winner = "A"
        elif sb > sa:
            pools["B"] += pot
            round_winner = "B"
        else:
            half = pot // 2  # 端数1は焼却
            pools["A"] += half
            pools["B"] += half
            round_winner = "split"

    for player, strat_name, share in pending_allin_events:
        if round_winner == "split":
            outcome = 0.5
        elif round_winner == player:
            outcome = 1.0
        else:
            outcome = 0.0
        stats.allin_outcome[strat_name][0] += outcome
        stats.allin_outcome[strat_name][1] += 1
        stats.allin_pool_share[strat_name][0] += share
        stats.allin_pool_share[strat_name][1] += 1

    return round_winner


def _relabel(ev, me):
    """round_history の actor を 'SELF'/'OPP' に変換したコピーを返す (戦略から見た視点)"""
    e = dict(ev)
    e["actor"] = "SELF" if ev["actor"] == me else "OPP"
    return e


def _legalize_and_apply(intent, p, pools, commits, bet_level, raises_used, call_amount, facing_bet, bet_unit):
    """意図されたアクションをルール上合法な形に補正し、pools/commits を更新する。
    戻り値: ((action_type, new_bet_level, new_raises_used, resolved_allin), ends_betting, is_raise_like)"""
    pool = pools[p]

    def pay(amount):
        amount = min(amount, pool)
        pools[p] -= amount
        commits[p] += amount
        return amount, (pools[p] == 0)

    if intent == "check":
        if facing_bet:
            intent = "call"  # 防御的フォールバック (通常発生しない)
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
            # raise は facing_bet 前提。防御的に bet として扱う
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
                # レイズ額を積みきれずオールイン
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


# ---------------------------------------------------------------------------
# ゲーム実装
# ---------------------------------------------------------------------------


def play_game(rng, strat_A, strat_B, first_actor, stats, pair_key):
    pools = {"A": KNOBS["pool_start"], "B": KNOBS["pool_start"]}
    strat_names = {"A": strat_A, "B": strat_B}
    pool_history = []  # (pool_A, pool_B) after each round
    first = first_actor
    other = {"A": "B", "B": "A"}
    rounds_played = 0

    busted = False
    for rnd in range(1, KNOBS["max_rounds"] + 1):
        rounds_played = rnd
        play_round(rng, pools, strat_names, first, stats, pair_key, rnd)
        pool_history.append((pools["A"], pools["B"]))
        if pools["A"] <= 0 or pools["B"] <= 0:
            busted = True
            break
        first = other[first]

    if pools["A"] <= 0 and pools["B"] <= 0:
        winner = "draw"
    elif pools["A"] <= 0:
        winner = "B"
    elif pools["B"] <= 0:
        winner = "A"
    elif rounds_played >= KNOBS["max_rounds"]:
        if pools["A"] > pools["B"]:
            winner = "A"
        elif pools["B"] > pools["A"]:
            winner = "B"
        else:
            winner = "draw"
    else:
        winner = "draw"  # 到達しないはずだが防御的に

    # --- 指標10: 決着様式 ---
    if busted:
        stats.game_end_mode["bust"] += 1
    elif winner == "draw":
        stats.game_end_mode["timeout_draw"] += 1
    else:
        stats.game_end_mode["timeout_win"] += 1

    # --- スパイラル検出 ---
    disadvantaged = None
    spiral_round = None
    for idx, (pa, pb) in enumerate(pool_history):
        if pa <= 0 or pb <= 0:
            lo, hi = min(pa, pb), max(pa, pb)
            if hi >= 2 * max(lo, 0) and hi > 0:
                disadvantaged = "A" if pa < pb else "B"
                spiral_round = idx + 1
            break
        if pa == 0 and pb == 0:
            break
        lo, hi = (pa, pb) if pa <= pb else (pb, pa)
        if lo > 0 and hi >= 2 * lo:
            disadvantaged = "A" if pa < pb else "B"
            spiral_round = idx + 1
            break

    if disadvantaged is not None:
        if winner == "draw":
            outcome = 0.5
        elif winner == disadvantaged:
            outcome = 1.0
        else:
            outcome = 0.0
        stats.spiral_records.append((pair_key, strat_names[disadvantaged], outcome))

    stats.game_lengths.append((pair_key, rounds_played))

    return winner, rounds_played


# ---------------------------------------------------------------------------
# トーナメント実行
# ---------------------------------------------------------------------------


def make_seed(i, j, game_idx, first_flag):
    return BASE_SEED * 1_000_000_000 + i * 10_000_000 + j * 100_000 + game_idx * 2 + first_flag


def run_pair(i, j, name_i, name_j, stats, games_per_pair=None):
    """strategy i vs strategy j。戻り値: strategy i の勝率 (0.5 for draw)"""
    if games_per_pair is None:
        games_per_pair = GAMES_PER_PAIR  # モジュール属性を呼び出し時に解決 (デフォルト引数の束縛時評価を回避)
    pair_key = f"{name_i}_vs_{name_j}"
    wins_i = 0.0
    half = games_per_pair // 2
    for g in range(games_per_pair):
        first_is_i = g < half  # 前半1000はi(=A側)が先手, 後半1000はj(=B側)が先手
        seed = make_seed(i, j, g, 0 if first_is_i else 1)
        rng = random.Random(seed)
        first_actor = "A" if first_is_i else "B"
        winner, _ = play_game(rng, name_i, name_j, first_actor, stats, pair_key)
        if winner == "A":
            wins_i += 1.0
        elif winner == "draw":
            wins_i += 0.5
    return wins_i / games_per_pair


def run_tournament():
    stats = Stats()
    n = len(STRATEGY_NAMES)
    matrix = [[None] * n for _ in range(n)]
    t0 = time.time()
    pair_count = 0
    for i in range(n):
        for j in range(i, n):
            name_i, name_j = STRATEGY_NAMES[i], STRATEGY_NAMES[j]
            wr_i = run_pair(i, j, name_i, name_j, stats)
            matrix[i][j] = wr_i
            matrix[j][i] = 1.0 - wr_i
            pair_count += 1
    elapsed = time.time() - t0
    return matrix, stats, elapsed, pair_count


# ---------------------------------------------------------------------------
# 指標集計と出力
# ---------------------------------------------------------------------------


def field_averages(matrix):
    n = len(STRATEGY_NAMES)
    return [sum(matrix[i]) / n for i in range(n)]


def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)


def build_report(matrix, stats, elapsed, pair_count, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    names = STRATEGY_NAMES
    n = len(names)

    # 1. 勝率マトリクス
    write_csv(os.path.join(out_dir, "win_rate_matrix.csv"),
              ["strategy"] + names,
              [[names[i]] + [f"{matrix[i][j]:.4f}" for j in range(n)] for i in range(n)])

    # 2. 対フィールド平均勝率
    favg = field_averages(matrix)
    ranked = sorted(zip(names, favg), key=lambda x: -x[1])
    write_csv(os.path.join(out_dir, "field_average.csv"),
              ["strategy", "field_avg_winrate"],
              [[nm, f"{v:.4f}"] for nm, v in ranked])

    # 3. 覗き発動率とポットサイズの相関
    band_names = [b[0] for b in POT_BANDS]
    rows3 = []
    for nm in names:
        row = [nm]
        for b in band_names:
            peeks, opps = stats.peek_by_band.get((nm, b), [0, 0])
            rate = peeks / opps if opps else None
            row.append(f"{rate:.4f}" if rate is not None else "n/a")
            row.append(opps)
        rows3.append(row)
    header3 = ["strategy"]
    for b in band_names:
        header3 += [f"peek_rate_{b}", f"opportunities_{b}"]
    write_csv(os.path.join(out_dir, "peek_rate_by_pot_band.csv"), header3, rows3)

    # 4. 覗きの判断転換率
    rows4 = []
    for nm in ["S2_AlwaysPeek", "S3_MarginalPeek", "S7_SignalReader", "S8_SignalBlind"]:
        flips, total = stats.peek_flip.get(nm, [0, 0])
        rate = flips / total if total else None
        rows4.append([nm, flips, total, f"{rate:.4f}" if rate is not None else "n/a"])
    write_csv(os.path.join(out_dir, "peek_flip_rate.csv"),
              ["strategy", "flip_count", "peek_count", "flip_rate"], rows4)

    # 5. スパイラル指標
    by_pair = defaultdict(lambda: [0.0, 0])
    by_strategy = defaultdict(lambda: [0.0, 0])
    overall = [0.0, 0]
    for pair_key, strat, outcome in stats.spiral_records:
        by_pair[(pair_key, strat)][0] += outcome
        by_pair[(pair_key, strat)][1] += 1
        by_strategy[strat][0] += outcome
        by_strategy[strat][1] += 1
        overall[0] += outcome
        overall[1] += 1
    rows5_pair = [[pk, st, f"{s/c:.4f}", c] for (pk, st), (s, c) in sorted(by_pair.items())]
    write_csv(os.path.join(out_dir, "spiral_by_pair.csv"),
              ["pair", "disadvantaged_strategy", "eventual_winrate", "n_events"], rows5_pair)
    rows5_strat = [[st, f"{s/c:.4f}", c] for st, (s, c) in sorted(by_strategy.items())]
    write_csv(os.path.join(out_dir, "spiral_by_strategy.csv"),
              ["disadvantaged_strategy", "eventual_winrate", "n_events"], rows5_strat)
    overall_rate = overall[0] / overall[1] if overall[1] else None

    # 6. ゲーム長分布
    lengths = [l for _, l in stats.game_lengths]
    hist = defaultdict(int)
    for l in lengths:
        hist[l] += 1
    rows6 = [[l, hist[l]] for l in sorted(hist)]
    write_csv(os.path.join(out_dir, "game_length_histogram.csv"), ["rounds", "count"], rows6)

    by_pair_len = defaultdict(lambda: [0, 0])
    for pk, l in stats.game_lengths:
        by_pair_len[pk][0] += l
        by_pair_len[pk][1] += 1
    rows6b = [[pk, f"{s/c:.2f}", c] for pk, (s, c) in sorted(by_pair_len.items())]
    write_csv(os.path.join(out_dir, "game_length_by_pair.csv"), ["pair", "avg_rounds", "n_games"], rows6b)

    overall_avg_len = sum(lengths) / len(lengths) if lengths else None

    # 7. オールイン統計
    rows7 = []
    for nm in names:
        dc = stats.decision_count.get(nm, 0)
        ac = stats.allin_count.get(nm, 0)
        rate = ac / dc if dc else None
        win_sum, win_n = stats.allin_outcome.get(nm, [0.0, 0])
        win_rate = win_sum / win_n if win_n else None
        share_sum, share_n = stats.allin_pool_share.get(nm, [0.0, 0])
        avg_share = share_sum / share_n if share_n else None
        rows7.append([
            nm, ac, dc,
            f"{rate:.4f}" if rate is not None else "n/a",
            f"{win_rate:.4f}" if win_rate is not None else "n/a",
            f"{avg_share:.4f}" if avg_share is not None else "n/a",
        ])
    write_csv(os.path.join(out_dir, "allin_stats.csv"),
              ["strategy", "allin_count", "decision_count", "allin_rate",
               "allin_round_winrate", "avg_pool_share_at_allin"], rows7)

    # 覗き実行数 n (kill4' の n>=500 フィルタ判定用の参考値。全ポット帯合算)
    peek_exec_total = defaultdict(int)
    for (nm, b), (peeks, opps) in stats.peek_by_band.items():
        peek_exec_total[nm] += peeks
    rows_peekn = [[nm, peek_exec_total.get(nm, 0)] for nm in names]
    write_csv(os.path.join(out_dir, "peek_exec_total.csv"), ["strategy", "peek_exec_n"], rows_peekn)

    # 8. 強制手率 (ラウンド帯別)
    rows8 = []
    for b in ROUND_BANDS:
        forced, total = stats.forced_hand.get(b, [0, 0])
        rate = forced / total if total else None
        rows8.append([b, forced, total, f"{rate:.4f}" if rate is not None else "n/a"])
    write_csv(os.path.join(out_dir, "forced_hand_rate.csv"),
              ["round_band", "forced_count", "decision_count", "forced_rate"], rows8)

    # 9. 帯別ショーダウンポット分布
    rows9 = []
    for b in ROUND_BANDS:
        pots = stats.showdown_pot_by_band.get(b, [])
        if pots:
            rows9.append([b, len(pots), f"{sum(pots)/len(pots):.2f}",
                          f"{statistics.median(pots):.2f}", max(pots)])
        else:
            rows9.append([b, 0, "n/a", "n/a", "n/a"])
    write_csv(os.path.join(out_dir, "showdown_pot_by_band.csv"),
              ["round_band", "n_showdowns", "mean_pot", "median_pot", "max_pot"], rows9)

    # 10. ゲーム決着様式の内訳
    total_games_end = sum(stats.game_end_mode.values())
    rows10 = []
    for mode in ("bust", "timeout_win", "timeout_draw"):
        c = stats.game_end_mode.get(mode, 0)
        rate = c / total_games_end if total_games_end else None
        rows10.append([mode, c, f"{rate:.4f}" if rate is not None else "n/a"])
    write_csv(os.path.join(out_dir, "game_end_mode.csv"), ["mode", "count", "rate"], rows10)

    # --- summary.md ---
    lines = []
    lines.append("# 思考課金ポーカー v0-B(蘇生版) モンテカルロ検証 結果サマリ\n")
    lines.append(f"- 実行日時(BASE_SEED基準): {BASE_SEED}")
    lines.append(f"- 総ペア数: {pair_count} ({n}戦略の重複組合せ, 対角含む)")
    lines.append(f"- ペアあたりゲーム数: {GAMES_PER_PAIR}")
    lines.append(f"- 総ゲーム数: {pair_count * GAMES_PER_PAIR}")
    lines.append(f"- 実行時間: {elapsed:.1f}秒")
    lines.append(f"- 無限ループガード発動回数: {len(stats.loop_guard_triggers)}\n")

    lines.append("## 1. 勝率マトリクス (行 vs 列, 引き分け0.5勝)\n")
    header = "| strategy | " + " | ".join(names) + " |"
    sep = "|---" * (n + 1) + "|"
    lines.append(header)
    lines.append(sep)
    for i in range(n):
        row = "| " + names[i] + " | " + " | ".join(f"{matrix[i][j]:.3f}" for j in range(n)) + " |"
        lines.append(row)
    lines.append("")

    lines.append("## 2. 対フィールド平均勝率\n")
    lines.append("| strategy | field_avg_winrate |")
    lines.append("|---|---|")
    for nm, v in ranked:
        lines.append(f"| {nm} | {v:.4f} |")
    lines.append("")

    lines.append("## 3. 覗き発動率 x ポットサイズ帯 (n=覗き実行総数)\n")
    lines.append("| strategy | n | " + " | ".join(band_names) + " |")
    lines.append("|---" * (len(band_names) + 2) + "|")
    for nm in names:
        cells = []
        for b in band_names:
            peeks, opps = stats.peek_by_band.get((nm, b), [0, 0])
            rate = peeks / opps if opps else None
            cells.append(f"{rate:.3f}" if rate is not None else "n/a")
        lines.append(f"| {nm} | {peek_exec_total.get(nm, 0)} | " + " | ".join(cells) + " |")
    lines.append("")

    lines.append("## 4. 覗きの判断転換率 (S2/S3/S7, 参考としてS8も併記)\n")
    lines.append("| strategy | flip_count | peek_count | flip_rate |")
    lines.append("|---|---|---|---|")
    for nm in ["S2_AlwaysPeek", "S3_MarginalPeek", "S7_SignalReader", "S8_SignalBlind"]:
        flips, total = stats.peek_flip.get(nm, [0, 0])
        rate = flips / total if total else None
        rate_str = f"{rate:.4f}" if rate is not None else "n/a"
        lines.append(f"| {nm} | {flips} | {total} | {rate_str} |")
    lines.append("")

    lines.append("## 5. スパイラル指標 (2:1劣勢からの逆転率)\n")
    lines.append(f"- 全体平均: {f'{overall_rate:.4f}' if overall_rate is not None else 'n/a'} "
                  f"(n={overall[1]})\n")
    lines.append("戦略別 (劣勢側だった場合の最終勝率):\n")
    lines.append("| strategy | eventual_winrate | n_events |")
    lines.append("|---|---|---|")
    for st, (s, c) in sorted(by_strategy.items()):
        lines.append(f"| {st} | {s/c:.4f} | {c} |")
    lines.append("")
    lines.append("(ペア別の詳細は spiral_by_pair.csv を参照)\n")

    lines.append("## 6. ゲーム長分布\n")
    lines.append(f"- 全体平均ラウンド数: {f'{overall_avg_len:.2f}' if overall_avg_len is not None else 'n/a'}")
    lines.append(f"- 最短: {min(lengths) if lengths else 'n/a'}, 最長: {max(lengths) if lengths else 'n/a'}")
    lines.append(f"- 30ラウンド到達(タイムアウト)率: "
                  f"{sum(1 for l in lengths if l >= 30) / len(lengths):.4f}\n")
    lines.append("(ヒストグラム全体は game_length_histogram.csv, ペア別平均は game_length_by_pair.csv を参照)\n")

    lines.append("## 7. オールイン統計\n")
    lines.append("| strategy | allin_count | decision_count | allin_rate | allin_round_winrate | avg_pool_share_at_allin |")
    lines.append("|---|---|---|---|---|---|")
    for nm, ac, dc, rate, win_rate, avg_share in rows7:
        lines.append(f"| {nm} | {ac} | {dc} | {rate} | {win_rate} | {avg_share} |")
    lines.append("")

    lines.append("## 8. 強制手率 (ラウンド帯別。合法な選択肢が実質{オールイン,フォールド}のみの決断の割合)\n")
    lines.append("| round_band | forced_count | decision_count | forced_rate |")
    lines.append("|---|---|---|---|")
    for b, forced, total, rate in rows8:
        lines.append(f"| {b} | {forced} | {total} | {rate} |")
    lines.append("")

    lines.append("## 9. 帯別ショーダウンポット分布\n")
    lines.append("| round_band | n_showdowns | mean_pot | median_pot | max_pot |")
    lines.append("|---|---|---|---|---|")
    for b, ns, mean_p, med_p, max_p in rows9:
        lines.append(f"| {b} | {ns} | {mean_p} | {med_p} | {max_p} |")
    lines.append("")

    lines.append("## 10. ゲーム決着様式の内訳\n")
    lines.append("| mode | count | rate |")
    lines.append("|---|---|---|")
    for mode, c, rate in rows10:
        lines.append(f"| {mode} | {c} | {rate} |")
    lines.append(f"\n(全{total_games_end}ゲーム中の内訳。bust=プール0到達, timeout_win/draw=R30到達)\n")

    if stats.loop_guard_triggers:
        lines.append("## 無限ループガード発動ケース\n")
        for msg in stats.loop_guard_triggers[:50]:
            lines.append(f"- {msg}")
        if len(stats.loop_guard_triggers) > 50:
            lines.append(f"- ... 他 {len(stats.loop_guard_triggers)-50} 件")
        lines.append("")

    with open(os.path.join(out_dir, "summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # 生データ(再現性確認用)
    with open(os.path.join(out_dir, "run_meta.json"), "w", encoding="utf-8") as f:
        json.dump({
            "base_seed": BASE_SEED,
            "games_per_pair": GAMES_PER_PAIR,
            "pair_count": pair_count,
            "total_games": pair_count * GAMES_PER_PAIR,
            "elapsed_sec": elapsed,
            "knobs": KNOBS,
            "ante_schedule": [[("inf" if lr == float("inf") else lr), amt] for lr, amt in ANTE_SCHEDULE],
            "call_margin": CALL_MARGIN,
            "signal_adjust": SIGNAL_ADJUST,
            "strategies": names,
            "loop_guard_trigger_count": len(stats.loop_guard_triggers),
        }, f, ensure_ascii=False, indent=2)

    return {
        "overall_spiral_rate": overall_rate,
        "overall_avg_length": overall_avg_len,
    }


def main():
    out_dir = os.path.join(os.path.dirname(__file__), "results_v0b")
    matrix, stats, elapsed, pair_count = run_tournament()
    summary = build_report(matrix, stats, elapsed, pair_count, out_dir)
    print(f"done in {elapsed:.1f}s, pairs={pair_count}, "
          f"spiral_overall={summary['overall_spiral_rate']}, "
          f"avg_len={summary['overall_avg_length']}")


if __name__ == "__main__":
    main()
