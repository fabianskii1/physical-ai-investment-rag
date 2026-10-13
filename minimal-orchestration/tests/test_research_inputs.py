"""기획 입력은 자료집을 읽되 이전 평가를 정답으로 넘기지 않는다."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from research_inputs import MAX_CONTEXT_CHARS, load_company_research_input


def test_figure_input_keeps_facts_unknowns_and_source_leads_without_prior_opinion():
    value = load_company_research_input(Path(__file__).parents[1] / 'com_data', 'Figure AI')
    assert value['total_pages'] == 9
    assert 'Series C' in value['context']
    assert 'BMW' in value['context']
    assert '실제 매출액' in value['context']
    assert 'https://www.figure.ai/news/series-c' in value['context']
    assert '8. Figure AI 투자평가' not in value['context']
    assert '잠정 투자 판단' not in value['context']
    assert '최종 정리' not in value['context']
    assert '390억 달러' not in value['context']
    assert not value['context_truncated']


def test_context_limit_is_explicit_and_does_not_hide_original_page_count(tmp_path):
    (tmp_path / 'MOTOMIND.pdf').touch()
    pages = [SimpleNamespace(extract_text=lambda: '원천 단서' * 6000), SimpleNamespace(extract_text=lambda: '추가자료')]
    with patch('research_inputs.PdfReader', return_value=SimpleNamespace(pages=pages)):
        value = load_company_research_input(tmp_path, 'MOTOMIND')
    assert len(value['context']) == MAX_CONTEXT_CHARS
    assert value['context_truncated'] is True
    assert value['total_pages'] == 2


def test_missing_input_is_reported_and_unknown_company_is_not_silently_mapped(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_company_research_input(tmp_path, 'ROBROS')
    with pytest.raises(ValueError):
        load_company_research_input(tmp_path, 'Unknown')
