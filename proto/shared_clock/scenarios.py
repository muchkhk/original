# -*- coding: utf-8 -*-
"""
共有時計 10問テスト ― 工程2: 局面10問の定義（生成パラメータ方式）

各局面は「生成例」(i0, g, purify_rounds) から実際に帯履歴を再生して作る。
これにより「帯の履歴が物理的にありえない局面」を機械的に排除できる
（生成例自体が enumerate_candidates で常に候補集合に残るため、非空が保証される）。

is_contrast / contrast_type は工程3・工程4でのみ参照する内部メタデータであり、
出題側 Markdown には一切出力しない（封印側だけに出す）。
"""

from enumerate import enumerate_candidates, current_band_of_history, simulate_round, band

WORDS = ("G", "Y", "O", "FALL")
WORD_JP = {"G": "緑", "Y": "黄", "O": "橙", "FALL": "落ちる"}
BAND_JP = {"G": "緑", "Y": "黄", "O": "橙"}


def build_band_history(i0, g, purify_rounds, upto_round):
    """生成例から第1R..第(upto_round)Rの帯履歴を再生する。死亡したら例外。"""
    e = i0
    history = []
    for k in range(1, upto_round + 1):
        e = simulate_round(e, g, purified=(k in purify_rounds))
        if e >= 16:
            raise ValueError(f"生成例 (i0={i0},g={g},purify={purify_rounds}) は第{k}Rで死亡した")
        history.append(band(e))
    return history, e


class Scenario:
    def __init__(self, sid, r, gen_i0, gen_g, purify_rounds, remaining_clock,
                 self_transmissions, received, is_contrast=False, contrast_type=None,
                 note=""):
        self.sid = sid
        self.r = r
        self.gen_i0 = gen_i0
        self.gen_g = gen_g
        self.purify_rounds = set(purify_rounds)
        self.remaining_clock = remaining_clock
        self.self_transmissions = list(self_transmissions)  # [(round, word)]
        self.received = list(received)  # [(round, word)]
        self.is_contrast = is_contrast
        self.contrast_type = contrast_type
        self.note = note

        self.band_history, self.gen_E_after = build_band_history(
            gen_i0, gen_g, self.purify_rounds, r - 1
        )
        self.current_band = current_band_of_history(r, self.band_history)
        self.purify_count = len(self.purify_rounds)

        result = enumerate_candidates(r, self.purify_rounds, self.band_history)
        self.candidates = result["candidates"]
        self.e_range = result["e_range"]
        self.reach16_by_r10 = result["reach16_by_r10"]
        self.reach16_next = result["reach16_next"]

        if not self.candidates:
            raise ValueError(f"局面{sid}: 候補集合が空")

    def remaining_rounds_incl_r(self):
        return 11 - self.r

    def last_self_transmission_word(self):
        if not self.self_transmissions:
            return None
        return sorted(self.self_transmissions, key=lambda x: x[0])[-1][1]

    def state_vector(self):
        """工程3(B) 同型検出用の正規化状態ベクトル。"""
        band_transitions = tuple(self.band_history)
        received_seq = tuple(sorted(self.received))
        return (
            self.r,
            self.remaining_clock,
            self.current_band,
            band_transitions,
            self.purify_count,
            received_seq,
        )


def build_scenarios():
    scenarios = []

    # --- S1: r=2, 帯1-3グループ, 受信0語 ---
    scenarios.append(Scenario(
        sid=1, r=2,
        gen_i0=1, gen_g=2, purify_rounds=[],
        remaining_clock=10,
        self_transmissions=[],
        received=[],
        note="ゲーム開始直後。誰も何も送っていない。",
    ))

    # --- S2: r=3, 帯1-3グループ, 黄帯, 受信1語 ---
    scenarios.append(Scenario(
        sid=2, r=3,
        gen_i0=3, gen_g=3, purify_rounds=[],
        remaining_clock=9,
        self_transmissions=[],
        received=[(2, "Y")],
        note="早い段階で黄に入った初期値高めの盤。",
    ))

    # --- S3: r=4, 帯4-6グループ, 黄帯, 浄化1回, 受信2語 ---
    scenarios.append(Scenario(
        sid=3, r=4,
        gen_i0=2, gen_g=3, purify_rounds=[2],
        remaining_clock=7,
        self_transmissions=[],
        received=[(1, "G"), (3, "FALL")],
        note="一度浄化したのに黄へ戻った。",
    ))

    # --- S4: r=5, 帯4-6グループ, 通常 ---
    scenarios.append(Scenario(
        sid=4, r=5,
        gen_i0=0, gen_g=2, purify_rounds=[3],
        remaining_clock=5,
        self_transmissions=[(2, "G")],
        received=[(1, "G"), (4, "Y")],
        note="残時計が中程度まで減ってきた頃。",
    ))

    # --- S5: r=6, 帯4-6グループ, 死亡対照(無差別) #1 ---
    scenarios.append(Scenario(
        sid=5, r=6,
        gen_i0=0, gen_g=1, purify_rounds=[],
        remaining_clock=10,
        self_transmissions=[(3, "G")],
        received=[(4, "G")],
        is_contrast=True, contrast_type="death",
        note="【死亡対照候補】全候補が第10R終了時まで16未満で安全、"
             "自分の帯は直近の自送信語と同じ、残時計は極めて潤沢。",
    ))

    # --- S6: r=7, 帯7-10グループ, 通常 ---
    scenarios.append(Scenario(
        sid=6, r=7,
        gen_i0=1, gen_g=2, purify_rounds=[3],
        remaining_clock=4,
        self_transmissions=[(5, "Y")],
        received=[(2, "Y"), (5, "O")],
        note="中盤で浄化して黄を維持している盤。",
    ))

    # --- S7: r=8, 帯7-10グループ, 橙帯, 受信3語以上 ---
    scenarios.append(Scenario(
        sid=7, r=8,
        gen_i0=2, gen_g=2, purify_rounds=[4],
        remaining_clock=3,
        self_transmissions=[],
        received=[(2, "Y"), (4, "O"), (6, "O"), (7, "FALL")],
        note="橙が続き、他人からの語が4件届いている。",
    ))

    # --- S8: r=9, 帯7-10グループ, 一意解対照 ---
    scenarios.append(Scenario(
        sid=8, r=9,
        gen_i0=1, gen_g=3, purify_rounds=[3, 5, 7],
        remaining_clock=2,
        self_transmissions=[(6, "O")],
        received=[(3, "Y"), (6, "O")],
        is_contrast=True, contrast_type="unique",
        note="【一意解対照候補】浄化なしで次R終了時に16以上へ達する候補が存在し、"
             "残時計はまだ1以上ある。",
    ))

    # --- S9: r=10, 帯7-10グループ, 死亡対照(無差別) #2 ---
    scenarios.append(Scenario(
        sid=9, r=10,
        gen_i0=0, gen_g=1, purify_rounds=[],
        remaining_clock=3,
        self_transmissions=[(8, "Y")],
        received=[(5, "Y")],
        is_contrast=True, contrast_type="death",
        note="【死亡対照候補】最終R。安全マージン十分、自送信と同帯、"
             "残時計は残り1ラウンド分の必要量(2)以上。",
    ))

    # --- S10: r=10, 帯7-10グループ, 通常（最終ラウンドの緊張） ---
    scenarios.append(Scenario(
        sid=10, r=10,
        gen_i0=3, gen_g=2, purify_rounds=[2, 6],
        remaining_clock=1,
        self_transmissions=[(9, "O")],
        received=[(3, "Y"), (6, "O"), (9, "FALL")],
        note="最終ラウンド、残時計はほぼ枯渇。",
    ))

    return scenarios


if __name__ == "__main__":
    for s in build_scenarios():
        print(f"S{s.sid}: r={s.r} band_hist={s.band_history} cur={s.current_band} "
              f"purify={sorted(s.purify_rounds)} cand={len(s.candidates)} "
              f"e_range={s.e_range} reach16_next={s.reach16_next} reach16_by10={s.reach16_by_r10} "
              f"remaining={s.remaining_clock} contrast={s.is_contrast}/{s.contrast_type}")
