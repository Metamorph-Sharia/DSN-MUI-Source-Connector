"""Data models.

FatwaEntry  - internal: one row of the listing table, enough to fetch later.
DocRecord   - what sync/list emit: a POINTER to structured raw data on disk.

DocRecord is deliberately NOT a frozen schema (see CONTRACT.md): the OJK/BI
connectors will carry their own metadata. For now we only agree that every
record has pointer fields + a status/error, and the real payload (raw bytes
and extracted text) lives in files.
"""
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Optional


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class FatwaEntry:
    source_id: str                 # e.g. "162-DSN-MUI-VII-2025" (stable key)
    number: str                    # as printed: "162/DSN-MUI/VII/2025"
    title: str                     # the "Tentang" cell
    pdf_href: str                  # EXACT href from listing, resourcekey kept.
                                   # Never strip query params — legacy Drive
                                   # files require the resourcekey to download.
    year: Optional[int] = None
    month: Optional[str] = None    # roman numeral as printed ("VII")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DocRecord:
    connector: str                 # "dsn_mui"
    source_id: str
    title: str
    status: str                    # "new" | "changed" | "failed"  (delta emits
                                   #  only these three; unchanged items stay
                                   #  silent — see CONTRACT.md)
    pdf_href: str
    sha256: Optional[str] = None
    size_bytes: Optional[int] = None
    content_type: Optional[str] = None
    raw_path: Optional[str] = None     # downloaded bytes on disk
    text_path: Optional[str] = None    # normalized raw text, None until extracted
    needs_ocr: Optional[bool] = None   # True = text layer missing/short → OCR later
    error: Optional[str] = None        # error class, see base.ERROR_CLASSES
    error_message: Optional[str] = None
    at: str = field(default_factory=utcnow)

    def to_dict(self) -> dict:
        return asdict(self)
