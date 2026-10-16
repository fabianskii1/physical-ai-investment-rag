"""첨부 설계서의 6개 영역·21개 항목·100점 Scorecard로 투자 의견을 만든다.

등급 판정과 의견은 검증 근거를 받은 모델이 수행하고, 배점·U·인용·수동
투자조건 제약은 코드로 검증한다. 임의의 70점선이나 U 재정규화는 사용하지 않는다.
"""

from collections.abc import Callable, Mapping
import re

from rag.scorecard import SCORECARD, calculate_scorecard
from criteria_registry import normalize_criterion, registry_payload


CRITERIA = [name for items in SCORECARD.values() for name in items]
TERM_CRITERIA = set(SCORECARD["투자조건"])
DECISIONS = {"우선 검토", "보류", "근거 부족"}
COMMERCIAL_NAMES = {"유상 매출·납품", "대기업 계약·납품 단계", "고객 재구매·확대"}
COMPANY_ALIASES = {"figureai": {"Figure", "Figure AI"}, "robros": {"ROBROS", "로브로스"},
                   "motomind": {"MOTOMIND", "모토마인드", "주식회사 모토마인드"}}


def _unknown(reason: str) -> dict:
    return {"grade": None, "reason": reason, "evidence_ids": []}


def _company_key(value: str) -> str:
    return re.sub(r"[^\w]", "", value.casefold())


def _commercial_stage(claim: Mapping, company: Mapping) -> tuple[str | None, bool]:
    """원문에서 재검증된 판매 방향과 실제 거래 단계만 채점 근거로 사용한다."""
    facts, validation = claim.get("commercial_facts"), claim.get("commercial_fact_validation")
    if not isinstance(facts, Mapping) or not isinstance(validation, Mapping):
        return None, False
    if (validation.get("verdict") != "supported" or validation.get("all_core_facts_supported") is not True
            or not isinstance(validation.get("quote"), str)
            or not validation["quote"].strip()):
        return None, False
    for key in ("seller", "buyer", "offering", "stage"):
        fact = facts.get(key)
        if not isinstance(fact, Mapping) or not isinstance(fact.get("value"), str):
            return None, False
        if not isinstance(fact.get("quote"), str) or not fact["quote"].strip():
            return None, False
        if key != "stage" and (not fact["value"].strip()
                or " ".join(fact["value"].split()).casefold() not in " ".join(fact["quote"].split()).casefold()):
            return None, False
    name = company.get("name", "")
    if not isinstance(name, str) or not name.strip():
        return None, False
    aliases = {name} | COMPANY_ALIASES.get(_company_key(name), set())
    supplied_aliases = company.get("aliases", [])
    if isinstance(supplied_aliases, list):
        aliases.update(value for value in supplied_aliases if isinstance(value, str) and value.strip())
    if _company_key(facts["seller"]["value"]) not in {_company_key(value) for value in aliases}:
        # 대상 회사가 공급자의 컴퓨팅·부품을 구매한 거래는 회사 매출·제품 납품이 아니다.
        return None, False
    if _company_key(facts["seller"]["value"]) == _company_key(facts["buyer"]["value"]):
        return None, False
    repeat = facts.get("same_customer_repeat")
    confirmed_repeat = (isinstance(repeat, Mapping) and repeat.get("value") is True
                        and isinstance(repeat.get("quote"), str) and bool(repeat["quote"].strip()))
    return facts["stage"]["value"], confirmed_repeat


def _commercial_support(name: str, grade: int, ids: list[str], evidence: Mapping,
                        company: Mapping) -> list[str]:
    """세 거래 항목만 고정 정책으로 확인한다. 타 18항목의 새 등급 기준은 만들지 않는다."""
    supported = []
    for ref in ids:
        claim = evidence[ref]
        if normalize_criterion(claim.get("criterion_id")) != normalize_criterion(name):
            continue
        stage, repeated = _commercial_stage(claim, company)
        if name == "유상 매출·납품":
            allowed = grade > 0 and stage in {"paid_delivery", "paid_expansion"}
        elif name == "고객 재구매·확대":
            allowed = grade > 0 and stage == "paid_expansion" and repeated
        else:
            # 설계 PDF의 관계 단계별 등급을 그대로 적용한다. 확대 의향은 4가 아니다.
            allowed = (claim.get("analysis") == "bigtech_analysis"
                       and {"no_relation": 0, "mou": 1, "pilot": 2,
                            "paid_delivery": 3, "paid_expansion": 4}.get(stage) == grade
                       and (stage != "paid_expansion" or repeated))
        if allowed:
            supported.append(ref)
    return supported


def evaluate_investment(state: dict, evaluator: Callable[[dict], dict]) -> dict:
    """기존 보고서 함수와 호환되는 점수·근거·투자 의견 사전을 반환한다."""
    claims = state.get("source_checked_claims", [])
    if not isinstance(claims, list):
        claims = []
    # 기존 report.py와 동일한 E-ID를 유지한다. 잘못된 사전은 근거에서 제외한다.
    evidence = {f"E{i}": item for i, item in enumerate(claims, 1) if isinstance(item, Mapping)}
    ready = state.get("evidence_status") == "scorable" and bool(evidence)
    by_name = {name: _unknown("검증된 근거가 없거나 판정이 누락되어 U입니다.") for name in CRITERIA}
    model_result = {}
    assessment_rejections = {}
    if ready:
        catalog = state.get("document_catalog", {})
        if not isinstance(catalog, Mapping):
            catalog = {}
        model_result = evaluator({
            "company_name": state.get("company_info", {}).get("name", ""),
            "criteria": CRITERIA, "scorecard": SCORECARD,
            "criterion_guidance": registry_payload(),
            "verified_evidence": [{**item, "id": key} for key, item in evidence.items()],
            "source_catalog": {doc_id: {key: document.get(key) for key in (
                "title", "publisher", "source_url", "publication_date", "published_at", "source_kind")}
                for doc_id, document in catalog.items()
                if isinstance(document, Mapping)},
            "manual_investment_terms": state.get("investment_terms"),
            'grading_feedback': state.get('feedback', []),
        })
        if not isinstance(model_result, Mapping):
            model_result = {}
        assessments = model_result.get("assessments", [])
        seen, duplicates = set(), set()
        for item in assessments if isinstance(assessments, list) else []:
            if (not isinstance(item, Mapping) or not isinstance(item.get("criterion"), str)
                    or item["criterion"] not in by_name):
                continue
            name = item["criterion"]
            if name in seen:
                duplicates.add(name)
                continue
            seen.add(name)
            grade, reason, ids = item.get("grade"), item.get("reason"), item.get("evidence_ids")
            if not isinstance(reason, str) or not reason.strip():
                by_name[name] = _unknown("판단 이유가 없어 U입니다.")
                continue
            if grade is None:
                by_name[name] = _unknown(reason)
                continue
            if "anchor_grounded" in item and item["anchor_grounded"] is not True:
                by_name[name] = _unknown("평가항목의 의미 또는 선택한 등급 단계를 원문 근거가 지지하지 않아 "
                                        "U입니다. 기존 판정 사유: " + reason)
                continue
            if isinstance(grade, bool) or not isinstance(grade, int) or not 0 <= grade <= 4:
                by_name[name] = _unknown("잘못된 등급이어서 U입니다.")
                continue
            if not isinstance(ids, list) or not ids or any(
                not isinstance(ref, str) or ref not in evidence for ref in ids
            ):
                by_name[name] = _unknown("검증 근거와 연결되지 않아 U입니다.")
                continue
            if state.get("decision_policy", {}).get("require_criteria_coverage") and not any(
                normalize_criterion(evidence[ref].get("criterion_id")) == normalize_criterion(name)
                for ref in ids
            ):
                by_name[name] = _unknown("이 평가항목에 해당하는 검증 근거가 연결되지 않아 U입니다.")
                continue
            if grade == 0 and item.get("explicit_negative_confirmed") is not True:
                # 플래그는 최소 형식 조건이다. 실제 반증의 의미는 근거·보고서 Judge가 검사한다.
                by_name[name] = _unknown("명시적 부정 근거를 확인하지 못해 U입니다.")
                continue
            if name == "대기업 실제 지분·CVC 투자" and grade not in {0, 4}:
                by_name[name] = _unknown("실제 지분투자는 확인 4점·부재 확인 0점·미확인 U만 허용합니다.")
                continue
            if name in {"대기업 실제 지분·CVC 투자", "대기업 계약·납품 단계"} and not any(
                evidence[ref].get("analysis") == "bigtech_analysis" for ref in ids
            ):
                by_name[name] = _unknown("빅테크 관계를 검증한 근거가 없어 U입니다.")
                continue
            if name in COMMERCIAL_NAMES:
                ids = _commercial_support(name, grade, ids, evidence, state.get("company_info", {}))
                if not ids:
                    by_name[name] = _unknown(
                        "원문에서 대상 회사의 판매·공급 방향과 실제 거래 단계를 확인하지 못했거나 "
                        "등급이 검증 단계와 일치하지 않아 U입니다. 구매·투자·계획·첫 계약은 "
                        "회사 매출·유상 납품 확대·동일 고객 재구매로 바꾸어 평가하지 않습니다.")
                    continue
            from report_assertions import assertion_scope_issues
            reason_issues = assertion_scope_issues('판정 이유: ' + reason + ' ' +
                ' '.join(f'[{ref}]' for ref in ids), evidence)
            if reason_issues:
                assessment_rejections[name] = {'grade':grade,'reason':reason,'evidence_ids':ids,'issues':reason_issues}
                by_name[name] = _unknown('판정 이유가 인용 원문의 거래 단계·목표·투자자 확인 범위를 '
                                        '확대해 등급을 유보합니다. 확인된 원문 사실과 추가 검증이 필요합니다.')
                continue
            if name in TERM_CRITERIA:
                manual_evidence = any(
                    evidence[ref].get("analysis") == "investment_terms"
                    and (evidence[ref].get("criterion") == name or evidence[ref].get("criterion_id") == name)
                    for ref in ids
                )
                if state.get("investment_terms") is None or not manual_evidence:
                    by_name[name] = _unknown("사람이 입력하고 검증한 현재 투자조건이 없어 U입니다.")
                    continue
            by_name[name] = {"grade": grade, "reason": reason.strip(), "evidence_ids": ids}
        for name in duplicates:
            by_name[name] = _unknown("같은 항목이 중복 판정되어 U입니다.")

    # 투자조건 미입력은 모델의 출력에 관계없이 세 항목 전부 U로 보장한다.
    if state.get("investment_terms") is None:
        for name in TERM_CRITERIA:
            by_name[name] = _unknown("현재 거래의 투자조건을 사람이 입력하지 않아 U입니다.")
    scored = calculate_scorecard({name: item["grade"] for name, item in by_name.items()})
    details = {
        name: {**scored["items"][name], "reason": by_name[name]["reason"],
               "evidence": [evidence[ref] for ref in by_name[name]["evidence_ids"]]}
        for name in CRITERIA
    }
    decision = model_result.get("decision")
    reason = model_result.get("decision_reason")
    if (not isinstance(decision, str) or decision not in DECISIONS
            or not isinstance(reason, str) or not reason.strip()):
        decision, reason = "근거 부족", "검증 근거에 기반한 유효한 투자 의견과 사유가 제시되지 않았습니다."
    if not ready or scored["scorable_weight"] == 0:
        decision, reason = "근거 부족", "점수를 매길 수 있는 검증 근거가 없어 전체 항목을 U로 표시했습니다."
    return {
        "score_details": details,
        "total_score": scored["earned_score"] if ready and scored["scorable_weight"] > 0 else None,
        "scorable_weight": scored["scorable_weight"], "unknown_weight": scored["unknown_weight"],
        "unknown_items": scored["unknown_items"],
        "investment_decision": decision, "decision_reason": reason.strip(),
        'assessment_guard_rejections': assessment_rejections,
    }
