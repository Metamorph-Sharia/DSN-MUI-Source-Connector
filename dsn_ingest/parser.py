"""HTML → structured rows. BeautifulSoup only; no network.

All three DSN listings share one table layout: [optional "No."] | number |
title | link. The number cell is found by pattern, not by position, so the
extra "No." column in pedoman/ta'limat doesn't matter. Ta'limat numbers carry
a "U-" prefix (U-0161/DSN-MUI/II/2026).
"""
import re

from bs4 import BeautifulSoup

from .models import FatwaEntry
from .products import PRODUCTS

NUMBER_RE = re.compile(
    r"((?:U-)?\d+[A-Z]?)\s*/\s*DSN[-\s]MUI\s*/\s*([IVX]+)\s*/\s*(\d{4})", re.I)


def source_id_for(number: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", number).strip("-").upper()


def parse_listing(html: str, product=None) -> list[FatwaEntry]:
    product = product or PRODUCTS["fatwa"]
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for tr in soup.select("table tr"):
        cells = tr.select("td")
        if len(cells) < 3:
            continue
        idx = next((i for i, c in enumerate(cells)
                    if NUMBER_RE.search(c.get_text(" ", strip=True))), None)
        if idx is None or idx + 1 >= len(cells):
            continue
        m = NUMBER_RE.search(cells[idx].get_text(" ", strip=True))
        # prefer a Drive link; keep rows whose link is broken or absent, so
        # coverage reports them instead of silently dropping them
        a = tr.select_one("a[href*='drive.google.com'], a[href*='drive.usercontent']") \
            or tr.select_one("a[href]")
        href = a["href"] if a is not None and a.get("href") else ""
        number = re.sub(r"\s+", "", m.group(0))
        sid = product.id_prefix + source_id_for(number)
        if sid in seen:
            continue
        seen.add(sid)
        out.append(FatwaEntry(
            source_id=sid,
            number=number,
            title=cells[idx + 1].get_text(" ", strip=True),
            pdf_href=href,
            year=int(m.group(3)),
            month=m.group(2).upper(),
        ))
    return out
