"""실제 네트워크 없이 링크 등록, 원문 캐시와 부분 실패를 확인한다."""

import hashlib
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject, TextStringObject

from link_sources import COMPANY_FILES, MAX_BYTES, WebSourceReader, _public_url, extract_text_urls, load_link_registry
from report_renderer import render_report


def registry_for(*urls):
    return {"Figure AI": {url: {"doc_id": "WEB-" + hashlib.sha256(url.encode()).hexdigest()[:16],
        "company": "Figure AI", "url": url, "origin_pdf": "Figure_AI.pdf", "origin_pages": [2]} for url in urls}}


HTML = ("<html><head><title>공식 기술 자료</title></head><body><nav>제외할 메뉴</nav>"
        "<main><h1>공식 기술 소개</h1><p>로봇의 시각 인지와 행동 제어 기술을 소개합니다. "
        "회사가 공개한 성능은 독립 검증과 구분하며 유상 계약 사실을 대신하지 않습니다.</p></main>"
        "<script>위험한 스크립트</script><footer>제외할 꼬리말</footer></body></html>").encode()


class LinkSourcesTest(unittest.TestCase):
    def test_wrapped_urls_and_annotations_registry(self):
        self.assertEqual(extract_text_urls("https://www.sec.gov/Archives/data/\nxslFormDX08/primary_doc.xml\n"),
                         ["https://www.sec.gov/Archives/data/xslFormDX08/primary_doc.xml"])
        self.assertEqual(extract_text_urls("https://www.saramin.co.kr/company\n_nm/test?nomo=1\n"),
                         ["https://www.saramin.co.kr/company_nm/test?nomo=1"])
        with tempfile.TemporaryDirectory() as temp:
            for filename in COMPANY_FILES:
                source = Path(temp) / filename
                render_report("# 링크 자료\nhttps://www.figure.ai/news/series-c", source)
                writer = PdfWriter()
                writer.append(str(source))
                writer.add_annotation(0, {"/Type": "/Annot", "/Subtype": "/Link", "/Rect": [20, 20, 50, 50],
                    "/A": DictionaryObject({NameObject("/S"): NameObject("/URI"),
                    NameObject("/URI"): TextStringObject("https://www.sec.gov/Archives/data.xml")})})
                with source.open("wb") as stream:
                    writer.write(stream)
            registry = load_link_registry(temp)
            self.assertEqual(set(registry), {"Figure AI", "ROBROS", "MOTOMIND"})
            self.assertIn("https://www.figure.ai/news/series-c", registry["Figure AI"])
            entry = registry["Figure AI"]["https://www.sec.gov/Archives/data.xml"]
            self.assertEqual(entry["origin_pages"], [1])
            self.assertEqual(entry["doc_id"], registry_for(entry["url"])["Figure AI"][entry["url"]]["doc_id"])

    def test_disallowed_urls_never_fetch(self):
        for url in ("http://localhost/a", "https://127.0.0.1/a", "https://10.0.0.1/", "https://user:pass@www.figure.ai/"):
            with self.assertRaises(ValueError):
                _public_url(url)
        with tempfile.TemporaryDirectory() as temp:
            reader = WebSourceReader(registry_for("https://www.figure.ai/"), temp)
            reader._fetch = Mock(side_effect=AssertionError("네트워크가 호출되면 안 됨"))
            result = reader.read("Figure AI", ["https://www.example.com/"])
            self.assertEqual(len(result["errors"]), 1)
            reader._fetch.assert_not_called()
            self.assertEqual(reader.read("Figure AI", []), {"documents": [], "catalog": {}, "errors": []})

    def test_html_pdf_cache_and_reopen(self):
        url = "https://www.figure.ai/news/series-c"
        with tempfile.TemporaryDirectory() as temp:
            reader = WebSourceReader(registry_for(url), temp)
            reader._fetch = Mock(return_value=(HTML, "text/html; charset=utf-8", url))
            result = reader.read("Figure AI", [url])
            self.assertEqual(result["errors"], [])
            self.assertEqual(len(result["documents"]), 1)
            text = result["documents"][0].page_content
            self.assertIn("시각 인지", text)
            self.assertNotIn("제외할 메뉴", text)
            self.assertNotIn("위험한 스크립트", text)
            entry = next(iter(result["catalog"].values()))
            self.assertEqual(entry["page_count"], len(PdfReader(entry["source_path"]).pages))
            self.assertEqual(result["documents"][0].metadata["page"], 1)
            self.assertEqual(entry["source_kind"], "web")
            self.assertEqual(entry["origin_pages"], [2])
            self.assertEqual(entry["publisher"], "www.figure.ai")
            reader.read("Figure AI", [url])
            reader._fetch.assert_called_once()
            second = WebSourceReader(registry_for(url), temp)
            second._fetch = Mock(side_effect=AssertionError("캐시 재사용"))
            self.assertEqual(second.read("Figure AI", [url])["errors"], [])
            second._fetch.assert_not_called()

    def test_parallel_reuses_one_cache(self):
        url = "https://www.figure.ai/news/series-c"
        with tempfile.TemporaryDirectory() as temp:
            reader = WebSourceReader(registry_for(url), temp)
            reader._fetch = Mock(return_value=(HTML, "text/html; charset=utf-8", url))
            with ThreadPoolExecutor(max_workers=3) as pool:
                results = list(pool.map(lambda _: reader.read("Figure AI", [url]), range(3)))
            self.assertTrue(all(not result["errors"] for result in results))
            reader._fetch.assert_called_once()

    def test_explicit_publication_metadata_and_unknown(self):
        url = "https://www.figure.ai/news/series-c"
        dated = HTML.replace(b"<head>", b'<head><meta property="article:published_time" content="2025-09-16T09:00:00Z">')
        with tempfile.TemporaryDirectory() as temp:
            for name, body, expected in [("dated", dated, "2025-09-16T09:00:00Z"), ("unknown", HTML, "")]:
                reader = WebSourceReader(registry_for(url), Path(temp) / name)
                reader._fetch = Mock(return_value=(body, "text/html; charset=utf-8", url))
                result = reader.read("Figure AI", [url])
                self.assertEqual(result["errors"], [])
                entry = next(iter(result["catalog"].values()))
                metadata = result["documents"][0].metadata
                self.assertEqual(entry["published_at"], expected)
                self.assertEqual(metadata["published_at"], expected)
                self.assertEqual(metadata["publication_date"], expected)
                self.assertEqual(metadata["title"], "공식 기술 자료")
                self.assertEqual(metadata["publisher"], "www.figure.ai")

    def test_partial_failure_and_page_budget(self):
        good, bad = "https://www.figure.ai/good", "https://www.figure.ai/blocked"
        with tempfile.TemporaryDirectory() as temp:
            reader = WebSourceReader(registry_for(good, bad), temp, existing_pages=199)
            reader._fetch = Mock(side_effect=[(b"<html><title>Access denied</title></html>", "text/html", bad),
                                             (HTML, "text/html; charset=utf-8", good)])
            result = reader.read("Figure AI", [bad, good])
            self.assertEqual(len(result["errors"]), 1)
            self.assertEqual(len(result["catalog"]), 1)
            reader._fetch = Mock(return_value=(HTML, "text/html; charset=utf-8", bad))
            failed = reader.read("Figure AI", [bad])
            self.assertIn("페이지 상한", failed["errors"][0])
            self.assertEqual(len(list(Path(temp).glob("*.pdf"))), 1)

    def test_pdf_xml_limits_and_redirect(self):
        url = "https://www.figure.ai/source"
        with tempfile.TemporaryDirectory() as temp:
            source = render_report("# 다운로드 원문\n" + "실제 다운로드 원문 텍스트. " * 10, Path(temp) / "sample.pdf")
            reader = WebSourceReader(registry_for(url), Path(temp) / "pdf-cache")
            reader._fetch = Mock(return_value=(source.read_bytes(), "application/pdf", url))
            result = reader.read("Figure AI", [url])
            self.assertEqual(result["errors"], [])
            entry = next(iter(result["catalog"].values()))
            self.assertEqual(Path(entry["source_path"]).read_bytes(), source.read_bytes())
            xml_reader = WebSourceReader(registry_for(url), Path(temp) / "xml-cache")
            xml = ("<root><name>Figure AI</name><description>" + "Original filing text " * 10 + "</description></root>").encode()
            xml_reader._fetch = Mock(return_value=(xml, "application/xml", url))
            self.assertEqual(xml_reader.read("Figure AI", [url])["errors"], [])
            for index, response in enumerate([(b"x" * (MAX_BYTES + 1), "text/plain", url),
                      (("원문 텍스트 " * 10000).encode(), "text/plain; charset=utf-8", url),
                      (HTML, "text/html", "https://www.unregistered.com/")]):
                bounded = WebSourceReader(registry_for(url), Path(temp) / f"limit-{index}")
                bounded._fetch = Mock(return_value=response)
                outcome = bounded.read("Figure AI", [url])
                self.assertEqual(len(outcome["errors"]), 1)
                self.assertFalse(outcome["catalog"])


if __name__ == "__main__":
    unittest.main()
