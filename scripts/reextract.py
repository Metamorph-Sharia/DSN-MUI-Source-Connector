#!/usr/bin/env python3
"""Re-run text extraction over the archived PDFs.

Use whenever the extraction engine or its cleaning rules change (happened
twice in week one: pypdf→PyMuPDF, then NFKC + garbage stripping + page
markers), and to switch METAMORPH_PDF_ENGINE (pymupdf ⇄ mineru) for the texts held. Reads blobs from the content-addressed store, rebuilds
data/dsn/text/<sha>.txt, updates text_path/needs_ocr in the DB.
Never touches the archived PDFs themselves.

usage: uv run python scripts/reextract.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from metamorph_pdf_text import ExtractionFailed, ExtractPdf  # noqa: E402

from dsn_ingest import extract  # noqa: E402
from dsn_ingest.store import Store  # noqa: E402


def reextract(store: Store, extractor: ExtractPdf | None = None) -> dict[str, int]:
    """Each held fatwa read again. One MinerU can't read keeps its text, and the rest are still read; a MinerU out
    of reach (ExtractionUnavailable) stops them all, so no text is half switched."""
    rows = store.db.execute(
        "SELECT source_id, sha256, raw_path FROM fatwa_docs "
        "WHERE raw_path IS NOT NULL AND last_result IN ('new','changed','unchanged')").fetchall()
    counts = dict.fromkeys(("ok", "needs_ocr", "skipped", "unreadable"), 0)
    for r in rows:
        p = store.locate(r["raw_path"])
        if not p.exists():                      # blob renamed? re-derive from sha
            p = Path(store.blob_dir) / r["sha256"][:2] / f"{r['sha256']}.pdf"
        if not p.exists() or p.suffix != ".pdf":
            counts["skipped"] += 1
            continue
        try:
            extracted = extract.extract_pdf(p, extractor=extractor)
        except ExtractionFailed as e:
            print(f"unreadable by MinerU, text kept: {r['source_id']} ({e})")
            counts["unreadable"] += 1
            continue
        if extracted is None:
            store.set_text(r["source_id"], None, None)      # engine unavailable
            counts["skipped"] += 1
        elif len(extract.page_text(extracted.pages)) < extract.MIN_TEXT_LEN:
            store.set_text(r["source_id"], None, True)      # scanned-ish
            counts["needs_ocr"] += 1
        else:
            store.set_text(r["source_id"], str(extract.save(r["sha256"], extracted)), False)
            counts["ok"] += 1
    store.db.commit()
    return counts


def main() -> int:
    counts = reextract(Store())
    print("re-extracted: " + " | ".join(f"{n} {name}" for name, n in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
