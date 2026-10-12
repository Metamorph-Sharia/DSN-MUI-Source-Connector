"""Normalization: PDF bytes → plain text. pypdf is optional.

DSN fatwas are born-digital PDFs (text layer present), so plain text
extraction usually suffices — no OCR needed. If a scanned PDF shows up,
extract_text() returns a very short string and the caller can flag it.
"""
from pathlib import Path

from . import config

MIN_TEXT_LEN = 200  # below this, the PDF is probably a scan → needs OCR later


def pdf_to_text(pdf_path: Path) -> str | None:
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    reader = PdfReader(str(pdf_path))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def write_text(sha: str, text: str) -> Path:
    p = config.TEXT_DIR / f"{sha}.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p
