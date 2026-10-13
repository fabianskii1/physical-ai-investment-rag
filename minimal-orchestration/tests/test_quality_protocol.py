from unittest.mock import Mock

import pytest

import legacy_adapter


def verdict(issues):
    return legacy_adapter.ReportVerdict(groundedness=True, neutrality=True, bias_control=True,
        groundedness_reason='원문 일치', neutrality_reason='회사 발표 구분',
        bias_control_reason='미확인 공개', issues=issues, data_gaps=['투자조건 미확인'])


def test_contradictory_judge_is_retried_with_same_sources_without_coercing_pass(monkeypatch):
    request = Mock(side_effect=[verdict(['자료 부족']), verdict([])])
    monkeypatch.setattr(legacy_adapter, '_structured_request', request)
    payload = {'source_contexts': ['original'], 'report': 'same'}
    result = legacy_adapter.request_report_verdict('검사', payload)
    assert request.call_count == 2 and result.issues == []
    assert all(call.args[2]['source_contexts'] is payload['source_contexts'] for call in request.call_args_list)
    assert request.call_args.args[2]['report_with_paragraph_ids'] == [{'id': 'P1', 'text': 'same'}]
    assert '이전 판정' in request.call_args.args[1]


def test_two_contradictions_fail_instead_of_becoming_pass(monkeypatch):
    request = Mock(return_value=verdict(['실제 결함']))
    monkeypatch.setattr(legacy_adapter, '_structured_request', request)
    with pytest.raises(ValueError, match='두 번 모두'):
        legacy_adapter.request_report_verdict('검사', {})
    assert request.call_count == 2


def test_real_findings_bind_existing_paragraph_and_derive_failed_axis(monkeypatch):
    review = legacy_adapter.QualityReview(findings=[legacy_adapter.QualityFinding(
        criterion='neutrality', basis='misleading_attribution', paragraph_id='P2',
        defect='자사 홍보를 독립 우수성으로 서술', action='rewrite')],
        groundedness_reason='원문 일치', neutrality_reason='우수성 과장', bias_control_reason='U 공개')
    monkeypatch.setattr(legacy_adapter, '_structured_request', Mock(return_value=review))
    result = legacy_adapter.request_report_verdict('검사', {'report': '# 제목\n세계 최고다.'})
    assert result.groundedness is True and result.neutrality is False and result.bias_control is True
    assert '세계 최고다.' in result.issues[0]


def test_invented_report_location_is_rejected_after_one_retry(monkeypatch):
    review = legacy_adapter.QualityReview(findings=[legacy_adapter.QualityFinding(
        criterion='groundedness', basis='unsupported_claim', paragraph_id='P999',
        defect='없는 문단', action='rewrite')], groundedness_reason='결함',
        neutrality_reason='중립', bias_control_reason='U 공개')
    request = Mock(return_value=review)
    monkeypatch.setattr(legacy_adapter, '_structured_request', request)
    with pytest.raises(ValueError):
        legacy_adapter.request_report_verdict('검사', {'report': '실제 보고서'})
    assert request.call_count == 2
