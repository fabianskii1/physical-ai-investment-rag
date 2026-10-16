"""Dense top-k를 항목별 카탈로그 핵심 페이지의 실제 PDF 본문으로 보완한다."""
from functools import lru_cache
import re

from langchain_core.documents import Document
from criteria_registry import CRITERIA_REGISTRY, normalize_criterion
from rag.PDF.pdf_loader import load_pdf, resolve_pdf_source_path

MAX_TASK_CONTEXT_CHARS = 120_000


def key_page_numbers(value, page_count):
    result = set()
    for part in re.split(r'[;,]', str(value or '')):
        match = re.fullmatch(r'\s*(\d+)(?:\s*-\s*(\d+))?\s*', part)
        if match:
            start, end = int(match[1]), int(match[2] or match[1])
            result.update(range(max(1, start), min(page_count, end) + 1))
    return sorted(result)


@lru_cache(maxsize=128)
def _read_pages(location, doc_id, company, size, modified):
    return tuple(load_pdf(location, doc_id=doc_id, company=company))


class TaskEvidenceCollector:
    def __init__(self, company, task, criteria, catalog, search, *, full_company_review=False):
        self.company, self.task, self.criteria = company, task, criteria
        self.catalog, self.search = catalog, search
        self.audit = {'queries': [], 'selected_pages': [], 'omitted_pages': [], 'context_characters': 0}
        self.expected = {c: set() for c in criteria}
        self.presented = set()
        self.full_company_review = full_company_review

    def collect(self, scope, k):
        questions = [f"{self.company} {CRITERIA_REGISTRY[c]['label']}: {CRITERIA_REGISTRY[c]['question']}"
                     for c in self.criteria] or [self.task['query']]
        if self.criteria and self.task['query'] not in questions:
            questions.append(self.task['query'])
        dense = []
        for query in questions:
            found = self.search(scope, query, k)
            self.audit['queries'].append({'scope': scope, 'query': query,
                'hits': [{'doc_id': d.metadata.get('doc_id'), 'page': d.metadata.get('page')} for d in found]})
            dense.extend(found)
        anchors = []
        labels = {CRITERIA_REGISTRY[c]['label'] for c in self.criteria}
        for doc_id, metadata in self.catalog.items():
            if metadata.get('company') != scope:
                continue
            related = {s.strip() for s in metadata.get('related_scorecard_items', '').split('|')}
            matched = labels.intersection(related)
            # 매핑이 없는 항목은 회사 자료의 핵심 페이지 전체를 검토한다.
            unmapped = {c for c in self.criteria if not any(
                m.get('company') == scope and CRITERIA_REGISTRY[c]['label'] in
                {v.strip() for v in m.get('related_scorecard_items', '').split('|')}
                for m in self.catalog.values())} if scope == self.company else set()
            ids = (set(self.criteria) if self.full_company_review and scope == self.company else
                   {c for c in self.criteria if CRITERIA_REGISTRY[c]['label'] in matched} | unmapped)
            if not ids:
                continue
            location = metadata.get('source_path') or metadata.get('file_path')
            if not location:
                continue
            path = resolve_pdf_source_path(location)
            version = path.stat()
            pages = _read_pages(str(path), doc_id, scope, version.st_size, version.st_mtime_ns)
            page_numbers = (range(1, len(pages) + 1) if self.full_company_review and scope == self.company
                            else key_page_numbers(metadata.get('key_pages'), len(pages)))
            for page in page_numbers:
                for criterion in ids:
                    self.expected[criterion].add((scope, doc_id, page))
                source = pages[page - 1]
                anchors.append(Document(page_content=source.page_content, metadata={**metadata,
                    'doc_id': doc_id, 'page': page, 'chunk_index': 'catalog-page',
                    'retrieval_method': 'catalog_key_page', 'matched_criteria': sorted(ids)}))
        result, full_pages, chunks = [], set(), set()
        used = self.audit['context_characters']
        for doc in [*anchors, *dense]:
            identity = (doc.metadata.get('doc_id'), doc.metadata.get('page'))
            full = doc.metadata.get('retrieval_method') == 'catalog_key_page'
            chunk = (*identity, doc.metadata.get('chunk_index'), doc.page_content)
            if identity in full_pages or chunk in chunks:
                continue
            info = {'scope': scope, 'doc_id': identity[0], 'page': identity[1],
                    'method': 'catalog_key_page' if full else 'dense', 'characters': len(doc.page_content)}
            if used + len(doc.page_content) > MAX_TASK_CONTEXT_CHARS:
                self.audit['omitted_pages'].append(info)
                continue
            result.append(doc)
            chunks.add(chunk)
            if full:
                full_pages.add(identity)
                self.presented.add((scope, *identity))
            used += len(doc.page_content)
            self.audit['selected_pages'].append(info)
        self.audit['context_characters'] = used
        return result

    def reviewed_criteria(self, claims):
        result = {}
        for criterion in self.criteria:
            relevant = [c for c in claims if normalize_criterion(c.get('criterion_id')) == criterion]
            expected = self.expected[criterion]
            complete = bool(expected) and expected <= self.presented and bool(relevant)
            result[criterion] = {'source_review_complete': complete,
                'expected_pages': len(expected), 'presented_pages': len(expected & self.presented),
                'explicit_outcome': bool(relevant),
                'only_unverified': bool(relevant) and all(c.get('status') == 'unverified' for c in relevant),
                'scope': 'registered_catalog_key_pages'}
        return result
