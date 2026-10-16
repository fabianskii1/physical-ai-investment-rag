"""최종 보고서 Judge가 기업보고서의 분석·한계·개별 의견까지 확인하는지 검사한다."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import combined_quality
import legacy_adapter


COMPANIES = ("MOTOMIND", "ROBROS", "Figure AI")


def reflection(company):
    original = next(item for item in payload()['company_reports'] if item['company'] == company)
    links = combined_quality.report_evidence_links(payload()['report'], [])
    proofs = []
    for aspect, paragraph in zip(('analysis', 'limitations', 'individual_opinion'), original['paragraphs']):
        target = next(link for link in links if link['text'] == paragraph['text'])
        proofs.append({'aspect': aspect, 'source_paragraph_id': paragraph['paragraph_id'],
                       'source_excerpt': paragraph['text'], 'final_paragraph_id': target['paragraph_id'],
                       'final_excerpt': target['text']})
    return {
        "company": company,
        "analysis_preserved": True,
        "limitations_preserved": True,
        "individual_opinion_preserved": True,
        "reason": "확인 사실의 사업적 의미와 검증 범위, 보류·재검토 조건이 유지됐다.",
        "missing_or_distorted": [],
        'preservation_evidence': proofs,
        'required_fact_reflection': [],
    }


def payload():
    reports = []
    body = ['# 최종 보고서', '## 3 — 기업별 투자 근거와 위험']
    for company, prefix in zip(COMPANIES, ('MM', 'RB', 'FG')):
        paragraphs = [
            {'paragraph_id': prefix+'-P001', 'kind': 'analysis', 'text': f'{company}의 시제품 개발을 고객 실증으로 전환해야 유상납품 가능성을 평가할 수 있다.'},
            {'paragraph_id': prefix+'-P002', 'kind': 'limitation', 'text': f'{company}의 실제 고객 유상납품과 반복 작업 성능은 현재 확인자료가 부족하다.'},
            {'paragraph_id': prefix+'-P003', 'kind': 'opinion', 'text': f'{company}의 현재 투자 의견은 보류이며 고객 실증과 거래조건 확보 후 재검토한다.'},
        ]
        reports.append({'company': company, 'paragraphs': paragraphs})
        body += ['### '+company+' — 투자 보류', *(p['text'] for p in paragraphs)]
    return {
        'report': '\n\n'.join(body),
        'company_reports': reports,
        "report_synthesis": {"comparison": "실증 근거의 범위와 미확인 사업 조건을 비교했다."},
        "source_contexts": [{"page_text": "회사가 발표한 실증 자료"}],
    }


def review(reflections=None):
    return combined_quality.CombinedQualityReview(
        findings=[],
        groundedness_reason="원문 범위에 맞는 사실과 추론",
        neutrality_reason="회사 발표에 귀속",
        bias_control_reason="미확인 자료와 실사 조건 공개",
        data_gaps=["현재 거래조건 추가 확인"],
        company_report_reflection=(
            reflections if reflections is not None else [reflection(company) for company in COMPANIES]
        ),
    )


@pytest.mark.parametrize("company", COMPANIES)
@pytest.mark.parametrize(
    "failed_field",
    ("analysis_preserved", "limitations_preserved", "individual_opinion_preserved", "missing_or_distorted"),
)
def test_reflection_failure_blocks_core_pass_and_requests_rewrite(monkeypatch, company, failed_field):
    reflections = [reflection(name) for name in COMPANIES]
    item = next(item for item in reflections if item["company"] == company)
    if failed_field == "missing_or_distorted":
        item[failed_field] = ["산업 수요와 해당 기업의 고객 전환을 구분하는 분석이 빠졌다."]
    else:
        item[failed_field] = False
        item["reason"] = "원보고서의 중요한 판단 연결이 반영되지 않았다."
    request = Mock(return_value=review(reflections))
    monkeypatch.setattr(legacy_adapter, "_structured_request", request)

    result = combined_quality.evaluate_combined_quality(payload())

    assert all(result["rubric"][key] for key in legacy_adapter.REPORT_QUALITY_KEYS)
    assert result["passed"] is False
    assert result["action"] == "rewrite"
    assert len(result["issues"]) == 1
    assert company in result["issues"][0]
    expected_issue = item["missing_or_distorted"][0] if item["missing_or_distorted"] else item["reason"]
    assert expected_issue in result["issues"][0]
    assert request.call_count == 1


def test_all_company_reflections_and_core_checks_pass_together(monkeypatch):
    request = Mock(return_value=review())
    monkeypatch.setattr(legacy_adapter, "_structured_request", request)

    result = combined_quality.evaluate_combined_quality(payload())

    assert result["passed"] is True
    assert result["issues"] == []
    assert result["data_gaps"] == ["현재 거래조건 추가 확인"]
    assert result["method"] == "hybrid"
    assert result["company_report_reflection"] == [reflection(company) for company in COMPANIES]
    assert request.call_count == 1
    assert request.call_args.args[0] is combined_quality.CombinedQualityReview


def test_judge_receives_kind_scoped_candidates_and_only_own_company_final_section(monkeypatch):
    original = payload()
    original['company_reports'][1]['paragraphs'].append({
        'paragraph_id': 'RB-P060', 'kind': 'analysis', 'text': '점수 사유의 U 설명은 한계처럼 읽혀도 분석 유형 문단이다.'})
    original['report'] += '\n\n## 5 — REFERENCE\n\nROBROS의 출처 목록은 반영 증거의 최종 문단이 아니다.'
    request = Mock(return_value=review())
    monkeypatch.setattr(legacy_adapter, '_structured_request', request)
    result = combined_quality.evaluate_combined_quality(original)
    assert result['passed']
    model_payload = request.call_args.args[2]
    robros = next(c for c in model_payload['reflection_candidates'] if c['company'] == 'ROBROS')
    assert 'RB-P060' in {p['paragraph_id'] for p in robros['source_candidates']['analysis']}
    assert 'RB-P060' not in {p['paragraph_id'] for p in robros['source_candidates']['limitations']}
    assert all('MOTOMIND' not in p['text'] and 'Figure AI' not in p['text'] and '출처 목록' not in p['text']
               for p in robros['final_candidates'])


def test_candidates_do_not_allow_analysis_kind_as_limitations_proof(monkeypatch):
    originals = payload()
    reflections = [reflection(company) for company in COMPANIES]
    robros = reflections[1]
    wrong = originals['company_reports'][1]['paragraphs'][0]
    robros['preservation_evidence'][1].update(source_paragraph_id=wrong['paragraph_id'], source_excerpt=wrong['text'])
    monkeypatch.setattr(legacy_adapter, '_structured_request', Mock(return_value=review(reflections)))
    result = combined_quality.evaluate_combined_quality(originals)
    assert result['passed'] is False and result['action'] == 'review'
    assert result['judge_evidence_issues'] and not result['deterministic_content_issues']


@pytest.mark.parametrize(
    "reflections",
    (
        [reflection("MOTOMIND"), reflection("ROBROS")],
        [reflection("MOTOMIND"), reflection("ROBROS"), reflection("ROBROS")],
        [],
    ),
)
def test_missing_or_duplicate_company_verdict_cannot_be_accepted(monkeypatch, reflections):
    verdict = legacy_adapter.ReportVerdict(
        groundedness=True, neutrality=True, bias_control=True,
        groundedness_reason="원문 일치", neutrality_reason="회사 발표 구분", bias_control_reason="U 공개",
        issues=[], company_report_reflection=reflections,
    )
    monkeypatch.setattr(combined_quality, "request_report_verdict", Mock(return_value=verdict))

    with pytest.raises(ValueError, match="세 기업 보고서의 반영 판정"):
        combined_quality.evaluate_combined_quality(payload())


def test_custom_review_is_preserved_in_verdict_and_uses_judge_model(monkeypatch):
    expected_review = review()
    parse = Mock(return_value=SimpleNamespace(output_parsed=expected_review))
    monkeypatch.setattr(legacy_adapter, "make_openai_client", Mock(return_value=SimpleNamespace(
        responses=SimpleNamespace(parse=parse))))
    monkeypatch.setenv("OPENAI_MODEL", "worker-model")
    monkeypatch.setenv("OPENAI_WRITER_MODEL", "writer-model")
    monkeypatch.setenv("OPENAI_JUDGE_MODEL", "judge-model")
    original = payload()
    before = deepcopy(original)

    result = legacy_adapter.request_report_verdict(
        "최종 반영 검사", original, review_schema=combined_quality.CombinedQualityReview)

    assert isinstance(result, legacy_adapter.ReportVerdict)
    assert result.company_report_reflection == [item.model_dump() for item in expected_review.company_report_reflection]
    assert all(getattr(result, key) for key in legacy_adapter.REPORT_QUALITY_KEYS)
    assert result.issues == []
    assert result.data_gaps == expected_review.data_gaps
    assert original == before
    assert parse.call_count == 1
    assert parse.call_args.kwargs["model"] == "judge-model"
    assert parse.call_args.kwargs["text_format"] is combined_quality.CombinedQualityReview


def test_default_quality_review_path_keeps_original_contract(monkeypatch):
    base = legacy_adapter.QualityReview(
        findings=[], groundedness_reason="원문 일치", neutrality_reason="회사 발표 구분",
        bias_control_reason="U 공개")
    request = Mock(return_value=base)
    monkeypatch.setattr(legacy_adapter, "_structured_request", request)

    result = legacy_adapter.request_report_verdict("기존 보고서 검사", {"report": "기존 보고서"})

    assert request.call_args.args[0] is legacy_adapter.QualityReview
    assert result.company_report_reflection == []
    assert result.issues == []
    assert all(getattr(result, key) for key in legacy_adapter.REPORT_QUALITY_KEYS)


def test_combined_without_company_inputs_uses_default_quality_schema(monkeypatch):
    request = Mock(return_value=legacy_adapter.QualityReview(
        findings=[], groundedness_reason="원문 일치", neutrality_reason="회사 발표 구분",
        bias_control_reason="U 공개"))
    monkeypatch.setattr(legacy_adapter, "_structured_request", request)

    result = combined_quality.evaluate_combined_quality({"report": "기존 통합 보고서"})

    assert result["passed"] is False
    assert '기업보고서 전체 입력' in result['issues'][0]
    assert result["company_report_reflection"] == []
    assert request.call_args.args[0] is legacy_adapter.QualityReview


def test_reflection_prompt_allows_correcting_unsupported_company_report_claims(monkeypatch):
    request = Mock(return_value=review())
    monkeypatch.setattr(legacy_adapter, "_structured_request", request)

    combined_quality.evaluate_combined_quality(payload())

    instruction = request.call_args.args[1]
    assert "문단 ID 연결이나 소제목 존재만으로 통과하지 않습니다" in instruction
    assert "분석을 세 사실/통상 U목록으로만 축소" in instruction
    assert "근거 없는 소송/수치/양산 주장을 제외" in instruction
    assert "독립입증 과장을 자사 발표에 귀속" in instruction
    assert "올바른 교정이며 누락 실패가 아닙니다" in instruction
    assert "원보고서보다 원문 검증 결과를 우선" in instruction
    assert "U는 실제 경쟁 열위로 단정하지 마세요" in instruction
    assert "관점 커버리지는 평가에서 제외" in instruction
    assert "MOTOMIND MOU/설계" in instruction
    assert "ROBROS 산업 수요와 자사 고객전환" in instruction
    assert "Figure BMW 시연/첫 계약" in instruction
    assert "가정시험과 제조검증, F02 운영과 F03 현재검증" in instruction
