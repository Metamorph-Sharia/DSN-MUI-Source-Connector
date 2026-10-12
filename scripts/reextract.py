#!/usr/bin/env python3
"""Re-run text extraction over the archived PDFs.

Use whenever the extraction engine or its cleaning rules change (happened
twice in week one: pypdf→PyMuPDF, then NFKC + garbage stripping + page
markers). Reads blobs from the content-addressed store, rebuilds
data/dsn/text/<sha>.txt, updates text_path/needs_ocr in the DB.
Never touches the archived PDFs themselves.

usage: uv run python scripts/reextract.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dsn_ingest import extract  # noqa: E402
from dsn_ingest.store import Store  # noqa: E402


def main() -> int:
    store = Store()
    rows = store.db.execute(
        "SELECT source_id, sha256, raw_path FROM fatwa_docs "
        "WHERE raw_path IS NOT NULL AND last_result IN ('new','changed','unchanged')").fetchall()
    ok = flagged = skipped = 0
    for r in rows:
        p = Path(r["raw_path"])
        if not p.exists():                      # repo moved? re-derive from sha
            p = Path(store.blob_dir) / r["sha256"][:2] / f"{r['sha256']}.pdf"
        if not p.exists() or p.suffix != ".pdf":
            skipped += 1
            continue
        pages = extract.pdf_to_pages(p)
        if pages is None:
            store.set_text(r["source_id"], None, None)      # engine unavailable
            skipped += 1
        elif len(extract.page_text(pages)) < extract.MIN_TEXT_LEN:
            store.set_text(r["source_id"], None, True)      # scanned-ish
            flagged += 1
        else:
            store.set_text(r["source_id"], str(extract.write_text(r["sha256"], pages)), False)
            ok += 1
    store.db.commit()
    print(f"re-extracted: {ok} ok | {flagged} needs_ocr | {skipped} skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
