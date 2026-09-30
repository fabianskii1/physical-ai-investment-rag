from scorecard import SCORECARD
from workflow import investment_judgement


CRITERIA = [name for items in SCORECARD.values() for name in items]
PRODUCT = "핵심 작업 성능"


def state_with_evidence(threshold=6):
    return {
        "company_info": {"name": "Figure AI"},
        "evidence_status": "scorable",
        "source_checked_claims": [
            {
                "claim": "Figure 02가 판금 부품을 고정 장치에 배치했다",
                "analysis": "technology_analysis",
                "source": {"doc_id": "bmw", "page": 3},
                "evidence_quote": "Figure 02가 판금 부품을 특정 고정 장치에 배치",
                "status": "verified",
            }
        ],
        "decision_policy": {"minimum_score": threshold, "required_items": [PRODUCT]},
    }


def graded_product(grade):
    return [
        {
            "criterion": name,
            "grade": grade if name == PRODUCT else None,
            "reason": "현장 작업 근거" if name == PRODUCT else "자료 없음",
            "evidence_ids": ["E1"] if name == PRODUCT else [],
        }
        for name in CRITERIA
    ]


def test_verified_evidence_is_graded_and_score_is_not_rescaled():
    result = investment_judgement(state_with_evidence(), grader=lambda _: graded_product(4))

    assert result["total_score"] == 6
    assert result["unknown_weight"] == 94
    assert result["score_details"][PRODUCT]["evidence"][0]["source"] == {"doc_id": "bmw", "page": 3}
    assert result["investment_decision"] == "우선 검토"


def test_uncertain_score_range_is_insufficient_not_zero():
    result = investment_judgement(state_with_evidence(threshold=50), grader=lambda _: graded_product(0))

    assert result["total_score"] == 0
    assert PRODUCT not in result["unknown_items"]
    assert result["investment_decision"] == "근거 부족"


def test_even_best_unknown_scores_cannot_reach_threshold_so_hold():
    result = investment_judgement(state_with_evidence(threshold=95), grader=lambda _: graded_product(0))

    assert result["investment_decision"] == "보류"


def test_unknown_required_item_blocks_decision():
    state = state_with_evidence()
    state["decision_policy"]["required_items"] = ["유상 매출·납품"]

    result = investment_judgement(state, grader=lambda _: graded_product(4))

    assert result["investment_decision"] == "근거 부족"


def test_missing_policy_does_not_invent_investment_cutoff():
    state = state_with_evidence()
    del state["decision_policy"]

    result = investment_judgement(state, grader=lambda _: graded_product(4))

    assert result["investment_decision"] == "근거 부족"
    assert result["total_score"] == 6


def test_unverified_reference_cannot_earn_points():
    grades = graded_product(4)
    grades[CRITERIA.index(PRODUCT)]["evidence_ids"] = ["E999"]

    result = investment_judgement(state_with_evidence(), grader=lambda _: grades)

    assert result["score_details"][PRODUCT]["grade"] is None
    assert result["investment_decision"] == "근거 부족"


def test_misspelled_criterion_is_unknown_not_a_score():
    grades = graded_product(4)
    grades[CRITERIA.index(PRODUCT)]["criterion"] = "제품·기술력-핵심 작업 성능"

    result = investment_judgement(state_with_evidence(), grader=lambda _: grades)

    assert result["score_details"][PRODUCT]["grade"] is None
    assert result["investment_decision"] == "근거 부족"


def test_duplicate_criterion_is_unknown():
    grades = graded_product(4)
    grades.append({**grades[CRITERIA.index(PRODUCT)], "grade": 0})

    result = investment_judgement(state_with_evidence(), grader=lambda _: grades)

    assert result["score_details"][PRODUCT]["grade"] is None


def test_zero_grade_without_reference_becomes_unknown():
    grades = graded_product(0)
    grades[CRITERIA.index(PRODUCT)]["evidence_ids"] = []

    result = investment_judgement(state_with_evidence(), grader=lambda _: grades)

    assert PRODUCT in result["unknown_items"]
    assert result["score_details"][PRODUCT]["grade"] is None
    assert result["investment_decision"] == "근거 부족"


def test_technical_pilot_cannot_score_bigtech_contract():
    grades = graded_product(4)
    contract = "대기업 계약·납품 단계"
    grades[CRITERIA.index(contract)] = {
        "criterion": contract,
        "grade": 3,
        "reason": "기술 실증을 상용 계약으로 오인",
        "evidence_ids": ["E1"],
    }

    result = investment_judgement(state_with_evidence(), grader=lambda _: grades)

    assert result["score_details"][contract]["grade"] is None
    assert contract in result["unknown_items"]


def test_unknown_may_reference_insufficient_source_without_scoring():
    result = investment_judgement(state_with_evidence(), grader=lambda _: graded_product(None))

    assert result["score_details"][PRODUCT]["grade"] is None
    assert result["score_details"][PRODUCT]["evidence"][0]["source"]["page"] == 3
    assert result["total_score"] == 0


def test_claim_cannot_override_generated_evidence_id():
    state = state_with_evidence()
    state["source_checked_claims"][0]["id"] = "E999"

    def inspect_payload(payload):
        assert payload["verified_evidence"][0]["id"] == "E1"
        return graded_product(4)

    assert investment_judgement(state, grader=inspect_payload)["total_score"] == 6


def test_insufficient_evidence_skips_llm_and_scoring():
    state = state_with_evidence()
    state["evidence_status"] = "insufficient"

    def fail_if_called(_):
        raise AssertionError("근거 부족이면 LLM을 호출하면 안 됩니다")

    result = investment_judgement(state, grader=fail_if_called)

    assert result["investment_decision"] == "근거 부족"
    assert result["total_score"] is None
