"""제한된 OpenAI 요청과 선택적인 LangSmith 모델 호출 추적."""
import os


def make_openai_client():
    from openai import OpenAI

    client = OpenAI(timeout=60, max_retries=1)
    enabled = os.getenv('LANGSMITH_TRACING', 'false').lower() in {'true', '1'}
    if enabled and os.getenv('LANGSMITH_API_KEY'):
        from langsmith.wrappers import wrap_openai
        return wrap_openai(client)
    return client
