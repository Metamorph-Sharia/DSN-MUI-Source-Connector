"""PDF → per-page Latin text (dsn_ingest/extract.py), on PDFs made in the test."""
import pymupdf

from dsn_ingest import extract


def ruling_pdf(tmp_path):
    """A ruling as fatwas set it: the label in a column of its own, a justified line set a word at a time."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Pertama")
    for n, word in enumerate(["Ketentuan", "Umum"]):
        page.insert_text((180 + 80 * n, 100), word)
    path = tmp_path / "fatwa.pdf"
    doc.save(path)
    return path


def test_each_printed_line_is_one_line_of_a_page(tmp_path):
    assert extract.pdf_to_pages(ruling_pdf(tmp_path)) == ["Pertama Ketentuan Umum"]


def test_pymupdfs_own_lines_are_kept_when_asked_for(tmp_path, monkeypatch):
    path = ruling_pdf(tmp_path)

    assert extract.pdf_to_pages(path, printed_lines=False) == ["Pertama\nKetentuan\nUmum"]
    monkeypatch.setenv("METAMORPH_PDF_LINES", "extracted")
    assert extract.pdf_to_pages(path) == ["Pertama\nKetentuan\nUmum"]


class MinerU:
    """ExtractPdf as the tests need it: what MinerU read of a fatwa whose Arabic it OCR'd."""

    def __init__(self, pages):
        from metamorph_pdf_text import ExtractedPdf, Extraction, OcrMode, Tier

        self.read = ExtractedPdf(tuple(pages), "ocr", Extraction("mineru", "4.0.12+t1", Tier.STANDARD, OcrMode.OCR, 1, 0), ())

    def run(self, pdf, role):
        assert role == "main"
        return self.read


QUOTED = "Firman Allah SWT:\nيَا أَيُّهَا الَّذِينَ آمَنُوا أَوْفُوا بِالْعُقُودِ\n\"Hai orang yang beriman, penuhilah akad itu.\""


def test_the_connectors_own_extraction_is_the_default(tmp_path):
    extracted = extract.extract_pdf(ruling_pdf(tmp_path))

    assert (extracted.pages, extracted.method, extracted.extraction, extracted.arabic) == (
        ["Pertama Ketentuan Umum"], "text_layer", None, None
    )


def test_minerus_reading_splits_the_arabic_quotation_off_the_latin_text(tmp_path):
    extracted = extract.extract_pdf(ruling_pdf(tmp_path), extractor=MinerU([QUOTED]))

    assert extracted.pages == ["Firman Allah SWT:\n\"Hai orang yang beriman, penuhilah akad itu.\""]
    assert extracted.arabic == ["يَا أَيُّهَا الَّذِينَ آمَنُوا أَوْفُوا بِالْعُقُودِ"]
    assert (extracted.method, extracted.extraction.engine) == ("ocr", "mineru")


def test_what_mineru_read_is_kept_beside_the_text_and_a_pymupdf_reading_removes_it(tmp_path, monkeypatch):
    monkeypatch.setattr(extract.config, "TEXT_DIR", tmp_path / "text")
    read = extract.extract_pdf(ruling_pdf(tmp_path), extractor=MinerU([QUOTED]))

    text = extract.save("ab" * 32, read)

    record = extract.read_record(text)
    assert (record.method, record.extraction) == (read.method, read.extraction)
    assert "آمَنُوا" in record.arabic.read_text(encoding="utf-8")

    extract.save("ab" * 32, extract.Extracted(["Pertama Ketentuan Umum"]))  # the rollback

    assert extract.read_record(text) is None and not record.arabic.exists()


def test_a_line_mostly_in_arabic_is_a_quotation_and_a_latin_one_naming_a_word_is_not():
    assert extract.is_arabic_line("بِسْمِ اللهِ الرَّحْمٰنِ الرَّحِيْمِ")
    assert not extract.is_arabic_line("akad wakalah (وكالة) adalah")
