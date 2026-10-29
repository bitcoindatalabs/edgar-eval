"""Split an EDGAR HTML filing into pages, keeping each page's printed page number.

  python3 build_pages.py --src <EDGAR .htm URL or local path> [--out data/processed/pages.jsonl]

Output JSONL rows: {"idx": physical page, "page": printed label or null, "text": ...}
Run from your project folder; paths are relative to it.
"""
import argparse
import html
import json
import re
import sys
from pathlib import Path

import requests

# SEC asks automated clients to identify themselves.
UA = {"User-Agent": "edgar-eval research (github.com/bitcoindatalabs/edgar-eval)"}

# EDGAR's HTML renderer puts every printed page in a fixed-height div.
PAGE_SPLIT = re.compile(r'(?=<div style="[^"]*height:792pt)')


def clean(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    text = html.unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def printed_label(text: str) -> str | None:
    # The footer page number is absolutely positioned at the bottom but comes
    # first in source order, so each page's text starts with "<n> Table of Contents".
    m = re.match(r"(F-\d+|II-\d+|\d{1,3}|[ivx]{1,6})\s+Table of Contents", text)
    return m.group(1) if m else None


def load(src: str, cache: Path) -> str:
    if re.match(r"https?://", src):
        if not cache.exists():
            r = requests.get(src, headers=UA, timeout=120)
            r.raise_for_status()
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(r.content)
        return cache.read_text(encoding="utf-8", errors="ignore")
    return Path(src).read_text(encoding="utf-8", errors="ignore")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="EDGAR .htm URL or local file")
    ap.add_argument("--out", default="data/processed/pages.jsonl")
    ap.add_argument("--cache", default="data/raw/filing.htm", help="where a downloaded filing is kept")
    args = ap.parse_args()

    raw = load(args.src, Path(args.cache))
    chunks = [c for c in PAGE_SPLIT.split(raw) if "height:792pt" in c[:400]]
    if not chunks:
        sys.exit("No fixed-height page divs found; this splitter expects EDGAR's paginated HTML.")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    words = labeled = 0
    with out.open("w") as f:
        for i, chunk in enumerate(chunks, 1):
            text = clean(chunk)
            label = printed_label(text)
            words += len(text.split())
            labeled += bool(label)
            f.write(json.dumps({"idx": i, "page": label, "text": text}) + "\n")
    print(f"{len(chunks)} pages ({labeled} with printed labels), {words:,} words -> {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
