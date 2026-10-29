# Filings beyond Anthropic's S-1

Each folder is a self-contained project for one S-1: `bench/` (gold questions, document name),
`data/processed/` (pages, fact sheet), `results/` (cached answers, summary). The raw filing
(`data/raw/filing.htm`) is not committed; `bench/meta.yaml` has its EDGAR URL. `models.yaml` and
`.env` are symlinks to the repo root's.

| Folder | Company, S-1 filed | Filing agent | Role | Questions |
|---|---|---|---|---|
| `reddit/` | Reddit, Inc., 2024-02-22 | Workiva | dev | 10, written by us while building |
| `rubrik/` | Rubrik, Inc., 2024-04-01 | Donnelley | dev | none; used to build the parser |
| `klaviyo/` | Klaviyo, Inc., 2023-08-25 | Workiva | held out | 22, written after the freeze |
| `instacart/` | Maplebear Inc. (Instacart), 2023-08-25 | Donnelley | held out | 22, written after the freeze |

## Dev vs held out

**Dev** filings are the ones we looked at while building: we read their HTML and fact sheets, found
bugs (Reddit's quarterly columns split in two, Rubrik's repeated row labels, a "% of revenue" table
chosen over the dollar one), fixed the code, and checked the fix on them. Reddit's 10 questions were
written by us and used to test the unit-conversion rule. Scores on dev filings are in-sample.

**Held-out** filings were not opened until the pipeline was frozen. The order was:

1. Freeze: record the sha256 of every script and the answering prompt (`FREEZE.txt`, 2026-10-29
   21:16 UTC). It was committed together with the questions and results, so the order rests on
   that recorded time, not on git history.
2. Build pages for the held-out filings with the frozen code, checking only the page count.
3. Have agents write the questions. They could read the filing's pages, the search tool, the
   question schema and the validator, and nothing else: no skill code, prompt, fact sheet or results.
4. Build the fact sheets with the frozen code, run the models, score. Verify the hashes still match.
5. Change nothing afterwards. Problems found go into the next version, and get tested on new filings.

Which filing played which role was our choice, with one constraint: each filing agent's HTML layout
needed a dev filing (Reddit for Workiva, Rubrik for Donnelley) and a held-out one (Klaviyo,
Instacart), so the frozen parser met both layouts unseen.

## Known issues (for the next version)

- Instacart: three small tables parsed with a single column (no answer was affected).
- Reddit: the user metrics (DAUq) are not picked up as a key-metrics table; answers come from passages.
- The held-out questions were easier than Anthropic's: naive retrieval scored 91-93%.

## Add a filing

```bash
mkdir -p filings/acme/bench && cd filings/acme
ln -s ../../models.yaml models.yaml && ln -s ../../.env .env
printf 'document: "Acme Inc.'"'"'s Form S-1 (preliminary prospectus, filed 2025-01-01)"\nsource_url: <EDGAR .htm URL>\n' > bench/meta.yaml
F=../../skills/filing-factsheet/scripts; E=../../skills/filing-evals/scripts
python3 $F/build_pages.py --src <EDGAR .htm URL>     # needs SEC_USER_AGENT in .env
# write bench/questions.yaml, then:
python3 $E/validate_bench.py && python3 $F/build_factsheet.py
python3 $E/run_bench.py --mode skill --models qwen/qwen3.8-27b && python3 $E/score.py --show skill
```
