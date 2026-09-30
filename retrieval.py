"""Physical AI 투자 검토 RAG의 BGE-M3 dense 검색 모듈.

팀 공통 공개 인터페이스는 ``search(company, query, k=4)``다. 최초 검색 시
``data/catalog.csv``를 읽어 메모리 인덱스를 지연 생성한다. 시작 단계에서
catalog·PDF·모델 오류를 먼저 확인하려면 :func:`initialize`를 호출한다.
"""

from __future__ import annotations

import csv
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

import numpy as np
from langchain_core.documents import Document

from PDF.pdf_loader import load_pdf

# catalog 스키마와 개발 A의 기본 검색 설정
CATALOG_FIELDS = (
    "doc_id",
    "company",
    "category",
    "title",
    "publisher",
    "published_at",
    "source_url",
    "file_path",
    "page_count",
    "key_pages",
    "related_scorecard_items",
    "notes",
)
DEFAULT_CATALOG_PATH = Path(__file__).resolve().parent / "data" / "catalog.csv"
DEFAULT_MODEL_NAME = "BAAI/bge-m3"
DEFAULT_CHUNK_SIZE = 1_000
DEFAULT_CHUNK_OVERLAP = 150

__all__ = [
    "BgeM3DenseEncoder",
    "CatalogError",
    "CatalogRecord",
    "DenseRetriever",
    "IndexBuildError",
    "RetrievalError",
    "build_index",
    "clear_index",
    "initialize",
    "load_catalog",
    "search",
]


# 호출자가 입력 오류, catalog 오류, 인덱스 오류를 구분해 처리할 수 있도록 검색 계층의 공개 예외를 한곳에 모은다.
class RetrievalError(Exception):
    """검색 모듈에서 외부로 전달하는 모든 예외의 기본 클래스."""


class CatalogError(RetrievalError, ValueError):
    """``catalog.csv``가 팀의 고정 형식을 위반했을 때 발생한다."""


class IndexBuildError(RetrievalError, RuntimeError):
    """문서 또는 임베딩으로 검색 가능한 인덱스를 만들 수 없을 때 발생한다."""


class DenseEmbeddingModel(Protocol):
    """실제 BGE-M3와 테스트용 임베더가 함께 따르는 최소 인터페이스."""

    def encode(self, sentences: Sequence[str], **kwargs: object) -> np.ndarray:
        """입력 문장마다 dense 벡터 하나를 반환한다."""


@dataclass(frozen=True, slots=True)
class CatalogRecord:
    """검증이 끝난 ``data/catalog.csv`` 한 행과 실제 PDF 경로."""

    doc_id: str
    company: str
    category: str
    title: str
    publisher: str
    published_at: str
    source_url: str
    file_path: str  # 검색 결과에 남길 저장소 상대경로
    page_count: int
    key_pages: str
    related_scorecard_items: str
    notes: str
    source_path: Path  # pdf_loader.py에 전달할 실제 PDF 절대경로


class BgeM3DenseEncoder:
    """Sentence Transformers의 BGE-M3 dense 임베딩을 감싸는 어댑터.

    ``sentence_transformers``와 모델 가중치는 인덱스를 만들 때만 로드한다.
    따라서 모듈 import만으로 모델 다운로드가 시작되지 않는다.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL_NAME,
        *,
        device: str | None = None,
        batch_size: int = 16,
    ) -> None:
        """모델명·실행 장치·배치 크기를 받아 실제 임베딩 모델을 준비한다."""

        if not isinstance(model_name, str) or not model_name.strip():
            raise ValueError("model_name must be a non-empty string")
        if (
            isinstance(batch_size, bool)
            or not isinstance(batch_size, int)
            or batch_size < 1
        ):
            raise ValueError("batch_size must be a positive integer")

        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - depends on installation state
            raise IndexBuildError(
                "sentence-transformers is required for BGE-M3 embeddings; "
                "install the root requirements.txt"
            ) from exc

        self.model_name = model_name.strip()
        self.batch_size = batch_size
        self._model = SentenceTransformer(self.model_name, device=device)

    def encode(self, sentences: Sequence[str], **_kwargs: object) -> np.ndarray:
        """문서나 질의를 정규화된 BGE-M3 dense 벡터로 변환한다."""

        texts = list(sentences)
        if not texts:
            return np.empty((0, 0), dtype=np.float32)
        return np.asarray(
            self._model.encode(
                texts,
                batch_size=self.batch_size,
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            ),
            dtype=np.float32,
        )


# 아래 함수들은 catalog를 읽기 전에 값, 저장소 상대경로, PDF 존재 여부를
# 검증한다. 잘못된 자료 목록은 임베딩을 시작하기 전에 즉시 중단시킨다.
def _required_text(row_number: int, field: str, value: str | None) -> str:
    """필수 문자열 필드가 비어 있지 않은지 확인하고 공백을 정리한다."""

    if value is None or not value.strip():
        raise CatalogError(f"catalog row {row_number}: {field} must not be empty")
    return value.strip()


def _repository_root(catalog_path: Path, repository_root: str | Path | None) -> Path:
    """catalog의 상대경로를 해석할 저장소 루트를 결정한다."""

    if repository_root is not None:
        root = Path(repository_root).expanduser().resolve()
    elif catalog_path.parent.name == "data":
        root = catalog_path.parent.parent.resolve()
    else:
        raise CatalogError(
            "repository_root is required when catalog.csv is not inside a data directory"
        )
    if not root.is_dir():
        raise CatalogError(f"repository root is not a directory: {root}")
    return root


def _resolve_catalog_file(root: Path, row_number: int, file_path: str) -> tuple[str, Path]:
    """상대경로가 저장소 안의 실제 PDF를 가리키는지 검증한다."""

    relative_path = Path(file_path)
    if relative_path.is_absolute():
        raise CatalogError(
            f"catalog row {row_number}: file_path must be repository-relative"
        )

    source_path = (root / relative_path).resolve()
    try:
        normalized_relative = source_path.relative_to(root).as_posix()
    except ValueError as exc:
        raise CatalogError(
            f"catalog row {row_number}: file_path escapes the repository: {file_path}"
        ) from exc

    if source_path.suffix.lower() != ".pdf":
        raise CatalogError(f"catalog row {row_number}: file_path must point to a PDF")
    if not source_path.is_file():
        raise CatalogError(
            f"catalog row {row_number}: PDF file does not exist: {normalized_relative}"
        )
    return normalized_relative, source_path


def load_catalog(
    catalog_path: str | Path = DEFAULT_CATALOG_PATH,
    *,
    repository_root: str | Path | None = None,
) -> list[CatalogRecord]:
    """고정 스키마, ``doc_id`` 중복, 상대경로와 PDF 존재 여부를 검증한다."""

    path = Path(catalog_path).expanduser().resolve()
    if not path.is_file():
        raise CatalogError(f"catalog.csv not found: {path}")
    root = _repository_root(path, repository_root)

    records: list[CatalogRecord] = []
    seen_doc_ids: set[str] = set()
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if tuple(reader.fieldnames or ()) != CATALOG_FIELDS:
                expected = ", ".join(CATALOG_FIELDS)
                actual = ", ".join(reader.fieldnames or ()) or "[missing header]"
                raise CatalogError(
                    f"catalog fields must be exactly [{expected}], got [{actual}]"
                )

            for row_number, row in enumerate(reader, start=2):
                if None in row:
                    raise CatalogError(
                        f"catalog row {row_number}: row has more values than fields"
                    )
                if not any((value or "").strip() for value in row.values()):
                    continue
                doc_id = _required_text(row_number, "doc_id", row["doc_id"])
                company = _required_text(row_number, "company", row["company"])
                raw_file_path = _required_text(
                    row_number, "file_path", row["file_path"]
                )
                if doc_id in seen_doc_ids:
                    raise CatalogError(
                        f"catalog row {row_number}: duplicate doc_id: {doc_id}"
                    )
                seen_doc_ids.add(doc_id)

                try:
                    page_count = int(
                        _required_text(row_number, "page_count", row["page_count"])
                    )
                except ValueError as exc:
                    raise CatalogError(
                        f"catalog row {row_number}: page_count must be an integer"
                    ) from exc
                if page_count < 1:
                    raise CatalogError(
                        f"catalog row {row_number}: page_count must be positive"
                    )

                normalized_path, source_path = _resolve_catalog_file(
                    root, row_number, raw_file_path
                )
                records.append(
                    CatalogRecord(
                        doc_id=doc_id,
                        company=company,
                        category=(row["category"] or "").strip(),
                        title=(row["title"] or "").strip(),
                        publisher=(row["publisher"] or "").strip(),
                        published_at=(row["published_at"] or "").strip(),
                        source_url=(row["source_url"] or "").strip(),
                        file_path=normalized_path,
                        page_count=page_count,
                        key_pages=(row["key_pages"] or "").strip(),
                        related_scorecard_items=(
                            row["related_scorecard_items"] or ""
                        ).strip(),
                        notes=(row["notes"] or "").strip(),
                        source_path=source_path,
                    )
                )
    except UnicodeDecodeError as exc:
        raise CatalogError(f"catalog.csv must be UTF-8 encoded: {path}") from exc

    if not records:
        raise CatalogError("catalog.csv contains no document rows")
    return records


# 청크는 원본 페이지 경계를 넘지 않는다. 이 원칙 덕분에 검색 결과의
# ``page``를 그대로 보고서의 source reference로 사용할 수 있다.
def _validate_chunk_settings(chunk_size: int, chunk_overlap: int) -> None:
    """청크 크기와 중첩 범위가 안전한 값인지 확인한다."""

    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or chunk_size < 1:
        raise ValueError("chunk_size must be a positive integer")
    if (
        isinstance(chunk_overlap, bool)
        or not isinstance(chunk_overlap, int)
        or chunk_overlap < 0
        or chunk_overlap >= chunk_size
    ):
        raise ValueError("chunk_overlap must be an integer from 0 to chunk_size - 1")


def _chunk_text(
    text: str,
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> list[tuple[str, int, int]]:
    """원본 페이지 하나를 문단·문장 경계를 우선해 겹치는 청크로 나눈다.

    각 반환값은 ``(청크 본문, 페이지 내 시작 위치, 페이지 내 끝 위치)``다.
    """

    _validate_chunk_settings(chunk_size, chunk_overlap)
    if not text or not text.strip():
        return []

    chunks: list[tuple[str, int, int]] = []
    start = 0
    text_length = len(text)
    separators = ("\n\n", "\n", ". ", "。 ", "? ", "! ", " ")

    while start < text_length:
        maximum_end = min(start + chunk_size, text_length)
        end = maximum_end
        if maximum_end < text_length:
            minimum_break = start + max(1, int(chunk_size * 0.6))
            best_break = -1
            best_separator_length = 0
            for separator in separators:
                candidate = text.rfind(separator, minimum_break, maximum_end)
                if candidate > best_break:
                    best_break = candidate
                    best_separator_length = len(separator)
            if best_break >= minimum_break:
                end = best_break + best_separator_length

        raw_chunk = text[start:end]
        left_trim = len(raw_chunk) - len(raw_chunk.lstrip())
        right_trimmed = raw_chunk.rstrip()
        content_start = start + left_trim
        content_end = start + len(right_trimmed)
        content = text[content_start:content_end]
        if content:
            chunks.append((content, content_start, content_end))

        if end >= text_length:
            break
        next_start = max(start + 1, end - chunk_overlap)
        while next_start < text_length and text[next_start].isspace():
            next_start += 1
        start = next_start

    return chunks


def _encode(model: DenseEmbeddingModel, texts: Sequence[str]) -> np.ndarray:
    """임베더 출력을 검사하고 cosine 검색용 단위 벡터로 정규화한다."""

    try:
        vectors = np.asarray(
            model.encode(
                list(texts),
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            ),
            dtype=np.float32,
        )
    except Exception as exc:
        raise IndexBuildError("failed to create dense embeddings") from exc

    if vectors.ndim == 1 and len(texts) == 1:
        vectors = vectors.reshape(1, -1)
    if vectors.ndim != 2 or vectors.shape[0] != len(texts) or vectors.shape[1] == 0:
        raise IndexBuildError(
            "embedding model must return a non-empty 2D matrix with one row per text"
        )
    if not np.isfinite(vectors).all():
        raise IndexBuildError("embedding model returned non-finite values")

    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return np.divide(vectors, norms, out=np.zeros_like(vectors), where=norms != 0)


class DenseRetriever:
    """미리 계산한 청크 벡터에서 기업별 cosine 유사도 검색을 수행한다."""

    def __init__(
        self,
        documents: Sequence[Document],
        embeddings: np.ndarray,
        embedding_model: DenseEmbeddingModel,
    ) -> None:
        """문서·벡터를 보관하고 기업별 후보 인덱스를 미리 구성한다."""

        if not documents:
            raise IndexBuildError("cannot build an index without non-empty PDF text")
        if len(documents) != len(embeddings):
            raise IndexBuildError("document and embedding counts do not match")
        self._documents = tuple(documents)
        self._embeddings = np.asarray(embeddings, dtype=np.float32)
        self._embedding_model = embedding_model
        self._company_indices: dict[str, list[int]] = {}
        for index, document in enumerate(self._documents):
            company = str(document.metadata.get("company", "")).strip().casefold()
            if not company:
                raise IndexBuildError("every indexed chunk must contain company metadata")
            self._company_indices.setdefault(company, []).append(index)

    @property
    def document_count(self) -> int:
        """현재 인덱스에 포함된 전체 청크 수를 반환한다."""

        return len(self._documents)

    def search(self, company: str, query: str, k: int = 4) -> list[Document]:
        """기업 필터를 먼저 적용한 뒤 유사도가 높은 청크를 반환한다.

        결과는 원본 인덱스 문서를 변경하지 않도록 새 ``Document``로 만들며,
        기존 출처 metadata에 ``retrieval_score``만 추가한다.
        """

        if not isinstance(company, str) or not company.strip():
            raise ValueError("company must be a non-empty string")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        if isinstance(k, bool) or not isinstance(k, int) or k < 1:
            raise ValueError("k must be a positive integer")

        candidate_indices = self._company_indices.get(company.strip().casefold(), [])
        if not candidate_indices:
            return []

        query_vector = _encode(self._embedding_model, [query.strip()])
        if query_vector.shape[1] != self._embeddings.shape[1]:
            raise IndexBuildError(
                "query embedding dimension does not match the document index"
            )

        candidates = np.asarray(candidate_indices, dtype=np.int64)
        scores = self._embeddings[candidates] @ query_vector[0]
        ranked_positions = np.argsort(-scores, kind="stable")[:k]

        results: list[Document] = []
        for position in ranked_positions:
            document_index = int(candidates[position])
            indexed_document = self._documents[document_index]
            metadata = dict(indexed_document.metadata)
            metadata["retrieval_score"] = float(scores[position])
            results.append(
                Document(
                    page_content=indexed_document.page_content,
                    metadata=metadata,
                )
            )
        return results


def build_index(
    catalog_path: str | Path = DEFAULT_CATALOG_PATH,
    *,
    repository_root: str | Path | None = None,
    embedding_model: DenseEmbeddingModel | None = None,
    model_name: str = DEFAULT_MODEL_NAME,
    device: str | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> DenseRetriever:
    """catalog PDF를 로드하고 페이지별 청크와 dense 인덱스를 만든다.

    PDF 로더가 반환한 절대경로는 catalog의 저장소 상대경로로 교체한다.
    ``page_count``도 실제 로더 결과와 대조해 잘못된 자료 목록을 막는다.
    """

    _validate_chunk_settings(chunk_size, chunk_overlap)
    records = load_catalog(catalog_path, repository_root=repository_root)
    documents: list[Document] = []

    for record in records:
        page_documents = load_pdf(
            record.source_path,
            doc_id=record.doc_id,
            company=record.company,
        )
        if len(page_documents) != record.page_count:
            raise CatalogError(
                f"catalog page_count mismatch for {record.doc_id}: "
                f"expected {record.page_count}, loaded {len(page_documents)}"
            )

        for page_document in page_documents:
            page = page_document.metadata.get("page")
            if isinstance(page, bool) or not isinstance(page, int) or page < 1:
                raise IndexBuildError(
                    f"loader returned invalid page metadata for {record.doc_id}: {page!r}"
                )
            for chunk_index, (content, start, end) in enumerate(
                _chunk_text(
                    page_document.page_content,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                )
            ):
                documents.append(
                    Document(
                        page_content=content,
                        metadata={
                            "doc_id": record.doc_id,
                            "company": record.company,
                            "category": record.category,
                            "title": record.title,
                            "publisher": record.publisher,
                            "published_at": record.published_at,
                            # 기존 소비자를 위한 metadata 호환 별칭이다.
                            "publication_date": record.published_at,
                            "source_url": record.source_url,
                            "file_path": record.file_path,
                            "key_pages": record.key_pages,
                            "related_scorecard_items": (
                                record.related_scorecard_items
                            ),
                            "notes": record.notes,
                            "page": page,
                            "chunk_index": chunk_index,
                            "char_start": start,
                            "char_end": end,
                        },
                    )
                )

    if not documents:
        raise IndexBuildError("catalog PDFs contain no extractable text to index")

    model = embedding_model or BgeM3DenseEncoder(model_name, device=device)
    embeddings = _encode(model, [document.page_content for document in documents])
    return DenseRetriever(documents, embeddings, model)


# 모듈 수준 ``search``를 여러 분석 노드가 공유할 수 있도록 기본 인덱스를
# 프로세스당 한 번 보관한다. 잠금은 병렬 노드가 동시에 첫 검색을 시작해도
# 같은 인덱스를 중복 생성하지 않게 한다.
_default_retriever: DenseRetriever | None = None
_default_lock = threading.Lock()


def initialize(
    catalog_path: str | Path = DEFAULT_CATALOG_PATH,
    *,
    repository_root: str | Path | None = None,
    embedding_model: DenseEmbeddingModel | None = None,
    model_name: str = DEFAULT_MODEL_NAME,
    device: str | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> DenseRetriever:
    """인덱스를 즉시 만들고 모듈 공용 ``search``가 사용하도록 등록한다."""

    retriever = build_index(
        catalog_path,
        repository_root=repository_root,
        embedding_model=embedding_model,
        model_name=model_name,
        device=device,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    global _default_retriever
    with _default_lock:
        _default_retriever = retriever
    return retriever


def clear_index() -> None:
    """테스트 또는 자료 갱신 후 재구축을 위해 공용 인덱스를 비운다."""

    global _default_retriever
    with _default_lock:
        _default_retriever = None


def _get_default_retriever() -> DenseRetriever:
    """공용 인덱스가 없으면 기본 catalog로 한 번만 지연 생성한다."""

    global _default_retriever
    if _default_retriever is None:
        with _default_lock:
            if _default_retriever is None:
                _default_retriever = build_index()
    return _default_retriever


def search(company: str, query: str, k: int = 4) -> list[Document]:
    """한 기업의 상위 ``k``개 청크를 출처 metadata와 함께 반환한다.

    개발 B가 직접 호출하는 팀 고정 인터페이스이므로 인자 순서와 기본값을
    변경하지 않는다.
    """

    return _get_default_retriever().search(company, query, k)
