"""Normalization: PDF bytes → clean Latin text.

Engine: PyMuPDF. The text layer of these PDFs mixes Indonesian (Latin)
with Arabic-script quotations, PUA glyph junk and BiDi controls. We keep
only the Latin reading matter:

  * NFKC normalization first, so presentation forms become base letters
    (and then get caught by the strip below rather than hiding in a form
    the strip would miss)
  * strip: all Arabic ranges, private-use area, zero-width/BiDi controls
  * tidy: collapsed runs of spaces, no triple blank lines

Arabic content is NOT lost — it lives in data/dsn/ocr/<sha>.ara.txt,
produced by scripts/ocr_arabic.py over the same archived PDF (one quality
tier, ~85%, instead of a worse copy baked into the text layer).

The needs_ocr decision is computed from page CONTENT only, never markers.
"""
import re
import unicodedata
from pathlib import Path

from . import config

MIN_TEXT_LEN = 200  # below this, the PDF is probably a scan → needs OCR later
PAGE_MARKER = "=== page {} ==="

# Arabic scripts + presentation forms + harakat, PUA, zero-width/BiDi controls
_NONLATIN_RE = re.compile(
    r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF"
    r"\uE000-\uF8FF\u200B-\u200F\u202A-\u202E\u2060\uFEFF]"
)


def _clean(raw: str) -> str:
    t = _NONLATIN_RE.sub("", unicodedata.normalize("NFKC", raw))
    t = re.sub(r"[ \t]{2,}", " ", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def pdf_to_pages(pdf_path: Path) -> list[str] | None:
    """Per-page cleaned (Latin-only) text, or None if no extractor is available."""
    try:
        import pymupdf                      # current PyMuPDF ≥1.24 API name
    except ImportError:
        try:
            import fitz as pymupdf          # older installs
        except ImportError:
            return None
    doc = pymupdf.open(str(pdf_path))
    try:
        return [_clean(page.get_text() or "") for page in doc]
    finally:
        doc.close()


def page_text(pages: list[str]) -> str:
    """Marker-free concatenation — this is what MIN_TEXT_LEN is measured on."""
    return "\n".join(pages)


def write_text(sha: str, pages: list[str]) -> Path:
    body = "\n\n".join(
        f"{PAGE_MARKER.format(n)}\n{text}" for n, text in enumerate(pages, 1)
    )
    p = config.TEXT_DIR / f"{sha}.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body + "\n", encoding="utf-8")
    return p
