from pathlib import Path

import pdfplumber

from run_reports import run_reports


def test_run_reports_saves_all_three_companies_as_markdown_and_pdf(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("run_reports.initialize", lambda: None)
    monkeypatch.setattr("run_reports.load_catalog", lambda: [])

    class Graph:
        def invoke(self, state):
            assert state["decision_policy"] == {
                "minimum_score": 70,
                "required_items": ["핵심 작업 성능"],
            }
            name = state["company_info"]["name"]
            return {"final_report": f"# {name} 투자 검토 보고서\n\n## 1p — SUMMARY + 기업 개요\n{name} 분석 완료\n"}

    monkeypatch.setattr("run_reports.create_graph", Graph)

    paths = run_reports(tmp_path)

    assert {path.name for path in paths} == {
        "motomind.pdf", "robros.pdf", "figure_ai.pdf"
    }
    for path in paths:
        markdown = path.with_suffix(".md")
        assert markdown.exists()
        with pdfplumber.open(path) as pdf:
            text = pdf.pages[0].extract_text()
        assert "투자 검토 보고서" in text
        assert "분석 완료" in text
