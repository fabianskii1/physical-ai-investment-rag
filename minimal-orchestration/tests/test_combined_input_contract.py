"""실제 원기업 본문→최종 입력→발행과 반영 증거 사이의 회귀 검사."""
from copy import deepcopy
from hashlib import sha256
import json
from unittest.mock import Mock

import pytest

import app
import combined_quality
from combined_report import save_combined_report, build_combined_report
from portfolio_decision import select_portfolio_investment
from report_synthesis import synthesize_company_reports, validate_report_synthesis
from test_combined_report import records, portfolio
from test_report_synthesis import inputs_and_synthesis
from test_combined_reflection import payload, reflection, COMPANIES
from test_combined_cli import save_state


@pytest.mark.parametrize('defect', ['wrong_key', 'body', 'hash', 'paragraph', 'company', 'empty'])
@pytest.mark.parametrize('stage', ['synthesis', 'selection'])
def test_wrong_or_stale_company_payload_fails_before_model(records, defect, stage):
    companies, reports, synthesis = inputs_and_synthesis(records)
    supplied = deepcopy(reports)
    if defect == 'wrong_key':
        supplied[0]['report'] = supplied[0].pop('report_markdown')
    elif defect == 'body':
        supplied[0]['report_markdown'] = '예전 보고서 본문'
    elif defect == 'hash':
        supplied[0]['report_sha256'] = 'stale'
    elif defect == 'paragraph':
        supplied[0]['paragraphs'][0]['text'] = '원기업 보고서와 다른 분석'
    elif defect == 'company':
        supplied[0]['company'] = 'Figure AI'
    else:
        supplied = []
    model = Mock(side_effect=AssertionError('잘못된 입력으로 API 호출 금지'))
    with pytest.raises(ValueError, match='입력 계약 불일치'):
        if stage == 'synthesis':
            synthesize_company_reports(companies, report_inputs=supplied, source_contexts=[], writer=model)
        else:
            select_portfolio_investment(records, report_inputs=supplied, report_synthesis=synthesis, judge=model)
    model.assert_not_called()


def test_missing_report_synthesis_can_never_publish_even_when_judge_passes(records, tmp_path):
    judge = Mock(return_value={'passed': True, 'issues': []})
    result = save_combined_report(records, tmp_path, quality_evaluator=judge)
    assert result['status'] == 'quality_failed'
    assert result['quality_stop_reason'] == 'company_report_synthesis_missing'
    assert not (tmp_path / 'final_investment_report.pdf').exists()
    judge.assert_not_called()


def test_actual_full_input_is_saved_with_matching_body_and_file_hashes(records, tmp_path):
    _, reports, _ = inputs_and_synthesis(records)
    _, actual, contexts, manifest = app._prepare_combined_inputs(records, tmp_path)
    saved = tmp_path / 'combined_report_inputs.json'
    snapshot = json.loads(saved.read_text())
    assert snapshot['company_reports'] == reports == actual
    assert snapshot['source_contexts'] == contexts
    assert len(snapshot['verified_results']) == len(snapshot['company_reports']) == 3
    assert snapshot['report_body_key'] == 'company_reports[].report_markdown'
    assert manifest['sha256'] == sha256(saved.read_bytes()).hexdigest()
    assert all(item['body_characters'] > 100 for item in manifest['companies'])


def test_exported_markdown_change_is_not_silently_ignored(tmp_path):
    for name in app.COMPANIES:
        save_state(tmp_path, name)
    (tmp_path / 'motomind.md').write_text('수정된 기업 보고서 본문')
    with pytest.raises(ValueError, match='Markdown과 State final_report'):
        app._load_company_records(tmp_path)


def test_newer_demo_state_does_not_replace_actual_company_report(tmp_path):
    for name in app.COMPANIES:
        save_state(tmp_path, name)
    (tmp_path / 'motomind_demo.state.json').write_text(json.dumps({'mode': 'demo', 'state': {}}))
    records = app._load_company_records(tmp_path)
    assert all(not record['state_path'].name.endswith('_demo.state.json') for record in records)


@pytest.mark.parametrize('defect', ['empty', 'source_id', 'final_id', 'source_quote', 'final_quote', 'other_company', 'wrong_kind'])
def test_boolean_true_and_plausible_ids_are_not_reflection_proof(defect):
    report = payload()
    checks = [reflection(name) for name in COMPANIES]
    proof = checks[0]['preservation_evidence'][0]
    if defect == 'empty':
        checks[0]['preservation_evidence'] = []
    elif defect == 'source_id':
        proof['source_paragraph_id'] = 'MM-P999'
    elif defect == 'final_id':
        proof['final_paragraph_id'] = 'P99999'
    elif defect == 'source_quote':
        proof['source_excerpt'] = '입력 원보고서에 존재하지 않는 구체적인 문장이다.'
    elif defect == 'final_quote':
        proof['final_excerpt'] = '최종 보고서에 실제로 들어 있지 않은 문장이다.'
    elif defect == 'other_company':
        proof['final_paragraph_id'] = checks[1]['preservation_evidence'][0]['final_paragraph_id']
        proof['final_excerpt'] = checks[1]['preservation_evidence'][0]['final_excerpt']
    else:
        proof['source_paragraph_id'] = 'MM-P003'
        proof['source_excerpt'] = report['company_reports'][0]['paragraphs'][2]['text']
    assert combined_quality.reflection_evidence_issues(report, checks)


def test_real_company_and_final_excerpts_support_all_three_aspects():
    assert combined_quality.reflection_evidence_issues(payload(), [reflection(name) for name in COMPANIES]) == []


def test_old_true_flags_without_actual_excerpts_cannot_pass_judge(monkeypatch):
    from test_combined_reflection import review
    import legacy_adapter
    checks = [reflection(name) for name in COMPANIES]
    for check in checks:
        check['preservation_evidence'] = []
    request = Mock(return_value=review(checks))
    monkeypatch.setattr(legacy_adapter, '_structured_request', request)
    result = combined_quality.evaluate_combined_quality(payload())
    assert result['passed'] is False and result['action'] == 'review'
    assert len(result['issues']) == 9
    assert request.call_count == 1


def test_invalid_judge_proof_does_not_trigger_report_writer(records, tmp_path):
    _, _, synthesis = inputs_and_synthesis(records)
    writer = Mock(side_effect=AssertionError('평가자 증거 오류를 보고서 Writer로 수정하면 안 된다'))
    result = save_combined_report(records, tmp_path, report_synthesis=synthesis,
        quality_evaluator=lambda _: {'passed': False, 'issues': ['평가자의 반영 증거 불일치'], 'action': 'review'},
        quality_reviser=writer)
    assert result['rewrite_count'] == 0 and result['quality_stop_reason'] == 'quality_review_required'
    writer.assert_not_called()


@pytest.mark.parametrize('field', ['executive_summary', 'comparison', 'business_context',
                                  'market_and_team', 'technology_and_execution',
                                  'risks_and_conditions', 'individual_opinion'])
def test_synthesis_cannot_silently_drop_paragraph_citations(records, field):
    companies, inputs, synthesis = inputs_and_synthesis(records)
    target = (synthesis[field] if field == 'executive_summary' else
              synthesis['cross_company_comparison'][0] if field == 'comparison' else synthesis['companies'][0][field])
    target['evidence_ids'] = []
    with pytest.raises(ValueError, match='원문 evidence_ids'):
        validate_report_synthesis(synthesis, inputs, companies)


def test_comparison_has_its_own_cross_company_sources_in_actual_markdown(records):
    _, _, synthesis = inputs_and_synthesis(records)
    decision = portfolio(action='조건부 투자')
    built = build_combined_report(records, report_synthesis=synthesis, portfolio_decision=decision)
    line = next(line for line in built['markdown'].splitlines() if line.startswith('다른 기업과 비교한 선택 이유:'))
    reason = next(line for line in built['markdown'].splitlines() if line.startswith('투자·보류 이유:'))
    assert all(f'[{prefix}-E1]' in line and f'[{prefix}-E1]' in reason for prefix in ('MM', 'RB', 'FG'))
    assert built['portfolio_decision']['evidence_ids'] == ['FG-E1']


def test_portfolio_cannot_omit_comparison_evidence_field(records):
    _, inputs, synthesis = inputs_and_synthesis(records)
    missing = portfolio()
    missing.pop('comparison_evidence_ids')
    with pytest.raises(ValueError, match='comparison_evidence_ids'):
        select_portfolio_investment(records, report_inputs=inputs, report_synthesis=synthesis,
                                    judge=lambda _: missing)


@pytest.mark.parametrize('field,bad', [
    ('report_paragraph_ids', 'RB-P016","RB-P032","RB-P074'),
    ('evidence_ids', 'MM-E1, RB-E2'),
    ('evidence_ids', 'MM-E1\nRB-E2'),
])
def test_schema_rejects_multiple_ids_packed_inside_one_string(field, bad):
    from report_synthesis import SynthesisParagraph
    value = {'text': '입력 보고서의 구체적인 판단을 보존한다.',
             'report_paragraph_ids': ['MM-P001'], 'evidence_ids': ['MM-E1']}
    value[field] = [bad]
    with pytest.raises(ValueError, match='pattern'):
        SynthesisParagraph.model_validate(value)
    assert 'pattern' in SynthesisParagraph.model_json_schema()['properties'][field]['items']


def test_summary_can_link_more_than_six_real_source_paragraphs():
    from report_synthesis import SynthesisParagraph
    ids = [f'{prefix}-P{i:03d}' for prefix in ('MM', 'RB', 'FG') for i in range(1, 5)]
    value = SynthesisParagraph(text='세 기업의 분석을 실제 문단에서 종합한다.', report_paragraph_ids=ids)
    assert value.report_paragraph_ids == ids
