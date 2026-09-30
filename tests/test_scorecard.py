import pytest

from scorecard import SCORECARD, calculate_scorecard


def empty_grades():
    return {
        criterion: None
        for criteria in SCORECARD.values()
        for criterion in criteria
    }


def test_rubric_has_21_items_worth_100_points():
    assert len(empty_grades()) == 21
    assert {category: sum(items.values()) for category, items in SCORECARD.items()} == {
        "창업자·핵심팀": 20,
        "시장성": 25,
        "제품·기술력": 15,
        "경쟁우위": 10,
        "실적": 20,
        "투자조건": 10,
    }


def test_unknown_is_not_zero_or_rescaled():
    grades = empty_grades()
    grades["대기업 실제 지분·CVC 투자"] = 4
    grades["대기업 계약·납품 단계"] = 0

    result = calculate_scorecard(grades)

    assert result["earned_score"] == 4
    assert result["scorable_weight"] == 10
    assert result["unknown_weight"] == 90
    assert len(result["unknown_items"]) == 19
    assert result["items"]["대기업 실제 지분·CVC 투자"]["score"] == 4
    assert result["items"]["대기업 계약·납품 단계"]["score"] == 0


def test_partial_grade_uses_item_weight():
    grades = empty_grades()
    grades["유상 매출·납품"] = 3

    result = calculate_scorecard(grades)

    assert result["earned_score"] == 3.75
    assert result["scorable_weight"] == 5
    assert result["unknown_weight"] == 95


@pytest.mark.parametrize("invalid_grade", [-1, 5, 2.5, True, "U"])
def test_rejects_invalid_grades(invalid_grade):
    grades = empty_grades()
    grades["유상 매출·납품"] = invalid_grade

    with pytest.raises(ValueError, match="유상 매출·납품"):
        calculate_scorecard(grades)


def test_rejects_missing_or_unknown_criteria():
    grades = empty_grades()
    grades.pop("유상 매출·납품")
    with pytest.raises(ValueError, match="유상 매출·납품"):
        calculate_scorecard(grades)

    grades["유상 매출·납품"] = None
    grades["없는 항목"] = 4
    with pytest.raises(ValueError, match="없는 항목"):
        calculate_scorecard(grades)
