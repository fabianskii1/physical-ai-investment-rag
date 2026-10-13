"""자격증명은 제거하고 보고서 재개에 필요한 작업 ID는 보존한다."""

import json

from app import _write_json


def test_trace_redaction_preserves_task_ids_and_removes_credentials(tmp_path, monkeypatch):
    secrets = {
        'OPENAI_API_KEY': 'configured-openai-secret',
        'LANGSMITH_API_KEY': 'configured-langsmith-secret',
        'HF_TOKEN': 'configured-huggingface-secret',
    }
    for name, secret in secrets.items():
        monkeypatch.setenv(name, secret)
    task_ids = ['task-technology-20261013', 'task-team-expertise', 'team-operations-review']
    unknown_tokens = ['sk-proj-AbCdEf0123456789', 'sk_testAbCdEf0123456789']
    payload = {
        'tasks': [{'id': task_id} for task_id in task_ids],
        'worker_results': {task_id: {'task_id': task_id} for task_id in task_ids},
        'errors': [f'Bearer {secret}' for secret in secrets.values()]
                  + [f'Authorization: Bearer {token}' for token in unknown_tokens],
    }
    target = tmp_path / 'report.failed_state.json'

    _write_json(target, payload)

    serialized = target.read_text(encoding='utf-8')
    restored = json.loads(serialized)
    assert restored['tasks'] == payload['tasks']
    assert restored['worker_results'] == payload['worker_results']
    assert all(secret not in serialized for secret in [*secrets.values(), *unknown_tokens])
    assert restored['errors'] == ['Bearer [REDACTED]'] * len(secrets) + [
        'Authorization: Bearer [REDACTED]', 'Authorization: Bearer [REDACTED]']
