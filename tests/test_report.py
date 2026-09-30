from report import report
from scorecard import SCORECARD


def sample_state():
    claim = {
        "claim": "Figure 02가 BMW 공장에서 판금 부품을 배치했다",
        "analysis": "technology_analysis",
        "source": {"doc_id": "bmw", "page": 3},
        "evidence_quote": "Figure 02가 판금 부품을 배치했다",
        "status": "verified",
    }
    details = {
        name: {
            "category": category,
            "weight": weight,
            "grade": 2 if name == "핵심 작업 성능" else None,
            "score": 3.0 if name == "핵심 작업 성능" else None,
            "reason": "현장 작업 실증" if name == "핵심 작업 성능" else "자료 없음",
            "evidence": [claim] if name == "핵심 작업 성능" else [],
        }
        for category, items in SCORECARD.items()
        for name, weight in items.items()
    }
    return {
        "company_info": {"name": "Figure AI"},
        "document_catalog": {
            "bmw": {"title": "BMW 현장 발표", "file_path": "PDF/input/bmw.pdf", "page_count": 3},
            "unused": {"title": "미사용 자료", "file_path": "PDF/input/unused.pdf", "page_count": 2},
        },
        "source_checked_claims": [claim],
        "evidence_gaps": [{"analysis": "market_competition_analysis", "claim": "시장 규모", "reason": "missing_source"}],
        "evidence_status": "scorable",
        "score_details": details,
        "total_score": 3.0,
        "scorable_weight": 6,
        "unknown_weight": 94,
        "investment_decision": "근거 부족",
        "decision_reason": "미확인 항목에 따라 판단이 달라질 수 있습니다.",
    }


def sample_draft():
    return {
        "overview": ["E1"],
        "investment_points": ["E1"],
        "market": [],
        "team": [],
        "technology": ["E1"],
        "competition": [],
        "performance": [],
        "risks": [],
    }


def test_single_company_report_has_five_sections_fixed_scores_and_used_references():
    result = report(sample_state(), writer=lambda _: sample_draft())["final_report"]

    assert result.startswith("# Figure AI 투자 검토 보고서")
    assert [f"## {page}p" in result for page in range(1, 6)] == [True] * 5
    assert "Figure 02가 BMW 공장에서 판금 부품을 배치했다 [E1]" in result
    assert "핵심 작업 성능: 2등급, 3/6점" in result
    assert "확인된 점수: 3/6점" in result
    assert "미확인 배점: 94점" in result
    assert "근거 부족" in result
    assert "BMW 현장 발표 — PDF/input/bmw.pdf, p.3" in result
    assert "미사용 자료" not in result
    assert "팀 관련 근거 미확인" in result


def test_unverified_claim_and_made_up_citation_do_not_enter_report():
    state = sample_state()
    state["technology_analysis"] = {"claims": [{"claim": "매출 1조 원"}]}
    draft = sample_draft()
    draft["investment_points"].append("E99")

    def inspect_payload(payload):
        assert payload["verified_evidence"][0]["claim"] == state["source_checked_claims"][0]["claim"]
        assert "매출 1조 원" not in str(payload)
        return draft

    result = report(state, writer=inspect_payload)["final_report"]

    assert "매출 1조 원" not in result
    assert "[E99]" not in result


def test_report_uses_exact_checked_claim_not_llm_paraphrase():
    result = report(sample_state(), writer=lambda _: sample_draft())["final_report"]

    assert "Figure 02가 BMW 공장에서 판금 부품을 배치했다" in result
    assert "사용되고 있다" not in result


def test_report_discloses_temporary_decision_threshold():
    state = sample_state()
    state["decision_policy"] = {"minimum_score": 70, "required_items": ["핵심 작업 성능"]}

    result = report(state, writer=lambda _: sample_draft())["final_report"]

    assert "테스트용 판단 기준: 70점, 필수 항목: 핵심 작업 성능" in result


def test_malformed_section_entries_are_skipped_without_losing_valid_evidence():
    draft = sample_draft()
    draft["overview"] = [{"id": "E1"}, "E1", "E1"]

    result = report(sample_state(), writer=lambda _: draft)["final_report"]
    overview = result.split("### 사업 아이디어 / 핵심 제품\n", 1)[1].split("### 핵심 투자 포인트", 1)[0]

    assert overview.count("Figure 02가 BMW 공장에서 판금 부품을 배치했다 [E1]") == 1


def test_insufficient_evidence_still_produces_report_without_llm():
    state = sample_state()
    state["evidence_status"] = "insufficient"
    state["source_checked_claims"] = []
    state["score_details"] = {}
    state["total_score"] = None

    def fail_if_called(_):
        raise AssertionError("검증된 근거가 없으면 보고서 LLM을 호출하면 안 됩니다")

    result = report(state, writer=fail_if_called)["final_report"]

    assert "## 5p — REFERENCE" in result
    assert "근거 부족" in result
    assert "실제 인용한 자료 없음" in result
    assert "BMW 현장 발표" not in result
