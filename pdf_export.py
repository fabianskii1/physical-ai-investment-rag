"""투자 검토 Markdown 보고서를 한글 PDF로 변환한다."""

import os
from html import escape
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _korean_font() -> str:
    font_name = "RAGReportKorean"
    if font_name in pdfmetrics.getRegisteredFontNames():
        return font_name
    candidates = (
        os.getenv("RAG_PDF_FONT"),
        "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    )
    font_path = next((Path(path) for path in candidates if path and Path(path).is_file()), None)
    if font_path is None:
        raise RuntimeError("한글 PDF 폰트가 없습니다. RAG_PDF_FONT에 TTF 경로를 지정하세요")
    pdfmetrics.registerFont(TTFont(font_name, str(font_path)))
    return font_name


def markdown_to_pdf(markdown: str, target: str | Path) -> Path:
    """현재 보고서의 제목·불릿·표·5개 구역을 PDF로 보존한다."""
    if not isinstance(markdown, str) or not markdown.strip():
        raise ValueError("변환할 보고서 내용이 없습니다")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    font = _korean_font()
    styles = {
        "title": ParagraphStyle("title", fontName=font, fontSize=16, leading=22, spaceAfter=13, alignment=TA_CENTER, wordWrap="CJK"),
        "section": ParagraphStyle("section", fontName=font, fontSize=12, leading=17, spaceAfter=10, wordWrap="CJK"),
        "subheading": ParagraphStyle("subheading", fontName=font, fontSize=9.5, leading=14, spaceBefore=8, spaceAfter=3, wordWrap="CJK"),
        "body": ParagraphStyle("body", fontName=font, fontSize=8.5, leading=13, spaceAfter=3, wordWrap="CJK"),
        "bullet": ParagraphStyle("bullet", fontName=font, fontSize=8.5, leading=13, leftIndent=10, firstLineIndent=-8, spaceAfter=3, wordWrap="CJK"),
        "table": ParagraphStyle("table", fontName=font, fontSize=8, leading=11, wordWrap="CJK"),
    }
    width = A4[0] - 34 * mm
    story = []
    table_rows: list[list[str]] = []
    section_count = 0

    def flush_table() -> None:
        if not table_rows:
            return
        rows = [[Paragraph(escape(cell), styles["table"]) for cell in row] for row in table_rows]
        columns = len(table_rows[0])
        widths = [width * 0.4] + [width * 0.6 / (columns - 1)] * (columns - 1)
        table = Table(rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E9EEF8")),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#B9C4D4")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.extend((table, Spacer(1, 5 * mm)))
        table_rows.clear()

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if line.startswith("|") and line.endswith("|"):
            cells = [cell.strip() for cell in line[1:-1].split("|")]
            if all(cell and set(cell) <= {"-", ":"} for cell in cells):
                continue
            table_rows.append(cells)
            continue
        flush_table()
        if not line:
            continue
        if line.startswith("## "):
            if section_count:
                story.append(PageBreak())
            section_count += 1
            story.append(Paragraph(escape(line[3:]), styles["section"]))
        elif line.startswith("# "):
            story.append(Paragraph(escape(line[2:]), styles["title"]))
        elif line.startswith("### "):
            story.append(Paragraph(escape(line[4:]), styles["subheading"]))
        elif line.startswith("- "):
            story.append(Paragraph("• " + escape(line[2:]), styles["bullet"]))
        else:
            story.append(Paragraph(escape(line), styles["body"]))
    flush_table()

    def footer(canvas, document) -> None:
        canvas.setFont(font, 8)
        canvas.setFillColor(colors.HexColor("#64748B"))
        canvas.drawRightString(A4[0] - 17 * mm, 10 * mm, str(document.page))

    doc = SimpleDocTemplate(
        str(target), pagesize=A4, leftMargin=17 * mm, rightMargin=17 * mm,
        topMargin=16 * mm, bottomMargin=17 * mm,
    )
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return target
