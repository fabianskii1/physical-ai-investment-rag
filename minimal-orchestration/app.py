"""기업별 독립 그래프를 실행하고 점수·보고서·기업 비교표를 저장한다."""
import argparse
import csv
import json
import logging
import os
import re
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from threading import Lock
from time import perf_counter
from uuid import uuid4
from dotenv import load_dotenv
from langchain_core.callbacks import UsageMetadataCallbackHandler
from langgraph.checkpoint.memory import InMemorySaver
from langsmith import tracing_context, traceable, get_current_run_tree
from agent_pipeline import build_graph, initial_state, _verification_signature, flat_payload
from legacy_adapter import demo_dependencies, load_project_catalog, real_dependencies
from rag.scorecard import SCORECARD, calculate_scorecard
from report_renderer import render_report, validate_pdf
from research_inputs import COMPANY_FILES, load_company_research_input
from evaluation_reference import verify_scorecard_reference

ROOT = Path(__file__).resolve().parent
ORIGINAL_PROJECT = Path(os.getenv('ORIGINAL_RAG_PROJECT', str(ROOT / 'rag'))).expanduser()
COMPANIES = tuple(COMPANY_FILES)


def tracing_settings() -> dict:
    """외부 요청 없이 설정 상태만 확인한다. 키 값은 반환하거나 출력하지 않는다."""
    flag = next((os.environ[name] for name in ('LANGSMITH_TRACING_V2', 'LANGCHAIN_TRACING_V2',
                 'LANGSMITH_TRACING', 'LANGCHAIN_TRACING') if os.environ.get(name, '').strip()), 'false')
    requested = flag.strip().lower() in {'true', '1', 'yes'}
    key_present = any(os.getenv(name, '').strip() for name in ('LANGSMITH_API_KEY', 'LANGCHAIN_API_KEY'))
    return {'requested': requested, 'key_present': key_present,
            'enabled': requested and key_present,
            'project': os.getenv('LANGSMITH_PROJECT') or os.getenv('LANGCHAIN_PROJECT') or 'default'}


def _tracing_notice(settings: dict) -> None:
    if settings['requested'] and not settings['key_present']:
        print('LangSmith 키가 비어 있어 이번 실행의 원격 추적을 끕니다. 로컬 trace JSON은 저장합니다.', flush=True)


class ExecutionMetrics(UsageMetadataCallbackHandler):
    """실제 노드 callback의 시간과 LangChain이 보고한 사용량만 기록한다."""

    def __init__(self, root_run_id):
        super().__init__()
        self.expected_root_run_id = str(root_run_id)
        self.root_run_id = None
        self._node_lock = Lock()
        self._running = {}
        self.nodes = []

    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None,
                       metadata=None, **kwargs):
        metadata = metadata or {}
        node = metadata.get('langgraph_node')
        with self._node_lock:
            if parent_run_id is None and str(run_id) == self.expected_root_run_id:
                self.root_run_id = str(run_id)
            # 내부 Runnable/분기 callback은 같은 metadata를 물려받으므로 제외한다.
            if node and kwargs.get('name') == node:
                state = inputs.get('snapshot', inputs) if isinstance(inputs, dict) else {}
                self._running[str(run_id)] = (perf_counter(), {
                    'node': node, 'run_id': str(run_id),
                    'round': state.get('recheck_count') if isinstance(state, dict) else None})

    def _finish_node(self, run_id, error=None):
        with self._node_lock:
            started = self._running.pop(str(run_id), None)
            if started is not None:
                start, record = started
                self.nodes.append({**record, 'elapsed_seconds': round(perf_counter() - start, 6),
                                   'status': 'failed' if error is not None else 'completed',
                                   **({'error_type': type(error).__name__} if error is not None else {})})

    def on_chain_end(self, outputs, *, run_id, **kwargs):
        self._finish_node(run_id)

    def on_chain_error(self, error, *, run_id, **kwargs):
        self._finish_node(run_id, error)

    def snapshot(self, graph_elapsed_seconds) -> dict:
        return {'graph_elapsed_seconds': round(graph_elapsed_seconds, 6),
                'nodes': list(self.nodes), 'observed_model_usage': deepcopy(self.usage_metadata),
                'token_usage_scope': 'LangChain AIMessage usage callbacks only; direct OpenAI Responses calls are excluded',
                'total_token_usage': None, 'total_cost': None}


def _trace_metadata(settings: dict, metrics: ExecutionMetrics) -> dict:
    return {**settings, 'root_run_id': metrics.root_run_id if settings['enabled'] else None,
            'upload_verified': False}


def input_directories(data_dir=None, com_data_dir=None, index_dir=None) -> dict:
    """명시 경로 우선, 원본 준비 자료가 있으면 사용하고 복사본으로 대체한다."""
    data = Path(data_dir).expanduser() if data_dir else (
        ORIGINAL_PROJECT / 'data' if (ORIGINAL_PROJECT / 'data/catalog.csv').is_file() else ROOT / 'rag/data')
    prepared = Path(com_data_dir).expanduser() if com_data_dir else (
        ORIGINAL_PROJECT / 'com_data' if (ORIGINAL_PROJECT / 'com_data').is_dir() else ROOT / 'com_data')
    index = Path(index_dir or os.getenv('RAG_INDEX_DIR') or ROOT / '.cache/rag_index').expanduser()
    return {'data_dir': str(data.resolve()), 'com_data_dir': str(prepared.resolve()), 'index_dir': str(index.resolve())}


def _write_json(path, value):
    text = json.dumps(value, ensure_ascii=False, indent=2)
    # 예외 문자열에 자격증명이 섞이더라도 로컬 추적 파일에 기록하지 않는다.
    for name in ('OPENAI_API_KEY', 'LANGSMITH_API_KEY', 'LANGCHAIN_API_KEY', 'HF_TOKEN'):
        secret = os.getenv(name)
        if secret:
            text = text.replace(secret, '[REDACTED]')
    # task-technology 같은 작업 ID 안의 'sk-'는 API 토큰 시작이 아니다.
    text = re.sub(r'(?<![A-Za-z0-9_-])sk[-_][A-Za-z0-9_-]{10,}', '[REDACTED]', text)
    path.write_text(text, encoding='utf-8')


def score_snapshot(final: dict, *, mode: str, inputs: dict, index_info: dict, report_path: Path,
                   criteria_reference: dict | None = None) -> dict:
    """점수와 확인 배점을 함께 보존해 U가 낮은 등급으로 오해되지 않게 한다."""
    investment = final.get('investment_result', {})
    details = investment.get('score_details', {})
    categories = {}
    for category, criteria in SCORECARD.items():
        known_weight = sum(weight for name, weight in criteria.items()
                           if details.get(name, {}).get('grade') is not None)
        categories[category] = {'weight': sum(criteria.values()),
            'earned_score': sum(details.get(name, {}).get('score') or 0 for name in criteria) if known_weight else None,
            'scorable_weight': known_weight,
            'unknown_items': [name for name in criteria if details.get(name, {}).get('grade') is None]}
    research = final.get('research_result', {})
    return {'company': final['company_info']['name'], 'mode': mode, 'status': final['status'],
        'total_score': investment.get('total_score'), 'score_scale': 100,
        'scorable_weight': investment.get('scorable_weight', 0), 'unknown_weight': investment.get('unknown_weight', 100),
        'coverage': {'research_criteria': research.get('research_criteria_count'),
                     'attempted_criteria': research.get('attempted_criteria_count'),
                     'supported_criteria': research.get('supported_criteria_count')},
        'investment_decision': investment.get('investment_decision', '근거 부족'),
        'decision_reason': investment.get('decision_reason', ''), 'investment_case': investment.get('investment_case', {}),
        'decision_policy': final.get('decision_policy', {}),
        'configured_models': {'openai_model': os.getenv('OPENAI_MODEL', 'gpt-4.1-mini'),
                              'judge_model': os.getenv('OPENAI_JUDGE_MODEL') or os.getenv('OPENAI_MODEL', 'gpt-4.1-mini'),
                              'evidence_model': os.getenv('OPENAI_EVIDENCE_MODEL') or os.getenv('OPENAI_MODEL', 'gpt-4.1-mini'),
                              'grader_model': os.getenv('OPENAI_GRADER_MODEL') or os.getenv('OPENAI_MODEL', 'gpt-4.1-mini'),
                              'writer_model': os.getenv('OPENAI_WRITER_MODEL') or os.getenv('OPENAI_MODEL', 'gpt-4.1-mini'),
                              'portfolio_model': os.getenv('OPENAI_PORTFOLIO_MODEL') or os.getenv('OPENAI_MODEL', 'gpt-4.1-mini')},
        'category_scores': categories, 'score_details': details,
        'report_evaluation': final.get('report_evaluation', {}), 'trace_id': final['trace_id'],
        'recheck_count': final['recheck_count'], 'input_paths': inputs, 'index_info': index_info,
        'report_path': str(report_path), 'evaluation_criteria': criteria_reference or verify_scorecard_reference(),
        'selection_note': '원 배점 점수와 U·근거 비중을 함께 검토; 자동 순위·투자 선택 아님'}


def _save_report_artifacts(final, directory, *, mode, inputs, index_info, events,
                           criteria_reference, provenance=None, tracing=None, metrics=None,
                           configured_models=None):
    """검증된 Markdown을 렌더링하고 같은 조사·판정으로 출력 파일을 완성한다."""
    if not final.get('final_report'):
        raise RuntimeError('그래프가 보고서를 생성하지 못했습니다')
    directory.mkdir(parents=True, exist_ok=True)
    stem = final['company_info']['name'].lower().replace(' ', '_') + ('_demo' if mode == 'demo' else '')
    if final['status'] == 'quality_failed':
        stem += '_draft'
    markdown_path, pdf_path = directory / f'{stem}.md', directory / f'{stem}.pdf'
    markdown_path.write_text(final['final_report'], encoding='utf-8')
    try:
        render_report(final['final_report'], pdf_path)
        checks = validate_pdf(pdf_path)
    except Exception as error:
        _write_json(directory / f'{stem}.failed_state.json', {
            'error_type': type(error).__name__, 'error_message': str(error), 'failed_stage': 'pdf',
            'state': final, 'events': events, 'input_paths': inputs, 'mode': mode,
            'index_info': index_info, 'resume': provenance, 'tracing': tracing, 'metrics': metrics,
            'configured_models': (configured_models if configured_models is not None else
                                  {'openai_model': os.getenv('OPENAI_MODEL', 'gpt-4.1-mini')})})
        raise
    trace_path = directory / f'{stem}.trace.json'
    _write_json(trace_path, {'trace_id': final['trace_id'], 'mode': mode,
        'status': final['status'], 'recheck_count': final['recheck_count'], 'steps': final['step_count'],
        'input_paths': inputs, 'index_info': index_info, 'pdf_checks': checks, 'events': events,
        'resume': provenance, 'tracing': tracing, 'metrics': metrics})
    scores = score_snapshot(final, mode=mode, inputs=inputs, index_info=index_info,
                            report_path=pdf_path, criteria_reference=criteria_reference)
    if configured_models is not None:
        scores['configured_models'] = configured_models
    if provenance:
        scores['resume'] = provenance
    scores['tracing'] = tracing
    scores['metrics'] = metrics
    scores_path = directory / f'{stem}.scores.json'
    _write_json(scores_path, scores)
    state_path = directory / f'{stem}.state.json'
    _write_json(state_path, {'state': final, 'mode': mode, 'scores_path': str(scores_path),
                            'report_path': str(pdf_path), 'tracing': tracing})
    return {'state': final, 'markdown': markdown_path, 'pdf': pdf_path, 'trace': trace_path,
            'scores': scores, 'scores_path': scores_path, 'state_path': state_path, 'pdf_checks': checks}


def run(company: str = 'Figure AI', *, demo: bool = False, max_rechecks: int = 4,
        output_dir: str | Path | None = None, env_file: str | None = None,
        com_data_dir: str | Path | None = None, data_dir: str | Path | None = None,
        index_dir: str | Path | None = None, _dependencies=None, _catalog=None,
        _resume_state=None, _resume_path=None, _resume_metadata=None) -> dict:
    if company not in COMPANIES:
        raise ValueError(f'지원하지 않는 기업: {company}')
    if demo and company != 'Figure AI':
        raise ValueError('오프라인 시연 자료는 Figure AI입니다. 실제 분석은 --demo 없이 실행하세요')
    criteria_reference = verify_scorecard_reference()
    load_dotenv(env_file or ROOT / '.env')
    trace_settings = tracing_settings()
    _tracing_notice(trace_settings)
    inputs = input_directories(data_dir, com_data_dir, index_dir)
    if _resume_state is not None and demo:
        raise ValueError('보고서 복구는 실제 분석의 실패 상태만 지원합니다')
    catalog = (_resume_state['document_catalog'] if _resume_state is not None else
               (_catalog if _catalog is not None else load_project_catalog(inputs['data_dir'])))
    directory = (Path(output_dir).expanduser() if output_dir is not None else ROOT / 'output').resolve()
    if _dependencies is not None:
        deps = _dependencies
    elif demo:
        deps = demo_dependencies(catalog)
    else:
        dependency_options = {'data_dir': inputs['data_dir'], 'index_dir': inputs['index_dir']}
        if _resume_state is not None:
            dependency_options['prepare_retrieval'] = False
        deps = real_dependencies(inputs['com_data_dir'], directory / 'web_sources', **dependency_options)
    graph = build_graph(deps, checkpointer=InMemorySaver())
    provenance = None
    if _resume_state is not None:
        state = deepcopy(_resume_state)
        provenance = {'snapshot_path': str(_resume_path) if _resume_path else None,
                      'resumed_from_trace_id': state['trace_id']}
        state.update(trace_id=str(uuid4()), route='write', report_evaluation={})
        state['decision_policy'] = {**state['decision_policy'], 'resume_report_only': True}
        # 종료된 조사 작업은 이력으로만 보존한다. 보고서 피드백이 S로 가더라도
        # 과거 워커 결과를 새 검증 snapshot에 다시 합류시키지 않는다.
        state['tasks'] = []
        # 자체 실패 기록의 검증 근거를 그대로 복구한다. 새 자료가 없는 보고서
        # 품질 반환에서 기존 원문 Judge를 다시 호출해 점수의 E-ID를 바꾸지 않는다.
        state['research_result']['last_verified_signature'] = _verification_signature(
            state, state['research_result'], state['document_catalog'])
        if state['status'] == 'quality_failed':
            state['status'] = 'partial'
    else:
        company_info = {'name': company, 'domain': '제조·물류'}
        if not demo:
            company_info['research_input'] = load_company_research_input(inputs['com_data_dir'], company)
        policy = {'method': 'fixed_scorecard', 'required_items': ['핵심 작업 성능'],
                  'decision_basis': '검증 사실·미확인 항목·위험의 종합 검토', 'require_criteria_coverage': not demo}
        state = initial_state(company_info, catalog, policy, max_rechecks)
    events = []
    root_run_id = uuid4()
    metrics = ExecutionMetrics(root_run_id)
    config = {'recursion_limit': 100, 'configurable': {'thread_id': state['trace_id']},
              'run_id': root_run_id, 'run_name': f'physical-ai-investment: {company}',
              'metadata': {'trace_id': state['trace_id'], 'company': company, 'mode': 'demo' if demo else 'live'},
              'callbacks': [metrics]}
    if _resume_state is not None:
        # Synthesizer의 write 분기 직후부터 시작한다. 기존 점수·근거·검토 예산은
        # 초기화하지 않으며 보고서 미달은 기존 Evaluator→Synthesizer 경로로 간다.
        # 반환 config의 checkpoint_id는 복구 직전 snapshot을 고정하므로 사용하지
        # 않는다. thread_id로 최신 checkpoint를 읽어 새 Writer 결과를 얻는다.
        with tracing_context(enabled=trace_settings['enabled'], project_name=trace_settings['project']):
            graph.update_state(config, state, as_node='synthesizer')
    index_info = dict(getattr(deps, 'index_info', None) or
                      (_resume_metadata or {}).get('index_info') or
                      {'initialized': False, 'mode': 'resume_report' if provenance else ('demo' if demo else 'live')})
    graph_started = perf_counter()
    try:
        with tracing_context(enabled=trace_settings['enabled'], project_name=trace_settings['project']):
            for update in graph.stream(None if provenance else state, config, stream_mode='updates'):
                events.append(update)
                print(' → '.join(update.keys()), flush=True)
    except Exception as error:
        # 보고서 노드가 실패해도 이미 확보한 근거·점수를 잃지 않도록 기록한다.
        # 실패 기록은 최종 보고서로 취급하지 않고 오류 재현·복구 입력으로 남긴다.
        directory.mkdir(parents=True, exist_ok=True)
        failed = graph.get_state(config).values
        stem = company.lower().replace(' ', '_') + ('_demo' if demo else '')
        _write_json(directory / f'{stem}.failed_state.json', {
            'error_type': type(error).__name__, 'error_message': str(error),
            'state': failed, 'events': events, 'input_paths': inputs,
            'index_info': index_info, 'resume': provenance,
            'tracing': _trace_metadata(trace_settings, metrics),
            'metrics': metrics.snapshot(perf_counter() - graph_started)})
        raise
    final = graph.get_state(config).values
    return _save_report_artifacts(final, directory, mode='demo' if demo else 'live', inputs=inputs,
        index_info=index_info, events=events, criteria_reference=criteria_reference, provenance=provenance,
        tracing=_trace_metadata(trace_settings, metrics), metrics=metrics.snapshot(perf_counter() - graph_started))


def _validate_report_resume(state, *, pdf_only=False, mode='live'):
    """보고서 재개가 조사·채점 재실행으로 확장되지 않도록 저장 상태를 검사한다."""
    required = {'company_info', 'document_catalog', 'decision_policy', 'investment_terms', 'tasks',
                'worker_results', 'research_result', 'investment_result', 'feedback', 'recheck_count',
                'max_rechecks', 'final_report', 'report_evaluation', 'trace_id', 'step_count', 'status', 'route'}
    if not isinstance(state, dict) or required - state.keys():
        raise ValueError('실패 파일에 완전한 에이전트 State가 없습니다')
    if not isinstance(state['company_info'], dict) or state['company_info'].get('name') not in COMPANIES:
        raise ValueError('실패 상태의 기업명이 올바르지 않습니다')
    maximum, count = state['max_rechecks'], state['recheck_count']
    valid_budget = type(maximum) is int and maximum in (3, 4, 5) and type(count) is int and 0 <= count <= maximum
    if not valid_budget or (not pdf_only and count != maximum and state.get('research_stop_reason') != 'no_progress'):
        raise ValueError('보고서 복구는 최종 조사·투자판정이 끝나고 추가 검토 예산이 소진된 상태만 지원합니다')
    if (not isinstance(state['document_catalog'], dict) or not isinstance(state['research_result'], dict)
            or not isinstance(state['research_result'].get('source_checked_claims'), list)):
        raise ValueError('저장된 검증 근거와 문서 카탈로그가 필요합니다')
    criteria = {name for group in SCORECARD.values() for name in group}
    investment = state.get('investment_result')
    details = investment.get('score_details') if isinstance(investment, dict) else None
    if not isinstance(details, dict) or set(details) != criteria or any(not isinstance(item, dict) for item in details.values()):
        raise ValueError('기존 21항목 투자판정이 모두 저장된 상태만 복구할 수 있습니다')
    if pdf_only:
        evaluation = state.get('report_evaluation')
        if (state.get('status') not in {'completed', 'partial'}
                or not isinstance(evaluation, dict) or evaluation.get('passed') is not True
                or evaluation.get('issues', [])
                or not isinstance(state.get('final_report'), str) or not state['final_report'].strip()):
            raise ValueError('PDF만 복구하려면 품질 평가를 통과한 Markdown이 저장되어 있어야 합니다')
        calculated = calculate_scorecard({name: item.get('grade') for name, item in details.items()})
        # 전부 U이면 계산기의 합계 0.0과 달리 투자 결과의 총점은 산정 보류(None)다.
        # 예전 오프라인 demo의 0 합계만 호환하며 live 결과의 U를 0점으로 인정하지 않는다.
        expected_total = calculated['earned_score'] if calculated['scorable_weight'] else None
        legacy_demo_zero = mode == 'demo' and not calculated['scorable_weight'] and investment.get('total_score') == 0
        if investment.get('total_score') != expected_total and not legacy_demo_zero:
            raise ValueError('PDF 복구 상태의 저장 점수와 21항목 등급이 일치하지 않습니다')
        for key, calculated_key in (('scorable_weight', 'scorable_weight'), ('unknown_weight', 'unknown_weight')):
            if investment.get(key) != calculated[calculated_key]:
                raise ValueError('PDF 복구 상태의 저장 점수와 21항목 등급이 일치하지 않습니다')
        if any(details[name].get('score') != item['score'] for name, item in calculated['items'].items()):
            raise ValueError('PDF 복구 상태의 항목별 점수가 등급과 일치하지 않습니다')


def _resume_pdf(wrapper, path, *, output_dir=None, env_file=None) -> dict:
    """품질 통과 뒤 PDF 출력만 실패했다면 모델·검색·그래프 없이 다시 렌더링한다."""
    state = deepcopy(wrapper['state'])
    _validate_report_resume(state, pdf_only=True, mode=wrapper.get('mode', 'live'))
    load_dotenv(env_file or ROOT / '.env')
    directory = Path(output_dir).expanduser().resolve() if output_dir is not None else path.parent
    provenance = {'snapshot_path': str(path), 'resumed_from_trace_id': state['trace_id'],
                  'kind': 'pdf_only', 'graph_reexecuted': False}
    return _save_report_artifacts(state, directory, mode=wrapper.get('mode', 'live'),
        inputs=wrapper.get('input_paths', {}), index_info=wrapper.get('index_info', {}),
        events=wrapper.get('events', []), criteria_reference=verify_scorecard_reference(), provenance=provenance,
        tracing=wrapper.get('tracing'), metrics=wrapper.get('metrics'),
        configured_models=wrapper.get('configured_models', {}))


def resume_report(snapshot_path, *, output_dir=None, env_file=None, com_data_dir=None,
                  data_dir=None, index_dir=None, _dependencies=None) -> dict:
    """자체 실패 JSON에서 PDF 출력 또는 Writer→Evaluator 단계만 재개한다."""
    path = Path(snapshot_path).expanduser().resolve()
    wrapper = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(wrapper, dict) or 'error_type' not in wrapper:
        raise ValueError('app.py가 만든 failed_state.json 파일을 지정하세요')
    if wrapper.get('failed_stage') == 'pdf':
        state = wrapper.get('state')
        # 품질 미달 초안은 기존 Writer→Evaluator 복구로 보내며 통과 상태로 승격하지 않는다.
        if isinstance(state, dict) and isinstance(state.get('report_evaluation'), dict) and state['report_evaluation'].get('passed') is True:
            return _resume_pdf(wrapper, path, output_dir=output_dir, env_file=env_file)
    state = wrapper.get('state')
    _validate_report_resume(state)
    prior = wrapper.get('input_paths', {})
    if not isinstance(prior, dict):
        prior = {}
    return run(state['company_info']['name'], output_dir=output_dir or path.parent, env_file=env_file,
               data_dir=data_dir or prior.get('data_dir'), com_data_dir=com_data_dir or prior.get('com_data_dir'),
               index_dir=index_dir or prior.get('index_dir'), _dependencies=_dependencies,
               _resume_state=state, _resume_path=path, _resume_metadata=wrapper)


def _comparison_artifacts(directory: Path, results: dict, errors: dict) -> dict:
    """입력 순서로 나열하고 근거 비중을 함께 표시한다. 자동 순위를 매기지 않는다."""
    rows = []
    for company in COMPANIES:
        if company in results:
            scores = results[company]['scores']
            row = {key: scores.get(key) for key in ('company', 'total_score', 'scorable_weight', 'unknown_weight',
                    'investment_decision', 'status', 'report_path')}
            row.update(coverage=scores['coverage'], category_scores=scores['category_scores'], error=None)
        else:
            row = dict(company=company, total_score=None, scorable_weight=None, unknown_weight=None,
                       investment_decision=None, status='failed', report_path=None, coverage={}, category_scores={}, error=errors[company])
        rows.append(row)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / 'company_comparison.json'
    _write_json(json_path, {'mode': 'live', 'companies': rows,
        'selection_note': '순위가 아닙니다. U를 0등급으로 바꾸거나 확인 배점만으로 100점 환산하지 않습니다.'})
    csv_path = directory / 'company_comparison.csv'
    fields = ['company', 'total_score', 'scorable_weight', 'unknown_weight', 'investment_decision', 'status', *SCORECARD, 'error', 'report_path']
    with csv_path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({**{key: row.get(key) for key in fields if key not in SCORECARD},
                             **{category: ('—' if row['status'] == 'failed' else
                                 ('U' if row['category_scores'].get(category, {}).get('earned_score') is None else
                                  row['category_scores'][category]['earned_score'])) for category in SCORECARD}})
    lines = ['# 기업별 투자 검토 점수', '', '동일한 6영역·21항목·100점 원 배점입니다. U는 미확인이며 0등급과 다릅니다.',
             '점수·확인 배점·품질 상태·사유를 함께 검토해 사람이 선택합니다. 자동 순위를 만들지 않습니다.', '',
             '| 기업 | 원 배점 점수 / 100 | 확인 배점 | U 배점 | 의견 | 상태 |', '|---|---:|---:|---:|---|---|']
    for row in rows:
        score = '—' if row['total_score'] is None else f"{row['total_score']:.2f}"
        checked = row['scorable_weight'] if row['scorable_weight'] is not None else '—'
        unknown = row['unknown_weight'] if row['unknown_weight'] is not None else '—'
        lines.append(f"| {row['company']} | {score} | {checked} | {unknown} | {row['investment_decision'] or '—'} | {row['status']} |")
    for company, result in results.items():
        scores = result['scores']
        lines.extend(['', f'## {company}', '', scores['decision_reason'], '',
                      f"보고서: [{result['pdf'].name}]({result['pdf']})", '',
                      '| 영역 | 획득 점수 | 원 배점 | 확인 배점 |', '|---|---:|---:|---:|'])
        for category, item in scores['category_scores'].items():
            earned = 'U' if item['earned_score'] is None else f"{item['earned_score']:.2f}"
            lines.append(f"| {category} | {earned} | {item['weight']} | {item['scorable_weight']} |")
        coverage = scores['coverage']
        lines.extend(['', f"자동 조사 항목: {coverage.get('research_criteria')} / 시도 {coverage.get('attempted_criteria')} / 지원 근거 확보 {coverage.get('supported_criteria')}", '',
                      f"보고서 내용 평가: {'통과' if scores['report_evaluation'].get('passed') else '미달'}"])
    if errors:
        lines.extend(['', '## 실행 실패', '', *[f'- {company}: {error}' for company, error in errors.items()]])
    markdown_path = directory / 'company_comparison.md'
    markdown_path.write_text('\n'.join(lines), encoding='utf-8')
    return {'json': json_path, 'csv': csv_path, 'markdown': markdown_path}


def refresh_comparison(output_dir: str | Path) -> dict:
    """기존 live 점수·PDF만 검사해 최신 기업별 결과의 비교표를 다시 만든다."""
    directory = Path(output_dir).expanduser().resolve()
    results, errors = {}, {}
    criteria = {name for group in SCORECARD.values() for name in group}
    for company in COMPANIES:
        try:
            stem = company.lower().replace(' ', '_')
            candidates = [path for path in (directory / f'{stem}.scores.json',
                                           directory / f'{stem}_draft.scores.json') if path.is_file()]
            if not candidates:
                raise FileNotFoundError('기업별 점수 파일이 없습니다')
            # 복구된 최종 파일과 초안 중 가장 최근의 결과를 사용한다. 같은 시각이면 최종 우선.
            score_path = max(candidates, key=lambda path: (
                path.stat().st_mtime_ns, path.name == f'{stem}.scores.json'))
            scores = json.loads(score_path.read_text(encoding='utf-8'))
            if (not isinstance(scores, dict) or scores.get('company') != company
                    or scores.get('mode') != 'live' or scores.get('score_scale') != 100):
                raise ValueError('점수 파일의 기업·실행 모드·배점 정보가 일치하지 않습니다')
            status = scores.get('status')
            evaluation = scores.get('report_evaluation')
            draft = score_path.name == f'{stem}_draft.scores.json'
            if (status not in {'completed', 'partial', 'quality_failed'}
                    or draft != (status == 'quality_failed')
                    or not isinstance(evaluation, dict)
                    or type(evaluation.get('passed')) is not bool
                    or evaluation['passed'] != (status != 'quality_failed')):
                raise ValueError('보고서 파일의 최종·초안 상태와 품질 판정이 일치하지 않습니다')
            details = scores.get('score_details')
            if (not isinstance(details, dict) or set(details) != criteria
                    or any(not isinstance(item, dict) or 'grade' not in item for item in details.values())):
                raise ValueError('21개 평가항목의 점수 정보가 필요합니다')
            scored = calculate_scorecard({name: details[name]['grade'] for name in criteria})
            for name, item in scored['items'].items():
                if any(details[name].get(key) != value for key, value in item.items()):
                    raise ValueError('항목별 배점·등급·점수 정보가 일치하지 않습니다')
            expected_total = scored['earned_score'] if scored['scorable_weight'] else None
            if (scores.get('total_score') != expected_total
                    or scores.get('scorable_weight') != scored['scorable_weight']
                    or scores.get('unknown_weight') != scored['unknown_weight']):
                raise ValueError('전체 점수·평가 가능 배점·U 배점이 일치하지 않습니다')
            categories = scores.get('category_scores')
            if not isinstance(categories, dict) or set(categories) != set(SCORECARD):
                raise ValueError('6개 영역별 점수 정보가 필요합니다')
            for category, items in SCORECARD.items():
                known = sum(weight for name, weight in items.items() if details[name]['grade'] is not None)
                expected = {'weight': sum(items.values()), 'scorable_weight': known,
                            'earned_score': sum(scored['items'][name]['score'] or 0 for name in items) if known else None,
                            'unknown_items': [name for name in items if details[name]['grade'] is None]}
                if not isinstance(categories[category], dict) or any(
                        categories[category].get(key) != value for key, value in expected.items()):
                    raise ValueError('영역별 점수와 항목별 점수가 일치하지 않습니다')
            coverage = scores.get('coverage')
            if (not isinstance(coverage, dict) or coverage.get('research_criteria') != 18
                    or any(type(coverage.get(key)) is not int or not 0 <= coverage[key] <= 18
                           for key in ('attempted_criteria', 'supported_criteria'))
                    or not isinstance(scores.get('decision_reason'), str)
                    or not isinstance(scores.get('investment_decision'), str)):
                raise ValueError('조사 범위·투자 의견의 저장 정보가 올바르지 않습니다')
            pdf_path = score_path.with_name(score_path.name.removesuffix('.scores.json') + '.pdf')
            stored_path = scores.get('report_path')
            if not isinstance(stored_path, str) or not stored_path.strip():
                raise ValueError('점수 파일에 보고서 PDF 경로가 없습니다')
            report_path = Path(stored_path).expanduser()
            # 예전 상대 경로도 호출자의 현재 디렉터리에 의존하지 않고 확인한다.
            if (not report_path.is_absolute() and '..' not in report_path.parts
                    and pdf_path.parts[-len(report_path.parts):] == report_path.parts):
                report_path = pdf_path
            if report_path.resolve() != pdf_path:
                raise ValueError('점수 파일과 보고서 PDF 경로가 일치하지 않습니다')
            if not pdf_path.is_file():
                raise FileNotFoundError('저장된 보고서 PDF가 없습니다')
            checks = validate_pdf(pdf_path)
            if not all(checks.get(key) is True for key in (
                    'all_a4', 'within_page_limit', 'text_within_margins')):
                raise ValueError('기존 PDF의 용지·쪽 수·여백 검사가 실패했습니다')
            scores = {**scores, 'report_path': str(pdf_path)}
            results[company] = {'scores': scores, 'scores_path': score_path,
                                'pdf': pdf_path, 'pdf_checks': checks}
        except Exception as error:
            errors[company] = type(error).__name__
    artifacts = _comparison_artifacts(directory, results, errors)
    return {'results': results, 'errors': errors, 'comparison': artifacts}


def run_batch(*, max_rechecks=4, output_dir=None, env_file=None, com_data_dir=None,
              data_dir=None, index_dir=None, _dependencies=None, _catalog=None) -> dict:
    """공유 임베딩을 한 번 준비하고 세 기업의 State·추적·결과는 분리한다."""
    verify_scorecard_reference()
    load_dotenv(env_file or ROOT / '.env')
    inputs = input_directories(data_dir, com_data_dir, index_dir)
    directory = (Path(output_dir).expanduser() if output_dir is not None else ROOT / 'output').resolve()
    catalog = _catalog if _catalog is not None else load_project_catalog(inputs['data_dir'])
    deps = _dependencies if _dependencies is not None else real_dependencies(inputs['com_data_dir'], directory / 'web_sources',
                                             data_dir=inputs['data_dir'], index_dir=inputs['index_dir'])
    results, errors = {}, {}
    for company in COMPANIES:
        print(f'기업 분석: {company}', flush=True)
        try:
            results[company] = run(company, max_rechecks=max_rechecks, output_dir=directory, env_file=env_file,
                com_data_dir=inputs['com_data_dir'], data_dir=inputs['data_dir'], index_dir=inputs['index_dir'],
                _dependencies=deps, _catalog=catalog)
        except Exception as error:
            # API 오류 본문에 요청 키·헤더가 포함될 수 있으므로 실패 유형만 공개한다.
            errors[company] = type(error).__name__
            print(f'기업 분석 실패: {company} ({type(error).__name__})', flush=True)
    artifacts = _comparison_artifacts(directory, results, errors)
    combined, combined_error = None, None
    if not errors:
        try:
            combined = _combine_results(list(results.values()), directory)
        except Exception as error:
            combined_error = type(error).__name__
            print(f'통합 보고서 생성 실패: {combined_error}; 기업별 결과를 보존했습니다.', flush=True)
    return {'results': results, 'errors': errors, 'comparison': artifacts,
            'combined': combined, 'combined_error': combined_error}


def _combine_results(records, directory, reuse_analysis=None):
    """기업별 확정 점수를 유지해 세 기업을 하나의 제출 보고서로 통합한다."""
    settings = tracing_settings()
    with tracing_context(enabled=settings['enabled'], project_name=settings['project']):
        return _generate_combined(records, directory, reuse_analysis=reuse_analysis)


@traceable(name='validated_combined_analysis_reuse')
def _load_combined_analysis(path, records, source_contexts=None):
    """보고서·점수·인용이 같은 저장 분석만 재사용한다. 품질 판정은 재사용하지 않는다."""
    from combined_report import build_combined_report
    from combined_report import _normalise_records, combined_source_contexts
    path = Path(path).expanduser().resolve()
    cached = json.loads(path.read_text(encoding='utf-8'))
    if cached.get('deterministic_checks', {}).get('company_reports_synthesized') is not True:
        raise ValueError('기업보고서 종합 입력이 없는 이전 최종본은 분석 재사용이 불가능합니다')
    synthesis, portfolio = cached.get('report_synthesis'), cached.get('portfolio_decision')
    if not isinstance(synthesis, dict) or not synthesis.get('source_reports') or not isinstance(portfolio, dict):
        raise ValueError('분석 재사용에 원기업 보고서 해시와 투자 선정 결과가 필요합니다')
    if portfolio.get('action') != '투자 보류' and not portfolio.get('comparison_evidence_ids'):
        raise ValueError('comparison_evidence_ids가 없는 이전 분석은 기업 비교 근거를 다시 선정해야 합니다')
    built = build_combined_report(records, report_synthesis=synthesis, portfolio_decision=portfolio)
    if cached.get('companies') != built['comparison']:
        raise ValueError('원기업 점수·조사 상태·실행 ID가 변경되어 분석을 다시 생성해야 합니다')
    companies = _normalise_records(records)
    current_details = {item['company']: item['details'] for item in companies}
    if 'source_score_details' in cached:
        if cached['source_score_details'] != current_details:
            raise ValueError('원기업 21항목 점수·사유가 변경되어 분석을 다시 생성해야 합니다')
    else:
        # 항목 snapshot 추가 전의 실제 산출물은 동일 묶음의 고정 점수표로 대조한다.
        markdown_path = path.with_suffix('.md')
        if not markdown_path.is_file():
            raise ValueError('분석 재사용에 원기업 21항목 점수 snapshot 또는 기존 Markdown 점수표가 필요합니다')
        tables = lambda text: [line for line in text.splitlines() if line.startswith('|')]
        if tables(markdown_path.read_text(encoding='utf-8')) != tables(built['markdown']):
            raise ValueError('원기업 21항목 점수가 변경되어 분석을 다시 생성해야 합니다')
    current_claims = {f"{item['prefix']}-{ref}": {k: v for k, v in claim.items() if k != 'id'}
                      for item in companies for ref, claim in item['evidence'].items()}
    cached_claims = {claim['id']: {k: v for k, v in claim.items() if k != 'id'}
                     for context in cached.get('source_contexts', []) for claim in context.get('checked_claims', [])}
    if cached_claims != current_claims:
        raise ValueError('검증된 원문 주장·인용이 변경되어 분석을 다시 생성해야 합니다')
    if source_contexts is None:
        source_contexts = combined_source_contexts(records, ' '.join(f'[{ref}]' for ref in current_claims))
    context_key = lambda context: (context.get('company'), context.get('doc_id'), context.get('page'))
    page_values = lambda contexts: {context_key(c): {k: v for k, v in c.items() if k != 'source_path'}
                                    for c in contexts}
    if page_values(cached.get('source_contexts', [])) != page_values(source_contexts):
        raise ValueError('원본 페이지 또는 출처가 변경되어 분석을 다시 생성해야 합니다')
    return built['report_synthesis'], built['portfolio_decision']


@traceable(name='physical-ai-final-investment-report')
def _generate_combined(records, directory, reuse_analysis=None):
    """기업 산출물을 최종 StateGraph에 전달하고 실행 기록만 수집한다."""
    from combined_quality import evaluate_combined_quality, revise_combined_quality
    from portfolio_decision import select_portfolio_investment
    from company_brief import save_company_brief
    from report_synthesis import synthesize_company_reports
    from final_report_pipeline import build_final_report_graph, FinalReportDependencies, initial_final_state

    def prepare_inputs(company_records, target):
        prepared = _prepare_combined_inputs(company_records, target)
        for record in company_records:
            save_company_brief(record, target)
        return prepared

    deps = FinalReportDependencies(prepare_inputs=prepare_inputs, synthesize=synthesize_company_reports,
        select=select_portfolio_investment, load_analysis=_load_combined_analysis,
        evaluate=evaluate_combined_quality, revise=revise_combined_quality, write_json=_write_json)
    graph = build_final_report_graph(deps, checkpointer=InMemorySaver())
    graph_run_id = uuid4()
    state = initial_final_state(records, directory, analysis_mode='reuse' if reuse_analysis else 'fresh',
                                reuse_analysis_path=reuse_analysis, trace_id=str(graph_run_id))
    metrics = ExecutionMetrics(graph_run_id)
    config = {'configurable': {'thread_id': str(graph_run_id)}, 'run_id': graph_run_id,
              'run_name': 'final-investment-orchestration', 'recursion_limit': 40,
              'callbacks': [metrics], 'metadata': {'workflow': 'final_report', 'company_count': len(records)}}
    events, started = [], perf_counter()
    directory = Path(directory).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    try:
        for update in graph.stream(state, config, stream_mode='updates'):
            for node, change in update.items():
                event = {'node': node, 'step': change.get('step_count')}
                for key in ('route', 'rewrite_count', 'revision_accepted'):
                    if key in change:
                        event[key] = change[key]
                if 'report_evaluation' in change:
                    event['quality_passed'] = change['report_evaluation'].get('passed')
                    event['quality_action'] = change['report_evaluation'].get('action')
                events.append(event)
                print(f"최종 그래프: {node}" + (f" → {event['quality_action']}" if event.get('quality_action') else ''), flush=True)
        final = graph.get_state(config).values
    except Exception as error:
        checkpoint = graph.get_state(config)
        _write_json(directory / 'final_report.failed_state.json', {
            'error_type': type(error).__name__, 'graph_run_id': str(graph_run_id),
            'next_nodes': list(checkpoint.next), 'events': events,
            'state': json.loads(json.dumps(dict(checkpoint.values), ensure_ascii=False, default=str)),
            'resume_note': 'combined_analysis.json이 있으면 --reuse-analysis로 분석을 보존하고 품질부터 재실행할 수 있습니다.'})
        raise
    result = final['result']
    root = get_current_run_tree()
    trace = {'root_run_id': str(root.id) if root is not None else None,
             'graph_run_id': str(graph_run_id), 'engine': 'langgraph',
             'company_trace_ids': [r['state']['trace_id'] for r in records],
             'project': tracing_settings()['project'], 'status': result['status'],
             'analysis_reused': bool(reuse_analysis),
             'reused_analysis_source': Path(reuse_analysis).name if reuse_analysis else None,
             'stages': final['node_history'], 'events': events,
             'input_manifest': final['input_manifest'],
             'metrics': metrics.snapshot(perf_counter() - started)}
    _write_json(directory / 'final_report.trace.json', trace)
    _write_json(directory / 'final_report.graph.json', {
        'engine': 'langgraph', 'graph_run_id': str(graph_run_id),
        'nodes': list(graph.get_graph().nodes),
        'edges': [{'source': edge.source, 'target': edge.target, 'conditional': edge.conditional}
                  for edge in graph.get_graph().edges], 'executed_nodes': final['node_history'],
        'rewrite_count': final['rewrite_count'], 'max_rewrites': final['max_rewrites'],
        'step_count': final['step_count'], 'max_steps': final['max_steps'],
        'status': result['status'], 'quality_stop_reason': result['quality_stop_reason']})
    return result


def _load_company_records(directory):
    """회사별 State·점수·내보낸 본문을 같은 파일 묶음에서만 읽는다."""
    source = Path(directory).expanduser().resolve()
    records = []
    for company in COMPANIES:
        stem = company.lower().replace(' ', '_')
        candidates = [path for path in (source / f'{stem}.state.json',
                                       source / f'{stem}_draft.state.json') if path.is_file()]
        if not candidates:
            raise FileNotFoundError(f'{company} 최종 State가 없습니다')
        latest = max(candidates, key=lambda path: (path.stat().st_mtime_ns, path.name == f'{stem}.state.json'))
        wrapper = json.loads(latest.read_text(encoding='utf-8'))
        state = wrapper.get('state', {})
        if state.get('company_info', {}).get('name') != company or wrapper.get('mode') != 'live':
            raise ValueError('통합 보고서는 기업명이 일치하는 실제 실행 State만 사용합니다')
        suffix = '_draft' if state.get('status') == 'quality_failed' else ''
        if latest.name != f'{stem}{suffix}.state.json':
            raise ValueError(f'{company}: State 파일명과 최종·초안 상태가 다릅니다')
        scores_path = source / f'{stem}{suffix}.scores.json'
        scores = json.loads(scores_path.read_text(encoding='utf-8'))
        if scores.get('company') != company or scores.get('trace_id') != state.get('trace_id'):
            raise ValueError('State와 점수 파일의 기업·실행 ID가 다릅니다')
        record = {'state': state, 'scores': scores, 'scores_path': scores_path,
                  'state_path': latest, 'pdf': source / f'{stem}{suffix}.pdf'}
        markdown_path = source / f'{stem}{suffix}.md'
        if markdown_path.is_file():
            if markdown_path.read_text(encoding='utf-8') != state.get('final_report'):
                raise ValueError(f'{company}: 기업 보고서 Markdown과 State final_report가 일치하지 않습니다')
            record['markdown'] = markdown_path
        records.append(record)
    return records


def _prepare_combined_inputs(records, directory):
    """추가 LLM 없이 실제 최종 입력 전문을 저장하고 회사별 키·해시를 노출한다."""
    from company_report_inputs import build_company_report_inputs
    from combined_report import _normalise_records, combined_source_contexts
    companies = _normalise_records(records)
    reports = build_company_report_inputs(companies)
    references = ' '.join(f"[{item['prefix']}-{ref}]" for item in companies for ref in item['evidence'])
    contexts = combined_source_contexts(records, references)
    payload = {'schema_version': 1, 'report_body_key': 'company_reports[].report_markdown',
               'canonical_body_source': 'state.final_report', 'company_reports': reports,
               'source_contexts': contexts,
               'verified_results': [{'company': item['company'], 'score_details': item['details'],
                   'verified_evidence': {f"{item['prefix']}-{ref}": claim for ref, claim in item['evidence'].items()},
                   'individual_case': item['case']} for item in companies]}
    directory = Path(directory).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'combined_report_inputs.json'
    _write_json(path, payload)
    summary = [{'company': report['company'], 'trace_id': report['trace_id'],
                'report_sha256': report['report_sha256'], 'body_characters': len(report['report_markdown']),
                'paragraph_count': len(report['paragraphs']),
                'source_page_count': sum(c['company'] == report['company'] for c in contexts)} for report in reports]
    manifest = {'path': str(path), 'sha256': sha256(path.read_bytes()).hexdigest(),
                'report_body_key': payload['report_body_key'], 'companies': summary}
    print('최종 입력: 기업 보고서 전문 3개 + 검증 원문 ' + str(len(contexts)) + '쪽 → ' + str(path), flush=True)
    return companies, reports, contexts, manifest


def inspect_combined_inputs(directory, *, output_dir=None):
    """모델·검색·임베딩 없이 실제 합성 입력과 원문 연결을 점검한다."""
    verify_scorecard_reference()
    records = _load_company_records(directory)
    return _prepare_combined_inputs(records, output_dir or directory)[3]


def combine_reports(directory, *, output_dir=None, env_file=None, reuse_analysis=None):
    """저장된 최종 State로 통합 작성·원문 품질 검사만 실행한다. 재조사·채점은 하지 않는다."""
    verify_scorecard_reference()
    load_dotenv(env_file or ROOT / '.env')
    source = Path(directory).expanduser().resolve()
    records = _load_company_records(source)
    return _combine_results(records, Path(output_dir).expanduser().resolve() if output_dir else source,
                            reuse_analysis=reuse_analysis)


def review_report(snapshot_path, *, output_dir=None, env_file=None):
    """저장된 보고서를 수정 없이 원문 품질 재평가한다. 조사·점수·재작성 예산은 보존한다."""
    load_dotenv(env_file or ROOT / '.env')
    path = Path(snapshot_path).expanduser().resolve()
    wrapper = json.loads(path.read_text(encoding='utf-8'))
    state = wrapper.get('state')
    _validate_report_resume(state)
    if not state.get('final_report') or wrapper.get('mode') != 'live':
        raise ValueError('원문 품질 재평가는 실제 실행의 완성된 보고서 State가 필요합니다')
    scores_path = path.parent / Path(wrapper.get('scores_path', '')).name
    prior = json.loads(scores_path.read_text(encoding='utf-8'))
    deps = real_dependencies(prior['input_paths']['com_data_dir'], path.parent / 'web_sources',
                             data_dir=prior['input_paths']['data_dir'], prepare_retrieval=False)
    with tracing_context(enabled=tracing_settings()['enabled'], project_name=tracing_settings()['project']):
        return _review_report(state, prior, path, deps,
                              Path(output_dir).resolve() if output_dir else path.parent)


def revalidate_report(snapshot_path, *, output_dir=None, env_file=None):
    """기존 검증 사실만 원문 재검증·재채점하고 새 보고서 품질 루프로 연결한다."""
    from saved_revalidation import revalidate_saved_research
    load_dotenv(env_file or ROOT / '.env')
    path = Path(snapshot_path).expanduser().resolve()
    wrapper = json.loads(path.read_text(encoding='utf-8'))
    state = wrapper.get('state')
    _validate_report_resume(state)
    if wrapper.get('mode') != 'live':
        raise ValueError('원문 재검증은 실제 실행의 조사 종료 State만 지원합니다')
    prior = json.loads((path.parent / Path(wrapper.get('scores_path', '')).name).read_text(encoding='utf-8'))
    directory = Path(output_dir).resolve() if output_dir else path.parent
    deps = real_dependencies(prior['input_paths']['com_data_dir'], directory / 'web_sources',
                             data_dir=prior['input_paths']['data_dir'], prepare_retrieval=False)
    with tracing_context(enabled=tracing_settings()['enabled'], project_name=tracing_settings()['project']):
        repaired = _revalidate_saved_state(state, deps, revalidate_saved_research)
    return run(state['company_info']['name'], output_dir=directory, env_file=env_file,
               _dependencies=deps, _resume_state=repaired, _resume_path=path, _resume_metadata=prior,
               **prior['input_paths'])


@traceable(name='saved_company_evidence_and_grade_revalidation')
def _revalidate_saved_state(state, deps, revalidate_research):
    repaired = deepcopy(state)
    repaired['research_result'] = revalidate_research(repaired, deps.evidence_checker)
    repaired['investment_result'] = {}  # 이전 E-ID·등급을 새 검증에 재사용하지 않는다.
    repaired['investment_result'] = deps.judge(flat_payload(repaired))
    repaired['final_report'] = ''
    repaired['report_evaluation'] = {}
    repaired['report_rewrite_count'] = 0  # 새 근거·판정 버전의 보고서 예산, 조사 예산은 보존한다.
    repaired['status'] = 'partial'
    repaired['feedback'] = ['기존 검증 사실을 원문 재검증·재채점했습니다. 미래 계획·자사 홍보·구매 거래를 실제 완료 성과로 확대하지 마세요.']
    return repaired


@traceable(name='saved_company_report_quality_review')
def _review_report(state, prior, path, deps, directory):
    source_trace_id = state['trace_id']
    state = deepcopy(state)
    evaluation = deps.report_evaluator(flat_payload(state))
    state['report_evaluation'] = evaluation
    state['status'] = ('partial' if prior.get('unknown_weight') or state.get('feedback') else 'completed') \
        if evaluation.get('passed') is True and not evaluation.get('issues') else 'quality_failed'
    state['trace_id'] = str(uuid4())
    root = get_current_run_tree()
    tracing = {**tracing_settings(), 'root_run_id': str(root.id) if root else None,
               'upload_verified': False, 'source_graph_tracing': prior.get('tracing')}
    return _save_report_artifacts(state, directory, mode='live', inputs=prior['input_paths'],
        index_info=prior.get('index_info', {}), events=[{'report_evaluator': {'report_evaluation': evaluation}}],
        criteria_reference=verify_scorecard_reference(), tracing=tracing, metrics=prior.get('metrics'),
        configured_models=prior.get('configured_models'),
        provenance={'snapshot_path': str(path), 'resumed_from_trace_id': source_trace_id,
                    'mode': 'same_report_quality_review', 'research_and_scores_unchanged': True})


def main():
    logging.getLogger('pdfminer').setLevel(logging.ERROR)
    parser = argparse.ArgumentParser(description='기업별 원문 검증 결과로 최종 투자 대상과 하나의 최대 10쪽 보고서 생성')
    parser.add_argument('--company', choices=[*COMPANIES, 'all'], default=None,
                        help='기본값 all: 세 기업 분석→최종 투자 대상→통합 보고서')
    parser.add_argument('--demo', action='store_true', help='Figure AI만 API 없이 모의 평가로 시연')
    parser.add_argument('--max-rechecks', type=int, choices=[3, 4, 5], default=4)
    parser.add_argument('--output-dir')
    parser.add_argument('--env-file', help='실제 분석용 OPENAI_API_KEY 환경 파일 경로')
    parser.add_argument('--data-dir', help='catalog.csv와 원천 PDF를 가진 data 폴더')
    parser.add_argument('--index-dir', help='BGE-M3 청크·임베딩 디스크 캐시 폴더')
    parser.add_argument('--com-data-dir', help='기업 조사 PDF와 등록 원문 링크를 읽을 폴더')
    parser.add_argument('--resume-report', help='품질 통과 PDF 출력 실패 또는 조사 종료 후 보고서 실패를 저장한 failed_state.json')
    parser.add_argument('--refresh-comparison', metavar='DIR', help='기존 live 점수·PDF로 기업 비교표만 갱신')
    parser.add_argument('--combine-reports', metavar='DIR', help='저장된 세 기업 State로 하나의 최대 10쪽 보고서 생성·원문 품질 평가')
    parser.add_argument('--check-combine-inputs', metavar='DIR', help='API 없이 기업 보고서 전문·키·해시·실제 근거 페이지를 점검하고 combined_report_inputs.json 저장')
    parser.add_argument('--reuse-analysis', metavar='JSON', help='--combine-reports에서 같은 기업보고서·점수의 저장 종합/선정 분석만 재사용하고 품질은 다시 평가')
    parser.add_argument('--review-report', metavar='STATE', help='저장된 실제 보고서 State의 원문 품질만 재평가 (조사·채점·재작성 없음)')
    parser.add_argument('--revalidate-report', metavar='STATE', help='저장된 검증 사실을 원문 재검증·재채점하고 보고서만 다시 생성 (새 워커 조사 없음)')
    parser.add_argument('--check-criteria', action='store_true', help='설계 PDF 2~3쪽과 코드의 21항목 배점·U 규칙 대조 (API 없음)')
    parser.add_argument('--check-tracing', action='store_true', help='LangSmith 설정·프로젝트·키 유무만 확인 (API 없음, 키 값 미출력)')
    args = parser.parse_args()
    if args.check_combine_inputs and any((args.combine_reports, args.reuse_analysis, args.check_tracing,
            args.check_criteria, args.resume_report, args.refresh_comparison, args.review_report,
            args.revalidate_report, args.demo, args.company)):
        parser.error('--check-combine-inputs는 다른 실행·검사 옵션과 함께 사용할 수 없습니다')
    if args.reuse_analysis and not args.combine_reports:
        parser.error('--reuse-analysis는 --combine-reports DIR과 함께 사용해야 합니다')
    if args.demo and args.company is None:
        args.company = 'Figure AI'
    options = {name: getattr(args, name) for name in ('max_rechecks', 'output_dir', 'env_file', 'com_data_dir', 'data_dir', 'index_dir')}
    if args.check_combine_inputs:
        manifest = inspect_combined_inputs(args.check_combine_inputs, output_dir=args.output_dir)
        for company in manifest['companies']:
            print(f"{company['company']}: 본문 {company['body_characters']}자, {company['paragraph_count']}문단, "
                  f"원문 {company['source_page_count']}쪽, SHA256 {company['report_sha256']}")
    elif args.check_tracing:
        if args.check_criteria or args.resume_report or args.refresh_comparison or args.combine_reports or args.review_report or args.revalidate_report or args.demo:
            parser.error('--check-tracing은 보고서 생성·복구·평가 기준 검사 옵션과 함께 사용할 수 없습니다')
        load_dotenv(args.env_file or ROOT / '.env')
        settings = tracing_settings()
        print(f"LangSmith 추적 설정: {'켜짐' if settings['requested'] else '꺼짐'}")
        print(f"프로젝트: {settings['project']}; 키: {'입력됨' if settings['key_present'] else '비어 있음'}")
        print(f"실행 시 원격 추적: {'활성화 가능' if settings['enabled'] else '비활성화'}; 인증·업로드는 확인하지 않았습니다.")
    elif args.check_criteria:
        if args.resume_report or args.refresh_comparison or args.combine_reports or args.review_report or args.revalidate_report or args.demo:
            parser.error('--check-criteria는 보고서 생성·복구·비교표 옵션과 함께 사용할 수 없습니다')
        reference = verify_scorecard_reference()
        print(f"평가 기준 검증 통과: {reference['category_count']}영역 / {reference['criterion_count']}항목 / {reference['total_weight']}점")
        print(f"환산식: {reference['formula']}; U: {reference['unknown_policy']}")
        print(f"기준 PDF: {reference['source_pdf']} (2~3쪽)")
    elif args.revalidate_report:
        if args.resume_report or args.refresh_comparison or args.combine_reports or args.review_report or args.demo:
            parser.error('--revalidate-report는 생성·복구·비교표 옵션과 함께 사용할 수 없습니다')
        result = revalidate_report(args.revalidate_report, output_dir=args.output_dir, env_file=args.env_file)
        print(f"원문·등급 재검증: {result['state']['status']}; PDF: {result['pdf']}")
    elif args.review_report:
        if args.resume_report or args.refresh_comparison or args.combine_reports or args.demo:
            parser.error('--review-report는 생성·복구·비교표 옵션과 함께 사용할 수 없습니다')
        result = review_report(args.review_report, output_dir=args.output_dir, env_file=args.env_file)
        print(f"원문 품질 재평가: {result['state']['status']}; PDF: {result['pdf']}")
    elif args.combine_reports:
        if args.resume_report or args.refresh_comparison or args.demo:
            parser.error('--combine-reports는 생성·복구·비교표 옵션과 함께 사용할 수 없습니다')
        result = combine_reports(args.combine_reports, output_dir=args.output_dir, env_file=args.env_file,
                                 reuse_analysis=args.reuse_analysis)
        print(f"통합 보고서 상태: {result['status']}; PDF: {result['pdf']} ({result['pdf_checks']['pages']}쪽)")
    elif args.refresh_comparison:
        if args.resume_report or args.demo:
            parser.error('--refresh-comparison은 --resume-report 또는 --demo와 함께 사용할 수 없습니다')
        result = refresh_comparison(args.refresh_comparison)
        print(f"기업 비교표 갱신: 성공 {len(result['results'])} / 실패 {len(result['errors'])}")
        print(f"기업 비교표: {result['comparison']['markdown']}")
    elif args.resume_report:
        if args.demo or args.company == 'all':
            parser.error('--resume-report는 --demo 또는 --company all과 함께 사용할 수 없습니다')
        options.pop('max_rechecks')  # 저장된 원래 예산을 그대로 사용한다.
        result = resume_report(args.resume_report, **options)
        print(f"보고서 복구 상태: {result['state']['status']}; PDF: {result['pdf']}")
        print(f"보존된 항목별 점수: {result['scores_path']}")
    elif args.company in {None, 'all'}:
        if args.demo:
            parser.error('--demo는 Figure AI 한 기업의 시연만 지원합니다')
        result = run_batch(**options)
        print(f"기업 분석: 성공 {len(result['results'])} / 실패 {len(result['errors'])}")
        print(f"기업 비교표: {result['comparison']['markdown']}")
        if result.get('combined'):
            combined = result['combined']
            print(f"3기업 통합 보고서: {combined['pdf']} ({combined['pdf_checks']['pages']}쪽, {combined['status']})")
        else:
            print('통합 보고서는 완성되지 않았습니다. 보존된 실패 상태를 복구한 뒤 --combine-reports를 실행하세요.')
    else:
        result = run(args.company, demo=args.demo, **options)
        print(f"상태: {result['state']['status']}; 추가 검토: {result['state']['recheck_count']}회")
        print(f"PDF: {result['pdf']} ({result['pdf_checks']['pages']}쪽, A4·20mm)")
        print(f"항목별 점수: {result['scores_path']}")


if __name__ == '__main__':
    main()
