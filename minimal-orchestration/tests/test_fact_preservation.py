from pathlib import Path
import re

from fact_preservation import data_fact_contract, preservation_issues
from report_renderer import render_report
from rag.PDF.pdf_loader import load_pdf
from rag.report import report


def test_unscored_data_collection_facts_are_preserved_with_actual_page_context(tmp_path):
    text='TAS\nTeleoperation Assist System\n비전문 작업자도 균일한 양팔 작업 데이터를 수집합니다.\n어시스트 기능으로 표준화된 학습 데이터를 확보합니다.'
    pdf=render_report(text,tmp_path/'source.pdf')
    page=load_pdf(pdf,'D','회사')[0].page_content
    quote=next(line for line in page.splitlines() if '표준화된' in line)
    state={'company_info':{'name':'회사'},'document_catalog':{'D':{
        'company':'회사','source_path':str(pdf),'page_count':1,'publisher':'회사 발표'}},
        'source_checked_claims':[{'claim':quote,'analysis':'technology_analysis',
            'criterion_id':'hard_to_copy_assets','source':{'doc_id':'D','page':1},'evidence_quote':quote}],
        'score_details':{}}
    result=report(state,writer=lambda _:{})
    assert result['report_fact_contract'][0]['source_excerpt']==page
    assert 'Teleoperation Assist System' in result['final_report'] and '[E1]' in result['final_report']
    assert '| 모방하기 어려운 자산 | U |' in result['final_report']
    assert not preservation_issues('TAS로 학습 데이터를 수집한다.',result['report_fact_contract'])
    assert preservation_issues('구체적인 자산은 미확인이다.',result['report_fact_contract'])


def test_goal_preservation_requires_original_numbers_and_explicit_target_label(tmp_path):
    text='The requirement was 84 seconds total, 37 seconds load time.'
    pdf=render_report(text,tmp_path/'goal.pdf')
    state={'company_info':{'name':'회사'},'document_catalog':{'D':{'company':'회사','source_path':str(pdf),'publisher':'회사'}},
        'source_checked_claims':[{'claim':text,'analysis':'technology_analysis','source_scope':'requirement',
             'source':{'doc_id':'D','page':1},'evidence_quote':text}]}
    contract=data_fact_contract(state)
    assert contract[0]['topic']=='performance_requirement'
    assert preservation_issues('84초와 37초를 달성했다.',contract)
    assert preservation_issues('목표와 실제 실측을 구분해야 한다.',contract)
    assert not preservation_issues('요구조건은 총 84초, 로딩 37초이며 달성 수치는 아니다.',contract)


def test_software_installation_requirements_are_not_robot_performance_targets(tmp_path):
    text='Requirements\nLinux x86_64 Ubuntu 24.04\nCompiler C++17\nCMake 3.14\nPython 3.12'
    pdf=render_report(text,tmp_path/'sdk.pdf')
    page=load_pdf(pdf,'D','회사')[0].page_content
    state={'company_info':{'name':'회사'},'document_catalog':{'D':{'company':'회사','source_path':str(pdf),'publisher':'회사'}},
        'source_checked_claims':[{'claim':page,'analysis':'technology_analysis','source_scope':'requirement',
             'source':{'doc_id':'D','page':1},'evidence_quote':page}]}
    assert not any(f['topic']=='performance_requirement' for f in data_fact_contract(state))
