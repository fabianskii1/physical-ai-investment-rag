"""기업 산출물 → 종합 → 선정 → 조립 → 품질/수정 → PDF의 실제 StateGraph.

조사·채점은 재실행하지 않는다. LLM 함수는 노드의 의존성이며, 분기·예산과
불변 데이터 검증은 코드가 제어한다. app의 배치와 저장 결과 재실행이 모두 사용한다.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Callable
from uuid import uuid4

from langgraph.graph import START, END, StateGraph

from combined_report import (prepare_combined_report, publish_combined_report,
                             _evaluate_combined_payload, _validate_combined_revision)
from state_schema import FinalReportState


def _write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


@dataclass(frozen=True)
class FinalReportDependencies:
    prepare_inputs: Callable | None = None
    synthesize: Callable | None = None
    select: Callable | None = None
    load_analysis: Callable | None = None
    evaluate: Callable | None = None
    revise: Callable | None = None
    write_json: Callable = _write_json


def initial_final_state(records, output_dir, *, analysis_mode='fresh', reuse_analysis_path=None,
                        max_pages=10, max_rewrites=2, report_synthesis=None, portfolio_decision=None,
                        initial_report=None, input_manifest=None, trace_id=None) -> FinalReportState:
    if type(max_rewrites) is not int or not 0 <= max_rewrites <= 2:
        raise ValueError('통합 보고서 수정 예산은 0~2 정수여야 합니다')
    if type(max_pages) is not int or not 1 <= max_pages <= 10:
        raise ValueError('최종 보고서 페이지 한도는 1~10 정수여야 합니다')
    if analysis_mode not in {'fresh', 'reuse', 'provided'}:
        raise ValueError('알 수 없는 최종 보고서 분석 모드')
    if analysis_mode == 'reuse' and not reuse_analysis_path:
        raise ValueError('분석 재사용 파일이 필요합니다')
    return {'trace_id': trace_id or str(uuid4()), 'records': deepcopy(records),
            'output_dir': str(Path(output_dir).expanduser().resolve()), 'analysis_mode': analysis_mode,
            'reuse_analysis_path': str(reuse_analysis_path) if reuse_analysis_path else None,
            'analysis_reused': analysis_mode == 'reuse', 'max_pages': max_pages,
            'max_rewrites': max_rewrites, 'rewrite_count': 0, 'step_count': 0, 'max_steps': 32,
            'node_history': [], 'rewrite_events': [], 'revision_accepted': False,
            'report_synthesis': deepcopy(report_synthesis), 'portfolio_decision': deepcopy(portfolio_decision),
            'initial_report': initial_report, 'input_manifest': deepcopy(input_manifest)}


def build_final_report_graph(deps: FinalReportDependencies, *, checkpointer=None):
    """품질 미달만 되돌리고 합성·선정·검색·채점을 반복하지 않는 유한 그래프."""
    if deps.revise is not None and not callable(deps.revise):
        raise ValueError('통합 보고서 수정 함수는 호출 가능해야 합니다')

    def prepare_final_inputs(state):
        if state['analysis_mode'] == 'provided':
            return {}
        if deps.prepare_inputs is None:
            raise ValueError('최종 입력 준비 함수가 필요합니다')
        companies, reports, contexts, manifest = deps.prepare_inputs(state['records'], Path(state['output_dir']))
        return {'companies': companies, 'company_reports': reports,
                'source_contexts': contexts, 'input_manifest': manifest}

    def report_synthesis(state):
        if deps.synthesize is None:
            raise ValueError('기업 보고서 종합 함수가 필요합니다')
        synthesis = deps.synthesize(deepcopy(state['companies']),
            source_contexts=deepcopy(state['source_contexts']), report_inputs=deepcopy(state['company_reports']))
        return {'report_synthesis': synthesis}

    def portfolio_decision(state):
        if deps.select is None:
            raise ValueError('최종 투자 선정 함수가 필요합니다')
        decision = deps.select(deepcopy(state['records']), source_contexts=deepcopy(state['source_contexts']),
            report_inputs=deepcopy(state['company_reports']), report_synthesis=deepcopy(state['report_synthesis']))
        return {'portfolio_decision': decision}

    def reuse_final_analysis(state):
        if deps.load_analysis is None:
            raise ValueError('저장 분석 검증 함수가 필요합니다')
        path = Path(state['reuse_analysis_path']).expanduser().resolve()
        synthesis, portfolio = deps.load_analysis(path, state['records'], source_contexts=state['source_contexts'])
        report_path = path.with_suffix('.md')
        initial = None
        if report_path.is_file():
            initial = report_path.read_text(encoding='utf-8')
            cached = json.loads(path.read_text(encoding='utf-8'))
            if cached.get('markdown_sha256') and cached['markdown_sha256'] != sha256(initial.encode()).hexdigest():
                raise ValueError('저장된 최종 Markdown 본문 해시가 일치하지 않습니다')
        return {'report_synthesis': synthesis, 'portfolio_decision': portfolio, 'initial_report': initial}

    def compose_final_report(state):
        built, payload, original = prepare_combined_report(state['records'],
            portfolio_decision=state.get('portfolio_decision'), report_synthesis=state.get('report_synthesis'),
            initial_report=state.get('initial_report'))
        if state['analysis_mode'] != 'provided':
            # 품질·PDF 실패 후 이 파일로 검증된 합성/선정만 복구할 수 있다.
            deps.write_json(Path(state['output_dir']) / 'combined_analysis.json', {
                'status': 'analysis_ready', 'companies': built['comparison'],
                'source_score_details': {item['company']: item['details'] for item in built['companies']},
                'deterministic_checks': built['deterministic_checks'], 'source_contexts': state['source_contexts'],
                'input_manifest': state['input_manifest'], 'company_reports': built['company_report_inputs'],
                'report_synthesis': built['report_synthesis'], 'portfolio_decision': built['portfolio_decision']})
        return {'built': built, 'quality_payload': payload, 'original_report': original}

    def final_quality(state):
        evaluation = _evaluate_combined_payload(deps.evaluate, state['quality_payload'])
        events = deepcopy(state['rewrite_events'])
        if events and events[-1]['accepted'] and 'quality_passed' not in events[-1]:
            events[-1].update(quality_passed=evaluation.get('passed'), action=evaluation.get('action'))
        return {'report_evaluation': evaluation, 'rewrite_events': events}

    def final_orchestrator(state):
        evaluation = state['report_evaluation']
        checks = state['built']['deterministic_checks']
        can_rewrite = (not evaluation.get('passed') and evaluation.get('action') == 'rewrite'
            and deps.evaluate is not None and deps.revise is not None
            and state['rewrite_count'] < state['max_rewrites']
            and checks['source_reports_quality_passed'] and checks['company_reports_synthesized'])
        return {'route': 'rewrite' if can_rewrite else 'publish'}

    def final_rewrite(state):
        attempt = state['rewrite_count'] + 1
        evaluation = deepcopy(state['report_evaluation'])
        try:
            proposal = deps.revise(deepcopy(state['quality_payload']), deepcopy(evaluation))
            if proposal == state['quality_payload']['report']:
                raise ValueError('품질 미달 보고서에 실제 수정이 없어 같은 문서를 다시 판정하지 않습니다')
            revised = _validate_combined_revision(state['original_report'], proposal)
        except Exception as error:
            message = str(error) if isinstance(error, ValueError) else f'보고서 수정 함수 실패: {type(error).__name__}'
            issues = list(dict.fromkeys([*evaluation.get('issues', []), message]))
            event = {'attempt': attempt, 'accepted': False, 'failure_type': type(error).__name__, 'issue': message}
            return {'rewrite_count': attempt, 'revision_accepted': False,
                    'report_evaluation': {**evaluation, 'passed': False, 'action': 'rewrite', 'issues': issues},
                    'rewrite_events': [*state['rewrite_events'], event]}
        payload, built = deepcopy(state['quality_payload']), deepcopy(state['built'])
        payload['report'] = built['markdown'] = revised
        return {'rewrite_count': attempt, 'revision_accepted': True, 'quality_payload': payload, 'built': built,
                'rewrite_events': [*state['rewrite_events'], {'attempt': attempt, 'accepted': True}]}

    def publish_final_report(state):
        execution = {'engine': 'langgraph', 'trace_id': state['trace_id'],
                     'analysis_mode': state['analysis_mode'], 'step_count': state['step_count'] + 1,
                     'max_steps': state['max_steps'], 'node_history': [*state['node_history'], 'publish_final_report']}
        result = publish_combined_report(state['built'], state['quality_payload'], state['report_evaluation'],
            state['output_dir'], max_pages=state['max_pages'], max_rewrites=state['max_rewrites'],
            rewrite_count=state['rewrite_count'], rewrite_events=state['rewrite_events'],
            input_manifest=state.get('input_manifest'), quality_evaluator=deps.evaluate, graph_execution=execution)
        if result['quality_stop_reason'] == 'new_evidence_required':
            deps.write_json(Path(state['output_dir']) / 'final_report.research_request.json', {
                'trace_id': state['trace_id'], 'action': 'research', 'issues': result['report_evaluation']['issues'],
                'company_trace_ids': [r['state']['trace_id'] for r in state['records']],
                'note': '새 원문이 필요한 결함은 동일 입력 재작성으로 우회하지 않습니다. 기업 조사 후 다시 종합합니다.'})
        return {'result': {**result, 'analysis_reused': state['analysis_reused'], 'graph_execution': execution}}

    def bounded(name, node):
        def run_node(state):
            if state['step_count'] >= state['max_steps']:
                raise RuntimeError('최종 보고서 그래프 단계 한도를 초과했습니다')
            return {**node(state), 'step_count': state['step_count'] + 1,
                    'node_history': [*state['node_history'], name]}
        return run_node

    graph = StateGraph(FinalReportState)
    for name, node in (('prepare_final_inputs', prepare_final_inputs), ('report_synthesis', report_synthesis),
                       ('portfolio_decision', portfolio_decision), ('reuse_final_analysis', reuse_final_analysis),
                       ('compose_final_report', compose_final_report), ('final_quality', final_quality),
                       ('final_orchestrator', final_orchestrator), ('final_rewrite', final_rewrite),
                       ('publish_final_report', publish_final_report)):
        graph.add_node(name, bounded(name, node))
    graph.add_edge(START, 'prepare_final_inputs')
    graph.add_conditional_edges('prepare_final_inputs', lambda s: s['analysis_mode'],
        {'fresh': 'report_synthesis', 'reuse': 'reuse_final_analysis', 'provided': 'compose_final_report'})
    graph.add_edge('report_synthesis', 'portfolio_decision')
    graph.add_edge('portfolio_decision', 'compose_final_report')
    graph.add_edge('reuse_final_analysis', 'compose_final_report')
    graph.add_edge('compose_final_report', 'final_quality')
    graph.add_edge('final_quality', 'final_orchestrator')
    graph.add_conditional_edges('final_orchestrator', lambda s: s['route'],
                                {'rewrite': 'final_rewrite', 'publish': 'publish_final_report'})
    graph.add_conditional_edges('final_rewrite', lambda s: s['revision_accepted'],
                                {True: 'final_quality', False: 'final_orchestrator'})
    graph.add_edge('publish_final_report', END)
    return graph.compile(checkpointer=checkpointer)
