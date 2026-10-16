#!/usr/bin/env python3
"""Draw the implementation architecture as SVG and PNG without model/API calls.

The upper panel describes agent_pipeline.py's company StateGraph. The lower panel
shows final_report_pipeline.py's final-report StateGraph, including quality feedback.
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
WIDTH, HEIGHT = 2500, 2850
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
    final_source = (ROOT / 'final_report_pipeline.py').read_text(encoding='utf-8')
    final_tree = ast.parse(final_source)
    final_nodes = {node.elts[0].value for node in ast.walk(final_tree)
                   if isinstance(node, ast.Tuple) and len(node.elts) == 2
                   and isinstance(node.elts[0], ast.Constant) and isinstance(node.elts[0].value, str)
                   and isinstance(node.elts[1], ast.Name)}
    expected_final = {'prepare_final_inputs', 'report_synthesis', 'portfolio_decision', 'reuse_final_analysis',
                      'compose_final_report', 'final_quality', 'final_orchestrator', 'final_rewrite', 'publish_final_report'}
    if final_nodes != expected_final:
        raise RuntimeError('Final StateGraph nodes changed; update the architecture layout')
    batch_ready = all(name in app_source for name in ('build_final_report_graph', 'graph.stream',
                      'synthesize=synthesize_company_reports', 'select=select_portfolio_investment',
                      'evaluate=evaluate_combined_quality', 'revise=revise_combined_quality'))
    sources = [source, app_source, final_source]
    for name in ('company_report_inputs.py', 'report_synthesis.py', 'combined_quality.py'):
        sources.append((ROOT / name).read_text(encoding='utf-8'))
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
    d.node((680, 430, 810, 150), "Selected Workers", ["Bigtech   |   Technology   |   Market", "Send fan-out: 0–3 selected task instances"], stroke=BLUE)
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
    d.arrow([(1220,1175),(1570,1175),(1570,895),(1630,895)],ORANGE,True)
    d.label(1410,1225,'regrade ≤1 / 동일 근거',23,ORANGE)
    d.rect((105, 1265, 2240, 44), fill=LIGHT, stroke=LIGHT, radius=10, width=1)
    d.text(1225, 1287, "Research: 3–5 rechecks  |  Stop: 2 no-progress rounds*  |  Rewrite: ≤2  |  Unknown stays U", 26, MUTED)
    d.text(110, 1320, "* 초기 미선택 역할의 조사 기회 보존 · 품질 실패는 초안으로 종료", 22, MUTED, center=False)

    d.rect((60, 1410, 2380, 1320), 'white')
    d.text(100, 1450, '02  FINAL REPORT STATEGRAPH', 32, BLUE, bold=True, center=False)
    badge = 'final_report_pipeline.py · actual LangGraph nodes' if batch_ready else 'graph integration pending'
    d.text(1430, 1450, badge, 25, MUTED, center=False)
    d.arrow([(1940, 1200), (1940, 1235), (2415, 1235), (2415, 1490), (410, 1490), (410, 1530)], TEAL)
    d.label(2300, 1375, 'all: 3 states', 24, TEAL)
    d.node((130, 1530, 560, 150), 'Prepare Final Inputs', ['Full reports + SHA256 + paragraph IDs', '기업 보고서·검증 원문·21항목 점수'], fill=GREEN, stroke=TEAL, title_size=32)
    d.arrow([(690, 1605), (840, 1605)])
    d.label(765, 1575, 'fresh', 22)
    d.node((840, 1530, 620, 150), 'Report Synthesis', ['5 fields/company + comparison paragraphs', '보고서 전문·원문 검증 근거로 합성'], stroke=BLUE, title_size=33)
    d.arrow([(1460, 1605), (1630, 1605)])
    d.node((1630, 1530, 620, 150), 'Portfolio Decision', ['Original reports + shared synthesis', '대상·비교 근거·조건 / 코드 검사'], stroke=BLUE, title_size=33)
    d.arrow([(1940, 1680), (1940, 1810)])
    d.node((1630, 1810, 620, 150), 'Compose Final Report', ['Fixed scores / U / references', '합성·선정 결과 조립 / 분석 checkpoint'], stroke=BLUE, title_size=32)
    d.arrow([(1630, 1885), (1460, 1885)])
    d.node((840, 1810, 620, 150), 'Final Quality', ['Groundedness / neutrality / bias control', '세 기업 분석·한계·개별 의견 반영'], fill=GREEN, stroke=TEAL)
    d.arrow([(840, 1885), (690, 1885)], TEAL)
    d.node((130, 1810, 560, 150), 'Final Orchestrator', ['Code routing / zero extra LLM calls', '통과·수정·근거 필요·예산 종료'], stroke=BLUE, title_size=32)
    d.arrow([(250, 1960), (250, 2160)], TEAL)
    d.label(310, 2050, 'pass', 24, TEAL)
    d.node((130, 2160, 560, 150), 'Publish Final Report', ['A4 / equal 20 mm / ≤10 pages', 'PDF 검사 후 최종본 또는 초안 저장'], fill=GREEN, stroke=TEAL, title_size=32)
    d.arrow([(570, 1960), (570, 2070), (800, 2070), (800, 2235), (840, 2235)], ORANGE, True)
    d.label(710, 2040, 'rewrite ≤2', 24, ORANGE)
    d.node((840, 2160, 620, 150), 'Final Rewrite', ['Same reports / evidence / scores / decision', '피드백만 수정 / 점수·출처·조건 보호'], stroke=ORANGE, title_size=33)
    d.arrow([(1300, 2160), (1300, 1960)], ORANGE, True)
    d.label(1430, 2050, 'accepted → rejudge', 23, ORANGE)
    d.arrow([(1000, 2160), (1000, 2115), (410, 2115), (410, 1960)], MUTED, True, width=3)
    d.label(1090, 2100, 'invalid → route only', 22, MUTED)
    d.node((1630, 2160, 620, 150), 'Reuse Final Analysis', ['Optional: same report / score / source hashes', '검증된 합성·선정만 재사용 / 품질 재평가'], stroke=BORDER, title_size=32)
    d.arrow([(410, 1680), (410, 1730), (2330, 1730), (2330, 2235), (2250, 2235)], MUTED, True, width=3)
    d.label(1200, 1730, '--reuse-analysis only', 22, MUTED)
    d.arrow([(1940, 2160), (1940, 1960)], MUTED, True, width=3)
    d.text(130, 2390, 'Research / review / exhausted → last safe draft; never promote failed quality.', 28, ORANGE, center=False)
    d.text(130, 2440, 'New evidence required → research_request.json; no blind repeated research.', 26, MUTED, center=False)
    d.text(130, 2530, 'Normal path: 7 nodes  |  Rewrite ≤2  |  Step cap 32  |  No new RAG / regrading', 28, MUTED, center=False)
    d.text(130, 2580, 'InMemorySaver + graph run ID + node trace + persisted combined_analysis.json', 27, MUTED, center=False)
    d.text(130, 2660, 'Source architecture; actual runtime evidence is saved separately in LangSmith / trace JSON.', 25, MUTED, center=False)
    d.text(95, 2785, f'Source code: {source_id}  ·  scripts/render_architecture.py  ·  implementation architecture, not a LangSmith trace', 24, MUTED, center=False)
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
