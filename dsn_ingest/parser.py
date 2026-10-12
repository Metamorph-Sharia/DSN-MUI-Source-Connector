"""HTML → structured rows. BeautifulSoup only; no network.

The fatwa listing (as of Oct 2026) is one table per page, columns:
  Nomor Fatwa | Tentang | <a drive link>Salinan Fatwa (PDF)</a>
Header row / other tables are skipped implicitly: a row counts if the first
cell matches the fatwa-number pattern. Broken hrefs are kept and flagged at
sync time ("content"), so coverage reports them instead of dropping fatwas.
"""
import re

from bs4 import BeautifulSoup

from .models import FatwaEntry

# "96/DSN-MUI/VI/2015", "162/DSN-MUI/VII/2025", "1/DSN-MUI/IV/2000"
NUMBER_RE = re.compile(r"(\d+[A-Z]?)\s*/\s*DSN[-\s]MUI\s*/\s*([IVX]+)\s*/\s*(\d{4})", re.I)


def source_id_for(number: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", number).strip("-").upper()


def parse_listing(html: str) -> list[FatwaEntry]:
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for tr in soup.select("table tr"):
        tds = tr.select("td")
        if len(tds) < 3:
            continue
        m = NUMBER_RE.search(tds[0].get_text(" ", strip=True))
        if m is None:
            continue
        # prefer a Drive link; if the site's link is broken ("https://") or
        # absent, keep the row anyway so it shows up in coverage as failed,
        # not silently missing from the corpus.
        a = tr.select_one("a[href*='drive.google.com'], a[href*='drive.usercontent']") \
            or tr.select_one("a[href]")
        href = (a["href"] if a is not None and a.get("href") else "")
        number = re.sub(r"\s+", "", m.group(0))
        sid = source_id_for(number)
        if sid in seen:
            continue
        seen.add(sid)
        out.append(FatwaEntry(
            source_id=sid,
            number=number,
            title=tds[1].get_text(" ", strip=True),
            pdf_href=href,
            year=int(m.group(3)),
            month=m.group(2).upper(),
        ))
    return out
