"""세 기업 단일 보고서의 전체 점수·인용·투자 전제조건·실제 PDF를 검사한다."""

from copy import deepcopy
import json

import pdfplumber
import pytest
from pypdf import PdfReader

from combined_report import (COMPANIES, CRITERIA, PREFIXES, build_combined_report,
                             combined_source_contexts, save_combined_report)
from investment_case import build_investment_case
from rag.scorecard import calculate_scorecard
from report_renderer import render_report


@pytest.fixture
def records(tmp_path):
    quote = "원문에는 제조 현장용 로봇 제품을 개발하고 있다고 명시되어 있다."
    source = render_report("# 검증용 회사 자료\n" + quote, tmp_path / "source.pdf")
    result = []
    for company in COMPANIES:
        claims = [{"claim": f"{company}의 공개 문서가 제조 현장용 로봇 제품 개발을 설명한다.",
                   "criterion_id": f"test-criterion-{i}", "analysis": "technology_analysis",
                   "evidence_quote": quote, "source": {"doc_id": "DOC", "page": 1}}
                  for i, name in enumerate(CRITERIA, 1)]
        calculated = calculate_scorecard({name: None if name in CRITERIA[-3:] else 3 for name in CRITERIA})
        details = {name: {**calculated["items"][name], "reason": "검증된 문서로 판정",
                         "evidence": [] if name in CRITERIA[-3:] else [claims[i]]}
                   for i, name in enumerate(CRITERIA)}
        investment = {"score_details": details, "total_score": calculated["earned_score"],
                      "scorable_weight": calculated["scorable_weight"], "unknown_weight": calculated["unknown_weight"],
                      "investment_decision": "우선 검토", "decision_reason": "원문 검토 뒤 현재 거래조건 확인 필요"}
        flat = {"source_checked_claims": claims}
        investment["investment_case"] = build_investment_case(flat, investment)
        state = {"company_info": {"name": company}, "research_result": flat,
                 "investment_result": investment, "report_evaluation": {"passed": True, "issues": []},
                 "status": "partial", "trace_id": f"trace-{PREFIXES[company]}",
                 "document_catalog": {"DOC": {"title": "회사 공개 자료", "source_path": str(source),
                     "file_path": "sources/company.pdf", "page_count": 1, "publisher": company,
                     "source_url": f"https://example.test/{PREFIXES[company]}", "published_at": "2026-10-13"}}}
        result.append({"state": state, "scores": {"company": company, "mode": "live", "status": "partial",
                                                  **deepcopy(investment)}})
    return result


def set_grade(record, criterion, grade):
    state = record["state"]
    investment = state["investment_result"]
    investment["score_details"][criterion]["grade"] = grade
    calculated = calculate_scorecard({name: detail["grade"] for name, detail in investment["score_details"].items()})
    claims = state["research_result"]["source_checked_claims"]
    for index, name in enumerate(CRITERIA):
        old = investment["score_details"][name]
        investment["score_details"][name] = {**old, **calculated["items"][name],
                                              "evidence": [] if old["grade"] is None else [claims[index]]}
    investment.update(total_score=calculated["earned_score"] if calculated["scorable_weight"] else None,
                      scorable_weight=calculated["scorable_weight"], unknown_weight=calculated["unknown_weight"])
    investment["investment_case"] = build_investment_case(state["research_result"], investment)
    record["scores"] = {**record["scores"], **deepcopy(investment)}


def portfolio(company="Figure AI", action="투자", ids=None):
    return {"selected_company": company, "action": action,
            "reason": "검증된 제품 개발 근거를 후보 검토 사유로 사용한다.",
            "comparison_reason": "각 기업의 확인 근거와 사업화 한계를 함께 검토했다. 총점 자동 순위는 사용하지 않았다.",
            "evidence_ids": ids if ids is not None else ["FG-E1"],
            'comparison_evidence_ids': ['MM-E1', 'RB-E1', 'FG-E1'],
            "required_conditions": ["현재 거래의 가치·지분권리·자금 사용 조건을 실사한다."]}


def synthesis_for(records):
    from test_report_synthesis import inputs_and_synthesis
    return inputs_and_synthesis(records)[2]


def test_all_three_companies_and_all_21_items_have_namespaced_references(records):
    built = build_combined_report(records)
    markdown = built["markdown"]
    assert markdown.count("\n## ") == 5
    for name in CRITERIA:
        assert f"| {name} (" in markdown
    for prefix in PREFIXES.values():
        assert f"[{prefix}-E1]" in markdown
        assert all(f"{prefix}-E{i}" in built["used_evidence_ids"] for i in range(1, 19))
    assert "[E1]" not in markdown
    references = markdown.split("## 5 — REFERENCE", 1)[1]
    for prefix in PREFIXES.values():
        assert all(f"[{prefix}-E{i}]" in references for i in range(1, 19))
    contexts = combined_source_contexts(records, markdown)
    assert len(contexts) == 3  # 같은 DOC/page라도 다른 기업의 E-ID는 합치지 않는다.
    assert {context["company"] for context in contexts} == set(COMPANIES)
    assert all(len(context["evidence_ids"]) == 18 for context in contexts)


def test_unknown_is_not_zero_or_renormalized_to_100(records):
    for record in records:
        for name in CRITERIA:
            set_grade(record, name, None)
    built = build_combined_report(records)
    assert "산정 보류" in built["markdown"]
    assert "확인 배점 0/100, U 배점 100/100" in built["markdown"]
    assert "투자 대상 선정을 유보" in built["markdown"]
    assert "실제 인용한 자료 없음" in built["markdown"]
    assert built["used_evidence_ids"] == []


def test_final_selection_is_conditional_when_current_terms_or_critical_facts_are_unknown(records):
    set_grade(records[2], "현장 안정성·안전성", None)
    built = build_combined_report(records, portfolio_decision=portfolio())
    assert built["portfolio_decision"]["action"] == "조건부 투자"
    assert "최종 투자 대상: Figure AI — 조건부 투자" in built["markdown"]
    assert "위 필수조건을 충족하면 Figure AI에 투자한다" in built["markdown"]
    assert "현장 안정성·안전성 — 장시간 운전 기록" in built["markdown"]
    assert "다른 기업과 비교한 선택 이유:" in built["markdown"]


def test_selection_cannot_use_total_score_or_unrelated_company_evidence(records):
    for decision in (portfolio(ids=["FG-E999"]), portfolio(ids=["MM-E1"]), portfolio(ids=[])):
        with pytest.raises(ValueError, match="근거|인용|긍정 평가"):
            build_combined_report(records, portfolio_decision=decision)
    set_grade(records[2], "현장 안정성·안전성", 0)
    with pytest.raises(ValueError, match="명시적 부정"):
        build_combined_report(records, portfolio_decision=portfolio())


@pytest.mark.parametrize("bad_case", ["score_mismatch", "missing_item", "invented_fact"])
def test_combining_rejects_inconsistent_scores_or_unsupported_investment_facts(records, bad_case):
    if bad_case == "score_mismatch":
        records[0]["state"]["investment_result"]["total_score"] = 99
    elif bad_case == "missing_item":
        records[0]["state"]["investment_result"]["score_details"].pop(CRITERIA[0])
    else:
        records[0]["state"]["investment_result"]["investment_case"]["supporting_facts"][0]["fact"] = "입증되지 않은 매출 1조원"
    with pytest.raises(ValueError):
        build_combined_report(records)


def test_changed_source_quote_blocks_combination_before_quality_judge(records, tmp_path):
    for record in records:
        record["state"]["research_result"]["source_checked_claims"][0]["evidence_quote"] = "원문에 없는 문자열"
        # 저장 점수의 evidence도 같은 주장에 맞춰 원문 검사 실패를 확인한다.
        record["scores"] = {**record["scores"], **deepcopy(record["state"]["investment_result"])}
    with pytest.raises(ValueError, match="원본 페이지"):
        save_combined_report(records, tmp_path, quality_evaluator=lambda _: pytest.fail("원문 검증 전에 Judge 호출 금지"))


@pytest.mark.parametrize("source_kind", ["quality_failed", "demo"])
def test_source_draft_or_demo_cannot_be_promoted_by_combined_judge(records, tmp_path, source_kind):
    if source_kind == "quality_failed":
        records[0]["state"]["status"] = "quality_failed"
        records[0]["state"]["report_evaluation"] = {"passed": False, "issues": ["원문 불일치"]}
    else:
        records[0]["scores"]["mode"] = "demo"
    output = save_combined_report(records, tmp_path, quality_evaluator=lambda _: {"passed": True, "issues": []})
    assert output["status"] == "quality_failed"
    assert output["pdf"].name == "final_investment_report_draft.pdf"
    assert output["report_evaluation"]["passed"] is False
    assert not (tmp_path / "final_investment_report.pdf").exists()


def test_actual_single_pdf_includes_three_company_matrix_and_fits_ten_a4_pages(records, tmp_path):
    captured = {}

    def evaluate(payload):
        captured.update(payload)
        return {"passed": True, "issues": [], "method": "hybrid"}

    output = save_combined_report(records, tmp_path, quality_evaluator=evaluate, portfolio_decision=portfolio(),
                                  report_synthesis=synthesis_for(records))
    assert output["status"] == "completed"
    assert 5 <= output["pdf_checks"]["pages"] <= 10
    assert all(output["pdf_checks"][key] for key in ("all_a4", "within_page_limit", "text_within_margins"))
    assert len(captured["source_contexts"]) == 3
    assert captured["portfolio_decision"]["action"] == "조건부 투자"
    text = "".join(page.extract_text() for page in PdfReader(output["pdf"]).pages)
    assert all(company in text for company in COMPANIES)
    assert all(name in text for name in CRITERIA)
    # SUMMARY의 본문은 첫 쪽의 반쪽 이내이며, 아래 페이지 번호는 제외한다.
    with pdfplumber.open(output["pdf"]) as pdf:
        body = [char for char in pdf.pages[0].chars if char["size"] != 8]
        assert max(char["bottom"] for char in body) <= pdf.pages[0].height / 2


def test_live_combination_without_content_judge_stays_draft(records, tmp_path):
    output = save_combined_report(records, tmp_path)
    assert output["status"] == "quality_failed"
    assert output["report_evaluation"]["method"] == "deterministic_only"
    assert output["pdf"].stem.endswith("_draft")


def test_combined_content_failure_rewrites_with_same_scores_and_sources_then_passes(records, tmp_path):
    evaluations, revisions = [], []

    def evaluate(payload):
        evaluations.append(deepcopy(payload))
        return {"passed": len(evaluations) > 1, "issues": [] if len(evaluations) > 1 else ["출처 귀속을 더 명확히 표현"],
                "action": "rewrite", "method": "hybrid"}

    def revise(payload, evaluation):
        revisions.append(deepcopy(payload))
        assert evaluation["passed"] is False
        return payload["report"].replace("고객 실증으로 전환", "고객의 현장 실증으로 전환")

    output = save_combined_report(records, tmp_path, quality_evaluator=evaluate,
                                  quality_reviser=revise, portfolio_decision=portfolio(),
                                  report_synthesis=synthesis_for(records))
    assert output["status"] == "completed"
    assert output["rewrite_count"] == 1
    assert len(evaluations) == 2 and len(revisions) == 1
    assert evaluations[0]["source_contexts"] == evaluations[1]["source_contexts"]
    assert evaluations[0]["comparison"] == evaluations[1]["comparison"]
    assert "고객의 현장 실증으로 전환" in output["markdown"].read_text()
    snapshot = json.loads(output["json"].read_text())
    assert snapshot["rewrite_count"] == 1 and snapshot["max_rewrites"] == 2
    assert snapshot["rewrite_events"][0]["accepted"] is True


def test_already_passed_combined_report_skips_revision_callback(records, tmp_path):
    output = save_combined_report(records, tmp_path,
        quality_evaluator=lambda _: {"passed": True, "issues": []},
        report_synthesis=synthesis_for(records),
        quality_reviser=lambda *_: pytest.fail("통과한 보고서의 추가 Writer 호출 금지"))
    assert output["rewrite_count"] == 0
    assert output["quality_stop_reason"] == "passed"


def test_combined_rewrite_budget_is_separate_and_bounded(records, tmp_path):
    evaluations, revisions = [], []

    def evaluate(payload):
        evaluations.append(payload)
        return {"passed": False, "issues": ["서술의 불확실성이 여전히 불명확"], "action": "rewrite"}

    def revise(payload, evaluation):
        revisions.append(payload)
        return payload["report"].replace("## 4 — 최종 투자 제안", "자료의 검증 범위를 명확히 구분한다.\n\n## 4 — 최종 투자 제안")

    output = save_combined_report(records, tmp_path, quality_evaluator=evaluate, quality_reviser=revise,
                                  report_synthesis=synthesis_for(records))
    assert output["status"] == "quality_failed"
    assert len(revisions) == output["rewrite_count"] == 2
    assert len(evaluations) == 3
    assert output["quality_stop_reason"] == "rewrite_budget_exhausted"
    assert output["pdf"].stem.endswith("_draft")


def test_new_evidence_failure_preserves_draft_without_source_research_or_writer(records, tmp_path):
    output = save_combined_report(records, tmp_path,
        quality_evaluator=lambda _: {"passed": False, "issues": ["추가 원문 필요"], "action": "research"},
        report_synthesis=synthesis_for(records),
        quality_reviser=lambda *_: pytest.fail("research 요구를 같은 자료의 Writer로 우회하면 안 됨"))
    assert output["status"] == "quality_failed"
    assert output["rewrite_count"] == 0
    assert output["quality_stop_reason"] == "new_evidence_required"


@pytest.mark.parametrize("unsafe", ["score", "citation", "reference", "decision"])
def test_unsafe_revision_cannot_change_scores_citations_sources_or_official_decision(records, tmp_path, unsafe):
    synthesis = synthesis_for(records)
    original = build_combined_report(records, portfolio_decision=portfolio(), report_synthesis=synthesis)["markdown"]

    def revise(payload, evaluation):
        if unsafe == "score":
            return payload["report"].replace("| 관련 전문성 (6) |", "| 관련 전문성 (99) |")
        if unsafe == "citation":
            return payload["report"].replace("[MM-E1]", "[GG-E1]")
        if unsafe == "reference":
            return payload["report"].replace("https://example.test/MM", "https://invented.test/source")
        return payload["report"].replace("필수조건을 충족하면 Figure AI에 투자한다", "필수조건을 충족하면 ROBROS에 투자한다")

    output = save_combined_report(records, tmp_path,
        quality_evaluator=lambda _: {"passed": False, "issues": ["출처 귀속 결함"], "action": "rewrite"},
        quality_reviser=revise, max_rewrites=1, portfolio_decision=portfolio(), report_synthesis=synthesis)
    assert output["status"] == "quality_failed"
    assert output["rewrite_count"] == 1
    assert output["rewrite_events"][0]["accepted"] is False
    assert output["markdown"].read_text() == original
    assert len(output["report_evaluation"]["issues"]) == 2


def test_revision_exception_keeps_last_safe_report_as_draft(records, tmp_path):
    synthesis = synthesis_for(records)
    def fail_revision(*_):
        raise RuntimeError("mock writer failure")

    output = save_combined_report(records, tmp_path,
        quality_evaluator=lambda _: {"passed": False, "issues": ["표현 결함"], "action": "rewrite"},
        quality_reviser=fail_revision, max_rewrites=1, report_synthesis=synthesis)
    assert output["status"] == "quality_failed"
    assert output["rewrite_events"][0]["failure_type"] == "RuntimeError"
    assert output["markdown"].read_text() == build_combined_report(records, report_synthesis=synthesis)["markdown"]


def test_unchanged_revision_does_not_resample_judge_until_it_passes(records, tmp_path):
    evaluations = []

    def evaluate(payload):
        evaluations.append(payload)
        return {"passed": False, "issues": ["표현 결함"], "action": "rewrite"}

    output = save_combined_report(records, tmp_path, quality_evaluator=evaluate,
                                  report_synthesis=synthesis_for(records),
                                  quality_reviser=lambda payload, _: payload["report"])
    assert len(evaluations) == 1
    assert output["rewrite_count"] == 2
    assert output["status"] == "quality_failed"
    assert len(output["report_evaluation"]["issues"]) == 2  # 같은 거절 사유를 중복 누적하지 않는다.


@pytest.mark.parametrize("budget", [True, -1, 3, 1.5])
def test_combined_rewrite_budget_cannot_be_unbounded(records, tmp_path, budget):
    with pytest.raises(ValueError, match="0~2"):
        save_combined_report(records, tmp_path, max_rewrites=budget)
