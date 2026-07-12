#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
add_toc.py ― Markdown に GitHub 互換のクリック可能な目次を付ける

使い方:
    python3 tools/add_toc.py docs/*.md          # 指定ファイルに目次を付与/更新
    python3 tools/add_toc.py docs/              # ディレクトリ内の全 .md を処理
    python3 tools/add_toc.py --check docs/      # 差分があるかだけ確認(CI向け)

仕様:
  ・<!-- TOC --> 〜 <!-- /TOC --> の間を目次として管理する
  ・既にマーカーがあれば中身を差し替え、なければ最初の見出しの直前に挿入する
  ・h2(##) と h3(###) を拾う。h1(#) は文書タイトルなので拾わない
  ・コードブロック(``` 〜 ```)内の # は見出しとして扱わない
  ・アンカーは GitHub の規則に従う:
      小文字化 → 空白をハイフン → 句読点・記号を除去 → 重複には -1, -2 を付す
    (日本語などの非ASCII文字はそのまま残る)

CLAUDE.md §5:「Markdown を作成・更新したら、必ずこれを通す」
"""

import sys
import os
import re
import glob
import unicodedata

TOC_START = "<!-- TOC -->"
TOC_END = "<!-- /TOC -->"


def slugify(text: str) -> str:
    """GitHub 互換のアンカーを生成する。"""
    # マークダウン記法を剥がす
    t = re.sub(r"`([^`]*)`", r"\1", text)              # `code`
    t = re.sub(r"\*\*([^*]*)\*\*", r"\1", t)            # **bold**
    t = re.sub(r"\*([^*]*)\*", r"\1", t)                # *italic*
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)      # [text](url)

    t = t.strip().lower().replace(" ", "-")

    out = []
    for ch in t:
        if ch in "-_":
            out.append(ch)
            continue
        cat = unicodedata.category(ch)
        # GitHub は句読点(P*)・記号(S*)・区切り(Z*)を除去する
        if cat[0] in "PSZ":
            continue
        out.append(ch)
    return "".join(out)


def display_text(text: str) -> str:
    """目次に表示する文字列(強調を外し、角括弧をエスケープ)。"""
    t = re.sub(r"\*\*([^*]*)\*\*", r"\1", text)
    t = re.sub(r"\*([^*]*)\*", r"\1", t)
    # [ ] はリンク記法と衝突するので全角に置換
    return t.replace("[", "［").replace("]", "］")


def collect_headings(lines):
    """(レベル, テキスト) の一覧を返す。コードブロック内は無視。"""
    heads = []
    in_code = False
    for ln in lines:
        if ln.lstrip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        m = re.match(r"^(#{2,3})\s+(.*)$", ln)
        if not m:
            continue
        text = m.group(2).strip()
        if "目次" in text:          # 目次の見出し自身は拾わない
            continue
        heads.append((len(m.group(1)), text))
    return heads


def build_toc(heads) -> str:
    seen = {}
    out = [TOC_START, "## 目次", ""]
    for level, text in heads:
        slug = slugify(text)
        if slug in seen:
            seen[slug] += 1
            slug = f"{slug}-{seen[slug]}"
        else:
            seen[slug] = 0
        indent = "  " * (level - 2)
        out.append(f"{indent}- [{display_text(text)}](#{slug})")
    out += ["", TOC_END]
    return "\n".join(out)


def process(path: str, check_only: bool = False) -> bool:
    """目次を付与/更新する。変更があれば True を返す。"""
    src = open(path, encoding="utf-8").read()
    lines = src.split("\n")

    heads = collect_headings(lines)
    if not heads:
        print(f"  - {os.path.basename(path)}: 見出し(##)がないので何もしない")
        return False

    toc = build_toc(heads)

    if TOC_START in src and TOC_END in src:
        # 既存の目次を差し替える
        new = re.sub(
            re.escape(TOC_START) + r".*?" + re.escape(TOC_END),
            toc.replace("\\", "\\\\"),
            src,
            flags=re.S,
        )
    else:
        # 最初の見出し(## 以降)の直前に挿入する
        idx = None
        in_code = False
        for i, ln in enumerate(lines):
            if ln.lstrip().startswith("```"):
                in_code = not in_code
                continue
            if not in_code and re.match(r"^#{2,3}\s+", ln):
                idx = i
                break
        if idx is None:
            return False
        lines.insert(idx, toc + "\n\n---\n")
        new = "\n".join(lines)

    changed = new != src
    if changed and not check_only:
        open(path, "w", encoding="utf-8").write(new)

    mark = "更新" if changed else "変更なし"
    print(f"  - {os.path.basename(path)}: {len(heads)} 見出し … {mark}")
    return changed


def expand(args):
    files = []
    for a in args:
        if os.path.isdir(a):
            files += sorted(glob.glob(os.path.join(a, "*.md")))
        else:
            files += sorted(glob.glob(a))
    return files


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    check_only = "--check" in sys.argv

    if not args:
        print(__doc__)
        sys.exit(1)

    files = expand(args)
    if not files:
        print("対象の .md が見つかりません")
        sys.exit(1)

    print(f"add_toc.py: {len(files)} ファイルを処理")
    changed = [process(f, check_only) for f in files]

    if check_only and any(changed):
        print("\n⚠️  目次が古いファイルがあります。add_toc.py を実行してください。")
        sys.exit(1)

    print("完了")


if __name__ == "__main__":
    main()
