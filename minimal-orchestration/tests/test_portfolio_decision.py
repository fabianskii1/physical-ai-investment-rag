import pytest

from portfolio_decision import validate_portfolio_decision
from investment_case import CRITICAL_CRITERIA, TERM_CRITERIA


def company(*, ready=True, critical_grade=None):
    details = {name: {'grade': critical_grade} for name in (*CRITICAL_CRITERIA, *TERM_CRITERIA)}
    details['관련 전문성'] = {'grade': 2}
    return {'company': 'Figure AI', 'prefix': 'FG', 'ready': ready, 'details': details,
            'score_refs': {name: ['E1'] if name == '관련 전문성' else [] for name in details},
            'evidence': {'E1': {'claim': '원문에서 확인한 팀 이력'}}}


def opinion():
    return {'selected_company': 'Figure AI', 'action': '투자', 'reason': '검증 팀 이력',
            'comparison_reason': '검증 범위와 핵심 미확인을 고려', 'evidence_ids': ['FG-E1'],
            'required_conditions': []}


def test_missing_current_terms_and_critical_data_require_conditional_investment():
    result = validate_portfolio_decision([company()], opinion())
    assert result['selected_company'] == 'Figure AI' and result['action'] == '조건부 투자'
    assert any('기업가치' in value for value in result['required_conditions'])
    assert any('유상 매출' in value for value in result['required_conditions'])
    assert result['source_grounded_supports'][0]['fact'] == '원문에서 확인한 팀 이력'
    assert result['policy']['selection_by_highest_score'] is False


@pytest.mark.parametrize('change', ['foreign_ref', 'invented_ref', 'unready', 'negative'])
def test_invalid_support_quality_or_confirmed_failure_blocks_investment(change):
    target, proposed = company(), opinion()
    if change == 'foreign_ref':
        proposed['evidence_ids'] = ['MM-E1']
    elif change == 'invented_ref':
        proposed['evidence_ids'] = ['FG-E999']
    elif change == 'unready':
        target['ready'] = False
    else:
        target['details'][CRITICAL_CRITERIA[0]]['grade'] = 0
    with pytest.raises(ValueError):
        validate_portfolio_decision([target], proposed)


def test_investment_conditions_cannot_contain_truncated_json_or_decoder_length_caps():
    from portfolio_decision import PortfolioOpinion
    schema = PortfolioOpinion.model_json_schema()['properties']
    assert 'maxLength' not in schema['reason']
    assert 'maxLength' not in schema['required_conditions']['items']
    proposed = opinion()
    proposed['required_conditions'] = ['고객 갱신 자료를 확인한다","현재 세']
    with pytest.raises(ValueError, match='JSON 표기'):
        validate_portfolio_decision([company()], proposed)


def test_required_commercial_diligence_is_not_duplicated():
    proposed = opinion()
    proposed['required_conditions'] = ['유상 계약·인도·매출 인식 자료와 실제 고객 확인',
                                       '동일 고객의 유상 갱신·재구매·도입 확대 확인 자료']
    result = validate_portfolio_decision([company()], proposed)
    assert len([x for x in result['required_conditions'] if '유상 계약' in x]) == 1
    assert len([x for x in result['required_conditions'] if '재구매' in x]) == 1
