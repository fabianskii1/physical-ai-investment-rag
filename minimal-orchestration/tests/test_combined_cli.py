import json
from unittest.mock import patch

import pytest

import app


def save_state(directory, company):
    stem = company.lower().replace(' ', '_')
    state = {'company_info': {'name': company}, 'trace_id': company,
             'status': 'partial', 'investment_result': {'total_score': 3}}
    (directory / f'{stem}.state.json').write_text(json.dumps({'state': state, 'mode': 'live'}))
    (directory / f'{stem}.scores.json').write_text(json.dumps({'company': company, 'trace_id': company}))


def test_combining_saved_states_never_researches_or_recalculates(tmp_path):
    for company in app.COMPANIES:
        save_state(tmp_path, company)
    with patch.object(app, '_combine_results', return_value={'status': 'partial'}) as combine, \
         patch.object(app, 'run', side_effect=AssertionError), \
         patch.object(app, 'real_dependencies', side_effect=AssertionError):
        assert app.combine_reports(tmp_path)['status'] == 'partial'
    records, target = combine.call_args.args
    assert [record['state']['company_info']['name'] for record in records] == list(app.COMPANIES)
    assert all(record['state']['investment_result']['total_score'] == 3 for record in records)
    assert target == tmp_path


def test_combining_rejects_different_score_run_and_incomplete_companies(tmp_path):
    save_state(tmp_path, app.COMPANIES[0])
    with pytest.raises(FileNotFoundError):
        app.combine_reports(tmp_path)
    for company in app.COMPANIES:
        save_state(tmp_path, company)
    path = tmp_path / 'motomind.scores.json'
    path.write_text(json.dumps({'company': 'MOTOMIND', 'trace_id': 'other-run'}))
    with pytest.raises(ValueError, match='실행 ID'):
        app.combine_reports(tmp_path)
