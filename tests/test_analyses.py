from langchain_core.documents import Document

from analyses import analyze_bigtech, analyze_market, analyze_technology


def mock_bigtech_search(company: str, query: str, k: int = 4):
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


def mock_empty_search(company: str, query: str, k: int = 4):
    return []


def mock_bigtech_llm(prompt: str) -> str:
    return """
[
  {
    "relation_type": "field_demo",
    "claim": "ROBROS는 롯데글로벌로지스 물류 현장에서 휴머노이드 로봇 실증을 진행했다.",
    "source_refs": [{"doc_id": "robros_test_001", "page": 3}],
    "status": "confirmed"
  }
]
""".strip()


def mock_bigtech_paid_llm(prompt: str) -> str:
    return """
[
  {
    "relation_type": "paid_contract_delivery",
    "claim": "ROBROS는 대기업 고객사에 유상 납품을 진행했다.",
    "source_refs": [{"doc_id": "robros_test_001", "page": 3}],
    "status": "confirmed"
  }
]
""".strip()


def mock_bigtech_unverified_but_confirmed_llm(prompt: str) -> str:
    return """
[
  {
    "relation_type": "unverified",
    "claim": "대기업 관계 여부를 확인할 수 없다.",
    "source_refs": [{"doc_id": "robros_test_001", "page": 3}],
    "status": "confirmed"
  }
]
""".strip()


def mock_bigtech_fake_source_llm(prompt: str) -> str:
    return """
[
  {
    "relation_type": "field_demo",
    "claim": "ROBROS는 대기업 물류 현장에서 실증을 진행했다.",
    "source_refs": [{"doc_id": "fake_document_999", "page": 99}],
    "status": "confirmed"
  }
]
""".strip()


def mock_technology_llm(prompt: str) -> str:
    return """
[
  {
    "analysis_type": "core_task_performance",
    "claim": "ROBROS의 휴머노이드 로봇은 물류 현장에서 물체를 집고 옮기는 작업을 실증했다.",
    "source_refs": [{"doc_id": "robros_tech_001", "page": 5}],
    "status": "confirmed"
  }
]
""".strip()


def mock_technology_fake_source_llm(prompt: str) -> str:
    return """
[
  {
    "analysis_type": "core_task_performance",
    "claim": "ROBROS의 로봇은 물류 현장에서 작업 성능을 검증했다.",
    "source_refs": [{"doc_id": "fake_tech_document", "page": 999}],
    "status": "confirmed"
  }
]
""".strip()


def mock_market_llm(prompt: str) -> str:
    return """
[
  {
    "analysis_type": "accessible_market",
    "claim": "ROBROS는 물류센터의 반복적인 물품 이동 작업을 주요 적용 시장으로 제시하고 있다.",
    "source_refs": [{"doc_id": "robros_market_001", "page": 7}],
    "status": "confirmed"
  }
]
""".strip()


def mock_market_fake_source_llm(prompt: str) -> str:
    return """
[
  {
    "analysis_type": "accessible_market",
    "claim": "ROBROS는 물류 시장을 주요 적용 대상으로 한다.",
    "source_refs": [{"doc_id": "fake_market_document", "page": 999}],
    "status": "confirmed"
  }
]
""".strip()


def test_analyze_bigtech():
    result = analyze_bigtech("ROBROS", mock_bigtech_search, mock_bigtech_llm)

    assert len(result) == 1
    claim = result[0]
    assert claim["relation_type"] == "field_demo"
    assert claim["criterion_id"] == "bigtech_contract_delivery_stage"
    assert claim["status"] == "confirmed"
    assert claim["source_refs"] == [{"doc_id": "robros_test_001", "page": 3}]

    print("✅ analyze_bigtech 구조화 분석 테스트 통과")


def test_analyze_bigtech_unverified():
    result = analyze_bigtech("ROBROS", mock_empty_search, mock_bigtech_llm)

    assert len(result) == 1
    assert result[0]["status"] == "unverified"
    assert result[0]["source_refs"] == []

    print("✅ analyze_bigtech 확인 불가 테스트 통과")


def test_analyze_bigtech_fake_source():
    result = analyze_bigtech(
        "ROBROS",
        mock_bigtech_search,
        mock_bigtech_fake_source_llm,
    )

    assert len(result) == 1
    assert result[0]["status"] == "unverified"
    assert result[0]["source_refs"] == []

    print("✅ analyze_bigtech 가짜 출처 차단 테스트 통과")


def test_analyze_bigtech_unverified_relation_forced():
    result = analyze_bigtech(
        "ROBROS",
        mock_bigtech_search,
        mock_bigtech_unverified_but_confirmed_llm,
    )

    assert len(result) == 1
    assert result[0]["relation_type"] == "unverified"
    assert result[0]["status"] == "unverified"
    assert result[0]["source_refs"] == []

    print("✅ analyze_bigtech unverified 강제 처리 테스트 통과")


def test_analyze_bigtech_paid_contract_maps_to_two_criteria():
    result = analyze_bigtech(
        "ROBROS",
        mock_bigtech_search,
        mock_bigtech_paid_llm,
    )

    criterion_ids = {claim["criterion_id"] for claim in result}
    assert criterion_ids == {
        "paid_sales_delivery",
        "bigtech_contract_delivery_stage",
    }
    assert all(claim["status"] == "confirmed" for claim in result)

    print("✅ analyze_bigtech 유상 납품 다중 기준 매핑 테스트 통과")


def test_analyze_technology():
    result = analyze_technology(
        "ROBROS",
        mock_technology_search,
        mock_technology_llm,
    )

    assert len(result) == 1
    claim = result[0]
    assert claim["criterion_id"] == "core_task_performance"
    assert claim["status"] == "confirmed"
    assert claim["source_refs"] == [{"doc_id": "robros_tech_001", "page": 5}]
    assert "물류 현장" in claim["claim"]

    print("✅ analyze_technology 구조화 분석 테스트 통과")


def test_analyze_technology_fake_source():
    result = analyze_technology(
        "ROBROS",
        mock_technology_search,
        mock_technology_fake_source_llm,
    )

    assert len(result) == 1
    assert result[0]["status"] == "unverified"
    assert result[0]["source_refs"] == []

    print("✅ analyze_technology 가짜 출처 차단 테스트 통과")


def test_analyze_market():
    result = analyze_market("ROBROS", mock_market_search, mock_market_llm)

    assert len(result) == 1
    claim = result[0]
    assert claim["criterion_id"] == "accessible_market"
    assert claim["status"] == "confirmed"
    assert claim["source_refs"] == [{"doc_id": "robros_market_001", "page": 7}]
    assert "물류센터" in claim["claim"]

    print("✅ analyze_market 구조화 분석 테스트 통과")


def test_analyze_market_fake_source():
    result = analyze_market(
        "ROBROS",
        mock_market_search,
        mock_market_fake_source_llm,
    )

    assert len(result) == 1
    assert result[0]["status"] == "unverified"
    assert result[0]["source_refs"] == []

    print("✅ analyze_market 가짜 출처 차단 테스트 통과")


if __name__ == "__main__":
    test_analyze_bigtech()
    test_analyze_bigtech_unverified()
    test_analyze_bigtech_fake_source()
    test_analyze_bigtech_unverified_relation_forced()
    test_analyze_bigtech_paid_contract_maps_to_two_criteria()
    test_analyze_technology()
    test_analyze_technology_fake_source()
    test_analyze_market()
    test_analyze_market_fake_source()
