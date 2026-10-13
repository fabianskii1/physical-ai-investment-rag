"""고정된 투자 평가표의 점수만 계산한다. 등급 판정은 호출자가 맡는다."""

from collections.abc import Mapping


SCORECARD: dict[str, dict[str, int]] = {
    "창업자·핵심팀": {
        "관련 전문성": 6,
        "제품화·사업 실행력": 6,
        "팀 구성·운영 역량": 5,
        "소통의 명확성과 신뢰성": 3,
    },
    "시장성": {
        "실제 접근 가능한 시장": 8,
        "문제의 심각성과 구매 필요성": 7,
        "시장 성장 동력": 5,
        "시장 진입·확장 가능성": 5,
    },
    "제품·기술력": {
        "핵심 작업 성능": 6,
        "현장 안정성·안전성": 5,
        "양산·유지보수 가능성": 4,
    },
    "경쟁우위": {
        "경쟁 제품 대비 고객 가치": 4,
        "모방하기 어려운 자산": 3,
        "고객 유지·전환 장벽": 3,
    },
    "실적": {
        "대기업 실제 지분·CVC 투자": 4,
        "유상 매출·납품": 5,
        "대기업 계약·납품 단계": 6,
        "고객 재구매·확대": 5,
    },
    "투자조건": {
        "기업가치의 합리성": 4,
        "지분·권리 조건": 3,
        "투자금 사용·다음 목표": 3,
    },
}


def calculate_scorecard(grades: Mapping[str, int | None]) -> dict:
    """21개 등급(0~4 또는 미확인 None)을 원래 100점 배점으로 환산한다."""
    criteria = {
        name: (category, weight)
        for category, category_items in SCORECARD.items()
        for name, weight in category_items.items()
    }
    missing = criteria.keys() - grades.keys()
    extra = grades.keys() - criteria.keys()
    if missing or extra:
        raise ValueError(f"평가 항목 불일치: missing={sorted(missing)}, extra={sorted(extra)}")

    items = {}
    earned_score = 0.0
    scorable_weight = 0
    unknown_items = []
    for name, (category, weight) in criteria.items():
        grade = grades[name]
        if grade is None:
            score = None
            unknown_items.append(name)
        else:
            if isinstance(grade, bool) or not isinstance(grade, int) or not 0 <= grade <= 4:
                raise ValueError(f"{name}: 등급은 0~4 또는 None이어야 합니다")
            score = weight * grade / 4
            earned_score += score
            scorable_weight += weight
        items[name] = {
            "category": category,
            "weight": weight,
            "grade": grade,
            "score": score,
        }

    return {
        "items": items,
        "earned_score": earned_score,
        "scorable_weight": scorable_weight,
        "unknown_weight": 100 - scorable_weight,
        "unknown_items": unknown_items,
    }
