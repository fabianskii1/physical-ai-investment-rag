"""세 기업을 실제 그래프로 독립 실행하고 근거·재호출·보고서 결과를 보존한다."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import logging
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
import app


def summarize(result):
    state = result['state']
    research = state['research_result']
    evidence = research.get('source_checked_claims', [])
    return {'status': state['status'], 'report': str(result['pdf']),
        'trace_id': state['trace_id'], 'recheck_count': state['recheck_count'],
        'worker_calls': len(state['worker_results']), 'verified_claims': len(evidence),
        'source_pages': sorted({f"{c['source']['doc_id']}:p{c['source']['page']}" for c in evidence}),
        'supported_criteria': research.get('supported_criteria_count'),
        'reviewed_unavailable': [key for key, c in research.get('coverage_by_criterion', {}).items()
                                 if c['status'] == 'reviewed_unavailable'],
        'gap_reasons': dict(Counter(g['reason'] for g in research.get('evidence_gaps', []))),
        'score': state['investment_result'].get('total_score'),
        'unknown_weight': state['investment_result'].get('unknown_weight'),
        'quality_passed': state['report_evaluation'].get('passed')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='실제 API·원문 읽기 비용을 사용하는 실행')
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--source-cache-dir',help='등록 원문의 기존 보관 PDF를 재사용할 캐시')
    args = parser.parse_args()
    if not args.live:
        parser.error('실제 기업별 생성에는 --live가 필요합니다')
    logging.getLogger('pdfminer').setLevel(logging.ERROR)
    load_dotenv(ROOT / '.env')
    directory = Path(args.output_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    paths = app.input_directories()
    deps = app.real_dependencies(paths['com_data_dir'], args.source_cache_dir or directory / 'web_sources',
                                data_dir=paths['data_dir'], index_dir=paths['index_dir'])
    catalog = app.load_project_catalog(paths['data_dir'])
    summaries = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {pool.submit(app.run, company, max_rechecks=3,
                   output_dir=directory / company.lower().replace(' ', '_'),
                   data_dir=paths['data_dir'], com_data_dir=paths['com_data_dir'], index_dir=paths['index_dir'],
                   _dependencies=deps, _catalog=catalog): company for company in app.COMPANIES}
        for job in as_completed(jobs):
            company = jobs[job]
            try:
                summaries[company] = summarize(job.result())
            except Exception as error:
                summaries[company] = {'status': 'execution_failed', 'error_type': type(error).__name__}
            (directory / 'audit_summary.json').write_text(json.dumps(summaries, ensure_ascii=False, indent=2))
            print('COMPANY RESULT', company, json.dumps(summaries[company], ensure_ascii=False), flush=True)
    return 0 if all(x.get('quality_passed') for x in summaries.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
