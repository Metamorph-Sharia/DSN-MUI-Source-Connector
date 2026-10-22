"""scripts/reextract.py: every held fatwa's PDF read again, from the blob store."""
import importlib.util
import sys
from pathlib import Path

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dsn_ingest import extract  # noqa: E402
from dsn_ingest.models import FatwaEntry  # noqa: E402
from dsn_ingest.store import Store  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "reextract.py"
GIRO = FatwaEntry("01-DSN-MUI-IV-2000", "01/DSN-MUI/IV/2000", "Giro",
                  "https://drive.google.com/file/d/giro/view", 2000, "IV")
HAJI = FatwaEntry("149-DSN-MUI-VI-2022", "149/DSN-MUI/VI/2022", "Haji", "https://drive.google.com/file/d/haji/view",
                  2022, "VI")


def reextract_script():
    spec = importlib.util.spec_from_file_location("reextract", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fatwa_pdf(wording: str) -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), wording)
    return doc.tobytes()


@pytest.fixture
def store(tmp_path, monkeypatch):
    data_dir = tmp_path / "data" / "dsn"
    monkeypatch.setattr(extract.config, "TEXT_DIR", data_dir / "text")
    return Store(db_path=data_dir / "dsn_fatwa.db", blob_dir=data_dir / "blobs")


def held(store: Store, entry: FatwaEntry, pdf: bytes) -> str:
    """A fatwa fetched and read before, its old text on disk."""
    import hashlib

    store.upsert_index(entry)
    sha = hashlib.sha256(pdf).hexdigest()
    store.record_success(entry, sha, len(pdf), "application/pdf", store.write_blob(sha, pdf, ".pdf"))
    store.set_text(entry.source_id, extract.write_text(sha, ["old text"]), False)
    return sha


class MinerU:
    """ExtractPdf as the script needs it, which can't read one of the files."""

    def __init__(self, unreadable: bytes):
        from metamorph_pdf_text import ExtractedPdf, Extraction, OcrMode, Tier

        self.unreadable = unreadable
        read = Extraction("mineru", "4.0.12+t1", Tier.STANDARD, OcrMode.OCR, 1, 0)
        self.read = ExtractedPdf(("FATWA tentang GIRO " + "Ketentuan Umum " * 20,), "ocr", read, ())

    def run(self, pdf, role):
        from metamorph_pdf_text import ExtractionFailed

        if pdf == self.unreadable:
            raise ExtractionFailed("unexpected Middle JSON: KeyError('blocks')")
        return self.read


def test_a_fatwa_mineru_cannot_read_keeps_its_text_and_the_others_are_still_read(store):
    unreadable = fatwa_pdf("Haji")
    held(store, GIRO, fatwa_pdf("Giro"))
    held(store, HAJI, unreadable)

    counts = reextract_script().reextract(store, MinerU(unreadable))

    assert counts == {"ok": 1, "needs_ocr": 0, "skipped": 0, "unreadable": 1}
    giro, haji = (store.locate(store.get_doc(e.source_id)["text_path"]) for e in (GIRO, HAJI))
    assert "FATWA tentang GIRO" in giro.read_text(encoding="utf-8")
    assert "old text" in haji.read_text(encoding="utf-8")
