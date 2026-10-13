"""점수 임계값 없이 검증 근거와 거래조건으로 투자 제안을 구성한다."""
from copy import deepcopy

import pytest

from investment_case import CRITICAL_CRITERIA, TERM_CRITERIA, build_investment_case
from rag.report import report
from rag.scorecard import SCORECARD, calculate_scorecard


def case_inputs(*, ready=False):
    grades = {name: None for items in SCORECARD.values() for name in items}
    grades['관련 전문성'] = 2
    if ready:
        grades.update({name: 2 for name in (*CRITICAL_CRITERIA, *TERM_CRITERIA)})
    claims = [{'claim': f'{name}의 시험용 검증 사실', 'criterion_id': name,
               'source': {'doc_id': 'D1', 'page': 1}, 'evidence_quote': '테스트 원문'}
              for name, grade in grades.items() if grade is not None]
    scored = calculate_scorecard(grades)
    details = {name: {**item, 'reason': '시험용 등급 판정',
                     'evidence': [claim for claim in claims if claim['criterion_id'] == name]}
               for name, item in scored['items'].items()}
    state = {'company_info': {'name': '시험 기업'}, 'source_checked_claims': claims,
             'document_catalog': {'D1': {'file_path': 'test.pdf', 'title': '시험 원문'}},
             'investment_terms': {'claims': []} if ready else None}
    graded = {'score_details': details, 'scorable_weight': scored['scorable_weight'],
              'unknown_weight': scored['unknown_weight'], 'total_score': scored['earned_score'],
              'investment_decision': '우선 검토', 'decision_reason': '시험용 종합 의견'}
    return state, graded


def test_missing_terms_or_commercial_evidence_forces_conditional_case():
    state, graded = case_inputs()
    case = build_investment_case(state, graded, {'decision': '우선 검토'})
    assert case['action'] == '조건부 투자 검토'
    assert case['required_conditions'][0]['criterion'] == '현재 투자조건'
    assert case['supporting_facts'][0]['fact'] == state['source_checked_claims'][0]['claim']
    assert case['supporting_facts'][0]['evidence_ids'] == ['E1']
    assert case['decision_policy']['score_threshold'] is None


def test_verified_conditions_can_proceed_without_arbitrary_70_point_cutoff():
    state, graded = case_inputs(ready=True)
    assert graded['total_score'] < 70
    case = build_investment_case(state, graded, {'decision': '우선 검토'})
    assert case['action'] == '투자 추진 검토'
    assert not case['required_conditions']


def test_critical_confirmed_negative_blocks_positive_model_opinion():
    state, graded = case_inputs(ready=True)
    graded['score_details']['현장 안정성·안전성']['grade'] = 0
    case = build_investment_case(state, graded, {'decision': '우선 검토'})
    assert case['action'] == '투자 보류'
    assert case['key_risks'][0]['basis'] == 'confirmed_negative'


def test_all_unknown_does_not_generate_supporting_facts_or_investment_recommendation():
    state, graded = case_inputs()
    for detail in graded['score_details'].values():
        detail.update(grade=None, evidence=[])
    graded['scorable_weight'] = 0
    case = build_investment_case(state, graded, {'decision': '우선 검토'})
    assert case['action'] == '판단 유보'
    assert case['supporting_facts'] == []


def test_report_prints_investment_case_with_actual_refs_and_conditions():
    state, graded = case_inputs()
    state.update(graded)
    state['investment_case'] = build_investment_case(state, graded, {'decision': '우선 검토'})
    markdown = report(state, writer=lambda payload: {})['final_report']
    assert '### 최종 투자 제안' in markdown
    assert '**조건부 투자 검토**' in markdown
    assert '### 왜 이 기업을 검토하는가' in markdown
    assert '관련 전문성의 시험용 검증 사실 [E1]' in markdown
    assert '현재 거래의 기업가치·지분/권리·자금 사용 계획 확인' in markdown
    assert markdown.count('\n## ') == 5


def test_case_cannot_replace_verified_fact_with_invented_fact_even_with_existing_id():
    state, graded = case_inputs()
    state.update(graded)
    case = build_investment_case(state, graded)
    case['supporting_facts'][0]['fact'] = '매출 999억원으로 투자해야 한다.'
    state['investment_case'] = deepcopy(case)
    with pytest.raises(ValueError, match='최종 투자 제안의 지지 사실'):
        report(state, writer=lambda payload: {})
