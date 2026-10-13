from rag.workflow import _company_advertising_issue, _protect_declared_performance_stage


def test_unattributed_company_superiority_is_not_independent_comparison():
    source = {'publisher': 'Figure AI', 'source_url': 'https://www.figure.ai/'}
    assert _company_advertising_issue('Figure AI는 세계 최고 수준의 팀이다.', source, 'Figure AI')
    assert not _company_advertising_issue('Figure AI는 공식 소개에서 세계 최고 수준이라고 주장한다.', source, 'Figure AI')
    assert not _company_advertising_issue('Figure AI의 실제 투자자가 참여했다.', source, 'Figure AI')
    assert not _company_advertising_issue('비교 시험에서 세계 최고 수준으로 기록됐다.',
                                         {'publisher': 'Independent Test Lab'}, 'Figure AI')


def test_specifications_without_required_repeated_measurements_cannot_be_grade_three():
    result = _protect_declared_performance_stage({
        'criterion': '핵심 작업 성능', 'grade': 3, 'evidence_ids': ['E1'],
        'reason': '6DoF 관절로 조작한다. 다만 반복 실측 데이터 및 다현장 재현 사례는 미제공.'})
    assert result['grade'] is None and result['evidence_ids'] == []
