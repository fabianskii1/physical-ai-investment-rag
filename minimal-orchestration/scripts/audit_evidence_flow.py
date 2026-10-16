"""API 없이 현재 원문→검증 사실→기업 본문→최종 본문 전달을 대조한다.

15개 고정 표본은 생성 에이전트에 전달하지 않는다. 정확한 구절 회수 및
인용 연결 검사이며 모든 의미의 회수율·내용 반영을 보장하는 점수가 아니다.
"""
import argparse
from collections import Counter
from hashlib import sha256
import json
import logging
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evidence_text import exact_quote_span
from rag.PDF.pdf_loader import load_pdf, resolve_pdf_source_path
from report_assertions import assertion_scope_issues

PREFIXES={'MOTOMIND':'MM','ROBROS':'RB','Figure AI':'FG'}


def compact(value):
    return re.sub(r'\s+', '', value).casefold()


def audit(directory, final_dir=None):
    directory=Path(directory).resolve()
    final_dir=Path(final_dir or directory).resolve()
    cases=json.loads((ROOT/'evaluation/retrieval_cases.json').read_text())['cases']
    results, snapshots, case_results = {}, {}, []
    parsed={}
    def page(catalog, doc_id, number):
        meta=catalog[doc_id]
        path=resolve_pdf_source_path(meta.get('source_path') or meta['file_path'])
        if path not in parsed:
            parsed[path]=load_pdf(path, doc_id=doc_id, company=meta.get('company',''))
        return parsed[path][number-1].page_content
    for company,prefix in PREFIXES.items():
        stem=company.lower().replace(' ','_')
        folder=directory/stem if (directory/stem).is_dir() else directory
        path=folder/f'{stem}.state.json'
        wrapper=json.loads(path.read_text())
        state=wrapper['state']
        snapshots[company]=state
        claims=state['research_result']['source_checked_claims']
        evidence={f'E{i}':c for i,c in enumerate(claims,1)}
        bad_quotes=[ref for ref,c in evidence.items() if exact_quote_span(
            page(state['document_catalog'],c['source']['doc_id'],c['source']['page']),c['evidence_quote']) is None]
        body=state['final_report'].split('## 5 — REFERENCE')[0]
        used=set(re.findall(r'\[(E\d+)\]',body))
        completed=set()
        recalled=[]
        calls=sorted(state['worker_results'].values(),key=lambda x:x['recheck_count'])
        for call in calls:
            targets=set(call['task'].get('criteria',[]))
            for c in targets & completed:
                recalled.append({'round':call['recheck_count'],'worker':call['worker'],'criterion_id':c})
            completed.update(c for c,x in call.get('analysis',{}).get('reviewed_criteria',{}).items()
                             if x.get('source_review_complete') is True)
        results[company]={'trace_id':state['trace_id'],'quality_passed':state['report_evaluation'].get('passed'),
            'verified_claims':len(claims),'invalid_current_source_quotes':bad_quotes,
            'worker_calls':dict(Counter(x['worker'] for x in calls)),
            'recheck_count':state['recheck_count'],'report_rewrites':state.get('report_rewrite_count',0),
            'completed_criteria_recalled':recalled,
            'scope_issues':assertion_scope_issues(state['final_report'],evidence)}
        for case in (c for c in cases if c['company']==company):
            anchor=compact(case['anchor'])
            refs=[ref for ref,c in evidence.items() if c['source']['doc_id']==case['doc_id']
                  and c['source']['page']==case['page'] and anchor in compact(c['evidence_quote'])]
            context_refs=[f['evidence_id'] for f in state.get('report_fact_contract',[]) if
                f['source']=={'doc_id':case['doc_id'],'page':case['page']}
                and anchor in compact(f['source_excerpt']) and exact_quote_span(
                    page(state['document_catalog'],case['doc_id'],case['page']),f['source_excerpt']) is not None]
            scoped_refs=list(dict.fromkeys(refs+context_refs))
            alternative_refs=[ref for ref,c in evidence.items() if ref not in bad_quotes
                and ref not in scoped_refs and anchor in compact(c['evidence_quote'])]
            case_results.append({**case,'anchor_exists_in_current_source':anchor in compact(page(
                state['document_catalog'],case['doc_id'],case['page'])),
                'verified_quote_covers_anchor':bool(refs),'evidence_ids':refs,
                'verified_context_covers_anchor':bool(context_refs),'context_evidence_ids':context_refs,
                'verified_source_scope_covers_anchor':bool(scoped_refs),
                'company_body_cites_verified_anchor':bool(set(scoped_refs)&used),
                'alternative_registered_source_evidence_ids':alternative_refs,
                'company_body_cites_registered_anchor':bool(set(scoped_refs+alternative_refs)&used)})
    final_path=final_dir/'final_investment_report.json'
    final={}
    if final_path.is_file():
        metadata=json.loads(final_path.read_text())
        text=(final_dir/'final_investment_report.md').read_text()
        body=text.split('## 5 — REFERENCE')[0]
        fingerprints={c:sha256(s['final_report'].encode()).hexdigest() for c,s in snapshots.items()}
        source_reports=metadata['report_synthesis']['source_reports']
        final={'status':metadata['status'],'quality_passed':metadata['report_evaluation']['passed'],
            'company_report_hashes_match':all(x['report_sha256']==fingerprints[x['company']] and
                  x['trace_id']==snapshots[x['company']]['trace_id'] for x in source_reports),
            'markdown_hash_matches':metadata['markdown_sha256']==sha256(text.encode()).hexdigest(),
            'selected_company':metadata['portfolio_decision'].get('selected_company'),
            'action':metadata['portfolio_decision'].get('action'),'pdf_checks':metadata['pdf_checks'],
            'reflection':metadata['report_evaluation'].get('company_report_reflection'),
            'graph_execution':metadata.get('graph_execution')}
        for case in case_results:
            prefix=PREFIXES[case['company']]
            case['final_body_cites_verified_anchor']=any(f'[{prefix}-{ref}]' in body for ref in case['evidence_ids']+case['context_evidence_ids'])
            case['final_body_cites_registered_anchor']=any(f'[{prefix}-{ref}]' in body for ref in
                case['evidence_ids']+case['context_evidence_ids']+case['alternative_registered_source_evidence_ids'])
    return {'scope':'15개 원문 구절 표본의 회수·인용 전달 검사. 전체 의미 회수율/내용 반영 보장이 아님',
            'companies':results,'cases':case_results,'final':final}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory')
    parser.add_argument('--final-dir')
    args=parser.parse_args()
    logging.getLogger('pdfminer').setLevel(logging.ERROR)
    result=audit(args.directory,args.final_dir)
    path=Path(args.final_dir or args.directory)/'evidence_flow_audit.json'
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({'path':str(path),'companies':result['companies'],'final':result['final'],
        'sample_exact_anchor_hits':sum(c['verified_quote_covers_anchor'] for c in result['cases'])},ensure_ascii=False))


if __name__=='__main__':
    main()
