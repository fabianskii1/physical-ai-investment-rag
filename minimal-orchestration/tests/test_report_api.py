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
    assert json.loads(request["input"][1]["content"]) == payload
    assert result == draft.model_dump()


def test_report_api_rejects_response_without_parsed_report(report_api):
    constructor, parse = report_api
    parse.return_value = SimpleNamespace(output_parsed=None)

    with pytest.raises(RuntimeError, match="보고서 모델이 구조화된 답을 반환하지 않았습니다"):
        _openai_write({"company_name": "검토 기업"})

    constructor.assert_called_once_with(timeout=60, max_retries=1)
    parse.assert_called_once()
