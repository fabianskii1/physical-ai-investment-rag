"""기존 21개 투자 평가항목과 조사 워커를 연결하는 공통 목록.

배점은 기존 scorecard.py에서 읽는다. 이 목록은 새 배점표가 아니라 검색 계획,
근거 검증, 항목별 재검토에서 같은 항목 ID를 사용하기 위한 연결 정보이다.
"""

from rag.scorecard import SCORECARD


_ITEMS = (
    ("founder_expertise", "관련 전문성", "technology", "창업자와 핵심 인력의 경력·전문성·실제 수행 역할"),
    ("productization_execution", "제품화·사업 실행력", "technology", "개발 결과를 제품·현장 도입으로 전환한 이력과 달성한 이정표"),
    ("team_operations", "팀 구성·운영 역량", "technology", "핵심 인력 구성, 제조·소프트웨어·사업 담당 역량과 운영 이력"),
    ("communication_reliability", "소통의 명확성과 신뢰성", "technology", "공식 설명의 명확성·일관성 및 목표와 실제 결과의 구분"),
    ("accessible_market", "실제 접근 가능한 시장", "market", "실제 고객·계약·실증으로 접근 가능한 고객군과 시장 범위"),
    ("customer_pain_purchase_need", "문제의 심각성과 구매 필요성", "market", "고객의 비용·인력·생산 문제와 유상 구매 필요성"),
    ("market_growth_drivers", "시장 성장 동력", "market", "산업 수요, 인력 부족, 기술 변화와 검증 가능한 시장 성장 지표"),
    ("market_entry_expansion", "시장 진입·확장 가능성", "market", "실제 시장 진입 경로, 고객 확보 및 산업·지역 확장 근거"),
    ("core_task_performance", "핵심 작업 성능", "technology", "파지·이동·삽입 등 핵심 작업의 측정된 성공률·속도·환경 조건"),
    ("field_reliability_safety", "현장 안정성·안전성", "technology", "반복 작업 신뢰성, 가동 시간, 장애·안전 검증과 실제 현장 조건"),
    ("mass_production_maintenance", "양산·유지보수 가능성", "technology", "실제 생산 이력, 제조 확장, 비용·정비·유지보수 체계"),
    ("customer_value_vs_competitors", "경쟁 제품 대비 고객 가치", "market", "기존 방식·경쟁 제품과 비교한 고객 비용·성능·도입 효과"),
    ("hard_to_copy_assets", "모방하기 어려운 자산", "technology", "독자 기술·특허·데이터·노하우 및 모방 장벽을 뒷받침하는 사실"),
    ("customer_retention_switching_barrier", "고객 유지·전환 장벽", "market", "고객 유지, 시스템 통합, 전환 비용과 지속 사용 근거"),
    ("bigtech_equity_investment", "대기업 실제 지분·CVC 투자", "bigtech", "대기업·CVC의 실제 지분 투자 여부와 투자 주체·거래 근거"),
    ("paid_sales_delivery", "유상 매출·납품", "bigtech", "유상 매출, 고객 지급, 상용 계약·납품의 실제 실적"),
    ("bigtech_contract_delivery_stage", "대기업 계약·납품 단계", "bigtech", "MOU·실증·유상 납품·유상 확대를 구분할 원문 근거"),
    ("customer_repurchase_expansion", "고객 재구매·확대", "bigtech", "기존 고객의 유상 재구매·계약 갱신·납품 확대"),
    ("valuation_reasonableness", "기업가치의 합리성", None, "사람이 입력한 이번 거래의 기업가치와 산정 근거"),
    ("equity_rights", "지분·권리 조건", None, "사람이 입력한 이번 거래의 지분·주주 권리·계약 조건"),
    ("funds_use_milestones", "투자금 사용·다음 목표", None, "사람이 입력한 이번 투자금 사용 계획과 다음 달성 목표"),
)

_WEIGHTS = {label: (category, weight) for category, items in SCORECARD.items()
            for label, weight in items.items()}
CRITERIA_REGISTRY = {
    criterion_id: {"id": criterion_id, "label": label, "worker": worker,
                   "question": question, "category": _WEIGHTS[label][0],
                   "weight": _WEIGHTS[label][1], "researchable": worker is not None}
    for criterion_id, label, worker, question in _ITEMS
}
LABEL_TO_ID = {item["label"]: key for key, item in CRITERIA_REGISTRY.items()}
RESEARCH_CRITERIA = tuple(key for key, item in CRITERIA_REGISTRY.items() if item["researchable"])
MANUAL_CRITERIA = tuple(key for key, item in CRITERIA_REGISTRY.items() if not item["researchable"])


def normalize_criterion(value: object) -> str | None:
    """기존 영문 주장 ID와 한글 배점표 항목을 하나의 영문 ID로 정규화한다."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value in CRITERIA_REGISTRY:
        return value
    return LABEL_TO_ID.get(value)


def criteria_for_worker(worker: str) -> list[str]:
    """해당 워커가 기본으로 조사할 항목 ID 목록을 반환한다."""
    return [key for key, item in CRITERIA_REGISTRY.items() if item["worker"] == worker]


def registry_payload(researchable_only: bool = False) -> list[dict]:
    """모델 프롬프트에 넣을 항목·배점·담당자·보완 질문 목록."""
    return [dict(item) for item in CRITERIA_REGISTRY.values()
            if not researchable_only or item["researchable"]]
