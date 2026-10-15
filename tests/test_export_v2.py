"""`dsn export`: the store as one contract v2 bundle the platform loader reads."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from metamorph_contract import Bundle, split_pages  # noqa: E402

from dsn_ingest import extract  # noqa: E402
from dsn_ingest.export_v2 import export_bundle  # noqa: E402
from dsn_ingest.models import FatwaEntry  # noqa: E402
from dsn_ingest.store import Store  # noqa: E402

PDF = b"%PDF-1.4 fatwa"
GIRO = FatwaEntry("01-DSN-MUI-IV-2000", "01/DSN-MUI/IV/2000", "Giro",
                  "https://drive.google.com/file/d/giro/view", 2000, "IV")
BROKEN = FatwaEntry("26-DSN-MUI-III-2002", "26/DSN-MUI/III/2002", "Rahn Emas", "https://", 2002, "III")


@pytest.fixture
def store(tmp_path, monkeypatch):
    data_dir = tmp_path / "data" / "dsn"
    monkeypatch.setattr(extract.config, "TEXT_DIR", data_dir / "text")
    store = Store(db_path=data_dir / "dsn_fatwa.db", blob_dir=data_dir / "blobs")
    for entry in (GIRO, BROKEN):
        store.upsert_index(entry)
    return store


def acquire(store: Store, entry: FatwaEntry, pdf: bytes, *, latin: list[str] | None, arabic: str | None) -> str:
    import hashlib

    sha = hashlib.sha256(pdf).hexdigest()
    blob = store.write_blob(sha, pdf, ".pdf")
    store.record_success(entry, sha, len(pdf), "application/octet-stream", blob)
    if latin is not None:
        store.set_text(entry.source_id, extract.write_text(sha, latin), False)
    if arabic is not None:
        ocr = store.data_dir / "ocr" / f"{sha}.ara.txt"
        ocr.parent.mkdir(parents=True, exist_ok=True)
        ocr.write_text(arabic, encoding="utf-8")
    return sha


def entries(directory: Path):
    return list(Bundle(directory).entries())


def test_every_acquired_fatwa_is_exported_with_its_pdf_and_texts(store, tmp_path):
    sha = acquire(store, GIRO, PDF, latin=["FATWA tentang GIRO", "Ketentuan"], arabic="=== page 2 ===\nبسم الله")
    store.record_failure(BROKEN, "content", "no usable link")

    stats = export_bundle(store, tmp_path / "bundle", run_id="r1")

    bundle = Bundle(tmp_path / "bundle")
    assert (bundle.manifest.connector, bundle.manifest.documents) == ("dsn-fatwa", 1)
    [entry] = entries(tmp_path / "bundle")
    assert entry.is_valid, entry.problems
    document = entry.document
    assert document.document.id == "dsnmui:fatwa:1"
    assert document.files[0].media_type == "application/pdf"
    assert bundle.blob_path(sha).read_bytes() == PDF
    latin, arabic = document.texts
    assert split_pages(bundle.blob_path(latin.sha256).read_text()) == [(1, "FATWA tentang GIRO"), (2, "Ketentuan")]
    assert split_pages(bundle.blob_path(arabic.sha256).read_text()) == [(2, "بسم الله")]
    assert stats.as_dict() == {"documents": 1, "texts": 2, "unresolved": 0, "without_content": 1}


def test_a_failed_refetch_keeps_exporting_the_last_good_copy(store, tmp_path):
    acquire(store, GIRO, PDF, latin=["FATWA"], arabic=None)
    store.record_failure(GIRO, "transient", "timeout")

    export_bundle(store, tmp_path / "bundle", run_id="r1")

    assert [e.document.source.source_id for e in entries(tmp_path / "bundle")] == ["01-DSN-MUI-IV-2000"]


def test_a_fatwa_whose_pdf_has_no_text_layer_still_exports_its_pdf(store, tmp_path):
    acquire(store, GIRO, PDF, latin=None, arabic=None)

    export_bundle(store, tmp_path / "bundle", run_id="r1")

    [entry] = entries(tmp_path / "bundle")
    assert entry.is_valid and entry.document.texts == ()


def test_a_missing_blob_stops_the_export_without_a_manifest(store, tmp_path):
    sha = acquire(store, GIRO, PDF, latin=None, arabic=None)
    (store.blob_dir / sha[:2] / f"{sha}.pdf").unlink()

    with pytest.raises(FileNotFoundError):
        export_bundle(store, tmp_path / "bundle", run_id="r1")
    assert not (tmp_path / "bundle" / "manifest.json").exists()
