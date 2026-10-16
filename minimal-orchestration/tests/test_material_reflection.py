from copy import deepcopy
from combined_quality import material_fact_reflection_issues


def setup():
    text='TAS를 통해 학습 데이터를 수집하지만 보호 장벽은 별도 검증해야 한다.'
    payload={'report':'## 3 기업별 분석\n### MOTOMIND — 기술 분석\n'+text+' [MM-E1]\n',
        'company_reports':[{'company':'MOTOMIND','paragraphs':[]}],
        'companies':[{'company':'MOTOMIND','prefix':'MM','report_fact_contract':[
            {'topic':'teleoperation','label':'학습 데이터 수집','evidence_id':'E1'}]}]}
    reflection=[{'company':'MOTOMIND','required_fact_reflection':[{
        'topic':'teleoperation','source_evidence_id':'MM-E1','final_paragraph_id':'P3',
        'final_excerpt':text,'preserved':True,'reason':'구체 수단과 한계가 보존됐다.'}]}]
    return payload,reflection


def test_material_preservation_needs_real_same_company_quote_and_original_evidence_id():
    payload,reflection=setup()
    assert material_fact_reflection_issues(payload,reflection)==([],[])
    for field,value in [('source_evidence_id','RB-E1'),('final_paragraph_id','P999'),('final_excerpt','실제로 존재하지 않는 본문 발췌문입니다.')]:
        altered=deepcopy(reflection);altered[0]['required_fact_reflection'][0][field]=value
        content,proof=material_fact_reflection_issues(payload,altered)
        assert not content and proof


def test_missing_review_is_a_judge_problem_and_actual_omission_is_content_feedback():
    payload,reflection=setup()
    content,proof=material_fact_reflection_issues(payload,[{'company':'MOTOMIND','required_fact_reflection':[]}])
    assert proof and not content
    reflection[0]['required_fact_reflection'][0]['preserved']=False
    content,proof=material_fact_reflection_issues(payload,reflection)
    assert content and not proof
