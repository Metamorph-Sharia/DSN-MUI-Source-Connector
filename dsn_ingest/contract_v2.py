"""A held fatwa → a contract v2 NormalizedDocument. Pure: no store, no files, no network.

Mapping decisions:
  * ID          dsnmui:fatwa:{n}. Fatwa numbers run 1, 2, 3 … across years and never
                repeat, so the year is left out (the contract's convention for such series).
  * citation    "Fatwa DSN-MUI No. 01/DSN-MUI/IV/2000", the number as DSN-MUI prints it.
  * status      unknown: DSN-MUI does not say whether a fatwa was later changed. The
                platform works that out from ta'limat and later fatwas.
  * texts       the PDF's Latin text layer (id) and the Arabic OCR (ar), separately: they
                are different readings of the same file, of different quality.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from metamorph_contract import (
    Confidence,
    DocumentInfo,
    ExtractionMethod,
    FileRef,
    FileRole,
    Kind,
    NormalizedDocument,
    Rights,
    SourceRef,
    Status,
    StatusClaim,
    TextOrigin,
    TextRef,
    canonical_id,
    page_marker,
    split_pages,
    unresolved_id,
)

CONNECTOR = "dsn-fatwa"          # the same name as the interface connector (adapter.DsnFatwaConnector)
ISSUER = "dsnmui"
FAMILY = "fatwa"

_FATWA_NUMBER = re.compile(r"^0*(\d+)([A-Z]?)/DSN-MUI/[IVX]+/\d{4}$", re.I)
_LEGACY_PAGE_MARKER = re.compile(r"^=== page (\d+) ===$", re.MULTILINE)


@dataclass(frozen=True)
class HeldText:
    language: str
    method: ExtractionMethod
    content: str                 # paged with the contract's markers (see paged_text)


@dataclass(frozen=True)
class HeldFatwa:
    """One acquired fatwa, as the store holds it: its listing row, its PDF and the texts read from it."""

    source_id: str
    number: str                  # as printed, e.g. "01/DSN-MUI/IV/2000"
    title: str
    year: int | None
    month: str | None            # roman numeral, as printed
    pdf_href: str
    fetched_at: str
    file_sha256: str
    media_type: str
    texts: tuple[HeldText, ...]


def fatwa_id(number: str) -> str | None:
    match = _FATWA_NUMBER.match(number)
    if not match:
        return None
    sequence, suffix = match.groups()
    return canonical_id(ISSUER, FAMILY, f"{int(sequence)}{suffix}")


def paged_text(content: str) -> str:
    """The store's "=== page N ===" markers → the contract's, keeping each page's number."""
    return _LEGACY_PAGE_MARKER.sub(lambda match: page_marker(int(match[1])), content)


def exported_texts(held: HeldFatwa) -> tuple[HeldText, ...]:
    """An OCR pass over a Latin-only fatwa leaves an empty file: that is not a text."""
    return tuple(text for text in held.texts if text.content.strip())


def normalized_document(held: HeldFatwa, *, connector_version: str) -> NormalizedDocument:
    texts = exported_texts(held)
    return NormalizedDocument(
        source=SourceRef(
            connector=CONNECTOR,
            connector_version=connector_version,
            source_id=held.source_id,
            url=held.pdf_href,
            fetched_at=held.fetched_at,
        ),
        document=_document_info(held),
        files=(
            FileRef(
                sha256=held.file_sha256,
                role=FileRole.MAIN,
                media_type=held.media_type,
                source_url=held.pdf_href,
                rights=Rights.PUBLIC,
                pages=_page_count(texts),
            ),
        ),
        texts=tuple(_text_ref(text, of_file=held.file_sha256) for text in texts),
        source_fields={
            "number": held.number,
            "title": held.title,
            "year": held.year,
            "month": held.month,
            "pdf_href": held.pdf_href,
        },
    )


def _document_info(held: HeldFatwa) -> DocumentInfo:
    resolved = fatwa_id(held.number)
    return DocumentInfo(
        id=resolved or unresolved_id(CONNECTOR, held.source_id),
        id_confidence=Confidence.HIGH if resolved else Confidence.NONE,
        id_evidence=f"listing number {held.number}",
        issuer=ISSUER,
        family=FAMILY,
        number=resolved.rsplit(":", 1)[1] if resolved else held.number,
        year=held.year,
        citation=f"Fatwa DSN-MUI No. {held.number}",
        title=held.title,
        language="id",
        kind=Kind.FATWA,
        status=StatusClaim(value=Status.UNKNOWN, source=CONNECTOR),
        aliases=_aliases(held.number),
    )


def _aliases(number: str) -> tuple[str, ...]:
    """The number as printed, and without its zero padding ("01/…" is cited as "1/…" too)."""
    return tuple(dict.fromkeys([number, number.lstrip("0")]))


def _page_count(texts: tuple[HeldText, ...]) -> int | None:
    """The text layer has a marker for every PDF page; the OCR only for pages with Arabic."""
    for text in texts:
        if text.method == ExtractionMethod.TEXT_LAYER:
            return len(split_pages(text.content)) or None
    return None


def _text_ref(text: HeldText, *, of_file: str) -> TextRef:
    return TextRef(
        sha256=hashlib.sha256(text.content.encode("utf-8")).hexdigest(),
        language=text.language,
        origin=TextOrigin.SELF_EXTRACTED,
        method=text.method,
        rights=Rights.PUBLIC,
        paged=True,
        of_file=of_file,
    )
