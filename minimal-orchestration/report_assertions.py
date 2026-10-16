"""본문의 각 인용을 검증된 사실에 연결하고 거래·투자자 범위 확대를 차단한다."""
import re
from report_language import report_evidence_links, explicit_limit


def assertion_pairs(report, evidence):
    return [{**p, 'allowed_facts': [{'id': ref, **evidence[ref]} for ref in p['evidence_ids'] if ref in evidence]}
            for p in report_evidence_links(report, []) if p['evidence_ids']]


def _qualified(tail):
    return explicit_limit(tail)


def assertion_scope_issues(report, evidence):
    issues = []
    for pair in assertion_pairs(report, evidence):
        # 인용을 포함한 문단의 다른 사실 단정도 계속 검사한다.
        text = re.sub(r'「[^」]*」', '[원문 인용]', pair['text'])
        paid = [m for m in re.finditer(r'유상\s*(?:납품|공급|매출|판매|계약)', text)
                if not _qualified(text[m.end():])]
        approved = any(f.get('commercial_fact_validation', {}).get('verdict') == 'supported' and
                       f.get('commercial_facts', {}).get('stage', {}).get('value') in {'paid_delivery', 'paid_expansion'}
                       for f in pair['allowed_facts'])
        if paid and not approved:
            issues.append(f"groundedness: {pair['paragraph_id']}에서 유상 실적을 단정했지만 인용 근거에 검증된 유상 거래 단계가 없습니다. 납품/계약 발표 범위로 수정하세요.")
        facts = pair['allowed_facts']
        if facts and all(f.get('source_scope') == 'requirement' for f in facts):
            if any(not _qualified(text[m.end():]) for m in re.finditer(
                    r'달성(?:했|한|하였|했다|하였다)|성공률(?:은|이)?\s*\d|실측(?:했|한|하였)', text)):
                issues.append(f"groundedness: {pair['paragraph_id']}의 근거는 요구조건/목표이며 달성 실측이 아닙니다. 목표라는 범위를 보존하세요.")
        deployment = [m for m in re.finditer(r'배치\s*(?:완료|했|하였|되어)|도입\s*(?:완료|했|하였)|운영\s*중', text)
                      if not _qualified(text[m.end():])]
        if deployment and facts and all(f.get('source_scope') == 'plan' or
                re.search(r'\bto deploy\b|배치\s*계획|도입\s*예정', f.get('evidence_quote', ''), re.I)
                for f in facts):
            issues.append(f"groundedness: {pair['paragraph_id']}의 계약 목적/계획을 배치 완료·현재 운영으로 확대했습니다. 계약 발표와 실제 운영을 구분하세요.")
        for m in re.finditer(r'(?:대기업\s*계열\s*(?:VC|벤처캐피탈)|투자자\s*(?:모두|전체))', text, re.I):
            if _qualified(text[m.end():]):
                continue
            quotes = ' '.join(f.get('evidence_quote', '') for f in pair['allowed_facts'])
            if not re.search(r'대기업\s*계열|corporate\s+venture|CVC', quotes, re.I):
                issues.append(f"groundedness: {pair['paragraph_id']}의 투자자 전체/대기업 계열 분류를 인용 원문이 입증하지 않습니다. 실제 투자자 이름과 개별 확인 범위로 수정하세요.")
                break
    return issues
