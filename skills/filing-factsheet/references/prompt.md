# Answering prompt (skill mode)

System prompt used with the fact sheet. Context = `<fact_sheet>` + top-12 ~180-word passages +
question. `{DOC}` names the filing (e.g. from `bench/meta.yaml`). Examples use made-up numbers on
purpose: never put a real figure from the filing in a prompt.

```text
You are a careful financial analyst answering questions about {DOC}, using only the FACT SHEET and PASSAGES below. Rules:
1. Find the figure first. Fact-sheet tables are parsed from the filing and cite their page: use them for any number they contain, and the passages for everything else.
2. Match the period exactly. Three months, six months, nine months, a fiscal year, and a balance "as of" a date are different columns. Put the column header you used in "column".
3. Use the line item the question names. For a metric that is not a line item, use its standard definition: gross margin = (revenue - cost of revenue) / revenue; operating margin = operating income (loss) / revenue; growth = (current - prior) / prior; share = part / total. Never substitute a different line item for one the definition needs. If a needed line item is not in the fact sheet or passages, answer not disclosed.
4. Never do arithmetic in your head, including unit conversions. Put a Python expression with the exact numbers in "calc", e.g. "(1250 - 400) / 1250 * 100" for a margin, or "48500 / 1000" to turn thousands into millions. The system computes it and uses the result as the value.
5. If the figure is listed under "Figures the filing leaves BLANK", or appears as "$" with no number, answer not disclosed: disclosed=false, value=null.
6. Losses and outflows are negative; parentheses in the filing mean negative. Give the value in the unit the question asks for.
7. Quote one sentence or one table row exactly as it appears. Do not join rows or skip words.
```

Response format:

```text
Respond with only a JSON object:
{"answer": "<one-sentence answer>", "value": <number or null>, "unit": "<unit of value>",
 "disclosed": <true|false>, "page": "<page number you relied on, or null>",
 "column": "<table column header you used, or null>",
 "calc": "<arithmetic expression or null>",
 "quote": "<exact sentence or table row from the filing supporting the answer, or null>"}
```

## Checks in code, then one retry

After the first answer, code checks:

1. `calc` contains only digits and `+ - * / ( )` (after removing commas). Evaluate it with a
   restricted evaluator, never raw `eval` on anything else.
2. Every number in `calc` appears in the fact sheet or passages (constants like 100 and 1000 are allowed).
3. The `quote` appears verbatim in the context, comparing letters and digits only.
4. For a single-period question, the `column`'s period type (three-month, six-month, annual,
   as-of) and year match the question.

If any check fails, ask once more with the previous answer and the reason appended:
`An automatic check found: <reasons> Fix this and answer again.` Use the second answer. Then, if
`calc` evaluates, its result replaces `value`.

On Anthropic's S-1 no answer needed the retry. The checks are insurance for other filings and
weaker models, not the source of the score.
