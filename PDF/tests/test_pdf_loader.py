from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from langchain_core.documents import Document
from pdfminer.pdfdocument import PDFPasswordIncorrect
from pdfplumber.utils.exceptions import PdfminerException
from reportlab.pdfgen import canvas

import pdf_loader
from pdf_loader import (
    PDFDirectoryError,
    PDFEncryptedError,
    PDFExtractionError,
    PDFInvalidFileError,
    PDFLoaderError,
    PDFMetadataError,
    PDFNotFoundError,
    load_pdf,
)


@pytest.fixture
def two_page_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "sample.pdf"
    document = canvas.Canvas(str(path))
    document.drawString(72, 720, "first page text")
    document.showPage()
    document.drawString(72, 720, "second page text")
    document.save()
    return path


def test_load_pdf_contract(two_page_pdf: Path) -> None:
    documents = load_pdf(two_page_pdf, doc_id="doc-1", company="acme")

    assert len(documents) == 2
    assert all(isinstance(document, Document) for document in documents)
    assert [document.metadata["page"] for document in documents] == [1, 2]
    assert "first page text" in documents[0].page_content
    assert "second page text" in documents[1].page_content
    assert all(
        document.metadata
        == {
            "doc_id": "doc-1",
            "company": "acme",
            "file_path": str(two_page_pdf.resolve()),
            "page": page_number,
        }
        for page_number, document in enumerate(documents, start=1)
    )


@pytest.mark.parametrize("field", ["doc_id", "company"])
def test_rejects_empty_metadata(two_page_pdf: Path, field: str) -> None:
    values = {"doc_id": "doc-1", "company": "acme"}
    values[field] = "  "

    with pytest.raises(PDFMetadataError, match=field):
        load_pdf(two_page_pdf, **values)


def test_path_exceptions_are_domain_and_builtin_compatible(tmp_path: Path) -> None:
    missing = tmp_path / "missing.pdf"
    with pytest.raises(PDFNotFoundError) as missing_error:
        load_pdf(missing, "doc", "company")
    assert isinstance(missing_error.value, FileNotFoundError)
    assert isinstance(missing_error.value, PDFLoaderError)

    with pytest.raises(PDFDirectoryError) as directory_error:
        load_pdf(tmp_path, "doc", "company")
    assert isinstance(directory_error.value, IsADirectoryError)


def test_rejects_non_pdf_and_corrupt_pdf(tmp_path: Path) -> None:
    text_file = tmp_path / "file.txt"
    text_file.write_text("not a PDF")
    with pytest.raises(PDFInvalidFileError):
        load_pdf(text_file, "doc", "company")

    corrupt_pdf = tmp_path / "corrupt.pdf"
    corrupt_pdf.write_bytes(b"not a PDF")
    with pytest.raises(PDFInvalidFileError):
        load_pdf(corrupt_pdf, "doc", "company")


def test_sanitizes_nul_characters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "fake.pdf"
    path.write_bytes(b"%PDF-fake")

    class FakePage:
        def extract_text(self) -> str:
            return "alpha\x00beta"

    class FakePDF:
        pages = [FakePage()]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(pdf_loader.pdfplumber, "open", lambda _path: FakePDF())
    documents = load_pdf(path, "doc", "company")

    assert documents[0].page_content == "alpha beta"


def test_wraps_encrypted_and_unexpected_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "fake.pdf"
    path.write_bytes(b"%PDF-fake")

    def encrypted(_path):
        raise PdfminerException(PDFPasswordIncorrect())

    monkeypatch.setattr(pdf_loader.pdfplumber, "open", encrypted)
    with pytest.raises(PDFEncryptedError):
        load_pdf(path, "doc", "company")

    class BrokenPage:
        def extract_text(self):
            raise RuntimeError("extractor failed")

    class BrokenPDF:
        pages = [BrokenPage()]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(pdf_loader.pdfplumber, "open", lambda _path: BrokenPDF())
    with pytest.raises(PDFExtractionError) as error:
        load_pdf(path, "doc", "company")
    assert isinstance(error.value.__cause__, RuntimeError)


def test_concurrent_calls_do_not_share_state(two_page_pdf: Path) -> None:
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(load_pdf, two_page_pdf, f"doc-{index}", "acme")
            for index in range(8)
        ]
        results = [future.result() for future in futures]

    assert [result[0].metadata["doc_id"] for result in results] == [
        f"doc-{index}" for index in range(8)
    ]
    assert all([document.metadata["page"] for document in result] == [1, 2] for result in results)
