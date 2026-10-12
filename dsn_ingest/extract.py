"""Normalization: PDF bytes → plain text.

Engine: PyMuPDF (fitz). Chosen over pypdf after side-by-side tests on real
DSN fatwa PDFs: cleaner handling of their embedded-font encodings and better
Arabic-script extraction.

Cleaning applied per page (see ARCHITECTURE.md §4):
  * NFKC normalization — folds Arabic presentation forms (U+FE8D …) back to
    base letters (2000–2007 PDFs store Arabic as glyph shapes)
  * private-use-area + BiDi-control removal — undecipherable font artifacts;
    the Arabic they hid is recovered downstream by scripts/ocr_arabic.py

Output format: `=== page N ===` markers, matching data/dsn/ocr/<sha>.ara.txt,
so a consumer can build a per-page view joining text layer + OCR'd Arabic.
The needs_ocr decision is computed from page CONTENT only, never markers.
"""
import re
import unicodedata
from pathlib import Path

from . import config

MIN_TEXT_LEN = 200  # below this, the PDF is probably a scan → needs OCR later
PAGE_MARKER = "=== page {} ==="

# PUA (BMP) + zero-width/bidi controls that pollute extracted text
_GARBAGE_RE = re.compile(
    r"[\uE000-\uF8FF\u200B-\u200F\u202A-\u202E\u2060\uFEFF]"
)


def _clean(raw: str) -> str:
    return _GARBAGE_RE.sub("", unicodedata.normalize("NFKC", raw))


def pdf_to_pages(pdf_path: Path) -> list[str] | None:
    """Per-page cleaned text, or None if no extractor is available."""
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
