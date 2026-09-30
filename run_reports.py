"""세 휴머노이드 기업을 실제 RAG 그래프로 분석하고 보고서를 저장한다."""

from pathlib import Path

from graph import create_graph, initial_state
from pdf_export import markdown_to_pdf
from retrieval import initialize, load_catalog


COMPANIES = {
    "MOTOMIND": "motomind",
    "ROBROS": "robros",
    "Figure AI": "figure_ai",
}
TEST_DECISION_POLICY = {"minimum_score": 70, "required_items": ["핵심 작업 성능"]}


def run_reports(output_dir: str | Path = Path(__file__).parent / "output" / "reports") -> list[Path]:
    """검색 인덱스를 한 번 만든 뒤 기업별 Markdown과 PDF를 저장한다."""
    records = load_catalog()
    initialize()
    graph = create_graph()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_paths = []
    for company, stem in COMPANIES.items():
        print(f"분석 중: {company}", flush=True)
        result = graph.invoke(initial_state(company, records, TEST_DECISION_POLICY))
        markdown = result["final_report"]
        markdown_path = output_dir / f"{stem}.md"
        markdown_path.write_text(markdown, encoding="utf-8")
        pdf_path = markdown_to_pdf(markdown, output_dir / f"{stem}.pdf")
        pdf_paths.append(pdf_path)
        print(f"{company}: {result.get('investment_decision', '근거 부족')} -> {pdf_path}")
    return pdf_paths


if __name__ == "__main__":
    run_reports()
