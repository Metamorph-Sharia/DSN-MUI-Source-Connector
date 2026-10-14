"""Offline interface tests (CONNECTOR_INTERFACE.md: 'Tests run offline against
recorded responses').

Fixtures: tests/fixtures/listing_page1.html (recorded listing shape) and
sample_fatwa.pdf. A FakeFetcher stands in for the network; MemoryStore is
the interface doc's own test double.

Run: python -m pytest tests -q        (repo root on sys.path via `python -m`)
"""
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from source_connector import (AuthFailed, Discovery, ItemFailed,  # noqa: E402
                              SourceUnavailable, StoredFile)
from dsn_ingest import config  # noqa: E402
from dsn_ingest.adapter import DsnMuiConnector  # noqa: E402
from dsn_ingest.fetcher import FetchError  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
PDF_BYTES = (FIX / "sample_fatwa.pdf").read_bytes()


class MemoryStore:
    """Test FileStore from the interface doc."""

    def __init__(self):
        self.files = {}

    def put(self, data, *, media_type, source_url=None):
        sha = hashlib.sha256(data).hexdigest()
        self.files[sha] = data
        return StoredFile(sha256=sha, media_type=media_type, source_url=source_url)


class FakeFetcher:
    """Recorded responses only; mirrors Fetcher's error contract."""

    def get_html(self, url):
        if url == config.page_url(1):
            return (FIX / "listing_page1.html").read_text(encoding="utf-8")
        raise FetchError(f"HTTP 404: {url}", "not_found")        # end of corpus

    def get_file(self, href):
        fid = re.search(r"/file/d/([A-Za-z0-9_-]+)", href).group(1)
        if fid == "FIXEDPDFID001":
            return PDF_BYTES, "application/pdf"
        if fid == "FIXEDNOKEYID2":
            raise FetchError("Drive demands sign-in", "auth")
        raise FetchError("not a usable document link", "content")


def make(fetcher=None):
    return DsnMuiConnector({"delay_s": 0.0, "page_delay_s": 0.0},
                           fetcher=fetcher or FakeFetcher())


def by_id(discovery, source_id):
    return next(i for i in discovery.items if i.source_id == source_id)


# --------------------------------------------------------------------- discover

def test_discover_lists_everything_and_returns_cursor():
    d = make().discover(None)
    assert isinstance(d, Discovery)
    assert [i.source_id for i in d.items] == [
        "162-DSN-MUI-VII-2025", "161-DSN-MUI-VII-2025", "96-DSN-MUI-VI-2015"]
    assert d.cursor == "162"


def test_source_ids_are_stable_across_runs():
    a = make().discover(None)
    b = make().discover(None)
    assert [i.source_id for i in a.items] == [i.source_id for i in b.items]


def test_discover_with_cursor_returns_only_newer():
    d = make().discover("161")
    assert [i.source_id for i in d.items] == ["162-DSN-MUI-VII-2025"]
    assert make().discover("162").items == []          # nothing newer


def test_metadata_is_json_safe_and_carries_the_link():
    item = by_id(make().discover(None), "162-DSN-MUI-VII-2025")
    json.dumps(item.metadata)
    assert item.metadata["pdf_href"].startswith("https://drive.google.com/file/d/FIXEDPDFID001")


def test_gated_listing_raises_source_unavailable():
    class Gated(FakeFetcher):
        def get_html(self, url):
            raise FetchError("still gated", "gate")
    with pytest.raises(SourceUnavailable):
        make(Gated()).discover(None)


# ------------------------------------------------------------------------ fetch

def test_fetch_stores_original_bytes():
    conn, store = make(), MemoryStore()
    item = by_id(conn.discover(None), "162-DSN-MUI-VII-2025")
    fetched = conn.fetch(item, store)
    sha = hashlib.sha256(PDF_BYTES).hexdigest()
    assert [f.sha256 for f in fetched.files] == [sha]
    assert store.files[sha] == PDF_BYTES                       # byte-identical archive
    assert fetched.files[0].media_type == "application/pdf"


def test_fetch_works_in_a_fresh_instance():
    """Rule 4: only `item` + config are used — simulate another process."""
    item = by_id(make().discover(None), "162-DSN-MUI-VII-2025")
    fresh = DsnMuiConnector({"delay_s": 0.0, "page_delay_s": 0.0}, fetcher=FakeFetcher())
    fetched = fresh.fetch(item, MemoryStore())
    assert fetched.files and fetched.files[0].media_type == "application/pdf"


def test_fetch_maps_drive_signin_to_authfailed():
    conn = make()
    item = by_id(conn.discover(None), "96-DSN-MUI-VI-2015")
    with pytest.raises(AuthFailed):
        conn.fetch(item, MemoryStore())


def test_broken_upstream_link_is_item_failed_nonretryable():
    conn = make()
    item = by_id(conn.discover(None), "161-DSN-MUI-VII-2025")
    with pytest.raises(ItemFailed) as ei:
        conn.fetch(item, MemoryStore())
    assert ei.value.retryable is False


# ----------------------------------------------------------------- name/version

def test_class_identity():
    assert DsnMuiConnector.name == "dsn-mui"
    assert DsnMuiConnector.max_concurrency == 1
