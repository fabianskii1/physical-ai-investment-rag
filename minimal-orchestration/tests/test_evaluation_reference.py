"""제공된 평가 PDF가 코드 배점과 일치하고 변경 시 조용히 진행하지 않는지 확인한다."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import app
from evaluation_reference import DESIGN_PDF, verify_scorecard_reference
from rag.scorecard import SCORECARD


def test_bundled_pdf_verifies_every_criterion_and_preserves_source_fingerprint():
    reference = verify_scorecard_reference()
    assert reference['source_pdf'] == str(DESIGN_PDF)
    assert reference['source_pages'] == [2, 3]
    assert reference['category_count'] == 6
    assert reference['criterion_count'] == 21
    assert reference['total_weight'] == 100
    assert reference['categories'] == SCORECARD
    assert len(reference['source_sha256']) == 64
    assert reference['verified_against_pdf'] is True


def test_changed_criterion_weight_is_rejected_before_research():
    from pypdf import PdfReader
    original = PdfReader(DESIGN_PDF)
    pages = [SimpleNamespace(extract_text=lambda page=page: page.extract_text()) for page in original.pages]
    text = pages[2].extract_text().replace('핵심 작업 성능(6)', '핵심 작업 성능(5)')
    assert '핵심 작업 성능(5)' in text
    pages[2] = SimpleNamespace(extract_text=lambda: text)
    with patch('evaluation_reference.PdfReader', return_value=SimpleNamespace(pages=pages)):
        with pytest.raises(ValueError, match='핵심 작업 성능'):
            verify_scorecard_reference()
        with patch.object(app, 'real_dependencies') as dependencies:
            with pytest.raises(ValueError, match='핵심 작업 성능'):
                app.run('MOTOMIND')
            dependencies.assert_not_called()


def test_missing_pdf_and_missing_criteria_pages_are_explicit_errors(tmp_path):
    with pytest.raises(FileNotFoundError):
        verify_scorecard_reference(tmp_path / 'missing.pdf')
    with patch('evaluation_reference.PdfReader', return_value=SimpleNamespace(pages=[])):
        with pytest.raises(ValueError, match='2~3쪽'):
            verify_scorecard_reference()


def test_check_criteria_cli_does_not_run_agents_or_api(monkeypatch, capsys):
    monkeypatch.setattr('sys.argv', ['app.py', '--check-criteria'])
    with patch.object(app, 'run') as single, patch.object(app, 'run_batch') as batch:
        app.main()
    single.assert_not_called()
    batch.assert_not_called()
    assert '6영역 / 21항목 / 100점' in capsys.readouterr().out
