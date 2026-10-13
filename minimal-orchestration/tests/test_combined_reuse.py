"""저장된 합성·선정 결과는 동일한 원보고서와 현재 항목 점수에만 재사용한다."""

from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

import app
import legacy_adapter
from combined_report import _normalise_records, build_combined_report, combined_source_contexts
from report_renderer import render_report
from test_combined_report import CRITERIA, portfolio, records, set_grade
from test_report_synthesis import inputs_and_synthesis


@pytest.fixture
def analysis_cache(records, tmp_path):
    _, _, synthesis = inputs_and_synthesis(records)
    built = build_combined_report(records, report_synthesis=synthesis, portfolio_decision=portfolio())
    companies = _normalise_records(records)
    all_references = " ".join(
        f"[{company['prefix']}-{ref}]" for company in companies for ref in company["evidence"])
    cached = {
        "companies": deepcopy(built["comparison"]),
        "deterministic_checks": deepcopy(built["deterministic_checks"]),
        "report_synthesis": deepcopy(built["report_synthesis"]),
        "portfolio_decision": deepcopy(built["portfolio_decision"]),
        "company_reports": deepcopy(built["company_report_inputs"]),
        "source_score_details": {company["company"]: deepcopy(company["details"]) for company in companies},
        "source_contexts": deepcopy(combined_source_contexts(records, all_references)),
        "report_evaluation": {"passed": False, "issues": ["이전 품질 판정은 재사용 대상이 아니다."]},
    }
    path = tmp_path / "previous_final_report.json"
    path.write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")
    return path, cached, built


def write_cache(path, cached):
    path.write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")


def test_valid_reuse_returns_only_revalidated_synthesis_and_portfolio_without_model_call(
    records, analysis_cache, monkeypatch
):
    path, cached, built = analysis_cache
    request = Mock(side_effect=AssertionError("분석 재사용 검증은 모델을 호출하면 안 된다."))
    monkeypatch.setattr(legacy_adapter, "_structured_request", request)
    before = deepcopy(records)

    synthesis, selection = app._load_combined_analysis(path, records)

    assert synthesis == built["report_synthesis"]
    assert selection == built["portfolio_decision"]
    assert selection["action"] == "조건부 투자"
    assert synthesis["source_reports"] == cached["report_synthesis"]["source_reports"]
    assert "report_evaluation" not in synthesis and "report_evaluation" not in selection
    assert records == before
    request.assert_not_called()


def test_same_trace_with_changed_original_report_rejects_stale_synthesis(records, analysis_cache):
    path, _, _ = analysis_cache
    trace = records[0]["state"]["trace_id"]
    records[0]["state"]["final_report"] += "\n후속 확인: 고객 계약의 유상 여부를 별도로 검증한다.\n"
    assert records[0]["state"]["trace_id"] == trace

    with pytest.raises(ValueError, match="원본 기업보고서 본문이 변경"):
        app._load_combined_analysis(path, records)


def test_changed_current_score_rejects_cached_selection(records, analysis_cache):
    path, _, _ = analysis_cache
    set_grade(records[0], CRITERIA[0], 2)

    with pytest.raises(ValueError):
        app._load_combined_analysis(path, records)


def test_missing_current_criterion_rejects_cache_even_when_total_is_unchanged(records, analysis_cache):
    path, _, _ = analysis_cache
    records[0]["state"]["investment_result"]["score_details"].pop(CRITERIA[0])
    records[0]["scores"] = {**records[0]["scores"],
                           **deepcopy(records[0]["state"]["investment_result"])}

    with pytest.raises(ValueError):
        app._load_combined_analysis(path, records)


def test_changed_criterion_grades_with_equal_totals_reject_cache(records, analysis_cache):
    path, cached, _ = analysis_cache
    before = records[0]["state"]["investment_result"]
    total, coverage = before["total_score"], before["scorable_weight"]
    # 서로 배점이 같은 항목을 반대로 바꾸면 총점·커버리지는 같지만 판단 근거는 달라진다.
    set_grade(records[0], "관련 전문성", 2)
    set_grade(records[0], "제품화·사업 실행력", 4)
    current = records[0]["state"]["investment_result"]
    assert current["total_score"] == total
    assert current["scorable_weight"] == coverage
    assert build_combined_report(records)["comparison"] == cached["companies"]

    with pytest.raises(ValueError):
        app._load_combined_analysis(path, records)


def test_changed_criterion_reason_rejects_cache_even_when_grade_is_unchanged(records, analysis_cache):
    path, _, _ = analysis_cache
    records[0]["state"]["investment_result"]["score_details"][CRITERIA[0]]["reason"] = "새 확인 자료로 판단 근거가 달라졌다."
    records[0]["scores"] = {**records[0]["scores"],
                           **deepcopy(records[0]["state"]["investment_result"])}

    with pytest.raises(ValueError, match="21항목 점수·사유"):
        app._load_combined_analysis(path, records)


def test_pre_snapshot_cache_can_reuse_matching_markdown_score_table(records, analysis_cache):
    path, cached, built = analysis_cache
    cached.pop("source_score_details")
    write_cache(path, cached)
    path.with_suffix(".md").write_text(built["markdown"], encoding="utf-8")

    synthesis, selection = app._load_combined_analysis(path, records)

    assert synthesis == built["report_synthesis"]
    assert selection == built["portfolio_decision"]


def test_pre_snapshot_cache_requires_score_table_sidecar(records, analysis_cache):
    path, cached, _ = analysis_cache
    cached.pop("source_score_details")
    write_cache(path, cached)

    with pytest.raises(ValueError, match="snapshot|Markdown 점수표"):
        app._load_combined_analysis(path, records)


def test_pre_snapshot_cache_rejects_different_criterion_with_same_totals(records, analysis_cache):
    path, cached, built = analysis_cache
    cached.pop("source_score_details")
    write_cache(path, cached)
    path.with_suffix(".md").write_text(built["markdown"], encoding="utf-8")
    set_grade(records[0], "관련 전문성", 2)
    set_grade(records[0], "제품화·사업 실행력", 4)

    with pytest.raises(ValueError, match="21항목 점수가 변경"):
        app._load_combined_analysis(path, records)


def test_changed_current_verified_quote_rejects_analysis_reuse(records, analysis_cache):
    path, _, _ = analysis_cache
    records[0]["state"]["research_result"]["source_checked_claims"][0]["evidence_quote"] = "원문에는 제조 현장용 로봇"
    # 새 점수·case가 새 인용과 내부적으로 맞더라도 이전 분석의 근거와는 달라졌다.
    set_grade(records[0], CRITERIA[0], 3)

    with pytest.raises(ValueError, match="점수·사유|원문 주장·인용"):
        app._load_combined_analysis(path, records)


@pytest.mark.parametrize("change", ("claim", "quote", "source", "missing_context"))
def test_cached_source_claims_must_match_current_verified_evidence(records, analysis_cache, change):
    path, cached, _ = analysis_cache
    if change == "missing_context":
        cached["source_contexts"].pop()
    else:
        claim = cached["source_contexts"][0]["checked_claims"][0]
        if change == "claim":
            claim["claim"] = "과거 캐시에만 있는 다른 사업 주장"
        elif change == "quote":
            claim["evidence_quote"] = "예전 인용"
        else:
            claim["source"]["page"] = 99
    write_cache(path, cached)

    with pytest.raises(ValueError, match="검증된 원문 주장·인용"):
        app._load_combined_analysis(path, records)


def test_changed_raw_pdf_page_rejects_cache_even_when_checked_quote_still_matches(records, analysis_cache):
    path, _, _ = analysis_cache
    claims = deepcopy(records[0]["state"]["research_result"]["source_checked_claims"])
    source_path = records[0]["state"]["document_catalog"]["DOC"]["source_path"]
    quote = claims[0]["evidence_quote"]
    render_report("# 검증용 회사 자료\n" + quote + "\n\n원본 페이지에 새 제한 사항이 추가됐다.", source_path)
    assert records[0]["state"]["research_result"]["source_checked_claims"] == claims

    with pytest.raises(ValueError, match="원본 페이지 또는 출처가 변경"):
        app._load_combined_analysis(path, records)


@pytest.mark.parametrize("field", ("source_url", "title", "publisher"))
def test_changed_current_source_metadata_rejects_reuse(records, analysis_cache, field):
    path, _, _ = analysis_cache
    records[0]["state"]["document_catalog"]["DOC"][field] = {
        "source_url": "https://other-source.example.test/revised",
        "title": "새 출처 제목",
        "publisher": "다른 발행기관",
    }[field]

    with pytest.raises(ValueError, match="원본 페이지 또는 출처가 변경"):
        app._load_combined_analysis(path, records)


def test_moving_identical_pdf_without_source_metadata_change_allows_reuse(records, analysis_cache, tmp_path):
    path, _, built = analysis_cache
    source_path = Path(records[0]["state"]["document_catalog"]["DOC"]["source_path"])
    original_bytes = source_path.read_bytes()
    moved_path = tmp_path / "moved" / "same_source.pdf"
    moved_path.parent.mkdir()
    source_path.rename(moved_path)
    for record in records:
        record["state"]["document_catalog"]["DOC"]["source_path"] = str(moved_path)
    assert moved_path.read_bytes() == original_bytes
    assert not source_path.exists()

    synthesis, selection = app._load_combined_analysis(path, records)

    assert synthesis == built["report_synthesis"]
    assert selection == built["portfolio_decision"]


@pytest.mark.parametrize("change", ("wrong_company", "missing_company", "different_trace", "different_status"))
def test_cached_company_identity_and_run_summary_must_match_current_records(
    records, analysis_cache, change
):
    path, cached, _ = analysis_cache
    if change == "wrong_company":
        cached["companies"][0]["company"] = "다른 회사"
    elif change == "missing_company":
        cached["companies"].pop()
    elif change == "different_trace":
        cached["companies"][0]["trace_id"] = "previous-other-run"
    else:
        cached["companies"][0]["source_status"] = "quality_failed"
    write_cache(path, cached)

    with pytest.raises(ValueError, match="원기업 점수·조사 상태·실행 ID"):
        app._load_combined_analysis(path, records)


@pytest.mark.parametrize("missing", ("synthesis_flag", "report_synthesis", "source_reports", "portfolio_decision"))
def test_legacy_or_incomplete_final_json_cannot_be_reused(records, analysis_cache, missing):
    path, cached, _ = analysis_cache
    if missing == "synthesis_flag":
        cached["deterministic_checks"].pop("company_reports_synthesized")
    elif missing == "source_reports":
        cached["report_synthesis"].pop("source_reports")
    else:
        cached.pop(missing)
    write_cache(path, cached)

    with pytest.raises(ValueError, match="재사용|원기업 보고서 해시"):
        app._load_combined_analysis(path, records)
