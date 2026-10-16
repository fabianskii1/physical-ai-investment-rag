from copy import deepcopy

from criteria_coverage import build_coverage
from criteria_registry import criteria_for_worker
from tests.test_criteria_coverage import strict_harness
from worker_policy import load_worker_policy


def test_all_roles_have_runtime_markdown_policies_with_version():
    for role in ('bigtech', 'technology', 'market'):
        p = load_worker_policy(role)
        assert '완료 기준' in p['content'] and '재호출' in p['content']
        assert p['path'].endswith(f'{role}.md') and len(p['sha256']) == 64


def test_reviewed_unavailable_is_not_a_new_research_request_and_never_becomes_supported():
    key = 'customer_repurchase_expansion'
    research={'criterion_attempts':{key:[{'round':0}]},
        'bigtech_analysis':{'reviewed_criteria':{key:{'source_review_complete':True,'only_unverified':True}}}}
    x=build_coverage(research,[])
    c=x['coverage_by_criterion'][key]
    assert c['status']=='reviewed_unavailable' and not c['evidence_ids']
    assert key not in x['missing_criteria']


def test_all_reviewed_workers_do_not_repeat_just_because_grades_remain_unknown():
    h=strict_harness()
    worker=h.worker_fn
    def reviewed(task,state):
        result=worker(task,state)
        result['reviewed_criteria']={c:{'source_review_complete':True,'only_unverified':False} for c in task['criteria']}
        return result
    h.worker_fn=reviewed
    judge=h.judge
    def unknown(state):
        result=judge(state)
        for detail in result['score_details'].values():
            detail['grade']=None
            detail['reason']='회사 설명은 확인했으나 배점 기준의 독립 검증 자료는 미확인'
        return result
    h.judge=unknown
    final=h.run(3,{'require_criteria_coverage':True})
    assert final['recheck_count']==0
    assert len(final['worker_results'])==3
    assert final['report_evaluation']['passed'] is True


def test_only_unfinished_role_is_dispatched_again_even_if_planner_returns_all_three():
    h=strict_harness()
    original=h.worker_fn
    calls=[]
    h.planner_fn=lambda s:[{'id':role,'worker':role,'query':f"{role} 부족 항목 확인 {s['recheck_count']}",
                           'criteria':criteria_for_worker(role)} for role in ('bigtech','technology','market')]
    def worker(task,state):
        calls.append((state['recheck_count'],task['worker']))
        result=original(task,state)
        completed=task['worker']!='market' or state['recheck_count']>0
        if not completed:
            result['claims']=[]
        result['reviewed_criteria']={c:{'source_review_complete':completed,'only_unverified':False} for c in task['criteria']}
        return result
    h.worker_fn=worker
    final=h.run(3,{'require_criteria_coverage':True})
    assert final['recheck_count']==1
    assert calls.count((0,'technology'))==1 and calls.count((0,'bigtech'))==1
    assert [role for round_,role in calls if round_>0]==['market']


def test_report_grade_feedback_revisits_judge_once_without_workers_or_new_source_checks():
    h=strict_harness()
    h.evaluator_fn=lambda s: {'passed':h.evaluations>1,'issues':[] if h.evaluations>1 else ['채점 사유 범위 오류'],
                              'action':'regrade'}
    result=h.run(3,{'require_criteria_coverage':True})
    assert result['report_evaluation']['passed'] and result['report_regrade_count']==1
    assert len(h.calls)==3 and len(h.plans)==1 and h.evidence_rounds==1
    assert h.nodes.count('judgement')==2


def test_regrade_budget_cannot_form_an_infinite_loop():
    h=strict_harness()
    h.evaluator_fn=lambda s: {'passed':False,'issues':['채점 사유 미달'],'action':'regrade'}
    result=h.run(3,{'require_criteria_coverage':True})
    assert result['status']=='quality_failed' and result['report_regrade_count']==1
    assert len(h.calls)==3 and h.evaluations==2
