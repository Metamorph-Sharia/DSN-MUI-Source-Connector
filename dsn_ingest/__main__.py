"""CLI — this IS the orchestrator contract (see CONTRACT.md).

  dsn check                              probe the site (one listing page, no downloads)
  dsn discover [--max-pages N]           crawl the listing, print the index
  dsn sync [--all] [--limit N] [--out F] download and extract new or changed docs
  dsn list [--filter LIST]               print every indexed doc with its status
  dsn status                             coverage, last runs, recent errors
  dsn help [verb]                        this overview, or help for one verb

Output conventions:
  check / status        one JSON doc on stdout
  discover / sync / list  JSONL (one record per line) on stdout; sync also
                          appends to --out FILE and prints its run report on stderr.
Exit codes: 0 ok · 1 partial (some docs failed) · 2 run-level failure.
"""
import argparse
import json
import sys

from . import __version__, config
from .connector import DsnConnector
from .store import Store
from .fetcher import Fetcher

VERBS = ["check", "discover", "sync", "list", "status"]

EPILOG = """\
verbs:
  check      probe the site: one listing page, parse sanity, no downloads
  discover   crawl all listing pages and print the index as JSONL
  sync       download and extract documents; prints only new/changed/failed
  list       print every indexed document with its status (full snapshot)
  status     JSON coverage report: remote vs local, errors, recent runs
  help       show this overview, or help for one verb: dsn help sync

output:
  stdout     machine-readable only (JSON or JSONL), safe to pipe
  stderr     progress and the sync run report

exit codes:
  0  ok        1  partial (some documents failed, or the listing was gated)
  2  run-level failure

examples:
  dsn sync --limit 3                 smoke-test a run
  dsn sync --out corpus/dsn.jsonl    append the delta to a corpus file
  dsn sync --refresh-index           re-crawl the listing now
  dsn list --filter failed,missing   see what still needs attention
"""


def build(store: Store) -> DsnConnector:
    return DsnConnector(Fetcher(), store)


def _emit_jsonl(records, out_path: str | None) -> None:
    for r in records:
        line = json.dumps(r.to_dict() if hasattr(r, "to_dict") else r, ensure_ascii=False)
        print(line, flush=True)
        if out_path:
            with open(out_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")


def _progress_printer(verbose: bool):
    """Per-document progress on stderr. Failures print in every mode; successes
    only when verbose (stderr is a terminal, or --progress)."""
    def emit(event: str, info: dict) -> None:
        if event == "start":
            if verbose:
                print(f"index: {info['index_source']} · {info['discovered']} listed · "
                      f"{info['todo']} to acquire", file=sys.stderr)
            return
        rec, i, n = info["rec"], info["i"], info["n"]
        if rec.status == "failed":
            print(f"[{i}/{n}] FAIL {rec.source_id}  {rec.error}: "
                  f"{(rec.error_message or '')[:120]}", file=sys.stderr)
        elif verbose:
            size = f"{rec.size_bytes / 1e6:.1f} MB" if rec.size_bytes else "-"
            ocr = "  needs_ocr" if rec.needs_ocr else ""
            print(f"[{i}/{n}] {rec.status:<9} {rec.source_id}  {size}{ocr}", file=sys.stderr)
    return emit


def _parser():
    """Returns (top-level parser, subparsers action) so help can reach each verb."""
    p = argparse.ArgumentParser(
        prog="dsn",
        description="DSN-MUI fatwa source connector: retrieves the corpus from "
                    "dsnmui.or.id as raw PDFs and extracted text.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"dsn {__version__}")
    sub = p.add_subparsers(dest="cmd", metavar="VERB", required=True)

    sub.add_parser("check", help="probe the site (one listing page, no downloads)",
                   description="Fetch the first listing page and confirm it parses. "
                               "Exit 0 if rows were found, 2 otherwise.")

    d = sub.add_parser("discover", help="crawl the listing and print the index as JSONL",
                       description="Crawl every listing page and refresh the stored index. "
                                   "Makes live requests to dsnmui.or.id.")
    d.add_argument("--max-pages", type=int, default=config.DEFAULT_MAX_PAGES)

    s = sub.add_parser("sync", help="download and extract new or changed documents",
                       description="Acquire every indexed document that is missing, failed, "
                                   "or moved, and print the delta (new, changed, failed) "
                                   "as JSONL. Reuses the stored listing for 24 h.")
    s.add_argument("--max-pages", type=int, default=config.DEFAULT_MAX_PAGES)
    s.add_argument("--limit", type=int,
                   help="acquire at most N pending documents (smoke-test a run)")
    s.add_argument("--all", action="store_true",
                   help="re-download everything (byte-level change detection)")
    s.add_argument("--refresh-index", action="store_true",
                   help="re-crawl the listing even if the stored index is fresh")
    s.add_argument("--out", metavar="FILE",
                   help="append the delta JSONL to FILE as well as stdout")
    verbosity = s.add_mutually_exclusive_group()
    verbosity.add_argument("--progress", action="store_true",
                           help="per-document progress on stderr (default on a terminal)")
    verbosity.add_argument("--quiet", action="store_true",
                           help="only failures and the run report on stderr "
                                "(default when piped)")

    l = sub.add_parser("list", help="print every indexed document with its status",
                       description="Full local snapshot as JSONL. Statuses: unchanged "
                                   "(held and current), failed, missing (listed but never "
                                   "acquired). Network-free.")
    l.add_argument("--filter", metavar="LIST",
                   help="comma-separated statuses to keep: unchanged,failed,missing")

    sub.add_parser("status", help="coverage, last runs and recent errors (JSON)",
                   description="Reconcile the remote index with local state. Exit 0 if "
                               "aligned (nothing missing or failed), 1 otherwise. Network-free.")

    h = sub.add_parser("help", help="show this overview, or help for one verb",
                       description="dsn help            overview and exit codes\n"
                                   "dsn help sync       options for one verb")
    h.add_argument("verb", nargs="?", choices=VERBS)
    return p, sub


def main(argv=None) -> int:
    p, sub = _parser()
    args = p.parse_args(argv)

    if args.cmd == "help":
        if args.verb:
            sub.choices[args.verb].print_help()
        else:
            p.print_help()
        return 0

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
            verbose = args.progress or (not args.quiet and sys.stderr.isatty())
            report, delta = conn.sync(max_pages=args.max_pages, limit=args.limit,
                                      all_files=args.all, refresh_index=args.refresh_index,
                                      progress=_progress_printer(verbose),
                                      on_delta=lambda rec: _emit_jsonl([rec], args.out))
            c = report["counts"]
            if verbose:
                print(f"done: {c['new']} new, {c['changed']} changed, {c['failed']} failed, "
                      f"{c['skipped_already_have']} already held", file=sys.stderr)
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
