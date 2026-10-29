"""Score cached answers and write results/summary.json (feeds the leaderboard site).

  python3 runner/score.py            # all modes found under results/raw
  python3 runner/score.py --show rag # also print per-question misses for one mode

Per answer:
  correct   - number within tolerance / not_disclosed respected / text keywords present
  invented  - gave a number for a figure the filing leaves blank (the headline failure)
  grounded  - the quote the model cited really appears in the filing
"""
import argparse
import json
import re
from pathlib import Path

import yaml

ROOT = Path.cwd()  # run from the project folder
QUESTIONS = {q["id"]: q for q in json.loads((ROOT / "bench/questions.json").read_text())}
MODELS = {m["id"]: m for m in yaml.safe_load((ROOT / "models.yaml").read_text())}
# Each page's text starts with its running header ("64 Table of Contents"). Drop it, or a quote
# that runs across a page break would have the header in the middle and fail to match.
HEADER = re.compile(r"^\s*(?:(?:F-\d+|II-\d+|\d{1,3}|[ivx]{1,6})\s+)?T\s?able of Contents\s*")
FILING = " ".join(HEADER.sub("", json.loads(l)["text"]) for l in (ROOT / "data/processed/pages.jsonl").open())


def norm(s: str) -> str:
    s = s.lower().replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip()


def squash(s: str) -> str:
    # Letters and digits only: immune to table pipes, line breaks, and the filing's
    # own extraction glitches ("o ur", "reve nue").
    return re.sub(r"[^a-z0-9]", "", norm(s))


FILING_SQ = squash(FILING)
# Also match with footnote markers like "(1)" removed, as fact sheets and models drop them.
FILING_SQ_NOFN = squash(re.sub(r"\(\d\)", " ", FILING))


def grounded(quote) -> bool | None:
    """True if every fragment of the cited quote appears in the filing.

    Models stitch table rows together or elide with "..."; each fragment must be real.
    Fragments under 12 letters/digits are ignored as too short to judge.
    """
    if not quote or not isinstance(quote, str):
        return None
    parts = [squash(p) for p in re.split(r"\n|; |\.\.\.|…|\[\.\.\.\]", quote)]
    parts = [p for p in parts if len(p) >= 12]
    if not parts:
        return None
    return all(p in FILING_SQ or p in FILING_SQ_NOFN for p in parts)


def to_number(v):
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.search(r"-?\(?[\d,]*\.?\d+", v)
        if m:
            s = m.group(0).replace(",", "")
            neg = s.startswith("(") or s.startswith("-")
            n = float(s.strip("(-"))
            return -n if neg else n
    return None


def judge(q: dict, a: dict | None) -> dict:
    if not a:
        return {"correct": False, "invented": False, "grounded": None, "note": "unparseable"}
    val = to_number(a.get("value"))
    said_blank = a.get("disclosed") is False or val is None
    g = grounded(a.get("quote"))
    if q["kind"] == "not_disclosed":
        return {"correct": said_blank, "invented": not said_blank, "grounded": g,
                "note": "" if said_blank else f"invented {a.get('value')} {a.get('unit') or ''}"}
    if q["kind"] == "number":
        gold = q["value"]
        tol = q.get("tol", 0.01)
        # Sign conventions vary for losses; accept magnitude match when the answer text says loss.
        ok = val is not None and (
            abs(val - gold) <= tol * abs(gold)
            or (gold < 0 and abs(abs(val) - abs(gold)) <= tol * abs(gold))
        )
        return {"correct": ok, "invented": False, "grounded": g,
                "note": "" if ok else f"said {a.get('value')} {a.get('unit') or ''} vs {gold}"}
    text = norm(json.dumps(a))
    aliases = q.get("accept_alias", {})
    missing = [kw for kw in q["must_include"]
               if not any(norm(t) in text for t in [kw, *aliases.get(kw, [])])]
    return {"correct": not missing, "invented": False, "grounded": g,
            "note": "" if not missing else f"missing {missing}"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", help="print misses for this mode")
    args = ap.parse_args()
    summary = {"questions": len(QUESTIONS), "modes": {}}
    for mode_dir in sorted((ROOT / "results/raw").glob("*")):
        mode = mode_dir.name
        rows = []
        for f in sorted(mode_dir.glob("*.jsonl")):
            mid = f.stem.replace("__", "/")
            recs = [json.loads(l) for l in f.open()]
            per_q, cost, n_in = {}, 0.0, 0
            for r in recs:
                q = QUESTIONS.get(r["qid"])
                if not q:
                    continue
                per_q[r["qid"]] = {**judge(q, r.get("parsed")), "answer": r.get("parsed")}
                cost += r.get("cost_usd") or 0
                n_in += r.get("in_tokens") or 0
            if not per_q:
                continue
            blanks = [k for k in per_q if QUESTIONS[k]["kind"] == "not_disclosed"]
            cats = {}
            for k, v in per_q.items():
                c = cats.setdefault(QUESTIONS[k]["category"], [0, 0])
                c[0] += v["correct"]
                c[1] += 1
            cited = [v["grounded"] for v in per_q.values() if v["grounded"] is not None]
            rows.append({
                "model": mid, "label": MODELS.get(mid, {}).get("label", mid),
                "group": MODELS.get(mid, {}).get("group", "other"),
                "answered": len(per_q),
                "accuracy": round(sum(v["correct"] for v in per_q.values()) / len(per_q), 3),
                "invented_blanks": sum(per_q[k]["invented"] for k in blanks),
                "blanks_asked": len(blanks),
                "grounded_rate": round(sum(cited) / len(cited), 3) if cited else None,
                "by_category": {c: round(a / n, 3) for c, (a, n) in cats.items()},
                "cost_usd": round(cost, 4), "input_tokens": n_in,
                "per_question": per_q,
            })
            if args.show == mode:
                print(f"\n== {mode} / {rows[-1]['label']}: {rows[-1]['accuracy']:.0%}")
                for k, v in per_q.items():
                    if not v["correct"]:
                        print(f"  x {k:<24} {v['note']}")
        rows.sort(key=lambda r: (-r["accuracy"], r["invented_blanks"]))
        summary["modes"][mode] = rows
        print(f"\n[{mode}]  {'model':<22} acc   invented-blanks  grounded  cost")
        for r in rows:
            gr = f"{r['grounded_rate']:.0%}" if r["grounded_rate"] is not None else "-"
            print(f"   {r['label']:<22} {r['accuracy']:>4.0%}   {r['invented_blanks']}/{r['blanks_asked']}"
                  f"              {gr:>5}   ${r['cost_usd']:.3f}")
    out = ROOT / "results/summary.json"
    out.write_text(json.dumps(summary, indent=1, ensure_ascii=False))
    # The leaderboard (docs/, served by GitHub Pages) reads its own copies.
    site = ROOT / "docs/data"
    if not site.parent.exists():
        return print(f"\nwrote {out.relative_to(ROOT)}")
    site.mkdir(exist_ok=True)
    (site / "summary.json").write_text(json.dumps(summary, ensure_ascii=False))
    (site / "questions.json").write_text((ROOT / "bench/questions.json").read_text())
    print(f"\nwrote {out.relative_to(ROOT)} and docs/data/")


if __name__ == "__main__":
    main()
