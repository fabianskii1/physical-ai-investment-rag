"""기존 RAG와 새 그래프가 만나는 지점의 검색·인용·PDF를 검증한다."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from langchain_core.documents import Document
from pypdf import PdfReader

import legacy_adapter
from app import run


def test_orchestrated_query_reaches_existing_company_and_common_search():
    """재계획 질문이 프롬프트만 바뀌고 고정 검색에 묶이는 회귀를 막는다."""
    query = "Figure AI 고객의 구매 필요성과 반복 운영 비용 재확인"
    doc = Document(page_content="기관 시장 자료", metadata={"doc_id": "CM-02", "page": 2})
    with patch.dict("os.environ", {"OPENAI_API_KEY": "offline-test"}), \
            patch.object(legacy_adapter, "initialize"), \
            patch.object(legacy_adapter, "search", return_value=[doc]) as search, \
            patch.object(legacy_adapter, "_structured_request") as llm:
        from source_worker import WorkerSourceReview
        llm.return_value=WorkerSourceReview(pages=[{'doc_id':'CM-02','page':2,'facts':[], 'exclusion_reason':'관련 기업 사실 없음'}])
        deps = legacy_adapter.real_dependencies()
        deps.worker({"id": "market-2", "worker": "market", "query": query},
                    {"company_info": {"name": "Figure AI"}, "feedback": ["구매 필요성 미달"]})
    calls=[call.args[:2] for call in search.call_args_list]
    assert ('Figure AI', query) in calls and ('COMMON', query) in calls
    assert any('실제 접근 가능한 시장' in question for scope,question in calls)
    assert query==llm.call_args.args[2]['query']
    assert "구매 필요성 미달" in llm.call_args.args[2]['feedback']


def test_manual_current_terms_use_same_original_page_verification():
    """자유 입력을 확인 사실로 만들지 않고 현재 거래의 문서·페이지를 검증한다."""
    catalog = legacy_adapter.load_project_catalog()
    entry = catalog["FG-03"]
    page = legacy_adapter.load_pdf(entry["source_path"], doc_id="FG-03", company="Figure AI")[2]
    quote = page.page_content.strip()[:80]
    base = {"company_info": {"name": "Figure AI"}, "document_catalog": catalog,
            "technology_analysis": {}, "bigtech_analysis": {}, "market_competition_analysis": {}}
    accepted = {"criterion_id": "지분·권리 조건", "claim": "사람이 확인을 요청한 현재 거래 주장",
                "status": "confirmed", "source_refs": [{"doc_id": "FG-03", "page": 3}]}
    checked = legacy_adapter.reuse_evidence_check(
        {**base, "investment_terms": {"claims": [accepted]}},
        judge=lambda claim, text: {"verdict": "supported", "quote": quote, "reason": "테스트 입력"})
    assert checked["source_checked_claims"][0]["analysis"] == "investment_terms"
    assert checked["source_checked_claims"][0]["criterion_id"] == "지분·권리 조건"
    assert checked["evidence_status"] == "insufficient"  # 투자조건으로 기술 근거를 대체하지 않는다.
    rejected = legacy_adapter.reuse_evidence_check(
        {**base, "investment_terms": {"claims": [{**accepted, "source_refs": [{"doc_id": "FG-03", "page": 999}]}]}},
        judge=lambda *args: (_ for _ in ()).throw(AssertionError("잘못된 페이지는 모델 호출 전에 제외")))
    assert not rejected["source_checked_claims"]
    assert rejected["evidence_gaps"][0]["reason"] == "invalid_page"


def test_link_body_flows_through_existing_worker_source_check_and_report():
    """웹 본문도 기존 분석의 허용 출처 검사와 실제 보관 페이지 검사를 거친다."""
    from link_sources import WebSourceReader, load_link_registry
    from investment_evaluation import evaluate_investment

    url = "https://www.figure.ai/news/series-c"
    registry = load_link_registry(Path(__file__).parents[1] / "com_data")
    doc_id = registry["Figure AI"][url]["doc_id"]
    claim = "로봇은 시각 인지와 행동 제어 기능을 제공합니다."
    html = f"<html><title>공식 기술 원문</title><main><p>{claim}</p><p>회사의 기술 소개이며 독립된 성능 검증과 구분합니다.</p></main></html>".encode()
    llm_output = json.dumps([{"analysis_type": "core_task_performance", "claim": claim,
        "source_refs": [{"doc_id": doc_id, "page": 1}], "status": "confirmed"}], ensure_ascii=False)
    with tempfile.TemporaryDirectory() as directory, \
            patch.dict("os.environ", {"OPENAI_API_KEY": "offline-test"}), \
            patch.object(legacy_adapter, "initialize"), \
            patch.object(legacy_adapter, "search", return_value=[]), \
            patch.object(WebSourceReader, "_fetch", return_value=(html, "text/html; charset=utf-8", url)), \
            patch.object(legacy_adapter, "_structured_request") as extraction:
        from source_worker import WorkerSourceReview
        def select_lines(schema,instruction,payload):
            p=next(p for p in payload['pages'] if p['doc_id']==doc_id)
            n=next(line['line'] for line in p['lines'] if claim in line['text'])
            return WorkerSourceReview(pages=[{'doc_id':doc_id,'page':1,'facts':[{
                'criterion_id':'core_task_performance','start_line':n,'end_line':n,'scope':'reported_fact'}], 'exclusion_reason':''}])
        extraction.side_effect=select_lines
        deps = legacy_adapter.real_dependencies(cache_dir=Path(directory) / "sources")
        analysis = deps.worker({"id": "technology", "worker": "technology", "query": "기술 원문 검토", "urls": [url]},
                               {"company_info": {"name": "Figure AI"}, "feedback": []})
        assert not analysis["link_errors"]
        assert analysis["claims"][0]["source_refs"][0]["doc_id"] == doc_id
        state = {"company_info": {"name": "Figure AI"}, "document_catalog": analysis["source_documents"],
                 "technology_analysis": analysis, "investment_terms": None}
        checked = legacy_adapter.reuse_evidence_check(state, judge=lambda *args: {
            "verdict": "supported", "quote": claim, "reason": "원문 문자열을 검증하는 모의 의미 판정"})
        assert checked["evidence_status"] == "scorable"
        assert len(checked["source_checked_claims"]) == 1
        state.update(checked)
        state.update(evaluate_investment(state, lambda payload: {"assessments": []}))
        markdown = legacy_adapter.report(state, writer=lambda payload: {"technology": ["E1"]})["final_report"]
        assert url in markdown
        assert "열람:" in markdown
        assert "웹 원문 보관 PDF p.1" in markdown
        assert "[E1]" in markdown


def test_demo_runs_real_graph_rechecks_only_market_and_exports_valid_a4():
    """모델을 사용하지 않는 종단 검증이며 투자 분석 품질을 입증하는 테스트가 아니다."""
    with tempfile.TemporaryDirectory() as directory:
        result = run(demo=True, output_dir=directory)
        state = result["state"]
        assert state["status"] == "completed"
        assert state["recheck_count"] == 1
        assert {key.split(":")[1] for key in state["worker_results"] if key.startswith("1:")} == {"market"}
        assert state["investment_result"]["unknown_weight"] == 100
        assert "70점" not in state["final_report"]
        assert "오프라인 파이프라인 시연" in state["final_report"]
        assert all(result["pdf_checks"][key] for key in (
            "all_a4", "within_page_limit", "text_within_margins"))
        assert 1 <= len(PdfReader(result["pdf"]).pages) <= 10
        trace = json.loads(Path(result["trace"]).read_text())
        assert trace["mode"] == "demo"
        assert state["report_evaluation"]["mode"] == "demo"
