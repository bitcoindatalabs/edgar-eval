---
name: filing-evals
description: >
  Build a self-validating eval set for questions over a long financial document (S-1, 10-K,
  prospectus, contract) and use it to compare models or pipelines: quote-anchored gold answers,
  traps for blank and derived figures, code-based pass/fail scoring, and a check that every quote
  a model cites really exists in the document. Use to decide whether a cheaper or local setup is
  good enough, or to audit an AI analyst. For open-ended failure discovery and LLM-judge
  validation, hand off to the evals-skills pack (error-discovery, validate-evaluator).
---

# Filing evals

The question is never "is the model good?" but "is *this setup* good enough for *this document*,
and how would we know?" Answer it with a small gold set you can trust and checks written in code.

Scripts live in this skill's `scripts/` folder; run them from the project folder. Project layout:
`data/processed/pages.jsonl` (from filing-factsheet), `bench/questions.yaml`, `bench/meta.yaml`
(`document:` names the filing in every prompt), `models.yaml` (OpenRouter ids), `.env` with
`OPENROUTER_API_KEY`. One folder per filing: run the same scripts from each (see `filings/` in the
repo). Needs `pip install requests pyyaml snowballstemmer`.

## 1. Write gold questions from the document, not from memory

Every item needs an **exact quote** from the document. Store them in `bench/questions.yaml`
(schema and examples: `references/question-types.md`). Cover these types:

| Type | Why it exists | Example failure seen |
|---|---|---|
| fact | baseline extraction | none at frontier; retrieval misses for small models |
| period-trap | quarter vs half vs year | -$693M (H1) reported for Q2's +$974M |
| blank | figure left empty in a preliminary filing | inventing a price range |
| derived | needs two numbers + arithmetic | gross margin 45% (compute line) instead of 85% (cost of revenue) |
| governance / risk | narrative facts | missing the passage entirely |

Aim for 40-60 items. Prefer questions an analyst would actually ask over trivia.

Write the questions without looking at the pipeline you will test, or they will fit it.

## 2. Validate the gold set before running anything

`python3 <skill>/scripts/validate_bench.py` must pass with zero errors. It checks that each quote exists
verbatim, records its page, checks the gold number appears in the quote, recomputes derived answers
from their `inputs`, and confirms blank items really contain an empty slot.

## 3. Score with code, binary pass/fail

- number: within tolerance (default 1%; set `tol` per item and say why)
- not_disclosed: pass only if the model says not disclosed and gives no number
- text: required keywords present, with aliases
- **grounded**: every fragment of the cited quote (split on line breaks, `;`, `...`) appears in
  the document after reducing both to lowercase letters and digits

Use LLM judges only where code cannot decide, and validate them first (`validate-evaluator`).

## 4. Run and compare setups, not just models

`python3 <skill>/scripts/run_bench.py --mode {closed,rag,full,skill} --models <ids>` then
`python3 <skill>/scripts/score.py --show <mode>`. `full` sends the whole document (frontier ceiling);
`rag` is naive top-8-page retrieval; `skill` uses the filing-factsheet recipe; `closed` gives no
document. Answers are cached per mode and model in `results/raw/`, so re-runs only fill gaps;
move a file to `results/archive/` to re-run it. Reasoning models get a 3k-token thinking cap so an
answer always comes back. In `skill` mode, an answer that fails a code check is retried once and
the record keeps `checks` and `retried` for error analysis. Keep the comparison small: one frontier
ceiling, one naive baseline, one improved pipeline. Report accuracy, invented blanks, grounding, and cost.

## 5. Read the failures before believing the score

Open every miss. In our runs this found: a wrong gold answer (rounded $1.3B vs exact $1,261M,
caught because *every* model "failed"); a grounding checker rejecting real quotes (table pipes,
stitched rows, and a sentence that crosses a page break with the running header in the middle);
and a harness bug (a reasoning model spending its whole token budget thinking).
**When all models fail the same item, suspect the gold. When a checker disagrees with your eyes,
suspect the checker.** Then hand recurring failures to `error-discovery` to build a taxonomy.

## 6. Guard against circularity

A pipeline built while looking at the questions will fit them. Before you report a number:

- **Search every prompt, example and config for gold values.** Our first skill prompt used the
  real revenue and cost-of-revenue figures as its worked `calc` example: the answer to a test
  question, handed to the model. Examples must use made-up numbers.
- **Ablate what you added.** Remove each hand-added rule, table or retrieval tweak and re-run. If
  the score holds, it was not carrying the result; if it drops, say so.
- **Separate in-sample from held-out.** Changes made after seeing results on a filing are tuned
  to it. Keep two kinds of extra filings: **dev** ones you may look at and fix code against, and
  **held-out** ones you do not open until the pipeline is frozen. The protocol we used:
  1. Freeze: record the sha256 of every script and prompt (`shasum -a 256 ... > FREEZE.txt`).
  2. Build the held-out filings' pages with the frozen code; check only that it ran.
  3. Have someone who has not seen the pipeline write the questions (we used agents allowed to read
     only the filing, the schema and the validator).
  4. Run, score, and verify the hashes still match (`shasum -a 256 -c`).
  5. Change nothing afterwards; fixes go into the next version and the next held-out filings.
  Report built-on, dev and held-out scores separately. Cover each document layout you expect (here,
  each filing agent's HTML) with both a dev and a held-out filing.
- **Check that the held-out set is not too easy.** If the naive baseline also scores near 100%, the
  test shows the pipeline does not break, not that it helps.

## 7. Calibrate the thresholds you chose

Tolerances and the grounding rule are decisions, not defaults. Label ~40 answers by hand, measure
how often each check agrees with you on passes and on fails (TPR/TNR), and adjust.
