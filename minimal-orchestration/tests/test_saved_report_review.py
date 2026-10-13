"""저장 보고서 검토·원문 재검증을 실제 app/graph 경로에서 오프라인 검증한다."""
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

import app
from agent_pipeline import PipelineDependencies, initial_state
from rag.scorecard import SCORECARD, calculate_scorecard
from saved_revalidation import revalidate_saved_research


@pytest.fixture(autouse=True)
def disable_remote_tracing(monkeypatch):
    monkeypatch.setenv('LANGSMITH_TRACING_V2', 'false')
    monkeypatch.setenv('LANGCHAIN_TRACING_V2', 'false')


def forbidden(*args, **kwargs):
    raise AssertionError('저장 보고서 작업에서 신규 조사·검색·임베딩 호출 금지')


def graded(claim, grade):
    grades = {name: None for group in SCORECARD.values() for name in group}
    grades['핵심 작업 성능'] = grade
    scored = calculate_scorecard(grades)
    scored['items']['핵심 작업 성능']['evidence'] = [deepcopy(claim)]
    return {'score_details': scored['items'], 'total_score': scored['earned_score'],
            'scorable_weight': scored['scorable_weight'], 'unknown_weight': scored['unknown_weight'],
            'investment_decision': '근거 부족', 'decision_reason': '검증 범위 안의 평가',
            'investment_case': {'supporting_facts': [{'criterion': '핵심 작업 성능',
                'fact': claim['claim'], 'evidence_ids': ['E1']}]}}


def stored_report(tmp_path):
    removed = {'claim': '과거에 완료로 확대했던 양산 계획', 'analysis': 'technology_analysis',
               'criterion_id': 'mass_production_maintenance', 'status': 'confirmed',
               'source': {'doc_id': 'DOC-1', 'page': 1}}
    retained = {'claim': '공개한 작업 사양', 'analysis': 'technology_analysis',
                'criterion_id': 'core_task_performance', 'status': 'confirmed',
                'source': {'doc_id': 'DOC-2', 'page': 1}}
    state = initial_state({'name': 'MOTOMIND'}, {'DOC-1': {}, 'DOC-2': {}},
                          {'require_criteria_coverage': True}, max_rechecks=3)
    state.update(recheck_count=1, research_stop_reason='no_progress', no_progress_rounds=2,
                 last_progress_round=1, report_rewrite_count=2, max_report_rewrites=2,
                 status='quality_failed', step_count=18, final_report='# 기존 보고서 [E2]')
    state['research_result'] = {'source_checked_claims': [removed, retained], 'evidence_status': 'scorable',
        'evidence_gaps': [], 'technology_analysis': {'claims': [removed, retained]},
        'criterion_attempts': {'core_task_performance': [{'round': 0, 'task_id': 'technology'}]},
        'last_verified_signature': 'old-signature', 'score_review_issues': ['old-E2-grade']}
    task = {'id': 'technology-last', 'worker': 'technology', 'query': '이전 라운드의 조사 질문'}
    state['tasks'] = [task]
    # 실제 저장 State에는 마지막 라운드의 원래 워커 반환도 남는다. 검증에서 제외된
    # 홍보 주장을 품질 실패 후 Synthesizer가 다시 병합하지 않아야 한다.
    advertising = {'claim': '검증에서 제외한 세계 최고 홍보', 'criterion_id': 'team_expertise',
                   'status': 'unverified', 'source_refs': [{'doc_id': 'DOC-1', 'page': 1}]}
    state['worker_results'] = {'1:technology-last': {'task': task, 'worker': 'technology',
        'recheck_count': 1, 'analysis': {'claims': [removed, retained, advertising]}}}
    state['investment_result'] = graded(retained, 4)
    state['investment_result']['investment_case']['supporting_facts'][0]['evidence_ids'] = ['E2']
    state['report_evaluation'] = {'passed': False, 'issues': ['과거 서술 미달'], 'action': 'rewrite'}
    inputs = {name: str(tmp_path / leaf) for name, leaf in
              (('data_dir', 'data'), ('com_data_dir', 'com_data'), ('index_dir', 'index'))}
    index_info = {'initialized': True, 'cache_hit': True, 'chunks': 17}
    prior = app.score_snapshot(state, mode='live', inputs=inputs, index_info=index_info,
                               report_path=tmp_path / 'original.pdf')
    prior['tracing'] = {'enabled': False, 'root_run_id': None}
    scores_path = tmp_path / 'original.scores.json'
    scores_path.write_text(json.dumps(prior), encoding='utf-8')
    path = tmp_path / 'original.state.json'
    path.write_text(json.dumps({'state': state, 'mode': 'live', 'scores_path': str(scores_path)}), encoding='utf-8')
    return state, prior, path, retained


@pytest.mark.parametrize('passed', [True, False])
def test_review_only_preserves_original_state_grades_body_and_all_budgets(tmp_path, passed):
    state, prior, path, _ = stored_report(tmp_path)
    original = deepcopy(state)
    original_file = path.read_bytes()
    seen = []
    def evaluator(payload):
        seen.append(payload)
        assert payload['final_report'] == original['final_report']
        assert payload['score_details'] == original['investment_result']['score_details']
        return {'passed': passed, 'issues': [] if passed else ['여전히 원문에서 지지되지 않는 설명'],
                'action': 'research'}
    deps = PipelineDependencies(forbidden, forbidden, forbidden, forbidden, forbidden, evaluator)
    with patch.object(app, 'render_report'), patch.object(app, 'validate_pdf', return_value={'pages': 1}):
        output = app._review_report(state, prior, path, deps, tmp_path / 'reviewed')
    reviewed = output['state']
    assert len(seen) == 1
    assert state == original and path.read_bytes() == original_file
    assert reviewed['investment_result'] == original['investment_result']
    assert reviewed['research_result'] == original['research_result']
    assert reviewed['final_report'] == original['final_report']
    for field in ('recheck_count', 'max_rechecks', 'report_rewrite_count', 'max_report_rewrites', 'no_progress_rounds'):
        assert reviewed[field] == original[field]
    assert reviewed['status'] == ('partial' if passed else 'quality_failed')
    assert reviewed['trace_id'] != original['trace_id']
    assert output['scores']['index_info'] == prior['index_info']
    assert output['scores']['input_paths'] == prior['input_paths']


def test_public_review_prepares_no_retrieval_and_uses_saved_input_index_metadata(tmp_path):
    _, prior, path, _ = stored_report(tmp_path)
    deps = PipelineDependencies(forbidden, forbidden, forbidden, forbidden, forbidden,
                                lambda payload: {'passed': True, 'issues': []})
    with patch.object(app, 'real_dependencies', return_value=deps) as prepared, \
         patch.object(app, 'render_report'), patch.object(app, 'validate_pdf', return_value={'pages': 1}):
        output = app.review_report(path, output_dir=tmp_path / 'reviewed')
    assert prepared.call_count == 1
    assert prepared.call_args.kwargs['prepare_retrieval'] is False
    assert prepared.call_args.kwargs['data_dir'] == prior['input_paths']['data_dir']
    assert prepared.call_args.args[0] == prior['input_paths']['com_data_dir']
    assert output['scores']['index_info'] == prior['index_info']


def test_revalidation_discards_old_investment_ids_and_resets_only_report_budget(tmp_path):
    state, _, _, retained = stored_report(tmp_path)
    original = deepcopy(state)
    calls = []
    def checker(payload):
        calls.append('evidence')
        assert [item['claim'] for item in payload['technology_analysis']['claims']] == [
            item['claim'] for item in original['research_result']['source_checked_claims']]
        return {'source_checked_claims': [deepcopy(retained)], 'evidence_gaps': [], 'evidence_status': 'scorable'}
    def judge(payload):
        calls.append('judge')
        assert payload['investment_result'] == {}
        assert 'score_details' not in payload
        assert payload['source_checked_claims'] == [retained]
        return graded(payload['source_checked_claims'][0], 1)
    deps = PipelineDependencies(forbidden, forbidden, checker, judge, forbidden, forbidden)
    repaired = app._revalidate_saved_state(state, deps, revalidate_saved_research)
    assert calls == ['evidence', 'judge'] and state == original
    assert repaired['investment_result']['total_score'] == 1.5
    assert repaired['investment_result']['investment_case']['supporting_facts'][0]['evidence_ids'] == ['E1']
    assert repaired['research_result']['coverage_by_criterion']['core_task_performance']['evidence_ids'] == ['E1']
    assert 'last_verified_signature' not in repaired['research_result']
    assert repaired['final_report'] == '' and repaired['report_evaluation'] == {}
    assert repaired['report_rewrite_count'] == 0
    for field in ('recheck_count', 'max_rechecks', 'max_report_rewrites', 'research_stop_reason', 'no_progress_rounds'):
        assert repaired[field] == original[field]


@pytest.mark.parametrize('action,expected_writers,expected_rewrites', [('rewrite', 3, 2), ('research', 1, 0)])
def test_public_revalidation_is_bounded_and_never_returns_to_workers(tmp_path, action, expected_writers, expected_rewrites):
    state, prior, path, retained = stored_report(tmp_path)
    original = deepcopy(state)
    original_file = path.read_bytes()
    calls = []
    def checker(payload):
        calls.append('evidence')
        return {'source_checked_claims': [deepcopy(retained)], 'evidence_gaps': [], 'evidence_status': 'scorable'}
    def judge(payload):
        calls.append('judge')
        assert payload['investment_result'] == {}
        return graded(payload['source_checked_claims'][0], 1)
    def writer(payload):
        calls.append('writer')
        assert payload['decision_policy']['resume_report_only'] is True
        assert payload['investment_case']['supporting_facts'][0]['evidence_ids'] == ['E1']
        assert payload['total_score'] == 1.5
        return {'final_report': '# 새 근거·등급에 연결된 보고서 [E1]'}
    def evaluator(payload):
        calls.append('evaluator')
        return {'passed': False, 'issues': ['새 보고서의 실제 결함 fixture'], 'action': action}
    deps = PipelineDependencies(forbidden, forbidden, checker, judge, writer, evaluator)
    with patch.object(app, 'real_dependencies', return_value=deps) as prepared, \
         patch.object(app, 'load_project_catalog', side_effect=forbidden), \
         patch.object(app, 'load_company_research_input', side_effect=forbidden), \
         patch.object(app, 'render_report'), patch.object(app, 'validate_pdf', return_value={'pages': 1}):
        output = app.revalidate_report(path, output_dir=tmp_path / 'revalidated')
    assert prepared.call_count == 1 and prepared.call_args.kwargs['prepare_retrieval'] is False
    assert calls.count('evidence') == calls.count('judge') == 1
    assert calls.count('writer') == calls.count('evaluator') == expected_writers
    assert state == original and path.read_bytes() == original_file
    final = output['state']
    assert final['status'] == 'quality_failed'
    assert final['recheck_count'] == original['recheck_count'] < final['max_rechecks']
    assert final['report_rewrite_count'] == expected_rewrites
    assert final['max_report_rewrites'] == 2
    assert final['investment_result']['total_score'] == 1.5
    assert final['tasks'] == []
    assert final['worker_results'] == original['worker_results']
    assert final['research_result']['source_checked_claims'] == [retained]
    assert output['scores']['index_info'] == prior['index_info']
    assert output['scores']['input_paths'] == prior['input_paths']
