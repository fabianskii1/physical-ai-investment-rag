"""설정 검사는 오프라인으로 수행하고 callback 관측값의 범위를 검증한다."""
from unittest.mock import patch
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langsmith import tracing_context

import app
from agent_pipeline import PipelineDependencies, build_graph, initial_state


@pytest.fixture(autouse=True)
def clean_tracing_environment(monkeypatch):
    for name in ('LANGSMITH_TRACING', 'LANGSMITH_TRACING_V2', 'LANGCHAIN_TRACING',
                 'LANGCHAIN_TRACING_V2', 'LANGSMITH_API_KEY', 'LANGCHAIN_API_KEY',
                 'LANGSMITH_PROJECT', 'LANGCHAIN_PROJECT'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('LANGSMITH_TRACING_V2', 'false')


def dependencies():
    return PipelineDependencies(
        planner=lambda state: [{'id': role, 'worker': role, 'query': role}
                               for role in ('bigtech', 'technology', 'market')],
        worker=lambda task, state: {'claims': []},
        evidence_checker=lambda state: {'source_checked_claims': [], 'evidence_gaps': [],
                                       'evidence_status': 'scorable'},
        judge=lambda state: {}, writer=lambda state: {'final_report': '# 오프라인 보고서'},
        report_evaluator=lambda state: {'passed': True, 'issues': []})


def test_requested_tracing_with_empty_key_is_disabled_and_does_not_create_remote_runs(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv('LANGSMITH_TRACING_V2', 'true')
    monkeypatch.setenv('LANGSMITH_API_KEY', '  ')
    with patch.object(app, 'load_dotenv'), \
         patch('langsmith.client.Client.create_run') as remote, \
         patch.object(app, 'render_report'), \
         patch.object(app, 'validate_pdf', return_value={'pages': 1}):
        result = app.run(demo=True, output_dir=tmp_path, _catalog={}, _dependencies=dependencies())
    assert remote.call_count == 0
    assert result['scores']['tracing']['enabled'] is False
    assert result['scores']['tracing']['root_run_id'] is None
    assert '키가 비어' in capsys.readouterr().out
    assert app.tracing_settings()['requested'] is True  # 실행 중 환경 설정을 덮어쓰지 않는다.


def test_check_tracing_never_calls_api_or_exposes_key(monkeypatch, tmp_path, capsys):
    token = 'test-secret-that-must-not-appear'
    env_file = tmp_path / 'trace.env'
    env_file.write_text(f'LANGSMITH_TRACING_V2=true\nLANGSMITH_API_KEY={token}\nLANGSMITH_PROJECT=team-project\n')
    monkeypatch.delenv('LANGSMITH_TRACING_V2')
    monkeypatch.setattr('sys.argv', ['app.py', '--check-tracing', '--env-file', str(env_file)])
    forbidden = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('설정 검사에서 API 호출 금지'))
    with patch.object(app, 'real_dependencies', side_effect=forbidden), \
         patch.object(app, 'build_graph', side_effect=forbidden), \
         patch('langsmith.client.Client.create_run', side_effect=forbidden):
        app.main()
    output = capsys.readouterr().out
    assert 'team-project' in output and '입력됨' in output and '활성화 가능' in output
    assert token not in output
    assert token not in str(app.tracing_settings())


def test_actual_graph_callbacks_record_parallel_node_durations_and_requested_root_run_id():
    run_id = uuid4()
    metrics = app.ExecutionMetrics(run_id)
    graph = build_graph(dependencies())
    with tracing_context(enabled=False):
        graph.invoke(initial_state({'name': 'Figure AI'}, {}, {}),
                     {'run_id': run_id, 'callbacks': [metrics], 'recursion_limit': 100})
    captured = metrics.snapshot(1.25)
    names = [item['node'] for item in captured['nodes']]
    assert metrics.root_run_id == str(run_id)
    assert names.count('worker') == 3
    assert set(names) == {'orchestrator', 'worker', 'synthesizer', 'judgement',
                          'score_review', 'writer', 'report_evaluator'}
    assert all(item['elapsed_seconds'] >= 0 and item['status'] == 'completed' for item in captured['nodes'])
    assert len({item['run_id'] for item in captured['nodes']}) == len(captured['nodes'])
    assert captured['total_token_usage'] is None
    assert captured['total_cost'] is None


def test_only_observed_langchain_token_usage_is_reported():
    metrics = app.ExecutionMetrics(uuid4())
    message = AIMessage(content='offline', usage_metadata={'input_tokens': 5, 'output_tokens': 3, 'total_tokens': 8},
                        response_metadata={'model_name': 'test-model'})
    metrics.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]))
    captured = metrics.snapshot(0)
    assert captured['observed_model_usage']['test-model']['total_tokens'] == 8
    assert 'direct OpenAI Responses calls are excluded' in captured['token_usage_scope']
    assert captured['total_token_usage'] is None


def test_failed_node_metrics_record_type_without_raw_error_content():
    metrics = app.ExecutionMetrics(uuid4())
    node_id = uuid4()
    metrics.on_chain_start(None, {'recheck_count': 1}, run_id=node_id,
                           metadata={'langgraph_node': 'writer'}, name='writer')
    metrics.on_chain_error(ValueError('do-not-record-this-sensitive-body'), run_id=node_id)
    captured = metrics.snapshot(0)
    assert captured['nodes'][0]['status'] == 'failed'
    assert captured['nodes'][0]['error_type'] == 'ValueError'
    assert 'sensitive-body' not in str(captured)
