"""Validate bench/questions.yaml against the filing and write bench/questions.json.

Checks, per item:
  - the gold quote appears verbatim (whitespace-normalized) in the filing; records its page
  - for direct `number` facts, the gold value appears inside the quote
  - for `derived` items, `inputs` re-computes to the gold value
  - for `not_disclosed` items, the quote really contains an empty "$ " / "% " slot
"""
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path.cwd()  # run from the project folder
PAGES = [json.loads(l) for l in (ROOT / "data/processed/pages.jsonl").open()]


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def find_page(quote: str):
    q = norm(quote)
    for p in PAGES:
        if q in p["text"]:
            return p["page"] or f"#{p['idx']}"
    return None


def value_forms(v: float) -> list[str]:
    a = abs(v)
    forms = [f"{a:,.0f}"]
    for div, word in ((1_000, "billion"), (1, "million")):
        if a >= div:
            forms.append(f"{a / div:,.1f} {word}".replace(".0 ", " "))
    if a >= 1_000_000:
        forms.append(f"{a / 1_000_000:,.0f} million")
    return forms


def main() -> int:
    items = yaml.safe_load((ROOT / "bench/questions.yaml").read_text())
    errors, ids = [], set()
    for it in items:
        iid = it["id"]
        if iid in ids:
            errors.append(f"{iid}: duplicate id")
        ids.add(iid)
        page = find_page(it["quote"])
        if not page:
            errors.append(f"{iid}: quote not found in filing")
            continue
        it["page"] = page
        q = norm(it["quote"])
        if it["kind"] == "number" and it["category"] != "derived":
            if not any(f in q for f in value_forms(it["value"])):
                errors.append(f"{iid}: value {it['value']} not visible in quote")
        if "inputs" in it:
            got = eval(it["inputs"].replace(",", ""), {"__builtins__": {}})  # trusted local file
            got = got * 100 if it["unit"] == "percent" else got
            if abs(got - it["value"]) > 0.051 * max(1, abs(it["value"]) / 100):
                errors.append(f"{iid}: inputs give {got:.2f}, gold is {it['value']}")
        if it["kind"] == "not_disclosed" and not re.search(r"\$ (,|\.|and|of|as|for)|, and % ", q):
            errors.append(f"{iid}: quote has no blank slot")
    for e in errors:
        print("FAIL", e, file=sys.stderr)
    (ROOT / "bench/questions.json").write_text(json.dumps(items, indent=1, ensure_ascii=False))
    cats = {}
    for it in items:
        cats[it["category"]] = cats.get(it["category"], 0) + 1
    print(f"{len(items)} items, {len(errors)} errors; {cats}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
