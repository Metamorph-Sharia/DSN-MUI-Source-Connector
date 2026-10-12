#!/usr/bin/env python3
"""Arabic OCR for the archived fatwa PDFs — a downstream job, NOT part of sync.

The connector archives every PDF. This script recovers the Arabic that the
PDF text layer does not provide:

  * 2008+ fatwas — Arabic exists only inside scanned page images
  * 2000–2007 fatwas — Arabic extracts, but ~30% of it is private-use
    glyphs the embedded font's mapping lost

Method: render each page at 300 dpi (PyMuPDF), OCR with
`tesseract -l ara --psm 3`, keep Arabic-dominant lines (>= 80% Arabic
chars, <= 1 digit). Latin content is not re-OCR'd — the PDF text layer
already has it. Output: data/dsn/ocr/<sha>.ara.txt, page-marked, one file
per document, written atomically so reruns skip finished work.

Accuracy note: harakat-heavy naskh OCRs at roughly 80-90%; legible for
human and LLM consumption, not certified-citation grade.

Requires: brew install tesseract tesseract-lang   (macOS)

usage:
  uv run python scripts/ocr_arabic.py                 # all pending documents
  uv run python scripts/ocr_arabic.py --limit 3       # sample
  uv run python scripts/ocr_arabic.py --status        # coverage counts
  uv run python scripts/ocr_arabic.py --doc 96-DSN-MUI-VI-2015 --rerun
"""
import argparse
import concurrent.futures
import re
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dsn_ingest import config  # noqa: E402
from dsn_ingest.store import Store  # noqa: E402

OCR_DIR = config.DATA_DIR / "ocr"
AR_LINE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]")
BASE_RE = re.compile(r"[\u0621-\u064A]")
PUA_RE = re.compile(r"[\uE000-\uF8FF]")


def keep_line(line: str) -> bool:
    ns = re.sub(r"\s", "", line)
    if len(ns) < 4:
        return False
    arabic = len(AR_LINE.findall(ns))
    digits = sum(1 for c in ns if c.isdigit())
    return arabic / len(ns) >= 0.8 and digits <= 1


def ocr_png(png: Path) -> str:
    out = subprocess.run(["tesseract", str(png), "stdout", "-l", "ara", "--psm", "3"],
                         capture_output=True)
    text = unicodedata.normalize("NFKC", out.stdout.decode("utf-8", "replace"))
    text = re.sub(r"[\u200e\u200f\u202a-\u202e]", "", text)   # bidi controls
    return "\n".join(l for l in text.splitlines() if keep_line(l))


def ocr_document(source_id: str, dpi: int) -> str:
    store = Store()
    row = store.db.execute(
        "SELECT raw_path, sha256 FROM fatwa_docs WHERE source_id = ?", (source_id,)).fetchone()
    if not row or not row["raw_path"]:
        return "skipped: no blob"
    out_path = OCR_DIR / f"{row['sha256']}.ara.txt"
    if out_path.exists():
        return "already done"
    doc = pymupdf.open(row["raw_path"])
    parts, pages_hit, pages_total = [], 0, len(doc)
    with tempfile.TemporaryDirectory() as td:
        png = Path(td) / "page.png"
        for n, page in enumerate(doc, 1):
            page.get_pixmap(dpi=dpi).save(png)
            kept = ocr_png(png)
            if kept.strip():
                parts.append(f"=== page {n} ===\n{kept}")
                pages_hit += 1
    doc.close()
    OCR_DIR.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".part")
    tmp.write_text("\n\n".join(parts) + "\n", encoding="utf-8")
    tmp.rename(out_path)                     # atomic: a half-file never counts as done
    return f"{pages_hit}/{pages_total} pages with arabic"


def needs_ocr(store: Store) -> list[str]:
    """Documents whose text layer lacks Arabic or is glyph-damaged."""
    todo = []
    rows = store.db.execute(
        "SELECT source_id, text_path FROM fatwa_docs "
        "WHERE raw_path IS NOT NULL AND last_result IN ('new','changed','unchanged')").fetchall()
    for r in rows:
        sid = r["source_id"]
        sha = store.db.execute(
            "SELECT sha256 FROM fatwa_docs WHERE source_id = ?", (sid,)).fetchone()["sha256"]
        if (OCR_DIR / f"{sha}.ara.txt").exists():
            continue
        text = ""
        if r["text_path"] and Path(r["text_path"]).exists():
            text = Path(r["text_path"]).read_text(encoding="utf-8", errors="replace")
        if len(BASE_RE.findall(text)) < 100 or len(PUA_RE.findall(text)) > 300:
            todo.append(sid)
    return todo


def status(store: Store) -> int:
    rows = store.db.execute(
        "SELECT source_id, text_path, sha256 FROM fatwa_docs WHERE raw_path IS NOT NULL").fetchall()
    have = sum(1 for r in rows if (OCR_DIR / f"{r['sha256']}.ara.txt").exists())
    want = [r["source_id"] for r in rows
            if not (OCR_DIR / f"{r['sha256']}.ara.txt").exists()]
    print(f"documents: {len(rows)} | ocr'd: {have} | remaining: {len(want)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="ocr_arabic")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--doc", action="append", help="specific source_id(s)")
    ap.add_argument("--rerun", action="store_true", help="with --doc: delete existing output first")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--dpi", type=int, default=300)
    args = ap.parse_args()

    store = Store()
    if args.status:
        return status(store)

    targets = args.doc if args.doc else needs_ocr(store)
    if args.doc and args.rerun:
        for sid in targets:
            sha = store.db.execute(
                "SELECT sha256 FROM fatwa_docs WHERE source_id = ?", (sid,)).fetchone()["sha256"]
            p = OCR_DIR / f"{sha}.ara.txt"
            p.unlink(missing_ok=True)
    if args.limit:
        targets = targets[:args.limit]
    print(f"{len(targets)} documents to OCR", file=sys.stderr)
    if not targets:
        return 0

    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(ocr_document, sid, args.dpi): sid for sid in targets}
        for fut in concurrent.futures.as_completed(futs):
            sid = futs[fut]
            done += 1
            try:
                info = fut.result()
            except Exception as ex:                          # noqa: BLE001
                info = f"FAILED {ex!r}"
            print(f"[{done}/{len(targets)}] {sid}: {info}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
