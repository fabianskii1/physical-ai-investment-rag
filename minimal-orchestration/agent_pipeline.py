"""최소 Orchestrator → Workers → Synthesizer → 보고서 파이프라인.

워커는 결과만 반환한다. 재조사가 필요한지 결정하고 오케스트레이터에
돌려보내는 노드는 신시사이저 하나이다. 모델 호출은 주입된 함수에 위임한다.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from state_schema import AgentState, ResearchTask
from criteria_registry import CRITERIA_REGISTRY, RESEARCH_CRITERIA, normalize_criterion
from criteria_coverage import (build_coverage, coverage_feedback, merge_analysis,
                               record_attempts, resolved_gaps)


ANALYSIS_KEYS = {
    "bigtech": "bigtech_analysis",
    "technology": "technology_analysis",
    "market": "market_competition_analysis",
}
RESEARCH_FIELDS = (*ANALYSIS_KEYS.values(), "source_checked_claims", "evidence_gaps", "evidence_status",
                   "coverage_by_criterion", "missing_criteria", "criterion_attempts",
                   "research_criteria_count", "attempted_criteria_count", "supported_criteria_count")
INVESTMENT_FIELDS = ("score_details", "total_score", "scorable_weight", "unknown_weight",
                     "unknown_items", "investment_decision", "decision_reason", "investment_case")


@dataclass(frozen=True)
class PipelineDependencies:
    """기존 RAG 함수와 새 계획·보고서 평가 함수를 연결하는 접점."""

    planner: Callable[[AgentState], list[dict]]
    worker: Callable[[ResearchTask, dict], dict]
    evidence_checker: Callable[[dict], dict]
    judge: Callable[[dict], dict]
    writer: Callable[[dict], dict]
    report_evaluator: Callable[[dict], dict]
    # 실제 임베딩 저장소의 생성·재사용 정보. 실행 기록용이며 점수의 근거는 아니다.
    index_info: dict | None = None


def initial_state(
    company_info: dict,
    document_catalog: dict,
    decision_policy: dict,
    max_rechecks: int = 4,
    investment_terms: dict | None = None,
) -> AgentState:
    """최초 조사 이후 추가 재검토를 3~5회 허용하는 실행 상태를 만든다."""
    if isinstance(max_rechecks, bool) or max_rechecks not in (3, 4, 5):
        raise ValueError("max_rechecks는 정수 3, 4, 5 중 하나여야 합니다")
    if not isinstance(max_rechecks, int):
        raise ValueError("max_rechecks는 정수여야 합니다")
    if (not isinstance(company_info, dict) or not isinstance(company_info.get("name"), str)
            or not company_info["name"].strip()):
        raise ValueError("company_info.name에 평가할 기업명을 입력하세요")
    if investment_terms is not None and not isinstance(investment_terms, dict):
        raise ValueError("investment_terms는 사전 또는 None이어야 합니다")
    return {
        "company_info": company_info, "document_catalog": document_catalog,
        "decision_policy": decision_policy, "investment_terms": investment_terms,
        "tasks": [], "worker_results": {},
        "research_result": {}, "investment_result": {}, "feedback": [],
        "recheck_count": 0, "max_rechecks": max_rechecks,
        "report_rewrite_count": 0, "max_report_rewrites": 2,
        "no_progress_rounds": 0, "last_progress_round": -1, "research_stop_reason": "",
        "final_report": "", "report_evaluation": {}, "trace_id": str(uuid4()),
        "step_count": 0, "status": "researching", "route": "judge",
    }


def flat_payload(state: AgentState) -> dict[str, Any]:
    """중첩 State를 기존 RAG의 평평한 함수 입력과 호환되게 펼친다."""
    # 기존 함수의 반환 필드만 펼친다. 중첩 결과에 섞인 오래된 보고서·회사명 등이
    # 최신 State를 덮어쓰거나, 이전 투자 결과가 새 근거를 덮어쓰지 못하게 한다.
    research, investment = state["research_result"], state["investment_result"]
    payload = {**state, **{key: investment[key] for key in INVESTMENT_FIELDS if key in investment},
               **{key: research[key] for key in RESEARCH_FIELDS if key in research}}
    # 재조사가 성공했어도 이전 보고서의 수정 요청은 Writer까지 전달해야 한다.
    payload["feedback"] = list(dict.fromkeys(
        state["feedback"] + research.get("report_revision_feedback", [])))
    return payload


def _validate_plan(tasks: list[dict]) -> list[ResearchTask]:
    """첫 버전은 역할당 한 작업으로 제한하되, 역할 선택·질문은 동적으로 한다."""
    if not isinstance(tasks, list) or len(tasks) > 3:
        raise ValueError("계획은 최대 3개 작업의 목록이어야 합니다")
    seen_ids, seen_workers = set(), set()
    for task in tasks:
        if not isinstance(task, dict) or task.get("worker") not in ANALYSIS_KEYS:
            raise ValueError("worker는 bigtech, technology, market 중 하나여야 합니다")
        if not isinstance(task.get("id"), str) or not task["id"].strip():
            raise ValueError("각 작업에 빈 문자열이 아닌 id가 필요합니다")
        if not isinstance(task.get("query"), str) or not task["query"].strip():
            raise ValueError("각 작업에 실제 검색할 query가 필요합니다")
        if task["id"] in seen_ids or task["worker"] in seen_workers:
            raise ValueError("같은 라운드의 작업 ID와 담당 워커는 중복될 수 없습니다")
        if "criteria" in task:
            criteria = task["criteria"]
            if not isinstance(criteria, list) or len(criteria) > 8:
                raise ValueError("작업별 criteria는 최대 8개 항목의 목록이어야 합니다")
            normalized = [normalize_criterion(item) for item in criteria]
            if any(item is None for item in normalized) or len(set(normalized)) != len(normalized):
                raise ValueError("criteria에 알려진 평가항목을 중복 없이 지정하세요")
            if any(CRITERIA_REGISTRY[item]["worker"] != task["worker"] for item in normalized):
                raise ValueError("criteria는 담당 워커의 자동 조사 항목이어야 합니다")
        seen_ids.add(task["id"])
        seen_workers.add(task["worker"])
    # 호출자가 전달한 계획 객체를 직접 수정하지 않는다.
    return [{**task, **({"criteria": [normalize_criterion(item) for item in task["criteria"]]}
                        if "criteria" in task else {})} for task in tasks]


def _verification_signature(state: AgentState, research: dict, catalog: dict) -> str:
    """검증 입력이 같으면 새 계획에서도 판정·인용 연결을 재사용한다.

    투자 판정·보고서 품질 검토 뒤 S로 돌아와도 원문과 작업이 같으면 이미 검증한
    주장 목록을 재사용한다. 원문 Judge의 새 표현 때문에 E-ID 연결이 달라지거나
    동일 원문에 비용을 다시 쓰는 문제를 막는다. 조사 횟수·질문·시도 이력은
    원문 판정 입력이 아니므로 서명에서 제외한다. 주장·원문이 바뀌면 다시 검증한다.
    """
    source_versions = {}
    for doc_id, metadata in catalog.items():
        location = metadata.get("source_path") or metadata.get("file_path")
        if not isinstance(location, str) or not location:
            continue
        path = Path(location)
        if not path.is_absolute():
            path = Path(__file__).resolve().parent / "rag" / path
        try:
            info = path.stat()
            source_versions[doc_id] = [info.st_size, info.st_mtime_ns]
        except OSError:
            source_versions[doc_id] = "unavailable"
    payload = {"company_info": state["company_info"], "document_catalog": catalog,
               "source_file_versions": source_versions,
               "investment_terms": state["investment_terms"],
               "analyses": {key: research.get(key, {}).get("claims", [])
                            for key in ANALYSIS_KEYS.values()}}
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _task_signature(task: dict) -> str:
    """작업 ID·항목 순서를 바꿔도 동일 질문과 자료의 완전 반복을 식별한다."""
    return json.dumps({"worker": task["worker"], "query": " ".join(task["query"].split()),
                       "criteria": sorted(task.get("criteria", [])),
                       "urls": sorted(task.get("urls", []))}, sort_keys=True, ensure_ascii=False)


def _evidence_keys(claims: list[dict]) -> set[str]:
    """표현을 바꾼 주장보다 실제 검증 원문·인용·항목의 증가를 측정한다."""
    keys = set()
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        source = claim.get("source", {})
        payload = {"criterion": claim.get("criterion_id") or claim.get("criterion"),
                   "source": source, "quote": " ".join(str(claim.get("evidence_quote", "")).split())}
        if not source and not payload["quote"]:
            payload["claim"] = claim.get("claim", "")  # 기존 테스트/어댑터의 최소 근거 호환
        keys.add(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str))
    return keys


def build_graph(deps: PipelineDependencies, checkpointer=None):
    """컴파일된 LangGraph 반환. checkpointer는 선택적으로 주입한다."""

    def orchestrator(state: AgentState) -> dict:
        research = {key: value for key, value in state["research_result"].items()
                    if key not in {"planning_error", "score_review_issues", "repeated_plan_tasks"}}
        try:
            tasks = _validate_plan(deps.planner(state))
            if state['recheck_count'] > 0:
                coverage = research.get('coverage_by_criterion', {})
                repair = {normalize_criterion(g.get('criterion_id')) for g in research.get('evidence_gaps', [])
                          if g.get('reason') not in {'unconfirmed_claim', 'absence_not_proven'}}
                filtered = []
                for task in tasks:
                    if not task.get('criteria'):
                        filtered.append(task)
                        continue
                    pending = [c for c in task['criteria'] if c in repair or
                               coverage.get(c, {}).get('source_review_complete') is not True]
                    if pending:
                        filtered.append({**task, 'criteria': pending})
                tasks = filtered
            previous = {_task_signature(value["task"]) for value in state["worker_results"].values()
                        if "analysis" in value and "error" not in value}
            repeated = [task for task in tasks if _task_signature(task) in previous]
            if repeated:
                research["repeated_plan_tasks"] = [task["id"] for task in repeated]
                tasks = [task for task in tasks if _task_signature(task) not in previous]
        except Exception as exc:
            # 계획 오류도 신시사이저가 판단하게 한다. 워커는 실행하지 않는다.
            tasks = []
            research["planning_error"] = f"{type(exc).__name__}: {exc}"
        return {
            "tasks": tasks, "research_result": research, "investment_result": {},
            "report_evaluation": {}, "status": "researching", "step_count": 1,
        }

    def dispatch(state: AgentState):
        # 빈 계획도 신시사이저로 보내므로 Send 0개에서 실행이 멈추지 않는다.
        return [Send("worker", {"task": task, "snapshot": state})
                for task in state["tasks"]] or "synthesizer"

    def worker(worker_input: dict) -> dict:
        task, state = worker_input["task"], worker_input["snapshot"]
        result_id = f"{state['recheck_count']}:{task['id']}"
        try:
            analysis = deps.worker(task, flat_payload(state))
            if not isinstance(analysis, dict):
                raise ValueError("워커 결과는 분석 사전이어야 합니다")
            result = {"task": task, "worker": task["worker"],
                      "recheck_count": state["recheck_count"], "analysis": analysis}
        except Exception as exc:
            # 부분 실패를 결과로 남긴다. 다른 병렬 워커의 성공은 소실되지 않는다.
            result = {"task": task, "worker": task["worker"],
                      "recheck_count": state["recheck_count"],
                      "error": f"{type(exc).__name__}: {exc}"}
        return {"worker_results": {result_id: result}, "step_count": 1}

    def synthesizer(state: AgentState) -> dict:
        research = dict(state["research_result"])
        catalog = dict(state["document_catalog"])
        issues = []
        if research.get("planning_error"):
            issues.append(f"조사 계획 실패: {research['planning_error']}")
        if research.get("repeated_plan_tasks"):
            issues.append("이미 수행한 질문·평가항목·원문 URL이 같은 작업은 다시 실행하지 않음: "
                          + ", ".join(research["repeated_plan_tasks"]))
        for task in state["tasks"]:
            result = state["worker_results"].get(f"{state['recheck_count']}:{task['id']}")
            if not result or "error" in result:
                error = result.get("error", "결과 미도착") if result else "결과 미도착"
                issues.append(f"{task['worker']} 작업 실패: {error}")
            else:
                analysis = result["analysis"]
                analysis_key = ANALYSIS_KEYS[task["worker"]]
                research[analysis_key] = merge_analysis(research.get(analysis_key, {}), analysis)
                record_attempts(research, task, state["recheck_count"], analysis)
                # 웹 원문을 저장한 PDF도 기존 문서처럼 doc_id·경로·페이지·URL로
                # 검증한다. 워커는 공유 카탈로그를 쓰지 않고 S에서만 병합한다.
                documents = analysis.get("source_documents", {})
                if not isinstance(documents, dict):
                    issues.append(f"{task['worker']}: 웹 원문 카탈로그 형식 오류")
                    documents = {}
                for doc_id, metadata in documents.items():
                    if not isinstance(doc_id, str) or not doc_id.strip() or not isinstance(metadata, dict):
                        issues.append(f"{task['worker']}: 웹 원문 문서 ID/메타데이터 오류")
                    elif doc_id in catalog and catalog[doc_id] != metadata:
                        # 같은 ID의 URL·파일·내용 metadata가 다르면 조용히 덮어쓰지 않는다.
                        collision = (f"{task['worker']}: 웹 원문 ID 충돌 {doc_id} "
                                     f"({metadata.get('source_url', '')})")
                        research["source_integrity_issues"] = list(dict.fromkeys(
                            research.get("source_integrity_issues", []) + [collision]))
                    else:
                        catalog[doc_id] = metadata
        issues.extend(research.get("source_integrity_issues", []))
        access_errors = []
        for role, key in ANALYSIS_KEYS.items():
            if key not in research:
                issues.append(f"{role} 관점의 분석 결과가 아직 없음")
            else:
                # 해결 전 오류는 최신 역할 분석에서 다시 읽는다. 성공한 재조사가
                # link_errors=[]를 반환하면 재검토 사유에서는 빠지고 기록은 보존된다.
                errors = research[key].get("link_errors", [])
                if isinstance(errors, list):
                    access_errors.extend(f"{role}: source link access failed {error}"
                                         for error in errors if isinstance(error, str) and error.strip())
        issues.extend(access_errors)
        research["source_access_errors"] = list(dict.fromkeys(
            research.get("source_access_errors", []) + access_errors))
        signature = _verification_signature(state, research, catalog)
        checked_fields = ("source_checked_claims", "evidence_gaps", "evidence_status")
        reuse_checked = (research.get("last_verified_signature") == signature
                         and all(key in research for key in checked_fields))
        try:
            if reuse_checked:
                # Judge의 score_details.evidence는 이 목록의 사전을 참조한다. 입력이
                # 그대로인 S 콜백에서 다시 검사하여 인용문·판정 사전을 교체하지 않는다.
                checked = {key: research[key] for key in checked_fields}
            else:
                checked = deps.evidence_checker(flat_payload({**state, "research_result": research,
                                                             "document_catalog": catalog}))
            if (not isinstance(checked, dict) or checked.get("evidence_status") not in {"scorable", "insufficient"}
                    or not isinstance(checked.get("source_checked_claims"), list)
                    or not isinstance(checked.get("evidence_gaps"), list)
                    or any(not isinstance(gap, dict) for gap in checked["evidence_gaps"])):
                raise ValueError("근거 검증 결과의 상태·목록 형식이 올바르지 않습니다")
            research["last_verified_signature"] = signature
            research["last_verified_round"] = state["recheck_count"]
            research["last_verified_task_ids"] = [task["id"] for task in state["tasks"]]
        except Exception as exc:
            # 검증 서비스 예외·잘못된 반환은 성공 snapshot으로 재사용하지 않는다.
            research.pop("last_verified_signature", None)
            checked = {"source_checked_claims": [], "evidence_gaps": [],
                       "evidence_status": "insufficient"}
            issues.append(f"원문 근거 검증 실패: {type(exc).__name__}: {exc}")
        # 이전의 미확인 주장은 새 원문 근거로 해결되었으면 현재 미달 목록에서 제거한다.
        # 반증이나 잘못된 인용은 숨기지 않고 계속 재검토 대상으로 남긴다.
        checked["evidence_gaps"] = resolved_gaps(research, checked["source_checked_claims"],
                                                checked["evidence_gaps"])
        research.update({key: checked[key] for key in RESEARCH_FIELDS if key in checked
                         and key not in ANALYSIS_KEYS.values()})
        if research.get('worker_source_recovery'):
            research['worker_source_recovery'] = {**research['worker_source_recovery'],
                'output_verified_claims': len(checked['source_checked_claims']),
                'validation_node': 'synthesizer'}
        coverage = build_coverage(research, checked["source_checked_claims"], state["investment_terms"])
        research.update(coverage)
        for role in ANALYSIS_KEYS:
            relevant = [item for item in coverage['coverage_by_criterion'].values() if item['worker'] == role]
            if relevant and all(item.get('source_review_complete') is True and
                    item['status'] in {'supported', 'reviewed_unavailable'} for item in relevant):
                issues = [issue for issue in issues if not issue.startswith(f'{role}: source link access failed ')]
        progress_count = state.get("no_progress_rounds", 0)
        progress_update = {}
        if state["recheck_count"] != state.get("last_progress_round", -1):
            previous = state["research_result"]
            new_evidence = _evidence_keys(checked["source_checked_claims"]) - _evidence_keys(
                previous.get("source_checked_claims", []))
            new_coverage = (coverage["supported_criteria_count"] > previous.get("supported_criteria_count", 0)
                            or coverage["attempted_criteria_count"] > previous.get("attempted_criteria_count", 0))
            new_role = any(key in research and key not in previous for key in ANALYSIS_KEYS.values())
            # 최초 조사와 처음 선택한 역할/항목은 무진전으로 세지 않는다.
            progress_count = (0 if state["recheck_count"] == 0 or new_evidence or new_coverage or new_role
                              else progress_count + 1)
            progress_update = {"no_progress_rounds": progress_count,
                               "last_progress_round": state["recheck_count"]}
        if state["decision_policy"].get("require_criteria_coverage"):
            # 기술 주장 하나로 scorable이어도 18개 평가항목 조사를 완료한 것은 아니다.
            issues.extend(coverage_feedback(coverage["coverage_by_criterion"]))
        if checked.get("evidence_status") != "scorable":
            issues.append("투자 검토에 필요한 핵심 제품·기술 근거가 부족함")
        for gap in checked.get("evidence_gaps", []):
            covered = coverage['coverage_by_criterion'].get(normalize_criterion(gap.get('criterion_id')), {})
            if (covered.get('status') == 'reviewed_unavailable'
                    and gap.get('reason') in {'unconfirmed_claim', 'absence_not_proven'}):
                continue
            issues.append(f"{gap.get('analysis', '근거')}: {gap.get('claim', '')} "
                          f"({gap.get('reason', '근거 부족')})")
        report_failed = state["report_evaluation"].get("passed") is False
        if report_failed:
            report_issues = state["report_evaluation"].get("issues", []) or ["보고서 품질 기준 미달"]
            research["report_revision_feedback"] = report_issues
            issues.extend(report_issues)
        score_issues = research.get("score_review_issues", [])
        issues.extend(score_issues)
        issues = list(dict.fromkeys(issues))
        update = {"research_result": research, "document_catalog": catalog,
                  "feedback": issues, "step_count": 1, **progress_update}
        all_roles_attempted = all(key in research for key in ANALYSIS_KEYS.values())
        stopped = (bool(state.get("research_stop_reason")) or (progress_count >= 2 and all_roles_attempted)
                   or state["decision_policy"].get("resume_report_only", False))
        if issues and stopped:
            stop_reason = state.get("research_stop_reason") or (
                "report_resume_only" if state["decision_policy"].get("resume_report_only") else "no_progress")
            update["research_stop_reason"] = stop_reason
            if stop_reason == "no_progress":
                update["feedback"] = [*issues, "연속 두 조사 라운드에서 검증 근거·지원 항목이 늘지 않아 재조사를 중단함. 미확인은 U로 유지함."]
        elif issues and state["recheck_count"] >= state["max_rechecks"]:
            update["research_stop_reason"] = "research_budget_exhausted"
        if issues and not stopped and state["recheck_count"] < state["max_rechecks"]:
            return {**update, "route": "replan",
                    "recheck_count": state["recheck_count"] + 1}
        if report_failed:
            # 한도까지 품질 미달이면 기존 보고서를 보존하고 실패 상태로 종료한다.
            return {**update, "route": "end", "status": "quality_failed"}
        if score_issues:
            # 마지막 투자 판정이 이미 존재한다. 재검토 한도 소진 후 판정을 반복하면
            # Judge→S 무한 반복이 생기므로 그대로 U 사유를 설명하는 Writer로 간다.
            return {**update, "route": "write", "status": "partial"}
        return {**update, "route": "judge", "status": "partial" if issues else "researching"}

    def judgement(state: AgentState) -> dict:
        return {"investment_result": deps.judge(flat_payload(state)), "step_count": 1,
                **({'report_evaluation':{},'report_draft':{},'report_section_lines':{}}
                   if state['report_evaluation'].get('action')=='regrade' else {})}

    def score_review(state: AgentState) -> dict:
        """원문이 있어도 등급 판단에 부족하면 S를 거쳐 항목별로 다시 조사한다."""
        if not state["decision_policy"].get("require_criteria_coverage"):
            return {}
        details = state["investment_result"].get("score_details", {})
        issues = []
        for criterion_id in RESEARCH_CRITERIA:
            item = CRITERIA_REGISTRY[criterion_id]
            assessment = details.get(item["label"], {}) if isinstance(details, dict) else {}
            if not isinstance(assessment, dict) or assessment.get("grade") is None:
                if state['research_result'].get('coverage_by_criterion', {}).get(criterion_id, {}).get('source_review_complete') is True:
                    # 배점 기준을 충족하지 못하는 것과 검색·추출 실패는 다르다.
                    # 읽을 수 있는 원문을 검토한 U를 강제로 양수로 만들지 않는다.
                    continue
                reason = assessment.get("reason", "투자 판정이 누락되었습니다.") if isinstance(assessment, dict) else "투자 판정 형식 오류"
                issues.append(f"[{item['worker']}/{criterion_id}] {item['label']} 등급 U: {reason} "
                              f"보완 조사: {item['question']}")
        return {"research_result": {**state["research_result"], "score_review_issues": issues},
                "step_count": 1}

    def writer(state: AgentState) -> dict:
        result=deps.writer(flat_payload(state))
        return {**{k:result[k] for k in ('final_report','report_draft','report_section_lines','report_fact_contract') if k in result},
                'step_count':1}

    def report_evaluator(state: AgentState) -> dict:
        try:
            evaluation = deps.report_evaluator(flat_payload(state))
            if not isinstance(evaluation.get("passed"), bool):
                raise ValueError("보고서 평가에 passed 불리언이 필요합니다")
            issues = evaluation.get("issues", [])
            if not isinstance(issues, list) or any(not isinstance(issue, str) for issue in issues):
                raise ValueError("보고서 평가 issues는 문자열 목록이어야 합니다")
            evaluation = {**evaluation, "issues": [issue.strip() for issue in issues if issue.strip()]}
            if evaluation["issues"]:
                # 미달 사유와 통과 플래그가 모순되면 미달로 처리한다.
                evaluation["passed"] = False
        except Exception as exc:
            evaluation = {"passed": False, "issues": [f"보고서 평가 실패: {type(exc).__name__}: {exc}"]}
        evaluation["action"] = (evaluation.get("action") if evaluation.get("action") in {"rewrite", "research", "regrade"}
                                else "research")
        status = state["status"]
        update = {}
        if not evaluation['passed'] and evaluation['action'] == 'regrade':
            count=state.get('report_regrade_count',0)
            if count >= 1:
                status='quality_failed'
                evaluation['regrade_budget_exhausted']=True
            else:
                update={'report_regrade_count':count+1,
                        'feedback':evaluation['issues'],
                        'research_result':{**state['research_result'], 'score_review_issues':[]}}
        if not evaluation["passed"] and evaluation["action"] == "rewrite":
            count, maximum = state.get("report_rewrite_count", 0), state.get("max_report_rewrites", 2)
            if count >= maximum:
                status = "quality_failed"
                evaluation["rewrite_budget_exhausted"] = True
            else:
                revision = evaluation["issues"] or ["보고서 품질 기준 미달: 기존 근거에 맞게 서술을 보완하세요."]
                update = {"report_rewrite_count": count + 1,
                          "feedback": list(dict.fromkeys(state["feedback"] + revision)),
                          "research_result": {**state["research_result"], "report_revision_feedback": revision}}
        if evaluation["passed"] and status != "partial":
            status = "completed"
        return {"report_evaluation": evaluation, "status": status, "step_count": 1, **update}

    def after_report(state: AgentState):
        if state["report_evaluation"]["passed"] or state["status"] == "quality_failed":
            return "end"
        return state['report_evaluation']['action'] if state['report_evaluation']['action'] in {'rewrite','regrade'} else 'research'

    graph = StateGraph(AgentState)
    for name, node in (("orchestrator", orchestrator), ("worker", worker),
                       ("synthesizer", synthesizer), ("judgement", judgement),
                       ("score_review", score_review),
                       ("writer", writer), ("report_evaluator", report_evaluator)):
        graph.add_node(name, node)
    graph.add_edge(START, "orchestrator")
    graph.add_conditional_edges("orchestrator", dispatch, ["worker", "synthesizer"])
    graph.add_edge("worker", "synthesizer")
    graph.add_conditional_edges("synthesizer", lambda s: s["route"],
                                {"replan": "orchestrator", "judge": "judgement", "write": "writer", "end": END})
    graph.add_edge("judgement", "score_review")
    graph.add_conditional_edges("score_review", lambda s: bool(s["research_result"].get("score_review_issues")),
                                {True: "synthesizer", False: "writer"})
    graph.add_edge("writer", "report_evaluator")
    graph.add_conditional_edges("report_evaluator", after_report,
                                {"end": END, "rewrite": "writer", "regrade": "judgement", "research": "synthesizer"})
    return graph.compile(checkpointer=checkpointer)
