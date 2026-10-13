"""저장 원문 재검증이 새 조사·기존 점수 재사용으로 확장되지 않도록 확인한다."""

from copy import deepcopy

import pytest

from investment_evaluation import evaluate_investment
from rag.workflow import CriterionAssessment, _protect_declared_performance_stage
from saved_revalidation import revalidate_saved_research


def saved_state():
    claims = [
        {"claim": "향후 생산능력을 현재 확보했다는 옛 주장", "criterion_id": "mass_production_maintenance",
         "analysis": "technology_analysis", "status": "confirmed", "source": {"doc_id": "D1", "page": 1},
         "source_refs": [{"doc_id": "D1", "page": 1}, {"doc_id": "unused", "page": 2}]},
        {"claim": "로봇의 사양이 공개되어 있다", "criterion_id": "core_task_performance",
         "analysis": "technology_analysis", "status": "confirmed", "source": {"doc_id": "D2", "page": 1}},
    ]
    return {"company_info": {"name": "기업"}, "document_catalog": {"D1": {}, "D2": {}},
            "investment_terms": None, "decision_policy": {}, "trace_id": "old-trace",
            "research_result": {"source_checked_claims": claims, "evidence_gaps": [{"claim": "미확인 계약", "reason": "unconfirmed_claim"}],
                "criterion_attempts": {"core_task_performance": [{"round": 0}]},
                "last_verified_signature": "old", "score_review_issues": ["old E-ID"],
                "technology_analysis": {"claims": [{"claim": "과거 워커의 제외된 세계 최고 홍보"}]}},
            "worker_results": {"0:worker": {"analysis": {"claims": [{"claim": "검증에서 제외된 사실"}]}}},
            "investment_result": {"total_score": 99}, "final_report": "옛 보고서 [E1]"}


def test_revalidates_only_saved_accepted_claims_once_and_rebuilds_ids_without_mutating_state():
    state = saved_state()
    original = deepcopy(state)
    calls = []

    def checker(payload):
        calls.append(payload)
        claims = payload["technology_analysis"]["claims"]
        assert len(claims) == 2
        assert claims[0]["source_refs"] == [{"doc_id": "D1", "page": 1}]
        assert all("세계 최고" not in claim["claim"] for claim in claims)
        return {"source_checked_claims": [claims[1]], "evidence_status": "scorable",
                "evidence_gaps": [{"analysis": "technology_analysis", "claim": claims[0]["claim"], "reason": "partial_support"}]}

    result = revalidate_saved_research(state, checker)
    assert len(calls) == 1
    assert state == original
    assert result["coverage_by_criterion"]["core_task_performance"]["evidence_ids"] == ["E1"]
    assert result["coverage_by_criterion"]["mass_production_maintenance"]["evidence_ids"] == []
    assert {gap["reason"] for gap in result["evidence_gaps"]} == {"unconfirmed_claim", "partial_support"}
    assert "last_verified_signature" not in result
    assert "score_review_issues" not in result
    assert result["saved_source_revalidation"]["worker_or_retrieval_executed"] is False
    assert result["saved_source_revalidation"]["investment_regrading_required"] is True


def test_current_terms_revalidation_uses_only_previously_checked_manual_claims():
    state = saved_state()
    state["investment_terms"] = {"claims": [{"claim": "추가 입력을 여기서 새로 검증하면 안 됨"}]}
    manual = {"claim": "기존 투자계약 권리", "analysis": "investment_terms", "status": "confirmed",
              "criterion_id": "지분·권리 조건", "source": {"doc_id": "D2", "page": 1}}
    state["research_result"]["source_checked_claims"].append(manual)
    captured = {}

    def checker(payload):
        captured.update(payload)
        return {"source_checked_claims": [], "evidence_status": "insufficient", "evidence_gaps": []}

    result = revalidate_saved_research(state, checker)
    assert captured["investment_terms"]["claims"][0]["claim"] == manual["claim"]
    assert len(captured["investment_terms"]["claims"]) == 1
    assert result["coverage_by_criterion"]["equity_rights"]["status"] == "manual_unverified"


@pytest.mark.parametrize("grade,reason", [
    (3, "제어 알고리즘은 공개됐지만 반복 운영과 실측 성능 검증 근거는 없다."),
    (3, "다수 현장의 재현과 반복적인 정량 데이터는 부재함."),
    (4, "정량 성능은 확인되지만 여러 현장의 재현 근거는 미확인이다."),
])
def test_declared_missing_required_performance_stage_cannot_keep_grade_three_or_four(grade, reason):
    item = {"criterion": "핵심 작업 성능", "grade": grade, "reason": reason, "evidence_ids": ["E1"],
            "anchor_grounded": True}
    result = _protect_declared_performance_stage(item)
    assert result["grade"] is None
    assert result["evidence_ids"] == []
    assert "U로 유보" in result["reason"]


def test_single_site_repeat_quantitative_grade_three_does_not_require_multiple_sites():
    item = {"criterion": "핵심 작업 성능", "grade": 3,
            "reason": "고객 현장에서 반복 운영과 성공률 실측 검증이 있다. 다수 현장 검증은 없다.",
            "evidence_ids": ["E1"], "anchor_grounded": True}
    assert _protect_declared_performance_stage(item) == item


def test_real_grade_schema_requires_anchor_proof_and_false_anchor_returns_unknown_with_source_metadata():
    with pytest.raises(ValueError):
        CriterionAssessment(criterion="관련 전문성", grade=4, reason="회사 홍보", evidence_ids=["E1"])
    captured = {}
    state = {"company_info": {"name": "기업"}, "evidence_status": "scorable", "investment_terms": None,
             "source_checked_claims": [{"claim": "세계 최고라는 회사 표현", "analysis": "technology_analysis"}],
             "document_catalog": {"DOC": {"title": "회사 소개", "publisher": "기업", "source_url": "https://company.test"}}}

    def evaluator(payload):
        captured.update(payload)
        return {"assessments": [{"criterion": "관련 전문성", "grade": 4, "reason": "회사 홍보만 있음",
                                  "evidence_ids": ["E1"], "anchor_grounded": False}]}

    result = evaluate_investment(state, evaluator)
    assert captured["source_catalog"]["DOC"]["publisher"] == "기업"
    assert result["score_details"]["관련 전문성"]["grade"] is None
    assert result["total_score"] is None
