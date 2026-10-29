"""Run an edgar-eval benchmark against models or pipelines (run from the project folder).

  python3 runner/run_bench.py --mode rag                 # all models in runner/models.yaml
  python3 runner/run_bench.py --mode closed --models x-ai/grok-4.7
  python3 runner/run_bench.py --mode full --limit 5      # whole filing in context (expensive)

Answers are cached in results/raw/<mode>/<model>.jsonl, so re-runs only fill gaps.
"""
import argparse
import json
import math
import os
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
import yaml

ROOT = Path.cwd()  # run from the project folder
PAGES = [json.loads(l) for l in (ROOT / "data/processed/pages.jsonl").open()]
QUESTIONS = json.loads((ROOT / "bench/questions.json").read_text())
MODELS = yaml.safe_load((ROOT / "models.yaml").read_text())
# bench/meta.yaml names the document in every prompt, e.g.
# document: "Anthropic, PBC's Form S-1 (preliminary prospectus, filed October 26, 2026)"
DOC = yaml.safe_load((ROOT / "bench/meta.yaml").read_text())["document"]

SYSTEM_DOC = """You are a careful financial analyst. Answer strictly from the provided text of \
{DOC}. Do not use outside \
knowledge or press coverage. A preliminary prospectus leaves some figures blank (e.g. "$ " or \
"% " with no number). If the requested figure is blank or not stated, set "disclosed" to false \
and "value" to null rather than estimating it. Derived figures (ratios, margins, growth) may be \
computed from numbers that are disclosed."""

SYSTEM_CLOSED = """You are a careful financial analyst answering questions about \
{DOC}. You have no document in front of you. \
If you do not know, or the figure was not disclosed, set "disclosed" to false and "value" to null."""

FORMAT = """Respond with only a JSON object:
{"answer": "<one-sentence answer>", "value": <number or null>, "unit": "<unit of value>",
 "disclosed": <true|false>, "page": "<page number you relied on, or null>",
 "quote": "<exact sentence or table row from the filing supporting the answer, or null>"}"""


def load_env() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


# ------------------------------------------------------------------ retrieval

TOKEN = re.compile(r"[a-z0-9$%]+")


class BM25:
    def __init__(self, docs: list[str], k1: float = 1.4, b: float = 0.75):
        self.tfs = [Counter(TOKEN.findall(d.lower())) for d in docs]
        self.lens = [sum(tf.values()) for tf in self.tfs]
        self.avg = sum(self.lens) / len(self.lens)
        df = Counter(t for tf in self.tfs for t in tf)
        n = len(docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.k1, self.b = k1, b

    def top(self, query: str, k: int) -> list[int]:
        q = set(TOKEN.findall(query.lower()))
        scores = []
        for i, tf in enumerate(self.tfs):
            s = 0.0
            for t in q & tf.keys():
                f = tf[t]
                s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.lens[i] / self.avg))
            scores.append(s)
        return sorted(range(len(scores)), key=lambda i: -scores[i])[:k]


INDEX = BM25([p["text"] for p in PAGES])


def page_block(p: dict) -> str:
    return f"[Page {p['page'] or '#' + str(p['idx'])}]\n{p['text']}"


# ------------------------------------------------------------------ skill mode
# Small-model recipe: a deterministic fact sheet (tables + blank figures), fine-grained
# passages instead of whole pages, and arithmetic done by code, not the model.

FACTSHEET = ROOT / "data/processed/factsheet.md"


def make_passages(words_per: int = 180, overlap: int = 40) -> list[dict]:
    out = []
    for p in PAGES:
        w = p["text"].split()
        for s in range(0, max(1, len(w) - overlap), words_per - overlap):
            out.append({"page": p["page"] or f"cover-{p['idx']}", "text": " ".join(w[s:s + words_per])})
    return out


PASSAGES = make_passages()
PINDEX = BM25([p["text"] for p in PASSAGES])

SYSTEM_SKILL = """You are a careful financial analyst answering questions about \
{DOC}, using only the FACT SHEET and PASSAGES \
below. Rules:
1. Look up figures in the fact sheet tables first; they are exact and cite their page.
2. If the question asks for a figure listed under "Figures the filing leaves BLANK", or the \
figure is blank ("$ " with no number), answer not disclosed: disclosed=false, value=null.
3. Never do arithmetic in your head. For any ratio, margin, growth multiple, or sum, put a Python \
arithmetic expression using the exact table numbers in "calc", e.g. "(16286 - 2380) / 16286 * 100". \
The system computes it and uses the result as the value.
4. Quote a sentence or table row exactly as it appears."""
SYSTEM_DOC, SYSTEM_CLOSED, SYSTEM_SKILL = (s.replace("{DOC}", DOC) for s in (SYSTEM_DOC, SYSTEM_CLOSED, SYSTEM_SKILL))

SKILL_FORMAT = FORMAT.replace('"quote":', '"calc": "<arithmetic expression or null>",\n "quote":')


def safe_calc(expr) -> float | None:
    if not isinstance(expr, str) or not re.fullmatch(r"[\d\s.+\-*/()]+", expr.replace(",", "")):
        return None
    try:
        return round(float(eval(expr.replace(",", ""), {"__builtins__": {}})), 4)
    except Exception:
        return None


def build_prompt(q: dict, mode: str, k: int) -> tuple[str, str]:
    if mode == "skill":
        unit = f"\nReport any numeric value in {q['unit']}." if q.get("kind") == "number" else ""
        hits = sorted(PINDEX.top(q["question"], 12))
        passages = "\n\n".join(f"[Page {PASSAGES[i]['page']}] {PASSAGES[i]['text']}" for i in hits)
        user = (f"<fact_sheet>\n{FACTSHEET.read_text()}</fact_sheet>\n\n<passages>\n{passages}\n</passages>\n\n"
                f"Question: {q['question']}{unit}\n\n{SKILL_FORMAT}")
        return SYSTEM_SKILL, user
    unit = f"\nReport any numeric value in {q['unit']}." if q["kind"] == "number" else ""
    ask = f"Question: {q['question']}{unit}\n\n{FORMAT}"
    if mode == "closed":
        return SYSTEM_CLOSED, ask
    if mode == "rag":
        pages = [PAGES[i] for i in sorted(INDEX.top(q["question"], k))]
    else:
        pages = PAGES
    doc = "\n\n".join(page_block(p) for p in pages if p["text"])
    return SYSTEM_DOC, f"<filing>\n{doc}\n</filing>\n\n{ask}"


# ------------------------------------------------------------------ providers

def user_content(model: str, user: str):
    # Anthropic models need an explicit cache breakpoint; others cache repeated prefixes
    # automatically. The filing is the stable prefix, so live questions reuse it.
    if model.startswith("anthropic/") and "</filing>" in user:
        doc, rest = user.split("</filing>", 1)
        return [{"type": "text", "text": doc + "</filing>", "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": rest}]
    return user


def call_openrouter(model: str, system: str, user: str) -> dict:
    r = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
                 "X-Title": "edgar-eval"},
        json={"model": model, "temperature": 0,
              "usage": {"include": True}, "reasoning": {"max_tokens": 3000}, "max_tokens": 32000 if len(user) > 500_000 else 8000,
              "messages": [{"role": "system", "content": system},
                           {"role": "user", "content": user_content(model, user)}]},
        timeout=600,
    )
    r.raise_for_status()
    d = r.json()
    u = d.get("usage", {})
    return {"text": d["choices"][0]["message"]["content"] or "",
            "in_tokens": u.get("prompt_tokens"), "out_tokens": u.get("completion_tokens"),
            "cost_usd": u.get("cost")}


def call_gemini(model: str, system: str, user: str) -> dict:
    name = model.split("/", 1)[-1]
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{name}:generateContent",
        headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
        json={"systemInstruction": {"parts": [{"text": system}]},
              "contents": [{"role": "user", "parts": [{"text": user}]}],
              "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}},
        timeout=600,
    )
    r.raise_for_status()
    d = r.json()
    u = d.get("usageMetadata", {})
    text = "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"])
    return {"text": text, "in_tokens": u.get("promptTokenCount"),
            "out_tokens": u.get("candidatesTokenCount"), "cost_usd": None}


PROVIDERS = {"openrouter": call_openrouter, "gemini": call_gemini}


def parse_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def ask(model: dict, q: dict, mode: str, k: int) -> dict:
    system, user = build_prompt(q, mode, k)
    t0 = time.time()
    for attempt in range(3):
        try:
            out = PROVIDERS[model["provider"]](model["id"], system, user)
            break
        except requests.RequestException as e:
            err = str(e)
            time.sleep(2 ** attempt * 3)
    else:
        return {"qid": q["id"], "error": err}
    text = out.pop("text")
    parsed = parse_json(text)
    if parsed and mode == "skill" and parsed.get("calc"):
        v = safe_calc(parsed["calc"])  # code does the arithmetic, not the model
        if v is not None:
            parsed["model_value"], parsed["value"] = parsed.get("value"), v
    if not parsed:
        out["raw"] = text[-2000:]  # keep for error analysis
    return {"qid": q["id"], "parsed": parsed,
            "latency_s": round(time.time() - t0, 2), **out}


BATCH_FORMAT = """Answer every question. Respond with only a JSON array, one object per question:
[{"id": "<question id>", "answer": "<one-sentence answer>", "value": <number or null>,
  "unit": "<unit>", "disclosed": <true|false>, "page": "<page or null>",
  "quote": "<exact supporting sentence or table row from the filing, or null>"}, ...]"""


def ask_batch(model: dict, qs: list[dict]) -> list[dict]:
    """Full mode: the whole filing once, all questions in one call (cheap and cache-friendly)."""
    lines = []
    for q in qs:
        unit = f" (report value in {q['unit']})" if q["kind"] == "number" else ""
        lines.append(f"- id: {q['id']}\n  question: {q['question']}{unit}")
    doc = "\n\n".join(page_block(p) for p in PAGES if p["text"])
    user = f"<filing>\n{doc}\n</filing>\n\nQuestions:\n" + "\n".join(lines) + f"\n\n{BATCH_FORMAT}"
    t0 = time.time()
    out = PROVIDERS[model["provider"]](model["id"], SYSTEM_DOC, user)
    m = re.search(r"\[.*\]", out["text"], re.S)
    try:
        answers = {a.get("id"): a for a in json.loads(m.group(0))} if m else {}
    except json.JSONDecodeError:
        answers = {}
    share = lambda v: round(v / len(qs), 6) if v else v
    return [{"qid": q["id"], "parsed": answers.get(q["id"]), "batch": True,
             "latency_s": round(time.time() - t0, 2), "in_tokens": share(out["in_tokens"]),
             "out_tokens": share(out["out_tokens"]), "cost_usd": share(out["cost_usd"])}
            for q in qs]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["closed", "rag", "full", "skill"], default="rag")
    ap.add_argument("--models", nargs="*", help="model ids (default: all in models.yaml)")
    ap.add_argument("--ids", nargs="*", help="question ids (default: all)")
    ap.add_argument("--limit", type=int, help="first N questions only")
    ap.add_argument("--k", type=int, default=8, help="pages retrieved in rag mode")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    load_env()

    models = [m for m in MODELS if not args.models or m["id"] in args.models]
    qs = [q for q in QUESTIONS if not args.ids or q["id"] in args.ids][: args.limit]
    for m in models:
        key = {"openrouter": "OPENROUTER_API_KEY", "gemini": "GEMINI_API_KEY"}[m["provider"]]
        if not os.environ.get(key):
            sys.exit(f"{key} is not set (put it in .env) - needed for {m['id']}")

    for m in models:
        out = ROOT / "results/raw" / args.mode / (m["id"].replace("/", "__") + ".jsonl")
        out.parent.mkdir(parents=True, exist_ok=True)
        done = {json.loads(l)["qid"] for l in out.open()} if out.exists() else set()
        todo = [q for q in qs if q["id"] not in done]
        print(f"{m['label']:<22} {args.mode}: {len(todo)} to run ({len(done)} cached)", file=sys.stderr)
        if args.mode == "full" and todo:
            try:
                recs = ask_batch(m, todo)
            except requests.RequestException as e:
                print(f"  ! batch failed: {str(e)[:200]}", file=sys.stderr)
                continue
            with out.open("a") as f:
                for rec in recs:
                    if rec["parsed"]:  # unanswered ids stay uncached and get retried
                        f.write(json.dumps(rec) + "\n")
            print(f"  answered {sum(1 for r in recs if r['parsed'])}/{len(recs)}", file=sys.stderr)
            continue
        with ThreadPoolExecutor(args.workers) as pool, out.open("a") as f:
            for rec in pool.map(lambda q: ask(m, q, args.mode, args.k), todo):
                if "error" in rec:
                    print(f"  ! {rec['qid']}: {rec['error'][:160]}", file=sys.stderr)
                    continue  # not cached, so the next run retries it
                f.write(json.dumps(rec) + "\n")
                f.flush()


if __name__ == "__main__":
    main()
