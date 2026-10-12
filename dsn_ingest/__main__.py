"""CLI — this IS the orchestrator contract (see CONTRACT.md).

  python -m dsn_ingest check
  python -m dsn_ingest discover [--max-pages N]
  python -m dsn_ingest sync [--all] [--limit N] [--out FILE]
  python -m dsn_ingest list [--filter missing,failed]
  python -m dsn_ingest status

Output conventions:
  check / status        one JSON doc on stdout
  discover / sync / list  JSONL (one record per line); sync also appends
                          to --out FILE and prints its run report on STDERR.
Exit codes: 0 ok · 1 partial (some docs failed) · 2 run-level failure.
"""
import argparse
import json
import sys

from . import config
from .connector import DsnConnector
from .store import Store
from .fetcher import Fetcher


def build(store: Store) -> DsnConnector:
    return DsnConnector(Fetcher(), store)


def _emit_jsonl(records, out_path: str | None) -> None:
    for r in records:
        line = json.dumps(r.to_dict() if hasattr(r, "to_dict") else r, ensure_ascii=False)
        print(line)
        if out_path:
            with open(out_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="dsn")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check")
    d = sub.add_parser("discover"); d.add_argument("--max-pages", type=int, default=config.DEFAULT_MAX_PAGES)
    s = sub.add_parser("sync")
    s.add_argument("--max-pages", type=int, default=config.DEFAULT_MAX_PAGES)
    s.add_argument("--limit", type=int, help="acquire at most N pending docs (smoke-test a run)")
    s.add_argument("--all", action="store_true", help="re-download everything (byte-level change detection)")
    s.add_argument("--out", help="append delta JSONL to this file too")
    l = sub.add_parser("list")
    l.add_argument("--filter", help="comma-separated statuses: new,changed,unchanged,failed,missing")
    sub.add_parser("status")

    args = p.parse_args(argv)
    store = Store()

    if args.cmd == "status":
        doc = {"connector": DsnConnector.name, **store.coverage(),
               "runs": store.recent_runs(5), "recent_errors": store.recent_errors(10)}
        print(json.dumps(doc, indent=2, ensure_ascii=False))
        return 0 if doc["aligned"] else 1

    conn = build(store)
    try:
        if args.cmd == "check":
            rep = conn.check()
            print(json.dumps(rep, indent=2, ensure_ascii=False))
            return 0 if rep["ok"] else 2

        if args.cmd == "discover":
            _emit_jsonl(conn.discover(args.max_pages), None)
            return 0

        if args.cmd == "sync":
            report, delta = conn.sync(max_pages=args.max_pages, limit=args.limit, all_files=args.all)
            _emit_jsonl(delta, args.out)
            print(json.dumps(report), file=sys.stderr)
            return {"ok": 0, "partial": 1, "failed": 2}[report["status"]]

        if args.cmd == "list":
            rows = conn.records()
            if args.filter:
                wanted = {f.strip() for f in args.filter.split(",")}
                rows = [r for r in rows if r.status in wanted]
            _emit_jsonl(rows, None)
            return 0
    finally:
        conn.close()
    return 2


if __name__ == "__main__":
    sys.exit(main())
