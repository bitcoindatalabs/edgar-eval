"""Split an EDGAR HTML filing into pages, keeping each page's printed page number.

  python3 build_pages.py --src <EDGAR .htm URL or local path> [--out data/processed/pages.jsonl]

Output JSONL rows: {"idx": physical page, "page": printed label or null, "text": ...}. The running
header ("Table of Contents") and the page number are removed from "text", so a sentence that
crosses a page break reads straight through. Run from your project folder; paths are relative to it.
"""
import argparse
import html
import json
import os
import re
import sys
from pathlib import Path

import requests


def user_agent() -> dict:
    """SEC rejects (403) automated requests without a contact email in the User-Agent.
    Set SEC_USER_AGENT="Your Org you@example.com" in the environment or the project's .env."""
    ua = os.environ.get("SEC_USER_AGENT")
    env = Path(".env")
    if not ua and env.exists():
        for line in env.read_text().splitlines():
            if line.strip().startswith("SEC_USER_AGENT="):
                ua = line.split("=", 1)[1].strip().strip('"')
    if not ua or "@" not in ua:
        sys.exit('SEC needs a contact email: set SEC_USER_AGENT="Your Org you@example.com" in .env')
    return {"User-Agent": ua}


# Filing agents mark printed pages differently: fixed-height page boxes, or CSS page breaks
# before or after an element. Use whichever convention finds the most pages.
PAGE_MARKERS = [
    (re.compile(r'<div style="[^"]*height:\s*792pt', re.I), "before"),
    (re.compile(r"<[a-z]+\b[^>]*page-break-before:\s*always[^>]*>", re.I), "before"),
    (re.compile(r"<[a-z]+\b[^>]*page-break-after:\s*always[^>]*>", re.I), "after"),
    (re.compile(r"<[a-z]+\b[^>]*break-(?:before|after):\s*page[^>]*>", re.I), "before"),
]
LABEL = r"(?:F-\d{1,3}|II-\d{1,2}|\d{1,3}|[ivx]{1,6})"
HEADER = re.compile(rf"^(?:({LABEL})\s+)?T\s?able of Contents\s*")
FOOTER = re.compile(rf"\s({LABEL})$")


def clean(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    text = html.unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def split_pages(raw: str) -> list[str]:
    """HTML of each printed page. Never drops text: a piece with no text (an empty page box,
    the document preamble) is joined to the page after it."""
    best: list[int] = []
    for rx, side in PAGE_MARKERS:
        cuts = [m.start() if side == "before" else m.end() for m in rx.finditer(raw)]
        if len(cuts) > len(best):
            best = cuts
    bounds = [0, *best, len(raw)]
    pages, carry = [], ""
    for a, b in zip(bounds, bounds[1:]):
        chunk = carry + raw[a:b]
        text = clean(chunk)
        if not text:
            carry = chunk
            continue
        carry = ""
        if a == 0 and len(text) < 200 and len(bounds) > 2:
            continue  # EDGAR's document header before page 1 ("S-1 1 name.htm ..."), not a page
        pages.append(chunk)
    if carry and pages:
        pages[-1] += carry
    return pages


def label_and_text(chunk: str) -> tuple[str | None, str]:
    """The printed page number and the page text without the running header and footer.
    Some agents put the number first ("12 Table of Contents ..."), others last ("... 12")."""
    text = clean(chunk)
    label = None
    m = HEADER.match(text)
    if m:
        label = m.group(1)
        text = text[m.end():]
    m = FOOTER.search(text)
    if m and not label:
        label = m.group(1)
        text = text[: m.start()]
    return label, text.strip()


def printed_label(text: str) -> str | None:
    return label_and_text(text)[0]


def load(src: str, cache: Path) -> str:
    if re.match(r"https?://", src):
        if not cache.exists():
            r = requests.get(src, headers=user_agent(), timeout=120)
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
    chunks = split_pages(raw)
    if len(chunks) < 10:
        sys.exit(f"Only {len(chunks)} pages found; no page-break convention matched this filing.")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    words = labeled = 0
    with out.open("w") as f:
        for i, chunk in enumerate(chunks, 1):
            label, text = label_and_text(chunk)
            words += len(text.split())
            labeled += bool(label)
            f.write(json.dumps({"idx": i, "page": label, "text": text}) + "\n")
    print(f"{len(chunks)} pages ({labeled} with printed labels), {words:,} words -> {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
