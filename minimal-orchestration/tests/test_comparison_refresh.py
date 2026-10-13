"""저장된 점수와 PDF로 비교표를 갱신하며 API·검색·출력을 다시 실행하지 않는다."""

import json
import os

import pytest

import app
from rag.scorecard import SCORECARD, calculate_scorecard


def save_report(directory, company, *, status='partial', grade=2, modified=100):
    stem = company.lower().replace(' ', '_') + ('_draft' if status == 'quality_failed' else '')
    pdf = directory / f'{stem}.pdf'
    pdf.write_bytes(b'existing PDF; layout validation is mocked')
    scored = calculate_scorecard({name: grade if name == '핵심 작업 성능' else None
                                 for items in SCORECARD.values() for name in items})
    state = {'company_info': {'name': company}, 'status': status, 'trace_id': f'{stem}-trace',
             'recheck_count': 3,
             'research_result': {'research_criteria_count': 18, 'attempted_criteria_count': 18,
                                 'supported_criteria_count': 1},
             'investment_result': {'score_details': scored['items'], 'total_score': scored['earned_score'],
                                   'scorable_weight': scored['scorable_weight'], 'unknown_weight': scored['unknown_weight'],
                                   'investment_decision': '근거 부족', 'decision_reason': '검증 범위의 제한'},
             'report_evaluation': {'passed': status != 'quality_failed', 'issues': []}}
    scores = app.score_snapshot(state, mode='live', inputs={}, index_info={}, report_path=pdf)
    path = directory / f'{stem}.scores.json'
    path.write_text(json.dumps(scores, ensure_ascii=False), encoding='utf-8')
    os.utime(path, ns=(modified, modified))
    return path, pdf, scores


@pytest.fixture(autouse=True)
def existing_outputs_only(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('비교표 갱신에서 API·검색·보고서 생성을 호출했습니다')
    for name in ('real_dependencies', 'load_project_catalog', 'render_report', 'run', 'run_batch'):
        monkeypatch.setattr(app, name, forbidden)
    monkeypatch.setattr(app, 'validate_pdf', lambda path: {
        'pages': 5, 'all_a4': True, 'within_page_limit': True, 'text_within_margins': True})


def test_newest_recovered_final_replaces_old_draft_and_preserves_all_statuses(tmp_path, monkeypatch):
    save_report(tmp_path, 'MOTOMIND', status='quality_failed', grade=1, modified=100)
    final_path, final_pdf, scores = save_report(tmp_path, 'MOTOMIND', grade=3, modified=200)
    save_report(tmp_path, 'ROBROS', status='quality_failed')
    save_report(tmp_path, 'Figure AI', status='completed')
    # 이전 생성기의 상대 경로도 현재 작업 디렉터리와 무관하게 처리한다.
    scores['report_path'] = f'{tmp_path.name}/{final_pdf.name}'
    final_path.write_text(json.dumps(scores), encoding='utf-8')
    monkeypatch.chdir(tmp_path.parent)

    refreshed = app.refresh_comparison(tmp_path.name)

    assert refreshed['errors'] == {}
    assert refreshed['results']['MOTOMIND']['scores_path'] == final_path
    comparison = json.loads(refreshed['comparison']['json'].read_text())
    assert [row['status'] for row in comparison['companies']] == ['partial', 'quality_failed', 'completed']
    assert comparison['companies'][0]['total_score'] == 4.5
    assert comparison['companies'][0]['report_path'] == str(final_pdf)
    assert all(path.is_absolute() and path.is_file() for path in refreshed['comparison'].values())
    assert str(final_pdf) in refreshed['comparison']['markdown'].read_text()
    assert refreshed['comparison']['csv'].read_bytes().startswith(b'\xef\xbb\xbf')


def test_newer_draft_replaces_old_final_without_changing_quality_failure(tmp_path):
    save_report(tmp_path, 'MOTOMIND', status='completed', modified=100)
    draft_path, _, _ = save_report(tmp_path, 'MOTOMIND', status='quality_failed', modified=200)

    refreshed = app.refresh_comparison(tmp_path)

    assert refreshed['results']['MOTOMIND']['scores_path'] == draft_path
    assert refreshed['results']['MOTOMIND']['scores']['status'] == 'quality_failed'


def test_missing_companies_are_failed_and_do_not_discard_saved_company(tmp_path):
    save_report(tmp_path, 'MOTOMIND')

    refreshed = app.refresh_comparison(tmp_path)

    assert set(refreshed['results']) == {'MOTOMIND'}
    assert refreshed['errors'] == {'ROBROS': 'FileNotFoundError', 'Figure AI': 'FileNotFoundError'}
    rows = json.loads(refreshed['comparison']['json'].read_text())['companies']
    assert rows[1]['status'] == 'failed' and rows[1]['report_path'] is None
    assert rows[1]['error'] == 'FileNotFoundError'


@pytest.mark.parametrize('corruption,error_type', [
    ('company', 'ValueError'), ('mode', 'ValueError'), ('score_scale', 'ValueError'),
    ('total_score', 'ValueError'), ('category_scores', 'ValueError'), ('report_path', 'ValueError'),
    ('evaluation', 'ValueError'), ('missing_pdf', 'FileNotFoundError'), ('pdf_layout', 'ValueError'),
])
def test_invalid_latest_metadata_or_pdf_is_failed_without_old_result_fallback(tmp_path, monkeypatch, corruption, error_type):
    save_report(tmp_path, 'MOTOMIND', status='quality_failed', modified=100)
    path, pdf, scores = save_report(tmp_path, 'MOTOMIND', modified=200)
    if corruption == 'company':
        scores['company'] = 'ROBROS'
    elif corruption == 'mode':
        scores['mode'] = 'demo'
    elif corruption == 'score_scale':
        scores['score_scale'] = 99
    elif corruption == 'total_score':
        scores['total_score'] = 99
    elif corruption == 'category_scores':
        scores['category_scores']['제품·기술력']['weight'] = 99
    elif corruption == 'report_path':
        scores['report_path'] = str(tmp_path / 'motomind_draft.pdf')
    elif corruption == 'evaluation':
        scores['report_evaluation']['passed'] = False
    elif corruption == 'missing_pdf':
        pdf.unlink()
    elif corruption == 'pdf_layout':
        monkeypatch.setattr(app, 'validate_pdf', lambda path: {
            'all_a4': True, 'within_page_limit': False, 'text_within_margins': True})
    path.write_text(json.dumps(scores), encoding='utf-8')

    refreshed = app.refresh_comparison(tmp_path)

    assert 'MOTOMIND' not in refreshed['results']
    assert refreshed['errors']['MOTOMIND'] == error_type
    failed = json.loads(refreshed['comparison']['json'].read_text())['companies'][0]
    assert failed['status'] == 'failed' and failed['error'] == error_type


def test_cli_refreshes_comparison_without_entering_analysis_path(tmp_path, monkeypatch, capsys):
    save_report(tmp_path, 'MOTOMIND')
    monkeypatch.setattr('sys.argv', ['app.py', '--refresh-comparison', str(tmp_path)])

    app.main()

    assert (tmp_path / 'company_comparison.json').is_file()
    assert '성공 1 / 실패 2' in capsys.readouterr().out
