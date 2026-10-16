"""수집이 끝난 워커 출력을 보존하고 원문 재검증·채점·보고서 그래프만 실행한다."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import json
import logging
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
import app


def recover(path, directory, deps, regrade_only=False, quality_only=False, refresh_template=False):
    wrapper=json.loads(path.read_text())
    state=deepcopy(wrapper['state'])
    paths=wrapper.get('input_paths')
    if not paths:
        prior=json.loads((path.parent/Path(wrapper['scores_path']).name).read_text())
        paths=prior['input_paths']
    if not regrade_only and not quality_only:
        state['research_result']['worker_source_recovery']={
        'original_trace_id':state['trace_id'],'worker_or_retrieval_executed':False,
        'new_web_collection_executed':False,
        'previous_verified_claims':len(state['research_result'].get('source_checked_claims',[])),
            'investment_regrading_required':True,'report_regeneration_required':True}
    else:
        state['research_result']['report_refresh']={'original_trace_id':state['trace_id'],
            'stage':'quality' if quality_only else 'grade','worker_or_retrieval_executed':False,
            'verified_claims_preserved':True}
    if refresh_template:
        from agent_pipeline import flat_payload
        from rag.report import report
        if not state.get('report_draft'):
            raise ValueError('재조립할 구조화된 기업 초안이 없습니다')
        state.update(report(flat_payload(state),writer=lambda _payload:state['report_draft']))
        state['research_result']['report_refresh']['template_refreshed_without_llm']=True
    state.update(final_report=state['final_report'] if quality_only else '',report_evaluation={},report_rewrite_count=0,report_regrade_count=0,status='partial',
                 feedback=['확보한 워커 원출력을 현재 원문으로 재검증했다. 새 조사 없이 정확한 확인 사실·목표·한계를 보고서에 반영하세요.'])
    return app.run(state['company_info']['name'],output_dir=directory,_dependencies=deps,
        _resume_state=state,_resume_path=path,_resume_metadata=wrapper,
        _recover_worker_sources=not regrade_only and not quality_only,
        _regrade_only=regrade_only,_review_only=quality_only,**paths)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory')
    parser.add_argument('--output-dir',required=True)
    parser.add_argument('--regrade-only',action='store_true',help='현재 검증 사실은 유지하고 채점·보고서 노드부터 실행')
    parser.add_argument('--quality-only',action='store_true',help='현재 보고서를 먼저 평가하고 실제 결함만 수정; 검색·워커·초기 채점 없음')
    parser.add_argument('--company',choices=app.COMPANIES,help='지정 기업만 복구; 생략하면 세 기업')
    parser.add_argument('--refresh-template',action='store_true',help='보존한 구조화 초안으로 최신 템플릿만 재조립하고 품질 평가')
    args=parser.parse_args()
    if args.regrade_only and args.quality_only:
        parser.error('재채점 시작과 품질 시작은 하나만 선택하세요')
    if args.refresh_template and not args.quality_only:
        parser.error('템플릿 재조립은 --quality-only와 함께 사용하세요')
    load_dotenv(ROOT/'.env')
    logging.getLogger('pdfminer').setLevel(logging.ERROR)
    source,directory=Path(args.directory).resolve(),Path(args.output_dir).resolve()
    directory.mkdir(parents=True,exist_ok=True)
    deps=app.real_dependencies(prepare_retrieval=False)
    paths=[]
    for company in app.COMPANIES:
        if args.company and company!=args.company:
            continue
        stem=company.lower().replace(' ','_')
        folder=source/stem if (source/stem).is_dir() else source
        candidates=[p for p in (folder/f'{stem}.state.json',folder/f'{stem}_draft.state.json',
                                folder/f'{stem}.failed_state.json',folder/f'{stem}_draft.failed_state.json') if p.is_file()]
        if not candidates:
            raise FileNotFoundError(company+' 확보된 워커 State 없음')
        paths.append(max(candidates,key=lambda p:p.stat().st_mtime_ns))
    summary={}
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs={pool.submit(recover,path,directory,deps,args.regrade_only,args.quality_only,args.refresh_template):path for path in paths}
        for job in as_completed(jobs):
            result=job.result()
            state=result['state']
            summary[state['company_info']['name']]={
                'status':state['status'],'quality_passed':state['report_evaluation'].get('passed'),
                'recovery':state['research_result'].get('report_refresh') or state['research_result'].get('worker_source_recovery'),
                'trace_id':state['trace_id'],'pdf_checks':result['pdf_checks']}
            (directory/'recovery_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
            print(json.dumps(summary,ensure_ascii=False),flush=True)
    return 0 if all(x['quality_passed'] for x in summary.values()) else 1


if __name__=='__main__':
    raise SystemExit(main())
