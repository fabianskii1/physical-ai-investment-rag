"""공식 산출물과 재현 자료만 allowlist로 수집하는 오프라인 제출 ZIP 생성기.

원본을 수정하지 않고 ZIP 안의 텍스트 경로만 상대경로로 정리한다.
필수 산출물이 없거나 비밀값/개인 경로가 남으면 ZIP을 새로 발행하지 않는다.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from hashlib import sha256
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from tempfile import NamedTemporaryFile
from urllib.parse import urlparse
import zipfile

from dotenv import dotenv_values
from pypdf import PdfReader
from pypdf.errors import PdfReadError


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_ROOT = 'physical_ai_orchestration'
NEUTRAL_FILENAME = 'physical_ai_orchestration_submission.zip'
LEGACY_REPOSITORY = 'https://github.com/fabianskii1/physical-ai-investment-rag'
ROOT_FILES = (
    'README.md', 'SUBMISSION.md', 'requirements.txt', 'requirements-rag.txt', '.env.example', '.gitignore',
    'app.py', 'agent_pipeline.py', 'state_schema.py', 'legacy_adapter.py', 'criteria_registry.py',
    'criteria_coverage.py', 'investment_evaluation.py', 'investment_case.py', 'combined_report.py', 'combined_quality.py',
    'company_brief.py', 'portfolio_decision.py', 'company_report_inputs.py', 'report_synthesis.py', 'report_language.py',
    'saved_revalidation.py',
    'evaluation_reference.py', 'research_inputs.py', 'link_sources.py', 'report_renderer.py', 'llm_client.py',
)
RAG_FILES = ('analyses.py', 'retrieval.py', 'report.py', 'scorecard.py', 'workflow.py')
FINAL_FILES = tuple(f'output/submission-live/final_investment_report.{suffix}' for suffix in ('pdf', 'md', 'json'))
COMPANY_STEMS = {'motomind': 'MOTOMIND', 'robros': 'ROBROS', 'figure_ai': 'Figure AI'}
COMPANY_FILES = tuple(f'output/submission-live/{stem}{suffix}' for stem in COMPANY_STEMS
                     for suffix in ('.state.json', '.scores.json', '.trace.json', '_brief.md', '_brief.pdf'))
TEXT_SUFFIXES = {'.py', '.md', '.txt', '.csv', '.json', '.svg'}
PRIVATE_PATH = re.compile(r'/(?:Users|home)/[\w.-]+/[^\s\"\'<>\[\]{}),;]*|/(?:private/)?var/folders/[^\s\"\'<>\[\]{}),;]+')
TOKEN_PATTERN = re.compile(rb'(?<![A-Za-z0-9_-])(?:sk[-_]|lsv2_[A-Za-z]+_)[A-Za-z0-9_-]{40,}')
PNG_HEADER = b'\x89PNG\r\n\x1a\n'


class SubmissionError(ValueError):
    pass


def known_secret_values(root: Path) -> tuple[bytes, ...]:
    """키는 메모리에서 비교만 한다. 값·길이·해시는 manifest/로그에 남기지 않는다."""
    names = ('OPENAI_API_KEY', 'LANGSMITH_API_KEY', 'LANGCHAIN_API_KEY', 'HF_TOKEN')
    values = {os.getenv(name, '') for name in names}
    env_path = root / '.env'
    if env_path.is_file():
        configured = dotenv_values(env_path)
        values.update(configured.get(name) or '' for name in names)
    return tuple(value.strip().encode() for value in values if value.strip())


def _safe_path(root: Path, relative: str) -> Path:
    name = PurePosixPath(relative)
    if name.is_absolute() or '..' in name.parts or any(part in {'.env', '.venv', '.git', '.cache', '__pycache__', 'tmp', 'hub', 'xet'} for part in name.parts):
        raise SubmissionError(f'허용되지 않는 제출 경로: {relative}')
    source = root / relative
    if source.is_symlink() or not source.resolve().is_relative_to(root.resolve()):
        raise SubmissionError(f'제출 파일의 심볼릭 링크는 허용하지 않습니다: {relative}')
    return source


def candidate_files(root: Path) -> list[str]:
    """과거 output이나 환경/모델 캐시는 확장자만으로 수집하지 않는다."""
    files = set(ROOT_FILES) | {f'rag/{name}' for name in RAG_FILES}
    files.update({'rag/data/catalog.csv', 'evaluation/physical_ai_investment_design.pdf',
                  'scripts/package_submission.py', 'scripts/render_architecture.py'})
    for pattern in ('rag/PDF/*.py', 'rag/data/raw/**/*.pdf', 'com_data/*.pdf', 'tests/test_*.py',
                    'tests/conftest.py', 'docs/**/*.md', 'docs/architecture.png', 'docs/architecture.svg',
                    'docs/traces/*.png', 'docs/traces/*.json'):
        files.update(path.relative_to(root).as_posix() for path in root.glob(pattern) if path.is_file())
    files.update(FINAL_FILES)
    files.update(COMPANY_FILES)
    # 최신 공식 실행 폴더의 명시된 보조 결과만 함께 보존한다.
    for pattern in ('output/submission-live/final_report.trace.json',
                    'output/submission-live/company_comparison.*', 'output/submission-live/web_sources/*.pdf',
                    'output/submission-live/web_sources/manifest.json'):
        files.update(path.relative_to(root).as_posix() for path in root.glob(pattern) if path.is_file())
    return sorted(name for name in files if _safe_path(root, name).is_file())


def _source_project(root: Path) -> str | None:
    app_path = root / 'app.py'
    if not app_path.is_file():
        return None
    match = re.search(r"^ORIGINAL_PROJECT = Path\(['\"]([^'\"]+)['\"]\)", app_path.read_text(encoding='utf-8'), re.MULTILINE)
    return match.group(1) if match else None


def sanitize_text(relative: str, text: str, root: Path, legacy_root: str | None, basename_paths: dict) -> str:
    if relative == 'app.py':
        text = re.sub(r"^ORIGINAL_PROJECT = Path\(['\"][^'\"]+['\"]\)",
            "ORIGINAL_PROJECT = Path(os.getenv('ORIGINAL_RAG_PROJECT', str(ROOT / 'rag'))).expanduser()",
            text, flags=re.MULTILINE)
    # 긴 경로부터 치환하며 원문 이름·페이지·인용 내용·E-ID는 유지한다.
    replacements = {str(root.resolve()): '.'}
    if legacy_root:
        replacements[legacy_root] = 'rag'
        replacements[f'{legacy_root}/com_data'] = 'com_data'
    for source, target in sorted(replacements.items(), key=lambda pair: len(pair[0]), reverse=True):
        text = text.replace(source, target)
    def replace_other_path(match):
        original = match.group(0)
        filename = original.rstrip('/').split('/')[-1]
        if filename in basename_paths:
            return basename_paths[filename]
        # 캐시·임시파일의 로컬 위치는 재현에 필요한 원문 경로와 구분한다.
        if any(part in original for part in ('/.cache/', '/.venv/', '/huggingface/', '/rag_index/', '/TemporaryItems/')):
            return '[local runtime path omitted]'
        return original  # 대응하지 못한 개인 경로는 후속 스캔에서 명시적으로 중단한다.
    return PRIVATE_PATH.sub(replace_other_path, text)


def _normalise_json_source_paths(value, pdf_paths):
    if isinstance(value, list):
        return [_normalise_json_source_paths(item, pdf_paths) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in {'source_path', 'file_path', 'source_pdf'} and isinstance(item, str) and item.lower().endswith('.pdf'):
            # 공식 폴더로 복사된 원문도 파일 이름이 유일하면 실제 ZIP member에 연결한다.
            item = pdf_paths.get(PurePosixPath(item).name, item)
        result[key] = _normalise_json_source_paths(item, pdf_paths)
    return result


def prepare_payloads(root: Path, files: list[str], secrets: tuple[bytes, ...]) -> tuple[dict, list[str]]:
    legacy = _source_project(root)
    by_basename = {}
    duplicates = set()
    for relative in files:
        filename = PurePosixPath(relative).name
        if filename in by_basename:
            duplicates.add(filename)
        by_basename[filename] = relative
    for filename in duplicates:
        by_basename.pop(filename, None)
    payloads, transformed = {}, []
    for relative in files:
        data = _safe_path(root, relative).read_bytes()
        if any(secret in data for secret in secrets) or TOKEN_PATTERN.search(data):
            raise SubmissionError(f'비밀값으로 의심되는 내용이 있어 포함하지 않았습니다: {relative}')
        if Path(relative).suffix in TEXT_SUFFIXES or relative in {'.env.example', '.gitignore'}:
            original = data.decode('utf-8')
            cleaned = sanitize_text(relative, original, root, legacy, by_basename)
            if relative.endswith('.json'):
                cleaned = json.dumps(_normalise_json_source_paths(json.loads(cleaned),
                    {name: path for name, path in by_basename.items() if path.endswith('.pdf')}),
                    ensure_ascii=False, indent=2)
            if PRIVATE_PATH.search(cleaned):
                raise SubmissionError(f'개인 절대경로가 남아 있습니다: {relative}')
            data = cleaned.encode('utf-8')
            if data != original.encode('utf-8'):
                transformed.append(relative)
        elif relative.endswith('.pdf'):
            reader = PdfReader(io.BytesIO(data))
            extracted = (str(reader.metadata or {}) + '\n' + '\n'.join(page.extract_text() or '' for page in reader.pages)).encode('utf-8')
            if any(secret in extracted for secret in secrets) or TOKEN_PATTERN.search(extracted):
                raise SubmissionError(f'PDF 원문에 비밀값으로 의심되는 내용이 있습니다: {relative}')
            if PRIVATE_PATH.search(extracted.decode('utf-8')):
                raise SubmissionError(f'PDF 원문에 개인 절대경로가 남아 있습니다: {relative}')
        if any(secret in data for secret in secrets):
            raise SubmissionError(f'정리 후 비밀값이 남아 있습니다: {relative}')
        payloads[relative] = data
    return payloads, transformed


def verify_payload_source_paths(payloads: dict) -> dict:
    """저장 State의 모든 원문 위치가 새 공용 resolver와 ZIP 파일 목록에 맞는지 확인한다."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from rag.PDF.pdf_loader import resolve_pdf_source_path
    count = 0
    for stem in COMPANY_STEMS:
        filename = f'output/submission-live/{stem}.state.json'
        if filename not in payloads:
            continue
        wrapper = json.loads(payloads[filename])
        catalog = wrapper.get('state', {}).get('document_catalog', {})
        if not isinstance(catalog, dict):
            raise SubmissionError(f'저장 State의 원문 카탈로그 형식 오류: {filename}')
        for doc_id, metadata in catalog.items():
            if not isinstance(metadata, dict):
                raise SubmissionError(f'저장 State의 원문 metadata 형식 오류: {filename}')
            locations = [metadata.get(key) for key in ('source_path', 'file_path') if metadata.get(key)]
            if not locations:
                raise SubmissionError(f'저장 State 원문 위치 없음: {filename} / {doc_id}')
            targets = []
            for location in locations:
                if not isinstance(location, str):
                    raise SubmissionError(f'저장 State 원문 위치 형식 오류: {filename} / {doc_id}')
                try:
                    target = resolve_pdf_source_path(location).relative_to(ROOT).as_posix()
                except ValueError as error:
                    raise SubmissionError(f'저장 State 원문 위치가 제출 폴더 밖을 가리킴: {filename} / {doc_id}') from error
                if target not in payloads or not target.lower().endswith('.pdf'):
                    raise SubmissionError(f'저장 State 원문이 ZIP에 포함되지 않음: {filename} / {doc_id}')
                targets.append(target)
                count += 1
            if len(set(targets)) != 1:
                raise SubmissionError(f'저장 State source_path와 file_path의 원문이 다름: {filename} / {doc_id}')
    return {'catalog_path_references': count, 'all_paths_resolve_to_bundled_pdfs': True}


def _validate_official_outputs(root: Path) -> list[str]:
    problems = []
    active_trace_ids = {}
    active_report_hashes = {}
    for name in FINAL_FILES:
        if not _safe_path(root, name).is_file():
            problems.append(f'필수 산출물 없음: {name}')
    for name in COMPANY_FILES:
        if not _safe_path(root, name).is_file():
            problems.append(f'기업별 중간 산출물 없음: {name}')
    for stem, company in COMPANY_STEMS.items():
        state_path = root / f'output/submission-live/{stem}.state.json'
        scores_path = root / f'output/submission-live/{stem}.scores.json'
        if state_path.is_file():
            wrapper = json.loads(state_path.read_text(encoding='utf-8'))
            wrapper = wrapper if isinstance(wrapper, dict) else {}
            state = wrapper.get('state', {})
            state = state if isinstance(state, dict) else {}
            evaluation = state.get('report_evaluation', {})
            evaluation = evaluation if isinstance(evaluation, dict) else {}
            info = state.get('company_info', {})
            info = info if isinstance(info, dict) else {}
            if (wrapper.get('mode') != 'live' or info.get('name') != company
                    or state.get('status') not in {'completed', 'partial'}
                    or evaluation.get('passed') is not True or evaluation.get('issues', [])
                    or not isinstance(state.get('trace_id'), str) or not state.get('trace_id')):
                problems.append(f'품질 통과한 실제 기업 State가 아님: {state_path.relative_to(root).as_posix()}')
            active_trace_ids[company] = state.get('trace_id')
            report = state.get('final_report')
            if not isinstance(report, str) or not report.strip():
                problems.append(f'실제 기업별 보고서 본문 없음: {stem}.state.json')
            else:
                active_report_hashes[company] = sha256(report.encode('utf-8')).hexdigest()
            if scores_path.is_file():
                scores = json.loads(scores_path.read_text(encoding='utf-8'))
                if not isinstance(scores, dict) or scores.get('company') != company or scores.get('trace_id') != state.get('trace_id'):
                    problems.append(f'기업 State와 점수 파일 실행 ID 불일치: {stem}')
            trace_path = root / f'output/submission-live/{stem}.trace.json'
            if trace_path.is_file():
                trace = json.loads(trace_path.read_text(encoding='utf-8'))
                if not isinstance(trace, dict) or trace.get('trace_id') != state.get('trace_id'):
                    problems.append(f'기업 State와 trace 파일 실행 ID 불일치: {stem}')
        brief_path = root / f'output/submission-live/{stem}_brief.pdf'
        if brief_path.is_file():
            pages = PdfReader(brief_path).pages
            if len(pages) != 1 or any(abs(float(page.mediabox.width) - 595.276) > 2
                    or abs(float(page.mediabox.height) - 841.89) > 2 for page in pages):
                problems.append(f'기업별 중간 PDF가 A4 한 쪽이 아님: {stem}_brief.pdf')
    pngs = list(root.glob('docs/traces/*.png'))
    if not pngs:
        problems.append('실제 LangSmith 화면 PNG 없음: docs/traces/*.png')
    for path in pngs:
        if not path.read_bytes().startswith(PNG_HEADER):
            problems.append(f'유효한 PNG가 아님: {path.relative_to(root).as_posix()}')
    architecture = [root / 'docs/architecture.png', root / 'docs/architecture.svg']
    if not any(path.is_file() for path in architecture):
        problems.append('구조도 없음: docs/architecture.png 또는 docs/architecture.svg')
    for path in architecture:
        if path.is_file() and ((path.suffix == '.png' and not path.read_bytes().startswith(PNG_HEADER))
                or (path.suffix == '.svg' and '<svg' not in path.read_text(encoding='utf-8'))):
            problems.append(f'유효한 구조도가 아님: {path.relative_to(root).as_posix()}')
    pdf = root / FINAL_FILES[0]
    if pdf.is_file():
        pages = PdfReader(pdf).pages
        if not 1 <= len(pages) <= 10:
            problems.append('최종 통합 PDF가 1~10쪽 범위를 벗어남')
        if any(abs(float(page.mediabox.width) - 595.276) > 2 or abs(float(page.mediabox.height) - 841.89) > 2 for page in pages):
            problems.append('최종 통합 PDF가 A4 규격이 아님')
    metadata = root / FINAL_FILES[2]
    if metadata.is_file():
        result = json.loads(metadata.read_text(encoding='utf-8'))
        if not isinstance(result, dict):
            problems.append('최종 보고서 JSON 형식 오류')
        else:
            markdown_path = root / FINAL_FILES[1]
            if ('markdown_sha256' in result and markdown_path.is_file()
                    and result['markdown_sha256'] != sha256(markdown_path.read_bytes()).hexdigest()):
                problems.append('최종 보고서 JSON의 본문 SHA256과 Markdown 불일치: 저장 후 변경된 본문 제출 불가')
            evaluation = result.get('report_evaluation', {})
            if (result.get('status') != 'completed' or not isinstance(evaluation, dict)
                    or evaluation.get('passed') is not True or evaluation.get('issues', [])):
                problems.append('최종 통합 보고서의 품질 통과 기록 없음')
            deterministic = result.get('deterministic_checks', {})
            if not isinstance(deterministic, dict) or deterministic.get('source_reports_quality_passed') is not True:
                problems.append('통합에 사용한 기업별 보고서의 품질 통과 기록 없음')
            if not isinstance(deterministic, dict) or deterministic.get('company_reports_synthesized') is not True:
                problems.append('실제 기업별 보고서 본문 종합 기록 없음')
            synthesis = result.get('report_synthesis', {})
            sources = synthesis.get('source_reports') if isinstance(synthesis, dict) else None
            expected_companies = set(COMPANY_STEMS.values())
            if (not isinstance(sources, list) or len(sources) != len(expected_companies)
                    or any(not isinstance(item, dict) for item in sources)
                    or {item.get('company') for item in sources} != expected_companies):
                problems.append('세 기업의 실제 보고서 종합 출처·본문 해시 기록 없음')
            else:
                for item in sources:
                    company = item['company']
                    if item.get('report_sha256') != active_report_hashes.get(company):
                        problems.append(f'현재 기업 보고서 본문 SHA256과 종합 출처 불일치: {company}; 이전 종합 보고서 재사용 불가')
                    if item.get('trace_id') != active_trace_ids.get(company):
                        problems.append(f'현재 기업 State와 종합 출처 실행 ID 불일치: {company}')
            reflection = evaluation.get('company_report_reflection') if isinstance(evaluation, dict) else None
            if (not isinstance(reflection, list) or len(reflection) != len(expected_companies)
                    or any(not isinstance(item, dict) for item in reflection)
                    or {item.get('company') for item in reflection} != expected_companies):
                problems.append('세 기업의 분석·한계·개별 의견 반영 품질 판정 없음')
            else:
                for item in reflection:
                    if (not all(item.get(key) is True for key in
                                ('analysis_preserved', 'limitations_preserved', 'individual_opinion_preserved'))
                            or item.get('missing_or_distorted') != []):
                        problems.append(f"기업 보고서 분석·한계·개별 의견 반영 품질 미달: {item['company']}")
            contexts = result.get('source_contexts')
            if (not isinstance(contexts, list) or any(not isinstance(item, dict)
                    or not isinstance(item.get('page_text'), str)
                    or not isinstance(item.get('evidence_ids'), list)
                    or any(not isinstance(ref, str) for ref in item['evidence_ids']) for item in contexts)):
                problems.append('최종 보고서의 실제 인용 원문 입력 형식 오류: source_contexts')
            else:
                markdown_path = root / FINAL_FILES[1]
                if markdown_path.is_file():
                    if str(ROOT) not in sys.path:
                        sys.path.insert(0, str(ROOT))
                    from report_language import report_evidence_scope_issues
                    issues = report_evidence_scope_issues(markdown_path.read_text(encoding='utf-8'), contexts)
                    problems.extend(f'최종 보고서 원문 범위 검사 실패: {issue}' for issue in issues)
            checks = result.get('pdf_checks', {})
            if not isinstance(checks, dict) or not all(checks.get(key) is True for key in
                    ('all_a4', 'within_page_limit', 'text_within_margins')):
                problems.append('최종 통합 PDF의 규격 검증 기록 없음')
            companies = result.get('companies', [])
            if (not isinstance(companies, list) or len(companies) != 3
                    or any(not isinstance(item, dict) for item in companies)
                    or {item.get('company') for item in companies} != set(COMPANY_STEMS.values())):
                problems.append('최종 통합 보고서의 3기업 실행 목록이 올바르지 않음')
            elif any(item.get('trace_id') != active_trace_ids.get(item['company']) for item in companies):
                problems.append('최종 통합 보고서와 현재 기업 State 실행 ID 불일치: 이전 통과 보고서 재사용 불가')
    return problems


def _catalog_check(root: Path) -> dict:
    rows = list(csv.DictReader((root / 'rag/data/catalog.csv').open(encoding='utf-8-sig', newline='')))
    actual_pages = 0
    for row in rows:
        filename = row['file_path']
        relative = filename if filename.startswith('rag/') else f'rag/{filename}'
        path = _safe_path(root, relative)
        if not path.is_file():
            raise SubmissionError(f'카탈로그 원문 PDF 없음: {relative}')
        pages = len(PdfReader(path).pages)
        if pages != int(row['page_count']):
            raise SubmissionError(f'카탈로그 원문 쪽 수 불일치: {relative}')
        actual_pages += pages
    return {'document_count': len(rows), 'page_count': actual_pages}


def _members_from_readme(root: Path) -> list[str]:
    path = root / 'README.md'
    if not path.is_file():
        return []
    text = path.read_text(encoding='utf-8')
    if '## Contributors' not in text:
        return []
    section = text.split('## Contributors', 1)[1].split('\n## ', 1)[0]
    pattern = re.compile(r'^\s*(?:\|\s*([가-힣]{2,5})\s*\||[-*+]\s+([가-힣]{2,5})\s*:)')
    names = []
    for line in section.splitlines():
        match = pattern.match(line)
        if match:
            name = match.group(1) or match.group(2)
            if name != '팀원' and name not in names:
                names.append(name)
    return names


def build_submission(root=ROOT, *, output_dir=None, preflight_only=False, github_url=None,
                     campus=None, class_name=None, members=None, secrets=None) -> dict:
    root = Path(root).resolve()
    directory = Path(output_dir).resolve() if output_dir else root / 'output/submission'
    directory.mkdir(parents=True, exist_ok=True)
    if github_url and (urlparse(github_url).scheme != 'https' or urlparse(github_url).hostname != 'github.com'):
        raise SubmissionError('제출 저장소 URL은 https://github.com 링크여야 합니다')
    team = members or _members_from_readme(root)
    pending = [label for label, value in (('제출용 GitHub 브랜치 URL', github_url), ('캠퍼스', campus),
                                         ('분반', class_name), ('팀원 명단', team)) if not value]
    try:
        problems = _validate_official_outputs(root)
        files = candidate_files(root)
    except (ValueError, OSError, PdfReadError) as error:
        problems = [str(error) if isinstance(error, SubmissionError) else f'공식 산출물 검증 실패: {type(error).__name__}']
        files = []
    required_code = set(ROOT_FILES) - {'.gitignore'}
    problems.extend(f'재현 파일 없음: {name}' for name in sorted(required_code - set(files)))
    try:
        catalog = _catalog_check(root)
        payloads, transformed = prepare_payloads(root, files, known_secret_values(root) if secrets is None else secrets)
        source_paths = verify_payload_source_paths(payloads)
    except (SubmissionError, ValueError, OSError, PdfReadError) as error:
        catalog, payloads, transformed, source_paths = {}, {}, [], {}
        problems.append(str(error) if isinstance(error, SubmissionError) else f'재현 자료 검증 실패: {type(error).__name__}')
    preflight = {'artifact_ready': not problems, 'submission_complete': not problems and not pending,
        'problems': problems, 'pending_submission_fields': pending, 'catalog': catalog,
        'source_path_checks': source_paths,
        'submission_repository': github_url, 'legacy_reference_repository': LEGACY_REPOSITORY,
        'team': {'campus': campus, 'class': class_name, 'members': team},
        'included_files': sorted(payloads), 'normalized_text_files': transformed,
        'excluded': ['.env', 'venv', 'cache/model weights', 'tmp', 'git metadata', 'past output folders'],
        'raster_capture_note': 'PNG 화면의 실행 진위와 보이는 내용은 캡처한 실제 LangSmith 화면과 함께 확인해야 합니다.'}
    (directory / 'preflight.json').write_text(json.dumps(preflight, ensure_ascii=False, indent=2), encoding='utf-8')
    if preflight_only or problems:
        return {'preflight': preflight, 'archive': None}
    fields = [campus, class_name, *team] if campus and class_name and team else []
    if fields and any(not re.fullmatch(r'[\w가-힣-]+', value) for value in fields):
        raise SubmissionError('ZIP 파일명 항목에는 문자·숫자·밑줄·하이픈만 사용할 수 있습니다')
    filename = f'Agent_{campus}_{class_name}_{"+".join(team)}.zip' if fields else NEUTRAL_FILENAME
    info = {'schema_version': 1, 'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'submission_complete': preflight['submission_complete'], 'pending_submission_fields': pending,
        'submission_repository': github_url, 'legacy_reference_repository': LEGACY_REPOSITORY,
        'team': preflight['team'], 'catalog': catalog,
        'source_path_checks': source_paths,
        'source_files_modified': False, 'normalized_text_files': transformed,
        'artifact_link_map': {
            'final_report': FINAL_FILES[0],
            'one_page_briefs': {company: f'output/submission-live/{stem}_brief.pdf' for stem, company in COMPANY_STEMS.items()},
            'saved_states': {company: f'output/submission-live/{stem}.state.json' for stem, company in COMPANY_STEMS.items()},
            'unbundled_detailed_reports': [f'output/submission-live/{stem}.{suffix}' for stem in COMPANY_STEMS for suffix in ('pdf', 'md')],
            'detailed_report_note': '상세 보고서 경로는 원래 실행 metadata로 보존한다. 상세 PDF/MD는 full app 실행으로 재생성하며 최종 제출 보고서는 위 final_report 한 파일이다.',
            'combine_from_states': 'app.py --combine-reports output/submission-live'},
        'files': [{'path': name, 'bytes': len(data), 'sha256': sha256(data).hexdigest()} for name, data in sorted(payloads.items())],
        'manifest_self_hash': 'excluded to avoid a circular hash'}
    manifest = json.dumps(info, ensure_ascii=False, indent=2).encode('utf-8')
    target = directory / filename
    with NamedTemporaryFile(dir=directory, prefix='.package-', suffix='.zip', delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with zipfile.ZipFile(temporary_path, 'w', zipfile.ZIP_DEFLATED) as archive:
            for relative, data in sorted(payloads.items()):
                archive.writestr(f'{ARCHIVE_ROOT}/{relative}', data)
            archive.writestr(f'{ARCHIVE_ROOT}/MANIFEST.json', manifest)
        verify_archive(temporary_path, secrets=known_secret_values(root) if secrets is None else secrets)
        temporary_path.replace(target)
    finally:
        temporary_path.unlink(missing_ok=True)
    (directory / 'manifest.json').write_bytes(manifest)
    checksum = sha256(target.read_bytes()).hexdigest()
    (directory / 'archive.sha256').write_text(f'{checksum}  {filename}\n', encoding='utf-8')
    return {'preflight': preflight, 'archive': target, 'sha256': checksum}


def verify_archive(path, *, secrets=()) -> dict:
    """ZIP 실제 데이터·member 경로·manifest SHA256을 독립적으로 재확인한다."""
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise SubmissionError('ZIP 안에 중복 경로가 있습니다')
        bad = archive.testzip()
        if bad:
            raise SubmissionError(f'ZIP CRC 검사 실패: {bad}')
        manifest = json.loads(archive.read(f'{ARCHIVE_ROOT}/MANIFEST.json'))
        expected = {f'{ARCHIVE_ROOT}/{item["path"]}': item for item in manifest['files']}
        if set(names) != set(expected) | {f'{ARCHIVE_ROOT}/MANIFEST.json'}:
            raise SubmissionError('ZIP 파일 목록과 manifest가 일치하지 않습니다')
        for name in names:
            member = PurePosixPath(name)
            if member.is_absolute() or '..' in member.parts or member.parts[0] != ARCHIVE_ROOT:
                raise SubmissionError('ZIP에 안전하지 않은 member 경로가 있습니다')
            if (any(part in {'.env', '.venv', '.git', '.cache', '__pycache__', 'tmp', 'hub', 'xet'} for part in member.parts)
                    or any(part.startswith('.env.') and part != '.env.example' for part in member.parts)
                    or stat.S_IFMT(archive.getinfo(name).external_attr >> 16) == stat.S_IFLNK):
                raise SubmissionError(f'ZIP에 허용되지 않는 파일이 있습니다: {name}')
            data = archive.read(name)
            if any(secret in data for secret in secrets) or TOKEN_PATTERN.search(data):
                raise SubmissionError(f'ZIP에 비밀값으로 의심되는 내용이 있습니다: {name}')
            if name in expected and (sha256(data).hexdigest() != expected[name]['sha256'] or len(data) != expected[name]['bytes']):
                raise SubmissionError(f'ZIP 파일 해시 불일치: {name}')
            if Path(name).suffix in TEXT_SUFFIXES and PRIVATE_PATH.search(data.decode('utf-8')):
                raise SubmissionError(f'ZIP에 개인 절대경로가 있습니다: {name}')
        return {'members': len(names), 'hashes_verified': len(expected), 'crc_verified': True}


def main():
    parser = argparse.ArgumentParser(description='공식 보고서·실제 trace·원천 자료를 안전한 제출 ZIP으로 정리')
    parser.add_argument('--preflight', action='store_true', help='누락·비밀값·개인경로만 검사하고 ZIP은 생성하지 않음')
    parser.add_argument('--output-dir')
    parser.add_argument('--github-url', help='현재 오케스트레이션 구현의 실제 제출용 브랜치 URL')
    parser.add_argument('--campus')
    parser.add_argument('--class-name')
    parser.add_argument('--members', nargs='+')
    args = parser.parse_args()
    result = build_submission(output_dir=args.output_dir, preflight_only=args.preflight, github_url=args.github_url,
                              campus=args.campus, class_name=args.class_name, members=args.members)
    preflight = result['preflight']
    print(f"필수 자료 검사: {'통과' if preflight['artifact_ready'] else '미완료'}")
    for problem in preflight['problems']:
        print(f'- {problem}')
    if preflight['pending_submission_fields']:
        print('제출 전에 입력할 정보: ' + ', '.join(preflight['pending_submission_fields']))
    if result['archive']:
        print(f"ZIP: {result['archive'].name}; SHA256: {result['sha256']}")
    return 0 if preflight['artifact_ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
