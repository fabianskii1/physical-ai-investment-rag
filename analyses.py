import json
import os
from typing import Callable, TypedDict, Literal, NotRequired

from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_openai import ChatOpenAI


load_dotenv()

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

def openai_llm(prompt: str) -> str:
    llm = ChatOpenAI(
        model=OPENAI_MODEL,
        temperature=0,
    )

    response = llm.invoke(prompt)

    return response.content

# 개발 A가 나중에 제공할 검색 함수 형태
SearchFn = Callable[[str, str, int], list[Document]]
LLMFn = Callable[[str], str]

# 근거 상태
AnalysisStatus = Literal["confirmed", "unverified"]

# 빅테크 관계 유형
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

    if not docs:
        return [
            {
                "claim": "대기업·빅테크 관련 근거를 확인하지 못했다.",
                "criterion_id": "bigtech_relation",
                "source_refs": [],
                "status": "unverified",
                "relation_type": "unverified",
            }
        ]

    evidence_parts = []
    source_refs: list[SourceRef] = []

    for doc in docs:
        doc_id = doc.metadata.get("doc_id")
        page = doc.metadata.get("page")

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

    if not evidence_parts:
        return [
            {
                "claim": "대기업·빅테크 관련 근거의 출처 위치를 확인하지 못했다.",
                "criterion_id": "bigtech_relation",
                "source_refs": [],
                "status": "unverified",
                "relation_type": "unverified",
            }
        ]

    evidence_text = "\n\n".join(evidence_parts)

    allowed_refs = {
        (ref["doc_id"], ref["page"])
        for ref in source_refs
    }

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

각 주장에는 그 주장을 직접 뒷받침하는
doc_id와 page만 연결한다.

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

    raw_result = llm_fn(prompt).strip()

    if raw_result.startswith("```"):
        raw_result = raw_result.removeprefix("```json")
        raw_result = raw_result.removeprefix("```")
        raw_result = raw_result.removesuffix("```")
        raw_result = raw_result.strip()

    try:
        parsed_result = json.loads(raw_result)
    except json.JSONDecodeError:
        return [
            {
                "claim": "LLM 분석 결과를 구조화하지 못했다.",
                "criterion_id": "bigtech_relation",
                "source_refs": [],
                "status": "unverified",
                "relation_type": "unverified",
            }
        ]

    if not isinstance(parsed_result, list):
        return [
            {
                "claim": "LLM 분석 결과 형식을 확인하지 못했다.",
                "criterion_id": "bigtech_relation",
                "source_refs": [],
                "status": "unverified",
                "relation_type": "unverified",
            }
        ]

    criterion_map = {
        "equity_investment": "bigtech_equity_investment",
        "mou_joint_development": "bigtech_contract_delivery_stage",
        "field_demo": "bigtech_contract_delivery_stage",
        "paid_contract_delivery": "bigtech_contract_delivery_stage",
        "repurchase_expansion": "customer_repurchase_expansion",
        "unverified": "bigtech_relation",
    }

    results: list[AnalysisClaim] = []

    for item in parsed_result:
        if not isinstance(item, dict):
            continue

        relation_type = item.get("relation_type")
        claim = item.get("claim", "").strip()
        status = item.get("status", "unverified")
        llm_refs = item.get("source_refs", [])

        if relation_type not in criterion_map:
            continue

        if not claim:
            continue

        valid_refs: list[SourceRef] = []
        invalid_ref_found = False

        for ref in llm_refs:
            if not isinstance(ref, dict):
                invalid_ref_found = True
                continue

            doc_id = ref.get("doc_id")
            page = ref.get("page")

            if (doc_id, page) not in allowed_refs:
                invalid_ref_found = True
                continue

            valid_refs.append(
                {
                    "doc_id": doc_id,
                    "page": page,
                }
            )

        if (
            status != "confirmed"
            or not valid_refs
            or invalid_ref_found
        ):
            status = "unverified"
            valid_refs = []

        results.append(
            {
                "claim": claim,
                "criterion_id": criterion_map[relation_type],
                "source_refs": valid_refs,
                "status": status,
                "relation_type": relation_type,
            }
        )

    if not results:
        return [
            {
                "claim": "대기업·빅테크 관련 관계를 확인하지 못했다.",
                "criterion_id": "bigtech_relation",
                "source_refs": [],
                "status": "unverified",
                "relation_type": "unverified",
            }
        ]

    return results



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
    - 소통의 명확성과 신뢰성
    - 핵심 작업 성능
    - 현장 안정성·안전성
    - 양산·유지보수 가능성
    - 모방하기 어려운 기술·자산

    점수 계산은 하지 않는다.
    """

    query = (
        f"{company}의 창업자와 핵심팀 전문성, 제품화 실행력, "
        "핵심 로봇 기술, 실제 작업 성능, 현장 안정성 및 안전성, "
        "양산 가능성, 유지보수, 특허·데이터·노하우 등 "
        "모방하기 어려운 기술 자산 관련 근거"
    )

    docs = search_fn(company, query, 4)

    if not docs:
        return [
            {
                "claim": "기술·팀·현장 적용 관련 근거를 확인하지 못했다.",
                "criterion_id": "technology_relation",
                "source_refs": [],
                "status": "unverified",
            }
        ]

    evidence_parts = []
    source_refs: list[SourceRef] = []

    for doc in docs:
        doc_id = doc.metadata.get("doc_id")
        page = doc.metadata.get("page")

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

    if not evidence_parts:
        return [
            {
                "claim": "기술·팀·현장 적용 근거의 출처 위치를 확인하지 못했다.",
                "criterion_id": "technology_relation",
                "source_refs": [],
                "status": "unverified",
            }
        ]

    evidence_text = "\n\n".join(evidence_parts)

    allowed_refs = {
        (ref["doc_id"], ref["page"])
        for ref in source_refs
    }

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

각 주장에는 그 주장을 직접 뒷받침하는
doc_id와 page만 연결한다.

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

    raw_result = llm_fn(prompt).strip()

    if raw_result.startswith("```"):
        raw_result = raw_result.removeprefix("```json")
        raw_result = raw_result.removeprefix("```")
        raw_result = raw_result.removesuffix("```")
        raw_result = raw_result.strip()

    try:
        parsed_result = json.loads(raw_result)
    except json.JSONDecodeError:
        return [
            {
                "claim": "LLM 기술 분석 결과를 구조화하지 못했다.",
                "criterion_id": "technology_relation",
                "source_refs": [],
                "status": "unverified",
            }
        ]

    if not isinstance(parsed_result, list):
        return [
            {
                "claim": "LLM 기술 분석 결과 형식을 확인하지 못했다.",
                "criterion_id": "technology_relation",
                "source_refs": [],
                "status": "unverified",
            }
        ]

    # 개발 B 내부 임시 ID
    # 추후 개발 C의 공식 21개 criterion_id와 매핑
    criterion_map = {
        "founder_expertise": "founder_expertise",
        "productization_execution": "productization_execution",
        "team_operations": "team_operations",
        "communication_reliability": "communication_reliability",
        "core_task_performance": "core_task_performance",
        "field_reliability_safety": "field_reliability_safety",
        "mass_production_maintenance": "mass_production_maintenance",
        "hard_to_copy_assets": "hard_to_copy_assets",
    }

    results: list[AnalysisClaim] = []

    for item in parsed_result:
        if not isinstance(item, dict):
            continue

        analysis_type = item.get("analysis_type")
        claim = item.get("claim", "").strip()
        status = item.get("status", "unverified")
        llm_refs = item.get("source_refs", [])

        if analysis_type not in criterion_map:
            continue

        if not claim:
            continue

        valid_refs: list[SourceRef] = []
        invalid_ref_found = False

        for ref in llm_refs:
            if not isinstance(ref, dict):
                invalid_ref_found = True
                continue

            doc_id = ref.get("doc_id")
            page = ref.get("page")

            if (doc_id, page) not in allowed_refs:
                invalid_ref_found = True
                continue

            valid_refs.append(
                {
                    "doc_id": doc_id,
                    "page": page,
                }
            )

        if (
            status != "confirmed"
            or not valid_refs
            or invalid_ref_found
        ):
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

    if not results:
        return [
            {
                "claim": "기술·팀·현장 적용 관련 항목을 확인하지 못했다.",
                "criterion_id": "technology_relation",
                "source_refs": [],
                "status": "unverified",
            }
        ]

    return results


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

    query = (
        f"{company}의 실제 고객과 접근 가능한 시장, 고객의 문제와 구매 필요성, "
        "시장 성장 동력, 시장 진입 및 확장 가능성, 기존 방식이나 경쟁 제품 대비 "
        "고객 가치, 차별화 자산, 고객 유지 및 전환 장벽 관련 근거"
    )

    docs = search_fn(company, query, 4)

    if not docs:
        return [
            {
                "claim": "시장성·경쟁 관련 근거를 확인하지 못했다.",
                "criterion_id": "market_relation",
                "source_refs": [],
                "status": "unverified",
            }
        ]

    evidence_parts = []
    source_refs: list[SourceRef] = []

    for doc in docs:
        doc_id = doc.metadata.get("doc_id")
        page = doc.metadata.get("page")

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

    if not evidence_parts:
        return [
            {
                "claim": "시장성·경쟁 근거의 출처 위치를 확인하지 못했다.",
                "criterion_id": "market_relation",
                "source_refs": [],
                "status": "unverified",
            }
        ]

    evidence_text = "\n\n".join(evidence_parts)

    allowed_refs = {
        (ref["doc_id"], ref["page"])
        for ref in source_refs
    }

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

전체 시장 규모를 이 기업이 실제 접근 가능한 시장으로
그대로 간주하지 않는다.

기업이 주장한 고객 수요를 실제 구매나 계약으로 바꾸어 쓰지 않는다.

기업이 특정 산업이나 고객군을 "적용 대상", "목표 시장",
"진출 대상"으로 제시한 것만으로 실제 접근 가능한 시장이라고 단정하지 않는다.

근거가 "제시했다", "목표로 한다", "계획한다"라고 표현하면
claim에서도 그 표현 수준을 그대로 유지한다.

실제 고객, 계약, 납품, 실증, 상용 도입 등
시장 접근을 보여주는 근거가 있을 때만
"실제로 접근하고 있다" 또는 이에 준하는 표현을 사용한다.

경쟁사보다 우수하다는 근거가 없으면
우수하다고 판단하지 않는다.

기업 간 투자 순위나 점수는 만들지 않는다.

각 주장에는 그 주장을 직접 뒷받침하는
doc_id와 page만 연결한다.

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

    raw_result = llm_fn(prompt).strip()

    if raw_result.startswith("```"):
        raw_result = raw_result.removeprefix("```json")
        raw_result = raw_result.removeprefix("```")
        raw_result = raw_result.removesuffix("```")
        raw_result = raw_result.strip()

    try:
        parsed_result = json.loads(raw_result)
    except json.JSONDecodeError:
        return [
            {
                "claim": "LLM 시장 분석 결과를 구조화하지 못했다.",
                "criterion_id": "market_relation",
                "source_refs": [],
                "status": "unverified",
            }
        ]

    if not isinstance(parsed_result, list):
        return [
            {
                "claim": "LLM 시장 분석 결과 형식을 확인하지 못했다.",
                "criterion_id": "market_relation",
                "source_refs": [],
                "status": "unverified",
            }
        ]

    criterion_map = {
        "accessible_market": "accessible_market",
        "customer_pain_purchase_need": "customer_pain_purchase_need",
        "market_growth_drivers": "market_growth_drivers",
        "market_entry_expansion": "market_entry_expansion",
        "customer_value_vs_competitors": "customer_value_vs_competitors",
        "hard_to_copy_assets": "hard_to_copy_assets",
        "customer_retention_switching_barrier": "customer_retention_switching_barrier",
    }

    results: list[AnalysisClaim] = []

    for item in parsed_result:
        if not isinstance(item, dict):
            continue

        analysis_type = item.get("analysis_type")
        claim = item.get("claim", "").strip()
        status = item.get("status", "unverified")
        llm_refs = item.get("source_refs", [])

        if analysis_type not in criterion_map:
            continue

        if not claim:
            continue

        valid_refs: list[SourceRef] = []
        invalid_ref_found = False

        for ref in llm_refs:
            if not isinstance(ref, dict):
                invalid_ref_found = True
                continue

            doc_id = ref.get("doc_id")
            page = ref.get("page")

            if (doc_id, page) not in allowed_refs:
                invalid_ref_found = True
                continue

            valid_refs.append(
                {
                    "doc_id": doc_id,
                    "page": page,
                }
            )

        if (
            status != "confirmed"
            or not valid_refs
            or invalid_ref_found
        ):
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

    if not results:
        return [
            {
                "claim": "시장성·경쟁 관련 항목을 확인하지 못했다.",
                "criterion_id": "market_relation",
                "source_refs": [],
                "status": "unverified",
            }
        ]

    return results