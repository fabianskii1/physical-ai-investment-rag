"""보고서 재작성과 자료 보완을 분리하고 무진전 재조사를 제한한다."""

from agent_pipeline import PipelineDependencies, build_graph, initial_state
from tests.test_pipeline import Harness, ROLES, plan_for


def test_rewrite_preserves_evidence_and_scores_without_research_or_regrading():
    h = Harness()
    snapshots = []
    h.judge = lambda state: {"total_score": 22.5, "unknown_items": ["매출"],
                             "investment_case": {"thesis": "추가 실사 후 검토"}}
    original_writer = h.writer

    def writer(state):
        snapshots.append({key: state[key] for key in
                          ("research_result", "investment_result", "trace_id", "feedback")})
        return original_writer(state)

    h.writer = writer
    h.evaluator_fn = lambda state: {"passed": h.evaluations > 1, "action": "rewrite",
                                    "issues": [] if h.evaluations > 1 else ["홍보 표현을 근거의 범위로 제한하세요."]}
    result = h.run()
    assert len(h.plans) == 1
    assert len(h.calls) == 3
    assert h.evidence_rounds == 1
    assert h.nodes.count("judgement") == 1
    assert h.nodes[-3:] == ["report_evaluator", "writer", "report_evaluator"]
    assert result["recheck_count"] == 0
    assert result["report_rewrite_count"] == 1
    assert snapshots[0]["investment_result"] == snapshots[1]["investment_result"]
    assert snapshots[0]["research_result"]["source_checked_claims"] == snapshots[1]["research_result"]["source_checked_claims"]
    assert snapshots[0]["trace_id"] == snapshots[1]["trace_id"]
    assert "홍보 표현을 근거의 범위로 제한하세요." in snapshots[1]["feedback"]
    assert result["investment_result"]["unknown_items"] == ["매출"]


def test_rewrite_budget_exhaustion_keeps_failed_draft_and_never_claims_quality_pass():
    h = Harness()
    h.evaluator_fn = lambda state: {"passed": False, "action": "rewrite", "issues": ["과장 표현이 남음"]}
    result = h.run()
    assert h.evaluations == 3  # 최초 보고서 + 최대 두 재작성
    assert h.nodes.count("writer") == 3
    assert len(h.plans) == 1
    assert result["recheck_count"] == 0
    assert result["report_rewrite_count"] == 2
    assert result["status"] == "quality_failed"
    assert result["report_evaluation"]["passed"] is False
    assert result["report_evaluation"]["rewrite_budget_exhausted"] is True
    assert result["final_report"]


def test_two_no_progress_rounds_stop_research_and_reuse_same_verification():
    h = Harness()
    h.planner_fn = lambda state: plan_for(query=f"재조사 표현 {state['recheck_count']}")
    h.worker_fn = lambda task, state: {"claims": [{"claim": "같은 미확인 주장"}]}
    h.checker_fn = lambda state: {"source_checked_claims": [], "evidence_gaps": [],
                                  "evidence_status": "insufficient"}
    result = h.run(5)
    assert result["recheck_count"] == 2
    assert result["no_progress_rounds"] == 2
    assert result["research_stop_reason"] == "no_progress"
    assert result["status"] == "partial"
    assert h.evidence_rounds == 1
    assert len(h.plans) == 3
    assert result["investment_result"]["scorecard"]["unknown"] is True
    assert "미확인은 U로 유지" in " ".join(result["feedback"])


def test_exact_task_repeat_is_blocked_even_with_new_ids_and_query_whitespace():
    h = Harness()
    h.planner_fn = lambda state: [{"id": f"{role}-{state['recheck_count']}", "worker": role,
                                  "query": "기본  조사" if state["recheck_count"] else "기본 조사",
                                  "urls": ["https://example.com/evidence"]} for role in ROLES]
    h.checker_fn = lambda state: {"source_checked_claims": [], "evidence_gaps": [],
                                  "evidence_status": "insufficient"}
    result = h.run(5)
    assert len(h.calls) == 3
    assert result["recheck_count"] == 2
    assert result["status"] == "partial"
    assert len(result["research_result"]["repeated_plan_tasks"]) == 3


def test_initially_unselected_roles_still_get_a_research_opportunity():
    h = Harness()
    h.planner_fn = lambda state: plan_for(
        ("technology",) if state["recheck_count"] < 3 else ("bigtech", "market"),
        query=f"조사 {state['recheck_count']}")
    h.worker_fn = lambda task, state: {"claims": []}
    h.checker_fn = lambda state: {"source_checked_claims": [], "evidence_gaps": [],
                                  "evidence_status": "insufficient"}
    result = h.run(3)
    assert {task["worker"] for _, task in h.calls} == set(ROLES)
    assert result["recheck_count"] == 3
    assert result["status"] == "partial"


def test_unknown_feedback_action_defaults_to_research_and_changed_claims_are_checked():
    h = Harness()
    h.planner_fn = lambda state: plan_for(query=f"새 근거 조사 {state['recheck_count']}")
    h.evaluator_fn = lambda state: {"passed": h.evaluations > 1, "action": "unknown",
                                    "issues": [] if h.evaluations > 1 else ["새 고객 원문을 확보하세요."]}
    result = h.run()
    index = h.nodes.index("report_evaluator")
    assert h.nodes[index + 1:index + 3] == ["synthesizer", "orchestrator"]
    assert len(h.plans) == 2
    assert h.evidence_rounds == 2
    assert result["report_evaluation"]["action"] == "research"
    assert result["report_rewrite_count"] == 0


def test_report_only_resume_does_not_restart_research_even_when_recheck_budget_remains():
    h = Harness()
    h.evaluator_fn = lambda state: {"passed": False, "action": "research", "issues": ["새 자료 필요"]}
    state = initial_state({"name": "테스트 기업"}, {}, {"resume_report_only": True}, max_rechecks=5)
    # 일반 최초 조사도 marker를 존중하며 보고서 이후 재조사로 확장하지 않는다.
    graph = build_graph(PipelineDependencies(h.planner, h.worker, h.checker, h.judge, h.writer, h.evaluator))
    result = graph.invoke(state, {"recursion_limit": 100})
    assert len(h.plans) == 1
    assert result["recheck_count"] == 0
    assert result["status"] == "quality_failed"


def test_flat_writer_payload_includes_investment_case():
    from agent_pipeline import flat_payload

    state = initial_state({"name": "기업"}, {}, {})
    state["investment_result"] = {"investment_case": {"thesis": "현장 근거를 검토함"}}
    assert flat_payload(state)["investment_case"]["thesis"] == "현장 근거를 검토함"


def test_verification_cache_invalidates_when_original_file_changes(tmp_path):
    from agent_pipeline import _verification_signature

    source = tmp_path / "original.pdf"
    source.write_bytes(b"original content")
    state = initial_state({"name": "기업"}, {"DOC": {"source_path": str(source)}}, {})
    before = _verification_signature(state, {}, state["document_catalog"])
    source.write_bytes(b"new original content with different source facts")
    after = _verification_signature(state, {}, state["document_catalog"])
    assert before != after
