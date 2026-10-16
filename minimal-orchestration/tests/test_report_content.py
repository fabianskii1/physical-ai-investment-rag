"""기업 점수·미확인 배점·서술 근거 연결을 검증한다. 모델 API는 호출하지 않는다."""

from copy import deepcopy

import pytest

from investment_evaluation import evaluate_investment
from rag.report import NEXT_CHECKS, ReportAnalysisDraft, consolidate_report_references, report
from rag.scorecard import SCORECARD


CRITERIA = [name for items in SCORECARD.values() for name in items]


def base_state():
    claims = [
        {"claim": "회사는 제조 작업용 로봇을 소개했습니다.",
         "analysis": "technology_analysis", "evidence_quote": "manufacturing robot",
         "source": {"doc_id": "DOC-1", "page": 1}},
        {"claim": "핵심 인력이 관련 제품을 개발한 이력이 확인됩니다.",
         "analysis": "team_analysis", "evidence_quote": "product experience",
         "source": {"doc_id": "DOC-2", "page": 2}},
    ]
    return {
        "company_info": {"name": "검토 기업"},
        "source_checked_claims": claims,
        "evidence_status": "scorable",
        "investment_terms": None,
        "document_catalog": {
            "DOC-1": {"title": "제품 원문", "file_path": "data/product.pdf", "publisher": "회사"},
            "DOC-2": {"title": "팀 원문", "file_path": "data/team.pdf"},
        },
        "feedback": ["시장 수요와 실증 단계를 분리해서 설명"],
        "evidence_gaps": [{"claim": "매출 추정", "reason": "unsupported_claim"}],
        "unverified_analysis": {"claim": "매출 999억원"},
    }


def with_grades(assessments=()):
    state = base_state()
    state.update(evaluate_investment(state, lambda payload: {
        "assessments": list(assessments), "decision": "보류", "decision_reason": "거래 조건의 추가 확인 필요",
    }))
    return state


def assessment(name="관련 전문성", grade=2, ids=None, negative=False):
    return {"criterion": name, "grade": grade, "reason": "관련 제품 경험 원문을 바탕으로 판정했습니다.",
            "evidence_ids": ids or ["E2"], "explicit_negative_confirmed": negative}


def narrative(ref="E1"):
    return {"text": "회사는 제조 작업용 로봇을 소개했습니다.", "evidence_ids": [ref],
            "interpretation": "공정 작업으로의 적용 가능성을 검토할 수 있습니다.",
            "limitation": "소개 자료만으로 현장 안정성은 입증되지 않습니다.",
            "next_check": "동일 작업의 장시간 운전 로그를 확인해야 합니다."}


def test_all_unknown_shows_21_items_reasons_and_requested_documents_without_false_zero():
    markdown = report(with_grades(), writer=lambda payload: {})["final_report"]
    assert "확인된 획득점수: 산정 보류" in markdown
    assert "평가 가능 배점: 0/100점; 미확인 배점: 100/100점" in markdown
    assert "근거 커버리지: 0%" in markdown
    for criterion in CRITERIA:
        assert f"| {criterion} | U |" in markdown
        assert f"- {criterion} (U;" in markdown
        assert NEXT_CHECKS[criterion] in markdown
    assert "확인된 획득점수: 0점" not in markdown
    assert markdown.count("\n## ") == 5


def test_positive_score_uses_original_100_weight_and_reports_company_domain_scores():
    state = with_grades([assessment()])
    markdown = report(state, writer=lambda payload: {})["final_report"]
    assert "확인된 획득점수: 3점 / 원래 100점 배점 중 확인된 획득점수" in markdown
    assert "평가 가능 배점: 6/100점; 미확인 배점: 94/100점" in markdown
    assert "근거 커버리지: 6%" in markdown
    assert "| 창업자·핵심팀 | 3/20 | 6 | 14 |" in markdown
    assert "| 관련 전문성 | 2 | 6 | 3 | [E2] |" in markdown
    assert "확인된 획득점수: 3/6점" not in markdown  # 부분 점수를 전체 점수처럼 보이지 않게 한다.
    assert "[E2] 팀 원문" in markdown  # 본문이 없어도 실제 점수 근거는 참고문헌에 남는다.


def test_explicit_confirmed_zero_and_unknown_are_distinct_in_item_table_and_coverage():
    state = with_grades([assessment(grade=0, negative=True)])
    markdown = report(state, writer=lambda payload: {})["final_report"]
    assert "| 관련 전문성 | 0 | 6 | 0 | [E2] |" in markdown
    assert "| 제품화·사업 실행력 | U | 6 | U | 미확인 |" in markdown
    assert "평가 가능 배점: 6/100점" in markdown
    assert "확인된 획득점수: 0점 / 원래 100점 배점" in markdown


@pytest.mark.parametrize("mutation", [
    lambda state: state.update(total_score=99),
    lambda state: state.update(scorable_weight=99),
    lambda state: state.update(unknown_weight=99),
    lambda state: state["score_details"]["관련 전문성"].update(score=6),
    lambda state: state["score_details"]["관련 전문성"].update(weight=10),
])
def test_upstream_score_mismatches_raise_before_writer(mutation):
    state = with_grades([assessment()])
    mutation(state)
    def should_not_write(payload):
        pytest.fail("일치하지 않는 점수로 작성 모델을 호출하면 안 됩니다")
    with pytest.raises(ValueError, match="일치하지 않습니다"):
        report(state, writer=should_not_write)


@pytest.mark.parametrize("ids", [[], ["E999"], ["E1", "E999"], [None]])
def test_new_narrative_rejects_missing_or_unregistered_evidence_ids(ids):
    block = {**narrative(), "evidence_ids": ids}
    with pytest.raises(ValueError, match="유효한 검증 근거 ID"):
        report(with_grades(), writer=lambda payload: {"technology": [block]})


def test_scored_item_cannot_cite_unregistered_evidence_ids():
    state = with_grades([assessment()])
    state["score_details"]["관련 전문성"]["evidence_ids"] = ["E999"]
    with pytest.raises(ValueError, match="등록되지 않은 평가 근거 ID"):
        report(state, writer=lambda payload: {})


def test_narrative_separates_supported_fact_from_interpretation_and_limitations():
    markdown = report(with_grades(), writer=lambda payload: {
        "technology": [narrative()], "technical_maturity": [narrative()],
    })["final_report"]
    assert "확인 사실: 회사는 제조 작업용 로봇을 소개했습니다. [E1]" in markdown
    assert "분석·해석 (추론): 공정 작업으로의 적용 가능성을 검토할 수 있습니다. [E1]" in markdown
    assert "근거의 한계: 소개 자료만으로 현장 안정성은 입증되지 않습니다." in markdown
    assert "후속 확인: 동일 작업의 장시간 운전 로그를 확인해야 합니다." in markdown
    for heading in ("기술 성숙도 관점", "시장성 관점", "이해관계자 관점", "제조·물류 도메인 적용 관점"):
        assert heading in markdown


def test_writer_receives_only_checked_facts_plus_score_feedback_and_gaps():
    state = with_grades([assessment()])
    captured = {}
    def writer(payload):
        captured.update(deepcopy(payload))
        return {"overview": [narrative()]}
    report(state, writer=writer)
    assert len(captured["score_details"]) == 21
    assert captured["score_details"]["관련 전문성"]["score"] == 3
    assert captured["score_details"]["관련 전문성"]["evidence_ids"] == ["E2"]
    assert captured["score_summary"]["unknown_weight"] == 94
    assert captured["review_feedback"] == state["feedback"]
    assert captured["evidence_gaps"] == state["evidence_gaps"]
    assert [item["id"] for item in captured["verified_evidence"]] == ["E1", "E2"]
    assert "unverified_analysis" not in captured
    assert "999억원" not in str(captured["verified_evidence"])


def test_old_id_list_writer_remains_compatible_and_does_not_include_unknown_refs():
    markdown = report(with_grades(), writer=lambda payload: {
        "overview": ["E1", "E1", "E999"], "team": ["E2"],
    })["final_report"]
    assert markdown.count("확인 사실: 회사는 제조 작업용 로봇을 소개했습니다. [E1]") == 1
    assert "[E999]" not in markdown
    assert "[E1] 제품 원문" in markdown
    assert "[E2] 팀 원문" in markdown


def test_web_reference_keeps_original_url_capture_time_and_saved_pdf_page():
    state = with_grades()
    state["document_catalog"]["DOC-1"].update(
        source_kind="web", source_url="https://example.org/robot", accessed_at="2026-10-13T10:00:00+09:00")
    markdown = report(state, writer=lambda payload: {"technology": [narrative()]})["final_report"]
    assert "웹 원문 보관 PDF p.1" in markdown
    assert "원문: https://example.org/robot" in markdown
    assert "열람: 2026-10-13T10:00:00+09:00" in markdown


def test_empty_evidence_does_not_call_model_and_still_lists_all_missing_criteria():
    state = with_grades()
    state["source_checked_claims"] = []
    def should_not_write(payload):
        pytest.fail("검증 근거가 없는 상태에서 작성 모델을 호출하면 안 됩니다")
    markdown = report(state, writer=should_not_write)["final_report"]
    assert "실제 인용한 자료 없음" in markdown
    assert all(f"| {name} | U |" in markdown for name in CRITERIA)


def test_new_model_schema_exposes_all_business_sections_and_four_views():
    assert set(ReportAnalysisDraft.model_fields) == {
        "overview", "investment_points", "market", "team", "technology", "competition",
        "performance", "risks", "technical_maturity", "market_feasibility", "stakeholders", "domain_applicability",
    }


def test_same_source_page_is_listed_once_and_preserves_each_claim_id():
    state = with_grades()
    state["source_checked_claims"][1]["source"] = {"doc_id": "DOC-1", "page": 1}
    state["document_catalog"]["DOC-1"].update(
        source_kind="web", source_url="https://example.org/original", accessed_at="2026-10-13T10:00:00+09:00")
    markdown = report(state, writer=lambda payload: {"overview": ["E1"], "team": ["E2"]})["final_report"]
    references = markdown.split("## 5 — REFERENCE", 1)[1]
    assert "- [E1] [E2] 제품 원문" in references
    assert references.count("https://example.org/original") == 1
    assert references.count("웹 원문 보관 PDF p.1") == 1
    assert "확인 사실: 회사는 제조 작업용 로봇을 소개했습니다. [E1]" in markdown
    assert "확인 사실: 핵심 인력이 관련 제품을 개발한 이력이 확인됩니다. [E2]" in markdown


def test_different_pages_keep_explicit_id_to_page_mapping_under_one_document():
    state = with_grades()
    state["source_checked_claims"][1]["source"] = {"doc_id": "DOC-1", "page": 2}
    markdown = report(state, writer=lambda payload: {"overview": ["E1"], "team": ["E2"]})["final_report"]
    references = markdown.split("## 5 — REFERENCE", 1)[1]
    assert "- [E1] 제품 원문 — data/product.pdf, p.1" in references
    assert "; p.2: [E2]" in references
    assert references.count('제품 원문')==1


def test_reference_consolidation_preserves_written_analysis_and_scores_without_model():
    state = with_grades([assessment()])
    state["source_checked_claims"][1]["source"] = {"doc_id": "DOC-1", "page": 1}
    markdown = report(state, writer=lambda payload: {"technology": [narrative()]})["final_report"]
    marker = "\n\n## 5 — REFERENCE\n\n"
    body = markdown.split(marker, 1)[0]
    old = body + marker + "- [E1] 이전 반복 문헌\n- [E2] 이전 반복 문헌\n"
    grouped = consolidate_report_references(old, state)
    assert grouped.split(marker, 1)[0] == body
    assert "- [E1] [E2] 제품 원문" in grouped
    assert "이전 반복 문헌" not in grouped
    assert consolidate_report_references(grouped, state) == grouped


def test_reference_consolidation_rejects_unregistered_ids_in_written_body():
    state = with_grades()
    with pytest.raises(ValueError, match="등록되지 않은 근거 ID"):
        consolidate_report_references("본문 [E999]\n\n## 5 — REFERENCE\n\n이전 문헌", state)


def assessment_limit(criteria=None, **extra):
    return {"basis": "assessment_limit", "assessment_criteria": criteria or ["유상 매출·납품"],
            "text": "실제 사고 99건과 유상 매출 0원을 확인했다는 허위 모델 문장",
            "evidence_ids": [], "interpretation": "사업 실패가 확정됐다는 허위 모델 해석",
            "limitation": "임의 한계 문구", "next_check": "임의 요청 문구", **extra}


def test_unknown_risk_is_generated_from_scorecard_and_discards_model_claims_and_interpretation():
    state = with_grades([assessment()])
    markdown = report(state, writer=lambda payload: {"risks": [assessment_limit()]})["final_report"]
    assert "평가 한계: 현재 검증자료로는 유상 매출·납품 항목을 확인할 수 없어 U로 표시한다." in markdown
    assert "자료 미확인은 실제 부재·성능 실패·안전 사고의 증거가 아니다." in markdown
    assert "추가 자료 — 유상 매출·납품: " + NEXT_CHECKS["유상 매출·납품"] in markdown
    assert "실제 사고 99건" not in markdown
    assert "유상 매출 0원을 확인" not in markdown
    assert "사업 실패가 확정" not in markdown
    assert "임의 한계 문구" not in markdown
    assert "임의 요청 문구" not in markdown
    assert "확인된 획득점수: 3점 / 원래 100점 배점" in markdown
    assert "| 유상 매출·납품 | U | 5 | U | 미확인 |" in markdown


@pytest.mark.parametrize("section", ["technical_maturity", "market_feasibility", "stakeholders", "domain_applicability"])
def test_four_views_can_use_explicit_unknown_assessment_limits(section):
    markdown = report(with_grades(), writer=lambda payload: {
        section: [assessment_limit(["핵심 작업 성능", "현장 안정성·안전성"])],
    })["final_report"]
    assert "평가 한계: 현재 검증자료로는 핵심 작업 성능, 현장 안정성·안전성 항목을 확인할 수 없어 U" in markdown
    assert NEXT_CHECKS["핵심 작업 성능"] in markdown
    assert NEXT_CHECKS["현장 안정성·안전성"] in markdown


@pytest.mark.parametrize("criterion,grade", [("관련 전문성", 2), ("관련 전문성", 0)])
def test_assessment_limit_rejects_scored_criteria_including_confirmed_zero(criterion, grade):
    state = with_grades([assessment(criterion, grade, negative=grade == 0)])
    with pytest.raises(ValueError, match="점수표에 명시된 U 항목"):
        report(state, writer=lambda payload: {"risks": [assessment_limit([criterion])]})


@pytest.mark.parametrize("criteria", [["유상 매출"], ["unknown"], [None], []])
def test_assessment_limit_rejects_unknown_labels_and_empty_selection(criteria):
    block = {**assessment_limit(), "assessment_criteria": criteria}
    with pytest.raises(ValueError, match="평가 한계"):
        report(with_grades(), writer=lambda payload: {"risks": [block]})


def test_assessment_limit_requires_explicit_unknown_grade_in_input_score_details():
    state = with_grades()
    del state["score_details"]["유상 매출·납품"]
    with pytest.raises(ValueError, match="점수표에 명시된 U 항목"):
        report(state, writer=lambda payload: {"risks": [assessment_limit()]})


def test_assessment_limit_cannot_create_product_overview_without_source_evidence():
    with pytest.raises(ValueError, match="사용할 수 없는 섹션"):
        report(with_grades(), writer=lambda payload: {"overview": [assessment_limit()]})


def test_assessment_limit_does_not_silently_accept_invented_source_ids():
    with pytest.raises(ValueError, match="유효한 검증 근거 ID"):
        report(with_grades(), writer=lambda payload: {
            "risks": [assessment_limit(evidence_ids=["E999"])],
        })


def test_original_narrative_defaults_to_source_evidence_and_still_needs_real_ids():
    from rag.report import AnalysisParagraph
    parsed = AnalysisParagraph.model_validate(narrative())
    assert parsed.basis == "source_evidence"
    assert parsed.assessment_criteria == []
    with pytest.raises(ValueError, match="유효한 검증 근거 ID"):
        report(with_grades(), writer=lambda payload: {
            "risks": [{**narrative(), "evidence_ids": []}],
        })


def test_summary_keeps_core_opinion_and_preserves_full_reason_in_investment_analysis():
    state = with_grades()
    state['decision_reason'] = '핵심 계약 자료가 미확인이라 보류합니다. 재구매 및 현재 거래 조건을 추가 확인해야 합니다.'
    markdown = report(state, writer=lambda payload: {})['final_report']
    summary = markdown.split('## 2 — 시장·팀 분석')[0]
    assessment = markdown.split('## 4 — 리스크 + 기업 투자평가')[1]
    assert '핵심 계약 자료가 미확인이라 보류합니다.' in summary
    assert '재구매 및 현재 거래 조건을 추가 확인해야 합니다.' not in summary
    assert state['decision_reason'] in assessment
    assert '### 비교 검토 시 해석' not in summary
    assert '### 비교 검토 시 해석' in assessment
    assert '설계 PDF 2~3쪽의 6영역·21항목·100점 고정 평가표' in summary
