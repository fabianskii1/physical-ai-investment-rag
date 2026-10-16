"""통합 에이전트가 기업별 실제 보고서 전체와 문단 출처를 읽도록 만든다.

원본 Markdown은 수정하거나 자르지 않는다. 문단 인덱스만 회사별 인용 ID로
변환하며, kind는 제목·명시된 문단 표지를 분류한 값이지 새 사실 판정이 아니다.
"""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
import re
from typing import Annotated
from pydantic import StringConstraints


ReportParagraphReference = Annotated[str, StringConstraints(pattern=r'^[A-Z][A-Z0-9_]*-P[0-9]{3,}$')]
EvidenceReference = Annotated[str, StringConstraints(pattern=r'^[A-Z][A-Z0-9_]*-E[1-9][0-9]*$')]
FinalParagraphReference = Annotated[str, StringConstraints(pattern=r'^P[1-9][0-9]*$')]


_EVIDENCE_ID = re.compile(r"E[1-9]\d*\Z")
_PREFIX = re.compile(r"[A-Z][A-Z0-9_]*\Z")
_CITATION_TOKEN = r"(?:[A-Z][A-Z0-9_]*-)?E\d+"
_CITATION = re.compile(r"\[(" + _CITATION_TOKEN + r"(?:[\s,;/]+" + _CITATION_TOKEN + r")*)\]")
_TOKEN = re.compile(_CITATION_TOKEN)
_HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_LIST_ITEM = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+")
_REFERENCE_HEADING = re.compile(r"\bREFERENCES?\b|참고\s*문헌|참고\s*자료|자료\s*출처|근거\s*목록", re.IGNORECASE)
_RULE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_LABELS = (
    ("fact", re.compile(r"^(?:(?:확인|검증)(?:된)?\s*)?사실\s*:")),
    ("analysis", re.compile(r"^(?:분석(?:[·ㆍ/]\s*해석)?|해석|추론)(?:\s*\([^)]*\))?\s*:")),
    ("limitation", re.compile(r"^(?:근거(?:의)?\s*한계|평가\s*한계|판단\s*제약|한계|미확인(?:\s*사항)?)\s*:")),
    ("follow_up", re.compile(r"^(?:후속\s*확인|추가\s*(?:자료|확인)|확인\s*과제|필수\s*조건)(?:\s*[—–-]\s*[^:]+)?\s*:")),
    ("opinion", re.compile(r"^(?:투자\s*검토\s*의견|최종\s*투자\s*(?:제안|의견)|종합\s*의견(?:의\s*이유)?|판단(?:의\s*이유)?)\s*:")),
)


def _citation_ids(text: str, prefix: str, evidence: Mapping, company: str) -> list[str]:
    """표와 REFERENCE까지 포함해 선언된 모든 인용을 검증한다."""
    ids = []
    for citation in _CITATION.finditer(text):
        for token in _TOKEN.findall(citation.group(1)):
            local = token
            if "-" in token:
                owner, local = token.rsplit("-", 1)
                if owner != prefix:
                    raise ValueError(f"{company}: 다른 기업의 근거 ID가 본문에 있습니다: {token}")
            if local not in evidence:
                raise ValueError(f"{company}: 보고서에 등록되지 않은 근거 ID가 있습니다: {token}")
            namespaced = f"{prefix}-{local}"
            if namespaced not in ids:
                ids.append(namespaced)
    return ids


def _namespace_citations(text: str, prefix: str) -> str:
    def replace(match):
        return " ".join(f"[{prefix}-{token.rsplit('-', 1)[-1]}]" for token in _TOKEN.findall(match.group(1)))
    return _CITATION.sub(replace, text)


def _plain_label(text: str) -> str:
    # 표지의 서식만 제거한다. 반환되는 실제 text는 그대로 유지한다.
    return _LIST_ITEM.sub("", text.lstrip()).replace("**", "").replace("__", "").strip()


def _kind(text: str, headings: dict[int, str]) -> str:
    plain = _plain_label(text)
    for kind, pattern in _LABELS:
        if pattern.match(plain):
            return kind
    section = headings[max(headings)] if headings else ""
    if re.search(r"후속\s*확인|투자\s*전\s*필수\s*조건|확인\s*(?:과제|계획)", section):
        return "follow_up"
    if re.search(r"판단\s*제약|근거(?:의)?\s*한계|평가\s*한계|리스크|위험", section):
        return "limitation"
    if re.search(r"최종\s*투자|투자\s*제안|투자\s*판단|종합\s*의견", section):
        return "opinion"
    if re.search(r"분석|해석|판정\s*이유|왜\s*이\s*기업|비교\s*검토", section):
        return "analysis"
    return "other"


def _table_separator(line: str) -> bool:
    cells = line.strip().strip("|").split("|")
    return len(cells) >= 2 and all(re.fullmatch(r"\s*:?-{3,}:?\s*", cell) for cell in cells)


def _paragraphs(markdown: str, prefix: str, evidence: Mapping, company: str) -> list[dict]:
    lines = markdown.splitlines()
    paragraphs, buffer, headings = [], [], {}
    in_table = False
    fence = None

    def flush():
        if not buffer:
            return
        text = "\n".join(buffer).strip()
        buffer.clear()
        if not text or any(_REFERENCE_HEADING.search(title) for title in headings.values()):
            return
        context = [title for level, title in sorted(headings.items()) if level >= 2]
        if not context:
            context = [title for _, title in sorted(headings.items())]
        ids = _citation_ids(text, prefix, evidence, company)
        paragraphs.append({"paragraph_id": f"{prefix}-P{len(paragraphs) + 1:03d}",
                           "section": " > ".join(context),
                           "text": _namespace_citations(text, prefix),
                           "evidence_ids": ids, "kind": _kind(text, headings)})

    for index, line in enumerate(lines):
        stripped = line.strip()
        if fence is not None:
            buffer.append(line)
            if re.match(r"^ {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*$", line):
                fence = None
                flush()
            continue
        opening = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if opening:
            flush()
            fence = opening.group(1)
            buffer.append(line)
            in_table = False
            continue
        heading = _HEADING.match(line)
        if heading:
            flush()
            level = len(heading.group(1))
            for old in [old for old in headings if old >= level]:
                del headings[old]
            headings[level] = heading.group(2)
            in_table = False
            continue
        if not stripped or _RULE.match(line):
            flush()
            in_table = False
            continue
        starts_table = (stripped.startswith("|") or
                        ("|" in line and index + 1 < len(lines) and _table_separator(lines[index + 1])))
        if starts_table or (in_table and "|" in line):
            flush()
            in_table = True
            continue
        in_table = False
        # 각 리스트 항목과 명시된 사실/해석/한계/후속 확인 표지를 별도 문단으로 유지한다.
        if _LIST_ITEM.match(line) or any(pattern.match(_plain_label(line)) for _, pattern in _LABELS):
            flush()
        buffer.append(line)
    flush()
    return paragraphs


def build_company_report_inputs(companies: list[dict]) -> list[dict]:
    """정규화된 기업 결과에서 전체 보고서와 누락 없는 본문 문단 입력을 만든다.

    원본을 변경하지 않는다. 표와 참고문헌은 report_markdown에 보존하고 문단
    인덱스에서는 제외한다. 인용 없는 문단도 제한 개수 없이 모두 전달한다.
    """
    if not isinstance(companies, list) or not companies:
        raise ValueError("기업별 보고서 입력은 비어 있지 않은 기업 목록이어야 합니다")
    results, seen_companies, seen_prefixes = [], set(), set()
    for record in companies:
        if not isinstance(record, Mapping):
            raise ValueError("기업별 보고서 입력의 기업 형식이 올바르지 않습니다")
        company, prefix = record.get("company"), record.get("prefix")
        if not isinstance(company, str) or not company.strip() or not isinstance(prefix, str) or not _PREFIX.fullmatch(prefix):
            raise ValueError("기업별 보고서 입력에는 기업명과 회사별 prefix가 필요합니다")
        if company in seen_companies or prefix in seen_prefixes:
            raise ValueError(f"{company}: 기업명 또는 문단 prefix가 중복되었습니다")
        seen_companies.add(company)
        seen_prefixes.add(prefix)
        state = record.get("state")
        report = state.get("final_report") if isinstance(state, Mapping) else None
        if not isinstance(report, str) or not report.strip():
            raise ValueError(f"{company}: 실제 기업별 final_report 본문이 없습니다")
        # 출력된 Markdown과 State가 서로 다르면 임의로 한쪽을 선택하지 않는다.
        # MD만 수정하고 예전 State를 합성하는 경우를 모델 호출 전에 드러낸다.
        artifact = record.get('record', {}).get('markdown')
        if artifact is not None:
            artifact = Path(artifact)
            if not artifact.is_file() or artifact.read_text(encoding='utf-8') != report:
                raise ValueError(f"{company}: 기업 보고서 Markdown과 State final_report가 일치하지 않습니다")
        evidence = record.get("evidence")
        if not isinstance(evidence, Mapping) or any(not isinstance(ref, str) or not _EVIDENCE_ID.fullmatch(ref) for ref in evidence):
            raise ValueError(f"{company}: 등록된 근거 ID 목록 형식이 올바르지 않습니다")
        _citation_ids(report, prefix, evidence, company)
        paragraphs = _paragraphs(report, prefix, evidence, company)
        if not paragraphs:
            raise ValueError(f"{company}: 기업별 보고서에 분석할 본문 문단이 없습니다")
        results.append({"company": company, "prefix": prefix, "trace_id": record.get("trace_id"),
                        "report_sha256": sha256(report.encode("utf-8")).hexdigest(),
                        "report_markdown": report, "paragraphs": paragraphs})
    return results


def validate_company_report_inputs(companies: list[dict], supplied=None) -> list[dict]:
    """모든 최종 에이전트가 같은 원보고서·키·문단을 받는지 확인한다.

    caller가 건넨 SHA만 믿지 않고 State와 다시 대조한다. 잘못된 report 키,
    빈 목록, 오래된 본문과 바뀐 문단 인덱스는 요약으로 대체하지 않는다.
    """
    expected = build_company_report_inputs(companies)
    if supplied is None:
        return expected
    if not isinstance(supplied, list) or supplied != expected:
        raise ValueError('기업 보고서 입력 계약 불일치: company_reports의 report_markdown·SHA256·문단을 현재 State와 대조하세요')
    return expected
