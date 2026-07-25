"""
共有時計 10問テスト ― 工程1: 状態候補の列挙器

これはシミュレータではない。凍結ルール表にある決定論的な成長式
（毎ラウンド終了時、侵蝕 +g。浄化は時計1個で侵蝕-4、0未満にならない）を
単純に再生し、観測された帯の履歴と一致する (i0, g) の組だけを残す。

方策比較・勝率測定・NPCは一切行わない（STOP_PREHUMAN_SIMULATION 非抵触）。
"""

I0_RANGE = (0, 1, 2, 3)
G_RANGE = (1, 2, 3)
DEATH_THRESHOLD = 16


def band(e):
    """侵蝕値 e から帯を返す。16以上は 'DEAD'（ゲーム上は即敗北で帯表示自体が発生しない）。"""
    if e < 0:
        raise ValueError("erosion must be >= 0")
    if e <= 5:
        return "G"
    if e <= 10:
        return "Y"
    if e <= 15:
        return "O"
    return "DEAD"


def simulate_round(e, g, purified):
    """1ラウンド分の解決順を適用する：浄化 → 成長+g。新しい侵蝕値を返す。"""
    if purified:
        e = max(0, e - 4)
    e = e + g
    return e


def enumerate_candidates(r, purify_rounds, band_history):
    """
    工程1本体。

    入力:
      r            : 現在決定しようとしているラウンド番号（1〜10）
      purify_rounds: これまでに自分が浄化した実施ラウンドの集合（1..r-1 の部分集合）
      band_history : 第1R〜第(r-1)R の「ラウンド解決後」の帯の列（長さ r-1）

    出力: dict
      candidates      : 生存候補のリスト [{"i0":int, "g":int, "E_cur":int}, ...]
      e_range         : (min, max) 現在侵蝕値の取りうる範囲（候補が空なら None）
      reach16_by_r10  : 今後1個も浄化しなかった場合、第10ラウンド終了時までに
                         16以上へ到達する候補が存在するか
      reach16_next    : 今後1個も浄化しなかった場合、次のラウンド終了時に
                         16以上へ到達する候補が存在するか
    """
    if len(band_history) != r - 1:
        raise ValueError(
            f"band_history の長さは r-1={r-1} でなければならない（実際={len(band_history)}）"
        )

    purify_rounds = set(purify_rounds)
    for k in purify_rounds:
        if not (1 <= k <= r - 1):
            raise ValueError(f"purify_rounds の要素は 1..{r-1} の範囲でなければならない: {k}")

    candidates = []
    for i0 in I0_RANGE:
        for g in G_RANGE:
            e = i0
            ok = True
            for k in range(1, r):  # ラウンド 1 .. r-1 を再生する
                e = simulate_round(e, g, purified=(k in purify_rounds))
                if e >= DEATH_THRESHOLD:
                    ok = False
                    break
                observed = band_history[k - 1]
                if band(e) != observed:
                    ok = False
                    break
            if ok:
                candidates.append({"i0": i0, "g": g, "E_cur": e})

    if not candidates:
        return {
            "candidates": [],
            "e_range": None,
            "reach16_by_r10": None,
            "reach16_next": None,
        }

    e_range = (min(c["E_cur"] for c in candidates), max(c["E_cur"] for c in candidates))

    remaining_rounds = 10 - r + 1  # r ラウンド目から第10ラウンドまでの数（r含む）
    reach16_by_r10 = any(
        c["E_cur"] + c["g"] * remaining_rounds >= DEATH_THRESHOLD for c in candidates
    )
    reach16_next = any(c["E_cur"] + c["g"] >= DEATH_THRESHOLD for c in candidates)

    return {
        "candidates": candidates,
        "e_range": e_range,
        "reach16_by_r10": reach16_by_r10,
        "reach16_next": reach16_next,
    }


def current_band_of_history(r, band_history):
    """現在（第rR決定時点）の帯。r=1 なら i0∈{0,1,2,3} は常に緑なので 'G' 固定。"""
    if r == 1:
        return "G"
    return band_history[-1]


def _self_test():
    """
    完了条件テストケース：i0=2, g=2, 浄化なし の帯遷移。
    第1R=3(G) 第2R=6(Y) 第3R=8(Y) 第4R=10(Y) → 帯履歴 [G,Y,Y,Y]
    この帯履歴（浄化なし）に一致する (i0,g) は 12通り中 (2,2) のみのはず。
    """
    band_history = ["G", "Y", "Y", "Y"]
    result = enumerate_candidates(r=5, purify_rounds=set(), band_history=band_history)
    cands = result["candidates"]
    assert len(cands) == 1, f"候補は1件のはずが {len(cands)} 件: {cands}"
    only = cands[0]
    assert only["i0"] == 2 and only["g"] == 2, f"候補が (2,2) ではない: {only}"
    assert only["E_cur"] == 10, f"E_cur は10のはずが {only['E_cur']}"
    assert result["e_range"] == (10, 10)
    print("自己診断テスト PASS：帯履歴", band_history, "→ 候補", cands)


if __name__ == "__main__":
    _self_test()
