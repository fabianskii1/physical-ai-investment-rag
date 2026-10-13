"""실제 planner 어댑터의 선택을 보존하고 coverage로 후속 조사를 배분한다."""

from unittest.mock import Mock

import pytest

import legacy_adapter
from agent_pipeline import PipelineDependencies, _validate_plan, build_graph, initial_state
from criteria_registry import CRITERIA_REGISTRY, RESEARCH_CRITERIA, criteria_for_worker


@pytest.fixture
def planner_dependencies(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "offline-planner-test")
    monkeypatch.setattr("link_sources.load_link_registry", lambda folder: {"Figure AI": {}})
    monkeypatch.setattr(legacy_adapter, "_catalog_records", lambda *args: [])
    initialize = Mock()
    monkeypatch.setattr(legacy_adapter, "initialize", initialize)
    request = Mock()
    monkeypatch.setattr(legacy_adapter, "_structured_request", request)
    deps = legacy_adapter.real_dependencies(com_data_dir=tmp_path, cache_dir=tmp_path / "sources")
    initialize.assert_called_once()
    return deps, request


def state():
    return initial_state({"name": "Figure AI", "research_input": {"source_leads": []}},
                         {}, {"require_criteria_coverage": True}, max_rechecks=3)


def task(role, criteria):
    return {"id": role, "worker": role, "query": f"Figure AI {role} 원문 확인", "criteria": criteria}


@pytest.mark.parametrize("selected", [
    [task("technology", ["core_task_performance"])],
    [task("technology", ["core_task_performance"]), task("market", ["accessible_market"])],
    [],
])
def test_initial_plan_preserves_selected_roles_and_criteria(planner_dependencies, selected):
    deps, request = planner_dependencies
    request.return_value = legacy_adapter.PlannedTasks(tasks=selected)

    tasks = _validate_plan(deps.planner(state()))

    assert [entry["worker"] for entry in tasks] == [entry["worker"] for entry in selected]
    assert [entry["criteria"] for entry in tasks] == [entry["criteria"] for entry in selected]
    assert request.call_args.args[0] is legacy_adapter.PlannedTasks
    payload = request.call_args.args[2]
    assert payload["missing_criteria"] == list(RESEARCH_CRITERIA)
    assert payload["previous_tasks"] == []


def test_selected_criteria_are_normalized_without_adding_other_roles(planner_dependencies):
    deps, request = planner_dependencies
    request.return_value = legacy_adapter.PlannedTasks(tasks=[task("technology", [
        "핵심 작업 성능", "core_task_performance", "accessible_market", "equity_rights", "unknown",
    ])])

    tasks = _validate_plan(deps.planner(state()))

    assert len(tasks) == 1
    assert tasks[0]["criteria"] == ["core_task_performance"]


def test_synthesizer_returns_unselected_coverage_to_dynamic_followup_plan(planner_dependencies):
    deps, request = planner_dependencies
    request.side_effect = [
        legacy_adapter.PlannedTasks(tasks=[task("technology", criteria_for_worker("technology"))]),
        legacy_adapter.PlannedTasks(tasks=[task(role, criteria_for_worker(role)) for role in ("bigtech", "market")]),
    ]
    worker_calls = []

    def worker(selected, payload):
        worker_calls.append((payload["recheck_count"], selected["worker"]))
        return {"claims": [{"criterion_id": key, "claim": f"{key} 원문 사실", "status": "confirmed"}
                           for key in selected["criteria"]], "attempted_criteria": selected["criteria"]}

    def check(payload):
        checked = []
        for key in ("technology_analysis", "bigtech_analysis", "market_competition_analysis"):
            checked.extend({**claim, "analysis": key} for claim in payload.get(key, {}).get("claims", []))
        return {"source_checked_claims": checked, "evidence_gaps": [], "evidence_status": "scorable"}

    def judge(payload):
        return {"score_details": {item["label"]: {"grade": 4 if item["researchable"] else None,
                                                  "reason": "검증 근거의 모의 판정"}
                                  for item in CRITERIA_REGISTRY.values()}}

    graph = build_graph(PipelineDependencies(deps.planner, worker, check, judge,
                        lambda payload: {"final_report": "# 모의 보고서"},
                        lambda payload: {"passed": True, "issues": []}))
    result = graph.invoke(state(), {"recursion_limit": 100})

    assert worker_calls[0] == (0, "technology")
    assert set(worker_calls[1:]) == {(1, "bigtech"), (1, "market")}
    assert request.call_count == 2
    followup = request.call_args_list[1].args[2]
    expected_missing = set(criteria_for_worker("bigtech") + criteria_for_worker("market"))
    assert set(followup["missing_criteria"]) == expected_missing
    assert all(followup["criterion_coverage"][key]["status"] == "not_searched" for key in expected_missing)
    assert {entry["worker"] for entry in followup["previous_tasks"]} == {"technology"}
    assert "bigtech 관점의 분석 결과가 아직 없음" in followup["feedback"]
    assert "market 관점의 분석 결과가 아직 없음" in followup["feedback"]
    assert result["research_result"]["attempted_criteria_count"] == 18
    assert result["research_result"]["supported_criteria_count"] == 18
    assert result["research_result"]["missing_criteria"] == []
    assert len(result["worker_results"]) == 3
    assert result["recheck_count"] == 1
    assert result["status"] == "completed"
