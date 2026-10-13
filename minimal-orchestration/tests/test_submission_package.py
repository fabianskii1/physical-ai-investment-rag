"""제출 ZIP은 실제 공식 파일이 없으면 발행하지 않고 환경/과거 출력은 제외한다."""
import base64
import csv
from hashlib import sha256
import io
import json
from pathlib import Path
import zipfile

import pytest
from pypdf import PdfWriter

from scripts.package_submission import (ARCHIVE_ROOT, COMPANY_STEMS, FINAL_FILES, ROOT_FILES, SubmissionError,
                                        build_submission, verify_archive)


def write(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, bytes):
        path.write_bytes(value)
    else:
        path.write_text(value, encoding='utf-8')
    return path


def pdf_bytes(pages=1):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595.276, height=841.89)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def source_tree(root, *, official=True):
    for name in ROOT_FILES:
        write(root, name, 'pass\n' if name.endswith('.py') else 'fixture\n')
    original_project = '/' + 'Users' + '/private-person/original-project'
    write(root, 'app.py', f"import os\nfrom pathlib import Path\nROOT = Path(__file__).resolve().parent\nORIGINAL_PROJECT = Path('{original_project}')\n")
    write(root, 'README.md', '# Subject\n## Contributors\n| 팀원 | 담당 |\n| 김테스트 | fixture |\n')
    write(root, '.env.example', 'OPENAI_API_KEY=\nLANGSMITH_API_KEY=\n')
    write(root, 'scripts/package_submission.py', 'pass\n')
    for name in ('analyses.py', 'retrieval.py', 'report.py', 'scorecard.py', 'workflow.py'):
        write(root, f'rag/{name}', 'pass\n')
    write(root, 'evaluation/physical_ai_investment_design.pdf', pdf_bytes())
    write(root, 'com_data/Fixture.pdf', pdf_bytes())
    write(root, 'rag/data/raw/company/source.pdf', pdf_bytes())
    csv_file = write(root, 'rag/data/catalog.csv', '')
    with csv_file.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['doc_id', 'file_path', 'page_count'])
        writer.writeheader()
        writer.writerow({'doc_id': 'DOC-1', 'file_path': 'data/raw/company/source.pdf', 'page_count': 1})
    if official:
        companies = []
        source_reports = []
        reflections = []
        for stem, company in COMPANY_STEMS.items():
            trace_id = f'run-{stem}'
            companies.append({'company': company, 'trace_id': trace_id})
            report = f'# {company} 보고서\n\n## 분석\n회사 발표와 독립 검증의 범위를 구분한다.\n'
            source_reports.append({'company': company, 'trace_id': trace_id,
                                   'report_sha256': sha256(report.encode('utf-8')).hexdigest()})
            reflections.append({'company': company, 'analysis_preserved': True, 'limitations_preserved': True,
                                'individual_opinion_preserved': True, 'missing_or_distorted': []})
            state = {'company_info': {'name': company}, 'status': 'partial', 'trace_id': trace_id,
                     'report_evaluation': {'passed': True, 'issues': []}, 'final_report': report,
                     'document_catalog': {'DOC-1': {'source_path': str(root / 'rag/data/raw/company/source.pdf'),
                                                  'file_path': 'data/raw/company/source.pdf'}}}
            write(root, f'output/submission-live/{stem}.state.json', json.dumps({'state': state, 'mode': 'live'}))
            write(root, f'output/submission-live/{stem}.scores.json', json.dumps({'company': company, 'trace_id': trace_id}))
            write(root, f'output/submission-live/{stem}.trace.json', json.dumps({'trace_id': trace_id}))
            write(root, f'output/submission-live/{stem}_brief.md', '# 한 쪽 중간 요약\n')
            write(root, f'output/submission-live/{stem}_brief.pdf', pdf_bytes())
        write(root, FINAL_FILES[0], pdf_bytes())
        write(root, FINAL_FILES[1], '# 실제 분석 fixture 보고서\n')
        write(root, FINAL_FILES[2], json.dumps({'status': 'completed',
            'report_evaluation': {'passed': True, 'issues': [], 'company_report_reflection': reflections},
            'deterministic_checks': {'source_reports_quality_passed': True, 'company_reports_synthesized': True},
            'report_synthesis': {'source_reports': source_reports},
            'source_contexts': [],
            'pdf_checks': {'all_a4': True, 'within_page_limit': True, 'text_within_margins': True},
            'companies': companies,
            'source_path': str(root / 'rag/data/raw/company/source.pdf')}))
        write(root, 'docs/architecture.svg', '<svg xmlns="http://www.w3.org/2000/svg"></svg>')
        # 테스트 fixture만 사용한다. 이 PNG를 실제 제출 경로에 생성하지 않는다.
        write(root, 'docs/traces/fixture.png', base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='))


def test_missing_official_artifacts_leave_clear_preflight_without_zip(tmp_path):
    source_tree(tmp_path, official=False)
    result = build_submission(tmp_path, secrets=())
    assert result['archive'] is None
    assert result['preflight']['artifact_ready'] is False
    assert any('최종' in item or 'final_investment_report' in item for item in result['preflight']['problems'])
    assert any('LangSmith' in item for item in result['preflight']['problems'])
    assert not list((tmp_path / 'output/submission').glob('*.zip'))


def test_allowlist_hashes_and_path_normalization_do_not_modify_source(tmp_path):
    source_tree(tmp_path)
    write(tmp_path, '.env', 'OPENAI_API_KEY=private-test-secret\n')
    write(tmp_path, 'output/live_reports/incorrect_old_scores.json', 'private-test-secret')
    write(tmp_path, '.cache/model.bin', b'private-test-secret')
    write(tmp_path, 'output/submission-live/figure_ai_draft.state.json', 'private-test-secret')
    write(tmp_path, 'output/submission-live/figure_ai_draft.trace.json', 'private-test-secret')
    original_app = (tmp_path / 'app.py').read_bytes()
    result = build_submission(tmp_path)
    assert result['archive'] is not None
    assert result['preflight']['submission_complete'] is False
    assert result['preflight']['team']['members'] == ['김테스트']
    checked = verify_archive(result['archive'], secrets=(b'private-test-secret',))
    assert checked['crc_verified'] is True
    with zipfile.ZipFile(result['archive']) as archive:
        names = archive.namelist()
        assert not any(name.endswith('/.env') or '/.cache/' in name or '/live_reports/' in name or '_draft.' in name for name in names)
        assert all(f'{ARCHIVE_ROOT}/output/submission-live/{stem}.state.json' in names for stem in COMPANY_STEMS)
        assert all(f'{ARCHIVE_ROOT}/output/submission-live/{stem}_brief.pdf' in names for stem in COMPANY_STEMS)
        assert all(f'{ARCHIVE_ROOT}/{name}' in names for name in ('company_report_inputs.py', 'report_synthesis.py'))
        packaged_app = archive.read(f'{ARCHIVE_ROOT}/app.py').decode()
        assert "os.getenv('ORIGINAL_RAG_PROJECT', str(ROOT / 'rag'))" in packaged_app
        assert 'private-person' not in packaged_app
        result_json = json.loads(archive.read(f'{ARCHIVE_ROOT}/{FINAL_FILES[2]}'))
        for source in result_json['report_synthesis']['source_reports']:
            stem = next(stem for stem, company in COMPANY_STEMS.items() if company == source['company'])
            state = json.loads(archive.read(f'{ARCHIVE_ROOT}/output/submission-live/{stem}.state.json'))['state']
            assert source['report_sha256'] == sha256(state['final_report'].encode('utf-8')).hexdigest()
        assert Path(result_json['source_path']) == Path('./rag/data/raw/company/source.pdf')
        assert f"{ARCHIVE_ROOT}/{Path(result_json['source_path']).as_posix()}" in names
        assert result['preflight']['source_path_checks']['catalog_path_references'] == 6
        manifest = json.loads(archive.read(f'{ARCHIVE_ROOT}/MANIFEST.json'))
        for record in manifest['files']:
            assert sha256(archive.read(f'{ARCHIVE_ROOT}/{record["path"]}')).hexdigest() == record['sha256']
    assert (tmp_path / 'app.py').read_bytes() == original_app
    assert (tmp_path / 'output/submission/archive.sha256').read_text().startswith(sha256(result['archive'].read_bytes()).hexdigest())


def test_original_company_input_directory_maps_to_standalone_com_data(tmp_path):
    source_tree(tmp_path)
    original_project = '/' + 'Users' + '/private-person/original-project'
    metadata = json.loads((tmp_path / FINAL_FILES[2]).read_text())
    metadata['input_paths'] = {'data_dir': f'{original_project}/data', 'com_data_dir': f'{original_project}/com_data'}
    write(tmp_path, FINAL_FILES[2], json.dumps(metadata))
    result = build_submission(tmp_path, secrets=())
    with zipfile.ZipFile(result['archive']) as archive:
        cleaned = json.loads(archive.read(f'{ARCHIVE_ROOT}/{FINAL_FILES[2]}'))
    assert cleaned['input_paths'] == {'data_dir': 'rag/data', 'com_data_dir': 'com_data'}


def test_nested_documentation_and_architecture_renderer_are_bundled(tmp_path):
    source_tree(tmp_path)
    expected = {
        'docs/traces/README.md': '# 실제 실행 화면\n[구조도](../architecture.svg)\n',
        'docs/traces/details/collection.md': '# 화면 수집 과정\n',
        'scripts/render_architecture.py': '"""Offline architecture renderer."""\n',
    }
    for name, content in expected.items():
        write(tmp_path, name, content)
    # docs 재귀 수집도 Markdown만 승인한다.
    write(tmp_path, 'docs/traces/details/session.env', 'fixture-excluded\n')
    result = build_submission(tmp_path, secrets=())
    assert result['archive'] is not None
    with zipfile.ZipFile(result['archive']) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read(f'{ARCHIVE_ROOT}/MANIFEST.json'))
        records = {record['path']: record for record in manifest['files']}
        for name, content in expected.items():
            data = archive.read(f'{ARCHIVE_ROOT}/{name}')
            assert data == content.encode('utf-8')
            assert records[name]['sha256'] == sha256(data).hexdigest()
        assert f'{ARCHIVE_ROOT}/docs/traces/details/session.env' not in names


def test_contributors_bullets_preserve_team_names_and_exclude_other_sections(tmp_path):
    source_tree(tmp_path)
    expected = ['이상수', '우강산', '정지영', '박소정', '김재혁']
    lines = '\n'.join(f'- {name} : 확정 담당 역할' for name in expected)
    write(tmp_path, 'README.md', f'# Subject\n\n## Contributors\n{lines}\n\n## Reference\n- 다른사람 : 참고 작성자\n')
    result = build_submission(tmp_path, secrets=())
    assert result['archive'] is not None
    assert result['preflight']['team']['members'] == expected
    with zipfile.ZipFile(result['archive']) as archive:
        manifest = json.loads(archive.read(f'{ARCHIVE_ROOT}/MANIFEST.json'))
    assert manifest['team']['members'] == expected


def test_known_secret_in_allowed_text_blocks_package_without_leaking_value(tmp_path):
    source_tree(tmp_path)
    secret = b'unit-secret-value-never-print'
    write(tmp_path, 'README.md', secret.decode())
    result = build_submission(tmp_path, secrets=(secret,))
    assert result['archive'] is None
    assert any('비밀값' in problem for problem in result['preflight']['problems'])
    assert secret.decode() not in json.dumps(result['preflight'])
    assert secret not in (tmp_path / 'output/submission/preflight.json').read_bytes()


def test_quality_failed_or_oversize_final_report_is_never_packaged(tmp_path):
    source_tree(tmp_path)
    write(tmp_path, FINAL_FILES[0], pdf_bytes(11))
    write(tmp_path, FINAL_FILES[2], json.dumps({'status': 'quality_failed', 'report_evaluation': {'passed': False}}))
    result = build_submission(tmp_path, secrets=())
    assert result['archive'] is None
    assert any('1~10쪽' in problem for problem in result['preflight']['problems'])
    assert any('품질 통과' in problem for problem in result['preflight']['problems'])


def test_symlink_to_external_source_is_rejected(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    source_tree(root)
    external = write(tmp_path, 'external.py', 'pass\n')
    (root / 'app.py').unlink()
    (root / 'app.py').symlink_to(external)
    result = build_submission(root, secrets=())
    assert result['archive'] is None
    assert any('심볼릭 링크' in problem for problem in result['preflight']['problems'])


def test_manifest_verification_rejects_extra_zip_members(tmp_path):
    source_tree(tmp_path)
    result = build_submission(tmp_path, secrets=())
    with zipfile.ZipFile(result['archive'], 'a') as archive:
        archive.writestr('../unsafe.txt', 'not-allowed')
    with pytest.raises(SubmissionError, match='파일 목록'):
        verify_archive(result['archive'])


def test_previous_passed_final_is_rejected_after_company_state_changes(tmp_path):
    source_tree(tmp_path)
    wrapper = json.loads((tmp_path / 'output/submission-live/motomind.state.json').read_text())
    wrapper['state']['trace_id'] = 'new-reviewed-company-run'
    write(tmp_path, 'output/submission-live/motomind.state.json', json.dumps(wrapper))
    write(tmp_path, 'output/submission-live/motomind.scores.json', json.dumps({'company': 'MOTOMIND', 'trace_id': 'new-reviewed-company-run'}))
    write(tmp_path, 'output/submission-live/motomind.trace.json', json.dumps({'trace_id': 'new-reviewed-company-run'}))
    result = build_submission(tmp_path, secrets=())
    assert result['archive'] is None
    assert any('이전 통과 보고서' in problem for problem in result['preflight']['problems'])


@pytest.mark.parametrize('change', ['replace_analysis', 'append_limitation', 'append_whitespace'])
def test_same_trace_id_with_changed_company_body_rejects_stale_synthesis(tmp_path, change):
    source_tree(tmp_path)
    filename = 'output/submission-live/figure_ai.state.json'
    wrapper = json.loads((tmp_path / filename).read_text())
    trace_id = wrapper['state']['trace_id']
    report = wrapper['state']['final_report']
    wrapper['state']['final_report'] = {
        'replace_analysis': report.replace('독립 검증', '독립 검증되지 않은 발표'),
        'append_limitation': report + '\n근거의 한계: 재구매는 미확인이다.\n',
        'append_whitespace': report + '\n',
    }[change]
    write(tmp_path, filename, json.dumps(wrapper))
    result = build_submission(tmp_path, secrets=())
    assert wrapper['state']['trace_id'] == trace_id
    assert result['archive'] is None
    assert any('본문 SHA256' in problem and 'Figure AI' in problem for problem in result['preflight']['problems'])
    assert not list((tmp_path / 'output/submission').glob('*.zip'))


@pytest.mark.parametrize('change', ['absent_flag', 'false_flag', 'absent_synthesis', 'absent_sources',
                                  'missing_company', 'duplicate_company', 'wrong_hash', 'wrong_source_trace'])
def test_missing_or_stale_company_report_synthesis_is_never_packaged(tmp_path, change):
    source_tree(tmp_path)
    metadata = json.loads((tmp_path / FINAL_FILES[2]).read_text())
    if change == 'absent_flag':
        metadata['deterministic_checks'].pop('company_reports_synthesized')
    elif change == 'false_flag':
        metadata['deterministic_checks']['company_reports_synthesized'] = False
    elif change == 'absent_synthesis':
        metadata.pop('report_synthesis')
    elif change == 'absent_sources':
        metadata['report_synthesis'].pop('source_reports')
    else:
        sources = metadata['report_synthesis']['source_reports']
        if change == 'missing_company':
            sources.pop()
        elif change == 'duplicate_company':
            sources[-1] = sources[0].copy()
        elif change == 'wrong_hash':
            sources[0]['report_sha256'] = '0' * 64
        elif change == 'wrong_source_trace':
            sources[0]['trace_id'] = 'previous-synthesis-run'
    write(tmp_path, FINAL_FILES[2], json.dumps(metadata))
    result = build_submission(tmp_path, secrets=())
    assert result['archive'] is None
    assert any('종합' in problem for problem in result['preflight']['problems'])


@pytest.mark.parametrize('change', ['absent', 'missing_company', 'duplicate_company',
                                  'analysis_lost', 'limitation_lost', 'opinion_distorted',
                                  'distortion_detail', 'missing_detail_list'])
def test_global_pass_cannot_override_missing_or_distorted_company_report_reflection(tmp_path, change):
    source_tree(tmp_path)
    metadata = json.loads((tmp_path / FINAL_FILES[2]).read_text())
    evaluation = metadata['report_evaluation']
    if change == 'absent':
        evaluation.pop('company_report_reflection')
    else:
        reflection = evaluation['company_report_reflection']
        if change == 'missing_company':
            reflection.pop()
        elif change == 'duplicate_company':
            reflection[-1] = reflection[0].copy()
        elif change == 'analysis_lost':
            reflection[0]['analysis_preserved'] = False
        elif change == 'limitation_lost':
            reflection[1]['limitations_preserved'] = False
        elif change == 'opinion_distorted':
            reflection[2]['individual_opinion_preserved'] = False
        elif change == 'distortion_detail':
            reflection[2]['missing_or_distorted'] = ['개별 투자 보류를 즉시 투자 승인으로 변경했다.']
        elif change == 'missing_detail_list':
            reflection[0].pop('missing_or_distorted')
    assert evaluation['passed'] is True and evaluation['issues'] == []
    write(tmp_path, FINAL_FILES[2], json.dumps(metadata))
    result = build_submission(tmp_path, secrets=())
    assert result['archive'] is None
    assert any('반영 품질' in problem for problem in result['preflight']['problems'])


@pytest.mark.parametrize(('report', 'source_text', 'expected_issue'), [
    ('확인 사실: 로브로스는 강화학습 기반 운동지능을 자체 개발한다. [RB-E1]',
     '50명 이상의 팀이 하드웨어 설계부터 AI 지능 개발까지 자체 기술로 수행한다.', '강화학습'),
    ('확인 사실: MOU 협력으로 공동 연구와 기술 교류 기반을 확보했다. [MM-E1]',
     'MOTOMIND와 LG사이언스파크는 MOU를 체결했다.', 'MOU 목적'),
    ('확인 사실: IFR 보고서는 제조업 인력 부족이 자동화 수요를 촉진한다고 설명한다. [RB-E1]',
     '50명 이상의 팀이 하드웨어 설계부터 AI 지능 개발까지 자체 기술로 수행한다.', 'IFR 수요'),
])
def test_llm_pass_does_not_override_final_markdown_original_scope_error(tmp_path, report, source_text, expected_issue):
    source_tree(tmp_path)
    metadata = json.loads((tmp_path / FINAL_FILES[2]).read_text())
    ref = 'MM-E1' if 'MM-E1' in report else 'RB-E1'
    metadata['source_contexts'] = [{'company': 'MOTOMIND' if ref.startswith('MM-') else 'ROBROS',
        'doc_id': 'DOC-1', 'page': 1, 'evidence_ids': [ref], 'page_text': source_text}]
    assert metadata['report_evaluation']['passed'] is True and metadata['report_evaluation']['issues'] == []
    write(tmp_path, FINAL_FILES[1], report)
    write(tmp_path, FINAL_FILES[2], json.dumps(metadata))
    result = build_submission(tmp_path, secrets=())
    assert result['archive'] is None
    assert any('원문 범위 검사 실패' in problem and expected_issue in problem
               for problem in result['preflight']['problems'])
    assert not list((tmp_path / 'output/submission').glob('*.zip'))


def test_final_markdown_with_actual_linked_scope_support_can_be_packaged(tmp_path):
    source_tree(tmp_path)
    report = '확인 사실: 로브로스는 강화학습 기반 운동지능을 자체 개발한다고 회사가 발표했다. [RB-E1]'
    metadata = json.loads((tmp_path / FINAL_FILES[2]).read_text())
    metadata['source_contexts'] = [{'company': 'ROBROS', 'doc_id': 'DOC-1', 'page': 1,
        'evidence_ids': ['RB-E1'], 'page_text': '로브로스는 강화학습 기반 운동지능을 자체 개발한다고 발표했다.'}]
    write(tmp_path, FINAL_FILES[1], report)
    write(tmp_path, FINAL_FILES[2], json.dumps(metadata))
    result = build_submission(tmp_path, secrets=())
    assert result['archive'] is not None


@pytest.mark.parametrize('contexts', [None, {}, [{}], [{'evidence_ids': ['RB-E1'], 'page_text': None}]])
def test_missing_or_malformed_original_scope_inputs_block_submission(tmp_path, contexts):
    source_tree(tmp_path)
    metadata = json.loads((tmp_path / FINAL_FILES[2]).read_text())
    if contexts is None:
        metadata.pop('source_contexts')
    else:
        metadata['source_contexts'] = contexts
    write(tmp_path, FINAL_FILES[2], json.dumps(metadata))
    result = build_submission(tmp_path, secrets=())
    assert result['archive'] is None
    assert any('인용 원문 입력 형식 오류' in problem for problem in result['preflight']['problems'])


@pytest.mark.parametrize('change_body', [False, True])
def test_final_markdown_sidecar_hash_prevents_post_save_body_changes(tmp_path, change_body):
    source_tree(tmp_path)
    metadata = json.loads((tmp_path / FINAL_FILES[2]).read_text())
    body = (tmp_path / FINAL_FILES[1]).read_bytes()
    metadata['markdown_sha256'] = sha256(body).hexdigest()
    write(tmp_path, FINAL_FILES[2], json.dumps(metadata))
    if change_body:
        write(tmp_path, FINAL_FILES[1], body + b'\n')
    result = build_submission(tmp_path, secrets=())
    if change_body:
        assert result['archive'] is None
        assert any('본문 SHA256과 Markdown 불일치' in problem for problem in result['preflight']['problems'])
    else:
        assert result['archive'] is not None
