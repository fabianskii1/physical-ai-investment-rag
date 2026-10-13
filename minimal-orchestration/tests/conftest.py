"""오프라인 회귀 검사는 실제 계정의 LangSmith에 전송하지 않는다."""
import pytest


@pytest.fixture(autouse=True)
def offline_tracing(monkeypatch):
    monkeypatch.setenv('LANGSMITH_TRACING', 'false')
    monkeypatch.setenv('LANGCHAIN_TRACING_V2', 'false')
    # 실제 .env의 역할별 모델 설정을 오프라인 테스트가 상속하지 않는다.
    for name in ('OPENAI_WRITER_MODEL', 'OPENAI_PORTFOLIO_MODEL', 'OPENAI_EVIDENCE_MODEL',
                 'OPENAI_GRADER_MODEL', 'OPENAI_JUDGE_MODEL', 'OPENAI_SYNTHESIS_MODEL',
                 'OPENAI_COMBINED_JUDGE_MODEL'):
        monkeypatch.setenv(name, '')
