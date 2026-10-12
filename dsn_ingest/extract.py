"""Normalization: PDF bytes → plain text.

Engine: PyMuPDF (fitz). Chosen over pypdf after side-by-side tests on real
DSN fatwa PDFs: cleaner handling of their embedded-font encodings (pypdf
produced artifacts like "knlian"/"MAJELIS T]LAMA INI}OFIESIA"; fitz reads
them correctly) and better Arabic-script extraction.

Fatwas are born-digital (text layer present) → no OCR needed. A document
whose extracted text is suspiciously short is flagged `needs_ocr` upstream,
and OCR stays a separate downstream job over the archived blobs.
"""
from pathlib import Path
import unicodedata

from . import config

MIN_TEXT_LEN = 200  # below this, the PDF is probably a scan → needs OCR later


def pdf_to_text(pdf_path: Path) -> str | None:
    try:
        import pymupdf                      # current PyMuPDF ≥1.24 API name
    except ImportError:
        try:
            import fitz as pymupdf          # older installs
        except ImportError:
            return None
    doc = pymupdf.open(str(pdf_path))
    try:
        # NFKC folds Arabic presentation forms (isolated/initial/medial/final glyph
        # shapes, e.g. U+FE8D → U+0627) back to base letters. Older DSN PDFs store
        # their Arabic this way. Private-use glyphs are left as-is: they need the
        # embedded font's own mapping.
        return unicodedata.normalize("NFKC", "\n".join(page.get_text() for page in doc))
    finally:
        doc.close()


def write_text(sha: str, text: str) -> Path:
    p = config.TEXT_DIR / f"{sha}.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p
