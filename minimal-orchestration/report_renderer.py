"""검증된 Markdown 보고서를 A4 PDF로 출력한다. 내용 판단은 그래프가 담당한다."""

import os
import re
import tempfile
from html import escape
from pathlib import Path

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import BaseDocTemplate, Frame, LayoutError, LongTable, PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle

MARGIN_MM = 20
BODY_SIZE = 10
BODY_LEADING = 15
TABLE_SIZE = 9


class ReportLayoutError(ValueError):
    """페이지 상한 또는 배치 규칙 미달. 기존 최종 파일은 덮어쓰지 않는다."""


def _korean_font() -> str:
    """한글 TTF를 PDF에 포함한다. 운영 환경에서는 RAG_PDF_FONT로 지정 가능하다."""
    name = "AgentReportKorean"
    if name not in pdfmetrics.getRegisteredFontNames():
        candidates = [os.getenv("RAG_PDF_FONT"), "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
                      "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"]
        path = next((p for p in candidates if p and Path(p).is_file()), None)
        if not path:
            raise ReportLayoutError("한글 TTF가 없습니다. RAG_PDF_FONT에 폰트 경로를 지정하세요.")
        pdfmetrics.registerFont(TTFont(name, path))
        pdfmetrics.registerFontFamily(name, normal=name, bold=name, italic=name, boldItalic=name)
    return name


def validate_pdf(path: str | Path, max_pages: int = 10) -> dict:
    """실제 PDF의 쪽 수·용지 크기·텍스트 경계를 검사한다. 시각 검토를 대신하지 않는다."""
    import pdfplumber

    pages = PdfReader(path).pages
    a4 = all(abs(float(p.mediabox.width) - A4[0]) < 0.1 and
             abs(float(p.mediabox.height) - A4[1]) < 0.1 for p in pages)
    margin = MARGIN_MM * mm
    with pdfplumber.open(path) as pdf:
        inside = all(c["x0"] >= margin - 0.5 and c["x1"] <= p.width - margin + 0.5
                     and c["top"] >= margin - 0.5 and c["bottom"] <= p.height - margin + 0.5
                     for p in pdf.pages for c in p.chars)
    return {"pages": len(pages), "all_a4": a4, "within_page_limit": 1 <= len(pages) <= max_pages,
            "text_within_margins": inside, "margins_mm": MARGIN_MM,
            "body_font_pt": BODY_SIZE, "table_font_pt": TABLE_SIZE, "visual_review_required": True}


def render_report(markdown: str, target: str | Path, max_pages: int = 10) -> Path:
    """A4·상하좌우 20mm·최대 10쪽을 적용하고, 검사 통과한 PDF만 최종 경로에 저장한다."""
    if not isinstance(markdown, str) or not markdown.strip():
        raise ValueError("변환할 보고서 내용이 없습니다.")
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or not 1 <= max_pages <= 10:
        raise ValueError("max_pages는 과제 상한을 넘지 않는 1~10 정수여야 합니다.")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    font, navy = _korean_font(), colors.HexColor("#18334F")
    styles = {}
    for key, size, leading in [("title", 17, 24), ("section", 13, 19), ("subheading", 11, 16),
                               ("body", BODY_SIZE, BODY_LEADING), ("table", TABLE_SIZE, 13)]:
        styles[key] = ParagraphStyle(key, fontName=font, fontSize=size, leading=leading,
            # CJK의 금칙처리는 ':'·괄호를 오른쪽 여백에 내어 걸 수 있다.
            # 한국어의 띄어쓰기를 우선하고 긴 URL·붙은 단어는 폭 안에서 나눈다.
            # 실제 글자 경계도 20mm 안에 두기 위해 여백 밖 문장부호 걸기를 쓰지 않는다.
            wordWrap="LTR", splitLongWords=1, spaceAfter=5 if key == "body" else 9,
            textColor=navy if key in ("title", "section", "subheading") else colors.HexColor("#263442"),
            alignment=TA_CENTER if key == "title" else 0,
            keepWithNext=key in ("section", "subheading"))
    # 인용 ID만 다음 쪽 첫 줄로 떨어지는 한 줄짜리 문단 조각을 막는다.
    styles['body'].allowWidows = 0
    styles['body'].allowOrphans = 0
    styles["bullet"] = ParagraphStyle("bullet", parent=styles["body"], leftIndent=9, firstLineIndent=-9)
    styles["score_label"] = ParagraphStyle("score_label", parent=styles["body"], leading=13,
                                           spaceAfter=0, textColor=navy)
    styles["score_value"] = ParagraphStyle("score_value", parent=styles["body"], fontSize=22,
                                           leading=26, spaceAfter=0, textColor=navy)
    story, table_rows, sections = [], [], 0
    width = A4[0] - 2 * MARGIN_MM * mm

    def paragraph(text: str, kind: str):
        # 원문 HTML은 실행하지 않고, 간단한 굵게 표시만 허용한다.
        safe = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escape(text))
        return Paragraph(safe, styles[kind])

    def score_panel(line: str):
        # 원문 점수·미확인 표현을 그대로 강조한다. 확인 배점을 100점으로
        # 재환산하거나 산정 보류를 0점으로 바꾸는 계산은 렌더러에서 하지 않는다.
        label, value = line[2:].split(":", 1)
        value = value.strip()
        match = re.fullmatch(r"(\d+(?:\.\d+)?점|U|산정\s*보류)(.*)", value)
        if not match:
            return paragraph("• " + line[2:], "bullet")
        metric, qualifier = match.groups()
        metric_size = 22 if metric.endswith("점") else 18
        content = (f'<font size="{metric_size}">{escape(metric)}</font>'
                   f'<font size="{BODY_SIZE}">{escape(qualifier)}</font>')
        panel = Table([["", paragraph(label + ":", "score_label")],
                       ["", Paragraph(content, styles["score_value"])]],
                      colWidths=[3, width - 3], hAlign="LEFT", splitByRow=0)
        panel.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EDF3F8")),
            ("SPAN", (0, 0), (0, 1)),
            # 선의 절반이 여백 밖으로 나가지 않도록 경계 안의 색 면으로 표시한다.
            ("BACKGROUND", (0, 0), (0, 1), navy),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("LEFTPADDING", (0, 0), (0, 1), 0),
            ("RIGHTPADDING", (0, 0), (0, 1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (1, 0), (1, 0), 7),
            ("BOTTOMPADDING", (1, 1), (1, 1), 8),
        ]))
        return panel

    def flush_table():
        if not table_rows:
            return
        count = len(table_rows[0])
        if any(len(row) != count for row in table_rows):
            raise ReportLayoutError("Markdown 표의 열 개수가 일치하지 않습니다.")
        widths = [width] if count == 1 else [width * .4] + [width * .6 / (count - 1)] * (count - 1)
        table = LongTable([[paragraph(c, "table") for c in row] for row in table_rows],
                          colWidths=widths, repeatRows=1, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EAF0F5")),
            ("GRID", (0, 0), (-1, -1), .3, colors.HexColor("#BCC7D2")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.extend([table, Spacer(1, 3 * mm)])
        table_rows.clear()

    for raw in markdown.splitlines():
        line = raw.strip()
        if line.startswith("|") and line.endswith("|"):
            cells = [c.strip() for c in line[1:-1].split("|")]
            if not all(re.fullmatch(r":?-+:?", c) for c in cells):
                table_rows.append(cells)
            continue
        flush_table()
        if not line:
            continue
        if line.startswith("## "):
            if sections:
                story.append(PageBreak())
            sections += 1
            story.append(paragraph(line[3:], "section"))
        elif line.startswith("### "):
            story.append(paragraph(line[4:], "subheading"))
        elif line.startswith("# "):
            story.append(paragraph(line[2:], "title"))
        elif line.startswith("- 확인된 획득점수:"):
            story.extend([score_panel(line), Spacer(1, 2 * mm)])
        else:
            story.append(paragraph("• " + line[2:] if line.startswith("- ") else line,
                                   "bullet" if line.startswith("- ") else "body"))
    flush_table()
    candidate = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", dir=target.parent, delete=False) as temp:
            candidate = Path(temp.name)
        margin = MARGIN_MM * mm
        doc = BaseDocTemplate(str(candidate), pagesize=A4, leftMargin=margin, rightMargin=margin,
                              topMargin=margin, bottomMargin=margin, title="기업 투자 검토 보고서")
        # 페이지 번호도 20mm 경계 안에 둔다. 본문 아래 9mm는 번호를 위한 공간이다.
        frame = Frame(margin, margin + 9 * mm, doc.width, doc.height - 9 * mm,
                      leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)

        def footer(canvas, document):
            canvas.setFont(font, 8)
            canvas.setFillColor(colors.HexColor("#66788A"))
            canvas.drawRightString(A4[0] - margin, margin + 2 * mm, str(document.page))

        doc.addPageTemplates(PageTemplate(id="report", frames=[frame], onPage=footer))
        doc.build(story)
        check = validate_pdf(candidate, max_pages)
        if not all(check[k] for k in ("all_a4", "within_page_limit", "text_within_margins")):
            raise ReportLayoutError(f"PDF 형식 미달: {check['pages']}쪽 (상한 {max_pages}), 검사 {check}")
        os.replace(candidate, target)
        return target
    except LayoutError as exc:
        raise ReportLayoutError("표 또는 문단이 A4 본문 영역에 배치되지 않습니다. 내용을 편집하세요.") from exc
    finally:
        if candidate is not None:
            candidate.unlink(missing_ok=True)
