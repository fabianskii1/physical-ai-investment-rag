def test_normalize_claims_preserves_primary_source_for_evidence_check():
    from graph import normalize_claims

    claims = [{
        "claim": "공장 현장 실증을 진행했다.",
        "criterion_id": "bigtech_contract_delivery_stage",
        "source_refs": [{"doc_id": "demo", "page": 2}],
        "status": "confirmed",
        "relation_type": "field_demo",
    }]

    assert normalize_claims(claims) == [{
        "claim": "공장 현장 실증을 진행했다.",
        "criterion_id": "bigtech_contract_delivery_stage",
        "source_refs": [{"doc_id": "demo", "page": 2}],
        "source": {"doc_id": "demo", "page": 2},
        "status": "confirmed",
        "relation_type": "field_demo",
    }]


def test_analysis_node_uses_company_and_returns_checker_state_shape():
    from graph import analysis_node

    def analyze(company, search_fn, llm_fn):
        assert company == "Figure AI"
        return [{
            "claim": "현장 실증을 진행했다.",
            "criterion_id": "bigtech_contract_delivery_stage",
            "source_refs": [{"doc_id": "demo", "page": 2}],
            "status": "confirmed",
        }]

    node = analysis_node(analyze, "bigtech_analysis", lambda *args: [], lambda prompt: "")
    result = node({"company_info": {"name": "Figure AI"}})

    assert result == {"bigtech_analysis": {"claims": [{
        "claim": "현장 실증을 진행했다.",
        "criterion_id": "bigtech_contract_delivery_stage",
        "source_refs": [{"doc_id": "demo", "page": 2}],
        "source": {"doc_id": "demo", "page": 2},
        "status": "confirmed",
    }]}}


def test_normalize_claims_keeps_all_candidate_pages():
    from graph import normalize_claims

    refs = [{"doc_id": "first", "page": 1}, {"doc_id": "second", "page": 3}]
    normalized = normalize_claims([{"claim": "현장 실증", "status": "confirmed", "source_refs": refs}])

    assert normalized[0]["source_refs"] == refs


def test_initial_state_maps_catalog_records_to_evidence_lookup():
    from types import SimpleNamespace
    from graph import initial_state

    record = SimpleNamespace(
        doc_id="demo", title="BMW 발표", file_path="PDF/input/demo.pdf",
        source_path="/external/materials/demo.pdf", page_count=3
    )
    policy = {"minimum_score": 70, "required_items": []}

    assert initial_state("Figure AI", [record], policy) == {
        "company_info": {"name": "Figure AI"},
        "document_catalog": {
            "demo": {
                "title": "BMW 발표", "file_path": "PDF/input/demo.pdf",
                "source_path": "/external/materials/demo.pdf", "page_count": 3,
            }
        },
        "decision_policy": policy,
    }


def test_graph_waits_for_all_analyses_then_scores_and_reports():
    from graph import build_graph

    events = []

    def analysis(key):
        return lambda state: {key: {"claims": []}}

    def check(state):
        assert all(key in state for key in (
            "bigtech_analysis", "technology_analysis", "market_competition_analysis"
        ))
        events.append("check")
        return {"evidence_status": "scorable"}

    def judge(state):
        events.append("judge")
        return {"investment_decision": "우선 검토"}

    def report(state):
        events.append("report")
        return {"final_report": state["investment_decision"]}

    app = build_graph(
        analysis("bigtech_analysis"),
        analysis("technology_analysis"),
        analysis("market_competition_analysis"),
        check,
        judge,
        report,
    )
    result = app.invoke({"company_info": {"name": "Figure AI"}})

    assert events == ["check", "judge", "report"]
    assert result["final_report"] == "우선 검토"


def test_graph_skips_scoring_when_evidence_is_insufficient():
    from graph import build_graph

    def analysis(key):
        return lambda state: {key: {"claims": []}}

    def reject_scoring(state):
        raise AssertionError("근거 부족이면 점수 평가를 호출하면 안 됩니다")

    app = build_graph(
        analysis("bigtech_analysis"),
        analysis("technology_analysis"),
        analysis("market_competition_analysis"),
        lambda state: {"evidence_status": "insufficient"},
        reject_scoring,
        lambda state: {"final_report": "근거 부족"},
    )

    assert app.invoke({"company_info": {"name": "Figure AI"}})["final_report"] == "근거 부족"


def test_report_optional_writer_is_not_overridden_by_langgraph():
    from graph import build_graph

    def analysis(key):
        return lambda state: {key: {"claims": []}}

    def report(state, writer=None):
        assert writer is None
        return {"final_report": "근거 부족"}

    app = build_graph(
        analysis("bigtech_analysis"),
        analysis("technology_analysis"),
        analysis("market_competition_analysis"),
        lambda state: {"evidence_status": "insufficient"},
        lambda state: {},
        report,
    )

    assert app.invoke({"company_info": {"name": "Figure AI"}})["final_report"] == "근거 부족"


def test_create_graph_connects_analysis_functions_to_existing_nodes(monkeypatch):
    import sys
    from types import ModuleType
    from graph import create_graph

    analyses = ModuleType("analyses")
    unavailable = lambda company, search, llm: [{
        "claim": "근거 미확인", "source_refs": [], "status": "unverified", "criterion_id": "test"
    }]
    analyses.analyze_bigtech = unavailable
    analyses.analyze_technology = unavailable
    analyses.analyze_market = unavailable
    analyses.openai_llm = lambda prompt: ""
    retrieval = ModuleType("retrieval")
    retrieval.search = lambda company, query, k: []
    monkeypatch.setitem(sys.modules, "analyses", analyses)
    monkeypatch.setitem(sys.modules, "retrieval", retrieval)

    result = create_graph().invoke({
        "company_info": {"name": "Figure AI"},
        "document_catalog": {},
    })

    assert result["evidence_status"] == "insufficient"
    assert "근거 부족" in result["final_report"]
