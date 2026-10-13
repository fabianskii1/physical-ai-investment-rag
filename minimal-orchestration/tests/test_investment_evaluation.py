"""설계서 Scorecard와 U·현재 투자조건·근거 인용 제약을 검증한다."""

import unittest

from investment_evaluation import CRITERIA, evaluate_investment
from rag.scorecard import SCORECARD


def assessment(criterion="관련 전문성", grade=2, ids=None, negative=False):
    return {"criterion": criterion, "grade": grade, "reason": "원문에서 확인한 판단 근거",
            "evidence_ids": ["E1"] if ids is None else ids,
            "explicit_negative_confirmed": negative}


def state(terms=None):
    return {
        "company_info": {"name": "기업"}, "evidence_status": "scorable",
        "source_checked_claims": [{"claim": "공식 자료에서 확인된 사실", "analysis": "technology_analysis"}],
        "investment_terms": terms,
        "decision_policy": {"minimum_score": 70},
    }


def evaluate(data, assessments, decision="우선 검토", reason="검증된 사실과 위험을 바탕으로 검토"):
    return evaluate_investment(data, lambda payload: {
        "assessments": assessments, "decision": decision, "decision_reason": reason,
    })


class InvestmentEvaluationTests(unittest.TestCase):
    def test_all_unknown_is_not_an_actual_zero_company_score(self):
        result = evaluate(state(), [])
        self.assertIsNone(result["total_score"])
        self.assertEqual(result["scorable_weight"], 0)

    def test_strict_company_scoring_requires_evidence_for_the_matching_criterion(self):
        data = state()
        data["decision_policy"]["require_criteria_coverage"] = True
        data["source_checked_claims"][0]["criterion_id"] = "core_task_performance"
        wrong = evaluate(data, [assessment("관련 전문성", 3)])
        self.assertIsNone(wrong["score_details"]["관련 전문성"]["grade"])
        matched = evaluate(data, [assessment("핵심 작업 성능", 2)])
        self.assertEqual(matched["score_details"]["핵심 작업 성능"]["score"], 3)
    def test_pdf_scorecard_is_6_areas_21_items_and_total_100(self):
        self.assertEqual(len(SCORECARD), 6)
        self.assertEqual(len(CRITERIA), 21)
        self.assertEqual([sum(items.values()) for items in SCORECARD.values()], [20, 25, 15, 10, 20, 10])
        self.assertEqual(sum(weight for items in SCORECARD.values() for weight in items.values()), 100)

    def test_grade_two_of_weight_six_is_three_without_unknown_renormalization(self):
        result = evaluate(state(), [assessment()])
        self.assertEqual(result["score_details"]["관련 전문성"]["score"], 3)
        self.assertEqual(result["total_score"], 3)
        self.assertEqual(result["scorable_weight"], 6)
        self.assertEqual(result["unknown_weight"], 94)
        self.assertEqual(len(result["unknown_items"]), 20)

    def test_confirmed_negative_zero_is_allowed_but_missing_proof_is_unknown(self):
        for negative, ids, expected in ((True, ["E1"], 0), (False, ["E1"], None), (True, [], None)):
            with self.subTest(negative=negative, ids=ids):
                result = evaluate(state(), [assessment(grade=0, ids=ids, negative=negative)])
                self.assertEqual(result["score_details"]["관련 전문성"]["grade"], expected)

    def test_missing_current_terms_forces_unknown_even_if_model_uses_funding_news(self):
        data = state()
        data["source_checked_claims"][0] = {"claim": "과거 Series C 투자 유치", "analysis": "bigtech_analysis"}
        result = evaluate(data, [assessment(name, 4) for name in SCORECARD["투자조건"]])
        for name in SCORECARD["투자조건"]:
            self.assertIsNone(result["score_details"][name]["grade"])
        self.assertEqual(result["scorable_weight"], 0)

    def test_current_terms_need_verified_manual_evidence_for_matching_criterion(self):
        name = "지분·권리 조건"
        data = state({"rights": "우선주 권리 계약"})
        data["source_checked_claims"].append({
            "claim": "현재 투자자의 우선주 권리가 계약 원문에 명시됨",
            "analysis": "investment_terms", "criterion": name,
        })
        good = evaluate(data, [assessment(name, 4, ["E2"])])
        self.assertEqual(good["score_details"][name]["grade"], 4)
        self.assertEqual(good["score_details"][name]["score"], 3)
        wrong_source = evaluate(data, [assessment(name, 4, ["E1"])])
        self.assertIsNone(wrong_source["score_details"][name]["grade"])
        wrong_criterion = evaluate(data, [assessment("기업가치의 합리성", 4, ["E2"])])
        self.assertIsNone(wrong_criterion["score_details"]["기업가치의 합리성"]["grade"])

    def test_duplicate_invalid_grade_missing_reason_or_invalid_source_become_unknown(self):
        cases = [
            [assessment(), assessment()], [assessment(grade=True)], [assessment(grade=5)],
            [assessment(ids=["E999"])], [{**assessment(), "reason": ""}],
            [{**assessment(), "criterion": ["관련 전문성"]}],
        ]
        for items in cases:
            with self.subTest(items=items):
                result = evaluate(state(), items)
                self.assertIsNone(result["score_details"]["관련 전문성"]["grade"])

    def test_decision_uses_evidence_based_opinion_and_not_an_automatic_70_point_cutoff(self):
        low_score = evaluate(state(), [assessment()], decision="우선 검토", reason="소규모 실증을 먼저 확인")
        self.assertEqual(low_score["total_score"], 3)
        self.assertEqual(low_score["investment_decision"], "우선 검토")
        self.assertEqual(low_score["decision_reason"], "소규모 실증을 먼저 확인")
        high_data = state()
        high_data["source_checked_claims"][0]["analysis"] = "bigtech_analysis"
        high_score = evaluate(high_data, [assessment(name, 4) for name in CRITERIA
                                         if name not in SCORECARD["투자조건"]],
                              decision="보류", reason="검증된 기술과 별개로 현재 권리 조건 확인 필요")
        # 거래 방향·단계의 검증 필드 없는 옛 주장은 세 거래 항목의 16점 배점을 채울 수 없다.
        self.assertEqual(high_score["total_score"], 74)
        self.assertEqual(high_score["investment_decision"], "보류")
        invalid = evaluate(state(), [assessment()], decision="매수", reason="")
        self.assertEqual(invalid["investment_decision"], "근거 부족")
        self.assertEqual(evaluate(state(), [assessment()], decision=[])["investment_decision"], "근거 부족")

    def test_insufficient_sources_skip_model_and_return_all_unknown(self):
        def should_not_run(payload):
            self.fail("근거 부족 상태에서 평가 모델을 호출하면 안 됨")

        for evidence_status, claims in (("insufficient", [{"claim": "사실"}]), ("scorable", [])):
            data = {**state(), "evidence_status": evidence_status, "source_checked_claims": claims}
            result = evaluate_investment(data, should_not_run)
            self.assertIsNone(result["total_score"])
            self.assertEqual(result["unknown_weight"], 100)
            self.assertEqual(len(result["unknown_items"]), 21)
            self.assertEqual(result["investment_decision"], "근거 부족")

    def test_payload_preserves_report_evidence_ids_and_passes_manual_terms(self):
        captured = {}
        data = state({"valuation": "현재 거래의 기업가치 입력"})
        data["source_checked_claims"] = [None, {"claim": "문서 근거"}]

        def evaluator(payload):
            captured.update(payload)
            return {"assessments": [assessment(ids=["E2"])], "decision": "우선 검토", "decision_reason": "검증 사유"}

        result = evaluate_investment(data, evaluator)
        self.assertEqual(captured["verified_evidence"][0]["id"], "E2")
        self.assertEqual(captured["manual_investment_terms"], data["investment_terms"])
        self.assertEqual(result["total_score"], 3)

    def test_cvc_is_four_zero_or_unknown_and_requires_bigtech_evidence(self):
        name = "대기업 실제 지분·CVC 투자"
        for role, grade, negative, expected in (
            ("technology_analysis", 4, False, None), ("bigtech_analysis", 2, False, None),
            ("bigtech_analysis", 4, False, 4), ("bigtech_analysis", 0, True, 0),
            ("bigtech_analysis", 0, False, None),
        ):
            with self.subTest(role=role, grade=grade, negative=negative):
                data = state()
                data["source_checked_claims"][0]["analysis"] = role
                result = evaluate(data, [assessment(name, grade, negative=negative)])
                self.assertEqual(result["score_details"][name]["grade"], expected)
        contracts = evaluate(state(), [assessment("대기업 계약·납품 단계", 3)])
        self.assertIsNone(contracts["score_details"]["대기업 계약·납품 단계"]["grade"])


if __name__ == "__main__":
    unittest.main()
