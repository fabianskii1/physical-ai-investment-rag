#!/usr/bin/env python3
"""Draw the implementation architecture as SVG and PNG without model/API calls.

The upper panel describes agent_pipeline.py's StateGraph. The lower panel shows
the separate app.py batch integration, not additional LangGraph node executions.
Source contracts are checked before rendering; this diagram is not a live trace.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
from html import escape
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
WIDTH, HEIGHT = 2500, 2390
NAVY, BLUE, MUTED = "#14243A", "#1D4ED8", "#52627A"
BORDER, LIGHT, ORANGE = "#CAD5E4", "#F3F7FD", "#B45309"
TEAL, GREEN = "#0F766E", "#EAF7F3"


def check_source_contract() -> tuple[str, bool]:
    source = (ROOT / "agent_pipeline.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    expected = {"orchestrator", "worker", "synthesizer", "judgement", "score_review", "writer", "report_evaluator"}
    declared = {node.elts[0].value for node in ast.walk(tree)
                if isinstance(node, ast.Tuple) and len(node.elts) == 2
                and isinstance(node.elts[0], ast.Constant) and isinstance(node.elts[0].value, str)
                and isinstance(node.elts[1], ast.Name)}
    if expected != declared:
        raise RuntimeError("StateGraph nodes changed; update the architecture layout before rendering")
    routes = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_edge":
            if len(node.args) >= 2 and all(isinstance(value, ast.Constant) for value in node.args[:2]):
                routes.add(tuple(value.value for value in node.args[:2]))
    if not {("worker", "synthesizer"), ("judgement", "score_review"), ("writer", "report_evaluator")} <= routes:
        raise RuntimeError("StateGraph routes changed; update this source diagram")
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    batch_ready = all((ROOT / name).is_file() for name in
                      ("portfolio_decision.py", "combined_report.py", "combined_quality.py",
                       "company_report_inputs.py", "report_synthesis.py"))
    batch_ready = batch_ready and all(name in app_source for name in
                                     ("save_combined_report", "select_portfolio_investment", "evaluate_combined_quality",
                                      "quality_reviser=revise_combined_quality", "build_company_report_inputs",
                                      "synthesize_company_reports", "report_inputs=report_inputs", "report_synthesis=synthesis"))
    sources = [source, app_source]
    for name in ("company_report_inputs.py", "report_synthesis.py", "combined_quality.py"):
        path = ROOT / name
        if path.is_file():
            sources.append(path.read_text(encoding="utf-8"))
    return hashlib.sha256("\n".join(sources).encode()).hexdigest()[:12], batch_ready


def find_font(override: str | None = None) -> Path:
    candidates = ([Path(override)] if override else []) + [
        Path("/System/Library/Fonts/Supplemental/AppleGothic.ttf"),
        ROOT / "assets/fonts/NanumGothic.ttf",
        Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("A Korean font is required; pass --font /absolute/path/font.ttf")


class Drawing:
    def __init__(self, font_path: Path):
        self.bitmap = Image.new("RGB", (WIDTH, HEIGHT), "white")
        self.draw = ImageDraw.Draw(self.bitmap)
        self.font_path = font_path
        self.bold_path = Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf")
        self.fonts = {}
        self.svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" role="img">',
                    '<title>Physical AI investment orchestration architecture</title>',
                    '<desc>Source architecture: company StateGraph followed by full company-report inputs, report synthesis, shared-analysis portfolio selection, fixed-score rendering and core quality plus report-reflection evaluation. This is not an execution trace.</desc>',
                    f'<rect width="{WIDTH}" height="{HEIGHT}" fill="white"/>']

    def font(self, size, bold=False):
        key = (size, bold)
        if key not in self.fonts:
            path = self.bold_path if bold and self.bold_path.is_file() else self.font_path
            self.fonts[key] = ImageFont.truetype(str(path), size)
        return self.fonts[key]

    def rect(self, box, fill="white", stroke=BORDER, radius=18, width=3):
        x, y, w, h = box
        self.draw.rounded_rectangle((x, y, x + w, y + h), radius=radius, fill=fill, outline=stroke, width=width)
        self.svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" stroke="{stroke}" stroke-width="{width}"/>')

    def text(self, x, y, value, size=27, color=NAVY, bold=False, center=True):
        font = self.font(size, bold)
        bbox = self.draw.textbbox((0, 0), value, font=font)
        if center and bbox[2] - bbox[0] > 2350:
            raise RuntimeError(f"Diagram text exceeds canvas: {value}")
        self.draw.text((x, y), value, font=font, fill=color, anchor="mm" if center else "lm")
        family = "Arial, sans-serif" if bold else "AppleGothic, NanumGothic, Noto Sans CJK KR, sans-serif"
        anchor = "middle" if center else "start"
        self.svg.append(f'<text x="{x}" y="{y}" fill="{color}" font-family="{family}" font-size="{size}" font-weight="{700 if bold else 400}" text-anchor="{anchor}" dominant-baseline="central">{escape(value)}</text>')

    def arrow(self, points, color=NAVY, dashed=False, width=4):
        for first, second in zip(points, points[1:]):
            if not dashed:
                self.draw.line([first, second], fill=color, width=width)
            else:
                dx, dy = second[0] - first[0], second[1] - first[1]
                length = math.hypot(dx, dy)
                for start in range(0, math.ceil(length), 24):
                    end = min(start + 14, length)
                    self.draw.line([(first[0] + dx * start / length, first[1] + dy * start / length),
                                    (first[0] + dx * end / length, first[1] + dy * end / length)], fill=color, width=width)
        x, y = points[-1]
        previous = points[-2]
        angle = math.atan2(y - previous[1], x - previous[0])
        head = [(x, y), (x - 18 * math.cos(angle) + 9 * math.sin(angle), y - 18 * math.sin(angle) - 9 * math.cos(angle)),
                (x - 18 * math.cos(angle) - 9 * math.sin(angle), y - 18 * math.sin(angle) + 9 * math.cos(angle))]
        self.draw.polygon(head, fill=color)
        vertices = " ".join(f"{px},{py}" for px, py in points)
        dash = ' stroke-dasharray="14 10"' if dashed else ""
        self.svg.append(f'<polyline points="{vertices}" fill="none" stroke="{color}" stroke-width="{width}" stroke-linejoin="round"{dash}/>')
        self.svg.append(f'<polygon points="{" ".join(f"{px},{py}" for px, py in head)}" fill="{color}"/>')

    def label(self, x, y, value, size=25, color=MUTED):
        bbox = self.draw.textbbox((0, 0), value, font=self.font(size))
        w = bbox[2] - bbox[0] + 28
        self.rect((x - w / 2, y - 20, w, 40), fill="white", stroke="white", radius=7, width=1)
        self.text(x, y, value, size, color)

    def node(self, box, title, lines, fill=LIGHT, stroke=BORDER, title_size=35):
        x, y, w, h = box
        self.rect(box, fill, stroke)
        self.text(x + w / 2, y + 36, title, title_size, bold=True)
        for index, line in enumerate(lines):
            self.text(x + w / 2, y + 80 + index * 32, line, 25 if len(line) < 39 else 22, MUTED)

    def save(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        png = directory / "architecture.png"
        svg = directory / "architecture.svg"
        self.bitmap.save(png, format="PNG", dpi=(240, 240), optimize=True)
        svg.write_text("\n".join([*self.svg, "</svg>"]), encoding="utf-8")
        return png, svg


def render(output_dir: Path, font_path: Path, *, require_batch=False):
    source_id, batch_ready = check_source_contract()
    if require_batch and not batch_ready:
        raise RuntimeError("Portfolio/combined batch integration is not present; complete app.py before publishing the diagram")
    d = Drawing(font_path)
    d.text(90, 65, "Physical AI Investment Orchestration", 57, bold=True, center=False)
    d.text(90, 123, "기업별 근거 조사 → 최종 투자 대상 선정 → 하나의 통합 보고서", 32, MUTED, center=False)
    d.rect((60, 180, 2380, 1160), "white")
    d.text(100, 220, "01  SINGLE COMPANY GRAPH", 33, BLUE, bold=True, center=False)
    d.text(100, 267, "agent_pipeline.py · 기업마다 독립 StateGraph / source architecture, not an execution trace", 26, MUTED, center=False)

    # Fixed positions keep the diagram legible; source contracts above detect changed nodes.
    d.node((130, 310, 650, 83), "Company PDFs + Scorecard", [], title_size=30)
    d.arrow([(345, 393), (345, 430)])
    d.node((130, 430, 430, 150), "Orchestrator", ["Structured task plan", "질문·역할·항목·원문 URL"], stroke=BLUE)
    d.node((680, 430, 810, 150), "Selected RAG Workers", ["Bigtech   |   Technology   |   Market", "Send fan-out: 0–3 selected task instances"], stroke=BLUE)
    d.arrow([(560, 505), (680, 505)])
    d.label(620, 475, "tasks", 22)
    d.arrow([(1490, 505), (1630, 505)])
    d.label(1560, 475, "join", 22)
    d.node((1630, 430, 620, 150), "Synthesizer", ["Merge + verify original pages", "근거·항목 누락·재조사 판단"], stroke=BLUE)
    d.arrow([(500, 430), (500, 409), (1940, 409), (1940, 430)], MUTED, True, width=3)
    d.label(1130, 409, "empty plan → Synthesizer", 22, MUTED)
    d.arrow([(1940, 580), (1940, 770)])
    d.label(2100, 700, "ready / U retained", 24)
    d.node((1630, 770, 620, 150), "Investment Judge", ["21 grades → deterministic scores", "기업별 투자 근거·제약·필요 조건"])
    d.arrow([(1630, 845), (1460, 845)])
    d.node((1070, 770, 390, 150), "Score Review", ["등급·U 사유 확인", "근거 보완은 S로 반환"], title_size=33)
    d.arrow([(1070, 845), (960, 845)])
    d.node((570, 770, 390, 150), "Report Writer", ["Evidence-linked analysis", "점수·근거를 유지해 작성"], title_size=33)
    d.arrow([(690, 920), (690, 1050)])
    d.node((570, 1050, 650, 150), "Report Evaluator", ["Groundedness · Neutrality · Bias control", "관점 커버리지는 통과 조건에서 제외"], fill=GREEN, stroke=TEAL)
    d.arrow([(1220, 1095), (1630, 1095)], TEAL)
    d.label(1385, 1065, "pass", 23, TEAL)
    d.node((1630, 1050, 620, 150), "Company Results", ["Company report + verified State", "원보고서 전체·점수·품질 / 한쪽 요약 보조"], fill=GREEN, stroke=TEAL)

    # Dashed arrows are controlled feedback, not new independent agents.
    d.arrow([(1720, 580), (1720, 665), (345, 665), (345, 580)], ORANGE, True)
    d.label(940, 665, "replan: missing criteria only / 부족 항목만", 25, ORANGE)
    d.arrow([(1265, 770), (1265, 715), (1530, 715), (1530, 560), (1630, 560)], ORANGE, True)
    d.arrow([(1220, 1145), (1530, 1145), (1530, 560), (1630, 560)], ORANGE, True)
    d.label(1455, 992, "research / 근거·채점 보완", 25, ORANGE)
    d.arrow([(840, 1050), (840, 972), (840, 920)], ORANGE, True)
    d.label(875, 973, "rewrite ≤2 / 근거·점수 유지", 25, ORANGE)
    d.rect((105, 1265, 2240, 44), fill=LIGHT, stroke=LIGHT, radius=10, width=1)
    d.text(1225, 1287, "Research: 3–5 rechecks  |  Stop: 2 no-progress rounds*  |  Rewrite: ≤2  |  Unknown stays U", 26, MUTED)
    d.text(110, 1320, "* 초기 미선택 역할의 조사 기회 보존 · 품질 실패는 초안으로 종료", 22, MUTED, center=False)

    d.rect((60, 1410, 2380, 870), "white")
    d.text(100, 1450, "02  REPORT SYNTHESIS / FINAL SELECTION", 32, BLUE, bold=True, center=False)
    badge = "app.py batch code · not extra LangGraph nodes" if batch_ready else "batch output contract · integration pending"
    d.text(1430, 1450, badge, 25, MUTED, center=False)
    d.arrow([(1940, 1200), (1940, 1235), (2415, 1235), (2415, 1490), (410, 1490), (410, 1530)], TEAL)
    d.label(2300, 1375, "all: 3 states", 24, TEAL)
    d.node((130, 1530, 560, 150), "3 Reports + State", ["Full text + SHA256 + paragraph IDs", "문단·원문 E-ID·21항목 점수·U"], fill=GREEN, stroke=TEAL, title_size=32)
    d.arrow([(690, 1605), (840, 1605)])
    d.label(765, 1575, "+1 LLM", 22)
    d.node((840, 1530, 620, 150), "ReportSynthesis Agent", ["5 fields/company + 2 comparison paragraphs", "문단↔분석↔원문 맵 · 원문 우선"], stroke=BLUE, title_size=33)
    d.arrow([(1460, 1605), (1630, 1605)])
    d.node((1630, 1530, 620, 150), "PortfolioDecision Agent", ["Original reports + shared synthesis", "선정기업·비교 이유·투자조건 / 코드 검사"], stroke=BLUE, title_size=33)
    d.arrow([(1940, 1680), (1940, 1810)])
    d.label(2110, 1745, "same analysis", 23)
    d.node((1630, 1810, 620, 150), "Combined Report", ["Fixed scores / U / references", "합성 본문·선정 결론 / revise callback"], stroke=BLUE)
    d.arrow([(1630, 1885), (1460, 1885)])
    d.node((840, 1810, 620, 150), "Combined Quality", ["Core 3 + company-report reflection", "같은 Judge: 분석·한계·현재 의견 반영"], fill=GREEN, stroke=TEAL)
    d.arrow([(840, 1885), (690, 1885)], TEAL)
    d.label(765, 1855, "pass", 23, TEAL)
    d.rect((130, 1810, 560, 150), NAVY, NAVY)
    d.text(410, 1846, "One Final PDF", 36, "white", bold=True)
    d.text(410, 1890, "A4 · 20 mm · ≤10 pages TOTAL", 25, "white")
    d.text(410, 1922, "final_investment_report.pdf", 23, "#C8D8ED")
    d.arrow([(1320, 1960), (1320, 2020), (2350, 2020), (2350, 1885), (2250, 1885)], ORANGE, True)
    d.label(1860, 2020, "rewrite ≤2 / same inputs", 25, ORANGE)
    d.text(1850, 2058, "quality_reviser → Combined Report", 23, ORANGE)
    d.arrow([(1150, 1960), (1150, 2080)], ORANGE, True)
    d.label(980, 2020, "research / exhausted", 23, ORANGE)
    d.node((840, 2080, 620, 130), "Quality Failed Draft", ["새 근거 필요·한도 소진 시 미달 내용 보존", "최종 제출본으로 발행하지 않음"], fill="#FFF7ED", stroke=ORANGE, title_size=32)
    d.text(110, 2245, "Normal path: +1 synthesis LLM  |  No new RAG / regrading  |  View coverage excluded", 26, MUTED, center=False)
    d.text(95, 2330, f"Source code: {source_id}  ·  scripts/render_architecture.py  ·  implementation architecture, not a LangSmith trace", 24, MUTED, center=False)
    return d.save(output_dir)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "docs")
    parser.add_argument("--font", help="Korean-capable TTF/TTC font for another operating system")
    parser.add_argument("--require-integrated-batch", action="store_true")
    args = parser.parse_args()
    for path in render(args.output_dir, find_font(args.font), require_batch=args.require_integrated_batch):
        print(path)


if __name__ == "__main__":
    main()
