"""PDF 임베딩의 디스크 재사용·무결성·기업별 검색을 실제 파일로 검증한다.

테스트 임베더만 사용하므로 모델 다운로드와 외부 API 요청은 발생하지 않는다.
"""

import csv
import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from reportlab.pdfgen.canvas import Canvas

from rag import retrieval


class FakeEncoder:
    cache_key = "test-weights-v1"

    def __init__(self):
        self.calls = []

    def encode(self, texts, **kwargs):
        self.calls.append(list(texts))
        # 서로 다른 회사가 같은 단어를 사용해도 company 후보 필터를 검증한다.
        return np.array([[text.lower().count("robot") + 1.0,
                          text.lower().count("customer") + 1.0, 1.0] for text in texts], dtype=np.float32)


class SlowEncoder(FakeEncoder):
    """동일 임베더의 실제 동시 진입 수를 측정하는 테스트용 모델."""

    def __init__(self):
        super().__init__()
        self.active = 0
        self.peak_active = 0
        self._tracking_lock = threading.Lock()

    def encode(self, texts, **kwargs):
        with self._tracking_lock:
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
        try:
            # 잠금이 없으면 동시에 시작한 다른 검색도 encode에 진입할 시간을 준다.
            time.sleep(0.02)
            return super().encode(texts, **kwargs)
        finally:
            with self._tracking_lock:
                self.active -= 1


def make_pdf(path: Path, text: str):
    canvas = Canvas(str(path))
    canvas.drawString(35, 780, text)
    canvas.save()


@pytest.fixture
def corpus(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    make_pdf(data / "alpha.pdf", "Alpha robot factory customer delivery")
    make_pdf(data / "beta.pdf", "Beta robot customer recurring customer orders")
    catalog = data / "catalog.csv"
    with catalog.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=retrieval.CATALOG_FIELDS)
        writer.writeheader()
        for doc_id, company, file_name in [("AL-01", "ALPHA", "alpha.pdf"), ("BE-01", "BETA", "beta.pdf")]:
            writer.writerow({
                "doc_id": doc_id, "company": company, "category": "technology",
                "title": f"{company} official report", "publisher": company,
                "published_at": "2026-10-01", "source_url": f"https://example.com/{file_name}",
                "file_path": f"data/{file_name}", "page_count": 1,
                "key_pages": "1", "related_scorecard_items": "robot performance", "notes": "source",
            })
    return catalog, tmp_path / "index"


def build(corpus, encoder, **kwargs):
    catalog, cache = corpus
    return retrieval.build_index(catalog, embedding_model=encoder,
                                 model_name="test-dense-model", cache_dir=cache, **kwargs)


def cache_payload(index):
    path = Path(index.index_info["cache_path"]) / "index.json"
    return path, json.loads(path.read_text())


def rewrite_payload(path, payload):
    payload.pop("content_sha256", None)
    payload["content_sha256"] = hashlib.sha256(retrieval._json_bytes(payload)).hexdigest()
    path.write_bytes(retrieval._json_bytes(payload))


def test_restart_loads_saved_embeddings_without_loading_pdf_or_document_encode(corpus):
    first_encoder = FakeEncoder()
    first = build(corpus, first_encoder)
    assert len(first_encoder.calls) == 1
    assert first.index_info["cache_hit"] is False
    assert first.index_info["pages"] == 2
    assert first.index_info["chunks"] == 2
    payload_path, payload = cache_payload(first)
    assert payload_path.is_file()
    assert (payload_path.parent / payload["vector_file"]).is_file()
    second_encoder = FakeEncoder()
    with patch.object(retrieval, "load_pdf", side_effect=AssertionError("캐시를 읽을 때 PDF를 재추출하면 안 됨")):
        second = build(corpus, second_encoder)
    assert second_encoder.calls == []
    assert second.index_info["cache_hit"] is True
    assert second.index_info["cache_status"] == "loaded"
    results = second.search("alpha", "robot customer", k=10)
    assert len(second_encoder.calls) == 1  # 검색 질의만 새로 임베딩한다.
    assert {item.metadata["company"] for item in results} == {"ALPHA"}
    assert results[0].metadata["doc_id"] == "AL-01"
    assert results[0].metadata["page"] == 1
    assert results[0].metadata["publisher"] == "ALPHA"
    assert isinstance(results[0].metadata["retrieval_score"], float)


@pytest.mark.parametrize("change", ["pdf_content", "model", "chunk_size", "chunk_overlap", "model_weights", "catalog_metadata"])
def test_source_or_model_or_chunk_changes_invalidate_cache(corpus, change):
    first = build(corpus, FakeEncoder())
    encoder = FakeEncoder()
    kwargs = {}
    if change == "pdf_content":
        make_pdf(corpus[0].parent / "alpha.pdf", "Alpha robot factory changed measured performance")
    elif change == "model":
        kwargs["model_name"] = "test-other-model"
    elif change == "chunk_size":
        kwargs["chunk_size"] = 999
    elif change == "chunk_overlap":
        kwargs["chunk_overlap"] = 149
    elif change == "model_weights":
        encoder.cache_key = "test-weights-v2"
    else:
        corpus[0].write_text(corpus[0].read_text().replace("ALPHA official report", "ALPHA amended report"))
    catalog, cache = corpus
    rebuilt = retrieval.build_index(catalog, embedding_model=encoder, cache_dir=cache,
                                   model_name=kwargs.pop("model_name", "test-dense-model"), **kwargs)
    assert rebuilt.index_info["cache_hit"] is False
    assert rebuilt.index_info["cache_path"] != first.index_info["cache_path"]
    assert len(encoder.calls) == 1


@pytest.mark.parametrize("damage", ["json_bytes", "vector_bytes", "count", "nonfinite", "metadata"])
def test_corrupt_cache_rebuilds_once_and_never_returns_invalid_index(corpus, damage):
    first = build(corpus, FakeEncoder())
    payload_path, payload = cache_payload(first)
    vector_path = payload_path.parent / payload["vector_file"]
    if damage == "json_bytes":
        payload_path.write_text("{broken")
    elif damage == "vector_bytes":
        vector_path.write_bytes(b"not-an-npz")
    elif damage in {"count", "nonfinite"}:
        # 체크섬도 재계산해 다음 단계의 차원·유한값 검사까지 확인한다.
        matrix = np.array([[1.0, 0.0, 1.0]], dtype=np.float32) if damage == "count" else np.full((2, 3), np.nan, dtype=np.float32)
        np.savez_compressed(vector_path, embeddings=matrix)
        payload["vectors_sha256"] = retrieval._sha256_file(vector_path)
        payload["vector_shape"] = list(matrix.shape)
        rewrite_payload(payload_path, payload)
    else:
        payload["documents"][0]["metadata"]["company"] = "BETA"
        rewrite_payload(payload_path, payload)
    encoder = FakeEncoder()
    rebuilt = build(corpus, encoder)
    assert rebuilt.index_info["cache_status"] == "rebuilt_invalid"
    assert rebuilt.index_info["cache_hit"] is False
    assert len(encoder.calls) == 1
    assert {item.metadata["company"] for item in rebuilt.search("ALPHA", "robot")} == {"ALPHA"}
    final = build(corpus, FakeEncoder())
    assert final.index_info["cache_hit"] is True


def test_initialize_preserves_public_search_and_environment_cache_dir(corpus, monkeypatch):
    catalog, cache = corpus
    monkeypatch.setenv("RAG_INDEX_DIR", str(cache))
    retrieval.clear_index()
    try:
        retriever = retrieval.initialize(catalog, embedding_model=FakeEncoder(), model_name="test-dense-model")
        assert Path(retriever.index_info["cache_path"]).parent == cache
        assert retrieval.get_index_info()["chunks"] == 2
        assert {doc.metadata["company"] for doc in retrieval.search("BETA", "robot", 4)} == {"BETA"}
        copied = retrieval.get_index_info()
        copied["chunks"] = 999
        assert retrieval.get_index_info()["chunks"] == 2
    finally:
        retrieval.clear_index()


def test_query_dimension_mismatch_is_not_hidden_after_cache_load(corpus):
    build(corpus, FakeEncoder())
    encoder = FakeEncoder()
    cached = build(corpus, encoder)
    encoder.encode = lambda texts, **kwargs: np.ones((len(texts), 4), dtype=np.float32)
    with pytest.raises(retrieval.IndexBuildError, match="dimension"):
        cached.search("ALPHA", "robot")


def test_failed_json_publish_keeps_previous_valid_index(corpus):
    """JSON 교체 실패 시 이전 세대의 청크와 벡터가 계속 함께 읽힌다."""
    first = build(corpus, FakeEncoder())
    manifest, payload = cache_payload(first)
    previous_bytes = manifest.read_bytes()
    real_replace = retrieval.os.replace

    def fail_manifest_replace(source, target):
        if Path(target).name == "index.json":
            raise OSError("simulated disk error")
        return real_replace(source, target)

    with patch.object(retrieval.os, "replace", side_effect=fail_manifest_replace):
        with pytest.raises(retrieval.IndexBuildError, match="persist"):
            retrieval._write_cached_index(
                manifest.parent, first.index_info["fingerprint"], payload["settings"],
                first._documents, first._embeddings * 0.5,
            )
    assert manifest.read_bytes() == previous_bytes
    encoder = FakeEncoder()
    cached = build(corpus, encoder)
    assert cached.index_info["cache_hit"] is True
    assert encoder.calls == []


def test_pdf_change_during_embedding_does_not_publish_stale_fingerprint(corpus):
    """임베딩하는 동안 입력 PDF가 바뀌면 재시도할 수 있게 명시적으로 중단한다."""
    encoder = FakeEncoder()
    original_encode = encoder.encode

    def change_source_during_encode(texts, **kwargs):
        result = original_encode(texts, **kwargs)
        make_pdf(corpus[0].parent / "alpha.pdf", "source changed during embeddings")
        return result

    encoder.encode = change_source_during_encode
    with pytest.raises(retrieval.IndexBuildError, match="changed while building"):
        build(corpus, encoder)
    assert not list(corpus[1].rglob("index.json"))


def test_parallel_workers_serialize_only_shared_model_query_encoding(corpus):
    """여러 기업 검색이 동시에 시작돼도 공유 모델의 encode 진입은 하나씩이다."""
    encoder = SlowEncoder()
    retriever = build(corpus, encoder)
    encoder.calls.clear()
    encoder.peak_active = 0
    workers = 4
    ready = threading.Barrier(workers)

    def concurrent_search(index):
        company = "ALPHA" if index % 2 == 0 else "BETA"
        ready.wait(timeout=5)
        result = retriever.search(company, f"robot customer query {index}")
        return company, result

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(concurrent_search, range(workers)))
    assert encoder.peak_active == 1
    assert encoder.active == 0
    assert len(encoder.calls) == workers
    assert all(len(texts) == 1 for texts in encoder.calls)
    assert all({doc.metadata["company"] for doc in docs} == {company}
               for company, docs in results)
