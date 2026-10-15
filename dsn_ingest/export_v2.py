"""`dsn export`: every fatwa the store holds, as one contract v2 bundle (a full snapshot).

A fatwa is exported when we hold its PDF, including when the latest re-fetch failed (a
failure never deletes the last good copy). A fatwa never acquired has no content, so the
contract can't carry it; it is counted in `without_content` instead.

A held file that has gone missing from disk stops the export before the manifest is
written, so the platform never mistakes a broken store for fatwas leaving the source.
"""
from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

from metamorph_contract import BundleWriter, ExtractionMethod, is_unresolved

from . import __version__
from .contract_v2 import CONNECTOR, HeldFatwa, HeldText, exported_texts, normalized_document, paged_text
from .store import Store

PDF_MEDIA_TYPE = "application/pdf"
OCR_DIR = "ocr"                         # scripts/ocr_arabic.py writes {sha}.ara.txt here

_HELD = """
SELECT i.source_id, i.number, i.title, i.year, i.month, i.pdf_href,
       d.sha256, d.content_type, d.raw_path, d.text_path, d.fetched_at
FROM fatwa_index i LEFT JOIN fatwa_docs d USING (source_id)
ORDER BY i.year, i.number
"""


@dataclass
class ExportStats:
    documents: int = 0
    texts: int = 0
    unresolved: int = 0
    without_content: int = 0             # listed but never acquired

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def export_bundle(store: Store, out_dir: Path, *, run_id: str) -> ExportStats:
    stats = ExportStats()
    with BundleWriter(out_dir, connector=CONNECTOR, connector_version=__version__, run_id=run_id) as bundle:
        for row in store.db.execute(_HELD).fetchall():
            if row["sha256"] is None:
                stats.without_content += 1
                continue
            held = held_fatwa(store, row)
            bundle.put_blob(store.locate(row["raw_path"]).read_bytes())
            for text in exported_texts(held):
                bundle.put_text(text.content)
            document = normalized_document(held, connector_version=__version__)
            bundle.add(document)
            stats.documents += 1
            stats.texts += len(document.texts)
            stats.unresolved += is_unresolved(document.document.id)
    return stats


def held_fatwa(store: Store, row: sqlite3.Row) -> HeldFatwa:
    raw = store.locate(row["raw_path"])
    return HeldFatwa(
        source_id=row["source_id"],
        number=row["number"],
        title=row["title"],
        year=row["year"],
        month=row["month"],
        pdf_href=row["pdf_href"],
        fetched_at=row["fetched_at"],
        file_sha256=row["sha256"],
        # Drive serves every file as octet-stream; the store names PDFs by their magic bytes.
        media_type=PDF_MEDIA_TYPE if raw.suffix == ".pdf" else row["content_type"],
        texts=tuple(_held_texts(store, row)),
    )


def _held_texts(store: Store, row: sqlite3.Row):
    latin = store.locate(row["text_path"])
    if latin is not None:
        yield HeldText("id", ExtractionMethod.TEXT_LAYER, _paged(latin))
    arabic = store.data_dir / OCR_DIR / f"{row['sha256']}.ara.txt"
    if arabic.is_file():
        yield HeldText("ar", ExtractionMethod.OCR, _paged(arabic))


def _paged(path: Path) -> str:
    return paged_text(path.read_text(encoding="utf-8")).strip()
