"""항목별 근거 수집·누적 및 점수 재검토를 모델 없이 검증한다."""

import unittest

from agent_pipeline import _validate_plan
from criteria_coverage import (build_coverage, merge_analysis, record_attempts,
                               resolved_gaps)
from criteria_registry import (CRITERIA_REGISTRY, MANUAL_CRITERIA, RESEARCH_CRITERIA,
                               criteria_for_worker, normalize_criterion)
from tests.test_pipeline import Harness, ROLES
from rag.report import report


def claim(criterion_id, text=None, status="confirmed"):
    return {"criterion_id": criterion_id, "claim": text or f"{criterion_id} 원문 사실",
            "status": status, "source_refs": [{"doc_id": "TEST", "page": 1}]}


def strict_harness():
    h = Harness()
    h.planner_fn = lambda s: [{"id": role, "worker": role, "query": f"{role} 항목별 조사",
                               "criteria": criteria_for_worker(role)} for role in ROLES]
    h.worker_fn = lambda task, s: {"claims": [claim(key) for key in task["criteria"]]}

    def checker(s):
        checked = []
        for role, key in (("bigtech", "bigtech_analysis"), ("technology", "technology_analysis"),
                          ("market", "market_competition_analysis")):
            checked.extend({**item, "analysis": key, "source": item["source_refs"][0],
                            "evidence_quote": item["claim"]}
                           for item in s.get(key, {}).get("claims", []) if item["status"] == "confirmed")
        return {"source_checked_claims": checked, "evidence_gaps": [], "evidence_status": "scorable"}

    h.checker_fn = checker
    h.judge = lambda s: {"score_details": {
        item["label"]: {"grade": 4 if item["researchable"] else None, "reason": "검증 근거 평가"}
        for item in CRITERIA_REGISTRY.values()}}
    return h


class CriteriaCoverageTests(unittest.TestCase):
    def test_registry_reuses_all_21_labels_and_100_existing_points(self):
        self.assertEqual(len(CRITERIA_REGISTRY), 21)
        self.assertEqual(sum(item["weight"] for item in CRITERIA_REGISTRY.values()), 100)
        self.assertEqual(len(RESEARCH_CRITERIA), 18)
        self.assertEqual(len(MANUAL_CRITERIA), 3)
        self.assertEqual([len(criteria_for_worker(role)) for role in ROLES], [4, 8, 6])
        self.assertEqual(normalize_criterion("핵심 작업 성능"), "core_task_performance")

    def test_task_validation_normalizes_labels_and_rejects_wrong_worker_or_manual_terms(self):
        tasks = [{"id": "technology", "worker": "technology", "query": "작업 성능",
                  "criteria": ["핵심 작업 성능"]}]
        self.assertEqual(_validate_plan(tasks)[0]["criteria"], ["core_task_performance"])
        self.assertEqual(tasks[0]["criteria"], ["핵심 작업 성능"])
        for key in ("paid_sales_delivery", "equity_rights", "not_a_criterion"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                _validate_plan([{**tasks[0], "criteria": [key]}])

    def test_claims_accumulate_without_duplicate_and_latest_link_errors_replace_old(self):
        previous = {"claims": [claim("core_task_performance")], "link_errors": ["old 403"]}
        current = {"claims": [claim("core_task_performance"), claim("field_reliability_safety")],
                   "link_errors": []}
        result = merge_analysis(previous, current)
        self.assertEqual(len(result["claims"]), 2)
        self.assertEqual(result["link_errors"], [])
        self.assertEqual(len(previous["claims"]), 1)

    def test_supported_unknown_and_not_searched_are_distinct_and_manual_missing_is_no_research_gap(self):
        research = {}
        task = {"id": "tech", "worker": "technology", "query": "실측 안전성",
                "criteria": ["core_task_performance", "field_reliability_safety"]}
        record_attempts(research, task, 0, {"claims": []})
        record_attempts(research, task, 0, {"claims": []})
        result = build_coverage(research, [claim("core_task_performance")])
        coverage = result["coverage_by_criterion"]
        self.assertEqual(coverage["core_task_performance"]["status"], "supported")
        self.assertEqual(coverage["core_task_performance"]["evidence_ids"], ["E1"])
        self.assertEqual(coverage["field_reliability_safety"]["status"], "searched_unverified")
        self.assertEqual(coverage["founder_expertise"]["status"], "not_searched")
        self.assertEqual(coverage["equity_rights"]["status"], "manual_missing")
        self.assertEqual(coverage["field_reliability_safety"]["attempt_count"], 1)
        self.assertEqual(result["attempted_criteria_count"], 2)
        self.assertFalse(set(MANUAL_CRITERIA) & set(result["missing_criteria"]))

    def test_resolved_unknown_is_removed_but_contradiction_or_bad_quote_remains(self):
        old = claim("core_task_performance", "성능을 확인하지 못했다", "unverified")
        research = {"technology_analysis": {"claims": [old]}}
        gaps = [{"analysis": "technology_analysis", "claim": old["claim"], "reason": reason}
                for reason in ("unconfirmed_claim", "contradicted_claim", "invalid_quote")]
        result = resolved_gaps(research, [claim("core_task_performance")], gaps)
        self.assertEqual([item["reason"] for item in result], ["contradicted_claim", "invalid_quote"])
        self.assertEqual(result[0]["criterion_id"], "core_task_performance")

    def test_one_supported_technology_claim_cannot_complete_18_item_research(self):
        h = strict_harness()
        h.worker_fn = lambda task, s: {"claims": [claim("core_task_performance")]
                                      if task["worker"] == "technology" else []}
        result = h.run(3, {"require_criteria_coverage": True})
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["recheck_count"], 2)
        self.assertEqual(result["research_result"]["supported_criteria_count"], 1)
        self.assertEqual(len(result["research_result"]["missing_criteria"]), 17)
        self.assertIn("실제 접근 가능한 시장", " ".join(result["feedback"]))

    def test_all_18_supported_items_pass_while_unprovided_terms_remain_manual_unknown(self):
        result = strict_harness().run(decision_policy={"require_criteria_coverage": True})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["recheck_count"], 0)
        self.assertEqual(result["research_result"]["supported_criteria_count"], 18)
        self.assertEqual(result["research_result"]["attempted_criteria_count"], 18)
        self.assertEqual(result["research_result"]["missing_criteria"], [])

    def test_judge_unknown_returns_to_synthesizer_then_replans_target_without_losing_old_claims(self):
        h = strict_harness()
        h.planner_fn = lambda s: ([{"id": role, "worker": role, "query": "초기 조사",
                                   "criteria": criteria_for_worker(role)} for role in ROLES]
                                 if s["recheck_count"] == 0 else [{"id": "tech-detail", "worker": "technology",
                                   "query": "반복 작업의 실측 성능 조건 확인", "criteria": ["core_task_performance"]}])
        original_worker = h.worker_fn
        h.worker_fn = lambda task, s: (original_worker(task, s) if s["recheck_count"] == 0 else {
            "claims": [claim("core_task_performance", "측정된 반복 작업 성능 보완")]})
        original_judge = h.judge

        def judge(s):
            h.events.append("judgement")
            result = original_judge(s)
            if s["recheck_count"] == 0:
                result["score_details"]["핵심 작업 성능"] = {"grade": None, "reason": "작업 환경 조건 부족"}
            return result

        h.judge = judge
        result = h.run(decision_policy={"require_criteria_coverage": True})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["recheck_count"], 1)
        first_judgement = h.nodes.index("judgement")
        self.assertEqual(h.nodes[first_judgement + 1:first_judgement + 4],
                         ["score_review", "synthesizer", "orchestrator"])
        self.assertIn("작업 환경 조건 부족", " ".join(h.plans[1]["feedback"]))
        self.assertEqual(len(result["research_result"]["technology_analysis"]["claims"]), 9)
        self.assertEqual(len(result["research_result"]["source_checked_claims"]), 19)
        self.assertEqual(result["research_result"]["criterion_attempts"]["core_task_performance"][1]["round"], 1)

    def test_persistent_unknown_score_ends_after_shared_budget_and_preserves_quality_evaluation(self):
        h = strict_harness()
        original_judge = h.judge

        def judge(s):
            result = original_judge(s)
            result["score_details"]["현장 안정성·안전성"] = {"grade": None, "reason": "독립 안전성 검증 부재"}
            return result

        h.judge = judge
        result = h.run(3, {"require_criteria_coverage": True})
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["recheck_count"], 2)
        self.assertEqual(len(h.plans), 3)
        self.assertEqual(h.evaluations, 1)
        self.assertTrue(result["report_evaluation"]["passed"])
        self.assertIn("독립 안전성 검증 부재", " ".join(result["feedback"]))

    def test_score_review_reuses_verification_snapshot_so_actual_report_keeps_score_evidence_links(self):
        h = strict_harness()
        original_checker = h.checker_fn
        versions = []

        def checker(s):
            result = original_checker(s)
            version = len(versions) + 1
            versions.append(version)
            # 같은 주장에도 재호출 시 LLM의 인용문·설명이 달라질 수 있다.
            # 판정 후 S 콜백이 이를 교체하면 기존 report.py의 사전 비교 인용이 끊긴다.
            for item in result["source_checked_claims"]:
                item["evidence_quote"] = f"원문 인용 {version}"
                item["evidence_reason"] = f"지지 판정 설명 {version}"
            return result

        def judge(s):
            details = {}
            for key, item in CRITERIA_REGISTRY.items():
                details[item["label"]] = {
                    "grade": 4 if item["researchable"] and key != "field_reliability_safety" else None,
                    "reason": "반복 작업 안전성 근거 추가 필요" if key == "field_reliability_safety" else "원문 확인",
                    "evidence": [claim for claim in s["source_checked_claims"]
                                 if claim.get("criterion_id") == key],
                }
            return {"score_details": details}

        h.checker_fn, h.judge = checker, judge
        # 모의 글쓰기만 주입한다. 점수·근거 사전 연결 검사는 실제 보고서 함수다.
        h.writer = lambda s: report(s, writer=lambda payload: {})
        result = h.run(3, {"require_criteria_coverage": True})
        self.assertEqual(result["status"], "partial")
        self.assertEqual(versions, [1])  # 새 원문·주장이 없으면 새 라운드도 동일 검증 재사용
        self.assertEqual(h.evidence_rounds, 1)
        self.assertEqual(result["research_result"]["last_verified_round"], 2)
        self.assertIn("85점", result["final_report"])
        for detail in result["investment_result"]["score_details"].values():
            for source in detail["evidence"]:
                self.assertIn(source, result["research_result"]["source_checked_claims"])

    def test_report_evaluation_callback_reuses_snapshot_but_new_round_reverifies(self):
        h = Harness()
        h.planner_fn = lambda s: [{"id": role, "worker": role, "query": f"추가 조사 {s['recheck_count']} {role}"}
                                  for role in ROLES]
        h.evaluator_fn = lambda s: {"passed": h.evaluations > 1,
                                    "issues": [] if h.evaluations > 1 else ["보고서 설명 보완"]}
        result = h.run()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(h.evidence_rounds, 2)
        self.assertEqual(h.nodes.count("synthesizer"), 3)
        self.assertEqual(result["research_result"]["last_verified_round"], 1)


if __name__ == "__main__":
    unittest.main()
