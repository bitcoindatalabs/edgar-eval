# Question types and schema

Each item in `bench/questions.yaml`:

```yaml
- id: gm-h1-2026                # unique, stable
  category: derived             # fact | period-trap | blank | derived | governance | risk
  question: What was Anthropic's gross margin for the six months ended June 30, 2026?
  kind: number                  # number | not_disclosed | text
  value: 85.4
  unit: percent
  tol: 0.02                     # relative tolerance; default 0.01
  inputs: (16,286 - 2,380) / 16,286   # derived only: recomputed by the validator; for percent
                                      # values give the fraction (no * 100), it scales it
  trap: Using compute and infrastructure ($8,945M) as cost of revenue, giving ~45%.
  quote: "Cost of revenue (1) $ 26 $ 752 $ 2,773 $ 1,019 $ 2,380"
```

```yaml
- id: blank-price-range
  category: blank
  question: What is the expected price range per share for Anthropic's IPO?
  kind: not_disclosed
  quote: "currently estimated that the initial public offering price per share will be between $ and $ ."
```

```yaml
- id: ticker
  category: governance
  question: Under what ticker symbol and on which exchange does Anthropic propose to list?
  kind: text
  must_include: [ANT, Nasdaq]
  accept_alias: {Nasdaq: [NASDAQ Global Select]}
  quote: "under the symbol “ANT.”"
```

## Writing good items

- **Use the filing's units.** If the table says "in thousands", the value is in thousands
  (`unit: USD thousands`). To test unit conversion, make it a `derived` item with `inputs: 804,029 / 1000`.
- **Name the period exactly**: "for the six months ended June 30, 2023", "as of December 31, 2022".

- **Quote the table row, not the narrative.** Narrative rounds ("$1.3 billion"); tables are exact.
- **Period traps** pair a quarter with the half-year or year that has the opposite sign or a very different value.
- **Blank items** come from the fact sheet's blank list. Ask them the way an investor would, without hinting that the figure is missing.
- **Derived items** need a `trap` naming the wrong-but-plausible input (compute vs cost of revenue, GAAP vs adjusted).
- **Text items**: keep `must_include` minimal and add aliases; over-strict keywords create false fails.
