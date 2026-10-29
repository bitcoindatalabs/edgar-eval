---
name: filing-factsheet
description: >
  Turn a long financial filing (S-1, 10-K, 10-Q, prospectus) into a compact, page-cited
  fact sheet so a small or local model (e.g. 27B on a 24GB GPU) can answer questions about it
  at frontier-level accuracy. Use when a document is too long for the model's context, when
  answers need page citations, or when derived metrics (margins, growth) must be exact.
  Do NOT use to evaluate answers; use filing-evals for that.
---

# Filing fact sheet

A 400-page S-1 is ~300k tokens. Frontier models can read it whole; a local model cannot.
Do not make a small model read the filing. Change the job:

1. **Extract tables deterministically**: financial statements become rows (label -> value per period), each with its page.
2. **List every blank**: a preliminary prospectus leaves figures empty (`$ ,`, `$ and $`, `% of`). Find them all, with their sentence and page, so "not disclosed" is a lookup, not a judgment call.
3. **Retrieve passages, not pages**: ~180-word passages for narrative questions (governance, risk).
4. **Arithmetic in code**: the model writes the expression (`(16286 - 2380) / 16286 * 100`); code computes it.

Result on Anthropic's S-1 (47 questions, github.com/bitcoindatalabs/edgar-eval): Qwen3.8 27B,
which fits a 24GB GPU, scores 89% with naive retrieval and 98% with this recipe (~8k tokens of
context), against 100% for a frontier model reading all 310k tokens.

Scripts live in this skill's `scripts/` folder. Run them from the project folder; they read and
write `data/` and `bench/` relative to it. Needs `pip install requests pyyaml`.

## Steps

1. **Pages with labels.** Split the filing into pages and keep the printed page number.
   `python3 <skill>/scripts/build_pages.py --src <EDGAR .htm URL or local file>`
   EDGAR HTML puts each page in a fixed-height div; the printed page number comes first in the
   page text, followed by "Table of Contents". Check a few pages by hand before continuing.

2. **Configure tables.** Search the pages (`python3 <skill>/scripts/grep_filing.py "Cost of revenue"`)
   and write `bench/factsheet.yaml`: a `title`, an optional `cover_starts_with`, and one entry per
   table with an `anchor` regex matching the text just before its first row and its `columns`
   (period labels). Start from `references/factsheet.example.yaml`. Typical set:
   - statement of operations (annual and interim), quarterly results
   - operating expenses by function (this is where **cost of revenue** hides when the income
     statement is presented by nature, e.g. "compute and infrastructure")
   - non-GAAP reconciliations, cash flows, balance sheet

3. **Build and inspect.** `python3 <skill>/scripts/build_factsheet.py`
   Read every table in `factsheet.md`. Check: each row has a value per column; totals foot
   (sum of lines = total line); no column-header residue (rows of years); no dropped rows.
   Fix the anchor or parser and rebuild. Never hand-edit numbers into the fact sheet.

4. **Review the blank list.** Every entry should be a real unfilled figure. Expect dozens in
   a preliminary S-1 (Anthropic's has 32).

5. **Answer with the recipe.** Prompt = fact sheet + top-12 passages + question, with the rules
   in `references/prompt.md` (look up tables first; blanks -> not disclosed; `calc` field for
   arithmetic; quote exactly). Evaluate `calc` with a restricted evaluator, never raw `eval` on
   anything but digits and operators.

6. **Prove it.** Run `filing-evals` against your setup and compare with a frontier model reading
   the full filing. Do not claim parity without that comparison.

## Gotchas

- Footnote markers look like negative numbers: `Net loss (2) $ (1,531)`. Treat a leading `(n)`
  as a footnote only when it makes the row one value too long.
- `—` means zero, not missing.
- Rounded narrative figures ("$1.3 billion") disagree with exact table figures ($1,261M).
  Prefer the table; it is what models and analysts will cite.
- Section prefixes ("Current assets: Cash and ...") end up in labels; strip text before the last colon.
