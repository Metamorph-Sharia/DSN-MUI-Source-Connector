"""Transport layer with the fallback ladder.

No Playwright anymore: as of Oct 2026 dsnmui.or.id answers plain HTTP
requests cleanly (the old JS interstitial is gone). Everything is
`requests`; a real browser would only add fragility.

Ladder, tried in order per request:
  1. transient network failure (timeout / conn reset / 5xx)
        → retry with exponential backoff (MAX_RETRIES)
  2. 429 rate limit
        → respect Retry-After, then 'quota'
  3. Drive "can't scan for viruses" interstitial (big files)
        → auto-submit the confirm form
  4. Drive "Sign-in" page
        → 'auth'. Anonymous fallbacks exhausted (the resourcekey, which we
          always keep, is the fix for legacy files; if it still asks, a
          human grabs it once). Error is logged, run continues.
  5. 403 / 404
        → 'not_found' (listing end-of-corpus also surfaces as this)
  6. bot interstitial ("One moment, please…") on HTML pages
        → longer waits (20/40/60s); if persistent → 'gate' and the connector
          falls back to its stored index (Drive downloads don't need the site)
  7. anything else
        → 'unknown' — one document failing NEVER crashes the run.

Error classes are declared in base.ERROR_CLASSES so every connector
(DSN / OJK / BI) logs the same vocabulary.
"""
import re
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import requests
from bs4 import BeautifulSoup

from . import config


class FetchError(RuntimeError):
    """kind ∈ ERROR_CLASSES — the orchestrator only ever needs to look at .kind"""

    def __init__(self, message: str, kind: str = "unknown"):
        super().__init__(message)
        self.kind = kind


def drive_download_url(href: str) -> str:
    """file/d/<id>/view?usp=..&resourcekey=.. → drive.usercontent download URL,
    preserving resourcekey (legacy files demand sign-in without it)."""
    m = re.search(r"/file/d/([A-Za-z0-9_-]+)", href)
    qs = parse_qs(urlsplit(href).query)
    fid = m.group(1) if m else qs.get("id", [""])[0]
    if not fid:
        raise FetchError(f"not a Drive file link: {href}", "content")
    params = {"id": fid, "export": "download", "confirm": "t"}
    if "resourcekey" in qs:
        params["resourcekey"] = qs["resourcekey"][0]
    return "https://drive.usercontent.google.com/download?" + urlencode(params)


def _is_signin_page(body: bytes) -> bool:
    head = body[:60_000].lower()
    return (b"google drive: sign-in" in head or b"weblitesignin" in head
            or b"accounts.google.com/v3/signin" in head
            or b"flowentry=service" in head)


def _consent_form(html_text: str) -> tuple[str, dict] | None:
    """Drive virus-scan interstitial: form#download-form with hidden id+uuid.
    Guarded on the uuid field so Google's sign-in form (whose continue= URL
    also contains 'download') is never mistaken for a consent form."""
    soup = BeautifulSoup(html_text, "html.parser")
    for form in soup.select("form[action]"):
        fields = {
            i["name"]: i.get("value", "")
            for i in form.select("input[name]")
            if i.get("type") != "submit"
        }
        if "uuid" in fields and "id" in fields:
            return form["action"], fields
    return None


def _is_gate_page(html: str) -> bool:
    m = re.search(r"<title>(.*?)</title>", html[:4000], re.I | re.S)
    return bool(m) and m.group(1).strip().lower() in config.INTERSTITIAL_TITLES


class Fetcher:
    def __init__(self, delay_s: float = config.DELAY_BETWEEN_FILES_S):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = config.UA
        self.delay_s = delay_s

    # -- internal: one GET, with retry/backoff ladder ------------------
    def _get(self, url: str, timeout: float) -> requests.Response:
        last: FetchError | None = None
        for attempt in range(config.MAX_RETRIES):
            wait = config.BACKOFF_BASE_S * (2 ** attempt)
            try:
                r = self.s.get(url, timeout=timeout, allow_redirects=True)
            except requests.RequestException as ex:
                last = FetchError(f"{type(ex).__name__}: {ex}", "transient")
            else:
                if r.status_code == 429:
                    wait = max(float(r.headers.get("retry-after") or 0), wait)
                    last = FetchError(f"HTTP 429 (quota): {url}", "quota")
                elif r.status_code in (500, 502, 503, 504):
                    last = FetchError(f"HTTP {r.status_code}: {url}", "transient")
                elif r.status_code in (403, 404):
                    raise FetchError(f"HTTP {r.status_code}: {url}", "not_found")
                elif r.ok:
                    return r
                else:
                    raise FetchError(f"HTTP {r.status_code}: {url}", "content")
            if attempt < config.MAX_RETRIES - 1:
                time.sleep(wait)
        raise last or FetchError(f"failed: {url}", "unknown")

    # -- public ---------------------------------------------------------
    def get_html(self, url: str) -> str:
        """HTML page, with bot-interstitial waits on top of the retry ladder."""
        for gate_attempt in range(config.GATE_RETRIES):
            r = self._get(url, config.HTML_TIMEOUT_S)
            if not _is_gate_page(r.text):
                return r.text
            time.sleep(config.GATE_WAIT_S * (gate_attempt + 1))   # 20s, 40s, 60s
        raise FetchError(f"still gated after {config.GATE_RETRIES} waits: {url}", "gate")

    def get_file(self, href: str) -> tuple[bytes, str]:
        """Fetch document bytes. Returns (data, content_type). Raises FetchError."""
        if "drive." in href:
            url = drive_download_url(href)
        elif re.match(r"https?://[^\s/?#]+\.\w{2,}", href):
            url = href
        else:
            raise FetchError(f"not a usable document link: {href!r}", "content")
        r = self._get(url, config.FILE_TIMEOUT_S)
        if "accounts.google.com" in r.url:      # redirected to sign-in
            raise FetchError(f"Drive demands sign-in (auth-blocked), landed on "
                             f"{r.url[:80]}", "auth")
        data, ctype = r.content, r.headers.get("content-type", "").split(";")[0].strip()

        if ctype == "text/html" or data[:15].lstrip()[:5] in (b"<!doc", b"<html"):
            # not the file — walk the HTML fallbacks
            if _is_signin_page(data):
                raise FetchError(
                    f"Drive demands sign-in for {url} (auth-blocked mirror needed "
                    f"or href changed upstream)", "auth")
            form = _consent_form(data.decode("utf-8", "replace"))
            if form:  # virus-scan confirm → resubmit once
                action, fields = form
                r2 = self._get(f"{action}?{urlencode(fields)}", config.FILE_TIMEOUT_S)
                data, ctype = r2.content, r2.headers.get("content-type", "").split(";")[0].strip()
                if ctype == "text/html" or _is_signin_page(data):
                    raise FetchError(f"consent resubmit still not a file: {url}", "content")
            else:
                raise FetchError(f"expected a file, got HTML from {url}", "content")

        if not data:
            raise FetchError(f"empty body from {url}", "content")
        time.sleep(self.delay_s)
        return data, ctype

    def close(self) -> None:
        self.s.close()


class MockFetcher:
    """Offline fixtures: pages keyed by URL, files keyed by Drive file id."""

    def __init__(self, pages: dict[str, str], pdfs: dict[str, bytes]):
        self.pages = pages
        self.pdfs = pdfs

    def get_html(self, url: str) -> str:
        if url in self.pages:
            return self.pages[url]
        raise FetchError(f"no mock page for {url}", "not_found")

    def get_file(self, href: str) -> tuple[bytes, str]:
        m = re.search(r"/file/d/([A-Za-z0-9_-]+)", href)
        fid = m.group(1) if m else parse_qs(urlsplit(href).query).get("id", [""])[0]
        if fid not in self.pdfs:
            raise FetchError(f"no mock file for id {fid}", "not_found")
        return self.pdfs[fid], "application/pdf"

    def close(self) -> None:
        pass
