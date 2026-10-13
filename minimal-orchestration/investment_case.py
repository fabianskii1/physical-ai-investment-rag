"""검증된 항목·현재 거래조건으로 최종 투자 제안을 구성한다.

모델은 의견과 중요한 항목을 선택한다. 코드는 출처 연결과 투자 전제조건을
검사하고 지지 사실·미확인 위험·실사 자료를 작성한다. 점수 임계값은 쓰지 않는다.
"""
from collections.abc import Mapping

from rag.report import NEXT_CHECKS
from rag.scorecard import SCORECARD

TERM_CRITERIA = tuple(SCORECARD['투자조건'])
CRITICAL_CRITERIA = ('핵심 작업 성능', '현장 안정성·안전성', '유상 매출·납품', '고객 재구매·확대')


def build_investment_case(state: Mapping, graded: Mapping, opinion: Mapping | None = None) -> dict:
    """긍정 등급의 실제 근거만 투자 포인트로 쓰고 U는 필요한 자료로 표시한다."""
    opinion = opinion or {}
    details = graded.get('score_details', {})
    claims = state.get('source_checked_claims', [])
    evidence = {f'E{i}': claim for i, claim in enumerate(claims, 1) if isinstance(claim, Mapping)}
    positive, risks = {}, []
    for criterion, detail in details.items():
        grade = detail.get('grade')
        refs = [ref for ref, claim in evidence.items() if claim in detail.get('evidence', [])]
        if type(grade) is int and grade > 0 and refs:
            ref = refs[0]
            positive[criterion] = {'criterion': criterion, 'grade': grade,
                                   'fact': evidence[ref].get('claim', ''), 'evidence_ids': [ref]}
    selected = opinion.get('supporting_criteria', [])
    selected = [name for name in selected if name in positive] if isinstance(selected, list) else []
    selected = list(dict.fromkeys(selected))
    for name in positive:
        if name not in selected:
            selected.append(name)
    supports = [positive[name] for name in selected[:3]]
    focus = opinion.get('risk_criteria', [])
    focus = focus if isinstance(focus, list) else []
    unknown = [name for name, detail in details.items() if detail.get('grade') is None]
    risk_names = list(dict.fromkeys([name for name in focus if name in unknown]
                                  + [name for name in CRITICAL_CRITERIA if name in unknown] + unknown))
    for name in risk_names[:3]:
        risks.append({'criterion': name, 'reason': '현재 검증 근거로는 U이며 실제 실패·부재를 뜻하지 않음',
                      'next_check': NEXT_CHECKS[name]})
    missing_terms = [name for name in TERM_CRITERIA if details.get(name, {}).get('grade') is None]
    missing_critical = [name for name in CRITICAL_CRITERIA if details.get(name, {}).get('grade') is None]
    negative_critical = [name for name in (*CRITICAL_CRITERIA, *TERM_CRITERIA)
                         if details.get(name, {}).get('grade') == 0]
    for name in reversed(negative_critical):
        refs = [ref for ref, claim in evidence.items() if claim in details[name].get('evidence', [])]
        if refs:
            risks.insert(0, {'criterion': name, 'basis': 'confirmed_negative',
                             'fact': evidence[refs[0]].get('claim', ''), 'evidence_ids': [refs[0]]})
    risks = risks[:3]
    conditions = []
    if missing_terms:
        conditions.append({'criterion': '현재 투자조건', 'required': '현재 거래의 기업가치·지분/권리·자금 사용 계획 확인',
                           'items': missing_terms})
    conditions.extend({'criterion': name, 'required': NEXT_CHECKS[name], 'items': [name]}
                      for name in missing_critical[:3])
    proposed = opinion.get('decision', graded.get('investment_decision', '근거 부족'))
    if not supports or not graded.get('scorable_weight'):
        action = '판단 유보'
        rationale = '긍정적인 투자 포인트를 뒷받침할 검증 근거가 부족하여 투자 여부를 판단할 수 없다.'
    elif negative_critical:
        action = '투자 보류'
        rationale = '핵심 사업화 항목 또는 현재 거래조건에 명시적 부정 근거가 확인되어 해당 문제를 해결하기 전 투자 추진을 보류한다.'
    elif proposed == '우선 검토':
        action = '조건부 투자 검토' if missing_terms or missing_critical else '투자 추진 검토'
        rationale = ('아래 확인 사실은 이 기업을 검토할 이유다. ' +
                     ('현재 투자조건 또는 핵심 사업화 근거가 미확인이므로 조건 확인 전 즉시 투자 제안은 유보한다.'
                      if action == '조건부 투자 검토' else
                      '핵심 검토 자료가 확인돼 다음 투자 심사 단계로 진행할 수 있다. 등급·점수만으로 투자 실행을 결정하지 않는다.'))
    elif proposed == '보류':
        action = '투자 보류'
        rationale = '확인된 투자 포인트가 있으나 현재의 위험·미확인 사항을 고려한 종합 의견은 보류다.'
    else:
        action = '판단 유보'
        rationale = '일부 긍정적 사실은 확인됐으나 최종 투자 판단을 뒷받침할 근거가 충분하지 않다.'
    return {'action': action, 'rationale': rationale, 'model_opinion': proposed,
            'supporting_facts': supports, 'key_risks': risks, 'required_conditions': conditions,
            'decision_policy': {'score_threshold': None, 'critical_criteria': list(CRITICAL_CRITERIA),
                                'current_terms_required': True, 'confirmed_critical_zero_requires_hold': True},
            'full_opinion_reason': opinion.get('decision_reason', graded.get('decision_reason', ''))}
