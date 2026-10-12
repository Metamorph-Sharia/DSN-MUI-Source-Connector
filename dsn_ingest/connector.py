"""DSN-MUI source connector — implements the CLI contract (see base.py).

  check     probe site liveness + parser sanity (one page, no downloads)
  discover  crawl listing tables → index (module 1: source discovery)
  sync      acquire + extract + dedupe, emit delta JSONL
            (modules 2 acquisition, 3 extraction/normalization,
             4 dedup/change-detection via sha256)
  records   full local snapshot rows (module 5 backing store: store.py)
  coverage  remote-indexed vs local-corpus reconciliation

Failure policy: one bad document never kills a run. The fallback ladder
(fetcher.py) classifies external-server/permission errors; failed items
are auto-retried on the next default sync.
"""
import hashlib

from . import config, extract
from .fetcher import FetchError
from .models import DocRecord, FatwaEntry
from .parser import parse_listing
from .store import Store


class DsnConnector:
    name = "dsn_mui"

    def __init__(self, fetcher, store: Store):
        self.fetcher = fetcher
        self.store = store

    # ---------- check ----------

    def check(self) -> dict:
        try:
            html = self.fetcher.get_html(config.FATWA_LIST_URL)
            rows = parse_listing(html)
            return {"connector": self.name, "ok": len(rows) >= 1,
                    "url": config.FATWA_LIST_URL, "rows_on_page1": len(rows),
                    "error": None}
        except FetchError as ex:
            return {"connector": self.name, "ok": False,
                    "url": config.FATWA_LIST_URL, "rows_on_page1": None,
                    "error": f"{ex.kind}: {ex}"}
        except Exception as ex:                      # noqa: BLE001
            return {"connector": self.name, "ok": False,
                    "url": config.FATWA_LIST_URL, "rows_on_page1": None,
                    "error": f"unknown: {ex!r}"}

    # ---------- discover (module 1) ----------

    def discover(self, max_pages: int = config.DEFAULT_MAX_PAGES) -> list[FatwaEntry]:
        out, n = [], 1
        while n <= max_pages:
            url = config.page_url(n)
            try:
                html = self.fetcher.get_html(url)
            except FetchError as ex:
                if ex.kind == "not_found" and n > 1:
                    break                       # ran off the end of the corpus
                raise
            rows = parse_listing(html)
            if not rows:
                break
            for e in rows:
                self.store.upsert_index(e)
                out.append(e)
            n += 1
        return out

    # ---------- sync (modules 2-4 + delta emit) ----------

    def sync(self, max_pages: int = config.DEFAULT_MAX_PAGES,
             limit: int | None = None, all_files: bool = False
             ) -> tuple[dict, list[DocRecord]]:
        run_id = self.store.start_run("sync")
        counts = {"discovered": 0, "new": 0, "changed": 0, "unchanged": 0,
                  "failed": 0, "skipped_already_have": 0}
        delta: list[DocRecord] = []

        index_source = "live"
        try:
            entries = self.discover(max_pages)
        except FetchError as ex:
            # degraded mode: listing is gated/unreachable — the Drive downloads
            # don't depend on it, so keep working the corpus we already indexed.
            self.store.log_error(run_id, None, "discover", ex.kind, str(ex))
            entries = self.store.index_entries()
            index_source = "stored-index"
            if not entries:
                counts["failed"] += 1
                self.store.finish_run(run_id, "failed", counts)
                return {"connector": self.name, "run_id": run_id, "status": "failed",
                        "error": f"{ex.kind}: {ex}", "counts": counts}, delta
        counts["discovered"] = len(entries)

        todo: list[FatwaEntry] = []
        for e in entries:
            doc = self.store.get_doc(e.source_id)
            have = doc and doc["last_result"] in ("new", "changed", "unchanged")
            href_changed = have and doc["pdf_href"] != e.pdf_href  # link rot / new file
            if have and href_changed:
                have = False
            if have and not all_files:
                counts["skipped_already_have"] += 1
                continue
            todo.append(e)
        if limit:
            todo = todo[:limit]

        for e in todo:
            rec = self._acquire(run_id, e)
            counts[rec.status] += 1
            if rec.status == "unchanged":
                continue
            delta.append(rec)               # new | changed | failed only

        status = "ok" if counts["failed"] == 0 and index_source == "live" else "partial"
        self.store.finish_run(run_id, status, counts)
        return {"connector": self.name, "run_id": run_id, "status": status,
                "index_source": index_source, "counts": counts}, delta

    def _acquire(self, run_id: int, e: FatwaEntry) -> DocRecord:
        """Modules 2-4 for a single document. Never raises."""
        stage = "acquire"
        try:
            data, ctype = self.fetcher.get_file(e.pdf_href)      # ladder inside

            stage = "dedupe"
            sha = hashlib.sha256(data).hexdigest()
            is_pdf = data[:5] == b"%PDF-" or ctype == "application/pdf"
            blob = self.store.write_blob(sha, data, ".pdf" if is_pdf else ".bin")
            result = self.store.record_success(e, sha, len(data), ctype, blob)

            text_path, needs_ocr = None, None
            if result == "unchanged":
                doc = self.store.get_doc(e.source_id)      # reuse last extraction
                if doc:
                    text_path = doc["text_path"]
                    needs_ocr = None if doc["needs_ocr"] is None else bool(doc["needs_ocr"])
            else:
                stage = "extract"
                if is_pdf:
                    text = extract.pdf_to_text(blob)
                    if text is None:                        # extractor unavailable
                        self.store.set_text(e.source_id, None, None)
                    else:
                        needs_ocr = len(text) < extract.MIN_TEXT_LEN
                        if needs_ocr:                       # scanned-ish → OCR later
                            self.store.set_text(e.source_id, None, True)
                        else:
                            text_path = str(extract.write_text(sha, text))
                            self.store.set_text(e.source_id, text_path, False)
                else:
                    self.store.set_text(e.source_id, None, None)
            return DocRecord(connector=self.name, source_id=e.source_id, title=e.title,
                             status=result, pdf_href=e.pdf_href, sha256=sha,
                             size_bytes=len(data), content_type=ctype,
                             raw_path=str(blob), text_path=text_path,
                             needs_ocr=needs_ocr)
        except FetchError as ex:
            self.store.record_failure(e, ex.kind, str(ex))
            self.store.log_error(run_id, e.source_id, stage, ex.kind, str(ex))
            return DocRecord(connector=self.name, source_id=e.source_id, title=e.title,
                             status="failed", pdf_href=e.pdf_href,
                             error=ex.kind, error_message=str(ex)[:300])
        except Exception as ex:                                   # noqa: BLE001
            self.store.record_failure(e, "unknown", repr(ex))
            self.store.log_error(run_id, e.source_id, stage, "unknown", repr(ex))
            return DocRecord(connector=self.name, source_id=e.source_id, title=e.title,
                             status="failed", pdf_href=e.pdf_href,
                             error="unknown", error_message=repr(ex)[:300])

    # ---------- list / status backing calls ----------

    def records(self) -> list[DocRecord]:
        return self.store.snapshot(self.name)

    def coverage(self) -> dict:
        return {"connector": self.name, **self.store.coverage()}

    def close(self) -> None:
        self.fetcher.close()
