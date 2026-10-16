"""명백한 주장 확대는 차단하되 자료 미확인 설명을 사실 부재로 읽지 않는다."""

import pytest

from report_language import report_evidence_scope_issues, report_language_issues


@pytest.mark.parametrize(('text', 'issue'), [
    ('현재 실적은 모두 부재하여 투자할 수 없다.', '미확인 실적을 실제 부재로 단정'),
    ('팀의 역량이 부족하다.', '자료 부족을 인력 역량·경험 부족으로 단정'),
    ('경쟁사 대비 우위에 있다.', '비교 실험 없이 경쟁사에 대한 기술 우열 단정'),
    ('회사 발표로 성능이 독립적으로 입증되었다.', '회사 발표를 독립 입증으로 단정'),
    ('국제적 수준의 기술을 갖췄다.', '검증 범위 없는 국제적 기술 수준 단정'),
    ('제조·물류 현장 제로샷 성능이 증명되었다.', '가정 제로샷을 제조·물류 현장 성능으로 확대'),
    ('설계가 검증되었으므로 현장 성능은 충분하다.', '설계 공개를 제품 검증 완료로 확대'),
    ('원문 자료가 남아 있지 않아 재검증이 불가능하다.', '확인 자료 부족을 자료 소실로 표현'),
])
def test_clear_unsupported_assertion_is_blocked(text, issue):
    assert issue in report_language_issues(text)


@pytest.mark.parametrize('text', [
    '유상 매출 실적에 대한 자료가 부족하다. 현재 실제 부재 여부는 판단할 수 없다.',
    '현재 유상 매출과 고객 재구매는 확인되지 않음.',
    '팀의 직무 경험을 평가할 자료가 부족하다.',
    '회사가 기술을 발표했다. 독립 검증은 현재 확인되지 않음.',
    '독립 입증 자료가 부족하여 기술 수준은 판단 유보한다.',
    '경쟁사 대비 우위 여부는 비교 실험으로 확인해야 한다.',
])
def test_unverified_or_insufficient_material_is_allowed(text):
    assert report_language_issues(text) == []


def test_spaces_and_newlines_cannot_hide_an_unsupported_claim():
    issues = report_language_issues('팀의 역량이\n 부족하다. 회사 발표는 독립적으로\n 입증되었다.')
    assert issues == ['자료 부족을 인력 역량·경험 부족으로 단정', '회사 발표를 독립 입증으로 단정']


@pytest.mark.parametrize('text',[
    '독립 검증·비교 수치는 현재 미확인이다.',
    '독립 검증과 여러 고객 재현을 확인할 자료가 부족하다.',
    '독립 검증이 미흡하여 추가 확인이 필요하다.',
    '판단을 바꿀 자료는 현장 성능의 독립 검증, 안전·고장 기록, 투자조건이다.',
    '회사 발표는 구체적이지만 독립 검증 범위가 제한적이다.',
])
def test_explicit_independent_validation_limits_are_not_positive_assertions(text):
    assert not report_language_issues(text)


def test_completed_independent_validation_is_still_blocked():
    assert report_language_issues('회사 발표로 기술이 독립적으로 검증되었다.')


@pytest.mark.parametrize(('text', 'issue'), [
    ('유상 매출 자료가 부족하다. 회사 발표로 기술이 독립적으로 입증되었다.', '회사 발표를 독립 입증으로 단정'),
    ('독립 검증 자료가 부족하다. 경쟁사 대비 우위에 있다.', '비교 실험 없이 경쟁사에 대한 기술 우열 단정'),
    ('독립 입증 자료가 부족하지만 회사 발표로 성능이 독립적으로 입증되었다.', '회사 발표를 독립 입증으로 단정'),
])
def test_data_gap_elsewhere_does_not_exempt_an_asserted_unsupported_claim(text, issue):
    assert issue in report_language_issues(text)


def test_market_data_gap_in_same_paragraph_does_not_exempt_wrong_market_citations():
    report = ('공공 실증과 IFR 수요 자료는 강하나, ROBROS의 실제 진입 경로·고객 계약·확대 실적은 '
              '확인자료가 부족하다. [RB-E1] [RB-E2]')
    assert len(report_evidence_scope_issues(report, market_contexts())) == 2


def test_same_violation_is_reported_once_for_multiple_sentences():
    issues = report_language_issues('팀의 역량이 부족하다. 팀의 경험이 부족하다.')
    assert issues == ['자료 부족을 인력 역량·경험 부족으로 단정']


# 아래 검사는 RL·MOU 목적·산업 수요의 알려진 오류를 잡는 좁은 휴리스틱이다.
# 일반적인 의미 함의 또는 보고서 전체의 groundedness 판정을 대신하지 않는다.
def original_page(refs, text, *, company='ROBROS', title='회사 소개', doc_id='TEAM-1'):
    """실제 combined_source_contexts/report_source_contexts의 원문 입력 형태."""
    return {'company': company, 'doc_id': doc_id, 'page': 1, 'title': title,
            'publisher': company, 'source_url': 'https://example.org/original',
            'source_path': f'rag/data/raw/fixture/{doc_id}.pdf', 'evidence_ids': refs,
            'checked_claims': [{'id': ref, 'claim': text, 'evidence_quote': text,
                               'status': 'confirmed', 'source': {'doc_id': doc_id, 'page': 1}}
                              for ref in refs],
            'page_text': text}


TEAM_PAGE = '50명 이상의 팀이 하드웨어 설계부터 AI 지능 개발까지 휴머노이드 개발 전 과정을 자체 기술로 수행한다.'
MOU_PAGE = 'MOTOMIND와 LG사이언스파크는 MOU를 체결했다.'
RL_PAGE = '로브로스는 강화학습(Reinforcement Learning, RL) 기반 운동지능과 VLA 기반 지능을 자체 개발한다.'
DEMAND_PAGE = 'IFR은 제조업 인력 부족과 공급망 회복력 강화가 자동화 수요의 성장 동력이라고 발표했다.'
DEMO_PAGE = 'KIRIA 로봇공정모델 실증사업은 제조업 인력난 해소와 고강도·고위험 작업 대체를 지원한다.'
MARKET_ASSERTION = '확인 사실: IFR 보고서와 공공 실증사업은 제조업 인력난 해소와 고강도·고위험 작업 대체 수요를 뒷받침한다.'


@pytest.mark.parametrize('technology', ['강화학습', 'RL', 'reinforcement learning'])
def test_scope_rl_assertion_requires_support_from_actual_cited_original_page(technology):
    ref = 'RB-E1'
    report = f'## 기술\n확인 사실: 로브로스는 {technology} 기반 운동지능을 자체 개발한다. [{ref}]\n'
    assert report_evidence_scope_issues(report, [original_page([ref], TEAM_PAGE)])


def test_scope_checked_claim_or_source_title_cannot_substitute_for_original_page_text():
    source = original_page(['RB-E1'], TEAM_PAGE, title='강화학습 기반 운동지능')
    source['checked_claims'][0]['claim'] = RL_PAGE
    source['checked_claims'][0]['evidence_quote'] = RL_PAGE
    report = '확인 사실: 로브로스는 강화학습 기반 운동지능을 자체 개발한다. [RB-E1]'
    assert report_evidence_scope_issues(report, [source])


def test_scope_supported_rl_assertion_is_allowed():
    report = '확인 사실: 로브로스는 강화학습 기반 운동지능을 자체 개발한다고 회사가 발표했다. [RB-E1]'
    assert report_evidence_scope_issues(report, [original_page(['RB-E1'], RL_PAGE)]) == []


def test_scope_line_wrap_does_not_separate_a_paragraph_from_its_citation():
    report = '확인 사실: 로브로스는 강화학습 기반 운동지능을 자체 개발한다고\n회사에서 발표했다. [RB-E1]\n'
    assert report_evidence_scope_issues(report, [original_page(['RB-E1'], RL_PAGE)]) == []


@pytest.mark.parametrize('text', [
    '강화학습 적용은 현재 확인되지 않았다.',
    '후속 확인: RL 적용 여부와 학습 환경을 추가 실사로 확인한다.',
    '강화학습 적용을 사실로 단정할 수 없다.',
    '강화학습 적용에 대한 확인 자료가 부족하다.',
])
def test_scope_rl_unknown_or_follow_up_mention_is_allowed(text):
    assert report_evidence_scope_issues(f'{text} [RB-E1]', [original_page(['RB-E1'], TEAM_PAGE)]) == []


@pytest.mark.parametrize('tail', ['', '\n\n## 5 — REFERENCE\n- [RB-E9] 강화학습 원문, p.1\n'])
def test_scope_uncited_or_reference_only_rl_page_does_not_support_team_citation(tail):
    contexts = [original_page(['RB-E1'], TEAM_PAGE), original_page(['RB-E9'], RL_PAGE, doc_id='RL-1')]
    report = '확인 사실: 강화학습 기반 운동지능을 자체 개발한다. [RB-E1]' + tail
    assert report_evidence_scope_issues(report, contexts)


def test_scope_unknown_statement_does_not_exempt_separate_factual_rl_assertion():
    report = ('근거의 한계: 강화학습 적용은 미확인이다. [RB-E1]\n\n'
              '확인 사실: 로브로스는 강화학습 기반 운동지능을 자체 개발한다. [RB-E1]')
    assert report_evidence_scope_issues(report, [original_page(['RB-E1'], TEAM_PAGE)])


@pytest.mark.parametrize('assertion', [
    '확인 사실: MOTOMIND는 LG사이언스파크와 공동 연구 및 기술 교류를 위한 MOU를 체결했다.',
    '확인 사실: LG사이언스파크와 MOU 협력을 통해 공동 연구와 기술 교류 기반을 확보했다.',
])
def test_scope_mou_signature_alone_does_not_establish_research_or_exchange_purpose(assertion):
    source = original_page(['MM-E1'], MOU_PAGE, company='MOTOMIND', doc_id='MOU-1')
    assert report_evidence_scope_issues(f'{assertion} [MM-E1]', [source])


def test_scope_explicit_mou_research_exchange_agreement_can_support_its_purpose():
    source = original_page(['MM-E1'], 'MOTOMIND와 LG사이언스파크는 공동 연구와 기술 교류를 위한 MOU를 체결했다.',
                           company='MOTOMIND', doc_id='MOU-1')
    report = '확인 사실: MOTOMIND는 LG사이언스파크와 공동 연구 및 기술 교류를 위한 MOU를 체결했다. [MM-E1]'
    assert report_evidence_scope_issues(report, [source]) == []


@pytest.mark.parametrize('text', [
    'MOU의 공동 연구·기술 교류 목적은 현재 확인되지 않았다.',
    '후속 확인: MOU의 공동 연구·기술 교류 합의 여부는 추가 합의 원문으로 확인해야 한다.',
    '분석·해석 (추론): MOU는 향후 공동 연구·기술 교류로 이어질 가능성이 있으나 해당 목적·실행 합의는 미확인이다.',
])
def test_scope_mou_unverified_purpose_or_explicit_future_possibility_is_allowed(text):
    source = original_page(['MM-E1'], MOU_PAGE, company='MOTOMIND', doc_id='MOU-1')
    assert report_evidence_scope_issues(f'{text} [MM-E1]', [source]) == []


def market_contexts(prefix='RB-'):
    return [original_page([f'{prefix}E1', f'{prefix}E2'], TEAM_PAGE),
            original_page([f'{prefix}E3'], DEMO_PAGE, title='로봇공정모델 실증사업', doc_id='KIRIA-1'),
            original_page([f'{prefix}E4'], DEMAND_PAGE, title='IFR 산업 보고서', doc_id='IFR-1')]


def test_scope_market_demand_and_public_demo_cannot_be_cited_to_team_evidence():
    prefix = 'RB-'
    report = f'{MARKET_ASSERTION} [{prefix}E1] [{prefix}E2]'
    # context 목록에 실제 산업 원문이 있어도 본문의 인용 연결을 통과해야 한다.
    assert report_evidence_scope_issues(report, market_contexts(prefix))


def test_scope_actual_market_originals_linked_in_the_same_paragraph_are_allowed():
    prefix = 'RB-'
    report = f'{MARKET_ASSERTION} [{prefix}E3] [{prefix}E4]'
    assert report_evidence_scope_issues(report, market_contexts(prefix)) == []


def test_scope_public_demo_assertion_cannot_be_supported_by_ifr_demand_only():
    report = '확인 사실: 공공 실증사업이 제조업 인력난 해소와 고위험 작업 대체를 지원한다. [RB-E4]'
    assert report_evidence_scope_issues(report, market_contexts())


def test_scope_public_demo_assertion_is_supported_by_actual_kiria_demo_original():
    report = '확인 사실: 공공 실증사업이 제조업 인력난 해소와 고위험 작업 대체를 지원한다. [RB-E3]'
    assert report_evidence_scope_issues(report, market_contexts()) == []


def test_scope_ifr_demand_assertion_cannot_be_supported_by_public_demo_only():
    report = '확인 사실: IFR 보고서는 제조업 인력 부족이 자동화 수요를 촉진한다고 설명한다. [RB-E3]'
    assert report_evidence_scope_issues(report, market_contexts())


def test_scope_ifr_title_or_metadata_cannot_turn_team_original_into_market_evidence():
    source = original_page(['RB-E3', 'RB-E4'], TEAM_PAGE, title='IFR 공공 실증사업', doc_id='IFR-1')
    source['publisher'] = 'IFR / KIRIA'
    assert report_evidence_scope_issues(f'{MARKET_ASSERTION} [RB-E3] [RB-E4]', [source])


def test_scope_reference_market_links_do_not_repair_wrong_in_text_citations():
    report = (f'{MARKET_ASSERTION} [RB-E1] [RB-E2]\n\n'
              '## 5 — REFERENCE\n- [RB-E3] 공공 실증 원문 p.1\n- [RB-E4] IFR 원문 p.1\n')
    assert report_evidence_scope_issues(report, market_contexts())


@pytest.mark.parametrize('text', [
    '근거의 한계: IFR 산업 수요와 공공 실증사업의 참여 여부는 현재 자료로 확인되지 않았다.',
    '후속 확인: IFR 산업 수요와 공공 실증사업에 관한 원문 자료를 추가로 확보해야 한다.',
])
def test_scope_market_unknown_or_follow_up_is_not_an_unsupported_factual_assertion(text):
    assert report_evidence_scope_issues(f'{text} [RB-E1]', market_contexts()) == []
