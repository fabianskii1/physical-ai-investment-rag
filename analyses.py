import os
from typing import Callable, TypedDict, Literal

from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_openai import ChatOpenAI


load_dotenv()

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

# 개발 A가 나중에 제공할 검색 함수 형태
SearchFn = Callable[[str, str, int], list[Document]]
LLMFn = Callable[[str], str]

# 근거 상태
AnalysisStatus = Literal["confirmed", "unverified"]


def openai_llm(prompt: str) -> str:
    llm = ChatOpenAI(
        model=OPENAI_MODEL,
        temperature=0,
    )

    response = llm.invoke(prompt)

    return response.content


class SourceRef(TypedDict):
    doc_id: str
    page: int


class AnalysisClaim(TypedDict):
    claim: str
    criterion_id: str
    source_refs: list[SourceRef]
    status: AnalysisStatus


def analyze_bigtech(
    company: str,
    search_fn: SearchFn,
    llm_fn: LLMFn,
) -> list[AnalysisClaim]:
    """
    대기업 / 빅테크 관계 분석

    - 실제 지분·CVC 투자
    - MOU / 공동개발
    - 현장 PoC / 실증
    - 유상 계약 / 납품
    - 재구매 / 계약 확대

    점수 계산은 하지 않는다.
    """

    query = (
        f"{company}의 대기업 또는 빅테크 관련 "
        "지분 투자, CVC 투자, MOU, 공동개발, "
        "PoC, 현장 실증, 유상 계약, 납품, "
        "재구매 또는 계약 확대 근거"
    )

    docs = search_fn(company, query, 4)

    # 검색 결과 자체가 없는 경우
    if not docs:
        return [
            {
                "claim": "대기업·빅테크 관련 근거를 확인하지 못했다.",
                "criterion_id": "bigtech_relation",
                "source_refs": [],
                "status": "unverified",
            }
        ]

    evidence_parts = []
    source_refs: list[SourceRef] = []

    for doc in docs:
        doc_id = doc.metadata.get("doc_id")
        page = doc.metadata.get("page")

        # 출처 위치가 없는 문서는 근거로 사용하지 않음
        if not doc_id or page is None:
            continue

        evidence_parts.append(
            f"[DOC doc_id={doc_id} page={page}]\n"
            f"{doc.page_content}"
        )

        source_refs.append(
            {
                "doc_id": doc_id,
                "page": page,
            }
        )

    # 검색 결과는 있었지만 사용할 수 있는 출처가 없는 경우
    if not evidence_parts:
        return [
            {
                "claim": "대기업·빅테크 관련 근거의 출처 위치를 확인하지 못했다.",
                "criterion_id": "bigtech_relation",
                "source_refs": [],
                "status": "unverified",
            }
        ]

    evidence_text = "\n\n".join(evidence_parts)

    prompt = f"""
기업: {company}

아래 근거만 사용해서 대기업·빅테크 관계를 분석하라.

반드시 다음 관계를 구분한다.
- 지분/CVC 투자
- MOU 또는 공동개발
- 현장 PoC 또는 실증
- 유상 계약 또는 납품
- 재구매 또는 계약 확대
- 확인 불가

근거에 없는 사실은 추정하지 않는다.

근거:
{evidence_text}
"""

    llm_result = llm_fn(prompt)

    return [
        {
            "claim": llm_result,
            "criterion_id": "bigtech_relation",
            "source_refs": source_refs,
            "status": "confirmed",
        }
    ]


def analyze_technology(
    company: str,
    search_fn: SearchFn,
    llm_fn: LLMFn,
) -> list[AnalysisClaim]:
    """
    기술·팀·현장 적용 분석

    - 창업자·핵심팀 전문성
    - 제품화·사업 실행력
    - 팀 구성·운영 역량
    - 핵심 작업 성능
    - 현장 안정성·안전성
    - 양산·유지보수 가능성
    - 기술적 강점과 한계

    점수 계산은 하지 않는다.
    """
    pass


def analyze_market(
    company: str,
    search_fn: SearchFn,
    llm_fn: LLMFn,
) -> list[AnalysisClaim]:
    """
    시장성·경쟁 분석

    - 실제 접근 가능한 시장
    - 고객 문제와 구매 필요성
    - 시장 성장 동력
    - 시장 진입·확장 가능성
    - 경쟁 제품 대비 고객 가치
    - 모방하기 어려운 자산
    - 고객 유지·전환 장벽

    비교기업의 투자 순위는 만들지 않는다.
    점수 계산은 하지 않는다.
    """
    pass