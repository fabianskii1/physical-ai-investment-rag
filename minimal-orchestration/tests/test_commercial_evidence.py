"""매출 방향·실제 거래 단계·같은 고객 재구매를 네트워크 없이 검증한다."""

from types import SimpleNamespace

import pytest

from investment_evaluation import evaluate_investment
from rag import workflow


COMMERCIAL_ITEMS = {
    "paid_sales_delivery": "유상 매출·납품",
    "bigtech_contract_delivery_stage": "대기업 계약·납품 단계",
    "customer_repurchase_expansion": "고객 재구매·확대",
}


def facts(text, seller="Figure", buyer="BMW", offering="humanoid robots",
          stage="paid_expansion", repeat=True):
    return {"seller": {"value": seller, "quote": text},
            "buyer": {"value": buyer, "quote": text},
            "offering": {"value": offering, "quote": text},
            "stage": {"value": stage, "quote": text},
            "same_customer_repeat": {"value": repeat, "quote": text}}


def checked(monkeypatch, text, raw_facts, criterion="paid_sales_delivery", *,
            verification_supported=True, all_facts_supported=True, injected=None):
    monkeypatch.setattr(workflow, "load_pdf", lambda *args, **kwargs: [SimpleNamespace(page_content=text)])
    calls = []

    def judge(claim, page_text):
        calls.append(claim)
        if len(calls) == 1:
            return {"verdict": "supported", "quote": text, "reason": "직접 명시",
                    "all_core_facts_supported": all_facts_supported, "commercial_facts": raw_facts}
        return {"verdict": "supported" if verification_supported else "partial",
                "quote": text, "reason": "방향과 실제 단계의 전체 의미 대조",
                "all_core_facts_supported": verification_supported}

    claim = {"claim": text, "criterion_id": criterion, "status": "confirmed",
             "source_refs": [{"doc_id": "DOC", "page": 1}], **(injected or {})}
    state = {"company_info": {"name": "Figure AI"},
             "document_catalog": {"DOC": {"source_path": "source.pdf", "page_count": 1}},
             "bigtech_analysis": {"claims": [claim]}}
    return workflow.check_evidence(state, judge), calls


def score(claim, criterion, grade=4):
    # 독립적인 제품 근거가 있어 전체 기업 평가 자체는 성립한다.
    state = {"company_info": {"name": "Figure AI"}, "evidence_status": "scorable",
             "decision_policy": {"require_criteria_coverage": True}, "investment_terms": None,
             "source_checked_claims": [claim, {"claim": "제품이 소개되었다",
                                                "analysis": "technology_analysis"}]}
    result = evaluate_investment(state, lambda _: {
        "assessments": [{"criterion": COMMERCIAL_ITEMS[criterion], "grade": grade,
                         "reason": "거래 근거로 제안한 등급", "evidence_ids": ["E1"]}],
        "decision": "보류", "decision_reason": "추가 자료 확인",
    })
    return result["score_details"][COMMERCIAL_ITEMS[criterion]]


@pytest.mark.parametrize("criterion", ["paid_sales_delivery", "bigtech_contract_delivery_stage"])
def test_nscale_compute_purchase_cannot_be_figures_sales_or_robot_delivery(monkeypatch, criterion):
    text = "Nscale supplies compute services to Figure under a paid contract for Figure's AI training."
    result, calls = checked(monkeypatch, text,
                            facts(text, seller="Nscale", buyer="Figure", offering="compute services",
                                  stage="paid_delivery", repeat=False), criterion)
    claim = result["source_checked_claims"][0]
    assert len(calls) == 2
    assert "판매·공급 주체는 Nscale" in calls[1]
    assert claim["commercial_facts"]["buyer"]["value"] == "Figure"
    item = score(claim, criterion, grade=3)
    assert item["grade"] is None
    assert item["score"] is None  # 공급자의 매출을 Figure의 0점으로 바꾸지도 않는다.


def test_first_commercial_bridge_is_not_same_customer_repurchase(monkeypatch):
    text = "Figure announces its first commercial agreement to supply humanoid robots to Catalyst."
    criterion = "customer_repurchase_expansion"
    result, _ = checked(monkeypatch, text,
                         facts(text, buyer="Catalyst", stage="commercial_agreement", repeat=False), criterion)
    assert score(result["source_checked_claims"][0], criterion)["grade"] is None


def test_future_plan_is_not_paid_delivery(monkeypatch):
    text = "Figure plans to supply humanoid robots to BMW next year; no delivery has occurred."
    result, _ = checked(monkeypatch, text, facts(text, stage="planned", repeat=False))
    assert score(result["source_checked_claims"][0], "paid_sales_delivery")["grade"] is None


@pytest.mark.parametrize("criterion", list(COMMERCIAL_ITEMS))
def test_verified_paid_expansion_to_same_customer_is_scorable(monkeypatch, criterion):
    text = ("Figure supplied humanoid robots to BMW under a paid contract. "
            "BMW renewed that contract and paid for additional humanoid robots from Figure.")
    result, calls = checked(monkeypatch, text, facts(text), criterion)
    claim = result["source_checked_claims"][0]
    assert len(calls) == 2
    assert claim["commercial_fact_validation"]["verdict"] == "supported"
    assert score(claim, criterion)["grade"] == 4


def test_contract_grade_must_match_actual_paid_delivery_stage(monkeypatch):
    text = "Figure supplied humanoid robots to BMW under a paid contract."
    criterion = "bigtech_contract_delivery_stage"
    result, _ = checked(monkeypatch, text, facts(text, stage="paid_delivery", repeat=False), criterion)
    claim = result["source_checked_claims"][0]
    assert score(claim, criterion, 3)["grade"] == 3
    assert score(claim, criterion, 4)["grade"] is None


@pytest.mark.parametrize("stage,grade,text", [
    ("mou", 1, "Figure signed an MOU to supply humanoid robots for BMW's pilot program."),
    ("pilot", 2, "Figure supplied humanoid robots to BMW for a completed unpaid factory pilot."),
])
def test_mou_and_actual_pilot_keep_their_pdf_stage_grade(monkeypatch, stage, grade, text):
    criterion = "bigtech_contract_delivery_stage"
    result, _ = checked(monkeypatch, text, facts(text, stage=stage, repeat=False), criterion)
    claim = result["source_checked_claims"][0]
    assert score(claim, criterion, grade)["grade"] == grade
    assert score(claim, criterion, 3)["grade"] is None
    assert score({**claim, "criterion_id": "paid_sales_delivery"}, "paid_sales_delivery", grade)["grade"] is None


@pytest.mark.parametrize("missing_proof", ["invalid_quote", "wrong_value", "partial_meaning"])
def test_field_label_and_existing_quote_do_not_alone_verify_commercial_facts(monkeypatch, missing_proof):
    text = "Figure supplied humanoid robots to BMW under a paid contract."
    raw = facts(text, stage="paid_delivery", repeat=False)
    if missing_proof == "invalid_quote":
        raw["stage"]["quote"] = "Paid expansion was completed."
    if missing_proof == "wrong_value":
        raw["buyer"]["value"] = "Unmentioned customer"
    result, calls = checked(monkeypatch, text, raw,
                            verification_supported=missing_proof != "partial_meaning")
    claim = result["source_checked_claims"][0]
    assert "commercial_facts" not in claim
    assert score(claim, "paid_sales_delivery")["grade"] is None
    assert len(calls) == (2 if missing_proof == "partial_meaning" else 1)


def test_worker_cannot_attach_its_own_unverified_commercial_fields(monkeypatch):
    text = "Figure introduced a humanoid robot."
    injected = {"commercial_facts": facts("Figure sells humanoid robots to BMW"),
                "commercial_fact_validation": {"verdict": "supported", "quote": "invented"}}
    result, calls = checked(monkeypatch, text, None, injected=injected)
    claim = result["source_checked_claims"][0]
    assert len(calls) == 1
    assert "commercial_facts" not in claim
    assert "commercial_fact_validation" not in claim
    assert score(claim, "paid_sales_delivery")["grade"] is None


@pytest.mark.parametrize("criterion", list(COMMERCIAL_ITEMS))
def test_old_evidence_without_transaction_facts_is_unknown(criterion):
    claim = {"claim": "A commercial relationship was announced", "analysis": "bigtech_analysis",
             "criterion_id": criterion, "evidence_quote": "commercial relationship",
             "source": {"doc_id": "DOC", "page": 1}}
    assert score(claim, criterion)["grade"] is None


def test_supported_verdict_with_missing_atomic_facts_is_rejected(monkeypatch):
    text = "ROBROS develops a VLA and Physical AI platform."
    result, calls = checked(monkeypatch, text, None, criterion="hard_to_copy_assets",
                            all_facts_supported=False)
    assert len(calls) == 1
    assert result["source_checked_claims"] == []
    assert result["evidence_gaps"][0]["reason"] == "incomplete_claim_support"


def test_partial_transaction_meaning_remains_valid_claim_but_not_valid_score(monkeypatch):
    text = "Figure and BMW announced a partnership for humanoid robots."
    result, _ = checked(monkeypatch, text, facts(text), verification_supported=False)
    assert len(result["source_checked_claims"]) == 1
    assert score(result["source_checked_claims"][0], "paid_sales_delivery")["grade"] is None
