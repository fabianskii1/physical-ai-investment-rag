"""통합 입력은 실제 기업 보고서의 인용 없는 제약·의견도 끝까지 보존한다."""

from copy import deepcopy
from hashlib import sha256

import pytest

from company_report_inputs import build_company_report_inputs


def company(report, *, prefix="MM", name="MOTOMIND", evidence=None):
    return {"company": name, "prefix": prefix, "trace_id": "run-company",
            "state": {"final_report": report},
            "evidence": {"E1": {"claim": "confirmed"}, "E2": {"claim": "second"}} if evidence is None else evidence,
            "details": {}, "score_refs": {}}


def test_full_original_report_hash_and_uncited_paragraphs_are_preserved():
    report = ("# 기업 보고서\n\n## 1 — 기업 분석\n### 제품\n"
              "확인 사실: 시제품을 회사가 발표했다. [E1]\n\n"
              "분석·해석 (추론): 고객 적용을 검토할 단서다. [E1] [E2]\n\n"
              "근거의 한계: 독립 성능 검증은 미확인이다.\n\n"
              "후속 확인: 고객별 원문 계약을 확보해야 한다.\n\n"
              "### 최종 투자 제안\n**투자 보류** — 현재 거래조건 확인 후 판단한다.\n\n"
              "## 5 — REFERENCE\n- [E1] [E2] 등록 원문, p.1\n")
    records = [company(report)]
    original = deepcopy(records)
    result = build_company_report_inputs(records)[0]
    assert result["report_markdown"] == report
    assert result["report_sha256"] == sha256(report.encode("utf-8")).hexdigest()
    assert result["trace_id"] == "run-company"
    paragraphs = result["paragraphs"]
    assert [item["paragraph_id"] for item in paragraphs] == [f"MM-P{i:03d}" for i in range(1, 6)]
    assert [item["kind"] for item in paragraphs] == ["fact", "analysis", "limitation", "follow_up", "opinion"]
    assert paragraphs[0]["section"] == "1 — 기업 분석 > 제품"
    assert paragraphs[0]["text"].endswith("[MM-E1]")
    assert paragraphs[1]["evidence_ids"] == ["MM-E1", "MM-E2"]
    assert paragraphs[2]["evidence_ids"] == paragraphs[3]["evidence_ids"] == paragraphs[4]["evidence_ids"] == []
    assert records == original


def test_all_late_uncited_limitations_conditions_and_opinions_remain_available():
    early = "\n\n".join(f"확인 사실: 앞 문단 {i}. [E1]" for i in range(150))
    report = (f"## 기술\n{early}\n\n### 주요 판단 제약\n"
              "마지막 제약은 고객 재구매가 미확인이라는 점이다.\n\n"
              "### 투자 전 필수 조건\n- 고객 계약 조건을 확인한다.\n- 독립 성능 검증을 확보한다.\n\n"
              "### 최종 투자 제안\n현재는 투자 보류 의견이다.\n")
    result = build_company_report_inputs([company(report)])[0]
    paragraphs = result["paragraphs"]
    assert len(paragraphs) == 154
    assert [item["kind"] for item in paragraphs[-4:]] == ["limitation", "follow_up", "follow_up", "opinion"]
    assert paragraphs[-1]["paragraph_id"] == "MM-P154"
    assert "마지막 제약" in paragraphs[-4]["text"]


def test_tables_and_reference_are_kept_in_full_input_but_excluded_from_paragraph_index():
    report = ("## 평가\n확인 사실: 기술 자료를 발표했다. [E1]\n\n"
              "| 항목 | 근거 |\n|---|---|\n| 성능 | [E2] |\n\n"
              "항목 | 근거\n--- | ---\n매출 | [E1]\n\n"
              "일반 본문은 표 다음에도 보존한다.\n\n"
              "## 5 — REFERENCE\n### 원문 목록\n- [E1] [E2] 문서 p.2\n\n"
              "## 6 — 최종 투자 의견\n후속 확인: 현재 투자조건을 확인한다.\n")
    result = build_company_report_inputs([company(report)])[0]
    assert result["report_markdown"] == report
    texts = [item["text"] for item in result["paragraphs"]]
    assert texts == ["확인 사실: 기술 자료를 발표했다. [MM-E1]", "일반 본문은 표 다음에도 보존한다.",
                     "후속 확인: 현재 투자조건을 확인한다."]


@pytest.mark.parametrize("location", ["body", "table", "reference"])
def test_undeclared_evidence_is_rejected_in_every_report_part(location):
    tails = {"body": "\n분석: 미등록 주장. [E99]\n",
             "table": "\n| 항목 | 근거 |\n|---|---|\n| 주장 | [E99] |\n",
             "reference": "\n## 5 — REFERENCE\n- [E99] 문서\n"}
    with pytest.raises(ValueError, match="등록되지 않은 근거 ID"):
        build_company_report_inputs([company("## 분석\n확인 사실: 등록 주장. [E1]\n" + tails[location])])


def test_grouped_and_existing_namespace_citations_are_canonical_and_deduplicated():
    report = "## 분석\n확인 사실: 공동 근거. [E1, E2] [MM-E1] [E2; E1]\n"
    paragraph = build_company_report_inputs([company(report)])[0]["paragraphs"][0]
    assert paragraph["evidence_ids"] == ["MM-E1", "MM-E2"]
    assert paragraph["text"] == "확인 사실: 공동 근거. [MM-E1] [MM-E2] [MM-E1] [MM-E2] [MM-E1]"
    with pytest.raises(ValueError, match="다른 기업의 근거 ID"):
        build_company_report_inputs([company("## 분석\n확인 사실: 섞인 기업 근거. [RB-E1]\n")])


def test_company_namespaces_do_not_collide_for_same_local_evidence_id():
    result = build_company_report_inputs([company("## 분석\n사실: 첫 기업. [E1]\n"),
        company("## 분석\n사실: 두 번째 기업. [E1]\n", prefix="RB", name="ROBROS")])
    assert [item["paragraphs"][0]["paragraph_id"] for item in result] == ["MM-P001", "RB-P001"]
    assert [item["paragraphs"][0]["evidence_ids"] for item in result] == [["MM-E1"], ["RB-E1"]]


def test_no_citation_does_not_require_fabricated_evidence():
    record = company("## 주요 판단 제약\n자료 미확인으로 판단을 유보한다.\n", evidence={})
    result = build_company_report_inputs([record])[0]
    assert result["paragraphs"][0]["kind"] == "limitation"
    assert result["paragraphs"][0]["evidence_ids"] == []


@pytest.mark.parametrize("report", [None, "", "  \n", 12])
def test_actual_final_report_body_is_required(report):
    with pytest.raises(ValueError, match="final_report 본문"):
        build_company_report_inputs([company(report)])


def test_reference_only_input_is_not_a_substitute_for_company_body():
    with pytest.raises(ValueError, match="분석할 본문 문단"):
        build_company_report_inputs([company("## 5 — REFERENCE\n- [E1] 문서\n")])


def test_adjacent_labeled_lines_and_multiline_content_are_not_dropped():
    report = ("## 분석\n확인 사실: 첫 줄. [E1]\n이어지는 같은 문단의 줄.\n"
              "분석·해석 (추론): 해석 문단. [E1]\n근거의 한계: 한계 문단.\n후속 확인: 후속 문단.\n")
    paragraphs = build_company_report_inputs([company(report)])[0]["paragraphs"]
    assert len(paragraphs) == 4
    assert paragraphs[0]["text"] == "확인 사실: 첫 줄. [MM-E1]\n이어지는 같은 문단의 줄."
    assert [item["kind"] for item in paragraphs] == ["fact", "analysis", "limitation", "follow_up"]


def test_duplicate_prefix_or_company_is_rejected():
    first = company("## 분석\n한 기업의 의견.\n")
    second = company("## 분석\n두 번째 기업의 의견.\n", name="ROBROS")
    with pytest.raises(ValueError, match="중복"):
        build_company_report_inputs([first, second])
