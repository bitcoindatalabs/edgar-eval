"""Search filing pages: python3 grep_filing.py "regex" [context_chars] [max_hits]"""
import json
import re
import sys
from pathlib import Path

PAGES = Path("data/processed/pages.jsonl")  # run from the project folder

pattern = re.compile(sys.argv[1], re.I)
ctx = int(sys.argv[2]) if len(sys.argv) > 2 else 250
limit = int(sys.argv[3]) if len(sys.argv) > 3 else 5
hits = 0
for line in PAGES.open():
    p = json.loads(line)
    for m in pattern.finditer(p["text"]):
        label = p["page"] or f"#{p['idx']}"
        print(f"[p.{label}] …{p['text'][max(0, m.start() - ctx):m.end() + ctx]}…\n")
        hits += 1
        if hits >= limit:
            sys.exit()
