"""최종 종합·선정·평가가 실제 그래프에 연결되고 분석을 반복하지 않는지 검사한다."""
from copy import deepcopy
import json

import pytest
from langgraph.checkpoint.memory import InMemorySaver

import app
import final_report_pipeline as pipeline
from final_report_pipeline import FinalReportDependencies, build_final_report_graph, initial_final_state
from portfolio_decision import select_portfolio_investment
from report_synthesis import synthesize_company_reports
from test_combined_report import records, portfolio
from test_report_synthesis import inputs_and_synthesis


def dependencies(records, *, evaluate=None, revise=None):
    _, originals, synthesis = inputs_and_synthesis(records)
    calls = {'synthesis': [], 'selection': []}

    def synthesize(companies, **kwargs):
        def writer(payload):
            calls['synthesis'].append(deepcopy(payload))
            return deepcopy(synthesis)
        return synthesize_company_reports(companies, writer=writer, **kwargs)

    def select(company_records, **kwargs):
        def judge(payload):
            calls['selection'].append(deepcopy(payload))
            return portfolio()
        return select_portfolio_investment(company_records, judge=judge, **kwargs)

    deps = FinalReportDependencies(prepare_inputs=app._prepare_combined_inputs,
        synthesize=synthesize, select=select, load_analysis=app._load_combined_analysis,
        evaluate=evaluate or (lambda _: {'passed': True, 'issues': []}), revise=revise)
    return deps, originals, calls


def test_fresh_graph_passes_whole_reports_verified_pages_and_same_synthesis_to_selection(records, tmp_path):
    deps, originals, calls = dependencies(records)
    before = deepcopy(records)
    state = build_final_report_graph(deps).invoke(initial_final_state(records, tmp_path))
    assert records == before
    assert state['node_history'] == ['prepare_final_inputs', 'report_synthesis', 'portfolio_decision',
        'compose_final_report', 'final_quality', 'final_orchestrator', 'publish_final_report']
    assert len(calls['synthesis']) == len(calls['selection']) == 1
    for payload in [calls['synthesis'][0], calls['selection'][0]]:
        assert payload['company_reports'] == originals
        assert len(payload['source_contexts']) == 3
        assert all(page['page_text'] and page['checked_claims'] for page in payload['source_contexts'])
    assert calls['selection'][0]['report_synthesis'] == state['report_synthesis']
    result = state['result']
    saved = json.loads(result['json'].read_text())
    assert result['status'] == 'completed' and saved['graph_execution']['engine'] == 'langgraph'
    assert saved['graph_execution']['node_history'] == state['node_history']
    assert saved['source_score_details'] == {r['state']['company_info']['name']:
        r['state']['investment_result']['score_details'] for r in records}


def test_feedback_returns_only_to_rewrite_and_judge_preserving_worker_inputs(records, tmp_path):
    reviews, revisions = [], []

    def evaluate(payload):
        reviews.append(deepcopy(payload))
        passed = len(reviews) == 2
        return {'passed': passed, 'issues': [] if passed else ['출처 표현 교정'], 'action': 'rewrite'}

    def revise(payload, feedback):
        revisions.append(feedback)
        return payload['report'].replace('개발과 실제 납품의 차이를 구별한다.',
            '현재 자료에서 개발 계획이 확인되며 실제 납품 여부는 후속 확인이 필요하다.')

    deps, _, calls = dependencies(records, evaluate=evaluate, revise=revise)
    state = build_final_report_graph(deps).invoke(initial_final_state(records, tmp_path))
    assert state['result']['status'] == 'completed' and state['rewrite_count'] == 1
    assert state['node_history'].count('final_quality') == 2
    assert state['node_history'].count('final_rewrite') == len(revisions) == 1
    assert len(calls['synthesis']) == len(calls['selection']) == 1
    for field in ('source_contexts', 'company_reports', 'report_synthesis', 'comparison', 'portfolio_decision'):
        assert reviews[0][field] == reviews[1][field]
    assert reviews[0]['report'] != reviews[1]['report']


def test_research_feedback_ends_as_draft_with_request_instead_of_rewriting_same_sources(records, tmp_path):
    deps, _, calls = dependencies(records,
        evaluate=lambda _: {'passed': False, 'issues': ['고객 납품의 추가 원문이 필요하다'], 'action': 'research'},
        revise=lambda *_: pytest.fail('새 근거가 필요한 문제를 재작성하면 안 됨'))
    state = build_final_report_graph(deps).invoke(initial_final_state(records, tmp_path))
    assert state['result']['status'] == 'quality_failed'
    assert state['result']['quality_stop_reason'] == 'new_evidence_required'
    assert state['rewrite_count'] == 0 and len(calls['synthesis']) == 1
    request = json.loads((tmp_path / 'final_report.research_request.json').read_text())
    assert request['action'] == 'research' and len(request['company_trace_ids']) == 3
    assert not (tmp_path / 'final_investment_report.pdf').exists()


def test_invalid_or_unchanged_rewrite_stops_at_budget_without_resampling_judge(records, tmp_path):
    reviews = []
    def evaluate(payload):
        reviews.append(payload['report'])
        return {'passed': False, 'issues': ['표현 교정'], 'action': 'rewrite'}
    deps, _, calls = dependencies(records, evaluate=evaluate, revise=lambda payload, _: payload['report'])
    state = build_final_report_graph(deps).invoke(initial_final_state(records, tmp_path))
    assert state['rewrite_count'] == state['max_rewrites'] == 2
    assert len(reviews) == 1 and len(calls['synthesis']) == len(calls['selection']) == 1
    assert state['result']['quality_stop_reason'] == 'rewrite_budget_exhausted'
    assert state['step_count'] < state['max_steps']


def test_checkpoint_resumes_failed_pdf_node_without_repeating_synthesis_selection_or_judge(records, tmp_path, monkeypatch):
    reviews = []
    deps, _, calls = dependencies(records, evaluate=lambda payload:
        reviews.append(payload['report']) or {'passed': True, 'issues': []})
    graph = build_final_report_graph(deps, checkpointer=InMemorySaver())
    publish = pipeline.publish_combined_report
    def fail_pdf(*args, **kwargs):
        raise RuntimeError('PDF 출력 실패')
    monkeypatch.setattr(pipeline, 'publish_combined_report', fail_pdf)
    config = {'configurable': {'thread_id': 'pdf-resume'}, 'recursion_limit': 40}
    with pytest.raises(RuntimeError, match='PDF 출력 실패'):
        graph.invoke(initial_final_state(records, tmp_path), config)
    assert graph.get_state(config).next == ('publish_final_report',)
    assert (tmp_path / 'combined_analysis.json').exists()
    monkeypatch.setattr(pipeline, 'publish_combined_report', publish)
    resumed = graph.invoke(None, config)
    assert resumed['result']['status'] == 'completed'
    assert len(calls['synthesis']) == len(calls['selection']) == len(reviews) == 1


def test_analysis_reuse_is_graph_branch_and_always_gets_new_quality_review(records, tmp_path):
    deps, _, calls = dependencies(records)
    graph = build_final_report_graph(deps)
    first = graph.invoke(initial_final_state(records, tmp_path / 'first'))
    reviewed = []
    reused_deps = FinalReportDependencies(prepare_inputs=app._prepare_combined_inputs,
        load_analysis=app._load_combined_analysis,
        synthesize=lambda *_a, **_k: pytest.fail('재사용 시 합성 호출 금지'),
        select=lambda *_a, **_k: pytest.fail('재사용 시 선정 호출 금지'),
        evaluate=lambda payload: reviewed.append(payload) or {'passed': True, 'issues': []})
    reused = build_final_report_graph(reused_deps).invoke(initial_final_state(records, tmp_path / 'second',
        analysis_mode='reuse', reuse_analysis_path=tmp_path / 'first/combined_analysis.json'))
    assert reused['result']['analysis_reused'] and len(reviewed) == 1
    assert 'reuse_final_analysis' in reused['node_history'] and 'report_synthesis' not in reused['node_history']
    assert reused['result']['markdown'].read_text() == first['result']['markdown'].read_text()
