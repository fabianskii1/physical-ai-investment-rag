"""기업별 검증 산출물을 한 쪽 중간 보고서로 출력한다. 새 모델 호출은 없다."""
from pathlib import Path

from rag.scorecard import SCORECARD
from report_renderer import ReportLayoutError, render_report, validate_pdf


def save_company_brief(record, output_dir):
    state, scores = record['state'], record['scores']
    company = state['company_info']['name']
    if scores['company'] != company or scores['trace_id'] != state['trace_id']:
        raise ValueError('기업 요약과 점수의 실행 ID가 일치하지 않습니다')
    case = state.get('investment_result', {}).get('investment_case', {})
    claims = state.get('research_result', {}).get('source_checked_claims', [])
    evidence = {f'E{i}': claim for i, claim in enumerate(claims, 1)}
    for point in case.get('supporting_facts', []):
        refs = point.get('evidence_ids', [])
        if not refs or any(ref not in evidence for ref in refs) or not any(
                point.get('fact') == evidence[ref].get('claim') for ref in refs):
            raise ValueError('기업 요약에 검증되지 않은 투자 포인트가 있습니다')
    status = state['status']
    stem = company.lower().replace(' ', '_') + ('_draft' if status == 'quality_failed' else '') + '_brief'
    directory = Path(output_dir).resolve()
    pdf, md = directory / f'{stem}.pdf', directory / f'{stem}.md'
    for points_limit in (3, 2, 1, 0):
        known = scores['scorable_weight']
        total = f"{scores['total_score']:g}점" if known else '산정 보류'
        lines = [f'# {company} — 기업별 투자 검토 요약',
                 f"상태: {status}. 검토 의견: {case.get('action', '판단 유보')}.",
                 f"확인 획득점수: {total} / 원 배점 100. 확인 배점 {known}, U 배점 {scores['unknown_weight']}.",
                 'U는 자료 미확인이며 0점·실패가 아니다. 점수를 100점으로 재환산하지 않는다.',
                 '### 영역별 점수', '| 평가 영역 | 확인 획득 / 원 배점 | 확인 배점 / U 배점 |',
                 '|---|---:|---:|']
        for name, criteria in SCORECARD.items():
            data = scores['category_scores'][name]
            earned = f"{data['earned_score']:g}" if data['earned_score'] is not None else 'U'
            lines.append(f"| {name} | {earned} / {sum(criteria.values())} | "
                         f"{data['scorable_weight']} / {sum(criteria.values())-data['scorable_weight']} |")
        lines += ['### 투자 검토 근거']
        points = case.get('supporting_facts', [])[:points_limit]
        for point in points:
            fact = ' '.join(point['fact'].split())
            excerpt = fact if len(fact) <= 145 else fact[:145] + ' …(발췌)'
            lines.append(f"- {point['criterion']}: {excerpt} " + ' '.join(f'[{ref}]' for ref in point['evidence_ids']))
        if not points:
            lines.append('세부 근거는 같은 실행의 점수 JSON 및 통합 보고서에서 확인한다.')
        lines += ['### 투자 전 확인 조건']
        conditions = case.get('required_conditions', [])
        for item in conditions[:3]:
            lines.append(f"- {item['criterion']}: {item['required']}")
        if not conditions:
            lines.append('확인된 근거와 현재 거래조건을 함께 심사한다.')
        lines.append('이 한 쪽은 최종 통합 보고서의 중간 산출물이다. 21항목 등급·원문 페이지·'
                     '추가 조건과 최종 투자 대상은 같은 실행의 통합 보고서 및 점수 JSON에 보존한다.')
        markdown = '\n\n'.join(lines[:5]) + '\n' + '\n'.join(lines[5:]) + '\n'
        try:
            render_report(markdown, pdf, max_pages=1)
            md.write_text(markdown, encoding='utf-8')
            return {'markdown': md, 'pdf': pdf, 'pdf_checks': validate_pdf(pdf, max_pages=1)}
        except ReportLayoutError:
            if points_limit == 0:
                raise
