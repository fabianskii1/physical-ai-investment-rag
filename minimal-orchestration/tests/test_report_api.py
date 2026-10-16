"""보고서 API의 시간 제한과 구조화 출력을 네트워크 없이 검증한다."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from rag.report import ReportAnalysisDraft, _openai_write


@pytest.fixture
def report_api(monkeypatch):
    # 로컬 .env와 실제 API 키를 읽거나 네트워크로 요청하지 않는다.
    monkeypatch.setenv("OPENAI_API_KEY", "test-report-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-report-model")
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: None)
    constructor = Mock()
    monkeypatch.setattr("openai.OpenAI", constructor)
    return constructor, constructor.return_value.responses.parse


def test_report_api_bounds_requests_and_preserves_structured_output(report_api):
    constructor, parse = report_api
    draft = ReportAnalysisDraft(**{name: [] for name in ReportAnalysisDraft.model_fields})
    parse.return_value = SimpleNamespace(output_parsed=draft)
    payload = {"company_name": "검토 기업", "verified_evidence": []}

    result = _openai_write(payload)

    constructor.assert_called_once_with(timeout=60, max_retries=1)
    parse.assert_called_once()
    request = parse.call_args.kwargs
    assert request["model"] == "test-report-model"
    assert request["text_format"] is ReportAnalysisDraft
    assert request["input"][0]["role"] == "system"
    assert request["input"][1]["role"] == "user"
    assert json.loads(request["input"][1]["content"]) == {**payload, 'assessment_limit_eligible_criteria': []}
    assert payload == {"company_name": "검토 기업", "verified_evidence": []}
    assert result == draft.model_dump()


def test_report_api_rejects_response_without_parsed_report(report_api):
    constructor, parse = report_api
    parse.return_value = SimpleNamespace(output_parsed=None)

    with pytest.raises(RuntimeError, match="보고서 모델이 구조화된 답을 반환하지 않았습니다"):
        _openai_write({"company_name": "검토 기업"})

    constructor.assert_called_once_with(timeout=60, max_retries=1)


def test_writer_gets_explicit_unknown_only_names_without_changing_original_scores(report_api):
    _, parse = report_api
    parse.return_value=SimpleNamespace(output_parsed=ReportAnalysisDraft(**{name:[] for name in ReportAnalysisDraft.model_fields}))
    scores={'시장 성장 동력':{'grade':4},'유상 매출·납품':{'grade':None}}
    _openai_write({'score_details':scores})
    actual=json.loads(parse.call_args.kwargs['input'][1]['content'])
    assert actual['assessment_limit_eligible_criteria']==['유상 매출·납품']
    assert scores['시장 성장 동력']['grade']==4
    parse.assert_called_once()


def test_feedback_rewrite_preserves_sections_outside_actual_defect(report_api):
    _,parse=report_api
    from rag.report import AnalysisParagraph
    empty={name:[] for name in ReportAnalysisDraft.model_fields}
    old=AnalysisParagraph(text='기존에 검증된 사업 사실이다.',evidence_ids=['E1'],interpretation='',limitation='',next_check='')
    changed=AnalysisParagraph(text='관련 없는 새 사업 사실이다.',evidence_ids=['E2'],interpretation='',limitation='',next_check='')
    repair=AnalysisParagraph(text='원문 범위로 교정한 기술 사실이다.',evidence_ids=['E3'],interpretation='',limitation='',next_check='')
    current=ReportAnalysisDraft(**{**empty,'overview':[old]}).model_dump()
    parse.return_value=SimpleNamespace(output_parsed=ReportAnalysisDraft(**{**empty,'overview':[changed],'technology':[repair]}))
    result=_openai_write({'current_draft':current,'section_line_ranges':{'overview':[1,5],'technology':[10,15]},
                         'review_feedback':['groundedness: 문단 P11의 기술 표현 오류']})
    assert result['overview']==current['overview']
    assert result['technology'][0]['text']==repair.text


def test_empty_assessment_limit_is_omitted_without_inventing_unknown_criteria(report_api):
    _,parse=report_api
    from rag.report import AnalysisParagraph
    empty={name:[] for name in ReportAnalysisDraft.model_fields}
    limit=AnalysisParagraph(text='',evidence_ids=[],interpretation='',limitation='',next_check='',
                            basis='assessment_limit',assessment_criteria=[])
    parse.return_value=SimpleNamespace(output_parsed=ReportAnalysisDraft(**{**empty,'technical_maturity':[limit]}))
    assert _openai_write({})['technical_maturity']==[]


def test_generator_never_turns_scored_items_or_domain_names_into_unknown_limits(report_api):
    _,parse=report_api
    from rag.report import AnalysisParagraph
    empty={name:[] for name in ReportAnalysisDraft.model_fields}
    limit=AnalysisParagraph(text='',evidence_ids=[],interpretation='',limitation='',next_check='',
        basis='assessment_limit',assessment_criteria=['시장성','시장 성장 동력','유상 매출·납품'])
    parse.return_value=SimpleNamespace(output_parsed=ReportAnalysisDraft(**{**empty,'market_feasibility':[limit]}))
    result=_openai_write({'score_details':{'시장 성장 동력':{'grade':2},'유상 매출·납품':{'grade':None}}})
    assert result['market_feasibility'][0]['assessment_criteria']==['유상 매출·납품']
