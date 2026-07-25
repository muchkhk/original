# -*- coding: utf-8 -*-
"""
共有時計 10問テスト ― 工程3: 機械照合

目視で判断しない。全て判定式・比較コードで結論を出す。

(A) 対照判定式を全10問に適用し、意図した3問だけが該当するか
(B) 全10問の状態ベクトルの同型・1要素違いペアを検出する
(C) 分布チェック（経過ラウンド分布・帯分布・黄帯上限・受信語0/3以上の両立）
"""

from scenarios import build_scenarios


def death_formula(s):
    """死亡対照（無差別）判定式：(a) and (b) and (c)"""
    a = (s.reach16_by_r10 is False)
    last_word = s.last_self_transmission_word()
    b = (last_word is not None and last_word == s.current_band)
    threshold = s.remaining_rounds_incl_r() * 2
    c = (s.remaining_clock >= threshold)
    return a and b and c


def unique_formula(s):
    """一意解対照判定式：reach16_next かつ 残時計>=1"""
    return bool(s.reach16_next) and (s.remaining_clock >= 1)


def check_A(scenarios):
    print("=== 工程3(A) 対照判定式の全数照合 ===")
    intended = {s.sid: s.contrast_type for s in scenarios if s.is_contrast}
    violations = []
    death_hits = []
    unique_hits = []
    for s in scenarios:
        d = death_formula(s)
        u = unique_formula(s)
        if d:
            death_hits.append(s.sid)
        if u:
            unique_hits.append(s.sid)
        expected_type = intended.get(s.sid)
        if expected_type == "death" and not d:
            violations.append(f"局面{s.sid}: 死亡対照を意図したが判定式に非該当")
        if expected_type == "unique" and not u:
            violations.append(f"局面{s.sid}: 一意解対照を意図したが判定式に非該当")
        if expected_type is None and (d or u):
            violations.append(f"局面{s.sid}: 対照を意図していないが判定式に該当(death={d}, unique={u})")
        if expected_type == "death" and u:
            violations.append(f"局面{s.sid}: 死亡対照が一意解対照にも該当（二重該当）")
        if expected_type == "unique" and d:
            violations.append(f"局面{s.sid}: 一意解対照が死亡対照にも該当（二重該当）")

    print(f"死亡対照式に該当した局面: {death_hits} (意図: {[sid for sid,t in intended.items() if t=='death']})")
    print(f"一意解対照式に該当した局面: {unique_hits} (意図: {[sid for sid,t in intended.items() if t=='unique']})")
    print(f"違反件数: {len(violations)}")
    for v in violations:
        print(" -", v)
    return violations


def check_B(scenarios):
    print("\n=== 工程3(B) 同型・1要素違い検出 ===")
    violations = []
    vectors = {s.sid: s.state_vector() for s in scenarios}
    ids = list(vectors.keys())
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            va, vb = vectors[a], vectors[b]
            if va == vb:
                violations.append(f"局面{a}と局面{b}が完全一致")
                continue
            diff = sum(1 for x, y in zip(va, vb) if x != y)
            if diff == 1:
                violations.append(f"局面{a}と局面{b}が1要素違い（{diff}要素差）")
    print(f"違反件数: {len(violations)}")
    for v in violations:
        print(" -", v)
    return violations


def check_C(scenarios):
    print("\n=== 工程3(C) 分布チェック ===")
    violations = []

    def bucket(r):
        if 1 <= r <= 3:
            return "1-3"
        if 4 <= r <= 6:
            return "4-6"
        return "7-10"

    from collections import Counter
    round_counts = Counter(bucket(s.r) for s in scenarios)
    expected_round = {"1-3": 2, "4-6": 3, "7-10": 5}
    print(f"経過ラウンド分布: {dict(round_counts)} (期待: {expected_round})")
    if dict(round_counts) != expected_round:
        violations.append(f"経過ラウンド分布が期待と不一致: {dict(round_counts)}")

    band_counts = Counter(s.current_band for s in scenarios)
    print(f"現在帯分布: {dict(band_counts)}")
    if band_counts.get("Y", 0) > 4:
        violations.append(f"黄帯の局面が上限4を超過: {band_counts.get('Y', 0)}")

    received_counts = [len(s.received) for s in scenarios]
    print(f"受信語数の一覧: {received_counts}")
    if 0 not in received_counts:
        violations.append("受信0語の局面が存在しない")
    if not any(c >= 3 for c in received_counts):
        violations.append("受信3語以上の局面が存在しない")

    print(f"残時計の一覧: {[s.remaining_clock for s in scenarios]}")
    print(f"違反件数: {len(violations)}")
    for v in violations:
        print(" -", v)
    return violations


def main():
    scenarios = build_scenarios()
    v_a = check_A(scenarios)
    v_b = check_B(scenarios)
    v_c = check_C(scenarios)
    total = len(v_a) + len(v_b) + len(v_c)
    print(f"\n=== 総違反件数: {total} ===")
    return scenarios, v_a, v_b, v_c


if __name__ == "__main__":
    main()
