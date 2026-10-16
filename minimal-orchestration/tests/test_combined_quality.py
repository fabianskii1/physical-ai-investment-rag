from unittest.mock import patch

from combined_quality import evaluate_combined_quality
from legacy_adapter import ReportVerdict
from test_combined_reflection import payload as full_payload, reflection, COMPANIES


def test_combined_quality_requires_all_three_content_criteria_and_no_issues():
    values = {'groundedness': True, 'neutrality': True, 'bias_control': True,
              'groundedness_reason': '원문 일치', 'neutrality_reason': '회사 발표 구분',
              'bias_control_reason': '미확인 공개', 'issues': []}
    values['company_report_reflection'] = [reflection(company) for company in COMPANIES]
    payload = full_payload()
    with patch('combined_quality.request_report_verdict', return_value=ReportVerdict(**values)) as judge:
        result = evaluate_combined_quality(payload)
    assert result['passed'] is True and result['method'] == 'hybrid'
    assert judge.call_args.args[1]['source_contexts'] == payload['source_contexts']
    assert judge.call_args.args[1]['report_evidence_links']
    assert '관점 커버리지는 평가에서 제외' in judge.call_args.args[0]
    for key in ('groundedness', 'neutrality', 'bias_control'):
        with patch('combined_quality.request_report_verdict',
                   return_value=ReportVerdict(**{**values, key: False})):
            assert evaluate_combined_quality(payload)['passed'] is False
    with patch('combined_quality.request_report_verdict',
               return_value=ReportVerdict(**{**values, 'issues': ['실제 결함']})):
        assert evaluate_combined_quality(payload)['passed'] is False


def test_quality_keeps_cited_facts_and_pages_without_resending_full_evidence_map():
    from copy import deepcopy
    payload = full_payload()
    cited = {'evidence_quote': '원문에 실제로 기록된 작업 검증 내용',
             'source': {'doc_id': 'MM-01', 'page': 1}, 'source_scope': 'reported_fact'}
    payload['report'] += '\n검증한 작업 범위를 설명한다. [MM-E1]\n'
    payload['companies'] = [{'company': 'MOTOMIND', 'prefix': 'MM',
        'evidence': {'MM-E1': cited, 'MM-E999': {'evidence_quote': '미인용 사실'}}}]
    before = deepcopy(payload)
    values = {'groundedness': True, 'neutrality': True, 'bias_control': True,
              'groundedness_reason': '원문 일치', 'neutrality_reason': '회사 발표 구분',
              'bias_control_reason': '미확인 공개', 'issues': [],
              'company_report_reflection': [reflection(company) for company in COMPANIES]}
    with patch('combined_quality.request_report_verdict', return_value=ReportVerdict(**values)) as judge:
        evaluate_combined_quality(payload)
    submitted = judge.call_args.args[1]
    pair = next(p for p in submitted['assertion_pairs'] if 'MM-E1' in p['evidence_ids'])
    assert pair['allowed_facts'] == [{'id': 'MM-E1', **cited}]
    assert all('evidence' not in c for c in submitted['companies'])
    assert submitted['source_contexts'] == payload['source_contexts']
    assert payload == before
