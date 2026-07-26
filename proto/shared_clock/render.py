# -*- coding: utf-8 -*-
"""共有時計 10問テスト ― 工程4: 出力2本(出題側/封印側)の生成"""

from scenarios import build_scenarios, WORD_JP, BAND_JP
from verify import death_formula, unique_formula, check_A, check_B, check_C

RULE_TABLE = """\
| 項目 | 値 |
|---|---|
| 人数 | 4 |
| ラウンド数 T | 10（10ラウンド全員生存で勝利） |
| 共有時計 N | 11個（ゲーム全体の総量。ラウンド配分なし。残量は全員に公開） |
| 各自の盤 | 侵蝕値 0〜16。16到達で即・全員敗北 |
| 初期侵蝕 i0 | 0〜3 のランダム。本人にも非公開 |
| 成長率 g | {1, 2, 3} からランダム。盤ごとに固定。本人にも非公開 |
| 成長 | 毎ラウンド終了時、侵蝕 +g（決定論・ノイズなし） |
| 本人の視界 | 侵蝕値そのものは見えない。帯のみ：緑 0-5 ／ 黄 6-10 ／ 橙 11-15 |
| 他人の視界 | 一切見えない。送信された語だけ |
| 行動 | 各ラウンド、各自が使えるのは最大1個。全員同時に伏せて選び、同時公開 |
| ― 浄化 | 時計1個。自分の侵蝕 −4（0未満にならない） |
| ― 送信 | 時計1個。語彙から1語を全員に公開 |
| ― 温存 | 時計を使わない。その1個は仲間の誰かの1個になる |
| 語彙 V | 4語：【緑】【黄】【橙】【落ちる】 |

**行動の解決順（明記）:** 各ラウンドは「全員同時に行動 → 浄化を適用 → 成長 +g を適用 → 帯を再表示」。
"""


def band_history_prose(band_history):
    """
    帯の履歴を「第1R終了時=緑 → 第4R終了時=黄」形式の遷移列＋現在帯の連続数に変換する。

    【表記の一意化】band_history[k-1] は「第kR終了時（浄化・成長を適用し帯を再表示した後）」
    の帯である。開始時（第1Rの行動を選ぶ前）は i0∈{0,1,2,3} により必ず緑になるが、
    これは自明なので履歴には含めない。「第kR」を「第kRの行動を選ぶ前に見える帯」と
    誤読しないよう、必ず「終了時」を明記する。
    """
    transitions = []
    prev = None
    for idx, b in enumerate(band_history, start=1):
        if b != prev:
            transitions.append((idx, b))
            prev = b
    parts = [f"第{rnd}R終了時={BAND_JP[b]}" for rnd, b in transitions]
    current_band = band_history[-1]
    streak = 0
    for b in reversed(band_history):
        if b == current_band:
            streak += 1
        else:
            break
    entry_round = len(band_history) - streak + 1
    if entry_round == len(band_history):
        current_desc = f"現在={BAND_JP[current_band]}（今ラウンドで{BAND_JP[current_band]}に入った）"
    else:
        current_desc = f"現在={BAND_JP[current_band]}（{BAND_JP[current_band]}に入って{streak}ラウンド目）"
    return " → ".join(parts) + " → " + current_desc


def purify_prose(s):
    if s.purify_count == 0:
        return "0回（まだ一度も浄化していない）"
    rounds = "、".join(f"第{r}R" for r in sorted(s.purify_rounds))
    return f"{s.purify_count}回（{rounds}）"


def received_prose(s):
    if not s.received:
        return "なし（これまで誰からも語は届いていない）"
    lines = []
    for rnd, w in sorted(s.received):
        lines.append(f"第{rnd}Rに誰かから【{WORD_JP[w]}】")
        lines.append("／")
    lines = lines[:-1]
    return "".join(lines) + "\n（誰が送ったかは分からない。送信は匿名である）"


def render_question_body(s):
    lines = []
    lines.append(f"局面 {s.sid}")
    lines.append(f"第 {s.r} ラウンドの選択。")
    lines.append(f"共有時計の残り：{s.remaining_clock}個。")
    lines.append(f"あなたの帯の履歴：{band_history_prose(s.band_history)}")
    lines.append(f"あなたが浄化した回数：{purify_prose(s)}")
    lines.append(f"これまでに届いた語：{received_prose(s)}")
    lines.append("あなたはこのラウンド、どうしますか。")
    lines.append(" (1) 浄化する　(2) 送信する（送る語も書く）　(3) 温存する")
    return "\n".join(lines)


def render_public_md(scenarios):
    out = []
    out.append("# 出題：共有時計 10問テスト v1")
    out.append("")
    out.append("設計案『共有時計』の中心決断が生きているかを判定するための10問である。")
    out.append("**ゲームは実装されていない。数値や語は全て凍結ルール表どおりに解釈すること。**")
    out.append("")
    out.append("## 凍結ルール表")
    out.append("")
    out.append(RULE_TABLE)
    out.append("")
    out.append("## 回答方法")
    out.append("")
    out.append("各局面について、次の**三値**のいずれか一つで答えること。")
    out.append("")
    out.append("- **引き裂かれた** ―― 複数の選択肢の間で本気で迷った")
    out.append("- **同値で決められない** ―― どれを選んでも同じに感じ、迷う理由がない")
    out.append("- **即決** ―― 一つの答えがすぐに決まった")
    out.append("")
    out.append("併せて、実際に選ぶ行動（浄化／送信＋送る語／温存）も書くこと。")
    out.append("")
    out.append("---")
    out.append("")
    for s in scenarios:
        out.append("```")
        out.append(render_question_body(s))
        out.append("```")
        out.append("")
        out.append("回答： 　　　　　　　　　　　　　　　（引き裂かれた／同値で決められない／即決）")
        out.append("")
        out.append("選んだ行動： 　　　　　　　　　　　　　　　")
        out.append("")
        out.append("---")
        out.append("")
    return "\n".join(out)


def render_sealed_md(scenarios, v_a, v_b, v_c):
    out = []
    out.append("# 封印：共有時計 10問テスト v1 ― 対照位置と照合ログ")
    out.append("")
    out.append("**判定前に開かないこと。** 設計者（マッチさん）が10問への回答・三値・")
    out.append("選んだ行動を確定させる前にこのファイルを読むと、器具が壊れる。")
    out.append("")
    out.append("---")
    out.append("")
    out.append("## 対照の位置と種別")
    out.append("")
    contrast_rows = [s for s in scenarios if s.is_contrast]
    out.append("| 局面 | 種別 | 判定式 |")
    out.append("|---|---|---|")
    for s in contrast_rows:
        if s.contrast_type == "death":
            formula = "死亡対照（無差別）：全候補が第10R終了時まで16未満／自帯=直近自送信語／残時計≥残ラウンド数×2"
        else:
            formula = "一意解対照：浄化なしで次R終了時に16以上へ達する候補が存在／残時計≥1"
        out.append(f"| 局面{s.sid} | {s.contrast_type} | {formula} |")
    out.append("")
    out.append(f"意図した対照は3問（死亡対照2・一意解対照1）。それ以外の7問は通常局面。")
    out.append("")
    out.append("## 工程3 機械照合ログ")
    out.append("")
    out.append("### (A) 対照判定式の全数照合")
    out.append("")
    death_hits = [s.sid for s in scenarios if death_formula(s)]
    unique_hits = [s.sid for s in scenarios if unique_formula(s)]
    out.append(f"- 死亡対照式に該当した局面: {death_hits}")
    out.append(f"- 一意解対照式に該当した局面: {unique_hits}")
    out.append(f"- 違反件数: {len(v_a)}")
    out.append("")
    out.append("### (B) 同型・1要素違い検出")
    out.append("")
    out.append(f"- 違反件数: {len(v_b)}")
    out.append("")
    out.append("### (C) 分布チェック")
    out.append("")
    out.append(f"- 違反件数: {len(v_c)}")
    out.append("")
    out.append("---")
    out.append("")
    out.append("## 各局面の候補集合（工程1出力）")
    out.append("")
    for s in scenarios:
        out.append(f"### 局面{s.sid}（第{s.r}R決定・対照={s.contrast_type or 'なし'}）")
        out.append("")
        cand_str = ", ".join(f"(i0={c['i0']},g={c['g']},E_cur={c['E_cur']})" for c in s.candidates)
        out.append(f"- 候補集合: {cand_str}")
        out.append(f"- 現在侵蝕値の範囲: {s.e_range}")
        out.append(f"- 浄化なしで第10R終了時までに16以上へ到達する候補が存在するか: {s.reach16_by_r10}")
        out.append(f"- 浄化なしで次Rに16以上へ到達する候補が存在するか: {s.reach16_next}")
        out.append(f"- 死亡対照式の結果: {death_formula(s)} ／ 一意解対照式の結果: {unique_formula(s)}")
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    scenarios = build_scenarios()
    v_a = check_A(scenarios)
    v_b = check_B(scenarios)
    v_c = check_C(scenarios)

    public_md = render_public_md(scenarios)
    sealed_md = render_sealed_md(scenarios, v_a, v_b, v_c)

    with open("出題_共有時計_10問_v1.md", "w", encoding="utf-8") as f:
        f.write(public_md)
    with open("封印_共有時計_対照位置_v1.md", "w", encoding="utf-8") as f:
        f.write(sealed_md)

    print("\n--- 出力完了 ---")
