#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
報告書プレースホルダ検出リント（指示書12 作業0-2）

CLAUDE.md §9-j「報告書はPR番号・ハッシュを空欄で納品しない」の機械的な検査。
指示書05・06・07・報告11で計4回、ハッシュ欄が「作成後に追記」「（マージ後に追記）」等の
プレースホルダのまま納品される同型違反が起きた（handoff_設計チャット21 §2、指示書12 §0）。
納品前に必ず本スクリプトを通し、プレースホルダが残っていないことを確認する。

検出パターン（指示書12が名指ししたもの。目標合わせでパターンを増減させない）：
  1. 「ラベル：（後に追記）」形の空欄（例：マージコミット：（マージ後に追記）／
     PR番号：（作成後に追記））。コロン＋括弧という「ラベルの値」の形に絞ることで、
     過去の違反事例を地の文で説明する文章（例：「報告08の『（マージ後に追記予定）』を
     回収した」）を誤検知しない
  2. 「TBD」（大文字小文字を区別しない）
  3. 「後で」の直後に空欄補充を示す語が続くもの（例：後で追記／後で記載／後で埋める）
  4. 2文字以上連続するアンダースコア「__」（下線プレースホルダ）

誤検知対策：
  - コードブロック（```）・インラインコード（`...`）は全パターンの対象外とする
    （`window.__calibTest`等の正当な識別子を誤検知しないため）
  - 全角鉤括弧「」で囲まれた引用（＝過去の違反事例を逐語引用した地の文）は対象外とする
    （この2点は本リポジトリの報告書の実際の書き方――コード識別子はバッククォート、
     過去の逐語引用は鉤括弧――を機械的に踏まえたもので、パターンの追加ではない）

対象：`proto/報告_*.md`（報告書。他のMarkdownは対象外＝納品物のみを検査する）。

使い方:
  python tools/check_report_placeholders.py            # 通常実行（全報告書を検査）
  python tools/check_report_placeholders.py --selftest  # 故意注入によるFAIL実証つき自己診断
"""
import argparse
import glob
import os
import re
import sys
import tempfile

PLACEHOLDER_PATTERNS = [
    ("ラベル：（…後に追記）", re.compile(r"[:：]\s*[\(（][^）)]*後に追記")),
    ("TBD", re.compile(r"TBD", re.IGNORECASE)),
    ("後で+空欄補充語", re.compile(r"後で(追記|記載|書く|埋める|入力|対応)")),
    ("連続アンダースコア(__)", re.compile(r"_{2,}")),
]

DEFAULT_GLOB = "proto/報告_*.md"

INLINE_CODE_RE = re.compile(r"`[^`]*`")
QUOTED_CITATION_RE = re.compile(r"「[^」]*」")


def _strip_code_spans(line):
    """インラインコード（`...`）を空文字へ置換する（位置はそのまま保つため、
    元の文字数分だけ空白で埋める＝行番号・部分文字列表示は変わらない）。"""
    return INLINE_CODE_RE.sub(lambda m: " " * len(m.group(0)), line)


def _is_inside_quoted_citation(line, start, end):
    """マッチ位置が全角鉤括弧「」で囲まれた引用の内側にあるかを判定する。"""
    for m in QUOTED_CITATION_RE.finditer(line):
        if m.start() <= start and end <= m.end():
            return True
    return False


def scan_text(text):
    """テキストをプレースホルダパターンで検査し、(行番号, パターン名, 行内容)のリストを返す。
    コードブロック（```で囲まれた範囲）・インラインコード（`...`）・鉤括弧「」で囲まれた
    過去事例の逐語引用は対象外とする（誤検知対策。モジュールdocstring参照）。"""
    hits = []
    in_code_block = False
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        line = _strip_code_spans(raw_line)
        for name, pattern in PLACEHOLDER_PATTERNS:
            for m in pattern.finditer(line):
                if _is_inside_quoted_citation(line, m.start(), m.end()):
                    continue
                hits.append((lineno, name, raw_line.strip()))
                break  # 同一パターン・同一行の重複記録は避ける
    return hits


def scan_file(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    return scan_text(text)


def run_check(pattern=DEFAULT_GLOB, quiet=False):
    paths = sorted(glob.glob(pattern))
    all_hits = {}
    for path in paths:
        hits = scan_file(path)
        if hits:
            all_hits[path] = hits

    if not quiet:
        print(f"[check] {len(paths)}件の報告書を検査対象とした（glob: {pattern}）")
        for path, hits in all_hits.items():
            for lineno, name, line in hits:
                print(f"  FAIL {path}:{lineno} [{name}] {line}")

    return all_hits


def selftest():
    """故意注入によるFAIL実証つきの自己診断。成功パス・失敗パスが必ず別の出力に
    なることを確認する（CLAUDE.md §9-c）。"""
    ok = True
    with tempfile.TemporaryDirectory() as tmpdir:
        bad_path = os.path.join(tmpdir, "報告_selftest_bad.md")
        with open(bad_path, "w", encoding="utf-8") as f:
            f.write("# 自己診断用ダミー\n\n- マージコミット：（マージ後に追記）\n")
        clean_path = os.path.join(tmpdir, "報告_selftest_clean.md")
        with open(clean_path, "w", encoding="utf-8") as f:
            f.write("# 自己診断用ダミー\n\n- マージコミット：`abc1234`\n"
                     "- コード例（連続アンダースコアを含む識別子がコードブロック内のみに"
                     "あることを確認する）:\n"
                     "```python\ndef dunder_init(self):\n    pass\n```\n"
                     "```python\ndef __init__(self):\n    pass\n```\n")

        bad_hits = scan_file(bad_path)
        clean_hits = scan_file(clean_path)

        print(f"[selftest] 故意注入ファイル（プレースホルダあり）: "
              f"{'検出=FAIL(想定どおり)' if bad_hits else '検出なし=異常'}")
        if not bad_hits:
            print("RESULT: FAIL (selftest: 故意注入したプレースホルダを検出できなかった)")
            ok = False

        print(f"[selftest] クリーンファイル（プレースホルダなし・コードブロック内__init__含む）: "
              f"{'誤検出なし=正常' if not clean_hits else '誤検出=異常'}")
        if clean_hits:
            print(f"RESULT: FAIL (selftest: 誤検出 {clean_hits})")
            ok = False

    if ok:
        print("RESULT: PASS (selftest: 成功パス・失敗パスとも想定どおりに分離した)")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--glob", type=str, default=DEFAULT_GLOB,
                     help="検査対象のglobパターン（既定: proto/報告_*.md）")
    ap.add_argument("--selftest", action="store_true",
                     help="故意注入によるFAIL実証つき自己診断を実行する")
    args = ap.parse_args()

    if args.selftest:
        ok = selftest()
        sys.exit(0 if ok else 1)

    all_hits = run_check(args.glob)
    if all_hits:
        total = sum(len(h) for h in all_hits.values())
        print(f"RESULT: FAIL ({len(all_hits)}ファイル・{total}件のプレースホルダを検出)")
        sys.exit(1)
    print("RESULT: PASS (プレースホルダなし)")
    sys.exit(0)


if __name__ == "__main__":
    main()
