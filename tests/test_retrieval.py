"""개발 A 검색기의 팀 계약과 PDF→검색 흐름을 검증한다.

실제 BGE-M3 가중치를 내려받지 않고도 테스트가 반복 가능하도록 작은 결정적
임베더를 사용한다. PDF 텍스트 추출은 팀의 실제 ``load_pdf``를 그대로 거친다.
"""

import csv
import inspect
import shutil
from collections.abc import Iterator, Sequence
from pathlib import Path

import numpy as np
import pytest
from langchain_core.documents import Document

import retrieval
from retrieval import (
    CatalogError,
    build_index,
    clear_index,
    initialize,
    load_catalog,
    search,
)


class KeywordEmbeddingModel:
    """키워드 출현 횟수를 벡터로 만드는 오프라인 테스트용 임베더."""

    keywords = ("motomind", "robros", "bmw", "contract", "factory")

    def encode(self, sentences: Sequence[str], **_kwargs: object) -> np.ndarray:
        """각 문장에서 정해진 키워드가 나온 횟수를 dense 벡터로 반환한다."""

        return np.asarray(
            [
                [text.casefold().count(keyword) for keyword in self.keywords]
                for text in sentences
            ],
            dtype=np.float32,
        )


# 아래 helper는 각 테스트가 독립된 임시 저장소 구조와 catalog를 만들게 한다.
def _write_catalog(root: Path, rows: list[dict[str, object]]) -> Path:
    """고정 필드 순서로 임시 ``data/catalog.csv``를 작성한다."""

    catalog = root / "data" / "catalog.csv"
    catalog.parent.mkdir(parents=True, exist_ok=True)
    with catalog.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=retrieval.CATALOG_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return catalog


def _copy_fixture(root: Path) -> str:
    """공용 3페이지 PDF fixture를 임시 저장소에 복사하고 상대경로를 반환한다."""

    relative_path = Path("tests/fixtures/humanoid_pdf_loader_sample.pdf")
    destination = root / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).parent / "fixtures" / relative_path.name
    shutil.copy2(source, destination)
    return relative_path.as_posix()


def _catalog_row(file_path: str, **overrides: object) -> dict[str, object]:
    """기본적으로 유효한 catalog 행을 만들고 테스트별 값만 덮어쓴다."""

    row: dict[str, object] = {
        "doc_id": "fixture-001",
        "company": "figure_ai",
        "category": "technology",
        "title": "Humanoid loader fixture",
        "publisher": "Project team",
        "published_at": "2026-09-29",
        "source_url": "https://example.com/fixture",
        "file_path": file_path,
        "page_count": 3,
        "key_pages": "1-3",
        "related_scorecard_items": "핵심 작업 성능",
        "notes": "검색기 테스트 fixture",
    }
    row.update(overrides)
    return row


@pytest.fixture(autouse=True)
def reset_default_index() -> Iterator[None]:
    """테스트 사이에 모듈 공용 인덱스가 공유되지 않도록 초기화한다."""

    clear_index()
    yield
    clear_index()


# 개발 B와 합의한 공개 함수의 인자와 기본값이 바뀌지 않는지 확인한다.
def test_public_search_signature_is_fixed() -> None:
    signature = inspect.signature(search)
    assert list(signature.parameters) == ["company", "query", "k"]
    assert signature.parameters["k"].default == 4
    assert signature.return_annotation == "list[Document]"


# 실제 PDF 로더부터 공용 search까지 연결해 원본 페이지·상대경로 보존을 확인한다.
def test_pdf_to_search_returns_original_page_and_relative_path(tmp_path: Path) -> None:
    file_path = _copy_fixture(tmp_path)
    catalog = _write_catalog(tmp_path, [_catalog_row(file_path)])

    retriever = initialize(
        catalog,
        embedding_model=KeywordEmbeddingModel(),
        chunk_size=4_000,
        chunk_overlap=200,
    )
    results = search("figure_ai", "BMW factory", k=1)

    assert retriever.document_count == 3
    assert len(results) == 1
    assert "BMW" in results[0].page_content
    assert results[0].metadata["doc_id"] == "fixture-001"
    assert results[0].metadata["company"] == "figure_ai"
    assert results[0].metadata["page"] == 3
    assert results[0].metadata["file_path"] == file_path
    assert results[0].metadata["category"] == "technology"
    assert results[0].metadata["published_at"] == "2026-09-29"
    assert results[0].metadata["publication_date"] == "2026-09-29"
    assert results[0].metadata["source_url"] == "https://example.com/fixture"
    assert not Path(results[0].metadata["file_path"]).is_absolute()


# 다른 기업의 점수가 더 높더라도 요청 기업의 청크만 반환해야 한다.
def test_company_filter_is_applied_before_ranking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_path = tmp_path / "data/raw/acme/first.pdf"
    second_path = tmp_path / "data/raw/beta/second.pdf"
    first_path.parent.mkdir(parents=True)
    second_path.parent.mkdir(parents=True)
    first_path.write_bytes(b"%PDF-test")
    second_path.write_bytes(b"%PDF-test")
    catalog = _write_catalog(
        tmp_path,
        [
            _catalog_row(
                first_path.relative_to(tmp_path).as_posix(),
                doc_id="acme-001",
                company="acme",
                page_count=1,
            ),
            _catalog_row(
                second_path.relative_to(tmp_path).as_posix(),
                doc_id="beta-001",
                company="beta",
                page_count=1,
            ),
        ],
    )

    def fake_load_pdf(path: Path, doc_id: str, company: str) -> list[Document]:
        """기업 필터 순서만 격리해 확인하기 위한 최소 페이지 문서를 만든다."""

        content = "factory overview" if company == "acme" else "contract contract"
        return [
            Document(
                page_content=content,
                metadata={
                    "doc_id": doc_id,
                    "company": company,
                    "file_path": str(path),
                    "page": 1,
                },
            )
        ]

    monkeypatch.setattr(retrieval, "load_pdf", fake_load_pdf)
    retriever = build_index(catalog, embedding_model=KeywordEmbeddingModel())

    results = retriever.search("acme", "contract", k=4)
    assert [result.metadata["doc_id"] for result in results] == ["acme-001"]
    assert retriever.search("missing-company", "contract") == []


# 기업별 검색 범위를 유지하면서 COMMON도 독립 검색할 수 있는지 확인한다.
def test_company_and_common_search_scopes_are_separate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    company_path = tmp_path / "data/raw/acme/company.pdf"
    other_path = tmp_path / "data/raw/beta/other.pdf"
    common_path = tmp_path / "data/raw/common/market.pdf"
    company_path.parent.mkdir(parents=True)
    other_path.parent.mkdir(parents=True)
    common_path.parent.mkdir(parents=True)
    company_path.write_bytes(b"%PDF-test")
    other_path.write_bytes(b"%PDF-test")
    common_path.write_bytes(b"%PDF-test")
    catalog = _write_catalog(
        tmp_path,
        [
            _catalog_row(
                company_path.relative_to(tmp_path).as_posix(),
                doc_id="acme-001",
                company="acme",
                page_count=1,
            ),
            _catalog_row(
                other_path.relative_to(tmp_path).as_posix(),
                doc_id="beta-001",
                company="beta",
                page_count=1,
            ),
            _catalog_row(
                common_path.relative_to(tmp_path).as_posix(),
                doc_id="common-001",
                company="COMMON",
                category="common",
                page_count=1,
            ),
        ],
    )

    def fake_load_pdf(path: Path, doc_id: str, company: str) -> list[Document]:
        """기업·공통·타사 청크의 후보 포함 여부를 구분한다."""

        content_by_company = {
            "acme": "factory overview",
            "beta": "contract contract contract",
            "COMMON": "contract contract",
        }
        return [
            Document(
                page_content=content_by_company[company],
                metadata={
                    "doc_id": doc_id,
                    "company": company,
                    "file_path": str(path),
                    "page": 1,
                },
            )
        ]

    monkeypatch.setattr(retrieval, "load_pdf", fake_load_pdf)
    retriever = build_index(catalog, embedding_model=KeywordEmbeddingModel())

    results = retriever.search("acme", "contract", k=4)
    assert [result.metadata["doc_id"] for result in results] == ["acme-001"]
    assert all(result.metadata["company"] != "beta" for result in results)
    common_results = retriever.search("COMMON", "contract")
    assert [result.metadata["doc_id"] for result in common_results] == ["common-001"]


# 이어지는 테스트는 잘못된 catalog가 모델 임베딩 전에 차단되는지 확인한다.
def test_catalog_rejects_duplicate_doc_ids(tmp_path: Path) -> None:
    file_path = _copy_fixture(tmp_path)
    catalog = _write_catalog(
        tmp_path,
        [
            _catalog_row(file_path),
            _catalog_row(file_path, company="another-company"),
        ],
    )

    with pytest.raises(CatalogError, match="duplicate doc_id"):
        load_catalog(catalog)


def test_catalog_requires_exact_fields_and_relative_paths(tmp_path: Path) -> None:
    catalog = tmp_path / "data" / "catalog.csv"
    catalog.parent.mkdir()
    catalog.write_text("doc_id,company,file_path\na,b,/tmp/a.pdf\n", encoding="utf-8")

    with pytest.raises(CatalogError, match="fields must be exactly"):
        load_catalog(catalog)

    file_path = _copy_fixture(tmp_path)
    absolute_catalog = _write_catalog(
        tmp_path,
        [_catalog_row(str((tmp_path / file_path).resolve()))],
    )
    with pytest.raises(CatalogError, match="repository-relative"):
        load_catalog(absolute_catalog)


def test_catalog_rejects_rows_with_extra_values(tmp_path: Path) -> None:
    file_path = _copy_fixture(tmp_path)
    catalog = _write_catalog(tmp_path, [_catalog_row(file_path)])
    with catalog.open("a", encoding="utf-8") as stream:
        stream.write(",".join(["extra"] * (len(retrieval.CATALOG_FIELDS) + 1)) + "\n")

    with pytest.raises(CatalogError, match="more values than fields"):
        load_catalog(catalog)


def test_page_count_mismatch_stops_index_build(tmp_path: Path) -> None:
    file_path = _copy_fixture(tmp_path)
    catalog = _write_catalog(tmp_path, [_catalog_row(file_path, page_count=2)])

    with pytest.raises(CatalogError, match="page_count mismatch"):
        build_index(catalog, embedding_model=KeywordEmbeddingModel())


# 빈 기업명·질의와 잘못된 k가 모호한 빈 결과로 처리되지 않게 한다.
@pytest.mark.parametrize(
    ("company", "query", "k", "message"),
    [
        ("", "query", 1, "company"),
        ("figure_ai", "", 1, "query"),
        ("figure_ai", "query", 0, "k"),
    ],
)
def test_search_validates_inputs(
    tmp_path: Path,
    company: str,
    query: str,
    k: int,
    message: str,
) -> None:
    file_path = _copy_fixture(tmp_path)
    catalog = _write_catalog(tmp_path, [_catalog_row(file_path)])
    retriever = build_index(
        catalog,
        embedding_model=KeywordEmbeddingModel(),
        chunk_size=4_000,
        chunk_overlap=200,
    )

    with pytest.raises(ValueError, match=message):
        retriever.search(company, query, k)
