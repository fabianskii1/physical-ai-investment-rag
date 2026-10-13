from copy import deepcopy

import pytest
from pypdf import PdfReader

from app import score_snapshot
from company_brief import save_company_brief
from investment_case import build_investment_case
from rag.scorecard import SCORECARD, calculate_scorecard


def record(tmp_path):
    claim = {'claim': '회사의 공개 원문은 제조 현장용 로봇 제품 개발을 설명한다.',
             'source': {'doc_id': 'DOC', 'page': 1}, 'evidence_quote': '로봇 제품 개발'}
    scored = calculate_scorecard({name: 2 if name == '핵심 작업 성능' else None
                                 for group in SCORECARD.values() for name in group})
    details = {name: {**item, 'evidence': [claim] if item['grade'] is not None else [], 'reason': '검증'}
               for name, item in scored['items'].items()}
    investment = {'score_details': details, 'total_score': scored['earned_score'],
                  'scorable_weight': scored['scorable_weight'], 'unknown_weight': scored['unknown_weight']}
    investment['investment_case'] = build_investment_case({'source_checked_claims': [claim]}, investment)
    state = {'company_info': {'name': 'Figure AI'}, 'trace_id': 'run-brief', 'status': 'partial',
             'recheck_count': 3, 'research_result': {'source_checked_claims': [claim]},
             'investment_result': investment}
    return {'state': state, 'scores': score_snapshot(state, mode='live', inputs={}, index_info={},
                                                    report_path=tmp_path / 'detail.pdf')}


def test_brief_is_one_a4_page_keeps_score_unknown_and_original_state(tmp_path):
    source = record(tmp_path)
    original = deepcopy(source)
    result = save_company_brief(source, tmp_path)
    checks = result['pdf_checks']
    assert checks['pages'] == 1 and checks['all_a4'] and checks['text_within_margins']
    assert checks['margins_mm'] == 20
    text = PdfReader(result['pdf']).pages[0].extract_text()
    assert '3점' in text and 'U 배점 94' in text
    assert source == original


def test_brief_rejects_modified_fact_using_real_id(tmp_path):
    source = record(tmp_path)
    source['state']['investment_result']['investment_case']['supporting_facts'][0]['fact'] = '매출 확정'
    with pytest.raises(ValueError, match='검증되지 않은'):
        save_company_brief(source, tmp_path)
