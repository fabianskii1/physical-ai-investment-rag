"""실제 워커 프롬프트에 연결되는 역할별 Markdown 기준과 버전."""
from pathlib import Path
from hashlib import sha256

POLICY_DIR = Path(__file__).resolve().parent / 'prompts/workers'


def load_worker_policy(role):
    if role not in {'bigtech', 'technology', 'market'}:
        raise ValueError('알 수 없는 워커 역할')
    path = POLICY_DIR / f'{role}.md'
    content = path.read_text(encoding='utf-8')
    if not content.strip():
        raise ValueError('워커 기준이 비어 있습니다')
    return {'content': content, 'path': path.relative_to(POLICY_DIR.parent.parent).as_posix(),
            'sha256': sha256(content.encode()).hexdigest()}
