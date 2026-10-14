"""Offline interface tests (CONNECTOR_INTERFACE.md: 'Tests run offline against
recorded responses').

Fixtures (tests/fixtures): listing_page1.html (fatwa shape, synthetic),
pedoman_page.html and talimat_page.html (recorded from the archive),
sample_fatwa.pdf. FakeFetcher stands in for the network.

Run: python -m pytest tests -q
"""
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from source_connector import (AuthFailed, ItemFailed, SourceUnavailable,  # noqa: E402
                              StoredFile)
from dsn_ingest.adapter import (DsnFatwaConnector, DsnPedomanConnector,  # noqa: E402
                                DsnTalimatConnector)
from dsn_ingest.fetcher import FetchError  # noqa: E402
from dsn_ingest.products import PRODUCTS  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
PDF_BYTES = (FIX / "sample_fatwa.pdf").read_bytes()
PAGES = {
    PRODUCTS["fatwa"].listing_url: (FIX / "listing_page1.html").read_text(encoding="utf-8"),
    PRODUCTS["pedoman"].listing_url: (FIX / "pedoman_page.html").read_text(encoding="utf-8"),
    PRODUCTS["talimat"].listing_url: (FIX / "talimat_page.html").read_text(encoding="utf-8"),
}


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
        if url in PAGES:
            return PAGES[url]
        raise FetchError(f"HTTP 404: {url}", "not_found")

    def get_file(self, href):
        m = re.search(r"/file/d/([A-Za-z0-9_-]+)", href)
        fid = m.group(1) if m else ""
        if fid == "FIXEDPDFID001":
            return PDF_BYTES, "application/pdf"
        if fid == "FIXEDNOKEYID2":
            raise FetchError("Drive demands sign-in", "auth")
        raise FetchError("not a usable document link", "content")


def make(cls, fetcher=None):
    return cls({"delay_s": 0.0, "page_delay_s": 0.0}, fetcher=fetcher or FakeFetcher())


def by_id(discovery, source_id):
    return next(i for i in discovery.items if i.source_id == source_id)


# ------------------------------------------------------------------ fatwa

def test_fatwa_discover_lists_everything_and_returns_cursor():
    d = make(DsnFatwaConnector).discover(None)
    assert [i.source_id for i in d.items] == [
        "162-DSN-MUI-VII-2025", "161-DSN-MUI-VII-2025", "96-DSN-MUI-VI-2015"]
    assert d.cursor == str(2025 * 10000 + 162)


def test_fatwa_source_ids_are_stable_across_runs():
    a = make(DsnFatwaConnector).discover(None)
    b = make(DsnFatwaConnector).discover(None)
    assert [i.source_id for i in a.items] == [i.source_id for i in b.items]


def test_fatwa_cursor_returns_only_newer():
    conn = make(DsnFatwaConnector)
    assert [i.source_id for i in conn.discover(str(2025 * 10000 + 161)).items] == [
        "162-DSN-MUI-VII-2025"]
    assert make(DsnFatwaConnector).discover(str(2025 * 10000 + 162)).items == []


def test_fatwa_metadata_is_json_safe_and_carries_the_link():
    item = by_id(make(DsnFatwaConnector).discover(None), "162-DSN-MUI-VII-2025")
    json.dumps(item.metadata)
    assert item.metadata["pdf_href"].startswith("https://drive.google.com/file/d/FIXEDPDFID001")


def test_gated_listing_raises_source_unavailable():
    class Gated(FakeFetcher):
        def get_html(self, url):
            raise FetchError("still gated", "gate")
    with pytest.raises(SourceUnavailable):
        make(DsnFatwaConnector, Gated()).discover(None)


def test_fetch_stores_original_bytes():
    conn, store = make(DsnFatwaConnector), MemoryStore()
    item = by_id(conn.discover(None), "162-DSN-MUI-VII-2025")
    fetched = conn.fetch(item, store)
    sha = hashlib.sha256(PDF_BYTES).hexdigest()
    assert [f.sha256 for f in fetched.files] == [sha]
    assert store.files[sha] == PDF_BYTES                       # byte-identical archive
    assert fetched.files[0].media_type == "application/pdf"


def test_fetch_works_in_a_fresh_instance():
    """Rule 4: only `item` + config are used, as in another process."""
    item = by_id(make(DsnFatwaConnector).discover(None), "162-DSN-MUI-VII-2025")
    fetched = make(DsnFatwaConnector).fetch(item, MemoryStore())
    assert fetched.files and fetched.files[0].media_type == "application/pdf"


def test_fetch_maps_drive_signin_to_authfailed():
    conn = make(DsnFatwaConnector)
    with pytest.raises(AuthFailed):
        conn.fetch(by_id(conn.discover(None), "96-DSN-MUI-VI-2015"), MemoryStore())


def test_broken_upstream_link_is_item_failed_nonretryable():
    conn = make(DsnFatwaConnector)
    with pytest.raises(ItemFailed) as ei:
        conn.fetch(by_id(conn.discover(None), "161-DSN-MUI-VII-2025"), MemoryStore())
    assert ei.value.retryable is False


# ------------------------------------------------------------- pedoman

def test_pedoman_lists_four_with_prefixed_ids():
    d = make(DsnPedomanConnector).discover(None)
    assert len(d.items) == 4
    assert all(i.source_id.startswith("pedoman-") for i in d.items)
    assert d.items[0].source_id == "pedoman-04-DSN-MUI-IX-2021"
    assert d.cursor == str(2021 * 10000 + 4)


def test_pedoman_needs_no_pagination():
    class CountingFetcher(FakeFetcher):
        calls = []
        def get_html(self, url):
            CountingFetcher.calls.append(url)
            return super().get_html(url)
    make(DsnPedomanConnector, CountingFetcher()).discover(None)
    assert CountingFetcher.calls == [PRODUCTS["pedoman"].listing_url]


# ------------------------------------------------------------- talimat

def test_talimat_lists_eleven_with_prefixed_ids():
    d = make(DsnTalimatConnector).discover(None)
    assert len(d.items) == 11
    assert d.items[0].source_id == "talimat-U-0161-DSN-MUI-II-2026"
    assert d.cursor == str(2026 * 10000 + 161)


def test_talimat_cursor_is_year_aware():
    """U-0161/2026 is newer than U-0727/2023 even though 161 < 727."""
    d = make(DsnTalimatConnector).discover(str(2023 * 10000 + 727))
    ids = [i.source_id for i in d.items]
    assert "talimat-U-0161-DSN-MUI-II-2026" in ids
    assert "talimat-U-0727-DSN-MUI-X-2023" not in ids


def test_talimat_drive_folder_link_is_nonretryable():
    conn = make(DsnTalimatConnector)
    item = by_id(conn.discover(None), "talimat-U-0161-DSN-MUI-II-2026")
    with pytest.raises(ItemFailed) as ei:
        conn.fetch(item, MemoryStore())
    assert ei.value.retryable is False
    assert "folder" in str(ei.value).lower()


# ------------------------------------------------------------ identity

def test_three_distinct_connectors():
    names = [DsnFatwaConnector.name, DsnPedomanConnector.name, DsnTalimatConnector.name]
    assert names == ["dsn-fatwa", "dsn-pedoman", "dsn-talimat"]
    assert len(set(names)) == 3
    assert all(c.max_concurrency == 1 for c in
               (DsnFatwaConnector, DsnPedomanConnector, DsnTalimatConnector))
