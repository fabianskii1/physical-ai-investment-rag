"""설계 PDF의 평가표와 고정 코드 배점의 일치를 실행 전에 확인한다.

PDF는 평가 규칙의 출처이며 기업 사실을 검색하는 RAG 인덱스에는 넣지 않는다.
모델이 배점표나 기존 기업별 점수를 즉석에서 가져와 계산하지 않도록 한다.
"""
from hashlib import sha256
from pathlib import Path
import re
import unicodedata

from pypdf import PdfReader

from rag.scorecard import SCORECARD

ROOT = Path(__file__).resolve().parent
DESIGN_PDF = ROOT / "evaluation" / "physical_ai_investment_design.pdf"
CRITERIA_PAGES = (2, 3)


def _normalized(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def verify_scorecard_reference(path: str | Path = DESIGN_PDF) -> dict:
    """2~3쪽의 6영역·21항목·배점·환산식·U 규칙을 고정 Scorecard와 대조한다.

PDF 내용을 실행 지시나 모델 프롬프트로 사용하지 않는다. 알려진 평가표와의
불일치는 명시적으로 실패시키며, LLM으로 새 평가표를 추측해서 만들지 않는다.
"""
    source = Path(path).expanduser().resolve()
    reader = PdfReader(source)
    if len(reader.pages) < max(CRITERIA_PAGES):
        raise ValueError("평가 기준 PDF에 2~3쪽이 없습니다")
    text = _normalized("\n".join(reader.pages[number - 1].extract_text() or ""
                                 for number in CRITERIA_PAGES))
    missing = []
    for category, items in SCORECARD.items():
        if _normalized(f"{category}{sum(items.values())}") not in text:
            missing.append(f"{category} 영역 배점")
        for criterion, weight in items.items():
            if _normalized(f"{criterion}({weight})") not in text:
                missing.append(f"{criterion}({weight})")
    for rule in ("6개 영역·21개 세부 항목·총 100점", "항목 배점 × 등급 ÷ 4",
                 "U는 0등급·0점이 아니며"):
        if _normalized(rule) not in text:
            missing.append(rule)
    if missing:
        raise ValueError("평가 기준 PDF와 코드가 일치하지 않습니다: " + ", ".join(missing))
    return {
        "name": "Physical AI·Robotics 투자 검토 평가표",
        "source_pdf": str(source), "source_pages": list(CRITERIA_PAGES),
        "source_sha256": sha256(source.read_bytes()).hexdigest(),
        "categories": SCORECARD, "category_count": len(SCORECARD),
        "criterion_count": sum(len(items) for items in SCORECARD.values()),
        "total_weight": sum(sum(items.values()) for items in SCORECARD.values()),
        "grade_scale": [0, 1, 2, 3, 4], "unknown_grade": "U",
        "formula": "항목 배점 × 등급 ÷ 4",
        "unknown_policy": "U는 0점으로 채우거나 제외 후 100점으로 재환산하지 않음",
        "verified_against_pdf": True,
    }
