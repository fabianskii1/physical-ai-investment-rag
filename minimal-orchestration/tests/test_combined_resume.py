"""저장 분석 재사용은 이미 교정한 본문을 유지하고 고정 표·REFERENCE를 보호한다."""

from copy import deepcopy
from hashlib import sha256
import json

import pytest

from combined_report import build_combined_report, save_combined_report
from test_combined_report import portfolio, records
from test_report_synthesis import inputs_and_synthesis


def corrected_report(records):
    _, _, synthesis = inputs_and_synthesis(records)
    original = build_combined_report(records, portfolio_decision=portfolio(), report_synthesis=synthesis)['markdown']
    corrected = original.replace('개발과 실제 납품의 차이를 구별한다.',
                                 '현재 자료는 제조 현장용 제품의 개발 단계만 보여 주며 실제 납품으로 확대하지 않는다.')
    assert corrected != original
    return synthesis, original, corrected


def test_quality_first_input_keeps_saved_correction_instead_of_original_synthesis(records, tmp_path):
    synthesis, original, corrected = corrected_report(records)
    received = []

    def evaluate(payload):
        received.append(deepcopy(payload))
        return {'passed': True, 'issues': [], 'method': 'hybrid'}

    result = save_combined_report(records, tmp_path / 'resumed', portfolio_decision=portfolio(),
                                  report_synthesis=synthesis, initial_report=corrected, quality_evaluator=evaluate)
    assert len(received) == 1
    assert received[0]['report'] == corrected and received[0]['report'] != original
    assert result['markdown'].read_text(encoding='utf-8') == corrected
    assert result['rewrite_count'] == 0


def test_modified_fixed_table_is_rejected_before_quality_judge(records, tmp_path):
    synthesis, _, corrected = corrected_report(records)
    changed = corrected.replace('| 평가 영역 |', '| 변조된 평가 영역 |', 1)
    assert changed != corrected
    with pytest.raises(ValueError, match='점수표'):
        save_combined_report(records, tmp_path / 'rejected', portfolio_decision=portfolio(),
            report_synthesis=synthesis, initial_report=changed,
            quality_evaluator=lambda _: pytest.fail('고정 점수표 검증 전에 Judge 호출 금지'))
    assert not (tmp_path / 'rejected').exists()


def test_modified_reference_is_rejected_before_quality_judge(records, tmp_path):
    synthesis, _, corrected = corrected_report(records)
    changed = corrected.replace('https://example.test/MM', 'https://example.test/changed', 1)
    assert changed != corrected
    with pytest.raises(ValueError, match='원문 목록'):
        save_combined_report(records, tmp_path / 'rejected', portfolio_decision=portfolio(),
            report_synthesis=synthesis, initial_report=changed,
            quality_evaluator=lambda _: pytest.fail('REFERENCE 검증 전에 Judge 호출 금지'))
    assert not (tmp_path / 'rejected').exists()


def test_snapshot_hash_matches_final_accepted_markdown_after_resumed_rewrite(records, tmp_path):
    synthesis, original, corrected = corrected_report(records)
    final = corrected.replace('현재 자료는 제조 현장용 제품의 개발 단계만',
                              '검증된 원문은 제조 현장용 제품의 개발 단계만')
    assert final != corrected
    received = []

    def evaluate(payload):
        received.append(payload['report'])
        passed = len(received) == 2
        return {'passed': passed, 'issues': [] if passed else ['출처 표현을 명확히 교정'],
                'action': 'rewrite', 'method': 'hybrid'}

    result = save_combined_report(records, tmp_path / 'resumed', portfolio_decision=portfolio(),
        report_synthesis=synthesis, initial_report=corrected, quality_evaluator=evaluate,
        quality_reviser=lambda payload, evaluation: final)
    assert received == [corrected, final]
    assert result['rewrite_count'] == 1 and result['status'] == 'completed'
    saved = result['markdown'].read_bytes()
    metadata = json.loads(result['json'].read_text(encoding='utf-8'))
    assert saved == final.encode('utf-8')
    assert metadata['markdown_sha256'] == sha256(saved).hexdigest()
    assert metadata['markdown_sha256'] not in {sha256(original.encode()).hexdigest(), sha256(corrected.encode()).hexdigest()}
