"""PDF를 페이지별 LangChain ``Document``로 로드하고 검증한다."""

import argparse
import hashlib
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pdfplumber
from langchain_core.documents import Document
from pdfminer.pdfdocument import PDFPasswordIncorrect
from pdfminer.pdfparser import PDFSyntaxError
from pdfplumber.utils.exceptions import PdfminerException

__all__ = [
    "PDFAccessError",
    "PDFDirectoryError",
    "PDFEmptyError",
    "PDFEncryptedError",
    "PDFExtractionError",
    "PDFInvalidFileError",
    "PDFLoaderError",
    "PDFMetadataError",
    "PDFNotFoundError",
    "PDFPathError",
    "PDFValidationError",
    "load_pdf",
]


class PDFLoaderError(Exception):
    """PDF 로더에서 발생하는 모든 공개 예외의 기본 클래스."""


class PDFPathError(PDFLoaderError, OSError):
    """PDF 경로가 잘못되었거나 접근할 수 없을 때 발생한다."""


class PDFNotFoundError(PDFPathError, FileNotFoundError):
    """PDF 파일을 찾을 수 없을 때 발생한다."""


class PDFDirectoryError(PDFPathError, IsADirectoryError):
    """PDF 파일 대신 디렉터리가 전달됐을 때 발생한다."""


class PDFAccessError(PDFPathError, PermissionError):
    """PDF를 읽을 권한이 없을 때 발생한다."""


class PDFValidationError(PDFLoaderError, ValueError):
    """PDF 또는 입력값이 로더 계약에 맞지 않을 때 발생한다."""


class PDFMetadataError(PDFValidationError):
    """``doc_id`` 또는 ``company`` 메타데이터가 잘못됐을 때 발생한다."""


class PDFInvalidFileError(PDFValidationError):
    """PDF가 아니거나 PDF 구조가 손상됐을 때 발생한다."""


class PDFEmptyError(PDFValidationError):
    """PDF에 페이지가 하나도 없을 때 발생한다."""


class PDFEncryptedError(PDFAccessError):
    """PDF가 암호화되어 읽을 수 없을 때 발생한다."""


class PDFExtractionError(PDFLoaderError):
    """PDF 페이지 텍스트 추출 중 예상하지 못한 오류가 발생한다."""


def _validate_metadata(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise PDFMetadataError(f"{name} must be a non-empty string")


def _normalize_pdf_path(pdf_path: str | Path) -> Path:
    try:
        path = Path(pdf_path).expanduser()
    except TypeError as exc:
        raise PDFPathError(f"Invalid PDF path: {pdf_path!r}") from exc

    if not path.exists():
        raise PDFNotFoundError(f"PDF file not found: {path}")
    if not path.is_file():
        raise PDFDirectoryError(f"Expected a PDF file, but got a directory: {path}")
    if path.suffix.lower() != ".pdf":
        raise PDFInvalidFileError(f"Expected a PDF file: {path}")
    return path.resolve()


def load_pdf(pdf_path: str | Path, doc_id: str, company: str) -> list[Document]:
    """PDF의 각 페이지를 하나의 ``Document``로 변환한다.

    ``page`` 메타데이터는 원본 PDF 기준 1부터 시작한다.
    ``file_path``는 작업 디렉터리에 영향받지 않도록 절대경로로 저장한다.

    Raises:
        PDFMetadataError: ``doc_id`` 또는 ``company``가 빈 문자열인 경우.
        PDFPathError: 파일 경로가 없거나 접근할 수 없는 경우.
        PDFValidationError: PDF가 아니거나 손상된 경우.
        PDFEncryptedError: PDF가 암호화된 경우.
        PDFExtractionError: 페이지 텍스트 추출에 실패한 경우.
    """
    _validate_metadata("doc_id", doc_id)
    _validate_metadata("company", company)
    path = _normalize_pdf_path(pdf_path)

    documents: list[Document] = []
    try:
        with pdfplumber.open(path) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                page_content = (page.extract_text() or "").replace("\x00", " ")
                documents.append(
                    Document(
                        page_content=page_content,
                        metadata={
                            "doc_id": doc_id,
                            "company": company,
                            "file_path": str(path),
                            "page": page_number,
                        },
                    )
                )
    except PdfminerException as exc:
        underlying_error = exc.args[0] if exc.args else exc
        if isinstance(underlying_error, PDFPasswordIncorrect):
            raise PDFEncryptedError(f"PDF is encrypted: {path}") from exc
        raise PDFInvalidFileError(f"Invalid or corrupted PDF: {path}") from exc
    except PDFPasswordIncorrect as exc:
        raise PDFEncryptedError(f"PDF is encrypted: {path}") from exc
    except PDFSyntaxError as exc:
        raise PDFInvalidFileError(f"Invalid or corrupted PDF: {path}") from exc
    except PermissionError as exc:
        raise PDFAccessError(f"No read permission for PDF: {path}") from exc
    except OSError as exc:
        raise PDFPathError(f"Could not read PDF: {path}") from exc
    except Exception as exc:
        raise PDFExtractionError(f"Failed to extract text from PDF: {path}") from exc

    if not documents:
        raise PDFEmptyError(f"PDF contains no pages: {path}")

    return documents


def _find_pdf_files(inputs: list[str]) -> list[Path]:
    """파일과 폴더 입력에서 PDF를 찾아 중복 없이 반환한다."""
    default_input_dir = Path(__file__).resolve().parent / "input"
    candidates = [Path(value).expanduser() for value in inputs]
    if not candidates:
        candidates = [default_input_dir]

    pdf_files: list[Path] = []
    seen_paths: set[Path] = set()
    for candidate in candidates:
        if candidate.is_dir():
            discovered = sorted(
                path
                for path in candidate.rglob("*")
                if path.is_file() and path.suffix.lower() == ".pdf"
            )
        else:
            discovered = [candidate]

        for path in discovered:
            identity = path.resolve(strict=False)
            if identity not in seen_paths:
                seen_paths.add(identity)
                pdf_files.append(path)

    return pdf_files


def _select_pdf_files() -> list[Path] | None:
    """운영체제의 파일 선택창을 열어 여러 PDF를 반환한다."""
    cancel_exit_codes: set[int] = set()
    try:
        if sys.platform == "darwin" and shutil.which("osascript"):
            script = """
                try
                    set selectedFiles to choose file with prompt \
                        "Select one or more PDF files" \
                        of type {"com.adobe.pdf"} \
                        with multiple selections allowed
                    set output to ""
                    repeat with selectedFile in selectedFiles
                        set output to output & POSIX path of selectedFile & linefeed
                    end repeat
                    return output
                on error number -128
                    return ""
                end try
            """
            result = subprocess.run(
                ["osascript", "-e", script],
                check=False,
                capture_output=True,
                text=True,
            )
        elif sys.platform == "win32" and shutil.which("powershell.exe"):
            script = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$dialog = New-Object System.Windows.Forms.OpenFileDialog; "
                "$dialog.Filter = 'PDF files (*.pdf)|*.pdf'; "
                "$dialog.Multiselect = $true; "
                "if ($dialog.ShowDialog() -eq 'OK') { $dialog.FileNames }"
            )
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", script],
                check=False,
                capture_output=True,
                text=True,
            )
        elif shutil.which("zenity"):
            cancel_exit_codes = {1}
            result = subprocess.run(
                [
                    "zenity",
                    "--file-selection",
                    "--multiple",
                    "--separator=\n",
                    "--file-filter=PDF files | *.pdf",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        elif shutil.which("kdialog"):
            cancel_exit_codes = {1}
            result = subprocess.run(
                ["kdialog", "--getopenfilename", ".", "PDF files (*.pdf)", "--multiple"],
                check=False,
                capture_output=True,
                text=True,
            )
        else:
            return None
    except OSError:
        return None

    if result.returncode != 0:
        return [] if result.returncode in cancel_exit_codes else None
    return [Path(line) for line in result.stdout.splitlines() if line.strip()]


def _file_digest(path: Path) -> str:
    """파일 이동에 영향받지 않는 내용 기반 해시를 생성한다."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()[:8]


def _make_doc_ids(paths: list[Path], custom_doc_id: str | None) -> list[str]:
    """중복 파일명이 있어도 이동에 안정적인 ``doc_id``를 생성한다."""
    if custom_doc_id:
        return [custom_doc_id]

    stem_counts: dict[str, int] = {}
    for path in paths:
        stem_counts[path.stem] = stem_counts.get(path.stem, 0) + 1

    doc_ids = []
    for path in paths:
        if stem_counts[path.stem] == 1:
            doc_ids.append(path.stem)
            continue

        try:
            content_hash = _file_digest(path)
        except OSError:
            content_hash = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:8]
        doc_ids.append(f"{path.stem}-{content_hash}")
    return doc_ids


def _terminal_width() -> int:
    """터미널 크기에 맞추되 지나치게 길거나 짧은 출력을 피한다."""
    return max(60, min(shutil.get_terminal_size(fallback=(100, 24)).columns, 120))


def _print_rule(character: str = "-") -> None:
    print(character * _terminal_width())


def _print_wrapped_text(text: str, indent: str = "    ") -> None:
    """본문의 원래 줄바꿈을 보존하면서 터미널 폭에 맞춰 표시한다."""
    if not text:
        print(f"{indent}[empty page]")
        return

    available_width = max(20, _terminal_width() - len(indent))
    for line in text.splitlines():
        if not line:
            print()
            continue
        print(
            textwrap.fill(
                line,
                width=available_width,
                initial_indent=indent,
                subsequent_indent=indent,
                replace_whitespace=False,
                break_long_words=False,
                break_on_hyphens=False,
            )
        )


def _print_document(document: Document, preview_chars: int | None = None) -> None:
    original_content = document.page_content
    page_content = original_content
    was_truncated = False
    if preview_chars is not None and len(page_content) > preview_chars:
        page_content = page_content[:preview_chars].rstrip()
        last_boundary = max(page_content.rfind(" "), page_content.rfind("\n"))
        if last_boundary >= int(preview_chars * 0.7):
            page_content = page_content[:last_boundary].rstrip()
        was_truncated = True

    print("metadata:")
    for key in ("doc_id", "company", "file_path", "page"):
        print(f"    {key:<10}: {document.metadata.get(key)!s}")
    print("page_content:")
    _print_wrapped_text(page_content)
    if was_truncated:
        print(
            f"    ... [preview: {preview_chars:,} of "
            f"{len(original_content):,} characters]"
        )


def _print_requirement_check(documents: list[Document]) -> None:
    expected_keys = {"doc_id", "company", "file_path", "page"}
    page_numbers = [document.metadata.get("page") for document in documents]
    expected_pages = list(range(1, len(documents) + 1))
    document_types_ok = all(isinstance(document, Document) for document in documents)
    metadata_ok = all(set(document.metadata) == expected_keys for document in documents)
    pages_ok = page_numbers == expected_pages
    pages_with_text = sum(bool(document.page_content.strip()) for document in documents)
    total_characters = sum(len(document.page_content) for document in documents)

    print("Requirement check:")
    print(
        f"    return type       : {'PASS' if document_types_ok else 'FAIL'} "
        "(list[Document])"
    )
    print(f"    documents/pages   : PASS ({len(documents):,})")
    print(
        f"    text extraction   : {'PASS' if pages_with_text else 'WARN'} "
        f"({pages_with_text:,}/{len(documents):,} pages, "
        f"{total_characters:,} characters)"
    )
    page_range = f"1-{len(documents):,}" if documents else "none"
    print(
        f"    1-based page nums : {'PASS' if pages_ok else 'FAIL'} "
        f"({page_range})"
    )
    print(
        f"    metadata keys     : {'PASS' if metadata_ok else 'FAIL'} "
        "(doc_id, company, file_path, page)"
    )


def main(argv: list[str] | None = None) -> int:
    """파일 선택창, 파일 또는 폴더에서 PDF를 찾아 순차 처리한다."""
    parser = argparse.ArgumentParser(
        description="Load PDFs into page-level LangChain Documents."
    )
    parser.add_argument("inputs", nargs="*", help="PDF files or directories.")
    parser.add_argument(
        "--doc-id",
        help="Document ID. Only valid when exactly one PDF is selected.",
    )
    parser.add_argument(
        "--company",
        default="unknown",
        help='Company metadata value. Defaults to "unknown".',
    )
    parser.add_argument(
        "--preview-chars",
        type=int,
        default=300,
        help="Characters to show from the first page. Defaults to 300.",
    )
    parser.add_argument(
        "--show-all-pages",
        action="store_true",
        help="Print page_content and metadata for every page.",
    )
    parser.add_argument(
        "--no-dialog",
        action="store_true",
        help="Skip the file picker and scan the project's input directory.",
    )
    args = parser.parse_args(argv)

    if args.preview_chars < 0:
        parser.error("--preview-chars must be zero or greater.")

    if args.inputs:
        paths = _find_pdf_files(args.inputs)
    elif args.no_dialog:
        paths = _find_pdf_files([])
    else:
        selected_paths = _select_pdf_files()
        if selected_paths is None:
            paths = _find_pdf_files([])
        elif not selected_paths:
            print("Cancelled: no PDF files were selected.", file=sys.stderr)
            return 130
        else:
            paths = _find_pdf_files([str(path) for path in selected_paths])

    if not paths:
        input_dir = Path(__file__).resolve().parent / "input"
        parser.exit(
            1,
            "Error: no PDF files found. "
            f"Put PDF files in {input_dir} or pass a file/directory argument.\n",
        )
    if args.doc_id and len(paths) != 1:
        parser.error("--doc-id can only be used when exactly one PDF is selected.")

    doc_ids = _make_doc_ids(paths, args.doc_id)
    failures = 0
    successes = 0
    for index, (path, doc_id) in enumerate(zip(paths, doc_ids), start=1):
        try:
            documents = load_pdf(path, doc_id=doc_id, company=args.company)
        except PDFLoaderError as exc:
            failures += 1
            print(
                f"\n[{index}/{len(paths)}] Error loading: {path.name}",
                file=sys.stderr,
            )
            print(f"    {exc}", file=sys.stderr)
            continue

        successes += 1
        print()
        _print_rule("=")
        print(f"[{index}/{len(paths)}] OK: {path.name}")
        _print_rule("=")
        print(f"doc_id  : {doc_id}")
        print(f"company : {args.company}")
        print(f"file    : {documents[0].metadata['file_path']}")
        print()
        _print_requirement_check(documents)
        print()
        if args.show_all_pages:
            for document in documents:
                _print_rule()
                print(
                    f"Document {document.metadata['page']}/{len(documents)} "
                    "(full text)"
                )
                _print_rule()
                _print_document(document)
        else:
            _print_rule()
            print(f"First Document (preview up to {args.preview_chars:,} characters)")
            _print_rule()
            _print_document(documents[0], args.preview_chars)

    if failures:
        print(
            f"\nCompleted: {successes} succeeded, {failures} failed, "
            f"{len(paths)} total.",
            file=sys.stderr,
        )
        return 1

    print()
    _print_rule("=")
    print(f"Completed successfully: {successes} PDF file(s).")
    _print_rule("=")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
