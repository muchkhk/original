"""
事前登録：シュレディンガー×ロンデル 工程3「不発税下の市場再検証」

仕様は同ディレクトリの凍結済み事前登録文書
（事前登録_シュレディンガー工程3_kill基準.md）の§0〜§9・§Dに厳密に従う。
Node.js不使用。標準ライブラリのみで実装する。

工程2実装(simulator_process2.py)からの流用:
  - posterior_PH（残余プールの閉形式）: 完全流用
  - bet_direction / value_class: 完全流用
  - gen_game_dice（ダイス生成・系統1）: 完全流用
  - percentile-based paired bootstrap（対応ペア差用）: 流用（Wilson score
    intervalは単一比率用に本ファイルで新規実装。§D-5参照）

工程3固有の新規実装:
  - 移動選択（M-fix/M-rand/M-value/M-seek/M-deny）
  - 座席対称化（同一ダイスで先手プレイヤーを入れ替えた2局を対にして平均）
  - Wilson score interval（単一比率のCI95。§D-5の解釈）
  - M-denyの仮想相手モデル（§D-8の凍結後修正）
"""

import csv
import json
import math
import random
import statistics
import sys
import time

SEED_BASE = 20260716
N = 100_000
B_BOOT = 10_000
PRICES = [0, 0.25, 0.5, 1.0]

D3_VALUES = [1, 2, 3, 10, 11, 12]
H_SET = frozenset({10, 11, 12})
L_SET = frozenset({1, 2, 3})

INFO_POLICIES = ["blind", "imm", "marginal", "prop"]
MOVE_POLICIES = ["M-fix", "M-rand", "M-value", "M-seek", "M-deny"]

# roster (name -> (info, move))
ROSTER = {
    "S-blind/M-fix": ("blind", "M-fix"),
    "S-blind/M-rand": ("blind", "M-rand"),
    "S-blind/M-value": ("blind", "M-value"),
    "S-blind/M-deny": ("blind", "M-deny"),
    "S-imm/M-fix": ("imm", "M-fix"),
    "S-imm/M-seek": ("imm", "M-seek"),
    "S-imm/M-value": ("imm", "M-value"),
    "S-marg/M-fix": ("marginal", "M-fix"),
    "S-marg/M-seek": ("marginal", "M-seek"),
    "S-marg/M-value": ("marginal", "M-value"),
    "S-prop/M-fix": ("prop", "M-fix"),
}
BLIND_VARIANTS = ["S-blind/M-fix", "S-blind/M-rand", "S-blind/M-value", "S-blind/M-deny"]
TESTED_STRATS = ["S-imm/M-fix", "S-imm/M-seek", "S-imm/M-value",
                 "S-marg/M-fix", "S-marg/M-seek", "S-marg/M-value"]
IMM_STRATS = ["S-imm/M-fix", "S-imm/M-seek", "S-imm/M-value"]
MARG_STRATS = ["S-marg/M-fix", "S-marg/M-seek", "S-marg/M-value"]
K_E2_MOVE = ["S-imm/M-seek", "S-imm/M-value", "S-marg/M-seek", "S-marg/M-value"]
K_E2_FIX = ["S-imm/M-fix", "S-marg/M-fix"]


def posterior_PH(known_values_set):
    residual = [v for v in D3_VALUES if v not in known_values_set]
    h = sum(1 for v in residual if v in H_SET)
    return h / len(residual)


def bet_direction(p_h):
    return "H" if p_h >= 0.5 else "L"


def value_class(v):
    return "H" if v in H_SET else "L"


def gen_game_dice(i):
    rng1 = random.Random(SEED_BASE + i)
    perm = rng1.sample(D3_VALUES, 6)
    rng1c = random.Random(SEED_BASE + i)
    f0d = [rng1c.choice(D3_VALUES) for _ in range(6)]
    return perm, f0d


def move_rng_for(i, seat_flag):
    """New stream for M-rand's randomness (not covered by process-2's original 3 streams,
    since movement is new in process-3). Independent per seat-symmetrization pairing."""
    return random.Random((4 * SEED_BASE + i) * 2 + seat_flag)


# ---------------------------------------------------------------------------
# Movement policies
# ---------------------------------------------------------------------------

def confidence_pick(unqueried_sorted, own_known):
    """Shared logic for M-value and M-seek (proven identical by exchangeability: a
    self-peeked-but-unqueried position always has confidence 1.0, strictly higher than
    any not-yet-personally-known position, so both policies reduce to the same rule)."""
    own_known_unqueried = [p for p in unqueried_sorted if p in own_known]
    if own_known_unqueried:
        return own_known_unqueried[0]
    return unqueried_sorted[0]


def move_deny_choice(unqueried_sorted, own_known, revealed_positions, revealed_values, k):
    """§D-8: assumed opponent follows the OLD fixed schedule (round j -> position j,
    1-indexed) as a peek-targeting habit, regardless of what's actually asked. For each
    past round j=1..k-1, if position (j-1) [0-indexed] was NOT yet actually revealed by
    then, and the assumed opponent's confidence (using only public info as of round j's
    peek phase) falls in (0.35, 0.65), assume they peeked it. Avoid all such positions
    that are still unqueried now; fall back to confidence_pick if none remain avoidable."""
    danger = set()
    for j in range(1, k):
        slot = j - 1
        already_public_by_then = slot in revealed_positions[: j - 1]
        if already_public_by_then:
            continue
        public_values_as_of_j = set(revealed_values[: j - 1])
        assumed_conf = posterior_PH(public_values_as_of_j)
        if 0.35 < assumed_conf < 0.65:
            danger.add(slot)
    candidates = [p for p in unqueried_sorted if p not in danger]
    if not candidates:
        candidates = unqueried_sorted
    return confidence_pick(candidates, own_known)


def choose_move(move_policy, unqueried_sorted, own_known, revealed_positions, revealed_values, k, rng):
    if move_policy == "M-fix":
        return unqueried_sorted[0]
    if move_policy == "M-rand":
        return rng.choice(unqueried_sorted)
    if move_policy in ("M-value", "M-seek"):
        return confidence_pick(unqueried_sorted, own_known)
    if move_policy == "M-deny":
        return move_deny_choice(unqueried_sorted, own_known, revealed_positions, revealed_values, k)
    raise ValueError(move_policy)


# ---------------------------------------------------------------------------
# Info policies (peek decisions) and betting
# ---------------------------------------------------------------------------

def eligible_to_peek(is_f0d, own_known, unqueried_set, position):
    if position not in unqueried_set or position in own_known:
        return False
    if is_f0d:
        return True
    residual_size = 6 - len(own_known)
    return residual_size > 1


def own_ph_for_position(is_f0d, own_known, position):
    if position in own_known:
        return 1.0 if value_class(own_known[position]) == "H" else 0.0
    if is_f0d:
        return 0.5
    return posterior_PH(set(own_known.values()))


def peek_phase(is_f0d, info_policy, own_known, unqueried_set, target_pos, true_values,
                prop_state, k, peek_log):
    """Mutates own_known (adds peeked position->value) and peek_log (list of peeked positions).
    Returns nothing. prop_state: mutable list [next_schedule_idx] for the prop policy."""
    if info_policy == "blind":
        return
    if info_policy in ("imm", "marginal"):
        if target_pos in own_known:
            return
        if not eligible_to_peek(is_f0d, own_known, unqueried_set, target_pos):
            return
        if info_policy == "marginal":
            pre_p = own_ph_for_position(is_f0d, own_known, target_pos)
            if not (0.35 < pre_p < 0.65):
                return
        own_known[target_pos] = true_values[target_pos]
        peek_log.append(target_pos)
        return
    if info_policy == "prop":
        schedule = [4, 5]  # 0-indexed positions 5,6
        if k > 2:
            return
        cand = schedule[k - 1]
        if cand in own_known:
            return
        if not eligible_to_peek(is_f0d, own_known, unqueried_set, cand):
            return
        own_known[cand] = true_values[cand]
        peek_log.append(cand)
        return
    raise ValueError(info_policy)


# ---------------------------------------------------------------------------
# Full two-player game
# ---------------------------------------------------------------------------

def play_game(is_f0d, true_values, info_a, move_a, info_b, move_b, a_controls_first, move_rng):
    """Simulates one full game (4 rounds) between player A and player B on a shared die.
    a_controls_first: True -> A controls rounds 1,3; B controls 2,4. False -> reversed.
    Returns dict with per-player bet_score, peek_count, peek_log (positions peeked),
    revealed_positions (order), correct_by_round for each player."""
    known_a, known_b = {}, {}
    peek_log_a, peek_log_b = [], []
    score_a = score_b = 0
    unqueried = set(range(6))
    revealed_positions = []
    revealed_values = []

    for k in range(1, 5):
        a_is_controller = a_controls_first if k in (1, 3) else (not a_controls_first)
        unqueried_sorted = sorted(unqueried)

        if a_is_controller:
            target_pos = choose_move(move_a, unqueried_sorted, known_a, revealed_positions,
                                      revealed_values, k, move_rng)
        else:
            target_pos = choose_move(move_b, unqueried_sorted, known_b, revealed_positions,
                                      revealed_values, k, move_rng)

        peek_phase(is_f0d, info_a, known_a, unqueried, target_pos, true_values, None, k, peek_log_a)
        peek_phase(is_f0d, info_b, known_b, unqueried, target_pos, true_values, None, k, peek_log_b)

        p_a = own_ph_for_position(is_f0d, known_a, target_pos)
        p_b = own_ph_for_position(is_f0d, known_b, target_pos)
        bet_a = bet_direction(p_a)
        bet_b = bet_direction(p_b)

        actual = value_class(true_values[target_pos])
        score_a += 1 if bet_a == actual else -1
        score_b += 1 if bet_b == actual else -1

        revealed_positions.append(target_pos)
        revealed_values.append(true_values[target_pos])
        unqueried.discard(target_pos)
        known_a[target_pos] = true_values[target_pos]
        known_b[target_pos] = true_values[target_pos]

    dud_a = sum(1 for p in peek_log_a if p not in revealed_positions)
    dud_b = sum(1 for p in peek_log_b if p not in revealed_positions)

    return {
        "score_a": score_a, "score_b": score_b,
        "peek_count_a": len(peek_log_a), "peek_count_b": len(peek_log_b),
        "dud_a": dud_a, "dud_b": dud_b,
    }


def win_indicator(x_score, free_score):
    if x_score > free_score:
        return 1.0
    if x_score == free_score:
        return 0.5
    return 0.0


def simulate_matchup(p_name, b_name, is_f0d=False, n=None):
    """P vs B, seat-symmetrized (2 games per die index i, averaged as win-indicators
    per price, not as raw scores -- see module notes). Returns per-game-slot raw data
    (price-independent) plus dud counts (reference only)."""
    n = n or N
    info_p, move_p = ROSTER[p_name]
    info_b, move_b = ROSTER[b_name]
    data = {k: [0.0] * n for k in
            ("g1_bs_p", "g1_pc_p", "g1_bs_b", "g1_pc_b",
             "g2_bs_p", "g2_pc_p", "g2_bs_b", "g2_pc_b",
             "dud_p", "dud_b")}

    for i in range(n):
        perm, f0d = gen_game_dice(i)
        true_values = f0d if is_f0d else perm

        rng0 = move_rng_for(i, 0)
        g1 = play_game(is_f0d, true_values, info_p, move_p, info_b, move_b, True, rng0)
        rng1 = move_rng_for(i, 1)
        g2 = play_game(is_f0d, true_values, info_p, move_p, info_b, move_b, False, rng1)

        data["g1_bs_p"][i] = g1["score_a"]; data["g1_pc_p"][i] = g1["peek_count_a"]
        data["g1_bs_b"][i] = g1["score_b"]; data["g1_pc_b"][i] = g1["peek_count_b"]
        data["g2_bs_p"][i] = g2["score_a"]; data["g2_pc_p"][i] = g2["peek_count_a"]
        data["g2_bs_b"][i] = g2["score_b"]; data["g2_pc_b"][i] = g2["peek_count_b"]
        data["dud_p"][i] = (g1["dud_a"] + g2["dud_a"]) / 2.0
        data["dud_b"][i] = (g1["dud_b"] + g2["dud_b"]) / 2.0

    return data


def compute_win_array(data, price, n=None):
    n = n or len(data["g1_bs_p"])
    out = [0.0] * n
    for i in range(n):
        s_p1 = data["g1_bs_p"][i] - data["g1_pc_p"][i] * price
        s_b1 = data["g1_bs_b"][i] - data["g1_pc_b"][i] * price
        w1 = win_indicator(s_p1, s_b1)
        s_p2 = data["g2_bs_p"][i] - data["g2_pc_p"][i] * price
        s_b2 = data["g2_bs_b"][i] - data["g2_pc_b"][i] * price
        w2 = win_indicator(s_p2, s_b2)
        out[i] = (w1 + w2) / 2.0
    return out


# ---------------------------------------------------------------------------
# Statistics: Wilson score interval (single proportion) + paired bootstrap (diffs)
# ---------------------------------------------------------------------------

Z_95 = 1.959963984540054


def wilson_ci95(p_hat, n):
    """Wilson score interval for a single proportion. §D-5: used for single-proportion
    CIs (P vs B*'s win rate); paired differences continue to use process-2's bootstrap."""
    z = Z_95
    denom = 1 + z * z / n
    center = (p_hat + z * z / (2 * n)) / denom
    margin = (z / denom) * math.sqrt(p_hat * (1 - p_hat) / n + z * z / (4 * n * n))
    return center - margin, center + margin


def percentile(sorted_vals, p, n_boot):
    idx = max(0, min(n_boot - 1, int(p * n_boot)))
    return sorted_vals[idx]


def bootstrap_ci_paired_diff(arr_a, arr_b, seed, n_boot=B_BOOT):
    n = len(arr_a)
    rng = random.Random(seed)
    reps = []
    for _ in range(n_boot):
        idx = rng.choices(range(n), k=n)
        ma = sum(arr_a[i] for i in idx) / n
        mb = sum(arr_b[i] for i in idx) / n
        reps.append(ma - mb)
    reps.sort()
    return percentile(reps, 0.025, n_boot), percentile(reps, 0.975, n_boot)


def bootstrap_ci_single_percentile(arr, seed, n_boot=B_BOOT):
    """Single-array percentile bootstrap, matching process-2's original K-D1 method
    (used only for K-E0's apples-to-apples regression check against results_process2.json,
    which was computed this way; K-E1 itself uses Wilson per §D-5)."""
    n = len(arr)
    rng = random.Random(seed)
    reps = []
    for _ in range(n_boot):
        idx = rng.choices(range(n), k=n)
        reps.append(sum(arr[i] for i in idx) / n)
    reps.sort()
    return percentile(reps, 0.025, n_boot), percentile(reps, 0.975, n_boot)


def ci_overlap(lo1, hi1, lo2, hi2):
    return lo1 <= hi2 and lo2 <= hi1


# ---------------------------------------------------------------------------
# Main driver
# ---------------------------------------------------------------------------

def pick_worst_blind(p_name, matchup_data):
    """B* = blind variant minimizing P's point-estimate win rate at c=0.5 (tie-break:
    lowest roster index among BLIND_VARIANTS)."""
    best_b, best_val = None, None
    for b in BLIND_VARIANTS:
        arr = compute_win_array(matchup_data[(p_name, b)], 0.5)
        val = statistics.mean(arr)
        if best_val is None or val < best_val - 1e-12:
            best_b, best_val = b, val
        elif abs(val - best_val) <= 1e-12 and BLIND_VARIANTS.index(b) < BLIND_VARIANTS.index(best_b):
            best_b, best_val = b, val
    return best_b, best_val


def argmax_by_point(names, point_estimates, variance_estimates, roster_order):
    """§5 tie-break: point estimate -> paired-variance (smaller) -> lowest roster index."""
    best = None
    for name in names:
        if best is None:
            best = name
            continue
        pe, pb = point_estimates[name], point_estimates[best]
        if pe > pb + 1e-12:
            best = name
        elif abs(pe - pb) <= 1e-12:
            ve, vb = variance_estimates[name], variance_estimates[best]
            if ve < vb - 1e-12:
                best = name
            elif abs(ve - vb) <= 1e-12 and roster_order.index(name) < roster_order.index(best):
                best = name
    return best


def conservative_paired_ci(name_x, bx, name_y, by, matchup_data, seed_offset):
    """K-E4/K-E2 rule: if B*x != B*y, compute the diff both ways (each aligned to one of
    the two B*'s) and take the CI-lower that is smaller (more conservative)."""
    results = []
    b_candidates = {bx, by}
    for b_align in b_candidates:
        arr_x = compute_win_array(matchup_data[(name_x, b_align)], 0.5)
        arr_y = compute_win_array(matchup_data[(name_y, b_align)], 0.5)
        lo, hi = bootstrap_ci_paired_diff(arr_x, arr_y, seed=SEED_BASE + seed_offset + hash_stable(b_align))
        point = statistics.mean(arr_x) - statistics.mean(arr_y)
        results.append({"b_align": b_align, "lo": lo, "hi": hi, "point": point})
    chosen = min(results, key=lambda r: r["lo"])
    return chosen, results


def hash_stable(name):
    """Deterministic small integer from a strategy/blind name (avoids Python's
    hash-randomization pitfall for reproducible seeding)."""
    return sum((i + 1) * ord(c) for i, c in enumerate(name)) % 100000


def unfired_rate(data):
    total_peeks = sum(data["g1_pc_p"]) + sum(data["g2_pc_p"])
    total_duds = sum(data["dud_p"]) * 2  # dud_p already averaged over the 2 seat games; undo for a raw rate
    if total_peeks == 0:
        return None
    return total_duds / total_peeks


def main():
    t0 = time.time()
    print("=== simulating core matchups (6 tested x 4 blind = 24) ===", file=sys.stderr, flush=True)
    matchup_data = {}
    for p in TESTED_STRATS:
        for b in BLIND_VARIANTS:
            matchup_data[(p, b)] = simulate_matchup(p, b, is_f0d=False)
            print(f"  done {p} vs {b}", file=sys.stderr, flush=True)

    print("=== extra: S-prop/M-fix vs all 4 blinds (observation) ===", file=sys.stderr, flush=True)
    for b in BLIND_VARIANTS:
        matchup_data[("S-prop/M-fix", b)] = simulate_matchup("S-prop/M-fix", b, is_f0d=False)

    print("=== extra: S-blind/M-value vs S-blind/M-fix (K-E2 blind-side reference) ===", file=sys.stderr, flush=True)
    matchup_data[("S-blind/M-value", "S-blind/M-fix")] = simulate_matchup(
        "S-blind/M-value", "S-blind/M-fix", is_f0d=False)

    print(f"core matchups done in {time.time()-t0:.1f}s", file=sys.stderr, flush=True)

    # --- point estimates & B* selection ---
    b_star = {}
    point_at_own_bstar = {}
    for p in TESTED_STRATS + ["S-prop/M-fix"]:
        b, val = pick_worst_blind(p, matchup_data)
        b_star[p] = b
        point_at_own_bstar[p] = val

    variance_at_own_bstar = {}
    for p in TESTED_STRATS:
        arr = compute_win_array(matchup_data[(p, b_star[p])], 0.5)
        variance_at_own_bstar[p] = statistics.pvariance(arr) if len(arr) > 1 else 0.0

    # --- K-E1 (Wilson, single proportion vs own B*) ---
    t1 = time.time()
    print("=== K-E1 (Wilson CI95) ===", file=sys.stderr, flush=True)
    ke1_terms = {}
    for p in TESTED_STRATS:
        b = b_star[p]
        p_hat = point_at_own_bstar[p]
        lo, hi = wilson_ci95(p_hat, N)
        ke1_terms[p] = {"b_star": b, "point": p_hat, "ci_lower": lo, "ci_upper": hi}
    ke1_fires = all(t["ci_lower"] <= 0.5 for t in ke1_terms.values())

    # --- K-E4: X*(marginal-best) vs Y*(imm-best) ---
    print("=== K-E4 ===", file=sys.stderr, flush=True)
    x_star = argmax_by_point(MARG_STRATS, point_at_own_bstar, variance_at_own_bstar, TESTED_STRATS)
    y_star = argmax_by_point(IMM_STRATS, point_at_own_bstar, variance_at_own_bstar, TESTED_STRATS)
    ke4_chosen, ke4_alts = conservative_paired_ci(
        x_star, b_star[x_star], y_star, b_star[y_star], matchup_data, seed_offset=900001)
    ke4_fires = ke4_chosen["lo"] <= 0

    # --- K-E2: Z*m (seek/value, 4) vs Z*f (fix, 2) ---
    print("=== K-E2 ===", file=sys.stderr, flush=True)
    z_star_m = argmax_by_point(K_E2_MOVE, point_at_own_bstar, variance_at_own_bstar, TESTED_STRATS)
    z_star_f = argmax_by_point(K_E2_FIX, point_at_own_bstar, variance_at_own_bstar, TESTED_STRATS)
    ke2_chosen, ke2_alts = conservative_paired_ci(
        z_star_m, b_star[z_star_m], z_star_f, b_star[z_star_f], matchup_data, seed_offset=900002)
    ke2_fires = ke2_chosen["lo"] <= 0

    # blind-side movement value reference (not judged)
    blind_move_arr = compute_win_array(matchup_data[("S-blind/M-value", "S-blind/M-fix")], 0.5)
    blind_move_value_point = statistics.mean(blind_move_arr) - 0.5

    print(f"K-E1/E4/E2 done in {time.time()-t1:.1f}s", file=sys.stderr, flush=True)

    # --- K-E0: regression check against results_process2.json ---
    t2 = time.time()
    print("=== K-E0 (regression vs process-2 + F0-D recheck) ===", file=sys.stderr, flush=True)
    with open("sim/schrodinger_probe/results_process2.json", "r", encoding="utf-8") as f:
        p2 = json.load(f)

    f0d_data = {}
    for p in ("S-imm/M-fix", "S-marg/M-fix", "S-prop/M-fix"):
        f0d_data[p] = simulate_matchup(p, "S-blind/M-fix", is_f0d=True)

    ke0_kd1_terms = {}
    p2_key_map = {"S-imm/M-fix": "S-peek-imm", "S-marg/M-fix": "S-peek-marginal", "S-prop/M-fix": "S-peek-prop"}
    kd1_point_A = {}
    for p, p2key in p2_key_map.items():
        arr = compute_win_array(matchup_data[(p, "S-blind/M-fix")], 0.5)
        lo, hi = bootstrap_ci_single_percentile(arr, seed=SEED_BASE + 800000 + hash_stable(p))
        p2_ci = p2["ci95"]["condition_A"]["0.5"][p2key]
        overlap = ci_overlap(lo, hi, p2_ci["lower"], p2_ci["upper"])
        kd1_point_A[p] = statistics.mean(arr)
        ke0_kd1_terms[p] = {
            "process3_ci": [lo, hi], "process2_ci": [p2_ci["lower"], p2_ci["upper"]], "overlap": overlap
        }

    # process-2's original X* selection rule: argmax among {imm,marg,prop} by point estimate @ c=0.5 condition A
    p2_x_star = max(p2_key_map.keys(), key=lambda p: kd1_point_A[p])
    arr_a_p2xstar = compute_win_array(matchup_data[(p2_x_star, "S-blind/M-fix")], 0.5)
    arr_c_p2xstar = compute_win_array(f0d_data[p2_x_star], 0.5)
    exc_a = statistics.mean(arr_a_p2xstar) - 0.5
    exc_c = statistics.mean(arr_c_p2xstar) - 0.5
    lo_kd4, hi_kd4 = bootstrap_ci_paired_diff(
        [w - 0.5 for w in arr_a_p2xstar], [w - 0.5 for w in arr_c_p2xstar], seed=SEED_BASE + 800100)
    p2_kd4_ci = p2["ci95"]["K-D4_pair"]
    kd4_overlap = ci_overlap(lo_kd4, hi_kd4, p2_kd4_ci["lower"], p2_kd4_ci["upper"])

    f0d_prop_arr = compute_win_array(f0d_data["S-prop/M-fix"], 0.5)
    f0d_prop_point = statistics.mean(f0d_prop_arr)
    ke0_f0d_prop_ok = abs(f0d_prop_point - 0.0) < 1e-9

    ke0_fires = (not all(t["overlap"] for t in ke0_kd1_terms.values())) or (not kd4_overlap) or (not ke0_f0d_prop_ok)

    print(f"K-E0 done in {time.time()-t2:.1f}s", file=sys.stderr, flush=True)

    overall_pass = (not ke0_fires) and (not ke1_fires) and (not ke4_fires)

    # --- reference records ---
    unfired = {p: unfired_rate(matchup_data[(p, b_star[p])]) for p in TESTED_STRATS + ["S-prop/M-fix"]}
    price_grid = {}
    for pr in (0, 0.25, 1.0):
        price_grid[str(pr)] = {p: statistics.mean(compute_win_array(matchup_data[(p, b_star[p])], pr))
                                for p in TESTED_STRATS}

    output = {
        "meta": {"seed_base": SEED_BASE, "n": N, "n_boot": B_BOOT, "runtime_sec": time.time() - t0},
        "b_star": b_star,
        "point_estimates_at_own_bstar_c0.5": point_at_own_bstar,
        "kill_conditions": {
            "K-E0_instrument_check": {
                "fires": ke0_fires,
                "kd1_equivalent_terms": ke0_kd1_terms,
                "kd4_equivalent": {
                    "x_star_process2_rule": p2_x_star, "exc_A": exc_a, "exc_C": exc_c,
                    "process3_ci": [lo_kd4, hi_kd4], "process2_ci": [p2_kd4_ci["lower"], p2_kd4_ci["upper"]],
                    "overlap": kd4_overlap,
                },
                "f0d_prop_zero_check": {"point": f0d_prop_point, "ok": ke0_f0d_prop_ok},
            },
            "K-E1_market_death_by_unfired_tax": {"fires": ke1_fires, "terms": ke1_terms},
            "K-E4_deduction_contribution_death": {
                "fires": ke4_fires, "x_star": x_star, "y_star": y_star,
                "b_star_x": b_star[x_star], "b_star_y": b_star[y_star],
                "chosen": ke4_chosen, "alternatives": ke4_alts,
                "process2_baseline_0.0334": 0.0334,
            },
            "K-E2_movement_is_decorative": {
                "fires": ke2_fires, "z_star_m": z_star_m, "z_star_f": z_star_f,
                "b_star_m": b_star[z_star_m], "b_star_f": b_star[z_star_f],
                "chosen": ke2_chosen, "alternatives": ke2_alts,
                "blind_side_movement_value_reference": blind_move_value_point,
            },
        },
        "overall_pass": overall_pass,
        "reference_not_for_judgment": {
            "unfired_rate_by_strategy_c0.5": unfired,
            "price_grid_c0_025_1.0": price_grid,
            "structural_findings": {
                "M_seek_equals_M_value_equals_M_fix_for_imm_and_marginal": True,
                "note": "imm/marginal never bank cross-round private knowledge (each peek is "
                        "immediately consumed the same round), so their movement choice when "
                        "controlling always reduces to lowest-remaining-index -- identical to "
                        "M-fix. Verified byte-identical per-game in simulator self-check.",
            },
        },
    }

    with open("sim/schrodinger_probe/results_process3.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(json.dumps({k: v for k, v in output.items() if k != "reference_not_for_judgment"},
                      ensure_ascii=False, indent=2))
    print(f"TOTAL runtime: {time.time()-t0:.1f}s", file=sys.stderr, flush=True)
    return output, matchup_data, f0d_data


if __name__ == "__main__":
    main()
