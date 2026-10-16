"""PDF 줄바꿈·띄어쓰기만 다른 인용을 원문의 실제 연속 구간으로 연결한다."""
import re


def exact_quote_span(page_text, quote):
    if not isinstance(quote, str) or not quote.strip():
        return None
    if ' '.join(quote.split()) in ' '.join(page_text.split()):
        return quote
    # 문자·숫자·문장부호는 한 글자도 보충/수정하지 않는다. 줄바꿈으로 쪼개진
    # 영단어만 다른 경우 실제 원문 위치를 반환하므로 downstream 검사는 그대로다.
    compact = re.sub(r'\s', '', quote)
    if len(compact) < 12:
        return None
    positions = [i for i, c in enumerate(page_text) if not c.isspace()]
    source = ''.join(page_text[i] for i in positions)
    start = source.find(compact)
    if start < 0:
        return None
    return page_text[positions[start]:positions[start + len(compact) - 1] + 1]
