"""The store records file paths relative to its data directory, so a copied or mounted
data/dsn (another laptop, a container volume) still finds its blobs and texts."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dsn_ingest.models import FatwaEntry  # noqa: E402
from dsn_ingest.store import Store  # noqa: E402

SHA = "2a" + "0" * 62
ENTRY = FatwaEntry("1-DSN-MUI-IV-2000", "01/DSN-MUI/IV/2000", "Giro", "https://drive.google.com/file/d/x/view",
                   2000, "IV")


def make_store(data_dir: Path) -> Store:
    return Store(db_path=data_dir / "dsn_fatwa.db", blob_dir=data_dir / "blobs")


def test_paths_are_recorded_relative_to_the_data_directory(tmp_path):
    store = make_store(tmp_path)
    blob = store.write_blob(SHA, b"%PDF-", ".pdf")
    store.record_success(ENTRY, SHA, 5, "application/pdf", blob)
    store.set_text(ENTRY.source_id, tmp_path / "text" / f"{SHA}.txt", False)

    row = store.get_doc(ENTRY.source_id)
    assert row["raw_path"] == f"blobs/2a/{SHA}.pdf"
    assert row["text_path"] == f"text/{SHA}.txt"
    assert store.locate(row["raw_path"]) == blob


def test_a_moved_data_directory_still_finds_its_files(tmp_path):
    store = make_store(tmp_path / "before")
    blob = store.write_blob(SHA, b"%PDF-", ".pdf")
    store.record_success(ENTRY, SHA, 5, "application/pdf", blob)
    store.db.close()

    (tmp_path / "before").rename(tmp_path / "after")
    moved = make_store(tmp_path / "after")
    assert moved.locate(moved.get_doc(ENTRY.source_id)["raw_path"]).read_bytes() == b"%PDF-"


def test_absolute_paths_from_another_machine_are_made_relative_on_open(tmp_path):
    store = make_store(tmp_path)
    elsewhere = "/Users/someone/projects/DSN-MUI-Source-Connector/data/dsn"
    store.db.execute(
        "INSERT INTO fatwa_docs (source_id, sha256, raw_path, text_path, last_result) VALUES (?, ?, ?, ?, 'new')",
        (ENTRY.source_id, SHA, f"{elsewhere}/blobs/2a/{SHA}.pdf", f"{elsewhere}/text/{SHA}.txt"))
    store.db.execute("INSERT INTO blobs (sha256, path) VALUES (?, ?)", (SHA, f"{elsewhere}/blobs/2a/{SHA}.pdf"))
    store.db.commit()
    store.db.close()

    make_store(tmp_path).db.close()

    db = sqlite3.connect(tmp_path / "dsn_fatwa.db")
    assert db.execute("SELECT raw_path, text_path FROM fatwa_docs").fetchone() == (
        f"blobs/2a/{SHA}.pdf", f"text/{SHA}.txt")
    assert db.execute("SELECT path FROM blobs").fetchone() == (f"blobs/2a/{SHA}.pdf",)


def test_snapshot_records_still_point_at_files_on_disk(tmp_path):
    store = make_store(tmp_path)
    store.upsert_index(ENTRY)
    blob = store.write_blob(SHA, b"%PDF-", ".pdf")
    store.record_success(ENTRY, SHA, 5, "application/pdf", blob)

    [record] = store.snapshot("dsn_mui")
    assert Path(record.raw_path) == blob
    assert record.text_path is None
