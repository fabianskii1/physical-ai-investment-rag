from pathlib import Path

from reportlab.pdfgen import canvas

from pdf_loader import _make_doc_ids, main


def _write_pdf(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = canvas.Canvas(str(path))
    document.drawString(72, 720, text)
    document.save()


def test_duplicate_filenames_use_content_based_stable_ids(tmp_path: Path) -> None:
    first = tmp_path / "a" / "report.pdf"
    second = tmp_path / "b" / "report.pdf"
    _write_pdf(first, "first document")
    _write_pdf(second, "second document")

    before_move = _make_doc_ids([first, second], None)
    moved = tmp_path / "moved" / "report.pdf"
    moved.parent.mkdir()
    first.rename(moved)
    after_move = _make_doc_ids([moved, second], None)

    assert len(set(before_move)) == 2
    assert before_move[0] == after_move[0]
    assert before_move[1] == after_move[1]


def test_cli_processes_valid_files_and_continues_after_failure(
    tmp_path: Path, capsys
) -> None:
    valid = tmp_path / "valid.pdf"
    corrupt = tmp_path / "corrupt.pdf"
    _write_pdf(valid, "valid document")
    corrupt.write_bytes(b"not a PDF")

    exit_code = main([str(tmp_path), "--company", "acme"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "OK: valid.pdf" in captured.out
    assert "return type       : PASS (list[Document])" in captured.out
    assert "1-based page nums : PASS (1-1)" in captured.out
    assert "metadata keys     : PASS" in captured.out
    assert "page_content:" in captured.out
    assert "    valid document" in captured.out
    assert "metadata:" in captured.out
    assert "Error loading" in captured.err
    assert "1 succeeded, 1 failed, 2 total" in captured.err
