---
name: filing-factsheet
description: >
  Turn a long SEC filing (S-1, 10-K, 10-Q, prospectus) into a compact, page-cited fact sheet so
  a small or local model (e.g. 27B on a 24GB GPU) can answer financial questions about it with
  citations. Use when the filing is too long for the model's context, when answers need page
  citations, or when derived figures (margins, growth, unit conversions) must be exact.
  Do NOT use to grade answers; use filing-evals for that.
---

# Filing fact sheet

A 400-page S-1 is ~300k-460k tokens. A frontier model can read it whole; a local model cannot.
Don't make a small model read the filing. Give it what an analyst would pull first, and let code
do everything code can check.

## The recipe

1. **Tables from the HTML, not the text.** Parse `<table>` markup: the filing's own column
   headers ("Six Months Ended June 30, 2026"), its units, negatives in parentheses, the page.
   Pages come from whichever page-break convention the filing agent used; text is never dropped.
2. **Choose tables by standard line items, not by config.** Revenue, cost of revenue, operating
   income, net income, cash, total assets, operating cash flow, ... A table is added while it
   covers a line item for a period no chosen table covers. Nothing in the choice knows the questions.
3. **List every blank.** A preliminary prospectus leaves figures empty (`$ and $`, `% of`, `[•]`).
   List each with its sentence and page, so "not disclosed" is a lookup, not a judgment call.
4. **Short passages for prose.** ~180-word passages, keyword search with Snowball stemming, top 12.
5. **Externalize what code can do** (next section), and retry once when a check fails.

## Answering rules: the model decides, code computes and checks

| Step | The model | Code |
|---|---|---|
| Arithmetic | writes an expression in `calc` (made-up example: `(1250 - 400) / 1250 * 100`) | evaluates it; the result is the answer |
| Unit conversion | puts it in `calc` (`48500 / 1000` for thousands to millions) | evaluates it |
| Period | names the column header it used in `column` | checks the period type and year match the question |
| Inputs | uses numbers from the fact sheet or passages | checks every number in `calc` appears there |
| Quote | copies one sentence or table row | checks it is verbatim in the context |
| Blanks | looks the figure up in the blank list | builds the blank list deterministically |

A failed check gets one retry with the reason; answers that pass cost one call. The full prompt
and output format are in `references/prompt.md`.

## Steps

Run from the project folder (paths are relative to it).
`pip install requests pyyaml beautifulsoup4 lxml snowballstemmer`. SEC rejects downloads without a
contact email: put `SEC_USER_AGENT="Your Org you@example.com"` in `.env`.

1. **Pages.** `python3 <skill>/scripts/build_pages.py --src <EDGAR .htm URL or local file>`
   Splits the filing at its page breaks (fixed-height page boxes, or CSS `page-break-before` /
   `page-break-after`, whichever the filing agent used), keeps each printed page number, and strips
   the running header and footer. Check the summary line: nearly every page should have a label.
2. **Fact sheet.** `python3 <skill>/scripts/build_factsheet.py`
   Prints the tables it chose, how many values were not found on their cited page (must be 0),
   the blank count, and the size. Output: `data/processed/factsheet.md` (what the model reads).
3. **Inspect before you trust it.** Read every table: periods in the headers, units in the
   heading, totals that foot, no rows of years. Read the blank list: each entry is a real unfilled
   figure. If a table you need is missing, fix the generic rule (a line item, a header pattern)
   and rebuild. Never hand-edit numbers into the fact sheet.
4. **Answer** with the rules above: fact sheet + top-12 passages + question.
5. **Prove it** with `filing-evals` against a frontier model reading the whole filing. Before
   you claim the recipe generalizes, run it on a filing you did not build it against.

## Keep it honest

- **Never use a real figure from the filing in a prompt example.** A worked example built from a
  real answer leaks it. Our first version did exactly this (see the repo README).
- **Fix rules, not questions.** When an answer is wrong, change a generic rule and re-run
  everything; don't add a table or keyword because one question needs it.
- **Ablate what you add.** Remove each rule or table you added and re-run. If the score holds,
  the rule was not carrying it.

## Gotchas

- Filing agents paginate differently (Anthropic and Reddit: Workiva; Rubrik and Instacart:
  Donnelley). A splitter that keeps only pieces matching one layout silently loses pages; ours
  once dropped 21 summary pages that way. Join empty pieces to the next page; never discard text.
- `—` means nil, not missing. Negatives come as `(551)`, `$ (1,045)` or `(1,045` + `)` cells.
- Units differ per table (`in thousands` is common). Each table heading carries its unit; a table
  that states none says it inherits the filing's usual unit.
- Long statements continue across pages under "(continued)"; they are merged and cite both pages.
  Only a caption that ends at "(continued)" is a continuation: notes pages carry "(Continued)" in
  their running header above unrelated tables.
- The same label can appear under two sections ("Subscription" under revenue and under cost of
  revenue). Repeated labels get their section name: "Cost of revenue: Subscription".
- Operating metrics (users, ARR, GTV) sit in small tables with few GAAP lines; they are added by
  caption ("key business metrics", "disaggregated by", "revenue by geographic area").
- "% of revenue" tables repeat the line items as percentages; they don't count as amounts.
- When the income statement is presented by nature (e.g. "compute and infrastructure"), cost of
  revenue lives in a note ("expenses grouped by function"). It is picked up because cost of revenue
  is a standard line item, not because it is configured.
- Rounded narrative ("$1.3 billion") disagrees with exact table figures ($1,261M). Prefer the table.
- Pages carry a running header ("Table of Contents") and a page number. `build_pages.py` strips
  both, so a sentence that crosses a page break reads straight through and its quote matches.
- A preliminary IPO S-1 has no inline XBRL, so the HTML tables are the structured source.
  For 10-Ks and 10-Qs, XBRL is worth using.

## Result, and its limits

Built on Anthropic's S-1 (47 questions) and two dev filings (Reddit, Rubrik), then frozen
(`filings/FREEZE.txt`) and tested on two S-1s it was not built on (Klaviyo, Instacart; 44 questions
written afterwards by agents that never saw the pipeline). Qwen3.8 27B, DeepSeek V4.1 Flash and
GLM-5.3 answered every question with the skill on all four filings; naive retrieval scored 40-41 of
the 44 held-out questions. The held-out questions were easier than Anthropic's, and it is four S-1s,
not every filing type. Details: github.com/bitcoindatalabs/edgar-eval.
