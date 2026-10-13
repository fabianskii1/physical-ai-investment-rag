"""기업 자료집을 기획용 입력으로 읽고 기존 투자 결론은 제외한다.

텍스트는 검색 질문을 만드는 단서다. 검증된 원문 근거나 점수의 정답으로
사용하지 않으며 PDF 문장은 실행할 지시가 아닌 조사 자료로 취급한다.
"""
from pathlib import Path
import re
from pypdf import PdfReader

COMPANY_FILES = {"MOTOMIND": "MOTOMIND.pdf", "ROBROS": "ROBROS.pdf", "Figure AI": "Figure_AI.pdf"}
MAX_CONTEXT_CHARS = 15_000


def _without_prior_conclusions(text: str) -> str:
    """기존 잠정 평가·결론을 빼되 추가 조사처와 미확인 정보는 유지한다."""
    text = re.sub(r"(?m)^\s*8\.\s*Figure\s+AI\s+투자평가[^\n]*[\s\S]*?(?=^\s*9\.|\Z)", "", text)
    text = re.sub(r"(?m)^\s*잠정 투자 판단[^\n]*[\s\S]*?(?=^\s*\d+\.\s|^\s*부록\s|\Z)", "", text)
    text = re.sub(r"(?m)^\s*최종 정리\s*$[\s\S]*?(?=^\s*부록\s|^\s*\d+\.\s[^\n]|\Z)", "", text)
    return text.strip()


def load_company_research_input(folder: str | Path, company: str) -> dict:
    """경로·페이지 수·기획용 본문·잘림 여부를 반환한다. 입력 오류는 숨기지 않는다."""
    if company not in COMPANY_FILES:
        raise ValueError(f"지원하지 않는 기업: {company}")
    path = Path(folder).expanduser().resolve() / COMPANY_FILES[company]
    if not path.is_file():
        raise FileNotFoundError(f"기업 조사 입력 PDF를 찾을 수 없습니다: {path}")
    reader = PdfReader(path)
    pages = []
    for number, page in enumerate(reader.pages, 1):
        text = _without_prior_conclusions(page.extract_text() or "")
        if text:
            pages.append(f"[자료집 p.{number} — 검증 전 조사 단서]\n{text}")
    context = "\n\n".join(pages)
    return {"source_pdf": str(path), "total_pages": len(reader.pages),
            "context": context[:MAX_CONTEXT_CHARS], "context_truncated": len(context) > MAX_CONTEXT_CHARS,
            "usage": "기획·검색 단서; 원문 재검증 없이 점수 근거로 채택하지 않음"}
