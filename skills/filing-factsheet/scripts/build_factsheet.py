"""Build a compact, cited fact sheet from an EDGAR filing - deterministically, no LLM, no per-filing config.

A 420-page S-1 is ~300k tokens; a 27B model on a 24GB GPU gets a small fraction of that. Instead of
making a small model read the filing, we pre-extract what financial questions need:
  1. financial tables, parsed from the filing's HTML <table> markup: the filing's own column
     headers ("Six Months Ended June 30, 2026"), its units, and the page each table is on
  2. which tables: a fixed list of standard line items (revenue, cost of revenue, operating
     income, net income, cash, total assets, operating cash flow, ...). A table is added while it
     covers a line item for a period that no table already chosen covers, up to a size budget.
     Nothing here knows which questions will be asked.
  3. every figure the filing leaves blank ("$ and $", "% of", "[•]"), with its sentence and page
  4. the prospectus cover page (offering, ticker, underwriters)

  python3 build_factsheet.py [--html data/raw/filing.htm] [--pages data/processed/pages.jsonl]

Output: data/processed/factsheet.json and factsheet.md (the .md is what the model reads).
"""
import argparse
import json
import re
import sys
from pathlib import Path

import yaml
from bs4 import BeautifulSoup

from build_pages import split_pages

# Standard line items, as US GAAP statements caption them. Written for financial statements in
# general; matched against row labels (lowercase, footnote markers removed).
LINE_ITEMS = {
    "revenue": r"^(total )?(net )?(revenues?|sales)$",
    "cost of revenue": r"^(total )?cost of (revenues?|sales|goods sold)",
    "gross profit": r"^gross (profit|margin|loss)",
    "research and development": r"^research and development",
    "sales and marketing": r"^(sales|selling),? (and )?marketing",
    "general and administrative": r"^(selling, )?general,? and administrative",
    "total operating expenses": r"^total (operating )?(costs and )?expenses",
    "operating income": r"^operating (income|loss|\(loss\))|(income|loss|\(loss\)) from operations",
    "net income": r"^net (income|loss|\(loss\))",
    "earnings per share": r"per share",
    "stock-based compensation": r"stock-based compensation",
    "cash": r"^cash(,)? (and )?cash equivalents",
    "total assets": r"^total assets$",
    "total liabilities": r"^total liabilities$",
    "equity": r"^total (stockholders|shareholders).? (equity|deficit)",
    "operating cash flow": r"^net cash .*operating activities",
    "investing cash flow": r"^net cash .*investing activities",
    "financing cash flow": r"^net cash .*financing activities",
    "non-gaap": r"^adjusted |^free cash flow|ebitda",
}
LINE_RE = {k: re.compile(v) for k, v in LINE_ITEMS.items()}

PERIOD = re.compile(r"\bended\b|\bas of\b|\bfiscal\b|\b(19|20)\d{2}\b", re.I)
YEAR = re.compile(r"(19|20)\d{2}")
UNIT = re.compile(r"\((?:dollars |amounts )?in (?:millions|thousands|billions)[^)]*\)", re.I)
NIL = {"—", "–", "-", "— %", "—%"}
# Row labels that only make sense under their section heading ("Net loss per share:" / "Basic").
QUALIFIERS = {"basic", "diluted", "basic and diluted", "total", "other", "net", "current", "noncurrent", "non-current"}
VALUE = re.compile(r"\$?\s*(\()?\s*\$?\s*(-?\d[\d,]*(?:\.\d+)?)\s*(\))?\s*(%)?\s*\)?")
FOOTNOTE = re.compile(r"\s*\((?:\d{1,2}|[a-z])\)")


def cell_value(text: str) -> str | None:
    """'$ 1,163' -> '1,163'; '(551)' or '$ (1,045' -> '-551' / '-1,045'; '—' -> '—'; text -> None."""
    if text in NIL:
        return "—"
    m = VALUE.fullmatch(text)
    if not m:
        return None
    neg = bool(m.group(1)) or m.group(2).startswith("-")
    return ("-" if neg else "") + m.group(2).lstrip("-") + ("%" if m.group(4) else "")


def grid(table) -> list[list[tuple[int, int, str]]]:
    """Each row as (first column, last column + 1, text) for its non-empty cells, colspans expanded."""
    rows = []
    for tr in table.find_all("tr"):
        col, cells = 0, []
        for td in tr.find_all(["td", "th"]):
            span = int(re.sub(r"\D", "", td.get("colspan") or "1") or 1)
            text = re.sub(r"\s+", " ", td.get_text(" ", strip=True).replace("\xa0", " ")).strip()
            if text:
                cells.append((col, col + span, text))
            col += span
        rows.append(cells)
    return rows


def caption(table) -> str:
    """The text just before the table on its page: usually its title and units."""
    bits = []
    for s in table.find_all_previous(string=True, limit=80):
        if s.find_parent("table") is not None:
            break
        if s.strip():
            bits.append(s.strip())
        if sum(map(len, bits)) > 400:
            break
    text = re.sub(r"\s+", " ", " ".join(reversed(bits))).strip()
    return re.sub(r"^.*T\s?able of Contents\s*", "", text)


def title_of(cap: str) -> str:
    """The sentence(s) that introduce the table, without running headers like "Notes ... (Continued)"."""
    text = re.sub(r"^.*\(continued\)\s+(?=\S)", "", cap, flags=re.I).strip()
    starts = [m.end() for m in re.finditer(r"[.:]\s+(?=[A-Z(])", text[:-2])]
    for s in reversed(starts):  # the last sentence, or the last two if it is a stub like "GAAP."
        if len(text) - s >= 30:
            text = text[s:]
            break
    if len(text) > 200:
        text = text[-200:]
        text = text[text.find(" ") + 1:]  # start at a word boundary
    return text.strip()


def values_of(cells: list) -> list[tuple[int, int, str]]:
    """Numeric cells in a row as (first column, end column, value); a lone "%" cell marks the
    value before it as a percentage."""
    vals = []
    for a, b, text in cells:
        v = cell_value(text)
        if v is not None:
            vals.append((a, b, v))
        elif text in ("%", ")%", "% )") and vals and not vals[-1][2].endswith("%"):
            vals[-1] = (vals[-1][0], vals[-1][1], vals[-1][2] + "%")
    return vals


def column_spans(header: list, data: list) -> list[tuple[int, int]]:
    """Value columns. Prefer the deepest header row's cells (their colspans cover the "$" cell and
    the number, wherever a row puts it); fall back to positions most rows use."""
    for cells in reversed(header):
        spans = [(a, b) for a, b, t in cells
                 if a > 0 and not UNIT.search(t) and t.lower() not in ("(unaudited)", "$", "%")]
        if spans and any(min(b, vb) > max(a, va) for a, b in spans for _, vals in data for va, vb, _ in vals):
            return spans
    counts: dict[int, int] = {}
    for _, vals in data:
        for a, _, _ in vals:
            counts[a] = counts.get(a, 0) + 1
    return [(c, c + 1) for c in sorted(c for c, n in counts.items() if n >= max(2, len(data) / 3))]


def parse_table(table, page: str) -> dict | None:
    rows = grid(table)
    data, header, section, seen = [], [], "", set()
    for cells in rows:
        if not cells:
            continue
        vals = values_of(cells)
        words = [c for c in cells if cell_value(c[2]) is None and c[2] not in ("$", ")", "%", "(", ")%")]
        label = words[0][2] if words and (not vals or words[0][0] < vals[0][0]) else ""
        years_only = vals and all(YEAR.fullmatch(v.lstrip("-")) for _, _, v in vals) and not data
        if not data and (not vals or years_only or not label):
            header.append(cells)  # rows above the first data row are column headers
            continue
        if not vals:
            if label:
                section = FOOTNOTE.sub("", label).strip(" :")  # "Cost of revenue(1):"
            continue
        name = FOOTNOTE.sub("", label).strip(" :")
        if section and (name.lower() in QUALIFIERS or name.lower() in seen):
            name = f"{section}: {name}"  # a repeated "Subscription" -> "Cost of revenue: Subscription"
        seen.add(FOOTNOTE.sub("", label).strip(" :").lower())
        data.append((name, vals))
    if len(data) < 3:
        return None

    spans = column_spans(header, data)
    if not spans:
        return None
    unit_bits, headers = [], []
    for cells in header:
        unit_bits += [t for _, _, t in cells if UNIT.search(t) or t.lower() == "(unaudited)"]
    for sa, sb in spans:
        parts = [t for cells in header for a, b, t in cells
                 if a > 0 and a <= sa < b and not UNIT.search(t) and t.lower() not in ("(unaudited)", "$", "%")]
        headers.append(re.sub(r"\s+", " ", " ".join(parts)).strip() or f"col {len(headers) + 1}")
    if not any(PERIOD.search(h) for h in headers):
        return None

    out_rows = []
    for name, vals in data:
        row = [None] * len(spans)
        for va, vb, v in vals:
            overlap = [min(b, vb) - max(a, va) for a, b in spans]
            j = max(range(len(spans)), key=lambda k: overlap[k])
            if overlap[j] <= 0:
                j = min(range(len(spans)), key=lambda k: abs(spans[k][0] - va))
                if abs(spans[j][0] - va) > 2:
                    continue
            if row[j] is None:
                row[j] = v
        if any(v is not None for v in row):
            out_rows.append({"label": name, "values": row})
    cap = caption(table)
    unit = next((m.group(0) for t in [cap, *unit_bits] if (m := UNIT.search(t))), "")
    title = title_of(cap) or "Table of " + ", ".join(r["label"] for r in out_rows[:3])
    return {"page": page, "pages": [page], "title": title, "caption": cap[-400:], "unit": unit, "columns": headers, "rows": out_rows,
            # A continuation's caption ends at "(continued)" (plus units): no sentence of its own.
            "continued": bool(re.search(r"\(continued\)\s*(?:\([^)]*\)\s*)?$", cap, re.I))}


def line_items(t: dict) -> set[str]:
    found = set()
    for r in t["rows"]:
        lab = r["label"].lower().split(": ")[-1]
        found |= {k for k, rx in LINE_RE.items() if rx.search(lab)}
    return found


def is_percent(t: dict) -> bool:
    """A "% of revenue" table: its caption says so, or one row is 100% in every column."""
    if re.search(r"\bpercent(age)? of\b", t["title"], re.I):
        return True
    return any(all(v and v.rstrip("%") == "100" for v in r["values"]) for r in t["rows"])


def coverage(t: dict) -> set[tuple[str, str]]:
    """(line item, period) pairs a table answers; the selection objective. A table must carry at
    least 3 standard line items in amounts (not % of revenue) to count as a statement."""
    items = line_items(t)
    if len(items) < 3 or is_percent(t):
        return set()
    periods = [re.sub(r"\W+", " ", h.lower()).strip() for h in t["columns"] if PERIOD.search(h)]
    return {(li, p) for li in items for p in periods}


def render(t: dict, max_rows: int = 60) -> str:
    unit = f" {t['unit']}" if t["unit"] and t["unit"].lower() not in t["title"].lower() else ""
    md = [f"## {t['title']} (p. {'–'.join(t['pages'])}){unit}", "",
          "| Line item | " + " | ".join(t["columns"]) + " |",
          "|---|" + "---:|" * len(t["columns"])]
    md += ["| " + r["label"] + " | " + " | ".join(v or "" for v in r["values"]) + " |" for r in t["rows"][:max_rows]]
    return "\n".join(md) + "\n"


def select(tables: list[dict], budget: int) -> list[dict]:
    """Greedy: keep taking the table that adds the most uncovered (line item, period) pairs per
    character, until nothing new is covered or the budget is spent."""
    chosen, covered, used = [], set(), 0
    pool = [t for t in tables if coverage(t)]
    while pool:
        best = max(pool, key=lambda t: len(coverage(t) - covered) / max(len(render(t)), 1500))
        pool.remove(best)
        if coverage(best) - covered and used + len(render(best)) <= budget:
            chosen.append(best)
            covered |= coverage(best)
            used += len(render(best))
    return sorted(chosen, key=lambda t: tables.index(t))


def check_values(tables: list[dict], pages: list[dict]) -> int:
    """Every extracted number must appear on the page the table cites; returns how many do not."""
    text = {page_label(p): re.sub(r"[^\d]", " ", p["text"]) for p in pages}
    bad = 0
    for t in tables:
        hay = " ".join(" ".join(text.get(pg, "").split()) for pg in t["pages"])
        for r in t["rows"]:
            for v in r["values"]:
                digits = re.sub(r"\D", "", v or "")
                if digits and digits not in hay.replace(" ", ""):
                    bad += 1
                    print(f"  WARN p.{t['page']} {r['label'][:40]}: {v} not found on that page")
    return bad


# Operating metrics live in small tables with few GAAP line items (users, ARR, GTV, net revenue
# retention). Include them by what their caption says they are, after the statements.
KEY_TABLE = re.compile(r"\bkey (?:business |operating |financial )?(?:metrics|performance indicators)\b"
                       r"|\bdisaggregat|\b(?:revenue|users?|customers?) by (?:geograph|region|segment|product)", re.I)


def key_tables(tables: list[dict], chosen: list[dict], budget: int) -> list[dict]:
    out, used = [], 0
    have = {tuple(r["label"] for r in t["rows"]) for t in chosen}
    for t in tables:
        rows = tuple(r["label"] for r in t["rows"])
        if t in chosen or rows in have or is_percent(t) or not KEY_TABLE.search(t["caption"] + " " + t["title"]):
            continue
        size = len(render(t))
        if used + size <= budget:
            out.append(t)
            have.add(rows)
            used += size
    return out


def merge_continued(tables: list[dict]) -> list[dict]:
    """EDGAR breaks long statements across pages; a "(continued)" table with the same columns
    is the rest of the one before it."""
    out = []
    for t in tables:
        if out and t["columns"] == out[-1]["columns"] and t["continued"]:
            out[-1]["rows"] += t["rows"]
            out[-1]["pages"].append(t["page"])
        else:
            out.append(t)
    return out


# A figure left blank: "$" with no number after it, "%" with no number before it, or a bracket
# placeholder. "$ —" and "— %" are nil values in tables, not blanks.
BLANK = re.compile(
    r"\$\s+(?![\d(—–-]|\.\d|[Cc]hange\b)"
    r"|(?<![\d.%—–-])\s%(?=\s+(?:of|and|to|in|per|for|on)\b|[,.;)])"
    r"|\[\s*(?:•|●|\*|_+|—|–)?\s*\]"
    r"|\b(?:as of|dated|after|before|through)\s+,"
)


def page_label(p: dict) -> str:
    return p["page"] or f"cover-{p['idx']}"


def find_blanks(pages: list[dict]) -> list[dict]:
    seen, out = set(), []
    for p in pages:
        for m in BLANK.finditer(p["text"]):
            s = p["text"].rfind(". ", 0, m.start()) + 2
            e = p["text"].find(". ", m.end())
            sent = p["text"][max(0, s):e + 1 if e > 0 else m.end() + 200].strip()
            sent = re.sub(r"^.*Table of Contents\s*", "", sent)
            if len(re.findall(r"\d[\d,.]*", sent)) > 0.35 * len(sent.split()):
                continue  # a run of table cells, not a sentence
            key = re.sub(r"\W", "", sent.lower())[:120]
            if key in seen or len(sent) > 600:
                continue
            seen.add(key)
            out.append({"page": page_label(p), "sentence": sent, "snippet": snippet(sent)})
    return out


def snippet(sent: str, before: int = 110, after: int = 70) -> str:
    """The words around the first blank in a sentence: enough to recognize the figure, a third
    of the tokens of the whole sentence."""
    m = BLANK.search(sent)
    if not m or len(sent) <= before + after + 40:
        return sent
    a, b = max(0, m.start() - before), min(len(sent), m.end() + after)
    a = sent.find(" ", a) + 1 if a > 0 else 0
    b = sent.rfind(" ", 0, b) if b < len(sent) else b
    return ("…" if a > 0 else "") + sent[a:b].strip() + ("…" if b < len(sent) else "")


def find_cover(pages: list[dict]) -> dict | None:
    # Item 501(b)(10): a preliminary prospectus cover carries the "Subject to Completion" legend.
    return next((p for p in pages if re.search(r"subject to completion", p["text"], re.I)), None)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", default="data/raw/filing.htm", help="the EDGAR .htm (build_pages.py caches it here)")
    ap.add_argument("--pages", default="data/processed/pages.jsonl")
    ap.add_argument("--title", help="document name (default: bench/meta.yaml `document`)")
    ap.add_argument("--budget", type=int, default=16000, help="max characters of statement tables")
    ap.add_argument("--key-budget", type=int, default=5000, help="max characters of key-metric tables")
    ap.add_argument("--out-dir", default="data/processed")
    args = ap.parse_args()

    pages = [json.loads(l) for l in Path(args.pages).open()]
    meta = Path("bench/meta.yaml")
    title = args.title or (yaml.safe_load(meta.read_text()).get("document") if meta.exists() else "Filing")
    raw = Path(args.html).read_text(encoding="utf-8", errors="ignore")
    chunks = split_pages(raw)
    if len(chunks) != len(pages):
        sys.exit(f"{len(chunks)} HTML pages but {len(pages)} rows in {args.pages}; rebuild pages first.")

    tables = []
    for chunk, p in zip(chunks, pages):
        page = page_label(p)
        for tb in BeautifulSoup(chunk, "lxml").find_all("table"):
            t = parse_table(tb, page)
            if t:
                tables.append(t)
    tables = merge_continued(tables)
    units = [re.search(r"millions|thousands|billions", t["unit"], re.I) for t in tables]
    units = [u.group(0).lower() for u in units if u]
    if units:  # a table that does not state its unit gets the filing's usual one, said as such
        common = max(set(units), key=units.count)
        for t in tables:
            t["unit"] = t["unit"] or f"(unit not stated here; this filing's tables are in {common})"
    chosen = select(tables, args.budget)
    chosen = sorted(chosen + key_tables(tables, chosen, args.key_budget), key=lambda t: tables.index(t))
    blanks = find_blanks(pages)
    cover = find_cover(pages)

    md = [f"# {title}: fact sheet",
          "Tables are parsed from the filing; column headers and units are the filing's own. "
          "Negative = loss or outflow; — = nil. Every table and blank cites its page.", ""]
    md += [render(t) for t in chosen]
    if cover:
        md += [f"## Prospectus cover page (p. {page_label(cover)}): offering, ticker, underwriters", "", cover["text"], ""]
    md += ["## Figures the filing leaves BLANK (not disclosed in this preliminary prospectus)",
           "If a question asks for one of these, the answer is: not disclosed.", ""]
    md += [f"- (p. {b['page']}) {b['snippet']}" for b in blanks]
    text = "\n".join(md) + "\n"

    out = Path(args.out_dir)
    (out / "factsheet.json").write_text(json.dumps({"tables": chosen, "blanks": blanks}, indent=1))
    (out / "factsheet.md").write_text(text)
    bad = check_values(chosen, pages)
    print(f"{len(tables)} financial tables parsed, {len(chosen)} chosen; {bad} values not found on their page:")
    for t in chosen:
        print(f"  p.{'–'.join(t['pages']):<9} {len(t['rows']):>3} rows x {len(t['columns'])} cols  {t['title'][:70]}")
    print(f"{len(blanks)} blank figures; cover: p.{page_label(cover) if cover else 'not found'}; "
          f"factsheet.md {len(text):,} chars (~{len(text) // 4:,} tokens)")


if __name__ == "__main__":
    main()
