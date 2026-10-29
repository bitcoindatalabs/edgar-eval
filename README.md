# edgar-eval

**Can a model that fits on one 24GB GPU read a 420-page SEC filing as well as a frontier model?**
On Anthropic's S-1, with the right recipe, nearly: **98% vs 100%**, at about 1/40th the cost.

| Setup (same 47 questions, same scoring) | Accuracy | Quotes real | Cost per run |
|---|---|---|---|
| Frontier: Claude Opus 5.5 reads the full filing (310k tokens) | 100% | 100% | $1.99 |
| Local 27B, naive retrieval (Qwen3.8 27B, ~7k tokens) | 89% | 100% | $0.06 |
| **Local 27B + `filing-factsheet` skill (~8k tokens)** | **98%** | **100%** | **$0.05** |

Naive retrieval missed cost of revenue and every gross-margin question; the fact sheet fixes them.
Leaderboard and answer explorer: **https://bitcoindatalabs.github.io/edgar-eval/**

Presented at AI Startup Rodeo, Austin, 2026-10-30 (*Inside Anthropic's IPO Filing: AI-Powered
Financial Analysis*). Maintained by [Bitcoin Data Labs](https://bitcoindatalabs.org).

## The skills

| Skill | What it does |
|---|---|
| [`filing-factsheet`](skills/filing-factsheet/SKILL.md) | Turns a long EDGAR filing into a compact, page-cited fact sheet: parsed tables, every blank figure (Anthropic's S-1 has 32), the cover page. The model looks numbers up and writes arithmetic as an expression that code evaluates. |
| [`filing-evals`](skills/filing-evals/SKILL.md) | Builds a self-validating gold set (every answer tied to an exact quote), scores with code (pass/fail, invented blanks, whether cited quotes exist), and compares setups. Hands off to [evals-skills](https://github.com/ai-evals-course/evals-skills) for error analysis and judge validation. |

Install in any agent that supports skills (Claude Code, Codex, Cursor, ...):

```bash
npx skills add https://github.com/bitcoindatalabs/edgar-eval
```

or as a Claude Code plugin:

```
/plugin marketplace add bitcoindatalabs/edgar-eval
/plugin install filing-evals@bitcoindatalabs
```

## The benchmark: S-1 Bench

47 questions over Anthropic, PBC's [Form S-1](https://www.sec.gov/Archives/edgar/data/0002130357/000162828026067168/anthropicpbc-sx1.htm)
(filed 2026-10-26, 422 pages, ~232k words), in `bench/questions.yaml`.

| Category | Example | The trap |
|---|---|---|
| **blank** | "What's the IPO price range?" | The preliminary S-1 leaves it blank (`$ and $`). The right answer is *not disclosed*. |
| **period-trap** | "Operating income, Q2 2026?" | +$974M for the quarter vs -$693M for the half. |
| **derived** | "Gross margin, H1 2026?" | 85.4% from cost of revenue (p. F-39); ~45% if you use the compute line. |
| fact / governance / risk | Ticker, LTBT, Texas curtailment, Fluidstack | Straight retrieval, with a citation. |

`validate_bench.py` checks every gold quote exists in the filing, records its page, checks the
number appears in it, and recomputes every derived answer.

### What we learned building it

- **Every model "failed" one question; the gold answer was wrong.** A footnote rounds Q2 stock comp to
  $1.3B; the table says $1,261M. When all models fail the same item, check the gold first.
- **The quote checker rejected real quotes** (table pipes, stitched rows, dropped footnote markers)
  until it was fixed. Validate the evaluator, not just the model.
- **A reasoning model spent its whole token budget thinking** and returned nothing. That was a
  harness setting, not the model; a 3k thinking cap fixed it.
- **Grok cited a sentence that does not exist in the filing** while getting the number right.
  Haiku computed gross margin from the wrong line (45% instead of 85%).

## Reproduce

```bash
pip install -r requirements.txt
cp .env.example .env                       # add OPENROUTER_API_KEY
F=skills/filing-factsheet/scripts; E=skills/filing-evals/scripts

python3 $F/build_pages.py --src "$(python3 -c 'import yaml;print(yaml.safe_load(open("bench/meta.yaml"))["source_url"])')"
python3 $F/build_factsheet.py              # tables per bench/factsheet.yaml -> data/processed/factsheet.md
python3 $E/validate_bench.py               # gold set -> bench/questions.json

python3 $E/run_bench.py --mode full  --models anthropic/claude-opus-5.5
python3 $E/run_bench.py --mode rag   --models qwen/qwen3.8-27b
python3 $E/run_bench.py --mode skill --models qwen/qwen3.8-27b
python3 $E/score.py --show skill           # -> results/summary.json and docs/data/
```

All answers from our runs are in `results/raw/`, so `score.py` reproduces the table without API calls.

## Live segment: Stump the model

- `docs/ask.html` (public): the audience types a question about the S-1 and pays 21 sats over
  Lightning. The question travels in the invoice memo (`S1B|handle|question`), so there is no database.
  The wallet connection goes in the link fragment (`ask.html#nwc=...`), which browsers never send to
  a server. Use a **receive-only** NWC connection. Without one the page runs in demo mode.
- `stage/server.py` (laptop only, bound to 127.0.0.1): shows paid questions live (`NWC_STAGE`,
  read-only), asks Claude, GPT and Grok over the full filing side by side, and flags any cited quote
  that is not in the filing. `python3 stage/server.py --warm`, then open http://127.0.0.1:8800.

## Limitations

- **One filing, 47 questions.** The results show the method works on Anthropic's S-1; they are not
  a general claim about SEC filings. 10-Ks and 10-Qs are next.
- **The "local" model ran through OpenRouter** with the same open weights (Qwen3.8 27B). Its ~8k-token
  context fits a 24GB GPU, but speed and quantization effects on real hardware are not measured here.
- **One run per setup**, temperature 0. Small differences (one or two questions) are within noise.
- **Gold answers were written by one person** and validated against the filing text by code. Text
  questions are scored by keywords; that check has not yet been calibrated against a second annotator.
- The fact-sheet table anchors are configured per filing (`bench/factsheet.yaml`).

## Layout

```
skills/filing-factsheet/   skill + scripts: pages, fact sheet, grep
skills/filing-evals/       skill + scripts: validate, run, score
bench/                     questions.yaml (gold), factsheet.yaml (tables), meta.yaml (document)
models.yaml                models to run          results/   cached answers, summary.json
docs/                      leaderboard + ask page (GitHub Pages)   stage/   live stage view
```

## Cite

See [`CITATION.cff`](CITATION.cff), or use GitHub's "Cite this repository" button.

## Disclaimer

Not affiliated with or endorsed by Anthropic, PBC or the U.S. Securities and Exchange Commission.
Figures are extracted from a preliminary prospectus that is subject to change. Nothing here is
investment advice.

Data: SEC EDGAR (public domain). Code and benchmark: [Apache-2.0](LICENSE).
