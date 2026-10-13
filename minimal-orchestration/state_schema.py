"""기존 RAG를 오케스트레이션으로 연결하는 최소 공유 상태.

이 단계에서는 주장·점수·보고서별로 세부 타입을 모두 새로 만들지 않는다.
기존 RAG 함수가 반환하는 사전을 research_result와 investment_result에 담아
재사용하고, 실제로 흐름 제어에 필요한 항목만 독립된 필드로 둔다.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, NotRequired, TypedDict


WorkerRole = Literal["bigtech", "technology", "market"]


class ResearchTask(TypedDict):
    """오케스트레이터가 워커에게 보내는 구조화된 작업 하나."""

    # 같은 라운드 안에서 중복되지 않는 작업 ID. 예: technology-1.
    id: str
    # 기존 RAG 분석 함수 중 어떤 전문 워커를 사용할지 결정한다.
    worker: WorkerRole
    # 기존 고정 검색 질문을 교체할 조사 질문. 재계획 시 부족한 근거를 구체적으로 적는다.
    query: str
    # 이번 작업이 조사할 평가항목의 영문 ID 목록. 한글 배점표 이름도 입력 가능하지만
    # 검증 후 영문 ID로 정규화한다. 예: ["core_task_performance", "field_reliability_safety"].
    # 재검토에서는 부족한 항목만 지정하여 이미 성공한 근거를 다시 버리지 않는다.
    criteria: NotRequired[list[str]]
    # com_data에서 등록된 원문 링크 중 선택한 최대 3개. 생략하면 기존 PDF만 검색한다.
    # 워커가 읽은 본문은 PDF로 보관되어 doc_id와 스냅샷 PDF 페이지로 인용한다.
    urls: NotRequired[list[str]]


def merge_worker_results(left: dict, right: dict) -> dict:
    """병렬 워커의 반환값을 서로 덮어쓰지 않도록 결과 ID로 합친다.

    키는 '재검토 횟수:작업 ID'이다. 예: 0:technology-1, 1:technology-1.
    재검토에서도 이전 결과를 보존하되, 신시사이저는 현재 tasks에 해당하는
    현재 라운드 결과만 새 결과로 취급한다. 같은 키에 다른 결과가 오면 오류다.
    """
    merged = dict(left)
    for key, value in right.items():
        if key in merged and merged[key] != value:
            raise ValueError(f"동일한 워커 결과 ID에 다른 내용이 저장됨: {key}")
        merged[key] = value
    return merged


class AgentState(TypedDict):
    # 실행 입력. 최소 name을 포함하며 domain, sw_hw, 선정 이유를 추가할 수 있다.
    company_info: dict[str, Any]
    # 문서 ID → 원본 경로·페이지 수 등의 메타데이터. 기존 인용 검증에 사용한다.
    document_catalog: dict[str, dict[str, Any]]
    # 투자 판단 기준. 조사 품질 통과 여부와 투자 점수는 서로 구분한다.
    decision_policy: dict[str, Any]
    # 사람이 직접 제공한 현재 거래의 투자조건. 기업가치·지분권리·자금 사용 등을
    # 담는다. 과거 투자 유치 뉴스는 이번 거래의 조건을 대신할 수 없다.
    # None이면 투자조건 3항목은 U이며, 입력해도 원문 검증을 통과해야 점수를 매긴다.
    investment_terms: dict[str, Any] | None

    # 현재 라운드의 구조화 계획. 필요한 워커만 선택하므로 0~3개가 될 수 있다.
    tasks: list[ResearchTask]
    # 병렬 실행 결과. 각 값에는 worker, task, analysis 또는 error가 들어간다.
    # 워커는 이 필드에만 결과를 추가하며 오케스트레이터로 직접 돌아가지 않는다.
    worker_results: Annotated[dict[str, dict[str, Any]], merge_worker_results]
    # 누적 역할별 분석과 근거 검증 결과를 묶는다. 기존 분석 키 3개 및
    # source_checked_claims, evidence_gaps, evidence_status를 그대로 재사용한다.
    # 계획 예외는 planning_error로 기록하여 신시사이저가 재검토를 결정한다.
    # report_revision_feedback에는 보고서 수정 요청을 보존해 재조사 후 Writer에
    # 전달한다. 다시 조사하지 않은 역할의 성공 결과도 다음 라운드에 보존한다.
    # criterion_attempts: 항목별 실제 검색을 수행한 라운드·작업·질문 기록.
    # coverage_by_criterion: 21항목 각각의 검색 여부·원문 검증 근거 E-ID·부족 사유.
    # missing_criteria: 재조사 가능한 18항목 중 원문 검증 근거가 부족한 항목 ID.
    # 투자조건 3항목 미입력은 manual_missing이며 자동 재검색 대상으로 넣지 않는다.
    # score_review_issues: 투자 Judge가 U로 판정한 조사 가능한 항목의 보완 요청.
    # last_verified_signature / last_verified_round / last_verified_task_ids:
    # 주장·원문 입력을 이미 검증했는지 확인하는 기록. 점수 검토·보고서 평가 또는
    # 새 조사 라운드에서도 실제 주장·원문이 같으면 검증 snapshot을 재사용한다.
    # 따라서 등급 판정에 사용한 주장·인용문과 보고서 E-ID가 중간에 바뀌지 않는다.
    research_result: dict[str, Any]
    # 기존 Scorecard로 계산한 score_details, total_score, 판정 등을 담는다.
    # 새 조사 라운드를 시작하면 비워 과거 점수가 현재 근거와 섞이지 않게 한다.
    investment_result: dict[str, Any]

    # 신시사이저가 정리한 미달 이유. 오케스트레이터는 이를 다음 작업에 반영한다.
    # 워커 오류와 미확인 근거도 이곳에서 합치며 워커가 직접 계획을 바꾸지 않는다.
    feedback: list[str]
    # 최초 조사=0. 신시사이저가 추가 조사로 돌려보낼 때만 1씩 증가한다.
    recheck_count: int
    # 추가 조사 허용 횟수. 3~5로 설정하며 기본은 4(최초 포함 최대 5라운드).
    # 소진 시 불확실한 항목을 U로 남긴 보고서를 만들고 부분 완료로 끝낸다.
    max_rechecks: int
    # 재조사와 별개의 보고서 재작성 예산. 표현·중립성 오류는 같은 근거와 점수로
    # Writer를 다시 실행하며, 기본 두 번까지만 수정한다. 이전 저장 State는 기본값 적용.
    report_rewrite_count: NotRequired[int]
    max_report_rewrites: NotRequired[int]
    # 서로 다른 조사 라운드에서 연속으로 새 검증 근거·지원 항목·조사 항목이
    # 늘지 않은 횟수. 같은 라운드의 Score Review 콜백은 중복 집계하지 않는다.
    no_progress_rounds: NotRequired[int]
    last_progress_round: NotRequired[int]
    # 빈 문자열 또는 no_progress / research_budget_exhausted / report_resume_only. 큰 실행 이력 대신
    # 짧은 종료 이유를 저장하고 미확인 등급은 U로 유지한다.
    research_stop_reason: NotRequired[str]

    # 최종 또는 현재 보고서 Markdown. A4 PDF 렌더러는 이 문자열을 입력으로 받는다.
    final_report: str
    # 보고서 생성 '후' 수행한 근거성·중립성·편향 통제 평가 결과.
    # action=rewrite이면 근거·점수를 보존하고 Writer로, research이면 S로 반환한다.
    # action 미지정/알 수 없는 값은 안전하게 research로 취급한다.
    # 새 조사 시작 시 이전 평가를 비운다.
    report_evaluation: dict[str, Any]

    # 실행 식별자. 체크포인트 thread_id와 LangSmith 추적 연결에 사용할 수 있다.
    trace_id: str
    # 실행된 노드 수. 병렬 워커도 각각 1을 더하므로 합산 reducer를 사용한다.
    step_count: Annotated[int, operator.add]
    # 완료 상태. completed와 partial(근거 부족), quality_failed를 구분한다.
    status: Literal["researching", "partial", "completed", "quality_failed"]
    # 다음 경로를 명시한다. 조사 재계획은 신시사이저만 replan으로 결정한다.
    route: Literal["replan", "judge", "write", "end"]
