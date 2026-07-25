#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_doc_numbering.py ― ドキュメントの自己整合性チェック（指示書#2 §3-b）

3つの検査を行う:
  1. 見出し番号の欠番検出   ―― docs/ 配下の全 .md の "## N. " 見出しに欠番がないか
  2. 参照切れ検出           ―― 本文中の docs/*.md・CLAUDE.md・PROJECT.md への相対参照が実在するか
  3. docs_meta.json 双方向照合 ―― meta記載 vs 実ファイルの過不足

使い方:
    python3 tools/check_doc_numbering.py            # 通常実行、結果を表示
    python3 tools/check_doc_numbering.py --selftest  # 発火例・非発火例の実演（存在証明義務 §14-9）

終了コード: 問題が1件でもあれば1、なければ0（CIで使う場合を想定）。
"""

import json
import os
import re
import sys
import shutil
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DIR = os.path.join(ROOT, "docs")
META = os.path.join(ROOT, "tools", "docs_meta.json")

HEADING_RE = re.compile(r"^## (\d+)\.\s")
REF_RE = re.compile(r"(?:docs/[^\s`\)\]」』]+\.md|(?<![\w./])CLAUDE\.md|(?<![\w./])PROJECT\.md)")


def find_md_files(base):
    out = []
    for dirpath, dirnames, filenames in os.walk(base):
        for fn in filenames:
            if fn.endswith(".md"):
                out.append(os.path.join(dirpath, fn))
    return sorted(out)


def read_text(path):
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def _relpath(path, base):
    try:
        return os.path.relpath(path, base)
    except ValueError:
        # 一時ディレクトリがROOTと別ドライブの場合（selftest用）
        return path


def check_numbering(md_files):
    """各ファイル内で ## N. の整数部が連番になっているか（1始まりでなくてよいが、歯抜けはNG）。"""
    problems = []
    for path in md_files:
        rel = _relpath(path, ROOT)
        try:
            text = read_text(path)
        except Exception as e:
            problems.append(f"[読込失敗] {rel}: {e}")
            continue
        nums = []
        for line in text.split("\n"):
            m = HEADING_RE.match(line)
            if m:
                nums.append(int(m.group(1)))
        if len(nums) < 2:
            continue
        seen = sorted(set(nums))
        gaps = []
        for a, b in zip(seen, seen[1:]):
            if b - a > 1:
                gaps.extend(range(a + 1, b))
        if gaps:
            problems.append(f"[欠番] {rel}: 見出し {seen[0]}〜{seen[-1]} のうち欠番 {gaps}")
    return problems


def check_references(md_files, base_root=None):
    """docs/*.md・CLAUDE.md・PROJECT.md への参照が実在するかを確認する。"""
    root = base_root or ROOT
    problems = []
    for path in md_files:
        rel = _relpath(path, root)
        text = read_text(path)
        for m in REF_RE.finditer(text):
            ref = m.group(0)
            target = os.path.join(root, ref)
            if not os.path.exists(target):
                problems.append(f"[参照切れ] {rel} が参照する `{ref}` が存在しない")
    return problems


def check_meta_bidirectional():
    problems = []
    with open(META, encoding="utf-8") as f:
        meta = json.load(f)
    meta_files = [m["file"] for m in meta]
    for mf in meta_files:
        p = os.path.join(DOCS_DIR, mf)
        if not os.path.exists(p):
            problems.append(f"[meta→実体なし] docs_meta.json に記載されているが存在しない: {mf}")
    # 実ファイル側（docs/ 直下 + info_game_knowledge_pack_v2/ のみ。新PJ/archive は別系譜のため対象外）
    top_level = [f for f in os.listdir(DOCS_DIR)
                 if f.endswith(".md") and os.path.isfile(os.path.join(DOCS_DIR, f))]
    for f in top_level:
        if f not in meta_files:
            problems.append(f"[実体→meta記載なし] docs/ に存在するが docs_meta.json 未記載: {f}"
                             f"（意図的に非公開のものは無視してよい。要目視確認）")
    return problems


def run(base_docs_dir=None):
    docs_dir = base_docs_dir or DOCS_DIR
    project_root = os.path.dirname(docs_dir) if base_docs_dir else ROOT
    md_files = find_md_files(docs_dir)
    numbering = check_numbering(md_files)
    refs = check_references(md_files, base_root=project_root)
    meta = check_meta_bidirectional() if docs_dir == DOCS_DIR else []
    return numbering, refs, meta


def print_report(numbering, refs, meta):
    print(f"== 1. 見出し番号の欠番検出 ({len(numbering)}件) ==")
    for p in numbering:
        print(" -", p)
    if not numbering:
        print(" なし")
    print(f"\n== 2. 参照切れ検出 ({len(refs)}件) ==")
    for p in refs:
        print(" -", p)
    if not refs:
        print(" なし")
    print(f"\n== 3. docs_meta.json 双方向照合 ({len(meta)}件) ==")
    for p in meta:
        print(" -", p)
    if not meta:
        print(" なし")
    total = len(numbering) + len(refs) + len(meta)
    print(f"\n合計 {total} 件")
    return total


def selftest():
    """存在証明義務（§14-9）: 発火例（故意に壊すと必ず落ちる）と非発火例（正常なら必ず通る）を実演する。"""
    print("=== 非発火例（現状のリポジトリ。クリーンなら 0 件） ===")
    numbering, refs, meta = run()
    baseline_total = print_report(numbering, refs, meta)

    print("\n=== 発火例（一時ディレクトリに壊れたファイルを注入して検出できるか確認） ===")
    with tempfile.TemporaryDirectory() as tmp:
        broken_dir = os.path.join(tmp, "docs")
        shutil.copytree(DOCS_DIR, broken_dir)

        # 注入1: 見出し番号に欠番を作る（## 1. と ## 3. だけにして ## 2. を消す）
        injected_path = os.path.join(broken_dir, "__inject_numbering_gap.md")
        with open(injected_path, "w", encoding="utf-8") as f:
            f.write("# テスト用注入ファイル\n\n## 1. 一つ目\n\n本文\n\n## 3. 三つ目\n\n本文\n")

        # 注入2: 存在しないファイルへの参照を作る
        injected_ref_path = os.path.join(broken_dir, "__inject_broken_ref.md")
        with open(injected_ref_path, "w", encoding="utf-8") as f:
            f.write("# テスト用注入ファイル\n\n参照: docs/存在しないファイル_絶対に無い_zzz.md\n")

        numbering2, refs2, _ = run(broken_dir)
        total2 = len(numbering2) + len(refs2)
        print_report(numbering2, refs2, [])

        ok_numbering = any("__inject_numbering_gap.md" in p for p in numbering2)
        ok_ref = any("__inject_broken_ref.md" in p for p in refs2)

        print("\n--- 判定 ---")
        print(f"欠番注入を検出したか: {'PASS' if ok_numbering else 'FAIL'}")
        print(f"参照切れ注入を検出したか: {'PASS' if ok_ref else 'FAIL'}")

    print("\n=== 非発火例の再確認（注入は一時ディレクトリのみで、本体には影響していないこと） ===")
    numbering3, refs3, meta3 = run()
    total3 = print_report(numbering3, refs3, meta3)
    print(f"\n注入前 {baseline_total} 件 / 注入除去後 {total3} 件 "
          f"({'一致・本体無傷' if baseline_total == total3 else '不一致・要調査'})")

    return ok_numbering and ok_ref and (baseline_total == total3)


def main():
    if "--selftest" in sys.argv:
        ok = selftest()
        sys.exit(0 if ok else 1)

    numbering, refs, meta = run()
    total = print_report(numbering, refs, meta)
    sys.exit(1 if total else 0)


if __name__ == "__main__":
    main()
