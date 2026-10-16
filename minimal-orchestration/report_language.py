"""미확인·회사 발표를 확정 사실로 바꾸는 명백한 보고서 표현을 차단한다."""
import re


def explicit_limit(tail):
    """같은 절의 부정·미확인을 인식하고 앞선 실제 달성 단정과 구분한다."""
    clause=re.split(r'[!?;。]|(?<!\d)\.|\.(?!\d)|하지만|그러나|다만',tail,maxsplit=1)[0]
    negative=re.search(r'미확인|확인(?:되지|할\s*수\s*없|하지\s*못)|입증(?:되지|하지\s*못)|'
        r'확인(?:이|은|가)?\s*필요|여부|필요\s*자료|추가\s*(?:검증|확인)|'
        r'단정(?:할\s*수\s*없|하지)|언급\s*(?:안\s*됨|되지)|미제시|미제공|'
        r'(?:근거|자료).{0,12}(?:부족|제한|없)|한계|미달|'
        r'(?:입증|검증|확인).{0,8}(?:부족|불가|없|어려|미흡)|'
        r'미흡|부족|불충분|제한|부재|비어\s*있|제시되지|보고되지|공개되지|판단\s*유보|불확실|'
        r'확보해야|추가\s*확보|필요|요구된다|'
        r'확인\s*(?:어려|곤란)|전환\s*가능|목표|요구조건|실사|아니|아닌|아님',clause[:150])
    if not negative:
        return False
    before=clause[:negative.start()]
    return not re.search(r'(?:확보|확인|달성|완료|도입|체결|발생|운영|실현)(?:했|됐|하였|되었|되어|한|된)|'
                         r'실적(?:이|은)?\s*있',before)


def report_evidence_links(text: str, source_contexts: list[dict]) -> list[dict]:
    """본문 문단의 실제 인용을 원문 페이지에 연결한다. 참고문헌은 제외한다."""
    sources = {ref: context for context in source_contexts for ref in context.get('evidence_ids', [])}
    links, buffer = [], []

    def flush():
        if not buffer:
            return
        number = buffer[0][0]
        line = ' '.join(value.strip() for _, value in buffer)
        buffer.clear()
        refs = list(dict.fromkeys(re.findall(r'\[((?:(?:MM|RB|FG)-)?E\d+)\]', line)))
        linked = [sources[ref] for ref in refs if ref in sources]
        pages = {(s.get('company'), s.get('doc_id'), s.get('page')): s for s in linked}
        links.append({'paragraph_id': f'P{number}', 'text': line, 'evidence_ids': refs,
                      'source_pages': [{'company': c.get('company'), 'doc_id': c.get('doc_id'),
                                        'page': c.get('page'), 'evidence_ids': c.get('evidence_ids', [])}
                                       for c in pages.values()]})

    for number, line in enumerate(text.splitlines(), 1):
        if re.match(r'^\s*#{1,6}\s+.*(?:REFERENCE|참고문헌)', line, re.I):
            flush()
            break
        if not line.strip() or line.lstrip().startswith(('#', '|')):
            flush()
            continue
        if re.match(r'^\s*(?:[-*+]\s|\d+[.)]\s)', line):
            flush()
        buffer.append((number, line))
    flush()
    return links


def report_evidence_scope_issues(text: str, source_contexts: list[dict]) -> list[str]:
    """알려진 기술·협력 목적·시장 귀속의 범위 확대를 좁게 검사한다.

    일반적인 의미 검증기는 아니며 원문 기반 LLM Judge를 대체하지 않는다.
    동일 문단의 다른 '자료 부족'은 앞선 사실 단정을 면제하지 않는다.
    """
    sources = {ref: context for context in source_contexts for ref in context.get('evidence_ids', [])}
    issues = []
    rules = (
        ('강화학습', r'강화\s*학습|reinforcement\s+learning|(?<![A-Za-z])RL(?![A-Za-z])',
         r'강화\s*학습|reinforcement\s+learning|(?<![A-Za-z])RL(?![A-Za-z])'),
        ('MOU 목적', r'공동\s*연구|기술\s*교류', r'공동\s*연구|기술\s*교류|joint\s+research|technolog\w*\s+exchange'),
        ('IFR 수요', r'(?<![A-Za-z])IFR(?![A-Za-z])', r'(?<![A-Za-z])IFR(?![A-Za-z])|International\s+Federation\s+of\s+Robotics'),
        ('공공 실증', r'공공\s*(?:로봇\s*)?실증', r'공공|실증사업|KIRIA|public'),
    )
    for link in report_evidence_links(text, source_contexts):
        paragraph = link['text']
        pages = [sources[ref].get('page_text', '') for ref in link['evidence_ids'] if ref in sources]
        for label, term, source_term in rules:
            if label == 'MOU 목적' and not re.search(r'(?<![A-Za-z])MOU(?![A-Za-z])', paragraph, re.I):
                continue
            for match in re.finditer(term, paragraph, re.I):
                # 불확실성을 해당 주장 바로 옆에서 분명히 드러낸 문장만 허용한다.
                tail = re.split(r'[.!?。,;]|\n|그러나|다만|하지만|반면', paragraph[match.end():], maxsplit=1)[0]
                compact = re.sub(r'\s+', '', tail[:80])
                uncertainty = re.search(r'여부|가능성|미확인|확인되지|확인할수없|확인해야|단정할수없|'
                                        r'확인자료가부족|원문자료.{0,6}확보해야|실사.{0,8}필요', compact)
                if uncertainty and not re.search(r'개발한다|개발했다|개발했다고|구축했다|확보했다|보유한다|기반일뿐|강하|뒷받침',
                                                  compact[:uncertainty.start()]):
                    continue
                support = source_term
                if label == 'MOU 목적':
                    support = (r'공동\s*연구|joint\s+research' if '연구' in match.group()
                               else r'기술\s*교류|technolog\w*\s+exchange')
                if any(re.search(support, page, re.I) for page in pages):
                    continue
                refs = ', '.join(link['evidence_ids']) or '인용 없음'
                issues.append(f"groundedness: {label} 주장에 연결된 원문 근거가 없습니다 "
                              f"({link['paragraph_id']}; {refs}). 확인된 기술/협력 범위로 고치거나 해당 사실의 원문 인용을 연결하세요.")
                break
    return issues


def report_language_issues(text: str) -> list[str]:
    compact = re.sub(r'\s+', '', text)
    rules = (
        ('미확인 실적을 실제 부재로 단정', r'(?:실적|지표|재구매|매출|계약).{0,22}(?:없음이확실|모두부재|부재하여)'),
        ('자료 부족을 인력 역량·경험 부족으로 단정', r'(?:역량|경험)(?:은|이|가)부족'),
        ('비교 실험 없이 경쟁사에 대한 기술 우열 단정', r'경쟁사.{0,12}(?:대비선행|대비우위|열세)'),
        ('회사 발표를 독립 입증으로 단정', r'독립(?:적으로|적)?(?:입증|검증)|일부독립적으로입증'),
        ('검증 범위 없는 국제적 기술 수준 단정', r'국제적수준의(?:기술|성능|시장진입)'),
        ('가정 제로샷을 제조·물류 현장 성능으로 확대', r'제조[·ㆍ/‧]?물류현장제로샷'),
        ('설계 공개를 제품 검증 완료로 확대', r'설계.{0,20}(?:가검증되었|검증완료)'),
        ('확인 자료 부족을 자료 소실로 표현', r'자료가남아있지않'),
    )
    issues = []
    for label, pattern in rules:
        for match in re.finditer(pattern, compact):
            if label in {'회사 발표를 독립 입증으로 단정', '비교 실험 없이 경쟁사에 대한 기술 우열 단정'}:
                # 해당 표현 바로 뒤의 부정/실사 문맥만 인정한다. 다른 문장의
                # '자료 부족'을 이유로 앞의 입증·우위 단정을 면제하지 않는다.
                tail = compact[match.end():]
                if label=='회사 발표를 독립 입증으로 단정' and explicit_limit(tail):
                    continue
                if label=='회사 발표를 독립 입증으로 단정' and '검증' in match.group():
                    # '독립 검증'이라는 조사 대상/자료 범주와 검증 완료의 실제
                    # 단정을 구분한다. 완료·실적·결과의 단정은 계속 차단한다.
                    if not re.match(r'(?:은|는|이|가|을|를|의)?(?:되었|됐|했|하였|완료|실적|결과|된)',tail):
                        continue
                if re.match(r'(?:은|는|이|가|의)?(?:현재)?(?:확인되지|확인불가|확인이필요|여부|미확인|부재|필요|확인해야|아니|'
                            r'자료(?:가|는)?(?:부족|없|제한)|자료를확보)|(?:라고|다고)?단정(?:하지|할수없)', tail):
                    continue
            issues.append(label)
            break
    return issues
