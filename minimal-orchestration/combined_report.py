"""세 기업의 검증 결과를 최대 10쪽의 단일 투자 검토 보고서로 만든다.

결합 단계는 새 사실·등급을 생성하지 않는다. 기업별 원 배점과 U를 대조하고,
회사별 인용 ID를 부여한 뒤 원문 품질 Judge와 A4 출력 검사에 연결한다.
"""

from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Mapping
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

from investment_case import CRITICAL_CRITERIA, TERM_CRITERIA, build_investment_case
from rag.PDF.pdf_loader import load_pdf, resolve_pdf_source_path
from rag.report import NEXT_CHECKS
from rag.scorecard import SCORECARD, calculate_scorecard
from report_renderer import render_report, validate_pdf


COMPANIES = ("MOTOMIND", "ROBROS", "Figure AI")
PREFIXES = {"MOTOMIND": "MM", "ROBROS": "RB", "Figure AI": "FG"}
CRITERIA = tuple(name for group in SCORECARD.values() for name in group)
REFERENCE_PATTERN = re.compile(r"\[((?:MM|RB|FG)-E\d+)\]")
RAG_ROOT = Path(__file__).resolve().parent / "rag"


def _plain(value) -> str:
    return " ".join(str(value).split())


def _cell(value) -> str:
    return _plain(value).replace("|", "／")


def _number(value) -> str:
    return f"{value:g}" if isinstance(value, (int, float)) and not isinstance(value, bool) else "U"


def _refs(values, prefix) -> str:
    return " ".join(f"[{prefix}-{value}]" for value in values)


def _quote_excerpt(value, limit=70) -> str:
    """긴 검증 인용은 연속된 앞부분만 발췌하고 생략을 표시한다. 원문 전문은 Judge에 전달한다."""
    text = _plain(value)
    if len(text) <= limit:
        return text
    end = text.rfind(" ", 0, limit)
    return text[:end if end >= limit // 2 else limit] + " …(인용 발췌)"


def _normalise_records(records) -> list[dict]:
    if not isinstance(records, (list, tuple)) or len(records) != len(COMPANIES):
        raise ValueError("통합 보고서는 MOTOMIND·ROBROS·Figure AI 세 기업의 실행 결과가 모두 필요합니다")
    prepared = {}
    for record in records:
        if not isinstance(record, Mapping) or not isinstance(record.get("state"), Mapping):
            raise ValueError("통합 보고서에는 app.run()의 state가 포함된 결과를 전달하세요")
        state, saved = record["state"], record.get("scores", {})
        company = state.get("company_info", {}).get("name")
        if company not in PREFIXES or company in prepared:
            raise ValueError("통합 보고서의 기업명이 지원 목록과 다르거나 중복되었습니다")
        research = state.get("research_result", state)
        investment = state.get("investment_result", state)
        if not isinstance(research, Mapping) or not isinstance(investment, Mapping):
            raise ValueError(f"{company}: 조사·투자 결과가 없습니다")
        details = investment.get("score_details", {})
        if not isinstance(details, Mapping) or set(details) != set(CRITERIA):
            raise ValueError(f"{company}: 21항목 점수표가 완전하지 않습니다")
        if any(not isinstance(detail, Mapping) for detail in details.values()):
            raise ValueError(f"{company}: 항목별 점수 형식이 올바르지 않습니다")
        for detail in details.values():
            grade = detail.get("grade")
            if grade is not None and (type(grade) is not int or not 0 <= grade <= 4):
                raise ValueError(f"{company}: 등급은 0~4 또는 U여야 합니다")
        scored = calculate_scorecard({name: details[name].get("grade") for name in CRITERIA})
        total = scored["earned_score"] if scored["scorable_weight"] else None
        expected = {"total_score": total, "scorable_weight": scored["scorable_weight"],
                    "unknown_weight": scored["unknown_weight"]}
        for key, value in expected.items():
            if investment.get(key) != value:
                raise ValueError(f"{company}: {key}와 원 배점 계산이 일치하지 않습니다")
        if isinstance(saved, Mapping) and saved:
            if saved.get("company") != company:
                raise ValueError(f"{company}: 저장 점수의 기업명이 일치하지 않습니다")
            for key, value in expected.items():
                if saved.get(key) != value:
                    raise ValueError(f"{company}: 저장 점수와 State의 {key}가 일치하지 않습니다")
            if saved.get("score_details") != details:
                raise ValueError(f"{company}: 저장 항목별 점수와 State가 일치하지 않습니다")
        claims = research.get("source_checked_claims", [])
        if not isinstance(claims, list):
            raise ValueError(f"{company}: 검증 주장 목록이 없습니다")
        evidence = {f"E{i}": claim for i, claim in enumerate(claims, 1) if isinstance(claim, Mapping)}
        score_refs = {}
        for name, calculated in scored["items"].items():
            detail = details[name]
            if detail.get("weight") != calculated["weight"] or detail.get("score") != calculated["score"]:
                raise ValueError(f"{company}: {name}의 배점·획득점수가 등급과 일치하지 않습니다")
            ids = [ref for ref, claim in evidence.items() if claim in detail.get("evidence", [])]
            declared = detail.get("evidence_ids", [])
            if not isinstance(declared, list) or any(ref not in evidence for ref in declared):
                raise ValueError(f"{company}: {name}에 등록되지 않은 근거가 연결되었습니다")
            ids = list(dict.fromkeys(ids + declared))
            if calculated["grade"] is not None and not ids:
                raise ValueError(f"{company}: {name}의 확인 등급에 검증 근거가 없습니다")
            score_refs[name] = ids
        flat = {**state, **research, **investment}
        case = investment.get("investment_case") or build_investment_case(flat, investment)
        if not isinstance(case, Mapping):
            raise ValueError(f"{company}: 투자 제안 형식이 올바르지 않습니다")
        supports = case.get("supporting_facts", [])
        risks = case.get("key_risks", [])
        if not isinstance(supports, list) or not isinstance(risks, list):
            raise ValueError(f"{company}: 투자 근거·위험 목록 형식이 올바르지 않습니다")
        if any(not isinstance(value, Mapping) for value in supports + risks):
            raise ValueError(f"{company}: 투자 근거·위험 항목 형식이 올바르지 않습니다")
        for fact in supports + [risk for risk in risks if risk.get("basis") == "confirmed_negative"]:
            if not isinstance(fact, Mapping):
                raise ValueError(f"{company}: 투자 근거 형식이 올바르지 않습니다")
            name, ids = fact.get("criterion"), fact.get("evidence_ids", [])
            if name not in details or not isinstance(ids, list) or not ids or any(ref not in evidence for ref in ids):
                raise ValueError(f"{company}: 투자 제안에 유효한 항목·인용이 없습니다")
            if any(ref not in score_refs[name] for ref in ids):
                raise ValueError(f"{company}: 투자 제안과 점수표의 근거가 다릅니다")
            if not any(fact.get("fact") == evidence[ref].get("claim") for ref in ids):
                raise ValueError(f"{company}: 투자 제안의 사실이 검증 주장과 다릅니다")
            expected_positive = fact in supports
            if (expected_positive and (details[name]["grade"] is None or details[name]["grade"] <= 0)) or (
                    not expected_positive and details[name]["grade"] != 0):
                raise ValueError(f"{company}: 투자 근거·위험의 방향과 등급이 다릅니다")
        quality = state.get("report_evaluation", {})
        mode = saved.get("mode", record.get("mode", "live")) if isinstance(saved, Mapping) else "live"
        status = state.get("status", saved.get("status", "quality_unreviewed"))
        is_ready = (mode != "demo" and status in {"completed", "partial"}
                    and isinstance(quality, Mapping) and quality.get("passed") is True and not quality.get("issues"))
        prepared[company] = {"company": company, "prefix": PREFIXES[company], "state": state,
                             "scores": dict(investment), "calculated": scored, "details": details,
                             "evidence": evidence, "score_refs": score_refs, "case": dict(case),
                             "status": status, "mode": mode, "quality": quality,
                             "ready": is_ready, "catalog": state.get("document_catalog", {}),
                             "trace_id": state.get("trace_id"), "record": record}
    return [prepared[company] for company in COMPANIES]


def _proposal(company) -> tuple[str, str, list[str]]:
    """최고 총점을 고르지 않고 기업별 확인 사실·핵심 U·거래조건으로 제안한다."""
    details, case = company["details"], company["case"]
    missing = [name for name in (*CRITICAL_CRITERIA, *TERM_CRITERIA) if details[name]["grade"] is None]
    negative = [name for name in (*CRITICAL_CRITERIA, *TERM_CRITERIA) if details[name]["grade"] == 0]
    action = case.get("action", "판단 유보")
    if not company["ready"]:
        return "판단 유보", "기업별 보고서가 실제 품질 통과 상태가 아니므로 검토용 초안으로만 사용한다.", missing
    if not case.get("supporting_facts") or not company["calculated"]["scorable_weight"]:
        return "판단 유보", "후보를 검토할 긍정 사실의 검증 근거가 부족하므로 투자 여부를 판단할 수 없다.", missing
    if negative:
        return "투자 보류", "핵심 사업화·현재 거래조건의 명시적 부정 근거가 확인되어 해결 전 투자 추진을 보류한다.", missing
    if action not in {"조건부 투자 검토", "투자 추진 검토", "투자 보류", "판단 유보"}:
        raise ValueError(f"{company['company']}: 지원하지 않는 투자 제안입니다")
    if action == "투자 추진 검토" and missing:
        action = "조건부 투자 검토"
    if missing:
        rationale = ("검증된 긍정 사실은 후보를 검토할 이유다. 핵심 사업화 또는 현재 투자조건이 U이므로 "
                     "아래 조건 확인 전 투자 실행 판단을 유보한다.")
    else:
        rationale = ("검증된 투자 포인트와 핵심 검토 자료를 함께 심사한다. 점수만으로 투자 대상을 고르거나 "
                     "투자 실행을 결정하지 않는다.")
    if action == "투자 보류":
        rationale = "검증된 투자 포인트가 있어도 현재 종합 투자 의견은 보류이며, 아래 실사·위험 확인 후 재검토한다."
    elif action == "판단 유보":
        rationale = "일부 검증 사실은 있지만 최종 투자 판단의 근거가 충분하지 않아 추가 실사 후 판단한다."
    return action, rationale, missing


def _reference_lines(companies, used) -> list[str]:
    lines = []
    for company in companies:
        groups = {}
        for ref, claim in company["evidence"].items():
            namespaced = f"{company['prefix']}-{ref}"
            if namespaced not in used:
                continue
            source = claim.get("source", {})
            doc_id, page = source.get("doc_id"), source.get("page")
            metadata = company["catalog"].get(doc_id, {})
            if not isinstance(doc_id, str) or type(page) is not int or page < 1 or not metadata:
                raise ValueError(f"{company['company']}: 인용 근거에 문서·원본 페이지 정보가 없습니다")
            quote = claim.get("evidence_quote")
            if not isinstance(quote, str) or not quote.strip():
                raise ValueError(f"{company['company']}: 실제 검증 인용문이 없습니다")
            groups.setdefault((doc_id, page), {"metadata": metadata, "claims": []})["claims"].append((namespaced, quote))
        for (doc_id, page), group in groups.items():
            metadata = group["metadata"]
            ids = " ".join(f"[{ref}]" for ref, quote in group["claims"])
            title = metadata.get("title") or doc_id
            publisher = metadata.get("publisher") or "발행자 미확인"
            url = metadata.get("source_url")
            location = metadata.get("file_path") or metadata.get("source_path") or "원문 보관 위치 미확인"
            date = metadata.get("publication_date") or metadata.get("published_at") or "발행일 미확인"
            lines.append(f"- {ids} {company['company']} | {title} — DOC {doc_id}, p.{page}; "
                         f"발행: {publisher}; 발행일: {date}; 원문: {url or location}")
            quotes = {}
            for ref, quote in group["claims"]:
                quotes.setdefault(_plain(quote), []).append(ref)
            for quote, refs in quotes.items():
                lines.append("  인용 " + " ".join(f"[{ref}]" for ref in refs) + ": “" + _quote_excerpt(quote) + "”")
    return lines


def _validated_portfolio(portfolio_decision, companies) -> dict | None:
    if portfolio_decision is None:
        return None
    if not isinstance(portfolio_decision, Mapping):
        raise ValueError("최종 투자 대상 제안 형식이 올바르지 않습니다")
    result = dict(portfolio_decision)
    selected, action = result.get("selected_company"), result.get("action")
    if selected is not None and selected not in COMPANIES:
        raise ValueError("최종 투자 대상 기업이 비교 대상에 없습니다")
    if action not in {"투자", "조건부 투자", "투자 보류"} or (action != "투자 보류" and selected is None):
        raise ValueError("최종 제안은 대상 기업과 투자·조건부 투자·보류 중 하나여야 합니다")
    for field in ("reason", "comparison_reason"):
        if not isinstance(result.get(field), str) or not result[field].strip():
            raise ValueError("최종 제안에 투자 이유와 비교 판단 이유가 필요합니다")
    ids = result.get("evidence_ids", [])
    known = {f"{company['prefix']}-{ref}" for company in companies for ref in company["evidence"]}
    if not isinstance(ids, list) or any(not isinstance(ref, str) or ref not in known for ref in ids):
        raise ValueError("최종 투자 제안에 등록되지 않은 회사별 근거 ID가 있습니다")
    result["evidence_ids"] = list(dict.fromkeys(ids))
    conditions = result.get("required_conditions", [])
    if not isinstance(conditions, list) or any(not isinstance(value, (str, Mapping)) for value in conditions):
        raise ValueError("최종 투자 제안의 확인 조건은 목록이어야 합니다")
    if selected is not None and action != "투자 보류":
        company = next(value for value in companies if value["company"] == selected)
        if not all(value["ready"] for value in companies):
            raise ValueError("기업별 품질 미달·데모 결과를 포함한 비교에서는 최종 투자 대상을 선정할 수 없습니다")
        if not company["case"].get("supporting_facts") or not any(ref.startswith(company["prefix"] + "-") for ref in ids):
            raise ValueError("검증된 긍정 사실과 회사별 인용 없이 최종 투자 대상을 선정할 수 없습니다")
        positive_ids = {f"{company['prefix']}-{ref}" for name in CRITERIA
                        if company["details"][name]["grade"] is not None and company["details"][name]["grade"] > 0
                        for ref in company["score_refs"][name]}
        selected_ids = {f"{company['prefix']}-{ref}" for ref in company['evidence']}
        if any(ref not in selected_ids for ref in ids) or not any(ref in positive_ids for ref in ids):
            raise ValueError("최종 투자 제안에는 선정 기업의 검증 근거와 긍정 평가 근거가 필요합니다")
        if any(company["details"][name]["grade"] == 0 for name in (*CRITICAL_CRITERIA, *TERM_CRITERIA)):
            raise ValueError("핵심 사업화·현재 거래조건의 명시적 부정 근거가 있는 기업에 투자 제안을 할 수 없습니다")
        missing = [name for name in (*CRITICAL_CRITERIA, *TERM_CRITERIA)
                   if company["details"][name]["grade"] is None]
        if action == "투자" and missing:
            result["action"] = "조건부 투자"
            result["policy_adjustment"] = "핵심 사업화·현재 거래조건이 U이므로 조건 충족 전 투자 실행을 유보한다."
        result["unverified_critical_conditions"] = missing
    return result


def build_combined_report(records, portfolio_decision=None, report_synthesis=None) -> dict:
    """다섯 구역·세 기업 전체 점수표·근거와 조건부 투자 제안을 결정론적으로 구성한다."""
    companies = _normalise_records(records)
    portfolio = _validated_portfolio(portfolio_decision, companies)
    report_inputs = []
    if report_synthesis is not None:
        from company_report_inputs import build_company_report_inputs
        from report_synthesis import validate_report_synthesis
        report_inputs = build_company_report_inputs(companies)
        report_synthesis = validate_report_synthesis(report_synthesis, report_inputs, companies)
    synthesized = {item['company']: item for item in report_synthesis['companies']} if report_synthesis else {}
    used = {f"{company['prefix']}-{ref}" for company in companies
            for refs in company["score_refs"].values() for ref in refs}
    proposals = {company["company"]: _proposal(company) for company in companies}
    summary = ["# 제조·물류 휴머노이드 3개 기업 투자 검토 보고서", "## 1 — SUMMARY",
               "대상: MOTOMIND·ROBROS·Figure AI. 동일한 6영역·21항목·100점 원 배점을 사용한다."]
    if report_synthesis:
        value = report_synthesis['executive_summary']
        used.update(value['evidence_ids'])
        summary.append(value['text'] + ' ' + ' '.join(f'[{ref}]' for ref in value['evidence_ids']))
    for company in companies:
        score, action = company["calculated"], proposals[company["company"]][0]
        total = _number(company["scores"]["total_score"]) + "점" if score["scorable_weight"] else "산정 보류"
        marker = " / 검토용 초안" if not company["ready"] else ""
        summary.append(f"- {company['company']}: 확인 획득 {total}; 확인 배점 {score['scorable_weight']}/100, "
                       f"U 배점 {score['unknown_weight']}/100. 현재 의견: {action}{marker}.")
    summary.append("U는 미확인이며 0점·실패와 다르다. 미확인 배점 제외 재환산·총점 자동 순위는 하지 않는다. "
                   "현재 거래조건과 핵심 미확인을 확인한 뒤 투자 심사에서 최종 판단한다.")
    if portfolio:
        summary.append(f"최종 대상 제안: {portfolio.get('selected_company') or '선정 유보'} — {portfolio['action']}.")
    rows = ["## 2 — 동일 기준 점수 비교", "### 6개 영역의 확인 획득점수 / 원래 배점",
            "| 평가 영역 | MOTOMIND | ROBROS | Figure AI |", "|---|---:|---:|---:|"]
    for category, criteria in SCORECARD.items():
        cells = []
        for company in companies:
            known = sum(weight for name, weight in criteria.items() if company["details"][name]["grade"] is not None)
            earned = sum(company["details"][name]["score"] or 0 for name in criteria)
            cells.append(f"{_number(earned) if known else 'U'}/{sum(criteria.values())}; 확인 {known}, U {sum(criteria.values())-known}")
        rows.append("| " + " | ".join([category, *cells]) + " |")
    rows.extend(["### 21개 항목의 등급 / 획득점수와 검증 인용",
                 "획득점수 = 원 배점 × 등급 ÷ 4. 각 셀은 등급 / 획득점수; U는 미확인이다.",
                 "| 평가 항목 (원 배점) | MOTOMIND | ROBROS | Figure AI |", "|---|---:|---:|---:|"])
    for name in CRITERIA:
        weight = companies[0]["details"][name]["weight"]
        cells = []
        for company in companies:
            detail = company["details"][name]
            cells.append("U" if detail["grade"] is None else
                         f"{detail['grade']} / {_number(detail['score'])} " + _refs(company["score_refs"][name], company["prefix"]))
        rows.append("| " + " | ".join([f"{name} ({weight})", *map(_cell, cells)]) + " |")
    narratives = ["## 3 — 기업별 투자 근거와 위험"]
    if portfolio:
        narratives.append("아래는 기업별 현재 자료 기준의 즉시 투자 검토 의견이다. 최종 조건부 투자 대상과 "
                          "필수조건 충족 이후의 계획은 4장의 종합 제안에서 구분한다.")
    for company in companies:
        narratives.append(f"### {company['company']} — {proposals[company['company']][0]}")
        if report_synthesis:
            from report_synthesis import NARRATIVE_FIELDS, NARRATIVE_LABELS
            for field, label in zip(NARRATIVE_FIELDS, NARRATIVE_LABELS):
                paragraph = synthesized[company['company']][field]
                used.update(paragraph['evidence_ids'])
                narratives.append(f"**{label}:** " + paragraph['text'] + ' ' +
                                  ' '.join(f'[{ref}]' for ref in paragraph['evidence_ids']))
            continue
        supports = company["case"].get("supporting_facts", [])
        if not supports:
            narratives.append("긍정적인 투자 포인트를 확정할 검증 근거가 부족하다. 현재 자료로 투자 판단을 유보한다.")
        for fact in supports[:3]:
            refs = fact["evidence_ids"]
            used.update(f"{company['prefix']}-{ref}" for ref in refs)
            first = company["evidence"][refs[0]]
            meta = company["catalog"].get(first.get("source", {}).get("doc_id"), {})
            narratives.append(f"- 검토 근거 — {fact['criterion']}: {_plain(fact['fact'])} "
                              f"{_refs(refs, company['prefix'])} (문서 발행: {_plain(meta.get('publisher', '미확인'))})")
        risks = company["case"].get("key_risks", [])
        for risk in risks[:3]:
            name = risk.get("criterion")
            if name not in company["details"]:
                raise ValueError(f"{company['company']}: 등록되지 않은 위험 항목입니다")
            if risk.get("basis") == "confirmed_negative":
                refs = risk["evidence_ids"]
                used.update(f"{company['prefix']}-{ref}" for ref in refs)
                narratives.append(f"- 확인 위험 — {name}: {_plain(risk['fact'])} {_refs(refs, company['prefix'])}")
            elif company["details"][name]["grade"] is None:
                narratives.append(f"- 실사 필요 — {name}: 현재 U. 필요 자료: {NEXT_CHECKS[name]}.")
            else:
                raise ValueError(f"{company['company']}: 확인 등급을 미확인 위험으로 표현할 수 없습니다")
        if not company["ready"]:
            narratives.append(f"- 품질 상태: {company['status']}; 실행 모드: {company['mode']}. "
                              "실제 품질 통과 최종 결과로 사용하지 않는다.")
    proposals_text = ["## 4 — 최종 투자 제안",
                      "검토 근거와 불확실성의 종류가 다르므로 세 기업의 총점만으로 투자 대상을 선택하지 않는다. "
                      "기업별 제안은 확인 사실·핵심 사업화 근거·현재 거래조건을 함께 검토한 결과다."]
    if report_synthesis:
        proposals_text.append('### 기업 보고서의 종합 비교')
        for value in report_synthesis['cross_company_comparison']:
            used.update(value['evidence_ids'])
            proposals_text.append(value['text'] + ' ' + ' '.join(f'[{ref}]' for ref in value['evidence_ids']))
    if portfolio:
        used.update(portfolio["evidence_ids"])
        proposals_text.extend([
            f"### 최종 투자 대상: {portfolio.get('selected_company') or '선정 유보'} — {portfolio['action']}",
            "투자·보류 이유: " + _plain(portfolio["reason"]) + " " +
            " ".join(f"[{ref}]" for ref in portfolio["evidence_ids"]),
            "다른 기업과 비교한 선택 이유: " + _plain(portfolio["comparison_reason"]),
        ])
        if portfolio.get("policy_adjustment"):
            proposals_text.append(portfolio["policy_adjustment"])
        for condition in portfolio.get("required_conditions", []):
            text = condition if isinstance(condition, str) else condition.get("required") or condition.get("condition") or condition.get("criterion")
            if not isinstance(text, str) or not text.strip():
                raise ValueError("최종 투자 제안의 조건 설명이 없습니다")
            proposals_text.append("- 투자 조건: " + _plain(text))
        missing = portfolio.get("unverified_critical_conditions", [])
        if missing:
            proposals_text.append("조건 충족 전 실행 유보: " + "; ".join(
                name + " — " + NEXT_CHECKS[name] for name in missing) + ".")
        proposals_text.append("위 제안은 보고서의 심사 결론이며 실제 투자 거래의 실행을 뜻하지 않는다.")
        if portfolio["action"] == "조건부 투자":
            proposals_text.append(f"최종 결론: 위 필수조건을 충족하면 {portfolio['selected_company']}에 투자한다. "
                                  "조건을 확인하기 전에는 투자 실행을 유보한다.")
        elif portfolio["action"] == "투자":
            proposals_text.append(f"최종 결론: 확인된 근거와 거래조건에 따라 {portfolio['selected_company']}에 투자를 제안한다.")
        else:
            proposals_text.append("최종 결론: 현재는 투자를 보류하며 추가 자료 확인 후 투자 대상을 재심사한다.")
    elif all(proposals[company["company"]][0] == "판단 유보" for company in companies):
        proposals_text.append("종합 제안: 현재는 투자 대상 선정을 유보하고 기업별 필요 자료를 확보한 뒤 재심사한다.")
    elif all(proposals[company["company"]][0] in {"판단 유보", "투자 보류"} for company in companies):
        proposals_text.append("종합 제안: 현재 결과로 즉시 투자 추진을 제안할 기업은 없다. 아래 보류·유보 이유와 실사 조건을 확인한다.")
    else:
        proposals_text.append("종합 제안: 아래 조건부 검토·투자 추진 검토 기업을 개별 심사한다. "
                              "확인 조건을 충족했다고 가정하거나 최고 점수 기업을 자동 추천하지 않는다.")
    individual_reviews = companies if portfolio is None else []
    for company in individual_reviews:
        action, rationale, missing = proposals[company["company"]]
        proposals_text.extend([f"### {company['company']} — {action}", rationale])
        supports = company["case"].get("supporting_facts", [])
        if supports:
            proposals_text.append("후보 검토 이유: " + "; ".join(
                fact["criterion"] + " " + _refs(fact["evidence_ids"], company["prefix"]) for fact in supports[:3]) + ".")
        critical = [name for name in CRITICAL_CRITERIA if name in missing]
        terms = [name for name in TERM_CRITERIA if name in missing]
        if critical:
            proposals_text.append("투자 전 핵심 확인: " + (
                ", ".join(critical) + ". 필요한 자료는 기업별 위험·최종 대상 조건과 원문 기록을 확인한다."
                if portfolio else "; ".join(name + " — " + NEXT_CHECKS[name] for name in critical) + "."))
        if terms:
            proposals_text.append("현재 거래조건 확인: " + ", ".join(terms) + ". 공개 투자유치 뉴스로 이번 거래조건을 대체하지 않는다.")
        if not missing and not supports:
            proposals_text.append("후속 판단에 사용할 긍정적인 사업화·고객 근거부터 확보한다.")
    references = ["## 5 — REFERENCE", "인용은 회사별 MM / RB / FG 접두어로 구분한다. "
                  "출처와 원본 페이지는 검증된 주장에서 가져오며 인용 발췌의 전문은 원문 Judge와 기록 파일에 보존한다."]
    references.extend(_reference_lines(companies, used))
    if not used:
        references.append("실제 인용한 자료 없음. 전체 기업 평가 항목은 U이며 투자 판단을 유보한다.")
    markdown = "\n\n".join([*summary, "\n".join(rows), *narratives, *proposals_text, *references]) + "\n"
    cited = set(REFERENCE_PATTERN.findall(markdown))
    if cited != used or markdown.count("\n## ") != 5:
        raise ValueError("통합 보고서의 구역·인용 검사에 실패했습니다")
    ready = all(company["ready"] for company in companies)
    comparison = [{"company": company["company"], "total_score": company["scores"]["total_score"],
                   "scorable_weight": company["calculated"]["scorable_weight"],
                   "unknown_weight": company["calculated"]["unknown_weight"],
                   "investment_action": proposals[company["company"]][0],
                   "source_status": company["status"], "source_mode": company["mode"],
                   "trace_id": company["trace_id"]} for company in companies]
    return {"markdown": markdown, "companies": companies, "comparison": comparison, "portfolio_decision": portfolio,
            "report_synthesis": report_synthesis, "company_report_inputs": report_inputs,
            "status": "quality_unreviewed" if ready else "source_quality_failed",
            "used_evidence_ids": sorted(used), "source_contexts_pending": True,
            "deterministic_checks": {"three_companies": True, "five_sections": True,
                "all_21_items_per_company": True, "all_six_categories": True,
                "score_and_unknown_consistency": True, "namespaced_citations": True,
                "all_scored_evidence_referenced": True, "source_reports_quality_passed": ready,
                "company_reports_synthesized": report_synthesis is not None}}


def combined_source_contexts(records, markdown: str, max_chars=180_000) -> list[dict]:
    """회사별 인용 ID를 실제 PDF 원본 페이지와 연결해 품질 Judge에 전달한다."""
    companies = _normalise_records(records)
    used = set(REFERENCE_PATTERN.findall(markdown))
    contexts, parsed, size = {}, {}, 0
    available = {f"{company['prefix']}-{ref}" for company in companies for ref in company["evidence"]}
    if used - available:
        raise ValueError("통합 보고서에 등록되지 않은 근거 ID가 있습니다")
    for company in companies:
        for ref, claim in company["evidence"].items():
            namespaced = f"{company['prefix']}-{ref}"
            if namespaced not in used:
                continue
            source = claim.get("source", {})
            doc_id, page = source.get("doc_id"), source.get("page")
            metadata = company["catalog"].get(doc_id, {})
            location = metadata.get("source_path") or metadata.get("file_path")
            if not location or type(page) is not int or page < 1:
                raise ValueError(f"{company['company']}: 근거 원본 위치가 없습니다")
            path = resolve_pdf_source_path(location)
            if path not in parsed:
                parsed[path] = load_pdf(path, doc_id=doc_id, company=company["company"])
            if page > len(parsed[path]):
                raise ValueError(f"{company['company']}: 근거 페이지가 실제 원본 범위를 벗어났습니다")
            text = parsed[path][page - 1].page_content
            quote = claim.get("evidence_quote")
            if not isinstance(quote, str) or not quote.strip() or _plain(quote) not in _plain(text):
                raise ValueError(f"{company['company']}: 검증 인용문이 현재 원본 페이지와 일치하지 않습니다")
            key = (company["company"], doc_id, page)
            if key not in contexts:
                size += len(text)
                if size > max_chars:
                    raise ValueError("통합 보고서 Judge의 원문 입력 상한을 초과했습니다")
                contexts[key] = {"company": company["company"], "doc_id": doc_id, "page": page,
                    "title": metadata.get("title"), "publisher": metadata.get("publisher"), "source_url": metadata.get("source_url"),
                    "source_path": str(path.resolve()), "evidence_ids": [], "checked_claims": [], "page_text": text}
            contexts[key]["evidence_ids"].append(namespaced)
            contexts[key]["checked_claims"].append({**claim, "id": namespaced})
    return list(contexts.values())


def _validate_combined_revision(original: str, revision) -> str:
    """수정 워커가 점수·인용·원문 목록·공식 투자 결론을 바꿀 수 없게 한다."""
    if not isinstance(revision, str) or not revision.strip():
        raise ValueError("통합 보고서 수정 결과는 비어 있지 않은 Markdown이어야 합니다")
    original_lines, revised_lines = original.splitlines(), revision.splitlines()
    if [line for line in original_lines if line.startswith("|")] != [
            line for line in revised_lines if line.startswith("|")]:
        raise ValueError("통합 보고서 수정에서 21항목·6영역 점수표를 변경할 수 없습니다")
    if [line for line in original_lines if line.startswith("## ")] != [
            line for line in revised_lines if line.startswith("## ")]:
        raise ValueError("통합 보고서의 다섯 구역 제목을 변경할 수 없습니다")
    citation_pattern = re.compile(r"\[([A-Z][A-Z0-9_-]*-E\d+|E\d+)\]", re.IGNORECASE)
    if set(citation_pattern.findall(original)) != set(citation_pattern.findall(revision)):
        raise ValueError("통합 보고서 수정에서 검증 인용을 삭제하거나 새 ID를 만들 수 없습니다")
    reference_header = "## 5 — REFERENCE"
    if original.split(reference_header, 1)[1].strip() != revision.split(reference_header, 1)[1].strip():
        raise ValueError("통합 보고서의 원문 목록·페이지·URL·검증 인용을 변경할 수 없습니다")
    locked_prefixes = (
        "최종 대상 제안:", "### 최종 투자 대상:", "최종 결론:", "종합 제안:",
        "- 투자 조건:", "조건 충족 전 실행 유보:", "현재 거래조건 확인:", "투자 전 핵심 확인:",
        *(f"- {company}:" for company in COMPANIES),
        *(f"### {company} —" for company in COMPANIES),
    )
    official = lambda lines: [line for line in lines if line.startswith(locked_prefixes)]
    if official(original_lines) != official(revised_lines):
        raise ValueError("통합 보고서 수정에서 공식 투자 대상·제안·필수조건·요약 점수를 변경할 수 없습니다")
    return revision


def _evaluate_combined_payload(quality_evaluator, payload) -> dict:
    if quality_evaluator is None:
        return {"passed": False, "issues": ["통합 보고서의 원문 기반 내용 품질 평가를 아직 실행하지 않았습니다"],
                "method": "deterministic_only", "action": "review"}
    evaluation = quality_evaluator(deepcopy(payload))
    if not isinstance(evaluation, Mapping) or type(evaluation.get("passed")) is not bool:
        raise ValueError("통합 보고서 품질 평가 형식이 올바르지 않습니다")
    if not isinstance(evaluation.get("issues", []), list):
        raise ValueError("통합 보고서 품질 평가의 결함은 목록이어야 합니다")
    result = dict(evaluation)
    if result["passed"] and result.get("issues"):
        result.update(passed=False, action=result.get("action") if result.get("action") in {"rewrite", "research"} else "rewrite")
    return result


def save_combined_report(records, output_dir, max_pages=10, quality_evaluator=None, portfolio_decision=None,
                         quality_reviser=None, max_rewrites=2, report_synthesis=None, initial_report=None) -> dict:
    """통합 보고서를 원문·내용·A4 규격으로 검사하고 단일 PDF와 기록 파일로 저장한다."""
    if type(max_rewrites) is not int or not 0 <= max_rewrites <= 2:
        raise ValueError("통합 보고서 수정 예산은 0~2 정수여야 합니다")
    if quality_reviser is not None and not callable(quality_reviser):
        raise ValueError("통합 보고서 수정 함수는 호출 가능해야 합니다")
    built = build_combined_report(records, portfolio_decision=portfolio_decision, report_synthesis=report_synthesis)
    original = built["markdown"]
    if initial_report is not None:
        # 재개 시 이미 교정한 표현을 원래 합성 문장으로 되돌리지 않는다.
        built['markdown'] = _validate_combined_revision(original, initial_report)
    contexts = combined_source_contexts(records, built["markdown"])
    payload = {"report": built["markdown"], "source_contexts": contexts,
               "comparison": built["comparison"], "portfolio_decision": built["portfolio_decision"],
               "company_reports": built['company_report_inputs'], "report_synthesis": built['report_synthesis'],
               "deterministic_checks": built["deterministic_checks"],
               "companies": [{"company": company["company"], "prefix": company["prefix"],
                   "score_details": company["details"], "investment_case": company["case"],
                   "source_report_evaluation": company["quality"], "source_status": company["status"],
                   "source_mode": company["mode"]} for company in built["companies"]]}
    evaluation = _evaluate_combined_payload(quality_evaluator, payload)
    rewrite_count, rewrite_events = 0, []
    while (not evaluation.get("passed") and evaluation.get("action") == "rewrite"
           and quality_reviser is not None and quality_evaluator is not None and rewrite_count < max_rewrites
           and built["deterministic_checks"]["source_reports_quality_passed"]):
        rewrite_count += 1
        try:
            proposal = quality_reviser(deepcopy(payload), deepcopy(evaluation))
            if proposal == payload["report"]:
                raise ValueError("품질 미달 보고서에 실제 수정이 없어 같은 문서를 다시 판정하지 않습니다")
            revised = _validate_combined_revision(original, proposal)
        except Exception as error:
            # 안전하지 않은 수정은 사용하지 않는다. 이전 Markdown과 점수를 보존하고
            # 한도 내에서 같은 자료로 교정하거나 마지막 안전한 초안으로 종료한다.
            message = str(error) if isinstance(error, ValueError) else f"보고서 수정 함수 실패: {type(error).__name__}"
            issues = list(evaluation.get("issues", []))
            if message not in issues:
                issues.append(message)
            evaluation = {**evaluation, "passed": False, "action": "rewrite", "issues": issues}
            rewrite_events.append({"attempt": rewrite_count, "accepted": False,
                                   "failure_type": type(error).__name__, "issue": message})
            continue
        payload["report"] = revised
        built["markdown"] = revised
        evaluation = _evaluate_combined_payload(quality_evaluator, payload)
        rewrite_events.append({"attempt": rewrite_count, "accepted": True,
                               "quality_passed": evaluation.get("passed"),
                               "action": evaluation.get("action")})
    stop_reason = ("passed" if evaluation.get("passed") else
                   "new_evidence_required" if evaluation.get("action") == "research" else
                   "rewrite_budget_exhausted" if rewrite_count >= max_rewrites else
                   "quality_reviser_unavailable")
    if not built["deterministic_checks"]["source_reports_quality_passed"]:
        evaluation = {**evaluation, "passed": False, "issues": list(evaluation.get("issues", [])) + [
            "기업별 품질 미달·미평가 또는 데모 결과가 포함되어 최종 통과 상태로 사용할 수 없습니다"]}
        stop_reason = "source_quality_failed"
    passed = evaluation.get("passed") is True and not evaluation.get("issues")
    status = "completed" if passed else "quality_failed"
    stem = "final_investment_report" + ("" if passed else "_draft")
    directory = Path(output_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    markdown_path, pdf_path, json_path = (directory / f"{stem}.{suffix}" for suffix in ("md", "pdf", "json"))
    # PDF 렌더링에 성공하기 전에는 이전 출력 묶음을 덮어쓰지 않는다.
    with tempfile.TemporaryDirectory(prefix="combined-report-", dir=directory) as staging:
        staged = Path(staging) / "report.pdf"
        render_report(built["markdown"], staged, max_pages=max_pages)
        checks = validate_pdf(staged, max_pages=max_pages)
        if not all(checks[key] for key in ("all_a4", "within_page_limit", "text_within_margins")):
            raise ValueError("통합 PDF 규격 검사에 실패했습니다")
        staged.replace(pdf_path)
    markdown_path.write_text(built["markdown"], encoding="utf-8")
    snapshot = {"status": status, "companies": built["comparison"], "used_evidence_ids": built["used_evidence_ids"],
                "markdown_sha256": sha256(built['markdown'].encode('utf-8')).hexdigest(),
                "source_score_details": {item['company']: item['details'] for item in built['companies']},
                "deterministic_checks": built["deterministic_checks"], "report_evaluation": evaluation,
                "pdf_checks": checks, "report_path": str(pdf_path),
                "source_contexts": contexts, "portfolio_decision": built["portfolio_decision"],
                "report_synthesis": built['report_synthesis'], "company_reports": built['company_report_inputs'],
                "rewrite_count": rewrite_count, "max_rewrites": max_rewrites,
                "rewrite_events": rewrite_events, "quality_stop_reason": stop_reason,
                "evaluation_method": "hybrid" if quality_evaluator else "deterministic_only"}
    json_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"markdown": markdown_path, "pdf": pdf_path, "scores_path": json_path, "json": json_path,
            "status": status, "pdf_checks": checks, "report_evaluation": evaluation,
            "rewrite_count": rewrite_count, "max_rewrites": max_rewrites,
            "rewrite_events": rewrite_events, "quality_stop_reason": stop_reason,
            "comparison": built["comparison"], "deterministic_checks": built["deterministic_checks"]}
