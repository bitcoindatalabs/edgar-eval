"""Stage view: a local-only server for the live "Stump the model" segment.

  python3 stage/server.py                       # http://127.0.0.1:8800
  python3 stage/server.py --models anthropic/claude-opus-5.5 x-ai/grok-4.7

Binds to 127.0.0.1 only: it holds NWC_STAGE (read-only wallet connection) and spends
OpenRouter credit, so it must never be reachable from the venue network.
"""
import argparse
import os
import json
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/filing-evals/scripts"))
os.chdir(ROOT)  # the eval scripts read project files relative to the working directory
import run_bench  # noqa: E402
import score  # noqa: E402

run_bench.load_env()

DEFAULT_MODELS = ["anthropic/claude-opus-5.5", "openai/gpt-6.1-sol", "x-ai/grok-4.7"]
LABELS = {m["id"]: m["label"] for m in run_bench.MODELS}
JOBS: dict[str, dict] = {}
POOL = ThreadPoolExecutor(8)


def ask_one(job: dict, model: str, mode: str) -> None:
    slot = job["results"][model]
    q = {"id": "live", "question": job["question"], "kind": "text"}
    try:
        rec = run_bench.ask({"id": model, "provider": "openrouter"}, q, mode, 8)
        if "error" in rec:
            raise RuntimeError(rec["error"])
        a = rec.get("parsed") or {}
        slot.update(status="done", answer=a, grounded=score.grounded(a.get("quote")),
                    latency_s=rec.get("latency_s"), cost_usd=rec.get("cost_usd"))
    except Exception as e:  # shown on screen; the segment goes on with the other models
        slot.update(status="error", error=str(e)[:300])


def start_job(question: str, models: list[str], mode: str) -> str:
    jid = uuid.uuid4().hex[:8]
    JOBS[jid] = {"question": question, "mode": mode, "started": time.time(),
                 "results": {m: {"label": LABELS.get(m, m), "status": "running"} for m in models}}
    for m in models:
        POOL.submit(ask_one, JOBS[jid], m, mode)
    return jid


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body, ctype="application/json") -> None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a) -> None:
        pass

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            return self._send(200, (ROOT / "stage/index.html").read_bytes(), "text/html; charset=utf-8")
        if self.path == "/api/config":
            return self._send(200, {"nwc": os.environ.get("NWC_STAGE", ""), "mode": self.server.mode,
                                    "models": [{"id": m, "label": LABELS.get(m, m)} for m in self.server.models]})
        if self.path.startswith("/api/job/"):
            job = JOBS.get(self.path.rsplit("/", 1)[-1])
            return self._send(200, job) if job else self._send(404, {"error": "no such job"})
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/api/ask":
            return self._send(404, {"error": "not found"})
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        question = (body.get("question") or "").strip()[:500]
        if len(question) < 8:
            return self._send(400, {"error": "question too short"})
        self._send(200, {"job": start_job(question, self.server.models, self.server.mode)})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8800)
    ap.add_argument("--models", nargs="*", default=DEFAULT_MODELS)
    ap.add_argument("--mode", choices=["full", "rag"], default="full",
                    help="full = whole filing (accurate, ~$0.1-1.5/model/question); rag = cheap")
    ap.add_argument("--warm", action="store_true", help="prime prompt caches with one throwaway question")
    args = ap.parse_args()
    if not os.environ.get("OPENROUTER_API_KEY"):
        sys.exit("OPENROUTER_API_KEY missing from .env")
    if not os.environ.get("NWC_STAGE"):
        print("NWC_STAGE not set: wallet feed disabled, type questions in manually.", file=sys.stderr)
    if args.warm:
        jid = start_job("What ticker symbol does Anthropic propose to use?", args.models, args.mode)
        print("warming caches…", file=sys.stderr)
        while any(r["status"] == "running" for r in JOBS[jid]["results"].values()):
            time.sleep(1)
        for r in JOBS[jid]["results"].values():
            print(f"  {r['label']}: {r['status']} ${r.get('cost_usd') or 0:.3f}", file=sys.stderr)
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    srv.models, srv.mode = args.models, args.mode
    print(f"Stage view: http://127.0.0.1:{args.port}  models={', '.join(args.models)}  mode={args.mode}",
          file=sys.stderr)
    srv.serve_forever()


if __name__ == "__main__":
    main()
