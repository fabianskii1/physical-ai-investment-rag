from pathlib import Path

from workflow import check_evidence


def fixture_judge(claim, page_text):
    assert "BMW Group" in page_text
    return {"verdict": "supported", "quote": "BMW Group", "reason": "원문에 명시됨"}


def sample_state():
    return {
        "company_info": {"name": "Figure AI"},
        "document_catalog": {
            "bmw_announcement": {"file_path": "tests/fixtures/humanoid_pdf_loader_sample.pdf", "page_count": 3},
            "product_brief": {"file_path": "tests/fixtures/humanoid_pdf_loader_sample.pdf", "page_count": 3},
        },
        "bigtech_analysis": {
            "claims": [
                {
                    "claim": "BMW 공장에서 파일럿을 진행했다",
                    "status": "verified",
                    "source": {"doc_id": "bmw_announcement", "page": 3},
                }
            ]
        },
        "technology_analysis": {
            "claims": [
                {
                    "claim": "물품을 집어 이동하는 작업을 수행한다",
                    "status": "company_claim",
                    "source": {"doc_id": "product_brief", "page": 3},
                }
            ]
        },
        "market_competition_analysis": {"claims": []},
    }


def test_valid_citations_allow_scoring_without_market_evidence():
    result = check_evidence(sample_state(), judge=fixture_judge)

    assert result["evidence_status"] == "scorable"
    assert [item["analysis"] for item in result["source_checked_claims"]] == [
        "bigtech_analysis",
        "technology_analysis",
    ]
    assert result["evidence_gaps"] == []
    assert result["source_checked_claims"][0]["evidence_quote"] == "BMW Group"


def test_analysis_confirmed_claim_is_rechecked_against_original_pdf():
    state = sample_state()
    state["technology_analysis"]["claims"][0]["status"] = "confirmed"

    result = check_evidence(state, judge=fixture_judge)

    assert result["evidence_status"] == "scorable"
    assert len(result["source_checked_claims"]) == 2


def test_later_cited_page_can_support_claim_when_first_source_is_invalid():
    state = sample_state()
    state["technology_analysis"]["claims"][0] = {
        "claim": "물품을 집어 이동하는 작업을 수행한다",
        "status": "confirmed",
        "source": {"doc_id": "missing", "page": 1},
        "source_refs": [
            {"doc_id": "missing", "page": 1},
            {"doc_id": "product_brief", "page": 3},
        ],
    }

    result = check_evidence(state, judge=fixture_judge)

    assert result["evidence_status"] == "scorable"
    assert result["source_checked_claims"][1]["source"] == {"doc_id": "product_brief", "page": 3}
    assert result["evidence_gaps"] == []


def test_resolved_source_path_is_used_for_pdf_check():
    state = sample_state()
    state["document_catalog"]["product_brief"]["file_path"] = "PDF/input/not-here.pdf"
    state["document_catalog"]["product_brief"]["source_path"] = str(
        Path(__file__).parent / "fixtures/humanoid_pdf_loader_sample.pdf"
    )

    result = check_evidence(state, judge=fixture_judge)

    assert result["evidence_status"] == "scorable"
    assert result["evidence_gaps"] == []


def test_multiple_failed_sources_count_as_one_excluded_claim():
    state = sample_state()
    state["technology_analysis"]["claims"][0] = {
        "claim": "물품을 집어 이동하는 작업을 수행한다",
        "status": "confirmed",
        "source_refs": [
            {"doc_id": "missing", "page": 1},
            {"doc_id": "product_brief", "page": 4},
        ],
    }

    result = check_evidence(state, judge=fixture_judge)

    assert result["evidence_status"] == "insufficient"
    assert len(result["evidence_gaps"]) == 1
    assert result["evidence_gaps"][0]["reason"] == "all_sources_rejected"
    assert len(result["evidence_gaps"][0]["source_attempts"]) == 2


def test_missing_bigtech_source_is_gap_not_zero_or_total_failure():
    state = sample_state()
    state["bigtech_analysis"]["claims"][0].pop("source")

    result = check_evidence(state, judge=fixture_judge)

    assert result["evidence_status"] == "scorable"
    assert len(result["source_checked_claims"]) == 1
    assert result["evidence_gaps"] == [
        {"analysis": "bigtech_analysis", "claim": "BMW 공장에서 파일럿을 진행했다", "reason": "missing_source"}
    ]


def test_unknown_or_out_of_range_source_cannot_support_product_claim():
    state = sample_state()
    state["technology_analysis"]["claims"] = [
        {"claim": "작업 A", "status": "verified", "source": {"doc_id": "missing", "page": 1}},
        {"claim": "작업 B", "status": "verified", "source": {"doc_id": "product_brief", "page": 4}},
        {"claim": "작업 C", "status": "unknown", "source": {"doc_id": "product_brief", "page": 3}},
    ]

    result = check_evidence(state, judge=fixture_judge)

    assert result["evidence_status"] == "insufficient"
    assert [gap["reason"] for gap in result["evidence_gaps"]] == [
        "unknown_doc_id",
        "invalid_page",
        "unconfirmed_claim",
    ]
    assert [item["analysis"] for item in result["source_checked_claims"]] == ["bigtech_analysis"]


def test_missing_company_name_is_insufficient_even_with_valid_citations():
    state = sample_state()
    state["company_info"]["name"] = " "

    assert check_evidence(state, judge=fixture_judge)["evidence_status"] == "insufficient"


def test_missing_company_info_skips_llm_and_returns_insufficient():
    state = sample_state()
    state["company_info"] = None

    def fail_if_called(claim, page_text):
        raise AssertionError("회사 정보 없이 LLM을 호출하면 안 됩니다")

    result = check_evidence(state, judge=fail_if_called)

    assert result["evidence_status"] == "insufficient"
    assert result["evidence_gaps"] == [
        {"analysis": "company_info", "claim": "", "reason": "missing_company"}
    ]


def test_catalog_needs_a_document_location():
    state = sample_state()
    state["document_catalog"]["product_brief"]["file_path"] = ""

    result = check_evidence(state, judge=fixture_judge)

    assert result["evidence_status"] == "insufficient"
    assert result["evidence_gaps"] == [
        {"analysis": "technology_analysis", "claim": "물품을 집어 이동하는 작업을 수행한다", "reason": "missing_file_path"}
    ]


def test_claim_cannot_override_its_analysis_origin():
    state = sample_state()
    state["bigtech_analysis"]["claims"][0]["analysis"] = "technology_analysis"
    state["technology_analysis"]["claims"] = []

    result = check_evidence(state, judge=fixture_judge)

    assert result["evidence_status"] == "insufficient"
    assert result["source_checked_claims"][0]["analysis"] == "bigtech_analysis"


def test_llm_rejection_prevents_scoring_despite_valid_page():
    state = sample_state()

    def reject_product(claim, page_text):
        assert "Figure 02" in page_text
        if "물품" in claim:
            return {"verdict": "unsupported", "quote": "", "reason": "원문에 물품 이동이 없음"}
        return fixture_judge(claim, page_text)

    result = check_evidence(state, judge=reject_product)

    assert result["evidence_status"] == "insufficient"
    assert [item["analysis"] for item in result["source_checked_claims"]] == ["bigtech_analysis"]
    assert result["evidence_gaps"] == [
        {"analysis": "technology_analysis", "claim": "물품을 집어 이동하는 작업을 수행한다", "reason": "unsupported_claim", "detail": "원문에 물품 이동이 없음"}
    ]


def test_supported_verdict_requires_quote_present_on_original_page():
    state = sample_state()

    def invent_quote(claim, page_text):
        if "물품" in claim:
            return {"verdict": "supported", "quote": "없는 원문 문장", "reason": ""}
        return fixture_judge(claim, page_text)

    result = check_evidence(state, judge=invent_quote)

    assert result["evidence_status"] == "insufficient"
    assert result["evidence_gaps"][0]["reason"] == "invalid_quote"


def test_missing_original_pdf_is_not_treated_as_valid_evidence():
    state = sample_state()
    state["document_catalog"]["product_brief"]["file_path"] = "tests/fixtures/missing.pdf"

    result = check_evidence(state, judge=fixture_judge)

    assert result["evidence_status"] == "insufficient"
    assert result["evidence_gaps"][0]["reason"] == "source_unreadable"
