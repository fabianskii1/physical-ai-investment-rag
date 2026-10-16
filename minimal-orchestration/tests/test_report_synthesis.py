from copy import deepcopy
from unittest.mock import patch

import pytest

from company_report_inputs import build_company_report_inputs
from combined_report import _normalise_records, build_combined_report, save_combined_report
from report_synthesis import NARRATIVE_FIELDS, synthesize_company_reports, validate_report_synthesis
from test_combined_report import records, portfolio


def inputs_and_synthesis(records):
    for record in records:
        name = record['state']['company_info']['name']
        record['state']['final_report'] = f'''# {name} 기업 보고서
## 1 — 사업 분석
### 제품·고객
확인 사실: {name}는 제조 현장용 로봇 제품을 개발한다. [E1]

분석·해석 (추론): 개발 계획은 고객 실증을 검토할 출발점이며 매출 증거는 아니다. [E1]

근거의 한계: 현재 고객 유상납품과 반복 작업 성능의 확인 자료가 부족하다.

후속 확인: 고객 실증과 거래조건을 확인한다.

## 4 — 투자 판단
### 최종 투자 제안
**투자 보류** — 고객 실증과 거래조건 확인이 필요하다.
'''
    companies = _normalise_records(records)
    inputs = build_company_report_inputs(companies)
    narratives = []
    for report in inputs:
        by_kind = {p['kind']: p for p in report['paragraphs']}
        def paragraph(kind, text, refs=None):
            return {'text': text, 'report_paragraph_ids': [by_kind[kind]['paragraph_id']],
                    'evidence_ids': refs or []}
        narratives.append({'company': report['company'],
            'business_context': paragraph('fact', '제조 현장용 제품을 개발한다.', [report['prefix']+'-E1']),
            'market_and_team': paragraph('analysis', '개발 계획을 고객 실증으로 전환할 필요가 있다.', [report['prefix']+'-E1']),
            'technology_and_execution': paragraph('analysis', '개발과 실제 납품의 차이를 구별한다.', [report['prefix']+'-E1']),
            'risks_and_conditions': paragraph('limitation', '유상납품과 반복 성능은 확인이 필요하다.', [report['prefix']+'-E1']),
            'individual_opinion': paragraph('opinion', '현재 투자 의견은 보류이며 실사 후 재검토한다.', [report['prefix']+'-E1'])})
    shared = {'text': '세 기업의 개발 계획과 실제 사업화 성과를 구분하여 투자 조건을 심사한다.',
              'report_paragraph_ids': [report['paragraphs'][1]['paragraph_id'] for report in inputs],
              'evidence_ids': [report['prefix']+'-E1' for report in inputs]}
    synthesis = {'companies': narratives, 'executive_summary': deepcopy(shared),
                 'cross_company_comparison': [deepcopy(shared), deepcopy(shared)]}
    return companies, inputs, synthesis


def test_writer_reads_full_company_reports_and_keeps_report_to_evidence_mapping(records):
    companies, inputs, synthesis = inputs_and_synthesis(records)
    received = []
    result = synthesize_company_reports(companies, source_contexts=[], report_inputs=inputs,
                                       writer=lambda payload: received.append(payload) or synthesis)
    assert all(item['report_markdown'] == records[i]['state']['final_report']
               for i, item in enumerate(received[0]['company_reports']))
    assert len(result['reflection_map']) == 18
    assert result['source_reports'][0]['report_sha256'] == inputs[0]['report_sha256']


def test_duplicate_origin_ids_are_normalized_without_a_model_retry_or_content_change(records):
    companies,inputs,synthesis=inputs_and_synthesis(records)
    p=synthesis['companies'][0]['technology_and_execution']
    original=p['text'];p['report_paragraph_ids']*=2
    result=validate_report_synthesis(synthesis,inputs,companies)
    assert result['companies'][0]['technology_and_execution']['text']==original
    assert len(result['companies'][0]['technology_and_execution']['report_paragraph_ids'])==1


def test_synthesis_cannot_replace_verified_data_platform_with_generic_unknown_assets(records):
    companies,inputs,synthesis=inputs_and_synthesis(records)
    companies[0]['state']['report_fact_contract']=[{'label':'데이터 라벨링 플랫폼','report_pattern':r'데이터\s*라벨링'}]
    with pytest.raises(ValueError,match='데이터 라벨링'):
        validate_report_synthesis(synthesis,inputs,companies)
    synthesis['companies'][0]['technology_and_execution']['text']='회사 발표의 데이터 라벨링 수단을 확인했으나 보호 자산의 장벽은 별도 검증이 필요하다.'
    validate_report_synthesis(synthesis,inputs,companies)


@pytest.mark.parametrize('defect', ['foreign_paragraph', 'invented_ref', 'unlinked_ref', 'no_analysis', 'no_limitation', 'no_opinion', 'duplicate_company', 'stale_report'])
def test_synthesis_cannot_drop_analysis_or_use_unconnected_sources(records, defect):
    companies, inputs, synthesis = inputs_and_synthesis(records)
    first = synthesis['companies'][0]
    if defect == 'foreign_paragraph':
        first['business_context']['report_paragraph_ids'] = [inputs[1]['paragraphs'][0]['paragraph_id']]
    elif defect == 'invented_ref':
        first['business_context']['evidence_ids'] = ['MM-E999']
    elif defect == 'unlinked_ref':
        first['business_context']['evidence_ids'] = ['MM-E2']
    elif defect in {'no_analysis', 'no_limitation', 'no_opinion'}:
        target = {'no_analysis': ('market_and_team', 'technology_and_execution'),
                  'no_limitation': ('risks_and_conditions',), 'no_opinion': ('individual_opinion',)}[defect]
        for field in target:
            first[field]['report_paragraph_ids'] = [inputs[0]['paragraphs'][0]['paragraph_id']]
    elif defect == 'duplicate_company':
        synthesis['companies'][1]['company'] = 'MOTOMIND'
    else:
        synthesis['source_reports'] = [{'company': 'MOTOMIND', 'trace_id': 'old', 'report_sha256': 'old'}]
    with pytest.raises(ValueError):
        validate_report_synthesis(synthesis, inputs, companies)


def test_company_analysis_reaches_final_body_and_fixed_score_table_remains_identical(records):
    companies, inputs, synthesis = inputs_and_synthesis(records)
    old = build_combined_report(records, portfolio_decision=portfolio())
    new = build_combined_report(records, portfolio_decision=portfolio(), report_synthesis=synthesis)
    assert '사업·고객 맥락' in new['markdown'] and '실사 후 재검토' in new['markdown']
    assert '기업 보고서의 종합 비교' in new['markdown']
    assert [line for line in old['markdown'].splitlines() if line.startswith('|')] == [
        line for line in new['markdown'].splitlines() if line.startswith('|')]
    assert new['deterministic_checks']['company_reports_synthesized'] is True
    assert new['company_report_inputs'][0]['report_markdown'] == inputs[0]['report_markdown']


def test_portfolio_receives_company_report_analysis_instead_of_only_three_facts(records):
    from portfolio_decision import select_portfolio_investment
    _, inputs, synthesis = inputs_and_synthesis(records)
    payloads = []
    selected = select_portfolio_investment(records, report_inputs=inputs, report_synthesis=synthesis,
        judge=lambda payload: payloads.append(payload) or portfolio(action='조건부 투자'))
    assert payloads[0]['company_reports'] == inputs
    assert payloads[0]['report_synthesis']['companies'] == synthesis['companies']
    assert payloads[0]['report_synthesis']['source_reports']
    assert selected['selected_company'] == 'Figure AI'


def test_saved_combined_report_preserves_full_inputs_and_reflection_map(records, tmp_path):
    _, inputs, synthesis = inputs_and_synthesis(records)
    payloads = []
    result = save_combined_report(records, tmp_path, portfolio_decision=portfolio(), report_synthesis=synthesis,
        quality_evaluator=lambda payload: payloads.append(payload) or {'passed': True, 'issues': []})
    import json
    metadata = json.loads(result['json'].read_text())
    assert metadata['report_synthesis']['reflection_map']
    assert metadata['company_reports'] == inputs
    assert payloads[0]['company_reports'] == inputs


def test_text_is_not_decoder_truncated_and_unfinished_sentence_is_rejected(records):
    from report_synthesis import SynthesisParagraph
    assert 'maxLength' not in SynthesisParagraph.model_json_schema()['properties']['text']
    companies, inputs, synthesis = inputs_and_synthesis(records)
    synthesis['cross_company_comparison'][0]['text'] = '완결되지 않고 끊긴 분석'
    with pytest.raises(ValueError, match='중간에서 끊겼'):
        validate_report_synthesis(synthesis, inputs, companies)


def test_valid_citation_records_actual_original_report_location_separately(records):
    companies, inputs, synthesis = inputs_and_synthesis(records)
    first = synthesis['companies'][0]
    first['individual_opinion']['evidence_ids'] = ['MM-E1']
    result = validate_report_synthesis(synthesis, inputs, companies)
    mapping = next(x for x in result['reflection_map'] if x['destination'] == 'company:MOTOMIND:individual_opinion')
    assert mapping['report_paragraph_ids'] != mapping['evidence_report_paragraph_ids']['MM-E1']
    assert inputs[0]['paragraphs'][0]['paragraph_id'] in mapping['evidence_report_paragraph_ids']['MM-E1']


@pytest.mark.parametrize('role', ['synthesis', 'portfolio', 'combined_judge'])
def test_final_roles_use_configured_model_and_bounded_reasoning(monkeypatch, role):
    from types import SimpleNamespace
    from unittest.mock import Mock
    import legacy_adapter
    from combined_quality import CombinedQualityReview
    from portfolio_decision import PortfolioOpinion
    from report_synthesis import CompanyReportSynthesis
    schema, setting = {'synthesis': (CompanyReportSynthesis, 'OPENAI_SYNTHESIS_MODEL'),
                       'portfolio': (PortfolioOpinion, 'OPENAI_PORTFOLIO_MODEL'),
                       'combined_judge': (CombinedQualityReview, 'OPENAI_COMBINED_JUDGE_MODEL')}[role]
    monkeypatch.setenv(setting, 'gpt-5.4')
    client = Mock()
    output = object()
    client.responses.parse.return_value = SimpleNamespace(output_parsed=output)
    monkeypatch.setattr(legacy_adapter, 'make_openai_client', lambda: client)
    assert legacy_adapter._structured_request(schema, 'test', {}) is output
    request = client.responses.parse.call_args.kwargs
    assert request['model'] == 'gpt-5.4'
    assert request['reasoning'] == {'effort': 'medium' if role == 'combined_judge' else 'low'}
    assert request['timeout'] == 120
