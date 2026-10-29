# edgar-eval

**Can a local open model match a frontier model on SEC filing analysis? On this task, with the right
skills and scaffolding, yes.**
By Saurabh (Soru) Kumar, [Bitcoin Data Labs](https://bitcoindatalabs.org).

A frontier model can read a 400-page filing whole. A 27B open model that fits on one GPU can't, so
we don't ask it to. We change the workflow around it instead:

- **Code prepares the context.** It parses the filing's financial tables, with page citations, and
  lists every figure the filing leaves blank. The model gets ~13k tokens, not the filing's 300k-460k.
- **The model does what models are good at.** It finds the figure and decides what to compute.
- **Code does the rest.** It runs the arithmetic, checks every input and quote against the filing,
  checks the period, and sends a failed answer back once with the reason.

The result: on answering questions about S-1 prospectuses with citations, an open 27B model is at
parity with frontier models on all three filings we scored. It matched Claude Opus 5.5's 47/47 on
Anthropic's S-1 at about 1/50th of the cost, and scored 22/22 on each of two held-out S-1s it was
never tuned on. That is the maximum, so no model could do better. Two other open models did the same.

| Questions answered correctly | Anthropic S-1 (built on) | Klaviyo S-1 (held out) | Instacart S-1 (held out) |
|---|---|---|---|
| Frontier model reading the whole filing (Claude Opus 5.5) | 47/47 · $1.99 | at most 22/22 (not run) | at most 22/22 (not run) |
| Open 27B model, naive retrieval (Qwen3.8 27B) | 42/47 | 22/22 | 19/22 |
| **Open 27B model + `filing-factsheet` skill** | **47/47 · $0.04** | **22/22** | **22/22** |

DeepSeek V4.1 Flash and GLM-5.3 with the skill also answered every question on all three filings.
"Held out" means the skill was frozen before anyone wrote those questions, and nothing changed
afterwards ([how](#held-out-test)). Cost is the API price of one full question set. "Local" means
open weights small enough for one GPU; our runs used the same weights through an API
([limitations](#limitations)). Parity is for this task: factual, cited answers about filings, not
open-ended analysis or judgment.

Leaderboard and answer explorer (Anthropic's S-1): **https://bitcoindatalabs.github.io/edgar-eval/**
Presented at AI Startup Rodeo, Austin, 2026-10-30 (*Inside Anthropic's IPO Filing: AI-Powered
Financial Analysis*).

## The skills

| Skill | What it does |
|---|---|
| [`filing-factsheet`](skills/filing-factsheet/SKILL.md) | Turns an EDGAR filing into a compact, page-cited fact sheet: financial tables parsed from the HTML and chosen by standard line items (no per-filing config), every figure the filing leaves blank, the cover page. The model looks numbers up; code does the arithmetic and checks each answer. |
| [`filing-evals`](skills/filing-evals/SKILL.md) | Builds a self-validating gold set (every answer tied to an exact quote), scores with code (pass/fail, invented blanks, whether cited quotes exist), compares setups, and guards against circularity with ablations and held-out filings. Hands off to [evals-skills](https://github.com/ai-evals-course/evals-skills) for error analysis and judge validation. |

Install in any agent that supports skills (Claude Code, Codex, Cursor, ...):

```bash
npx skills add https://github.com/bitcoindatalabs/edgar-eval
```

or as a Claude Code plugin:

```
/plugin marketplace add bitcoindatalabs/edgar-eval
/plugin install filing-evals@bitcoindatalabs
```

## How the skill works

A preliminary S-1 is 300k-460k tokens, depending on the tokenizer. The skill gives the model about
13k instead: the fact sheet plus 12 short passages found by keyword search. Then it splits the work.

| Step | The model | Code |
|---|---|---|
| Find the number | looks it up in a parsed table and names the column it used | parses the tables from HTML, with their own headers, units and pages |
| Arithmetic and unit conversion | writes an expression (`(1250 - 400) / 1250 * 100`) | evaluates it; the result is the answer |
| Period | names the column header it used | checks the period type and year match the question |
| Inputs and quote | uses numbers and words from the context | checks each number in the expression, and the quote, appear verbatim |
| Blanks | looks the figure up in the blank list | lists every unfilled figure ("between $ and $") deterministically |

A failed check gets one retry with the reason. The rules are in
[`references/prompt.md`](skills/filing-factsheet/references/prompt.md); its examples use made-up numbers.

## The benchmark

**S-1 Bench:** 47 questions over Anthropic, PBC's
[Form S-1](https://www.sec.gov/Archives/edgar/data/0002130357/000162828026067168/anthropicpbc-sx1.htm)
(filed 2026-10-26, 422 pages), in `bench/questions.yaml`.

| Category | Example | The trap |
|---|---|---|
| **blank** | "What's the IPO price range?" | The preliminary S-1 leaves it blank (`$ and $`). The right answer is *not disclosed*. |
| **period-trap** | "Operating income, Q2 2026?" | +$974M for the quarter vs -$693M for the half. |
| **derived** | "Gross margin, H1 2026?" | 85.4% from cost of revenue (p. F-39); ~45% if you use the compute line. |
| fact / governance / risk | Ticker, LTBT, Texas curtailment, Fluidstack | Straight retrieval, with a citation. |

`validate_bench.py` checks every gold quote exists in the filing, records its page, checks the
number appears in it, and recomputes every derived answer.

Frontier models reading the whole filing: Claude Opus 5.5, Claude Sonnet 5.5, GPT-6.1 Sol and
Grok 4.7 scored 47/47 (Sonnet for $1.00); Claude Haiku 5.5 scored 44/47.

## Held-out test

A recipe built while looking at one filing's questions will fit them. So the skill was built on
Anthropic's S-1 and two dev filings, then **frozen**: the sha256 of every script and the prompt is in
[`filings/FREEZE.txt`](filings/FREEZE.txt). Only then were questions written for two other S-1s, by
agents that could read the filing but not the pipeline, its prompt, fact sheets or results. The
freeze was recorded at 2026-10-29 21:16 UTC, before the questions were written; `FREEZE.txt`, the
questions and the results were committed together afterwards, so git history alone does not prove
the order.

| Filing (S-1) | Role | Questions | Naive retrieval (Qwen / DeepSeek / GLM) | Skill (all three) |
|---|---|---|---|---|
| Anthropic, PBC (2026) | built on | 47 | 42 / 41 / 42 | 47 |
| Reddit, Inc. (2024) | dev | 10 | 8 / 8 / 7 | 10 |
| Klaviyo, Inc. (2023) | **held out** | 22 | 22 / 21 / 22 | 22 |
| Maplebear (Instacart, 2023) | **held out** | 22 | 19 / 19 / 19 | 22 |

Dev filings are the ones we looked at and fixed code against; held-out filings were not opened until
the code was frozen. Rubrik (2024) was a second dev filing, used only to build the table parser for
a second filing agent's HTML; it has no questions. The two held-out filings come from different
filing agents (Workiva and Donnelley), so the frozen parser was tested on both layouts.
[`filings/README.md`](filings/README.md) has the details.

On the held-out filings the one-retry rule mattered: 4 skill answers first came back malformed and
were fixed on retry. The held-out questions were easier than Anthropic's (naive retrieval scored
91-93%), so they show the skill transfers without breaking, more than they show a large margin.

## How the setups failed

Across the four filings and three open models, naive retrieval (keyword search over pages) missed
33 answers. The skill missed none.

| What happened | Count | Example |
|---|---|---|
| Naive retrieval said the answer was not in the filing, when it was | 27 | Instacart's Q2 2023 revenue; Anthropic's cost of revenue, which sits in a note |
| Naive retrieval gave a slightly wrong number | 3 | Reddit's revenue growth as 21% (it is 20.6%), rounded in its head |
| Naive retrieval returned malformed output | 2 | |
| Correct, but scored wrong by the keyword check (left as scored) | 1 | "does not carry any economic rights" vs the keyword "no economic rights" |

No setup invented a figure the filing leaves blank. The most misleading answers came from a fast
model reading the whole filing: Claude Haiku 5.5 computed Anthropic's gross margin from the wrong
line (45% instead of 85.4%) and cited a real table row, so the answer looks right. Opus, Sonnet,
GPT-6.1 Sol and Grok made no such errors with the full filing.

## What we learned building it

- **Our own prompt leaked an answer.** The first skill prompt's worked `calc` example used the real
  H1 2026 revenue and cost of revenue, the inputs to a test question. With made-up numbers the score
  held (47/47), and it held again with the metric definitions removed from the prompt. v1's answers
  (46/47) are in `results/archive/`.
- **Our page splitter silently dropped 21 pages** (the prospectus summary), because it kept only page
  boxes whose style matched one pattern. Every mode, frontier included, read the filing without them.
  The splitter now handles each filing agent's page breaks and never discards text.
- **The quote checker flagged a real Grok quote.** The sentence runs across a page break, and the
  running header ("64 Table of Contents") sat in the middle of it. Fixed; Grok's quotes are 100% real.
- **Every model "failed" one question; the gold answer was wrong.** A footnote rounds Q2 stock comp to
  $1.3B; the table says $1,261M. When all models fail the same item, check the gold first.
- **A reasoning model spent its whole token budget thinking** and returned nothing. That was a
  harness setting, not the model; a 3k thinking cap fixed it.

## Reproduce

```bash
pip install -r requirements.txt
cp .env.example .env     # add OPENROUTER_API_KEY, and SEC_USER_AGENT with a contact email (SEC requires it)
F=skills/filing-factsheet/scripts; E=skills/filing-evals/scripts

python3 $F/build_pages.py --src "$(python3 -c 'import yaml;print(yaml.safe_load(open("bench/meta.yaml"))["source_url"])')"
python3 $F/build_factsheet.py              # HTML tables + blanks -> data/processed/factsheet.md
python3 $E/validate_bench.py               # gold set -> bench/questions.json

python3 $E/run_bench.py --mode rag   --models qwen/qwen3.8-27b
python3 $E/run_bench.py --mode skill --models qwen/qwen3.8-27b
python3 $E/run_bench.py --mode full  --models anthropic/claude-opus-5.5   # frontier ceiling, ~$2
python3 $E/score.py --show skill           # -> results/summary.json and docs/data/
```

For another filing, run the same commands from its folder, e.g. `cd filings/klaviyo` with
`F=../../skills/filing-factsheet/scripts; E=../../skills/filing-evals/scripts`. Every answer from our
runs is in each `results/raw/`, so `score.py` reproduces the tables without API calls.

## Live segment: Stump the model

- `docs/ask.html` (public): the audience types a question about the S-1 and pays 21 sats over
  Lightning. The question travels in the invoice memo (`S1B|handle|question`), so there is no database.
  The wallet connection goes in the link fragment (`ask.html#nwc=...`), which browsers never send to
  a server. Use a **receive-only** NWC connection. Without one the page runs in demo mode.
- `stage/server.py` (laptop only, bound to 127.0.0.1): shows paid questions live (`NWC_STAGE`,
  read-only), asks Claude, GPT and Grok over the full filing side by side, and flags any cited quote
  that is not in the filing. `python3 stage/server.py --warm`, then open http://127.0.0.1:8800.

## Limitations

- **Four S-1s, two held out, 44 held-out questions.** That shows the recipe transfers to other S-1s,
  not that it works on every SEC filing. 10-Ks and 10-Qs are next.
- **Held-out questions were written by AI agents** following our category mix, not by an outside
  analyst, and they turned out easier than Anthropic's. A harder, human-written set is the next test.
- **The open models ran through OpenRouter.** A 27B model with a ~13k-token prompt should fit a 24GB
  GPU at 4-bit, but speed and quantization effects on real hardware are not measured here.
- **One run per setup**, temperature 0. Differences of one or two questions are within noise.
- **Gold answers are validated by code but written by one author per filing**, and text questions
  are scored by keywords, which has known false negatives (above).
- **Frontier models were not run on the held-out filings.** The skill's 22/22 is the maximum, so they
  cannot score higher; parity there holds whatever they would score. The held-out questions were
  easier than Anthropic's, so they leave little room between setups.

## Layout

```
skills/filing-factsheet/   skill + scripts: pages, fact sheet, grep
skills/filing-evals/       skill + scripts: validate, run, score
bench/                     Anthropic S-1: questions.yaml (gold), meta.yaml (document)
filings/<company>/         dev and held-out S-1s, each with bench/, data/processed/, results/
filings/FREEZE.txt         hashes of the frozen pipeline used for the held-out test
models.yaml                models to run          results/   cached answers, summary.json
docs/                      leaderboard + ask page (GitHub Pages)   stage/   live stage view
```

## Author and citation

edgar-eval is a research project of [Bitcoin Data Labs](https://bitcoindatalabs.org), led by
Saurabh (Soru) Kumar ([ORCID 0009-0004-7944-3492](https://orcid.org/0009-0004-7944-3492)).
To cite it, use [`CITATION.cff`](CITATION.cff) or GitHub's "Cite this repository" button.

## Disclaimer

Not affiliated with or endorsed by Anthropic, PBC, the other companies named here, or the U.S.
Securities and Exchange Commission. Figures are extracted from preliminary prospectuses that are
subject to change. Nothing here is investment advice.

Data: SEC EDGAR (public domain). Code and benchmark: [Apache-2.0](LICENSE).
