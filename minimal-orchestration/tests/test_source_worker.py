from types import SimpleNamespace
from langchain_core.documents import Document
import pytest

from source_worker import extract_source_facts, source_statement, WorkerSourceReview
from rag import workflow
from report_assertions import assertion_scope_issues


def test_missing_page_is_repaired_once_without_repeating_completed_page():
    docs = [Document(page_content=f'기관 {p}의 로봇 운영 이력을 확인했습니다.',
                     metadata={'doc_id':'D','page':p,'publisher':'발행기관'}) for p in (1,2)]
    calls=[]
    def request(schema, instruction, payload):
        calls.append(payload)
        p=payload['pages'][0]
        return WorkerSourceReview(pages=[{'doc_id':'D','page':p['page'], 'facts':[{
            'criterion_id':'productization_execution','start_line':1,'end_line':1,'scope':'reported_fact'}], 'exclusion_reason':''}])
    result=extract_source_facts('회사','technology',['productization_execution'],docs,{'content':'완료 기준'},request)
    assert len(result['claims'])==2 and result['page_review']['complete']
    assert [p['page'] for p in calls[1]['pages']]==[2]
    assert all('기관' in c['source_bound_quote'] for c in result['claims'])


def test_invalid_quote_cannot_become_complete_or_a_fact_after_repair_budget():
    doc=Document(page_content='해당 로봇은 연구기관에 납품되었습니다.',metadata={'doc_id':'D','page':1})
    def request(*_):
        return WorkerSourceReview(pages=[{'doc_id':'D','page':1,'facts':[{
            'criterion_id':'paid_sales_delivery','start_line':999,'end_line':999,
            'scope':'reported_fact'}],'exclusion_reason':''}])
    result=extract_source_facts('회사','bigtech',['paid_sales_delivery'],[doc],{'content':''},request)
    assert not result['page_review']['complete']
    assert all(c['status']=='unverified' for c in result['claims'])
    assert result['reviewed_criteria']['paid_sales_delivery']['source_review_complete'] is False


def test_one_invalid_span_does_not_reopen_another_completed_criterion():
    doc=Document(page_content='반복 작업에서 측정된 속도와 조건을 발표했습니다.',metadata={'doc_id':'D','page':1})
    def request(*_):
        return WorkerSourceReview(pages=[{'doc_id':'D','page':1,'facts':[
            {'criterion_id':'core_task_performance','start_line':1,'end_line':1,'scope':'reported_fact'},
            {'criterion_id':'hard_to_copy_assets','start_line':999,'end_line':999,'scope':'reported_fact'}],
            'exclusion_reason':''}])
    result=extract_source_facts('회사','technology',['core_task_performance','hard_to_copy_assets'],
                                 [doc],{'content':''},request)
    assert result['reviewed_criteria']['core_task_performance']['source_review_complete'] is True
    assert result['reviewed_criteria']['hard_to_copy_assets']['source_review_complete'] is False


def test_exact_quote_is_verified_as_attributed_text_but_injected_paraphrase_is_rejected(monkeypatch):
    text='The requirement was 84 seconds total, 37 seconds load time.'
    monkeypatch.setattr(workflow,'load_pdf',lambda *_a,**_k:[SimpleNamespace(page_content=text)])
    claim={'claim':source_statement('기관',text),'criterion_id':'core_task_performance','status':'confirmed',
           'source_bound_quote':text,'source_scope':'requirement','source_refs':[{'doc_id':'D','page':1}]}
    state={'company_info':{'name':'회사'},'document_catalog':{'D':{'publisher':'기관','source_path':'a.pdf','page_count':1}},
           'technology_analysis':{'claims':[claim]}}
    judge=lambda *_: (_ for _ in ()).throw(AssertionError('exact source quote uses mechanical validation'))
    result=workflow.check_evidence(state,judge)
    assert result['source_checked_claims'][0]['source_scope']=='requirement'
    state['technology_analysis']['claims']=[{**claim,'claim':'84초를 달성했다.'}]
    result=workflow.check_evidence(state,judge)
    assert not result['source_checked_claims'] and result['evidence_gaps'][0]['reason']=='bound_quote_mismatch'


@pytest.mark.parametrize('prefix',['','MM-'])
def test_paid_delivery_and_blanket_investor_inflation_are_blocked_even_if_judge_passes(prefix):
    e={prefix+'E1':{'evidence_quote':'연구기관 로봇 납품', 'claim':'납품'},
       prefix+'E2':{'evidence_quote':'HB Investment and Lotte Ventures invested.'}}
    report=f"확인 사실: 연구기관에 유상 납품한 실적이 있다. [{prefix}E1]\n\n확인 사실: 대기업 계열 VC가 투자했다. [{prefix}E2]"
    assert len(assertion_scope_issues(report,e))==2
    safe=f"확인 사실: 납품 사례가 있으나 유상 납품 여부는 미확인이다. [{prefix}E1]"
    assert not assertion_scope_issues(safe,e)
    assert not assertion_scope_issues(f'확인 사실: 납품 사례가 있으나 유상 계약까지는 언급 안 됨. [{prefix}E1]',e)


@pytest.mark.parametrize('text,scope,quote',[
    ('확인 사실: 84초를 달성했다.', 'requirement', 'The requirement was 84 seconds total.'),
    ('확인 사실: 고객 물류센터에 배치 완료했다.', 'plan', 'Agreement to deploy robots.'),
    ('확인 사실: 고객 물류센터에서 운영 중이다.', 'reported_fact', 'Agreement to deploy robots.'),
])
def test_requirements_and_deployment_intent_cannot_be_reported_as_achieved(text,scope,quote):
    assert assertion_scope_issues(text+' [FG-E1]',{'FG-E1':{'source_scope':scope,'evidence_quote':quote}})


def test_an_exact_quote_does_not_exempt_unsupported_assertion_next_to_it():
    report='확인 사실: 「연구기관 납품」 이처럼 유상 납품 실적을 확보했다. [E1]'
    assert assertion_scope_issues(report,{'E1':{'evidence_quote':'연구기관 납품'}})


@pytest.mark.parametrize('text',[
    '반복적 유상납품, 상용 제품 완성 및 여러 산업 현장 연속확장 근거는 부족하다.',
    '반복적 유상납품·대량 상용시장 접근을 보여주는 근거는 제한적임.',
    '유상 계약 및 반복적 대기업 공급이 아닌 단건 연구기관 공급이다.',
    '반복적 유상 공급, 대규모 시장 지배력은 확인 어려움.',
    '반복 유상 계약·설치의 실적·범위는 입증 부족.',
    '유상 매출·반복 납품·재구매와 투자조건의 확인 자료가 비어 있다.',
    '반복적 대량 유상 판매와 장기 고객 재계약 확대 증거는 부재합니다.',
    '매출·유상 납품 반복 확대 등 주요 상업 지표는 독립적 검증이나 복수 사례 보고가 미흡하다.',
    '반복 유상 매출·여러 고객 연속 납품 등 사업의 반복성은 제시되지 않는다.',
])
def test_explicit_commercial_limits_are_not_forced_to_unknown_or_research(text):
    assert not assertion_scope_issues('분석: '+text+' [E1]',{'E1':{'evidence_quote':'연구기관 납품'}})


def test_unsupported_positive_delivery_is_not_exempted_by_another_negative_fact():
    assert assertion_scope_issues('확인 사실: 유상 납품 실적을 확보했으나 재구매 근거는 부족하다. [E1]',
                                 {'E1':{'evidence_quote':'연구기관 납품'}})


def test_prior_web_nul_quote_is_recovered_only_after_current_source_and_attribution_match(monkeypatch):
    text='수서 ICT 동해휴게소 무인 로봇 카페 운영'
    old='수서 ICT\x00동해휴게소 무인 로봇 카페 운영'
    monkeypatch.setattr(workflow,'load_pdf',lambda *_a,**_k:[SimpleNamespace(page_content=text)])
    claim={'claim':source_statement('기관',old),'source_bound_quote':old,'status':'confirmed',
           'criterion_id':'productization_execution','source_refs':[{'doc_id':'D','page':1}]}
    state={'company_info':{'name':'회사'},'document_catalog':{'D':{'publisher':'기관','source_path':'a.pdf','page_count':1}},
           'technology_analysis':{'claims':[claim]}}
    r=workflow.check_evidence(state,lambda *_:None)
    assert r['source_checked_claims'][0]['evidence_quote']==text
    assert '\x00' not in r['source_checked_claims'][0]['claim']
    state['technology_analysis']['claims']=[{**claim,'claim':'유상 납품을 달성했다.'}]
    assert not workflow.check_evidence(state,lambda *_:None)['source_checked_claims']


def test_source_recovery_rechecks_raw_worker_output_and_never_calls_workers():
    from saved_revalidation import recover_worker_research
    from copy import deepcopy
    state={'trace_id':'old','company_info':{'name':'회사'},'document_catalog':{},'investment_terms':None,
           'research_result':{'source_checked_claims':[], 'evidence_gaps':[{'reason':'bound_quote_mismatch'}],
                             'technology_analysis':{'claims':[{'claim':'previously rejected source quote'}]}},
           'worker_results':{'0:technology':{'analysis':{'claims':['immutable audit']}}}}
    original=deepcopy(state)
    def checker(payload):
        assert payload['technology_analysis']['claims'][0]['claim']=='previously rejected source quote'
        return {'source_checked_claims':[], 'evidence_gaps':[],'evidence_status':'insufficient'}
    result=recover_worker_research(state,checker)
    assert result['worker_source_recovery']['worker_or_retrieval_executed'] is False
    assert not result['evidence_gaps'] and state==original
