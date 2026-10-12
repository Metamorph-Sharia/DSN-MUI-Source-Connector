"""CLI — this IS the orchestrator contract (see CONTRACT.md).

  python -m dsn_ingest check
  python -m dsn_ingest discover [--max-pages N]
  python -m dsn_ingest sync [--all] [--limit N] [--out FILE]
  python -m dsn_ingest list [--filter missing,failed]
  python -m dsn_ingest status

  --mode mock  runs the whole thing on bundled fixtures (no network).

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


def build(mode: str, store: Store) -> DsnConnector:
    if mode == "mock":
        from .fetcher import MockFetcher
        pages = {
            config.page_url(1): (config.MOCK_DIR / "fatwa_list_page1.html").read_text(encoding="utf-8"),
            config.page_url(2): (config.MOCK_DIR / "fatwa_list_page2.html").read_text(encoding="utf-8"),
        }
        pdfs = {p.stem: p.read_bytes()
                for p in (config.MOCK_DIR / "pdfs").glob("*.pdf")}
        return DsnConnector(MockFetcher(pages, pdfs), store)
    from .fetcher import Fetcher
    return DsnConnector(Fetcher(), store)


def _emit_jsonl(records, out_path: str | None) -> None:
    for r in records:
        line = json.dumps(r.to_dict() if hasattr(r, "to_dict") else r, ensure_ascii=False)
        print(line)
        if out_path:
            with open(out_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="dsn_ingest")
    p.add_argument("--mode", choices=["mock", "live"], default="live")
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

    # keep mock runs fully isolated from the real corpus state
    data_dir = config.DATA_DIR / "mock" if args.mode == "mock" else config.DATA_DIR
    config.TEXT_DIR = data_dir / "text"
    store = Store(data_dir / "dsn_fatwa.db", data_dir / "blobs")

    if args.cmd == "status":
        conn = build(args.mode, store)
        doc = {"connector": conn.name, "mode": args.mode, **store.coverage(),
               "runs": store.recent_runs(5), "recent_errors": store.recent_errors(10)}
        print(json.dumps(doc, indent=2, ensure_ascii=False))
        return 0 if doc["aligned"] else 1

    conn = build(args.mode, store)
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
