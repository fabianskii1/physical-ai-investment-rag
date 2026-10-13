"""모델·네트워크 없이 실제 LangGraph의 분기와 반복 상한을 검증한다."""

import unittest

from agent_pipeline import PipelineDependencies, build_graph, flat_payload, initial_state
from state_schema import merge_worker_results


ROLES = ("bigtech", "technology", "market")


def plan_for(roles=ROLES, query="기본 조사"):
    return [{"id": role, "worker": role, "query": f"{role}: {query}"} for role in roles]


class Harness:
    def __init__(self):
        self.plans, self.calls, self.events, self.nodes = [], [], [], []
        self.evidence_rounds, self.evaluations = 0, 0
        self.planner_fn = lambda state: plan_for()
        self.worker_fn = lambda task, state: {"claims": [{"claim": task["query"]}]}
        self.checker_fn = lambda state: {
            "source_checked_claims": [{"claim": "검증된 주장"}],
            "evidence_gaps": [], "evidence_status": "scorable",
        }
        self.evaluator_fn = lambda state: {"passed": True, "issues": []}

    def planner(self, state):
        self.events.append("orchestrator")
        self.plans.append({"round": state["recheck_count"], "feedback": list(state["feedback"])})
        return self.planner_fn(state)

    def worker(self, task, state):
        self.events.append(f"worker:{task['worker']}")
        self.calls.append((state["recheck_count"], dict(task)))
        return self.worker_fn(task, state)

    def checker(self, state):
        self.events.append("synthesizer")
        self.evidence_rounds += 1
        return self.checker_fn(state)

    def judge(self, state):
        self.events.append("judgement")
        return {"scorecard": {"unknown": state["evidence_status"] != "scorable"}}

    def writer(self, state):
        self.events.append("writer")
        return {"final_report": "# 기업 보고서\nU: 근거 미확인\n"}

    def evaluator(self, state):
        self.events.append("report_evaluator")
        self.evaluations += 1
        return self.evaluator_fn(state)

    def run(self, max_rechecks=4, decision_policy=None):
        deps = PipelineDependencies(self.planner, self.worker, self.checker,
                                    self.judge, self.writer, self.evaluator)
        result = None
        for mode, update in build_graph(deps).stream(
                initial_state({"name": "테스트 기업"}, {}, decision_policy or {}, max_rechecks),
                {"recursion_limit": 100}, stream_mode=["updates", "values"]):
            if mode == "updates":
                self.nodes.extend(update)
            else:
                result = update
        return result


class PipelineTests(unittest.TestCase):
    def test_success_has_three_parallel_workers_and_mandatory_report_evaluation(self):
        h = Harness()
        result = h.run()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["recheck_count"], 0)
        self.assertEqual(len(result["worker_results"]), 3)
        self.assertEqual(h.events[-3:], ["judgement", "writer", "report_evaluator"])
        self.assertEqual(h.evidence_rounds, 1)
        self.assertEqual(result["step_count"], 8)

    def test_synthesizer_replans_only_missing_role_and_passes_actual_query(self):
        h = Harness()
        h.planner_fn = lambda s: plan_for() if s["recheck_count"] == 0 else plan_for(
            ("technology",), "고객 현장의 안전성 수치 추가 확인")
        h.checker_fn = lambda s: {
            "source_checked_claims": [], "evidence_status": "scorable",
            "evidence_gaps": ([{"analysis": "technology_analysis", "reason": "invalid_quote"}]
                              if s["recheck_count"] == 0 else []),
        }
        result = h.run()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["recheck_count"], 1)
        self.assertEqual([task["worker"] for round_, task in h.calls if round_ == 1], ["technology"])
        self.assertIn("안전성 수치", h.calls[-1][1]["query"])
        self.assertIn("invalid_quote", " ".join(h.plans[1]["feedback"]))
        self.assertEqual(result["research_result"]["bigtech_analysis"]["claims"][0]["claim"],
                         "bigtech: 기본 조사")
        self.assertEqual(len(result["worker_results"]), 4)
        self.assertEqual(h.events[h.events.index("orchestrator", 1) - 1], "synthesizer")

    def test_three_to_five_extra_rechecks_terminate_partial_with_unknown(self):
        for limit in (3, 4, 5):
            with self.subTest(limit=limit):
                h = Harness()
                h.planner_fn = lambda s: plan_for(query=f"새 조사 {s['recheck_count']}")
                h.checker_fn = lambda s: {"source_checked_claims": [{"claim": f"부분 근거 {s['recheck_count']}"}], "evidence_gaps": [],
                                          "evidence_status": "insufficient"}
                result = h.run(limit)
                self.assertEqual(result["status"], "partial")
                self.assertEqual(result["recheck_count"], limit)
                self.assertEqual(len(h.plans), limit + 1)
                self.assertEqual(h.evaluations, 1)
                self.assertTrue(result["investment_result"]["scorecard"]["unknown"])
                self.assertIn("U", result["final_report"])

    def test_worker_failure_does_not_discard_other_workers(self):
        h = Harness()

        def worker(task, state):
            if task["worker"] == "technology" and state["recheck_count"] == 0:
                raise RuntimeError("API 일시 실패")
            return {"claims": [{"claim": task["query"]}]}

        h.worker_fn = worker
        h.planner_fn = lambda s: plan_for() if s["recheck_count"] == 0 else plan_for(("technology",))
        result = h.run()
        self.assertEqual(result["status"], "completed")
        self.assertIn("error", result["worker_results"]["0:technology"])
        self.assertIn("bigtech_analysis", result["research_result"])
        self.assertIn("market_competition_analysis", result["research_result"])
        self.assertIn("API 일시 실패", " ".join(h.plans[1]["feedback"]))

    def test_failed_report_returns_through_synthesizer_before_new_plan(self):
        h = Harness()
        h.evaluator_fn = lambda s: {"passed": h.evaluations > 1,
                                    "issues": [] if h.evaluations > 1 else ["이해관계자 관점 누락"]}
        h.planner_fn = lambda s: plan_for() if s["recheck_count"] == 0 else plan_for(("market",), "고객 현장 추가 실증")
        result = h.run()
        first_eval = h.nodes.index("report_evaluator")
        self.assertEqual(h.nodes[first_eval + 1:first_eval + 3], ["synthesizer", "orchestrator"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(h.evaluations, 2)
        self.assertIn("이해관계자 관점 누락", h.plans[1]["feedback"])

    def test_failed_report_never_becomes_passed_just_because_budget_is_exhausted(self):
        h = Harness()
        h.planner_fn = lambda s: plan_for(query=f"보완 조사 {s['recheck_count']}")
        h.checker_fn = lambda s: {"source_checked_claims": [{"claim": f"새 근거 {s['recheck_count']}"}],
                                  "evidence_gaps": [], "evidence_status": "scorable"}
        h.evaluator_fn = lambda s: {"passed": False, "issues": ["근거성 미달"]}
        result = h.run(3)
        self.assertEqual(result["status"], "quality_failed")
        self.assertFalse(result["report_evaluation"]["passed"])
        self.assertEqual(h.evaluations, 4)
        self.assertEqual(result["recheck_count"], 3)
        self.assertTrue(result["final_report"])

    def test_empty_plan_still_reaches_synthesizer_and_terminates(self):
        h = Harness()
        h.planner_fn = lambda s: []
        result = h.run(3)
        self.assertEqual(len(h.calls), 0)
        self.assertEqual(len(h.plans), 4)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["final_report"])

    def test_parallel_result_merger_rejects_conflicting_same_key(self):
        self.assertEqual(merge_worker_results({"0:a": {"x": 1}}, {"0:b": {"x": 2}}),
                         {"0:a": {"x": 1}, "0:b": {"x": 2}})
        with self.assertRaises(ValueError):
            merge_worker_results({"0:a": {"x": 1}}, {"0:a": {"x": 2}})

    def test_invalid_retry_limits_are_rejected_and_invalid_plan_is_bounded(self):
        for limit in (2, 6, True, 4.0):
            with self.assertRaises(ValueError):
                initial_state({"name": "기업"}, {}, {}, limit)
        h = Harness()
        h.planner_fn = lambda s: plan_for(("technology", "technology"))
        result = h.run(3)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["recheck_count"], 3)
        self.assertEqual(len(h.calls), 0)
        self.assertIn("계획", " ".join(result["feedback"]))

    def test_planner_exception_reaches_synthesizer_then_can_recover(self):
        h = Harness()

        def planner(s):
            if s["recheck_count"] == 0:
                raise RuntimeError("계획 API 일시 실패")
            return plan_for()

        h.planner_fn = planner
        result = h.run()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["recheck_count"], 1)
        self.assertEqual(h.events[:3], ["orchestrator", "synthesizer", "orchestrator"])
        self.assertIn("계획 API 일시 실패", " ".join(h.plans[1]["feedback"]))
        self.assertNotIn("planning_error", result["research_result"])

    def test_report_issues_override_true_pass_and_survive_until_revised_writer(self):
        h = Harness()
        writer_feedback = []
        original_writer = h.writer

        def writer(s):
            writer_feedback.append(list(s["feedback"]))
            return original_writer(s)

        h.writer = writer
        h.evaluator_fn = lambda s: {"passed": True,
                                    "action": "rewrite",
                                    "issues": ["이해관계자 설명 보완"] if h.evaluations == 1 else []}
        result = h.run()
        self.assertEqual(h.evaluations, 2)
        self.assertEqual(result["status"], "completed")
        self.assertIn("이해관계자 설명 보완", writer_feedback[1])
        self.assertEqual(result["report_rewrite_count"], 1)
        self.assertEqual(len(h.plans), 1)

    def test_flat_payload_keeps_latest_state_and_research_over_nested_stale_keys(self):
        data = initial_state({"name": "현재 기업"}, {}, {})
        data["final_report"] = "현재 보고서"
        data["research_result"] = {"final_report": "오래된 보고서", "company_info": {"name": "다른 기업"},
                                   "source_checked_claims": [{"claim": "현재 근거"}],
                                   "report_revision_feedback": ["재작성 요청"]}
        data["investment_result"] = {"source_checked_claims": [{"claim": "오래된 근거"}], "total_score": 3}
        payload = flat_payload(data)
        self.assertEqual(payload["company_info"]["name"], "현재 기업")
        self.assertEqual(payload["final_report"], "현재 보고서")
        self.assertEqual(payload["source_checked_claims"][0]["claim"], "현재 근거")
        self.assertEqual(payload["total_score"], 3)
        self.assertEqual(payload["feedback"], ["재작성 요청"])

    def test_new_round_clears_old_investment_result_and_bad_checker_is_bounded(self):
        h = Harness()
        previous_scores = []

        def worker(task, s):
            previous_scores.append(dict(s["investment_result"]))
            return {"claims": [{"claim": task["query"]}]}

        h.worker_fn = worker
        h.planner_fn = lambda s: plan_for(query=f"보완 조사 {s['recheck_count']}")
        h.evaluator_fn = lambda s: {"passed": h.evaluations > 1,
                                    "issues": [] if h.evaluations > 1 else ["보완 조사 필요"]}
        h.run()
        self.assertTrue(all(not scores for scores in previous_scores))
        bad = Harness()
        bad.checker_fn = lambda s: {"evidence_status": "scorable", "evidence_gaps": "형식 오류"}
        result = bad.run(3)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["recheck_count"], 2)
        self.assertIn("근거 검증", " ".join(result["feedback"]))

    def test_synthesizer_merges_cached_web_sources_before_evidence_check(self):
        h = Harness()
        metadata = {"source_url": "https://example.com/company", "source_path": "/tmp/company-source.pdf",
                    "page_count": 2, "content_sha256": "원문내용해시"}
        seen = []

        def worker(task, s):
            self.assertNotIn("WEB-1", s["document_catalog"])
            return {"claims": [{"claim": "웹 원문 주장", "source_refs": [{"doc_id": "WEB-1", "page": 1}]}],
                    "source_documents": {"WEB-1": metadata}, "link_errors": []}

        def checker(s):
            seen.append(s["document_catalog"]["WEB-1"])
            return {"source_checked_claims": [{"claim": "웹 원문 주장"}],
                    "evidence_gaps": [], "evidence_status": "scorable"}

        h.worker_fn, h.checker_fn = worker, checker
        result = h.run()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(seen, [metadata])
        self.assertEqual(result["document_catalog"]["WEB-1"], metadata)
        self.assertEqual(result["recheck_count"], 0)  # 동일 metadata 병렬 반환은 멱등 병합.

    def test_web_source_collision_keeps_first_source_and_link_error_is_traceable(self):
        h = Harness()
        originals = {"source_url": "https://example.com/original", "source_path": "/tmp/original.pdf"}

        def worker(task, s):
            metadata = originals if task["worker"] == "bigtech" else {
                "source_url": "https://example.com/different", "source_path": "/tmp/different.pdf"}
            return {"claims": [], "source_documents": {"WEB-1": metadata},
                    "link_errors": ["https://example.com/blocked HTTP 403"] if task["worker"] == "market" else []}

        h.worker_fn = worker
        result = h.run(3)
        self.assertEqual(result["document_catalog"]["WEB-1"], originals)
        self.assertEqual(result["status"], "partial")
        self.assertIn("ID 충돌", " ".join(result["feedback"]))
        self.assertIn("https://example.com/blocked", " ".join(result["research_result"]["source_access_errors"]))


if __name__ == "__main__":
    unittest.main()
