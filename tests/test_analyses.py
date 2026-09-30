from langchain_core.documents import Document

from analyses import analyze_bigtech


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


def test_analyze_bigtech():
    result = analyze_bigtech(
        company="ROBROS",
        search_fn=mock_search,
    )

    assert len(result) == 1

    claim = result[0]

    assert claim["claim"] == (
        "ROBROS는 롯데글로벌로지스 물류 현장에서 "
        "휴머노이드 로봇 실증을 진행했다."
    )
    assert claim["criterion_id"] == "bigtech_relation"
    assert claim["status"] == "confirmed"

    assert claim["source_refs"] == [
        {
            "doc_id": "robros_test_001",
            "page": 3,
        }
    ]

    print("✅ analyze_bigtech 기본 테스트 통과")

def mock_empty_search(company: str, query: str, k: int = 4):
    return []


def test_analyze_bigtech_unverified():
    result = analyze_bigtech(
        company="ROBROS",
        search_fn=mock_empty_search,
    )

    assert len(result) == 1

    claim = result[0]

    assert claim["status"] == "unverified"
    assert claim["source_refs"] == []

    print("✅ analyze_bigtech 확인 불가 테스트 통과")


if __name__ == "__main__":
    test_analyze_bigtech()
    test_analyze_bigtech_unverified()