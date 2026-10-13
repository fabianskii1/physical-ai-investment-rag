"""기업별 독립 실행·동일 배점 비교·부분 실패 계속 처리의 오프라인 검사."""
import csv
import json
from types import SimpleNamespace
from unittest.mock import patch
import pytest
import app
from rag.scorecard import SCORECARD, calculate_scorecard


@pytest.fixture(autouse=True)
def offline_tracing(monkeypatch):
    # 개발자의 .env에 실제 키가 있어도 이 테스트에서 원격 trace를 보내지 않는다.
    monkeypatch.setenv('LANGSMITH_TRACING_V2', 'false')
    monkeypatch.setenv('LANGCHAIN_TRACING_V2', 'false')


class FakeGraph:
    states = []

    def stream(self, state, config, **kwargs):
        self.state = state
        self.states.append(state)
        scored = calculate_scorecard({name: (4 if name == '핵심 작업 성능' else None)
            for group in SCORECARD.values() for name in group})
        self.final = {**state, 'status': 'partial', 'final_report': f"# {state['company_info']['name']} 보고서",
            'report_evaluation': {'passed': True},
            'investment_result': {'score_details': scored['items'], 'total_score': scored['earned_score'],
                'scorable_weight': scored['scorable_weight'], 'unknown_weight': scored['unknown_weight'],
                'investment_decision': '근거 부족', 'decision_reason': '검증 근거의 배점이 제한적입니다.'}}
        yield {'orchestrator': {}}

    def get_state(self, config):
        return SimpleNamespace(values=self.final)


def test_batch_shares_one_dependencies_object_but_never_company_states(tmp_path):
    FakeGraph.states = []
    deps = SimpleNamespace(index_info={'cache_hit': True, 'chunks': 31})
    prepared = {'context': '검증 전 기업자료', 'source_pdf': '/fake/input.pdf', 'total_pages': 1}
    with patch.object(app, 'load_project_catalog', return_value={}) as catalog, \
         patch.object(app, 'real_dependencies', return_value=deps) as dependencies, \
         patch.object(app, 'load_company_research_input', return_value=prepared), \
         patch.object(app, 'build_graph', side_effect=lambda *a, **kw: FakeGraph()) as graphs, \
         patch.object(app, 'render_report'), \
         patch.object(app, 'validate_pdf', return_value={'pages': 5}):
        result = app.run_batch(output_dir=tmp_path)
    assert dependencies.call_count == catalog.call_count == 1
    assert graphs.call_count == 3
    assert all(call.args[0] is deps for call in graphs.call_args_list)
    assert [state['company_info']['name'] for state in FakeGraph.states] == list(app.COMPANIES)
    assert len({state['trace_id'] for state in FakeGraph.states}) == 3
    assert all(state['decision_policy']['require_criteria_coverage'] for state in FakeGraph.states)
    assert not result['errors']
    for company, output in result['results'].items():
        scores = json.loads(output['scores_path'].read_text())
        assert scores['company'] == company
        assert len(scores['score_details']) == 21
        assert scores['total_score'] == 6  # U를 제외해 100점으로 재환산하지 않는다.
        assert scores['scorable_weight'] == 6
        assert scores['unknown_weight'] == 94
        assert scores['index_info']['cache_hit'] is True
    comparison = json.loads(result['comparison']['json'].read_text())
    assert [row['company'] for row in comparison['companies']] == list(app.COMPANIES)
    assert all('rank' not in row for row in comparison['companies'])
    with result['comparison']['csv'].open(encoding='utf-8-sig') as stream:
        rows = list(csv.DictReader(stream))
        assert len(rows) == 3
        assert all(row['투자조건'] == 'U' for row in rows)
        assert all(row['제품·기술력'] == '6.0' for row in rows)
    assert '| 투자조건 | U | 10 | 0 |' in result['comparison']['markdown'].read_text()


def test_category_all_unknown_is_u_but_verified_zero_grade_keeps_zero(tmp_path):
    grades = {name: None for group in SCORECARD.values() for name in group}
    grades['핵심 작업 성능'] = 0
    scored = calculate_scorecard(grades)
    state = {'company_info': {'name': 'MOTOMIND'}, 'status': 'partial', 'trace_id': 'test',
             'recheck_count': 1, 'investment_result': {'score_details': scored['items']}}
    snapshot = app.score_snapshot(state, mode='live', inputs={}, index_info={}, report_path=tmp_path / 'report.pdf')
    assert snapshot['category_scores']['투자조건']['earned_score'] is None
    assert snapshot['category_scores']['제품·기술력']['earned_score'] == 0
    assert snapshot['category_scores']['제품·기술력']['scorable_weight'] == 6


def test_batch_continues_other_companies_when_one_fails_and_suppresses_raw_api_errors(tmp_path):
    def fake_run(company, **kwargs):
        if company == 'ROBROS':
            raise RuntimeError('secret header or API key must not be serialized')
        state = {'company_info': {'name': company}, 'status': 'partial', 'trace_id': company,
                 'recheck_count': 1, 'investment_result': {}, 'report_evaluation': {'passed': True}}
        pdf = tmp_path / f'{company}.pdf'
        return {'pdf': pdf, 'scores': app.score_snapshot(state, mode='live', inputs={}, index_info={}, report_path=pdf)}
    with patch.object(app, 'run', side_effect=fake_run) as run:
        result = app.run_batch(output_dir=tmp_path, _dependencies=object(), _catalog={})
    assert run.call_count == 3
    assert set(result['results']) == {'MOTOMIND', 'Figure AI'}
    assert result['errors'] == {'ROBROS': 'RuntimeError'}
    assert 'secret header' not in result['comparison']['json'].read_text()
    rows = json.loads(result['comparison']['json'].read_text())['companies']
    assert rows[1]['status'] == 'failed' and rows[1]['total_score'] is None


def test_explicit_input_paths_override_original_and_copy_defaults(tmp_path):
    dirs = app.input_directories(tmp_path / 'data', tmp_path / 'com_data', tmp_path / 'index')
    assert dirs == {name: str((tmp_path / child).resolve()) for name, child in (
        ('data_dir', 'data'), ('com_data_dir', 'com_data'), ('index_dir', 'index'))}


def report_failure_snapshot(tmp_path):
    """조사·채점이 완료된 자체 실패 기록을 네트워크 없이 만든다."""
    from agent_pipeline import initial_state
    grades = {name: (2 if name == '핵심 작업 성능' else None) for group in SCORECARD.values() for name in group}
    scored = calculate_scorecard(grades)
    state = initial_state({'name': 'MOTOMIND', 'domain': '제조·물류'}, {},
                          {'require_criteria_coverage': True}, max_rechecks=3)
    state.update(recheck_count=3, status='partial', step_count=17)
    state['research_result'] = {'source_checked_claims': [], 'evidence_gaps': [], 'evidence_status': 'insufficient'}
    state['investment_result'] = {'score_details': scored['items'], 'total_score': scored['earned_score'],
                                 'scorable_weight': scored['scorable_weight'], 'unknown_weight': scored['unknown_weight']}
    path = tmp_path / 'motomind.failed_state.json'
    wrapper = {'error_type': 'ValueError', 'state': state, 'input_paths': {},
               'index_info': {'cache_hit': True, 'chunks': 10}}
    path.write_text(json.dumps(wrapper), encoding='utf-8')
    return path, state


def test_report_resume_runs_writer_then_evaluator_without_replanning_or_grading(tmp_path):
    from agent_pipeline import PipelineDependencies
    path, saved = report_failure_snapshot(tmp_path)
    calls = []
    def forbidden(*args):
        raise AssertionError('보고서 복구에서 조사·점수·검증을 다시 호출하면 안 됩니다')
    def writer(state):
        calls.append('writer')
        assert state['source_checked_claims'] == saved['research_result']['source_checked_claims']
        assert state['score_details'] == saved['investment_result']['score_details']
        return {'final_report': '# 복구 보고서'}
    deps = PipelineDependencies(forbidden, forbidden, forbidden, forbidden, writer,
                                lambda state: (calls.append('report_evaluator') or {'passed': True, 'issues': []}))
    with patch.object(app, 'render_report'), patch.object(app, 'validate_pdf', return_value={'pages': 1}):
        result = app.resume_report(path, _dependencies=deps)
    assert calls == ['writer', 'report_evaluator']
    assert result['state']['investment_result'] == saved['investment_result']
    assert result['state']['research_result']['source_checked_claims'] == saved['research_result']['source_checked_claims']
    assert result['state']['recheck_count'] == result['state']['max_rechecks'] == 3
    assert result['state']['step_count'] == 19
    assert result['state']['status'] == 'partial'
    assert result['state']['trace_id'] != saved['trace_id']
    trace = json.loads(result['trace'].read_text())
    assert trace['resume']['resumed_from_trace_id'] == saved['trace_id']
    assert trace['index_info']['chunks'] == 10
    assert [next(iter(event)) for event in trace['events']] == ['writer', 'report_evaluator']


def test_report_resume_quality_failure_never_converts_to_pass_or_consumes_new_research(tmp_path):
    from agent_pipeline import PipelineDependencies
    path, saved = report_failure_snapshot(tmp_path)
    unexpected_calls = []
    def forbidden(*args):
        unexpected_calls.append(args)
        raise AssertionError('소진된 검토 예산으로 다시 조사·검증하면 안 됩니다')
    deps = PipelineDependencies(forbidden, forbidden, forbidden, forbidden,
        lambda state: {'final_report': '# 품질 미달 보고서'},
        lambda state: {'passed': False, 'issues': ['근거 연결 설명 미달']})
    with patch.object(app, 'render_report'), patch.object(app, 'validate_pdf', return_value={'pages': 1}):
        result = app.resume_report(path, _dependencies=deps)
    assert result['state']['status'] == 'quality_failed'
    assert result['state']['report_evaluation']['passed'] is False
    assert result['state']['investment_result'] == saved['investment_result']
    assert result['state']['research_result']['source_checked_claims'] == []
    assert result['state']['recheck_count'] == 3
    assert result['pdf'].name.endswith('_draft.pdf')
    assert unexpected_calls == []


def test_report_resume_rejects_incomplete_grades_or_unused_recheck_budget(tmp_path):
    import pytest
    path, saved = report_failure_snapshot(tmp_path)
    wrapper = json.loads(path.read_text())
    wrapper['state']['recheck_count'] = 2
    path.write_text(json.dumps(wrapper))
    with pytest.raises(ValueError, match='예산이 소진'):
        app.resume_report(path, _dependencies=object())
    wrapper['state']['recheck_count'] = 3
    wrapper['state']['investment_result']['score_details'].pop('핵심 작업 성능')
    path.write_text(json.dumps(wrapper))
    with pytest.raises(ValueError, match='21항목'):
        app.resume_report(path, _dependencies=object())


def test_early_pdf_failure_recovers_without_any_agent_or_retrieval_calls(tmp_path):
    path, _ = report_failure_snapshot(tmp_path)
    wrapper = json.loads(path.read_text())
    state = wrapper['state']
    state.update(recheck_count=0, status='completed', final_report='# 품질 통과한 보고서',
                 report_evaluation={'passed': True, 'issues': []})
    wrapper.update(failed_stage='pdf', events=[{'report_evaluator': {'passed': True}}],
                   configured_models={'openai_model': 'original-model'})
    path.write_text(json.dumps(wrapper), encoding='utf-8')
    forbidden = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('PDF 복구에서 에이전트 호출 금지'))
    with patch.object(app, 'real_dependencies', side_effect=forbidden), \
         patch.object(app, 'build_graph', side_effect=forbidden), \
         patch.object(app, 'load_project_catalog', side_effect=forbidden), \
         patch.object(app, 'render_report') as renderer, \
         patch.object(app, 'validate_pdf', return_value={'pages': 1}):
        result = app.resume_report(path, _dependencies=object())
    renderer.assert_called_once_with(state['final_report'], result['pdf'])
    assert result['state'] == state
    assert result['scores']['score_details'] == state['investment_result']['score_details']
    assert result['scores']['configured_models'] == {'openai_model': 'original-model'}
    assert result['scores']['resume']['kind'] == 'pdf_only'
    trace = json.loads(result['trace'].read_text())
    assert trace['trace_id'] == state['trace_id']
    assert trace['events'] == wrapper['events']
    assert trace['resume']['graph_reexecuted'] is False


def test_pdf_only_recovery_rejects_changed_scores_and_contradictory_quality(tmp_path):
    path, _ = report_failure_snapshot(tmp_path)
    wrapper = json.loads(path.read_text())
    wrapper.update(failed_stage='pdf')
    wrapper['state'].update(recheck_count=0, final_report='# 보고서',
                            report_evaluation={'passed': True, 'issues': ['품질 미달']})
    path.write_text(json.dumps(wrapper), encoding='utf-8')
    with pytest.raises(ValueError, match='품질 평가'):
        app.resume_report(path, _dependencies=object())
    wrapper['state']['report_evaluation']['issues'] = []
    wrapper['state']['investment_result']['total_score'] = 99
    path.write_text(json.dumps(wrapper), encoding='utf-8')
    with pytest.raises(ValueError, match='저장 점수'):
        app.resume_report(path, _dependencies=object())


def test_validation_failure_also_records_pdf_only_recovery_snapshot(tmp_path):
    deps = SimpleNamespace(index_info={})
    with patch.object(app, 'build_graph', return_value=FakeGraph()), \
         patch.object(app, 'load_company_research_input', return_value={}), \
         patch.object(app, 'render_report'), \
         patch.object(app, 'validate_pdf', side_effect=ValueError('PDF 검증 실패')):
        with pytest.raises(ValueError, match='PDF 검증 실패'):
            app.run('MOTOMIND', output_dir=tmp_path, _dependencies=deps, _catalog={})
    snapshot = json.loads((tmp_path / 'motomind.failed_state.json').read_text())
    assert snapshot['failed_stage'] == 'pdf'
    assert snapshot['state']['recheck_count'] == 0
    assert snapshot['state']['report_evaluation']['passed'] is True


def test_no_progress_writer_resume_never_spends_remaining_research_budget(tmp_path):
    from agent_pipeline import PipelineDependencies
    path, _ = report_failure_snapshot(tmp_path)
    wrapper = json.loads(path.read_text())
    wrapper['state'].update(recheck_count=1, research_stop_reason='no_progress')
    path.write_text(json.dumps(wrapper), encoding='utf-8')
    calls = []
    def forbidden(*args):
        raise AssertionError('무진전 조사 종료 뒤 복구에서 재조사·재채점 금지')
    def writer(state):
        calls.append('writer')
        assert state['decision_policy']['resume_report_only'] is True
        return {'final_report': '# 보고서 재개'}
    deps = PipelineDependencies(forbidden, forbidden, forbidden, forbidden, writer,
        lambda state: {'passed': False, 'issues': ['추가 조사 필요'], 'action': 'research'})
    with patch.object(app, 'render_report'), patch.object(app, 'validate_pdf', return_value={'pages': 1}):
        result = app.resume_report(path, _dependencies=deps)
    assert calls == ['writer']
    assert result['state']['recheck_count'] == 1
    assert result['state']['status'] == 'quality_failed'


@pytest.mark.parametrize('mode,total,accepted', [('live', None, True), ('demo', 0, True), ('live', 0, False)])
def test_all_unknown_pdf_resume_keeps_unscored_total_and_supports_legacy_demo_only(tmp_path, mode, total, accepted):
    from investment_evaluation import evaluate_investment
    path, _ = report_failure_snapshot(tmp_path)
    wrapper = json.loads(path.read_text())
    state = wrapper['state']
    forbidden = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('전부 U인 PDF 복구에서 모델 호출 금지'))
    # 실제 투자 평가 함수의 전부 U 결과는 total_score=None이다. 등급 0과 구분한다.
    state['investment_result'] = evaluate_investment({'source_checked_claims': [],
        'evidence_status': 'insufficient', 'investment_terms': None}, forbidden)
    assert state['investment_result']['total_score'] is None
    state['investment_result']['total_score'] = total
    state.update(recheck_count=0, status='partial', final_report='# 산정 보류\n모든 항목 U',
                 report_evaluation={'passed': True, 'issues': []})
    if mode == 'demo':
        state['company_info']['name'] = 'Figure AI'
    wrapper.update(failed_stage='pdf', mode=mode)
    path.write_text(json.dumps(wrapper), encoding='utf-8')
    with patch.object(app, 'real_dependencies', side_effect=forbidden), \
         patch.object(app, 'build_graph', side_effect=forbidden), \
         patch.object(app, 'render_report') as renderer, \
         patch.object(app, 'validate_pdf', return_value={'pages': 1}):
        if not accepted:
            with pytest.raises(ValueError, match='저장 점수'):
                app.resume_report(path)
            renderer.assert_not_called()
            return
        result = app.resume_report(path)
    assert result['scores']['total_score'] == total
    assert result['scores']['scorable_weight'] == 0
    assert result['scores']['unknown_weight'] == 100
    assert all(item['grade'] is None for item in result['scores']['score_details'].values())
    renderer.assert_called_once_with(state['final_report'], result['pdf'])
