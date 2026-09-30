from langchain_core.documents import Document

from analyses import analyze_bigtech, analyze_technology, analyze_market


def mock_search(company: str, query: str, k: int = 4):
    """
    개발 A의 실제 검색기가 완성되기 전까지 사용하는 가짜 검색기
    """

    return [
        Document(
            page_content=(
                "ROBROS는 롯데글로벌로지스 물류 현장에서 "
                "휴머노이드 로봇 실증을 진행했다."
            ),
            metadata={
                "doc_id": "robros_test_001",
                "company": "ROBROS",
                "page": 3,
                "file_path": "data/raw/robros/test.pdf",
            },
        )
    ]

def mock_technology_search(company: str, query: str, k: int = 4):
    return [
        Document(
            page_content=(
                "ROBROS는 물류 현장에서 물체를 집고 옮기는 "
                "휴머노이드 로봇 실증을 진행했다."
            ),
            metadata={
                "doc_id": "robros_tech_001",
                "company": "ROBROS",
                "page": 5,
                "file_path": "data/raw/robros/technology.pdf",
            },
        )
    ]


def mock_technology_llm(prompt: str) -> str:
    return """
[
  {
    "analysis_type": "core_task_performance",
    "claim": "ROBROS의 휴머노이드 로봇은 물류 현장에서 물체를 집고 옮기는 작업을 실증했다.",
    "source_refs": [
      {
        "doc_id": "robros_tech_001",
        "page": 5
      }
    ],
    "status": "confirmed"
  }
]
""".strip()


def mock_llm(prompt: str) -> str:
    """
    실제 GPT를 호출하지 않고
    구조화된 LLM 응답을 흉내 내는 테스트용 함수
    """

    return """
[
  {
    "relation_type": "field_demo",
    "claim": "ROBROS는 롯데글로벌로지스 물류 현장에서 휴머노이드 로봇 실증을 진행했다.",
    "source_refs": [
      {
        "doc_id": "robros_test_001",
        "page": 3
      }
    ],
    "status": "confirmed"
  }
]
""".strip()

def mock_llm_fake_source(prompt: str) -> str:
    return """
[
  {
    "relation_type": "field_demo",
    "claim": "ROBROS는 대기업 물류 현장에서 실증을 진행했다.",
    "source_refs": [
      {
        "doc_id": "fake_document_999",
        "page": 99
      }
    ],
    "status": "confirmed"
  }
]
""".strip()


def test_analyze_bigtech():
    result = analyze_bigtech(
        company="ROBROS",
        search_fn=mock_search,
        llm_fn=mock_llm,
    )

    assert len(result) == 1

    claim = result[0]

    assert claim["claim"] == (
        "ROBROS는 롯데글로벌로지스 물류 현장에서 "
        "휴머노이드 로봇 실증을 진행했다."
    )

    assert claim["relation_type"] == "field_demo"
    assert claim["criterion_id"] == "bigtech_contract_delivery_stage"
    assert claim["status"] == "confirmed"

    assert claim["source_refs"] == [
        {
            "doc_id": "robros_test_001",
            "page": 3,
        }
    ]

    print("✅ analyze_bigtech 구조화 분석 테스트 통과")


def mock_empty_search(company: str, query: str, k: int = 4):
    return []


def test_analyze_bigtech_unverified():
    result = analyze_bigtech(
        company="ROBROS",
        search_fn=mock_empty_search,
        llm_fn=mock_llm,
    )

    assert len(result) == 1

    claim = result[0]

    assert claim["status"] == "unverified"
    assert claim["source_refs"] == []

    print("✅ analyze_bigtech 확인 불가 테스트 통과")

def mock_technology_llm_fake_source(prompt: str) -> str:
    return """
[
  {
    "analysis_type": "core_task_performance",
    "claim": "ROBROS의 로봇은 물류 현장에서 작업 성능을 검증했다.",
    "source_refs": [
      {
        "doc_id": "fake_tech_document",
        "page": 999
      }
    ],
    "status": "confirmed"
  }
]
""".strip()


def test_analyze_technology_fake_source():
    result = analyze_technology(
        company="ROBROS",
        search_fn=mock_technology_search,
        llm_fn=mock_technology_llm_fake_source,
    )

    assert len(result) == 1

    claim = result[0]

    assert claim["status"] == "unverified"
    assert claim["source_refs"] == []

    print("✅ analyze_technology 가짜 출처 차단 테스트 통과")


def test_analyze_bigtech_fake_source():
    result = analyze_bigtech(
        company="ROBROS",
        search_fn=mock_search,
        llm_fn=mock_llm_fake_source,
    )

    assert len(result) == 1

    claim = result[0]

    assert claim["status"] == "unverified"
    assert claim["source_refs"] == []

    print("✅ analyze_bigtech 가짜 출처 차단 테스트 통과")


def test_analyze_technology():
    result = analyze_technology(
        company="ROBROS",
        search_fn=mock_technology_search,
        llm_fn=mock_technology_llm,
    )

    assert len(result) == 1

    claim = result[0]

    assert claim["criterion_id"] == "core_task_performance"
    assert claim["status"] == "confirmed"

    assert claim["source_refs"] == [
        {
            "doc_id": "robros_tech_001",
            "page": 5,
        }
    ]

    assert "물류 현장" in claim["claim"]

    print("✅ analyze_technology 구조화 분석 테스트 통과")

def mock_market_search(company: str, query: str, k: int = 4):
    return [
        Document(
            page_content=(
                "ROBROS는 물류센터의 반복적인 물품 이동 작업을 "
                "휴머노이드 로봇의 주요 적용 대상으로 제시하고 있다."
            ),
            metadata={
                "doc_id": "robros_market_001",
                "company": "ROBROS",
                "page": 7,
                "file_path": "data/raw/robros/market.pdf",
            },
        )
    ]


def mock_market_llm(prompt: str) -> str:
    return """
[
  {
    "analysis_type": "accessible_market",
    "claim": "ROBROS는 물류센터의 반복적인 물품 이동 작업을 주요 적용 시장으로 제시하고 있다.",
    "source_refs": [
      {
        "doc_id": "robros_market_001",
        "page": 7
      }
    ],
    "status": "confirmed"
  }
]
""".strip()


def test_analyze_market():
    result = analyze_market(
        company="ROBROS",
        search_fn=mock_market_search,
        llm_fn=mock_market_llm,
    )

    assert len(result) == 1

    claim = result[0]

    assert claim["criterion_id"] == "accessible_market"
    assert claim["status"] == "confirmed"

    assert claim["source_refs"] == [
        {
            "doc_id": "robros_market_001",
            "page": 7,
        }
    ]

    assert "물류센터" in claim["claim"]

    print("✅ analyze_market 구조화 분석 테스트 통과")


def mock_market_llm_fake_source(prompt: str) -> str:
    return """
[
  {
    "analysis_type": "accessible_market",
    "claim": "ROBROS는 물류 시장을 주요 적용 대상으로 한다.",
    "source_refs": [
      {
        "doc_id": "fake_market_document",
        "page": 999
      }
    ],
    "status": "confirmed"
  }
]
""".strip()


def test_analyze_market_fake_source():
    result = analyze_market(
        company="ROBROS",
        search_fn=mock_market_search,
        llm_fn=mock_market_llm_fake_source,
    )

    assert len(result) == 1

    claim = result[0]

    assert claim["status"] == "unverified"
    assert claim["source_refs"] == []

    print("✅ analyze_market 가짜 출처 차단 테스트 통과")


if __name__ == "__main__":
    test_analyze_bigtech()
    test_analyze_bigtech_unverified()
    test_analyze_bigtech_fake_source()
    test_analyze_technology()
    test_analyze_technology_fake_source()
    test_analyze_market()
    test_analyze_market_fake_source()