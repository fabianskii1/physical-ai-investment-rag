"""점수가 U여도 확인된 데이터 수집 수단은 기업·종합 보고서에 보존한다."""
import re
from functools import lru_cache
from pathlib import Path

from evidence_text import exact_quote_span
from rag.PDF.pdf_loader import load_pdf, resolve_pdf_source_path


GROUPS=(
    ('teleoperation','원격 조작·학습 데이터 수집',
     r'Master\s*Arm|Teleoperation|텔레오퍼|원격\s*(?:조작|보조)|학습\s*데이터|양팔\s*작업\s*데이터|training\s*data|data\s*collection',
     r'Master\s*Arm|마스터\s*암|TAS|텔레오퍼|원격\s*(?:조작|보조)|(?:학습|작업|수집)\s*데이터|데이터\s*수집'),
    ('data_platform','데이터 검수·라벨링 플랫폼',
     r'Data\s*Platform|데이터\s*라벨링|에피소드\s*검수',
     r'Data\s*Platform|데이터\s*(?:라벨링|플랫폼)|에피소드\s*검수'),
    ('performance_requirement','성능 요구조건·목표',r'requirement|target|요구조건|목표',r'요구조건|목표'),
    ('failure_information','장애·고장 관련 정보',r'failure|고장|장애',r'forearm|전완|고장|장애'),
)


@lru_cache(maxsize=64)
def _pages(location,doc_id,company,size,modified):
    return load_pdf(location,doc_id,company)


def data_fact_contract(state):
    """근거 목록에서 데이터 수단 두 종류를 고르고 실제 페이지 문맥을 재검증한다.

    회사별 정답·평가 표본은 사용하지 않는다. 원문 인용과 같은 페이지의
    문맥만 보완하며, 별도 자료 탐색·새 회사 사실 생성·등급 변경은 없다.
    """
    claims=state.get('source_checked_claims',[])
    catalog=state.get('document_catalog',{})
    result=[]
    for topic,label,pattern,report_pattern in GROUPS:
        candidates=[(i,c) for i,c in enumerate(claims,1) if c.get('analysis')=='technology_analysis'
                    and re.search(pattern,c.get('evidence_quote',''),re.I)]
        if topic=='performance_requirement':
            candidates=[x for x in candidates if x[1].get('source_scope')=='requirement'
                and re.search(r'\d',x[1]['evidence_quote']) and re.search(
                    r'seconds?|success|accuracy|throughput|latency|cycle\s*time|%|초|성공률|정확도|처리량|사이클',
                    x[1]['evidence_quote'],re.I)]
        if topic=='failure_information':
            candidates=[x for x in candidates if x[1].get('source_scope')=='limitation']
        if not candidates:
            continue
        priority=(r'학습\s*데이터|작업\s*데이터|data\s*collection|training\s*data' if topic=='teleoperation' else
                  r'requirement|요구조건' if topic=='performance_requirement' else r'top.{0,25}failure|주요.{0,12}고장')
        i,c=min(candidates,key=lambda x:(not bool(re.search(priority,x[1]['evidence_quote'],re.I)),len(x[1]['evidence_quote'])))
        source=c['source'];meta=catalog[source['doc_id']]
        path=resolve_pdf_source_path(meta.get('source_path') or meta['file_path'])
        info=path.stat()
        page=_pages(str(path),source['doc_id'],meta.get('company') or state['company_info']['name'],
                    info.st_size,info.st_mtime_ns)[source['page']-1].page_content
        quote=exact_quote_span(page,c['evidence_quote'])
        if quote is None:
            raise ValueError('데이터 수단의 현재 원문 인용이 일치하지 않습니다')
        if topic=='teleoperation' and len(page)<=650:
            excerpt=page
        else:
            excerpt=quote
        if exact_quote_span(page,excerpt) is None:
            raise ValueError('데이터 수단 문맥의 실제 원문 구간이 없습니다')
        if topic=='performance_requirement':
            numbers=list(dict.fromkeys(re.findall(r'\d+(?:\.\d+)?',quote)))[:3]
            report_pattern=r'(?s)(?=.*(?:목표|요구조건))'+''.join(r'(?=.*'+re.escape(n)+')' for n in numbers)
        result.append({'topic':topic,'label':label,'evidence_id':f'E{i}', 'source':source,
                       'source_excerpt':excerpt,'publisher':meta.get('publisher') or source['doc_id'],
                       'report_pattern':report_pattern})
    return result


def preservation_issues(text,contract):
    return [f"{item['label']}: 검증된 구체적 수단을 일반적인 자산 미확인으로 대체하지 말고 종합 본문에 보존하세요"
            for item in contract if not re.search(item['report_pattern'],text,re.I)]
