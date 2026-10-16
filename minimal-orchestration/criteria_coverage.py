"""조사한 항목과 원문 검증을 통과한 항목을 구분하여 부족한 근거를 찾는다."""

import json

from criteria_registry import CRITERIA_REGISTRY, normalize_criterion


ANALYSIS_KEYS = {"bigtech": "bigtech_analysis", "technology": "technology_analysis",
                 "market": "market_competition_analysis"}


def _claim_key(claim: dict) -> tuple:
    refs = claim.get("source_refs", [])
    if not isinstance(refs, list):
        refs = []
    sources = sorted(json.dumps(ref, sort_keys=True, ensure_ascii=False) for ref in refs
                     if isinstance(ref, dict))
    return (normalize_criterion(claim.get("criterion_id") or claim.get("criterion")),
            str(claim.get("claim", "")).strip(), tuple(sources))


def merge_analysis(previous: dict, current: dict) -> dict:
    """재조사 주장만 추가하고, 이전 라운드의 성공한 주장을 보존한다.

    동일 항목·주장·출처는 중복으로 저장하지 않는다. 같은 주장이 확인 상태로
    개선되면 새 결과를 사용한다. 링크 오류는 최신 시도만 현재 미달로 취급한다.
    """
    if not isinstance(previous, dict):
        previous = {}
    merged = {**previous, **current}
    claims, positions = [], {}
    previous_claims, current_claims = previous.get("claims", []), current.get("claims", [])
    if not isinstance(previous_claims, list) or not isinstance(current_claims, list):
        # 기존 원문 Checker가 invalid_claims로 처리하게 형식 오류를 유지한다.
        return merged
    for item in [*previous_claims, *current_claims]:
        if not isinstance(item, dict):
            continue
        key = _claim_key(item)
        if key not in positions:
            positions[key] = len(claims)
            claims.append(dict(item))
        elif item.get("status") in {"confirmed", "verified", "company_claim"}:
            claims[positions[key]] = dict(item)
    merged["claims"] = claims
    merged["attempted_criteria"] = list(dict.fromkeys(
        [*previous.get("attempted_criteria", []), *current.get("attempted_criteria", [])]))
    merged['reviewed_criteria'] = {**previous.get('reviewed_criteria', {}), **current.get('reviewed_criteria', {})}
    # 카탈로그의 실제 충돌 검사는 Synthesizer가 수행하므로 여기서는 기존 값 우선.
    current_docs, previous_docs = current.get("source_documents", {}), previous.get("source_documents", {})
    merged["source_documents"] = {**(current_docs if isinstance(current_docs, dict) else {}),
                                  **(previous_docs if isinstance(previous_docs, dict) else {})}
    return merged


def record_attempts(research: dict, task: dict, round_number: int, analysis: dict) -> None:
    """성공한 워커 호출의 항목별 검색 시도를 기록한다. 실패한 작업은 조사 아님."""
    attempted = task.get("criteria", []) or analysis.get("attempted_criteria", [])
    # 항목 지정이 없는 예전 어댑터의 경우 실제 반환한 주장 항목만 조사로 센다.
    if not attempted:
        claims = analysis.get("claims", [])
        attempted = [claim.get("criterion_id") for claim in (claims if isinstance(claims, list) else [])
                     if isinstance(claim, dict)]
    records = dict(research.get("criterion_attempts", {}))
    for value in attempted:
        criterion_id = normalize_criterion(value)
        if criterion_id is None:
            continue
        entries = list(records.get(criterion_id, []))
        entry = {"round": round_number, "task_id": task["id"], "worker": task["worker"],
                 "query": task["query"]}
        if entry not in entries:
            entries.append(entry)
        records[criterion_id] = entries
    research["criterion_attempts"] = records


def build_coverage(research: dict, checked_claims: list[dict], investment_terms=None) -> dict:
    """21항목별 조사 여부·검증 근거·미달 이유를 계산한다.

    evidence_ids는 보고서와 같은 source_checked_claims 순서의 E1.. 번호다.
    근거가 존재한다는 사실만 나타내며, 해당 항목의 등급은 투자 Judge가 판단한다.
    """
    evidence = {key: [] for key in CRITERIA_REGISTRY}
    for index, claim in enumerate(checked_claims, 1):
        if not isinstance(claim, dict):
            continue
        criterion_id = normalize_criterion(claim.get("criterion_id") or claim.get("criterion"))
        if criterion_id:
            evidence[criterion_id].append(f"E{index}")
    coverage, missing = {}, []
    attempts = research.get("criterion_attempts", {})
    for criterion_id, item in CRITERIA_REGISTRY.items():
        entries = attempts.get(criterion_id, [])
        ids = evidence[criterion_id]
        role_result = research.get(ANALYSIS_KEYS.get(item['worker'], ''), {})
        review = role_result.get('reviewed_criteria', {}).get(criterion_id, {})
        review_complete = review.get('source_review_complete') is True
        if not item["researchable"] and investment_terms is None:
            status, reason = "manual_missing", "이번 거래의 투자조건을 사람이 입력하지 않아 U로 남깁니다."
        elif ids:
            status, reason = "supported", "원문 검증을 통과한 근거가 있습니다. 등급 충족 여부는 별도 판단합니다."
        elif not item["researchable"]:
            status, reason = "manual_unverified", "입력한 투자조건에 검증 가능한 원문 근거가 부족합니다."
        elif entries and review_complete and review.get('only_unverified') is True:
            status, reason = 'reviewed_unavailable', '등록된 원문 핵심 페이지를 검토하고 미확인 사유를 반환했습니다. U를 보존하며 자동 반복하지 않습니다.'
        elif entries:
            status, reason = "searched_unverified", "검색·분석했지만 항목을 직접 지지하는 원문 근거를 확보하지 못했습니다."
            missing.append(criterion_id)
        else:
            status, reason = "not_searched", "이 평가항목을 대상으로 한 검색·분석이 아직 수행되지 않았습니다."
            missing.append(criterion_id)
        coverage[criterion_id] = {**item, "status": status, "attempted": bool(entries),
                                  "attempt_count": len(entries), "evidence_ids": ids,
                                  'source_review_complete': review_complete,
                                  "reason": reason}
    return {"coverage_by_criterion": coverage, "missing_criteria": missing,
            "research_criteria_count": sum(item["researchable"] for item in CRITERIA_REGISTRY.values()),
            "attempted_criteria_count": sum(item["researchable"] and item["attempted"] for item in coverage.values()),
            "supported_criteria_count": sum(item["researchable"] and bool(item["evidence_ids"]) for item in coverage.values())}


def coverage_feedback(coverage: dict) -> list[str]:
    """다음 계획에서 워커·항목·보완 질문을 정확히 선택할 수 있게 한다."""
    return [f"[{item['worker']}/{key}] {item['label']}: {item['reason']} "
            f"보완 조사: {item['question']} (검색 시도 {item['attempt_count']}회)"
            for key, item in coverage.items()
            if item["researchable"] and item["status"] not in {"supported", 'reviewed_unavailable'}]


def resolved_gaps(research: dict, checked_claims: list[dict], gaps: list[dict]) -> list[dict]:
    """후속 근거로 해결된 과거의 '미확인' 경고만 현재 재검토 사유에서 제외한다.

    반증·부분 지지·잘못된 인용은 새로운 지지 근거가 있어도 삭제하지 않는다.
    """
    supported = {normalize_criterion(claim.get("criterion_id") or claim.get("criterion"))
                 for claim in checked_claims if isinstance(claim, dict)} - {None}
    lookup = {}
    for key in ANALYSIS_KEYS.values():
        claims = research.get(key, {}).get("claims", [])
        for claim in claims if isinstance(claims, list) else []:
            if isinstance(claim, dict):
                criterion_id = normalize_criterion(claim.get("criterion_id") or claim.get("criterion"))
                if criterion_id:
                    lookup[(key, claim.get("claim"))] = criterion_id
    current = []
    stale_reasons = {"unconfirmed_claim", "missing_source", "absence_not_proven"}
    for gap in gaps:
        criterion_id = normalize_criterion(gap.get("criterion_id")) or lookup.get(
            (gap.get("analysis"), gap.get("claim")))
        if criterion_id in supported and gap.get("reason") in stale_reasons:
            continue
        current.append({**gap, **({"criterion_id": criterion_id} if criterion_id else {})})
    return current
