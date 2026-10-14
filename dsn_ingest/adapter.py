"""Orchestrator-facing connector: implements CONNECTOR_INTERFACE.md v1.0.

The CLI (`dsn sync`) and this adapter share the same internals (fetcher
ladder, parser); the adapter just speaks the team's vocabulary.

  our internals                  interface surface
  ---------------------------    --------------------------------------
  FatwaEntry (listing rows)      Discovered(source_id, source_url,
                                 version=pdf href, metadata=row fields)
  Fetcher.get_file ladder        fetch(item, store) -> Fetched
  FetchError(kind=...)           SourceUnavailable / AuthFailed / ItemFailed

Deliberately NOT in the adapter: text extraction (shared downstream step
per interface rules), dedupe/change detection, SQLite state — the
orchestrator owns storage. The CLI keeps those for human/scheduled runs.

Error mapping:
  discover: any FetchError except end-of-listing not_found
            -> SourceUnavailable (listing gated/changed/down: stop, retry later)
  fetch:    transient, content, unknown -> ItemFailed (retryable — next run
            re-fetches; the fetcher already backed off 3x per document)
            quota                      -> SourceUnavailable (global rate limit)
            gate                       -> SourceUnavailable (listing class)
            auth                       -> AuthFailed (a human grabs it once)
            not_found                  -> ItemFailed(retryable=False)
            missing/broken link        -> ItemFailed(retryable=False)

Known limits (documented for the team):
  * cursor = highest fatwa sequence number; the listing is reverse-chron so
    cursor discovery early-stops after 1-2 pages. A revision that KEEPS its
    old number (e.g. "dalam penyempurnaan" fatwas) is invisible to cursor
    discovery — run discover(None) occasionally.
  * version = exact pdf href incl. resourcekey: catches link swaps at the
    source, not silent re-uploads under the same link (sha256 at storage
    time does).
  * rule 5 (identified UA): config.UA names the project. If the site's bot
    gate turns out to react to UAs, revisit — never by defeating the gate.
"""
import logging
import re
import time

try:
    from source_connector import (AuthFailed, ConnectorError, Discovered,
                                  Discovery, Fetched, ItemFailed, SourceConnector,
                                  SourceUnavailable)
except ImportError:      # editable installs may not put repo root on sys.path
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from source_connector import (AuthFailed, ConnectorError, Discovered,
                                  Discovery, Fetched, ItemFailed, SourceConnector,
                                  SourceUnavailable)

from . import config
from .fetcher import FetchError, Fetcher
from .parser import parse_listing

log = logging.getLogger(__name__)

_SEQ_RE = re.compile(r"^(\d+)")


def _seq(number: str) -> int:
    m = _SEQ_RE.match(number)
    return int(m.group(1)) if m else 0


class DsnMuiConnector(SourceConnector):
    name = "dsn-mui"
    version = "0.4.0"
    max_concurrency = 1                      # polite to dsnmui.or.id and Drive

    def __init__(self, config_map=None, *, fetcher=None):
        super().__init__(dict(config_map or {}))
        # `fetcher` is injectable for offline tests only; production derives
        # everything it needs from config (rule 4: fetch must stand alone).
        self.fetcher = fetcher or Fetcher(delay_s=self.config.get("delay_s", 1.0))

    # ------------------------------------------------------------------ discover
    def discover(self, cursor: str | None = None) -> Discovery:
        cursor_seq = int(cursor) if cursor and cursor.isdigit() else 0
        items: list[Discovered] = []
        highest = cursor_seq
        max_pages = self.config.get("max_pages", config.DEFAULT_MAX_PAGES)
        n = 1
        while n <= max_pages:
            if n > 1:
                time.sleep(self.config.get("page_delay_s", 1.0))   # pace listing crawl
            try:
                html = self.fetcher.get_html(config.page_url(n))
            except FetchError as ex:
                if ex.kind == "not_found" and items:
                    break                                          # end of corpus
                raise SourceUnavailable(f"listing page {n}: {ex}") from ex
            rows = parse_listing(html)
            if not rows:
                break
            fresh = 0
            for e in rows:
                seq = _seq(e.number)
                highest = max(highest, seq)
                if seq <= cursor_seq:
                    continue
                fresh += 1
                items.append(Discovered(
                    source_id=e.source_id,
                    source_url=e.pdf_href,                   # the Drive view link
                    version=e.pdf_href,                      # changes when the file link changes
                    metadata=e.to_dict(),                    # incl. pdf_href — fetch needs it
                ))
            if cursor and fresh == 0:
                break                                          # reverse-chron: older pages add nothing
            n += 1
        if not items and not highest:
            raise SourceUnavailable("listing unreadable: no rows parsed (source changed?)")
        log.info("discover: %d items, cursor -> %s", len(items), highest or cursor)
        return Discovery(items=items, cursor=str(highest) if highest else cursor)

    # ------------------------------------------------------------------ fetch
    def fetch(self, item: Discovered, store) -> Fetched:
        href = str(item.metadata.get("pdf_href") or item.source_url or "")
        if not href.startswith("http") or len(href) < 20:
            # the source itself lists a broken link (2 fatwas as of Oct 2026)
            raise ItemFailed(f"{item.source_id}: no usable file link at source ({href!r})",
                             retryable=False)
        try:
            data, ctype = self.fetcher.get_file(href)
        except FetchError as ex:
            raise self._as_interface_error(ex) from ex
        media = "application/pdf" if data[:5] == b"%PDF-" else (ctype or "application/octet-stream")
        stored = store.put(data, media_type=media, source_url=href)   # original bytes, unchanged
        log.info("fetched %s: %d bytes -> %s", item.source_id, len(data), stored.sha256[:12])
        return Fetched(files=[stored], metadata={})

    @staticmethod
    def _as_interface_error(ex: FetchError) -> ConnectorError:
        kind = ex.kind
        if kind == "quota" or kind == "gate":
            return SourceUnavailable(str(ex))
        if kind == "auth":
            return AuthFailed(str(ex))
        if kind == "not_found":
            return ItemFailed(str(ex), retryable=False)
        return ItemFailed(str(ex))            # transient / content / unknown
