from copy import deepcopy
from unittest.mock import Mock

from langchain_core.documents import Document
import pytest

from evidence_text import exact_quote_span
from task_evidence import TaskEvidenceCollector, key_page_numbers
from report_renderer import render_report


def test_low_rank_project_body_is_added_from_catalog_actual_pdf_not_catalog_summary(tmp_path):
    quote='회사 발표에 따르면 연구 기관에 양팔로봇을 납품했습니다.'
    path=render_report('# 프로젝트\n'+quote,tmp_path/'projects.pdf')
    catalog={'MM-04':{'company':'MOTOMIND','source_path':str(path),'page_count':1,'key_pages':'1',
                        'related_scorecard_items':'제품화·사업 실행력','notes':'실제 PDF에 없는 카탈로그 설명'}}
    before=deepcopy(catalog)
    footer=Document(page_content='CONTACT 문의 메일',metadata={'doc_id':'MM-01','page':6,'chunk_index':0})
    search=Mock(return_value=[footer])
    task={'query':'개발과 납품 사례를 재확인'}
    collector=TaskEvidenceCollector('MOTOMIND',task,['productization_execution'],catalog,search)
    docs=collector.collect('MOTOMIND',4)
    assert docs[0].metadata['doc_id']=='MM-04' and quote in docs[0].page_content
    assert '카탈로그 설명' not in docs[0].page_content and catalog==before
    assert search.call_args_list[-1].args[1]==task['query']
    assert collector.audit['selected_pages'][0]['method']=='catalog_key_page'


def test_anchor_never_crosses_company_scope_or_invents_missing_key_page(tmp_path):
    path=render_report('# 기업 자료\n확인 가능한 회사 제품의 상세 설명입니다.',tmp_path/'source.pdf')
    catalog={'RB':{'company':'ROBROS','source_path':str(path),'key_pages':'1-3;99','related_scorecard_items':'제품화·사업 실행력'}}
    collector=TaskEvidenceCollector('MOTOMIND',{'query':'제품화'},['productization_execution'],catalog,lambda *_:[])
    assert collector.collect('MOTOMIND',4)==[]
    assert key_page_numbers('1-3;99',1)==[1]


def test_whitespace_only_quote_alignment_returns_real_pdf_span_and_rejects_changed_fact():
    page='Stick\nVentures, HB Investment, and Lotte Ventures have invested in the c\nompany.'
    quote='Stick Ventures, HB Investment, and Lotte Ventures have invested in the company.'
    actual=exact_quote_span(page,quote)
    assert actual==page
    assert exact_quote_span(page,quote.replace('Lotte','Samsung')) is None
    assert exact_quote_span('The total investment was $10 million.','The total investment was $100 million.') is None
    assert exact_quote_span(page,'have invested ... company') is None


def test_aligned_quote_does_not_promote_semantically_partial_claim(tmp_path):
    from rag.workflow import check_evidence
    quote='로봇은 현재 한 연구기관에 납품되었다.'
    path=render_report('# 회사 자료\n'+quote,tmp_path/'source.pdf')
    state={'company_info':{'name':'MOTOMIND'},'document_catalog':{'D':{'source_path':str(path),'page_count':1}},
           'technology_analysis':{'claims':[{'claim':'로봇은 두 연구기관에 납품되었다.',
                'status':'confirmed','criterion_id':'productization_execution','source_refs':[{'doc_id':'D','page':1}]}]}}
    result=check_evidence(state,judge=lambda *_:{'verdict':'partial','quote':quote,
        'all_core_facts_supported':False,'reason':'한 기관뿐이다'})
    assert not result['source_checked_claims']
    assert result['evidence_gaps'][0]['reason']=='partial_support'


def test_production_catalog_preserves_key_pages_and_evaluation_mapping_for_actual_project_pdf():
    from pathlib import Path
    from legacy_adapter import load_project_catalog
    catalog=load_project_catalog(Path(__file__).resolve().parents[1]/'rag/data')
    assert catalog['MM-04']['key_pages']=='2;3;4'
    assert '제품화·사업 실행력' in catalog['MM-04']['related_scorecard_items']
    collector=TaskEvidenceCollector('MOTOMIND',{'query':'제품화 사례'},['productization_execution'],catalog,lambda *_:[])
    docs=collector.collect('MOTOMIND',4)
    assert any(d.metadata['doc_id']=='MM-04' and d.metadata['page']==3 and '납품' in d.page_content for d in docs)
    outcome=collector.reviewed_criteria([{'criterion_id':'productization_execution','status':'confirmed'}])
    assert outcome['productization_execution']['source_review_complete'] is True
