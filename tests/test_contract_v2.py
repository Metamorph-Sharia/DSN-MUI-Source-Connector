"""The pure mapping from a held fatwa to a contract v2 NormalizedDocument."""
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from metamorph_contract import (  # noqa: E402
    Confidence,
    ExtractionMethod,
    FileRole,
    Kind,
    Rights,
    Status,
    TextOrigin,
    problems,
    split_pages,
)

from dsn_ingest.contract_v2 import (  # noqa: E402
    CONNECTOR,
    HeldFatwa,
    HeldText,
    fatwa_id,
    normalized_document,
    paged_text,
)

PDF_SHA = "ab" * 32
LATIN = "=== page 1 ===\nFATWA\nTentang\nGIRO\n\n=== page 2 ===\nKetentuan Umum"
ARABIC = "=== page 2 ===\nيا أيها الذين آمنوا"


def held(**changes) -> HeldFatwa:
    fatwa = HeldFatwa(
        source_id="01-DSN-MUI-IV-2000",
        number="01/DSN-MUI/IV/2000",
        title="Giro",
        year=2000,
        month="IV",
        pdf_href="https://drive.google.com/file/d/abc/view?usp=sharing&resourcekey=0-k",
        fetched_at="2026-10-12T07:03:30+00:00",
        file_sha256=PDF_SHA,
        media_type="application/pdf",
        texts=(
            HeldText("id", ExtractionMethod.TEXT_LAYER, paged_text(LATIN)),
            HeldText("ar", ExtractionMethod.OCR, paged_text(ARABIC)),
        ),
    )
    return replace(fatwa, **changes)


def test_the_fatwa_id_is_its_sequence_number_without_padding():
    assert fatwa_id("01/DSN-MUI/IV/2000") == "dsnmui:fatwa:1"
    assert fatwa_id("162/DSN-MUI/VII/2025") == "dsnmui:fatwa:162"
    assert fatwa_id("12A/DSN-MUI/IV/2000") == "dsnmui:fatwa:12a"
    assert fatwa_id("Fatwa tanpa nomor") is None


def test_page_markers_become_the_contract_markers_with_their_page_numbers():
    assert split_pages(paged_text(ARABIC)) == [(2, "يا أيها الذين آمنوا")]
    assert [number for number, _ in split_pages(paged_text(LATIN))] == [1, 2]


def test_a_fatwa_becomes_a_valid_document():
    document = normalized_document(held(), connector_version="0.5.0")

    assert problems(document) == []
    assert document.source.connector == CONNECTOR == "dsn-fatwa"
    assert document.source.source_id == "01-DSN-MUI-IV-2000"
    info = document.document
    assert (info.id, info.id_confidence, info.issuer, info.family, info.number, info.year) == (
        "dsnmui:fatwa:1", Confidence.HIGH, "dsnmui", "fatwa", "1", 2000)
    assert info.kind == Kind.FATWA
    assert info.citation == "Fatwa DSN-MUI No. 01/DSN-MUI/IV/2000"
    assert info.title == "Giro"
    assert info.aliases == ("01/DSN-MUI/IV/2000", "1/DSN-MUI/IV/2000")


def test_status_is_unknown_because_dsn_mui_does_not_publish_one():
    assert normalized_document(held(), connector_version="0.5.0").document.status.value == Status.UNKNOWN


def test_the_pdf_is_the_main_file_and_both_texts_come_from_it():
    document = normalized_document(held(), connector_version="0.5.0")

    [pdf] = document.files
    assert (pdf.sha256, pdf.role, pdf.rights, pdf.pages) == (PDF_SHA, FileRole.MAIN, Rights.PUBLIC, 2)
    latin, arabic = document.texts
    assert (latin.language, latin.origin, latin.method, latin.paged, latin.of_file) == (
        "id", TextOrigin.SELF_EXTRACTED, ExtractionMethod.TEXT_LAYER, True, PDF_SHA)
    assert (arabic.language, arabic.method, arabic.of_file) == ("ar", ExtractionMethod.OCR, PDF_SHA)


def test_text_shas_are_of_the_held_content():
    from hashlib import sha256

    document = normalized_document(held(), connector_version="0.5.0")
    assert document.texts[0].sha256 == sha256(paged_text(LATIN).encode()).hexdigest()


def test_an_ocr_file_without_arabic_is_not_a_text():
    document = normalized_document(
        held(texts=(HeldText("id", ExtractionMethod.TEXT_LAYER, paged_text(LATIN)),
                    HeldText("ar", ExtractionMethod.OCR, "\n"))),
        connector_version="0.5.0")
    assert [text.language for text in document.texts] == ["id"]


def test_a_fatwa_without_extracted_text_still_carries_its_pdf():
    document = normalized_document(held(texts=()), connector_version="0.5.0")
    assert problems(document) == []
    assert document.texts == ()
    assert document.files[0].pages is None


def test_an_unreadable_number_is_loaded_unresolved_for_review():
    document = normalized_document(held(number="tanpa nomor"), connector_version="0.5.0")
    assert problems(document) == []
    assert document.document.id.startswith("unresolved:dsn-fatwa:")
    assert document.document.id_confidence == Confidence.NONE


def test_the_listing_row_is_kept_as_source_fields():
    fields = normalized_document(held(), connector_version="0.5.0").source_fields
    assert fields == {"number": "01/DSN-MUI/IV/2000", "title": "Giro", "year": 2000, "month": "IV",
                      "pdf_href": held().pdf_href}
