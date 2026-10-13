"""PDF를 실제 생성해 용지·여백·분량 제한을 확인하는 출력 경계 테스트."""

import tempfile
import unittest
from pathlib import Path

import pdfplumber
from pypdf import PdfReader
from report_renderer import ReportLayoutError, render_report, validate_pdf


class ReportRendererTest(unittest.TestCase):
    def test_score_panel_preserves_noninteger_score_and_original_weight(self):
        score = "확인된 획득점수: 15.75점 / 원래 100점 배점 중 확인된 획득점수"
        markdown = f"# 기업 투자 검토 보고서\n## SUMMARY\n- {score}\n- 평가 가능 배점: 25/100점; 미확인 배점: 75/100점\n검증 사실을 유지합니다."
        with tempfile.TemporaryDirectory() as temp:
            path = render_report(markdown, Path(temp) / "report.pdf")
            check = validate_pdf(path)
            self.assertTrue(all(check[key] for key in ("all_a4", "within_page_limit", "text_within_margins")))
            self.assertEqual(check["pages"], 1)
            text = "".join(PdfReader(path).pages[0].extract_text().split())
            self.assertIn("".join(score.split()), text)
            self.assertIn("평가가능배점:25/100점;미확인배점:75/100점", text)
            with pdfplumber.open(path) as pdf:
                words = pdf.pages[0].extract_words(extra_attrs=["size"])
                self.assertTrue(any(word["text"] == "15.75점" and word["size"] == 22 for word in words))
                self.assertTrue(any(word["text"] == "검증" and word["size"] == 10 for word in words))

    def test_score_panel_keeps_unknown_as_unknown_without_a_numeric_score(self):
        for value in ("산정 보류 — 평가 가능한 배점이 없어 기업 점수를 확정할 수 없음", "U — 검증 근거 미확인"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as temp:
                score = f"확인된 획득점수: {value}"
                path = render_report(f"# 기업 보고서\n## SUMMARY\n- {score}\n- U는 0등급과 다릅니다.",
                                     Path(temp) / "report.pdf")
                check = validate_pdf(path)
                self.assertEqual(check["pages"], 1)
                self.assertTrue(check["all_a4"])
                self.assertTrue(check["text_within_margins"])
                text = "".join(PdfReader(path).pages[0].extract_text().split())
                self.assertIn("".join(score.split()), text)
                self.assertNotIn("0점", text)
                with pdfplumber.open(path) as pdf:
                    self.assertTrue(any(char["size"] == 18 for char in pdf.pages[0].chars))

    def test_korean_a4_margins_and_table(self):
        markdown = "# 테스트 기업 투자 검토 보고서\n## 1. 개요\n### 투자 검토\n- 한글 근거 [E1]\n" \
                   "| 영역 | 점수 |\n|---|---:|\n| 기술 성숙도 | U |\n## 2. 참고문헌\n- [E1] 회사 자료, p.1\n"
        with tempfile.TemporaryDirectory() as temp:
            path = render_report(markdown, Path(temp) / "report.pdf")
            check = validate_pdf(path)
            self.assertEqual(check["pages"], 2)
            self.assertTrue(check["all_a4"])
            self.assertTrue(check["text_within_margins"])
            self.assertIn("한글 근거", PdfReader(path).pages[0].extract_text())
            fonts = PdfReader(path).pages[0]["/Resources"]["/Font"]
            self.assertTrue(any("/FontFile2" in f.get_object().get("/FontDescriptor", {}) for f in fonts.values()))

    def test_over_ten_pages_preserves_previous_output(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "report.pdf"
            render_report("# 이전 보고서\n검증 완료", path)
            previous = path.read_bytes()
            long_report = "\n".join(f"## 구역 {i}\n근거를 그대로 유지합니다." for i in range(11))
            with self.assertRaises(ReportLayoutError):
                render_report(long_report, path)
            self.assertEqual(path.read_bytes(), previous)
            self.assertEqual(list(Path(temp).glob("*.pdf")), [path])

    def test_limit_cannot_exceed_assignment(self):
        with self.assertRaises(ValueError):
            render_report("# 보고서", "/private/tmp/unused-report.pdf", max_pages=11)

    def test_oversized_table_row_has_clear_error(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "report.pdf"
            with self.assertRaises(ReportLayoutError):
                render_report("| 근거 |\n|---|\n| " + "아주 긴 근거 " * 1500 + " |", path)
            self.assertFalse(path.exists())

    def test_reference_punctuation_and_long_url_stay_inside_twenty_mm(self):
        # CJK의 금칙처리는 아래 원문 ':'를 오른쪽 여백 밖으로 내어 걸었다.
        # 내용·글자 크기·검증 허용오차를 바꾸지 않고 실제 줄바꿈을 검증한다.
        line = "[E1] F.03 Arrives at BMW — data/raw/figure_ai/figure_f03_at_bmw.pdf, p.3; 발행: Figure AI; 원문:"
        url = "https://www.figure.ai/news/" + "long-source-with-original-context-" * 10
        with tempfile.TemporaryDirectory() as temp:
            path = render_report(f"# 기업 보고서\n## 참고문헌\n- {line}\n- {url}", Path(temp) / "report.pdf")
            self.assertTrue(validate_pdf(path)["text_within_margins"])
            text = "".join(PdfReader(path).pages[0].extract_text().split())
            self.assertIn(url, text)


if __name__ == "__main__":
    unittest.main()
