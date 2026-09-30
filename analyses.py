import json
import os
from typing import Callable, Literal, NotRequired, TypedDict

from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_openai import ChatOpenAI


load_dotenv()

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
DEFAULT_SEARCH_K = 4
COMMON_COMPANY = "COMMON"


def openai_llm(prompt: str) -> str:
    """기본 OpenAI LLM 호출 함수."""
    llm = ChatOpenAI(model=OPENAI_MODEL, temperature=0)
    response = llm.invoke(prompt)
    return response.content


# 개발 A가 제공할 검색 함수와 LLM 함수 인터페이스
SearchFn = Callable[[str, str, int], list[Document]]
LLMFn = Callable[[str], str]

AnalysisStatus = Literal["confirmed", "unverified"]
BigtechRelationType = Literal[
    "equity_investment",
    "mou_joint_development",
    "field_demo",
    "paid_contract_delivery",
    "repurchase_expansion",
    "unverified",
]


class SourceRef(TypedDict):
    doc_id: str
    page: int


class AnalysisClaim(TypedDict):
    claim: str
    criterion_id: str
    source_refs: list[SourceRef]
    status: AnalysisStatus
    relation_type: NotRequired[BigtechRelationType]


# 개발 B 내부 임시 criterion_id.
# 개발 C의 공식 21개 criterion_id가 확정되면 이 상수만 교체한다.
BIGTECH_CRITERION_MAP: dict[str, tuple[str, ...]] = {
    "equity_investment": ("bigtech_equity_investment",),
    "mou_joint_development": ("bigtech_contract_delivery_stage",),
    "field_demo": ("bigtech_contract_delivery_stage",),
    # 유상 계약·납품은 '유상 매출·납품'과 '대기업 계약·납품 단계'에 모두 근거가 될 수 있다.
    "paid_contract_delivery": (
        "paid_sales_delivery",
        "bigtech_contract_delivery_stage",
    ),
    "repurchase_expansion": ("customer_repurchase_expansion",),
    "unverified": ("bigtech_relation",),
}

TECHNOLOGY_CRITERION_MAP: dict[str, str] = {
    "founder_expertise": "founder_expertise",
    "productization_execution": "productization_execution",
    "team_operations": "team_operations",
    "communication_reliability": "communication_reliability",
    "core_task_performance": "core_task_performance",
    "field_reliability_safety": "field_reliability_safety",
    "mass_production_maintenance": "mass_production_maintenance",
    "hard_to_copy_assets": "hard_to_copy_assets",
}

MARKET_CRITERION_MAP: dict[str, str] = {
    "accessible_market": "accessible_market",
    "customer_pain_purchase_need": "customer_pain_purchase_need",
    "market_growth_drivers": "market_growth_drivers",
    "market_entry_expansion": "market_entry_expansion",
    "customer_value_vs_competitors": "customer_value_vs_competitors",
    "hard_to_copy_assets": "hard_to_copy_assets",
    "customer_retention_switching_barrier": "customer_retention_switching_barrier",
}


def _unverified_claim(message: str, criterion_id: str) -> AnalysisClaim:
    return {
        "claim": message,
        "criterion_id": criterion_id,
        "source_refs": [],
        "status": "unverified",
    }


def _unverified_bigtech(message: str) -> AnalysisClaim:
    return {
        "claim": message,
        "criterion_id": "bigtech_relation",
        "source_refs": [],
        "status": "unverified",
        "relation_type": "unverified",
    }


def _build_evidence(docs: list[Document]) -> tuple[str, set[tuple[str, int]]]:
    """
    검색 결과에서 LLM에 전달할 근거 텍스트와 허용된 (doc_id, page) 집합을 만든다.
    doc_id/page가 없는 문서는 인용 가능한 근거에서 제외한다.
    """
    evidence_parts: list[str] = []
    allowed_refs: set[tuple[str, int]] = set()

    for doc in docs:
        raw_doc_id = doc.metadata.get("doc_id")
        raw_page = doc.metadata.get("page")

        if raw_doc_id is None or raw_page is None:
            continue

        doc_id = str(raw_doc_id).strip()
        if not doc_id:
            continue

        try:
            page = int(raw_page)
        except (TypeError, ValueError):
            continue

        if page < 1:
            continue

        source_company = str(doc.metadata.get("company", "")).strip()
        company_label = f" company={source_company}" if source_company else ""
        evidence_parts.append(
            f"[DOC doc_id={doc_id} page={page}{company_label}]\n{doc.page_content}"
        )
        allowed_refs.add((doc_id, page))

    return "\n\n".join(evidence_parts), allowed_refs


def _merge_unique_documents(*groups: list[Document]) -> list[Document]:
    """여러 검색 범위의 결과를 같은 청크의 중복 없이 합친다."""

    merged: list[Document] = []
    seen_chunks: set[tuple[str, str, str]] = set()
    for group in groups:
        for doc in group:
            key = (
                str(doc.metadata.get("doc_id", "")).strip(),
                str(doc.metadata.get("page", "")).strip(),
                str(doc.metadata.get("chunk_index", doc.page_content)),
            )
            if key in seen_chunks:
                continue
            seen_chunks.add(key)
            merged.append(doc)
    return merged


def _parse_json_array(raw_result: str) -> list[dict] | None:
    """LLM 응답에서 JSON 배열만 파싱한다."""
    cleaned = raw_result.strip()

    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json")
        cleaned = cleaned.removeprefix("```")
        cleaned = cleaned.removesuffix("```")
        cleaned = cleaned.strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return None

    if not isinstance(parsed, list):
        return None

    return [item for item in parsed if isinstance(item, dict)]


def _validate_source_refs(
    llm_refs: object,
    allowed_refs: set[tuple[str, int]],
) -> tuple[list[SourceRef], bool]:
    """
    LLM이 제시한 출처가 실제 검색 결과에 존재하는지 검증한다.
    반환값: (유효한 출처 목록, 잘못된 출처가 하나라도 있었는지 여부)
    """
    if not isinstance(llm_refs, list):
        return [], True

    valid_refs: list[SourceRef] = []
    seen: set[tuple[str, int]] = set()
    invalid_ref_found = False

    for ref in llm_refs:
        if not isinstance(ref, dict):
            invalid_ref_found = True
            continue

        raw_doc_id = ref.get("doc_id")
        raw_page = ref.get("page")

        if raw_doc_id is None or raw_page is None:
            invalid_ref_found = True
            continue

        doc_id = str(raw_doc_id).strip()
        try:
            page = int(raw_page)
        except (TypeError, ValueError):
            invalid_ref_found = True
            continue

        key = (doc_id, page)
        if key not in allowed_refs:
            invalid_ref_found = True
            continue

        if key in seen:
            continue

        seen.add(key)
        valid_refs.append({"doc_id": doc_id, "page": page})

    return valid_refs, invalid_ref_found


def _process_standard_analysis(
    parsed_result: list[dict],
    allowed_refs: set[tuple[str, int]],
    criterion_map: dict[str, str],
    fallback_message: str,
    fallback_criterion_id: str,
) -> list[AnalysisClaim]:
    """technology/market 공통 후처리."""
    results: list[AnalysisClaim] = []

    for item in parsed_result:
        analysis_type = item.get("analysis_type")
        raw_claim = item.get("claim", "")
        claim = raw_claim.strip() if isinstance(raw_claim, str) else ""
        status = item.get("status", "unverified")

        if analysis_type not in criterion_map or not claim:
            continue

        valid_refs, invalid_ref_found = _validate_source_refs(
            item.get("source_refs", []),
            allowed_refs,
        )

        if status != "confirmed" or not valid_refs or invalid_ref_found:
            status = "unverified"
            valid_refs = []

        results.append(
            {
                "claim": claim,
                "criterion_id": criterion_map[analysis_type],
                "source_refs": valid_refs,
                "status": status,
            }
        )

    if results:
        return results

    return [_unverified_claim(fallback_message, fallback_criterion_id)]


def analyze_bigtech(
    company: str,
    search_fn: SearchFn,
    llm_fn: LLMFn,
) -> list[AnalysisClaim]:
    """
    대기업·빅테크 관계 분석.

    실제 지분·CVC 투자, MOU/공동개발, 현장 PoC/실증,
    유상 계약·납품, 재구매·계약 확대를 구분한다.
    점수 계산은 하지 않는다.
    """
    query = (
        f"{company}의 대기업 또는 빅테크 관련 "
        "지분 투자, CVC 투자, MOU, 공동개발, "
        "PoC, 현장 실증, 유상 계약, 납품, "
        "재구매 또는 계약 확대 근거"
    )

    docs = search_fn(company, query, DEFAULT_SEARCH_K)
    if not docs:
        return [_unverified_bigtech("대기업·빅테크 관련 근거를 확인하지 못했다.")]

    evidence_text, allowed_refs = _build_evidence(docs)
    if not evidence_text:
        return [
            _unverified_bigtech(
                "대기업·빅테크 관련 근거의 출처 위치를 확인하지 못했다."
            )
        ]

    prompt = f"""
기업: {company}

아래 근거만 사용해서 대기업·빅테크 관계를 분석하라.

관계 유형은 반드시 다음 중 하나로 구분한다.

- equity_investment
  실제 지분 투자 또는 CVC 투자

- mou_joint_development
  MOU 또는 공동개발

- field_demo
  현장 PoC 또는 실증

- paid_contract_delivery
  유상 계약, 납품 또는 상용 사용

- repurchase_expansion
  재구매, 계약 갱신 또는 확대

근거에 없는 사실은 절대 추정하지 않는다.
각 주장에는 그 주장을 직접 뒷받침하는 doc_id와 page만 연결한다.

확인되는 관계가 없으면
relation_type을 "unverified",
status를 "unverified",
source_refs를 빈 배열로 반환한다.

반드시 아래 형식의 JSON 배열만 반환한다.
설명문, Markdown, 코드블록은 출력하지 않는다.

[
  {{
    "relation_type": "field_demo",
    "claim": "확인된 사실을 한 문장으로 작성",
    "source_refs": [
      {{
        "doc_id": "문서 ID",
        "page": 1
      }}
    ],
    "status": "confirmed"
  }}
]

근거:
{evidence_text}
"""

    parsed_result = _parse_json_array(llm_fn(prompt))
    if parsed_result is None:
        return [_unverified_bigtech("LLM 분석 결과를 구조화하지 못했다.")]

    results: list[AnalysisClaim] = []

    for item in parsed_result:
        relation_type = item.get("relation_type")
        raw_claim = item.get("claim", "")
        claim = raw_claim.strip() if isinstance(raw_claim, str) else ""
        status = item.get("status", "unverified")

        if relation_type not in BIGTECH_CRITERION_MAP or not claim:
            continue

        valid_refs, invalid_ref_found = _validate_source_refs(
            item.get("source_refs", []),
            allowed_refs,
        )

        # relation_type 자체가 unverified면 LLM이 status를 잘못 confirmed로 줘도
        # 반드시 unverified로 강제한다.
        if (
            relation_type == "unverified"
            or status != "confirmed"
            or not valid_refs
            or invalid_ref_found
        ):
            status = "unverified"
            valid_refs = []

        for criterion_id in BIGTECH_CRITERION_MAP[relation_type]:
            results.append(
                {
                    "claim": claim,
                    "criterion_id": criterion_id,
                    "source_refs": valid_refs,
                    "status": status,
                    "relation_type": relation_type,
                }
            )

    if results:
        return results

    return [_unverified_bigtech("대기업·빅테크 관련 관계를 확인하지 못했다.")]


def analyze_technology(
    company: str,
    search_fn: SearchFn,
    llm_fn: LLMFn,
) -> list[AnalysisClaim]:
    """
    기술·팀·현장 적용 분석.

    창업자/핵심팀, 제품화 실행력, 핵심 작업 성능, 현장 안정성·안전성,
    양산·유지보수 가능성, 모방하기 어려운 기술·자산을 다룬다.
    점수 계산은 하지 않는다.
    """
    query = (
        f"{company}의 창업자와 핵심팀 전문성, 제품화 실행력, "
        "핵심 로봇 기술, 실제 작업 성능, 현장 안정성 및 안전성, "
        "양산 가능성, 유지보수, 특허·데이터·노하우 등 "
        "모방하기 어려운 기술 자산 관련 근거"
    )

    docs = search_fn(company, query, DEFAULT_SEARCH_K)
    if not docs:
        return [
            _unverified_claim(
                "기술·팀·현장 적용 관련 근거를 확인하지 못했다.",
                "technology_relation",
            )
        ]

    evidence_text, allowed_refs = _build_evidence(docs)
    if not evidence_text:
        return [
            _unverified_claim(
                "기술·팀·현장 적용 근거의 출처 위치를 확인하지 못했다.",
                "technology_relation",
            )
        ]

    prompt = f"""
기업: {company}

아래 근거만 사용해서 기술·팀·현장 적용을 분석하라.

분석 유형은 반드시 다음 중 하나로 구분한다.

- founder_expertise
  창업자·핵심팀의 관련 산업·기술 전문성

- productization_execution
  연구·개발 결과를 실제 제품이나 사업으로 구현한 실행력

- team_operations
  핵심 인력 구성과 팀 운영 역량

- communication_reliability
  공식 자료에서 확인되는 설명의 명확성·일관성·신뢰성

- core_task_performance
  로봇의 핵심 작업 수행 성능

- field_reliability_safety
  실제 현장 안정성, 반복 작업 신뢰성, 안전성

- mass_production_maintenance
  양산 가능성, 제조 확장성, 유지보수 가능성

- hard_to_copy_assets
  특허, 독자 기술, 데이터, 노하우 등 모방하기 어려운 자산

근거에 없는 사실은 절대 추정하지 않는다.
회사가 발표한 목표 수치를 실제 검증된 성능처럼 표현하지 않는다.
실험실 결과를 실제 현장 성능으로 바꾸어 쓰지 않는다.
생산 계획을 실제 양산 실적으로 표현하지 않는다.

각 주장에는 그 주장을 직접 뒷받침하는 doc_id와 page만 연결한다.
확인할 수 없는 항목은 억지로 주장하지 않는다.

반드시 아래 형식의 JSON 배열만 반환한다.
설명문, Markdown, 코드블록은 출력하지 않는다.

[
  {{
    "analysis_type": "core_task_performance",
    "claim": "확인된 사실을 한 문장으로 작성",
    "source_refs": [
      {{
        "doc_id": "문서 ID",
        "page": 1
      }}
    ],
    "status": "confirmed"
  }}
]

근거:
{evidence_text}
"""

    parsed_result = _parse_json_array(llm_fn(prompt))
    if parsed_result is None:
        return [
            _unverified_claim(
                "LLM 기술 분석 결과를 구조화하지 못했다.",
                "technology_relation",
            )
        ]

    return _process_standard_analysis(
        parsed_result=parsed_result,
        allowed_refs=allowed_refs,
        criterion_map=TECHNOLOGY_CRITERION_MAP,
        fallback_message="기술·팀·현장 적용 관련 항목을 확인하지 못했다.",
        fallback_criterion_id="technology_relation",
    )


def analyze_market(
    company: str,
    search_fn: SearchFn,
    llm_fn: LLMFn,
) -> list[AnalysisClaim]:
    """
    시장성·경쟁 분석.

    접근 가능한 시장, 고객 문제/구매 필요성, 성장 동력, 시장 확장성,
    경쟁 대비 고객 가치, 모방하기 어려운 자산, 전환 장벽을 다룬다.
    기업 자료와 COMMON 공통 시장 자료를 함께 사용한다.
    기업 간 투자 순위나 점수는 만들지 않는다.
    """
    query = (
        f"{company}의 실제 고객과 접근 가능한 시장, 고객의 문제와 구매 필요성, "
        "시장 성장 동력, 시장 진입 및 확장 가능성, 기존 방식이나 경쟁 제품 대비 "
        "고객 가치, 차별화 자산, 고객 유지 및 전환 장벽 관련 근거"
    )

    company_docs = search_fn(company, query, DEFAULT_SEARCH_K)
    if not company_docs:
        return [
            _unverified_claim(
                "시장성·경쟁 관련 기업 근거를 확인하지 못했다.",
                "market_relation",
            )
        ]

    common_docs = (
        []
        if company.strip().casefold() == COMMON_COMPANY.casefold()
        else search_fn(COMMON_COMPANY, query, DEFAULT_SEARCH_K)
    )
    docs = _merge_unique_documents(company_docs, common_docs)
    if not docs:
        return [
            _unverified_claim(
                "시장성·경쟁 관련 근거를 확인하지 못했다.",
                "market_relation",
            )
        ]

    evidence_text, allowed_refs = _build_evidence(docs)
    if not evidence_text:
        return [
            _unverified_claim(
                "시장성·경쟁 근거의 출처 위치를 확인하지 못했다.",
                "market_relation",
            )
        ]

    prompt = f"""
기업: {company}

아래 근거만 사용해서 시장성·경쟁력을 분석하라.

분석 유형은 반드시 다음 중 하나로 구분한다.

- accessible_market
  기업이 실제로 접근할 수 있는 고객과 시장

- customer_pain_purchase_need
  고객 문제의 심각성과 실제 구매 필요성

- market_growth_drivers
  시장을 성장시키는 산업·기술·수요 요인

- market_entry_expansion
  시장 진입 가능성과 다른 고객·산업으로의 확장 가능성

- customer_value_vs_competitors
  기존 방식 또는 경쟁 제품 대비 고객이 얻는 가치

- hard_to_copy_assets
  데이터, 파트너십, 유통망, 기술 등 모방하기 어려운 자산

- customer_retention_switching_barrier
  고객 유지 요인과 경쟁 제품으로 전환하기 어려운 이유

근거에 없는 사실은 절대 추정하지 않는다.
전체 시장 규모를 이 기업이 실제 접근 가능한 시장으로 그대로 간주하지 않는다.
기업이 주장한 고객 수요를 실제 구매나 계약으로 바꾸어 쓰지 않는다.
company=COMMON인 근거는 산업 공통 정보이며 특정 기업의 실적처럼 표현하지 않는다.

기업이 특정 산업이나 고객군을 "적용 대상", "목표 시장", "진출 대상"으로
제시한 것만으로 실제 접근 가능한 시장이라고 단정하지 않는다.
근거가 "제시했다", "목표로 한다", "계획한다"라고 표현하면
claim에서도 그 표현 수준을 그대로 유지한다.

실제 고객, 계약, 납품, 실증, 상용 도입 등 시장 접근을 보여주는 근거가 있을 때만
"실제로 접근하고 있다" 또는 이에 준하는 표현을 사용한다.
경쟁사보다 우수하다는 근거가 없으면 우수하다고 판단하지 않는다.
기업 간 투자 순위나 점수는 만들지 않는다.

각 주장에는 그 주장을 직접 뒷받침하는 doc_id와 page만 연결한다.
확인할 수 없는 항목은 억지로 주장하지 않는다.

반드시 아래 형식의 JSON 배열만 반환한다.
설명문, Markdown, 코드블록은 출력하지 않는다.

[
  {{
    "analysis_type": "accessible_market",
    "claim": "확인된 사실을 한 문장으로 작성",
    "source_refs": [
      {{
        "doc_id": "문서 ID",
        "page": 1
      }}
    ],
    "status": "confirmed"
  }}
]

근거:
{evidence_text}
"""

    parsed_result = _parse_json_array(llm_fn(prompt))
    if parsed_result is None:
        return [
            _unverified_claim(
                "LLM 시장 분석 결과를 구조화하지 못했다.",
                "market_relation",
            )
        ]

    return _process_standard_analysis(
        parsed_result=parsed_result,
        allowed_refs=allowed_refs,
        criterion_map=MARKET_CRITERION_MAP,
        fallback_message="시장성·경쟁 관련 항목을 확인하지 못했다.",
        fallback_criterion_id="market_relation",
    )
