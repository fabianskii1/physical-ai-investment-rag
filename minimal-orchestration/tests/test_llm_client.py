"""실제 키나 네트워크 없이 선택적 LangSmith 래퍼를 검증한다."""
from unittest.mock import Mock

from llm_client import make_openai_client


def test_client_wraps_only_when_tracing_is_enabled_and_key_present(monkeypatch):
    constructor, wrapper = Mock(), Mock()
    monkeypatch.setattr('openai.OpenAI', constructor)
    monkeypatch.setattr('langsmith.wrappers.wrap_openai', wrapper)
    monkeypatch.setenv('LANGSMITH_TRACING', 'true')
    monkeypatch.delenv('LANGSMITH_API_KEY', raising=False)
    assert make_openai_client() is constructor.return_value
    wrapper.assert_not_called()
    monkeypatch.setenv('LANGSMITH_API_KEY', 'offline-test-key')
    assert make_openai_client() is wrapper.return_value
    wrapper.assert_called_once_with(constructor.return_value)
    constructor.assert_called_with(timeout=60, max_retries=1)
