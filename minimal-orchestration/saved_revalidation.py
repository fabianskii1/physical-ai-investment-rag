"""이미 확보한 검증 주장만 원본 PDF에 다시 대조한다. 워커·검색·새 웹 읽기는 수행하지 않는다."""

from collections.abc import Mapping
from copy import deepcopy

from criteria_coverage import build_coverage, resolved_gaps


ANALYSIS_KEYS = ("bigtech_analysis", "technology_analysis", "market_competition_analysis")


def recover_worker_research(state: Mapping, evidence_checker) -> dict:
    """확보된 워커 원출력 전체를 다시 검사한다. 검색·워커를 재호출하지 않는다.

    로더 정규화 오류로 과거 탈락한 정확한 인용도 현재 원문에서 재검증한다.
    이전 채점과 보고서는 폐기해야 하며, 원본 워커 이력은 그대로 남긴다.
    """
    research=deepcopy(state['research_result'])
    payload={**deepcopy(dict(state)), **{k:research.get(k,{}) for k in ANALYSIS_KEYS}}
    checked=evidence_checker(payload)
    if not isinstance(checked, Mapping) or not isinstance(checked.get('source_checked_claims'),list):
        raise ValueError('워커 원문 재검증 형식 오류')
    research.update(deepcopy(dict(checked)))
    research['evidence_gaps']=resolved_gaps(research,research['source_checked_claims'],checked['evidence_gaps'])
    research.update(build_coverage(research,research['source_checked_claims'],state.get('investment_terms')))
    for key in ('last_verified_signature','last_verified_round','last_verified_task_ids','score_review_issues',
                'report_revision_feedback'):
        research.pop(key,None)
    research['worker_source_recovery']={'original_trace_id':state['trace_id'],
        'worker_or_retrieval_executed':False,'new_web_collection_executed':False,
        'input_worker_claims':sum(len(research.get(k,{}).get('claims',[])) for k in ANALYSIS_KEYS),
        'previous_verified_claims':len(state['research_result'].get('source_checked_claims',[])),
        'output_verified_claims':len(research['source_checked_claims']),
        'previous_gap_count':len(state['research_result'].get('evidence_gaps',[])),
        'investment_regrading_required':True,'report_regeneration_required':True}
    return research


def revalidate_saved_research(state: Mapping, evidence_checker) -> dict:
    """saved checked claims를 재검증하고 현재 E-ID에 맞는 조사·coverage 결과를 반환한다.

    기존 투자 결과·보고서는 유효성이 사라진다. 호출자는 investment_result와
    report_evaluation을 비운 뒤 새 근거로 재채점·Writer·Evaluator를 실행해야 한다.
    원래 State, workers, catalog를 수정하지 않고 추가 워커를 호출하지 않는다.
    """
    if not isinstance(state, Mapping) or not callable(evidence_checker):
        raise ValueError("저장 State와 원문 검증 함수를 전달하세요")
    research = state.get("research_result")
    catalog, company = state.get("document_catalog"), state.get("company_info")
    if not isinstance(research, Mapping) or not isinstance(catalog, Mapping) or not isinstance(company, Mapping):
        raise ValueError("저장된 조사 결과·문서 카탈로그·기업 정보가 필요합니다")
    previous = research.get("source_checked_claims")
    if not isinstance(previous, list) or any(not isinstance(claim, Mapping) for claim in previous):
        raise ValueError("저장된 검증 주장 목록이 올바르지 않습니다")
    analyses = {key: {"claims": []} for key in ANALYSIS_KEYS}
    manual = []
    for claim in previous:
        role = claim.get("analysis")
        if role not in (*ANALYSIS_KEYS, "investment_terms"):
            raise ValueError("저장된 검증 주장의 분석 역할이 올바르지 않습니다")
        item = deepcopy(dict(claim))
        chosen = item.get("source")
        if isinstance(chosen, Mapping):
            # 과거 검증에서 실제 채택한 원문 페이지만 읽는다. 미채택 링크 재탐색은 하지 않는다.
            item["source_refs"] = [dict(chosen)]
        if role == "investment_terms":
            manual.append(item)
        else:
            analyses[role]["claims"].append(item)
    supplied_terms = state.get("investment_terms")
    terms = {"claims": manual} if supplied_terms is not None else None
    payload = {"company_info": deepcopy(dict(company)), "document_catalog": deepcopy(dict(catalog)),
               "decision_policy": deepcopy(state.get("decision_policy", {})),
               "investment_terms": terms, **analyses}
    checked = evidence_checker(payload)
    if not isinstance(checked, Mapping) or not isinstance(checked.get("source_checked_claims"), list):
        raise ValueError("원문 재검증 결과 형식이 올바르지 않습니다")
    if any(not isinstance(claim, Mapping) for claim in checked["source_checked_claims"]):
        raise ValueError("원문 재검증의 주장 목록 형식이 올바르지 않습니다")
    result = deepcopy(dict(research))
    result.update(deepcopy(analyses))
    result.update(deepcopy(dict(checked)))
    gaps = research.get("evidence_gaps", [])
    current_gaps = checked.get("evidence_gaps", [])
    if not isinstance(gaps, list) or not isinstance(current_gaps, list):
        raise ValueError("저장된 또는 현재 근거 부족 목록 형식이 올바르지 않습니다")
    combined = []
    for gap in [*gaps, *current_gaps]:
        if gap not in combined:
            combined.append(deepcopy(gap))
    result["evidence_gaps"] = resolved_gaps(result, result["source_checked_claims"], combined)
    result.update(build_coverage(result, result["source_checked_claims"], supplied_terms))
    # 이전 캐시 서명·이전 점수 검토는 새 주장 목록과 E-ID에 적용될 수 없다.
    for key in ("last_verified_signature", "last_verified_round", "last_verified_task_ids", "score_review_issues"):
        result.pop(key, None)
    result["saved_source_revalidation"] = {
        "input_claim_count": len(previous), "output_claim_count": len(result["source_checked_claims"]),
        "worker_or_retrieval_executed": False, "new_web_collection_executed": False,
        "original_trace_id": state.get("trace_id"), "investment_regrading_required": True,
        "report_regeneration_required": True,
    }
    return result
