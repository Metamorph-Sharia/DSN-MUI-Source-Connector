"""Connector contract — the agreement between repos.

We do NOT share code. DSN-MUI-Source-Connector, OJK-Source-Connector etc.
are separate repos; compatibility means: same CLI verbs, same delta
semantics, same error vocabulary, records as pointers.

Verbs (every connector implements):
  check      → liveness probe: can we reach the site and parse one page?
  discover   → remote index as JSONL (what EXISTS out there, no downloads)
  sync       → acquire + extract; emits JSONL delta: only new/changed/failed
  list       → full local corpus snapshot as JSONL (what WE HAVE)
  status     → one JSON doc: coverage, last runs, error breakdown

Alignment check = `discover` (remote) reconciled against `list` (local),
or in one call: `status` prints remote vs local counts directly.

Error classes — the fallback ladder (see fetcher.py) logs these and never
crashes a run. A failed document becomes a delta record with status=failed
and is retried automatically on the next default sync.
"""
from typing import Protocol, runtime_checkable

ERROR_CLASSES = {
    "transient": "network timeout / connection reset / 5xx — retried with "
                 "exponential backoff, then logged",
    "quota":     "rate-limited (429) — Retry-After respected; run slows down",
    "auth":      "server demands sign-in — anonymous fallbacks exhausted "
                 "(incl. Drive resourcekey/consent); needs human/alternate route",
    "not_found": "link rot / file gone / end of paginated listing",
    "content":   "got a response but not the expected document "
                 "(HTML where a PDF should be, empty body)",
    "gate":      "bot interstitial from the website ('One moment, please…') — "
                 "longer waits retried; if persistent, sync continues from stored index",
    "unknown":   "anything unforeseen — still just one failed item, never a crash",
}

STATUS_VALUES = {"new", "changed", "failed"}                      # delta (sync) output
LIST_STATUS_VALUES = STATUS_VALUES | {"unchanged", "missing"}     # snapshot (list) output


@runtime_checkable
class SourceConnector(Protocol):
    """Python-side shape of the contract. Duck-typed: an OJK/BI connector
    in another repo satisfies it without importing anything from here."""

    name: str

    def check(self) -> dict: ...
    def discover(self, max_pages: int) -> list: ...
    def sync(self, max_pages: int = ..., limit: int | None = ...,
             all_files: bool = ...) -> tuple[dict, list]: ...
    def records(self) -> list: ...
    def coverage(self) -> dict: ...
