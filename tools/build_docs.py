#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_docs.py ― docs/*.md から docs.html(設計論ビューア)を再生成する

使い方:
    python3 tools/build_docs.py                 # docs/ を読んで docs.html を書き出す
    python3 tools/build_docs.py --check         # 再生成しても差分がないか確認するだけ

仕組み:
    tools/docs_template.html   ... ビューアのUI(サイドバー・検索・目次)。__DOCS_DATA__ が差し込み口
    tools/docs_meta.json       ... どのMDを、どの見出し・アイコンで並べるか
    docs/*.md                  ... 本文(これが「真実」)
        ↓
    docs.html                  ... 上記を1ファイルに焼き固めたもの(GitHub Pages で配信)

⚠️ docs.html は「生成物」である。直接編集してはいけない。
   直すなら docs/*.md を直し、このスクリプトを回す。(CLAUDE.md §5)

新しいMDを追加するとき:
    1. docs/ に .md を置く
    2. tools/docs_meta.json に {file, title, sub, icon} を追記する
    3. python3 tools/add_toc.py docs/  → python3 tools/build_docs.py
"""

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DIR = os.path.join(ROOT, "docs")
TOOLS_DIR = os.path.join(ROOT, "tools")
TEMPLATE = os.path.join(TOOLS_DIR, "docs_template.html")
META = os.path.join(TOOLS_DIR, "docs_meta.json")
OUT = os.path.join(ROOT, "docs.html")

PLACEHOLDER = "__DOCS_DATA__"


def doc_id(filename: str) -> str:
    """ビューア内部で使うID。ファイル名の記号を _ に均す。"""
    return re.sub(r"[^\w\u3040-\u30ff\u4e00-\u9fff]", "_", filename)


def build() -> str:
    meta = json.load(open(META, encoding="utf-8"))
    template = open(TEMPLATE, encoding="utf-8").read()

    if PLACEHOLDER not in template:
        sys.exit(f"エラー: テンプレートに {PLACEHOLDER} がありません")

    docs = []
    missing = []
    for m in meta:
        path = os.path.join(DOCS_DIR, m["file"])
        if not os.path.exists(path):
            missing.append(m["file"])
            continue
        md = open(path, encoding="utf-8").read()
        docs.append({
            "id": doc_id(m["file"]),
            "file": m["file"],
            "title": m["title"],
            "sub": m["sub"],
            "icon": m["icon"],
            "md": md,
        })
        print(f"  + {m['file']}  ({len(md):,} 文字)")

    if missing:
        sys.exit("エラー: docs/ に見つからないファイル: " + ", ".join(missing))

    # JSON を <script> に埋めるので、</script> の早期終了だけ潰しておく
    payload = json.dumps(docs, ensure_ascii=False)
    payload = payload.replace("</script>", "<\\/script>")

    return template.replace(PLACEHOLDER, payload)


def main():
    check_only = "--check" in sys.argv
    print(f"build_docs.py: {DOCS_DIR} → {OUT}")

    html = build()

    if check_only:
        if not os.path.exists(OUT):
            sys.exit("⚠️ docs.html がありません。build_docs.py を実行してください。")
        cur = open(OUT, encoding="utf-8").read()
        if cur != html:
            sys.exit("⚠️ docs.html が docs/*.md と食い違っています。build_docs.py を実行してください。")
        print("差分なし。docs.html は最新です。")
        return

    open(OUT, "w", encoding="utf-8").write(html)
    print(f"完了: docs.html を書き出しました ({len(html):,} バイト)")


if __name__ == "__main__":
    main()
