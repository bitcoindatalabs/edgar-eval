"""Build a compact, cited fact sheet from the filing - deterministically, no LLM.

A 420-page S-1 is ~310k tokens; a 27B model on a 24GB GPU gets ~32k. Instead of making a
small model read the filing, we pre-extract what financial questions need:
  1. key tables parsed into rows (label -> values per period), each with its page
  2. every figure the filing leaves blank ("$ ,", "$ and $", "% of"), with its sentence

  python3 build_factsheet.py [--pages data/processed/pages.jsonl] [--config bench/factsheet.yaml]

Config (YAML): {title, cover_starts_with, tables: [{name, anchor, columns}]}. `anchor` is a regex
matching the text just before the table's first row; `columns` are the period labels.
Output: data/processed/factsheet.json and factsheet.md (the .md is what the model reads).
"""
import argparse
import json
import re
from pathlib import Path

import yaml

PAGES: list[dict] = []


NUM = re.compile(r"^\(?-?[\d,]*\.?\d+\)?%?$|^—$")
FOOTNOTE = re.compile(r"^\(\d\)$")
STOP = re.compile(r"_{4,}|Table of Contents")


def to_value(tok: str):
    if tok == "—":
        return 0.0
    neg = tok.startswith("(")
    v = float(tok.strip("()%").replace(",", ""))
    return -v if neg else v


def parse_rows(text: str, k: int, max_rows: int = 40) -> list[dict]:
    """Rows are '<label words> [$] v1 [$] v2 ... vk'. A lone '(n)' right after a label is a
    footnote marker only when it makes the row one value too long."""
    rows, label, vals, raw = [], [], [], []
    toks = text.split()
    i = 0
    while i < len(toks) and len(rows) < max_rows:
        t = toks[i]
        if STOP.search(t):
            break
        if t == "$":
            i += 1
            continue
        if NUM.match(t):
            raw.append(t)
        else:
            if raw and label:
                emit(rows, label, raw, k)
                label = []
            elif raw:
                label = []
            raw = []
            label.append(t)
        i += 1
    if raw and label:
        emit(rows, label, raw, k)
    return rows


def emit(rows: list, label: list, raw: list, k: int) -> None:
    if len(raw) == k + 1 and FOOTNOTE.match(raw[0]):
        raw = raw[1:]
    if len(raw) != k:
        return
    name = re.sub(r"\s*\(\d\)$", "", " ".join(label)).strip(" :")
    name = re.sub(r"^\(unaudited\)\s*", "", name.split(": ")[-1])  # drop section prefixes
    vals = [to_value(t) for t in raw]
    if name.startswith("(") or ("Ended" in name and all(2000 <= v <= 2100 for v in vals)):
        return  # column-header or footnote residue, not a data row
    rows.append({"label": name, "values": vals, "raw": raw})


def page_label(p: dict) -> str:
    return p["page"] or f"cover-{p['idx']}"


def find_tables(specs: list[dict]) -> list[dict]:
    out = []
    for spec in specs:
        for p in PAGES:
            m = re.search(spec["anchor"], p["text"])
            if not m:
                continue
            body = p["text"][m.end():]
            # Skip the column-header tokens (years, dates) up to the first labelled row.
            body = re.sub(r"^.*?(?=[A-Z][a-z])", "", body, count=1)
            rows = parse_rows(body, len(spec["columns"]))
            if rows:
                out.append({**spec, "page": page_label(p), "rows": rows})
                break
        else:
            print(f"WARN table not found: {spec['name']}")
    return out


BLANK = re.compile(r"\$ (?=,|\.|and |of |as |for )|, and % |\band % of\b| % of total")


def find_blanks() -> list[dict]:
    seen, out = set(), []
    for p in PAGES:
        for m in BLANK.finditer(p["text"]):
            s = p["text"].rfind(". ", 0, m.start()) + 2
            e = p["text"].find(". ", m.end())
            sent = p["text"][max(0, s):e + 1 if e > 0 else m.end() + 200].strip()
            key = re.sub(r"\W", "", sent.lower())[:120]
            if key in seen or len(sent) > 600:
                continue
            seen.add(key)
            out.append({"page": page_label(p), "sentence": sent})
    return out


def fmt(v: float) -> str:
    return f"{v:,.0f}" if abs(v) >= 100 or v == int(v) else f"{v:,.2f}"


def to_markdown(cfg: dict, tables: list[dict], blanks: list[dict]) -> str:
    md = [f"# {cfg.get('title', 'Filing')}: fact sheet",
          "All amounts in USD millions unless the row says otherwise. Negative = loss/outflow. "
          "Every table cites its page. Compute derived metrics from these rows, do not estimate.", ""]
    for t in tables:
        md += [f"## {t['name']} (p. {t['page']})", "",
               "| Line item | " + " | ".join(t["columns"]) + " |",
               "|---|" + "---:|" * len(t["columns"])]
        md += ["| " + r["label"] + " | " + " | ".join(fmt(v) for v in r["values"]) + " |" for r in t["rows"]]
        md.append("")
    cover = next((p for p in PAGES if cfg.get("cover_starts_with")
                  and p["text"].startswith(cfg["cover_starts_with"])), None)
    if cover:
        md += [f"## Cover page of the prospectus (p. {page_label(cover)}): offering, ticker, underwriters", "", cover["text"], ""]
    md += ["## Figures the filing leaves BLANK (not disclosed in this preliminary S-1)",
           "If a question asks for one of these, the answer is: not disclosed.", ""]
    md += [f"- (p. {b['page']}) {b['sentence']}" for b in blanks]
    return "\n".join(md) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", default="data/processed/pages.jsonl")
    ap.add_argument("--config", default="bench/factsheet.yaml")
    ap.add_argument("--out-dir", default="data/processed")
    args = ap.parse_args()
    PAGES.extend(json.loads(l) for l in Path(args.pages).open())
    cfg = yaml.safe_load(Path(args.config).read_text())
    tables, blanks = find_tables(cfg["tables"]), find_blanks()
    out = Path(args.out_dir)
    (out / "factsheet.json").write_text(json.dumps({"tables": tables, "blanks": blanks}, indent=1))
    md = to_markdown(cfg, tables, blanks)
    (out / "factsheet.md").write_text(md)
    for t in tables:
        print(f"{t['name']:<62} p.{t['page']:<6} {len(t['rows'])} rows")
    print(f"{len(blanks)} blank figures; factsheet.md ~{len(md.split()):,} words")


if __name__ == "__main__":
    main()
