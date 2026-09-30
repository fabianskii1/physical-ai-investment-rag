from pathlib import Path

import pdfplumber

from pdf_export import markdown_to_pdf


def test_markdown_report_exports_five_readable_pages(tmp_path: Path) -> None:
    markdown = """# 모토마인드 투자 검토 보고서

## 1p — SUMMARY + 기업 개요
### 핵심 투자 포인트
- 현장 실증 확인 [E1]

## 2p — 시장·팀 분석
### 시장 규모
고객의 구매 필요성을 검토한다.

## 3p — 기술·경쟁력·실적
### 핵심 기술
로봇이 공장에서 작동한다.

## 4p — 리스크 + 투자평가
### Scorecard 평가표
| 영역 | 배점 | 점수 |
|---|---:|---:|
| 실적 | 20 | 10/20 |

## 5p — REFERENCE
- [E1] 실증 자료 — data/raw/sample.pdf, p.2
"""
    target = tmp_path / "report.pdf"

    markdown_to_pdf(markdown, target)

    with pdfplumber.open(target) as pdf:
        assert len(pdf.pages) == 5
        pages = [page.extract_text() for page in pdf.pages]
    assert "모토마인드 투자 검토 보고서" in pages[0]
    assert "현장 실증 확인 [E1]" in pages[0]
    assert "시장 규모" in pages[1]
    assert "로봇이 공장에서 작동한다." in pages[2]
    assert "실적" in pages[3] and "10/20" in pages[3]
    assert "data/raw/sample.pdf" in pages[4]
