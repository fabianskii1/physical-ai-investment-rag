"""관점 커버리지 제외, 원본 페이지 입력, 모순 방어와 rewrite/research를 검증한다."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import legacy_adapter


def state():
    return {'company_info': {'name': 'Figure AI'}, 'final_report': '구매 계약 [E1]',
            'document_catalog': {'D1': {'file_path': '/mock.pdf', 'company': 'Figure AI'}},
            'source_checked_claims': [{'claim': 'Figure는 컴퓨팅을 구매한다.',
                                     'source': {'doc_id': 'D1', 'page': 1},
                                     'evidence_quote': 'Figure는 컴퓨팅을 구매한다.'}],
            'score_details': {}}


def test_source_contexts_come_from_original_page_and_group_ids(monkeypatch):
    payload = state()
    payload['final_report'] += ' [E2]'
    payload['source_checked_claims'] *= 2
    parse = Mock(return_value=[SimpleNamespace(page_content='Figure는 컴퓨팅을 구매한다. 판매 실적은 별개다.')])
    monkeypatch.setattr(legacy_adapter, 'load_pdf', parse)
    contexts = legacy_adapter.report_source_contexts(payload)
    assert len(contexts) == 1
    assert contexts[0]['evidence_ids'] == ['E1', 'E2']
    assert '판매 실적은 별개다.' in contexts[0]['page_text']
    parse.assert_called_once()


def test_changed_source_quote_is_rejected(monkeypatch):
    monkeypatch.setattr(legacy_adapter, 'load_pdf', lambda *a, **k: [SimpleNamespace(page_content='다른 원문')])
    with pytest.raises(ValueError, match='현재 원문과 일치'):
        legacy_adapter.report_source_contexts(state())


def dependencies(monkeypatch, tmp_path):
    monkeypatch.setenv('OPENAI_API_KEY', 'offline-test')
    monkeypatch.setattr('link_sources.load_link_registry', lambda *a: {})
    monkeypatch.setattr(legacy_adapter, '_catalog_records', lambda *a: [])
    monkeypatch.setattr(legacy_adapter, 'report_source_contexts', lambda payload: [{'page_text': '판매가 아닌 구매 원문'}])
    return legacy_adapter.real_dependencies(tmp_path, tmp_path / 'sources', prepare_retrieval=False)


def test_quality_method_uses_three_axes_and_original_sources_not_view_coverage(monkeypatch, tmp_path):
    deps = dependencies(monkeypatch, tmp_path)
    verdict = legacy_adapter.ReportVerdict(groundedness=True, neutrality=True, bias_control=True,
        groundedness_reason='원문과 일치', neutrality_reason='자사 발표 구분',
        bias_control_reason='단일 출처 한계 명시', issues=[], data_gaps=['현재 거래 미입력'], action='rewrite')
    request = Mock(return_value=verdict)
    monkeypatch.setattr(legacy_adapter, '_structured_request', request)
    result = deps.report_evaluator(state())
    assert result['passed'] is True and result['method'] == 'hybrid'
    assert result['action'] == 'rewrite'
    assert set(legacy_adapter.REPORT_QUALITY_KEYS) == {'groundedness', 'neutrality', 'bias_control'}
    assert request.call_args.args[2]['source_contexts'][0]['page_text'] == '판매가 아닌 구매 원문'
    assert 'technical_maturity' not in result['rubric']


def test_max_grade_and_not_max_reason_is_blocked_before_llm(monkeypatch, tmp_path):
    deps = dependencies(monkeypatch, tmp_path)
    payload = state()
    payload['score_details'] = {'관련 전문성': {'grade': 4, 'reason': '최고점 대신 4점 부여'}}
    request = Mock()
    monkeypatch.setattr(legacy_adapter, '_structured_request', request)
    result = deps.report_evaluator(payload)
    assert result['passed'] is False and result['action'] == 'research'
    assert '모순' in result['issues'][0]
    request.assert_not_called()
