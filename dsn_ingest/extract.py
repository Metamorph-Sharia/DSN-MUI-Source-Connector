"""Normalization: PDF bytes → clean Latin text.

Engine: PyMuPDF. The text layer of these PDFs mixes Indonesian (Latin)
with Arabic-script quotations, PUA glyph junk and BiDi controls. We keep
only the Latin reading matter:

  * NFKC normalization first, so presentation forms become base letters
    (and then get caught by the strip below rather than hiding in a form
    the strip would miss)
  * strip: all Arabic ranges, private-use area, zero-width/BiDi controls
  * tidy: collapsed runs of spaces, no triple blank lines

Arabic content is NOT lost: MinerU OCRs it (below). Before MinerU it lived in data/dsn/ocr/<sha>.ara.txt,
from Tesseract over the same archived PDF (scripts/ocr_arabic.py, retired), which export still reads for a
file MinerU hasn't read.

The needs_ocr decision is computed from page CONTENT only, never markers.

With METAMORPH_PDF_ENGINE=mineru (metamorph-pdf-text), MinerU reads the file instead: its page furniture
removed, its reading checked page by page against the text layer, and the Arabic quotations it OCRs read
instead of the text layer's glyph junk. They go to <sha>.ara.txt beside the Latin text, with how the file was
read in <sha>.extraction.json; Tesseract's data/dsn/ocr/<sha>.ara.txt stays as it is. METAMORPH_PDF_ENGINE=
pymupdf (the default) is the extraction below, unchanged, and a re-extract with it removes both files.

A page's text is a printed line to a line (metamorph_contract.printed_text):
PyMuPDF's own lines split a justified line set a word at a time, and a
ruling's label ("Pertama") from the title beside it. METAMORPH_PDF_LINES=
extracted (or printed_lines=False) keeps PyMuPDF's own lines, as before.
"""
import json
import os
import re
import unicodedata
from dataclasses import asdict, dataclass
from functools import cache
from pathlib import Path

from metamorph_contract import Extraction, ExtractionMethod, printed_text, wants_printed_lines
from metamorph_pdf_text import ExtractPdf, from_environment, text_extraction, text_method, wants_mineru

from . import config

MIN_TEXT_LEN = 200  # below this, the PDF is probably a scan → needs OCR later
PAGE_MARKER = "=== page {} ==="

# Arabic scripts + presentation forms + harakat, PUA, zero-width/BiDi controls
_NONLATIN_RE = re.compile(
    r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF"
    r"\uE000-\uF8FF\u200B-\u200F\u202A-\u202E\u2060\uFEFF]"
)


ARABIC_LETTER = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]")
ARABIC_SHARE = 0.8  # of a line's printed characters: an Arabic quotation's line, not a Latin one quoting a word


@dataclass(frozen=True)
class Extracted:
    """A fatwa's PDF read: its Latin text a page each, and how it was read."""

    pages: list[str]
    method: ExtractionMethod = ExtractionMethod.TEXT_LAYER
    extraction: Extraction | None = None  # None: read with PyMuPDF, as before MinerU
    arabic: list[str] | None = None  # the Arabic quotations MinerU read, a page each


def is_arabic_line(line: str) -> bool:
    """A line of an Arabic quotation: mostly Arabic letters, at most a verse number."""
    printed = re.sub(r"\s", "", line)
    if len(printed) < 4:
        return False
    arabic = len(ARABIC_LETTER.findall(printed))
    digits = sum(character.isdigit() for character in printed)
    return arabic / len(printed) >= ARABIC_SHARE and digits <= 1


def extract_pdf(pdf_path: Path, *, extractor: ExtractPdf | None = None) -> Extracted | None:
    """The PDF read by the engine METAMORPH_PDF_ENGINE names; None if no extractor is available."""
    if extractor is None and not wants_mineru(os.environ):
        pages = pdf_to_pages(pdf_path)
        return None if pages is None else Extracted(pages)
    read = (extractor or _environment_extractor()).run(Path(pdf_path).read_bytes(), role="main")
    lines = [page.splitlines() for page in read.pages]
    return Extracted(
        pages=[_clean("\n".join(line for line in page if not is_arabic_line(line))) for page in lines],
        method=text_method(read),
        extraction=text_extraction(read),
        arabic=["\n".join(line for line in page if is_arabic_line(line)) for page in lines],
    )


@cache
def _environment_extractor() -> ExtractPdf:
    return from_environment(os.environ)


def save(sha: str, extracted: Extracted) -> Path:
    """The Latin text written, and with it what MinerU read: its Arabic and how it read the file. A text read
    with PyMuPDF leaves neither behind, so export reads Tesseract's Arabic again, as before."""
    text = write_text(sha, extracted.pages)
    arabic, record = _arabic_path(text), _record_path(text)
    if extracted.extraction is None:
        arabic.unlink(missing_ok=True)
        record.unlink(missing_ok=True)
        return text
    arabic.write_text(_paged(extracted.arabic or []) + "\n", encoding="utf-8")
    record.write_text(
        json.dumps({"method": str(extracted.method), "extraction": asdict(extracted.extraction)}), encoding="utf-8"
    )
    return text


@dataclass(frozen=True)
class ReadRecord:
    """How a text written by `save` was read, beside it: its method and extraction, and MinerU's Arabic."""

    method: ExtractionMethod
    extraction: Extraction
    arabic: Path


def read_record(text: Path) -> ReadRecord | None:
    record = _record_path(text)
    if not record.is_file():
        return None
    data = json.loads(record.read_text(encoding="utf-8"))
    return ReadRecord(ExtractionMethod(data["method"]), Extraction(**data["extraction"]), _arabic_path(text))


def _record_path(text: Path) -> Path:
    return text.with_name(f"{text.stem}.extraction.json")


def _arabic_path(text: Path) -> Path:
    return text.with_name(f"{text.stem}.ara.txt")


def _paged(pages: list[str]) -> str:
    return "\n\n".join(f"{PAGE_MARKER.format(n)}\n{text}" for n, text in enumerate(pages, 1))


def _clean(raw: str) -> str:
    t = _NONLATIN_RE.sub("", unicodedata.normalize("NFKC", raw))
    t = re.sub(r"[ \t]{2,}", " ", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def pdf_to_pages(pdf_path: Path, *, printed_lines: bool | None = None) -> list[str] | None:
    """Per-page cleaned (Latin-only) text, or None if no extractor is available.

    `printed_lines`: None follows METAMORPH_PDF_LINES (printed lines unless it says "extracted")."""
    if printed_lines is None:
        printed_lines = wants_printed_lines(os.environ)
    try:
        import pymupdf                      # current PyMuPDF ≥1.24 API name
    except ImportError:
        try:
            import fitz as pymupdf          # older installs
        except ImportError:
            return None
    doc = pymupdf.open(str(pdf_path))
    try:
        return [_clean(_page_text(pymupdf, page, printed_lines) or "") for page in doc]
    finally:
        doc.close()


def _page_text(pymupdf, page, printed_lines: bool) -> str:
    text = page.get_text()
    if not printed_lines:
        return text
    return printed_text(text, page.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT))


def page_text(pages: list[str]) -> str:
    """Marker-free concatenation — this is what MIN_TEXT_LEN is measured on."""
    return "\n".join(pages)


def write_text(sha: str, pages: list[str]) -> Path:
    body = _paged(pages)
    p = config.TEXT_DIR / f"{sha}.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body + "\n", encoding="utf-8")
    return p
