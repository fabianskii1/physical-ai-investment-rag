"""준비 자료의 링크만 읽고, 페이지 인용이 가능한 원문 PDF 캐시를 만든다.

com_data PDF의 기업 요약·점수는 근거로 사용하지 않는다. 여기서는 URL과
URL이 실린 PDF 위치만 추출한다. 웹 검색, 하위 링크 탐색, 로그인은 하지 않는다.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import tempfile
import threading
from datetime import datetime, timezone
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from xml.etree import ElementTree

import pdfplumber
from langchain_core.documents import Document
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate

from report_renderer import _korean_font

MAX_BYTES = 8 * 1024 * 1024
MAX_TEXT_CHARS = 60_000
COMPANY_FILES = {"Figure_AI.pdf": "Figure AI", "ROBROS.pdf": "ROBROS", "MOTOMIND.pdf": "MOTOMIND"}


def _public_url(value: str, *, resolve: bool = False) -> str:
    """공개 HTTP(S) 주소만 허용한다. 실제 요청 직전에 DNS 결과도 확인한다."""
    if not isinstance(value, str) or any(ord(c) <= 32 for c in value):
        raise ValueError("URL에 공백 또는 제어 문자가 있습니다.")
    parts = urlsplit(value)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise ValueError("HTTP(S) URL이 아닙니다.")
    if parts.username is not None or parts.password is not None:
        raise ValueError("사용자 인증정보가 포함된 URL은 허용하지 않습니다.")
    host = parts.hostname.lower().rstrip(".").encode("idna").decode("ascii")
    if parts.port not in (None, 80, 443):
        raise ValueError("표준 웹 포트만 허용합니다.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host or host.endswith((".local", ".localhost", ".internal", ".test", ".invalid")):
            raise ValueError("로컬 호스트는 허용하지 않습니다.")
    else:
        if not address.is_global:
            raise ValueError("비공개 IP 주소는 허용하지 않습니다.")
    if resolve:
        addresses = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise ValueError("호스트가 비공개 IP 주소로 연결됩니다.")
    netloc = f"[{host}]" if ":" in host else host
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", parts.query, ""))


def extract_text_urls(text: str) -> list[str]:
    """본문 URL과 다음 줄로 이어진 경로를 복원한다. 설명 문장은 이어 붙이지 않는다."""
    lines, urls = text.splitlines(), []
    for index, line in enumerate(lines):
        for match in re.finditer(r"https?://[^\s<>\"'；;]+", line):
            value = match.group().rstrip(".,)]}")
            cursor = index + 1
            while cursor < len(lines) and match.end() == len(line.rstrip()):
                next_line = lines[cursor].strip()
                continues = (value.endswith(("/", "-", "%")) or next_line.startswith(("_", "/", "?", "#", "%")))
                if not continues or not re.fullmatch(r"[A-Za-z0-9_~.%/?#=&:+@-]+", next_line) or next_line.isdigit():
                    break
                value += next_line
                cursor += 1
            try:
                urls.append(_public_url(value))
            except ValueError:
                pass
    return list(dict.fromkeys(urls))


def load_link_registry(folder: str | Path) -> dict[str, dict[str, dict]]:
    """세 준비 PDF의 URI annotation과 본문 링크를 기업별 허용 목록으로 등록한다."""
    registry = {company: {} for company in COMPANY_FILES.values()}
    for filename, company in COMPANY_FILES.items():
        path = Path(folder) / filename
        reader = PdfReader(path)
        for page_number, page in enumerate(reader.pages, 1):
            candidates = extract_text_urls(page.extract_text() or "")
            for annotation in page.get("/Annots", []):
                action = annotation.get_object().get("/A")
                if action and action.get("/URI"):
                    candidates.append(str(action["/URI"]))
            for candidate in candidates:
                try:
                    url = _public_url(candidate)
                except ValueError:
                    continue
                entry = registry[company].setdefault(url, {
                    "doc_id": "WEB-" + hashlib.sha256(url.encode()).hexdigest()[:16],
                    "company": company, "url": url, "origin_pdf": str(path.resolve()), "origin_pages": [],
                })
                if page_number not in entry["origin_pages"]:
                    entry["origin_pages"].append(page_number)
    return registry


class _BodyParser(HTMLParser):
    """스크립트·내비게이션 등을 제외하고 main/article가 있으면 해당 본문을 우선한다."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.all_parts, self.main_parts, self.title_parts = [], [], []
        self.skip, self.main, self.title = 0, 0, 0
        self.publication_date, self._date_priority = "", 0

    def _record_date(self, value, priority):
        """원문이 명시한 유효 ISO 일자만 보존한다. 조회일을 발행일로 추정하지 않는다."""
        if not isinstance(value, str) or not re.match(r"^\d{4}-\d{2}-\d{2}(?:$|[Tt ])", value):
            return
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return
        if priority > self._date_priority:
            self.publication_date, self._date_priority = value, priority

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "meta":
            field = (attributes.get("property") or attributes.get("name") or attributes.get("itemprop") or "").lower()
            if field in ("article:published_time", "date", "datepublished"):
                self._record_date(attributes.get("content"), 2)
        elif tag == "time" and (attributes.get("itemprop") or "").lower() != "datemodified":
            self._record_date(attributes.get("datetime"), 1)
        if tag in ("script", "style", "nav", "header", "footer", "form", "noscript", "svg"):
            self.skip += 1
        if tag in ("main", "article"):
            self.main += 1
        if tag == "title":
            self.title += 1
        if tag in ("p", "div", "br", "li", "h1", "h2", "h3", "tr") and not self.skip:
            self.all_parts.append("\n")
            if self.main:
                self.main_parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "header", "footer", "form", "noscript", "svg"):
            self.skip = max(0, self.skip - 1)
        if tag in ("main", "article"):
            self.main = max(0, self.main - 1)
        if tag == "title":
            self.title = max(0, self.title - 1)
        if tag in ("p", "div", "li", "h1", "h2", "h3", "tr") and not self.skip:
            self.all_parts.append("\n")
            if self.main:
                self.main_parts.append("\n")

    def handle_data(self, data):
        if self.title:
            self.title_parts.append(data)
        if not self.skip and not self.title:
            self.all_parts.append(data)
            if self.main:
                self.main_parts.append(data)

    def result(self):
        parts = self.main_parts if "".join(self.main_parts).strip() else self.all_parts
        text = "\n".join(" ".join(line.split()) for line in "".join(parts).splitlines() if line.strip())
        return text, " ".join(self.title_parts).strip()


class WebSourceReader:
    """워커가 선택한 등록 URL 최대 세 개를 읽는다. 실패는 URL별 오류로 반환한다.

    HTML/XML은 읽은 텍스트를 그대로 PDF로 보존한 스냅샷이다. HTML 원래 페이지
    번호가 있다는 뜻이 아니며, 반환 page는 스냅샷 PDF 기준 1부터 시작한다.
    같은 프로세스의 병렬 워커는 캐시 잠금을 공유한다. 독립 프로세스 동시 실행은 지원하지 않는다.
    """

    _locks: dict[str, threading.RLock] = {}
    _locks_guard = threading.Lock()

    def __init__(self, registry, cache_dir, existing_pages=106, max_total_pages=200):
        if not 0 <= existing_pages <= max_total_pages <= 200:
            raise ValueError("기존 자료와 웹 자료의 합산 상한은 최대 200쪽이어야 합니다.")
        self.registry, self.cache_dir = registry, Path(cache_dir).resolve()
        self.existing_pages, self.max_total_pages = existing_pages, max_total_pages
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.cache_dir / "manifest.json"
        self._memory = {}
        with self._locks_guard:
            self._lock = self._locks.setdefault(str(self.cache_dir), threading.RLock())

    def _fetch(self, url: str) -> tuple[bytes, str, str]:
        # 시작 주소는 read에서 허용 목록을 확인한다. redirect도 공개 주소여야 한다.
        _public_url(url, resolve=True)
        allowed_hosts = {urlsplit(item).hostname for entries in self.registry.values() for item in entries}

        class SafeRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                canonical = _public_url(newurl, resolve=True)
                if urlsplit(canonical).hostname not in allowed_hosts:
                    raise ValueError("등록되지 않은 호스트로의 리다이렉트는 허용하지 않습니다.")
                return super().redirect_request(req, fp, code, msg, headers, canonical)

        request = Request(url, headers={"User-Agent": "SKALA-RAG/1.0", "Accept": "text/html,application/xml,application/pdf,text/plain"})
        with build_opener(SafeRedirect()).open(request, timeout=15) as response:
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise ValueError("응답이 8MB 상한을 넘습니다.")
            return body, response.headers.get("Content-Type", ""), _public_url(response.geturl())

    def _manifest(self):
        if not self.manifest_path.exists():
            return {}
        data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        # 없는 캐시는 실제로 사용되지 않으므로 페이지 예산에 포함하지 않는다.
        return {key: entry for key, entry in data.items() if Path(entry["source_path"]).is_file()}

    def _documents(self, entry, company):
        key = entry["doc_id"]
        if key not in self._memory:
            with pdfplumber.open(entry["source_path"]) as pdf:
                self._memory[key] = [p.extract_text() or "" for p in pdf.pages]
        return [Document(page_content=text, metadata={
            "doc_id": key, "company": company, "file_path": entry["source_path"], "page": i,
            "source_kind": "web", "source_url": entry["source_url"], "accessed_at": entry["accessed_at"],
            "title": entry.get("title", ""), "publisher": entry.get("publisher", ""),
            "publication_date": entry.get("published_at", ""), "published_at": entry.get("published_at", ""),
        }) for i, text in enumerate(self._memory[key], 1)]

    def _snapshot(self, body, content_type, path, url, accessed_at):
        """다운로드 PDF는 원래 페이지를 보존하고, 텍스트는 요약 없이 PDF에 배치한다."""
        if body.startswith(b"%PDF-"):
            path.write_bytes(body)
            return url, "pdf", ""
        if not any(kind in content_type.lower() for kind in ("html", "xml", "text/")):
            raise ValueError("HTML/XML/PDF/텍스트 이외 형식은 읽지 않습니다.")
        charset = re.search(r"charset\s*=\s*[\"']?([\w-]+)", content_type, flags=re.I)
        if not charset:
            charset = re.search(r"charset\s*=\s*[\"']?([\w-]+)", body[:2000].decode("ascii", errors="ignore"), flags=re.I)
        text = body.decode(charset.group(1) if charset else "utf-8", errors="strict")
        title, kind, published_at = url, "text", ""
        if "html" in content_type.lower():
            parser = _BodyParser()
            parser.feed(text)
            text, title = parser.result()
            title, kind = title or url, "html_snapshot"
            published_at = parser.publication_date
        elif "xml" in content_type.lower():
            ElementTree.fromstring(text)  # 손상된 XML을 정상 원문으로 취급하지 않는다.
            kind = "xml_snapshot"
        if len(text) > MAX_TEXT_CHARS:
            raise ValueError("원문 텍스트가 60,000자 상한을 넘습니다. 임의로 자르지 않습니다.")
        markers = ("access denied", "403 forbidden", "just a moment", "checking your browser", "verify you are human",
                   "subscribe to continue", "sign in to continue", "구독자 전용", "로그인 후 이용")
        lower = text.lower()
        if any(marker in title.lower() or (len(text) < 2000 and marker in lower) for marker in markers):
            raise ValueError("차단·로그인·유료 본문은 읽을 수 없습니다.")
        if len(text.strip()) < 40:
            raise ValueError("읽을 수 있는 원문 본문이 부족합니다.")
        style = ParagraphStyle("source", fontName=_korean_font(), fontSize=10, leading=15, wordWrap="CJK", spaceAfter=4)
        doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                                topMargin=20 * mm, bottomMargin=20 * mm)
        story = [Paragraph(escape(f"웹 원문 스냅샷 | {url}"), style),
                 Paragraph(escape(f"접근 시각: {accessed_at}"), style)]
        story.extend(Paragraph(escape(line), style) for line in text.splitlines() if line.strip())
        doc.build(story)
        return title, kind, published_at

    def _read_one(self, company, url):
        registration = self.registry.get(company, {}).get(url)
        if registration is None:
            raise ValueError("선택한 기업의 준비 PDF에 등록되지 않은 URL입니다.")
        manifest = self._manifest()
        key = registration["doc_id"]
        used = self.existing_pages + sum(entry["page_count"] for entry in manifest.values())
        if used > self.max_total_pages:
            raise ValueError(f"기존 자료·캐시가 이미 {used}쪽으로 상한을 넘습니다.")
        if key in manifest:
            entry = {**manifest[key], "company": company, "origin_pdf": registration["origin_pdf"],
                     "origin_pages": registration["origin_pages"]}
            return self._documents(entry, company), entry
        candidate = None
        try:
            body, content_type, resolved_url = self._fetch(url)
            if len(body) > MAX_BYTES:
                raise ValueError("응답이 8MB 상한을 넘습니다.")
            _public_url(resolved_url)
            allowed_hosts = {urlsplit(item).hostname for entries in self.registry.values() for item in entries}
            if urlsplit(resolved_url).hostname not in allowed_hosts:
                raise ValueError("등록되지 않은 호스트로의 리다이렉트는 허용하지 않습니다.")
            accessed_at = datetime.now(timezone.utc).isoformat()
            with tempfile.NamedTemporaryFile(dir=self.cache_dir, suffix=".pdf", delete=False) as temp:
                candidate = Path(temp.name)
            title, kind, published_at = self._snapshot(body, content_type, candidate, url, accessed_at)
            pages = PdfReader(candidate).pages
            page_count = len(pages)
            if page_count < 1 or used + page_count > self.max_total_pages:
                raise ValueError(f"원문 합산 페이지 상한 초과: 기존·캐시 {used} + 신규 {page_count} > {self.max_total_pages}")
            with pdfplumber.open(candidate) as pdf:
                texts = [p.extract_text() or "" for p in pdf.pages]
            if not any(text.strip() for text in texts):
                raise ValueError("추출 가능한 텍스트가 없는 원문 PDF입니다.")
            if sum(map(len, texts)) > MAX_TEXT_CHARS:
                raise ValueError("원문 텍스트가 60,000자 상한을 넘습니다.")
            final = self.cache_dir / f"{key}.pdf"
            entry = {**registration, "title": title, "file_path": str(final), "source_path": str(final),
                     "page_count": page_count, "source_kind": "web", "web_format": kind,
                     "publisher": urlsplit(resolved_url).hostname, "source_url": url,
                     "resolved_url": resolved_url, "accessed_at": accessed_at, "published_at": published_at, "category": "web"}
            os.replace(candidate, final)
            manifest[key] = entry
            temporary_manifest = self.cache_dir / "manifest.tmp"
            temporary_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary_manifest, self.manifest_path)
            self._memory[key] = texts
            return self._documents(entry, company), entry
        finally:
            if candidate is not None:
                candidate.unlink(missing_ok=True)

    def read(self, company: str, urls: list[str]) -> dict:
        documents, catalog, errors = [], {}, []
        if not isinstance(company, str) or not isinstance(urls, list) or any(not isinstance(url, str) for url in urls):
            return {"documents": [], "catalog": {}, "errors": ["company는 문자열, urls는 문자열 목록이어야 합니다."]}
        if len(urls) > 3:
            errors.append("워커 작업당 링크는 최대 3개입니다. 4번째 이후 링크는 읽지 않았습니다.")
        for original in dict.fromkeys(urls[:3]):
            try:
                url = _public_url(original)
                with self._lock:
                    pages, entry = self._read_one(company, url)
                documents.extend(pages)
                catalog[entry["doc_id"]] = entry
            except Exception as exc:
                # 한 URL의 차단·오류가 다른 URL의 성공 근거를 폐기하지 않는다.
                errors.append(f"{original}: {type(exc).__name__}: {exc}")
        return {"documents": documents, "catalog": catalog, "errors": errors}
