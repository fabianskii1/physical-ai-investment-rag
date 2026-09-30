"""단일 기업 투자 분석 노드의 LangGraph 연결."""

from collections.abc import Mapping
from typing import TypedDict


class InvestmentState(TypedDict, total=False):
    company_info: dict
    document_catalog: dict
    decision_policy: dict
    bigtech_analysis: dict
    technology_analysis: dict
    market_competition_analysis: dict
    source_checked_claims: list
    evidence_gaps: list
    evidence_status: str
    score_details: dict
    total_score: float | None
    scorable_weight: int
    unknown_weight: int
    unknown_items: list
    investment_decision: str
    decision_reason: str
    final_report: str


def initial_state(company_name: str, catalog_records, decision_policy: dict) -> InvestmentState:
    """검색기의 catalog 행을 근거 검사·보고서가 조회할 State로 바꾼다."""
    if not isinstance(company_name, str) or not company_name.strip():
        raise ValueError("평가할 기업명이 필요합니다")
    return {
        "company_info": {"name": company_name.strip()},
        "document_catalog": {
            record.doc_id: {
                "title": record.title,
                "file_path": record.file_path,
                "source_path": str(record.source_path),
                "page_count": record.page_count,
            }
            for record in catalog_records
        },
        "decision_policy": decision_policy,
    }


def normalize_claims(claims: list[dict]) -> list[dict]:
    """분석 에이전트의 출처 목록을 근거 검사 노드의 입력으로 맞춘다."""
    normalized = []
    for claim in claims:
        if not isinstance(claim, Mapping):
            continue
        refs = claim.get("source_refs", [])
        source = refs[0] if isinstance(refs, list) and refs else None
        normalized.append({
            **claim,
            "source": source,
        })
    return normalized


def analysis_node(analyze, output_key: str, search_fn, llm_fn):
    """팀 분석 함수를 LangGraph의 State 입출력 노드로 감싼다."""
    def run(state: InvestmentState) -> dict:
        company = state.get("company_info", {}).get("name")
        if not isinstance(company, str) or not company.strip():
            raise ValueError("company_info.name이 필요합니다")
        return {output_key: {"claims": normalize_claims(analyze(company, search_fn, llm_fn))}}

    return run


def build_graph(bigtech_node, technology_node, market_node, evidence_node, judgment_node, report_node):
    """세 분석을 병렬 실행하고 근거 상태에 따라 투자 판단을 분기한다."""
    from langgraph.graph import END, START, StateGraph

    builder = StateGraph(InvestmentState)
    builder.add_node("bigtech", bigtech_node)
    builder.add_node("technology", technology_node)
    builder.add_node("market", market_node)
    builder.add_node("check_evidence", evidence_node)
    builder.add_node("investment_judgement", judgment_node)
    builder.add_node("report", lambda state: report_node(state))

    for name in ("bigtech", "technology", "market"):
        builder.add_edge(START, name)
    builder.add_edge(["bigtech", "technology", "market"], "check_evidence")
    builder.add_conditional_edges(
        "check_evidence",
        lambda state: "investment_judgement" if state["evidence_status"] == "scorable" else "report",
    )
    builder.add_edge("investment_judgement", "report")
    builder.add_edge("report", END)
    return builder.compile()


def create_graph():
    """팀 검색·분석 코드와 기존 근거 검사·판단·보고서 노드를 연결한다."""
    from analyses import analyze_bigtech, analyze_market, analyze_technology, openai_llm
    from retrieval import search
    from report import report
    from workflow import check_evidence, investment_judgement

    return build_graph(
        analysis_node(analyze_bigtech, "bigtech_analysis", search, openai_llm),
        analysis_node(analyze_technology, "technology_analysis", search, openai_llm),
        analysis_node(analyze_market, "market_competition_analysis", search, openai_llm),
        check_evidence,
        investment_judgement,
        report,
    )
