"""SQLite state + content-addressed blob store.

Private to this connector — the outside world only sees DocRecord lines
and the status/coverage dicts. But the schema is deliberately the same
5-module shape (index / docs / runs / errors) so an orchestrator could,
if ever needed, read any connector's DB the same way.
"""
import json
import sqlite3
from pathlib import Path

from . import config
from .models import DocRecord, FatwaEntry, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS fatwa_index (
    source_id    TEXT PRIMARY KEY,
    number       TEXT,
    title        TEXT,
    pdf_href     TEXT,
    year         INTEGER,
    month        TEXT,
    first_seen   TEXT,
    last_seen    TEXT
);
CREATE TABLE IF NOT EXISTS fatwa_docs (
    source_id     TEXT PRIMARY KEY,
    sha256        TEXT,
    size_bytes    INTEGER,
    content_type  TEXT,
    raw_path      TEXT,
    text_path     TEXT,
    needs_ocr     INTEGER,           -- NULL = not attempted/undecidable
    pdf_href      TEXT,              -- href used for the current sha
    last_result   TEXT,              -- new | changed | unchanged | failed
    error_class   TEXT,
    error_message TEXT,
    fetched_at    TEXT,
    updated_at    TEXT
);
CREATE TABLE IF NOT EXISTS blobs (
    sha256    TEXT PRIMARY KEY,
    path      TEXT,
    size      INTEGER,
    mime      TEXT,
    ref_count INTEGER DEFAULT 1,
    created   TEXT
);
CREATE TABLE IF NOT EXISTS run (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT,
    started_at  TEXT,
    finished_at TEXT,
    status      TEXT,
    counts_json TEXT
);
CREATE TABLE IF NOT EXISTS run_error (
    run_id    INTEGER,
    source_id TEXT,
    stage     TEXT,
    kind      TEXT,
    message   TEXT,
    at        TEXT
);
"""


class Store:
    def __init__(self, db_path: Path = config.DB_PATH, blob_dir: Path = config.BLOB_DIR):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.blob_dir = blob_dir
        self.db = sqlite3.connect(db_path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # ---------- discovery index ----------

    def upsert_index(self, e: FatwaEntry) -> None:
        now = utcnow()
        self.db.execute(
            """INSERT INTO fatwa_index (source_id, number, title, pdf_href, year, month, first_seen, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (source_id) DO UPDATE SET
                 number = excluded.number, title = excluded.title,
                 pdf_href = excluded.pdf_href, year = excluded.year,
                 month = excluded.month, last_seen = excluded.last_seen""",
            (e.source_id, e.number, e.title, e.pdf_href, e.year, e.month, now, now),
        )
        self.db.commit()

    def index_entries(self) -> list[FatwaEntry]:
        rows = self.db.execute("SELECT * FROM fatwa_index ORDER BY year, number").fetchall()
        return [FatwaEntry(r["source_id"], r["number"], r["title"], r["pdf_href"],
                           r["year"], r["month"]) for r in rows]

    # ---------- acquisition / change detection ----------

    def write_blob(self, sha: str, data: bytes, ext: str) -> Path:
        """Content-addressed: same bytes → same path, writing twice is a no-op."""
        p = self.blob_dir / sha[:2] / f"{sha}{ext}"
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        return p

    def get_doc(self, source_id: str) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM fatwa_docs WHERE source_id = ?", (source_id,)).fetchone()

    def record_success(self, e: FatwaEntry, sha: str, size: int,
                       ctype: str, raw_path: Path) -> str:
        """Change detection. Returns 'new' | 'changed' | 'unchanged'."""
        now = utcnow()
        row = self.get_doc(e.source_id)
        if row is None or row["sha256"] is None:
            status = "new"
        elif row["sha256"] == sha:
            status = "unchanged"
        else:
            status = "changed"

        self.db.execute(
            """INSERT OR IGNORE INTO blobs (sha256, path, size, mime, created) VALUES (?, ?, ?, ?, ?)""",
            (sha, str(raw_path), size, ctype, now))
        self.db.execute(
            """UPDATE blobs SET ref_count = ref_count + 1 WHERE sha256 = ?""", (sha,))
        self.db.execute(
            """INSERT INTO fatwa_docs (source_id, sha256, size_bytes, content_type, raw_path,
                   pdf_href, last_result, error_class, error_message, fetched_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
               ON CONFLICT (source_id) DO UPDATE SET
                 sha256 = excluded.sha256, size_bytes = excluded.size_bytes,
                 content_type = excluded.content_type, raw_path = excluded.raw_path,
                 pdf_href = excluded.pdf_href, last_result = excluded.last_result,
                 error_class = NULL, error_message = NULL,
                 fetched_at = excluded.fetched_at, updated_at = excluded.updated_at""",
            (e.source_id, sha, size, ctype, str(raw_path), e.pdf_href, status, now, now))
        self.db.commit()
        return status

    def set_text(self, source_id: str, text_path: Path | None, needs_ocr: bool | None) -> None:
        self.db.execute(
            """UPDATE fatwa_docs SET text_path = ?, needs_ocr = ?, updated_at = ?
               WHERE source_id = ?""",
            (str(text_path) if text_path else None,
             None if needs_ocr is None else int(needs_ocr), utcnow(), source_id))
        self.db.commit()

    def record_failure(self, e: FatwaEntry, kind: str, message: str) -> None:
        """Keeps any last-good raw/text paths — a failure never deletes data."""
        now = utcnow()
        self.db.execute(
            """INSERT INTO fatwa_docs (source_id, pdf_href, last_result, error_class,
                   error_message, updated_at)
               VALUES (?, ?, 'failed', ?, ?, ?)
               ON CONFLICT (source_id) DO UPDATE SET
                 pdf_href = excluded.pdf_href, last_result = 'failed',
                 error_class = excluded.error_class, error_message = excluded.error_message,
                 updated_at = excluded.updated_at""",
            (e.source_id, e.pdf_href, kind, message[:500], now))
        self.db.commit()

    # ---------- views for list / status ----------

    def snapshot(self, connector: str) -> list[DocRecord]:
        rows = self.db.execute(
            """SELECT i.source_id, i.title, i.pdf_href, d.* FROM fatwa_index i
               LEFT JOIN fatwa_docs d USING (source_id) ORDER BY i.year, i.number""").fetchall()
        out = []
        for r in rows:
            last = r["last_result"]
            if last is None:
                status = "missing"          # discovered remotely, never acquired
            elif last == "failed":
                status = "failed"
            else:
                status = "unchanged"        # new/changed in the past = up to date now
            out.append(DocRecord(
                connector=connector, source_id=r["source_id"], title=r["title"],
                status=status, pdf_href=r["pdf_href"],
                sha256=r["sha256"], size_bytes=r["size_bytes"], content_type=r["content_type"],
                raw_path=r["raw_path"], text_path=r["text_path"],
                needs_ocr=None if r["needs_ocr"] is None else bool(r["needs_ocr"]),
                error=r["error_class"], error_message=r["error_message"],
                at=r["updated_at"] or ""))
        return out

    def coverage(self) -> dict:
        total_idx = self.db.execute("SELECT COUNT(*) c FROM fatwa_index").fetchone()["c"]
        by_result = {r["last_result"]: r["c"] for r in self.db.execute(
            "SELECT last_result, COUNT(*) c FROM fatwa_docs GROUP BY last_result")}
        never = self.db.execute(
            """SELECT COUNT(*) c FROM fatwa_index i LEFT JOIN fatwa_docs d USING (source_id)
               WHERE d.source_id IS NULL""").fetchone()["c"]
        errors = {r["error_class"]: r["c"] for r in self.db.execute(
            "SELECT error_class, COUNT(*) c FROM fatwa_docs WHERE last_result = 'failed' GROUP BY error_class")}
        ocr = self.db.execute(
            "SELECT COUNT(*) c FROM fatwa_docs WHERE needs_ocr = 1").fetchone()["c"]
        return {
            "remote_indexed": total_idx,
            "local_by_result": by_result,
            "never_acquired": never,
            "failed_by_error_class": errors,
            "needs_ocr": ocr,
            "aligned": never == 0 and not by_result.get("failed"),
        }

    # ---------- monitoring ----------

    def start_run(self, kind: str) -> int:
        cur = self.db.execute(
            "INSERT INTO run (kind, started_at, status) VALUES (?, ?, 'running')",
            (kind, utcnow()))
        self.db.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, status: str, counts: dict) -> None:
        self.db.execute(
            "UPDATE run SET finished_at = ?, status = ?, counts_json = ? WHERE run_id = ?",
            (utcnow(), status, json.dumps(counts), run_id))
        self.db.commit()

    def log_error(self, run_id: int, source_id: str | None, stage: str, kind: str, message: str) -> None:
        self.db.execute(
            "INSERT INTO run_error (run_id, source_id, stage, kind, message, at) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, source_id, stage, kind, message[:500], utcnow()))
        self.db.commit()

    def recent_runs(self, n: int = 5) -> list[dict]:
        rows = self.db.execute("SELECT * FROM run ORDER BY run_id DESC LIMIT ?", (n,)).fetchall()
        return [dict(r) for r in rows]

    def recent_errors(self, n: int = 10) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM run_error ORDER BY rowid DESC LIMIT ?", (n,)).fetchall()
        return [dict(r) for r in rows]
