"""聊天与自我经历共用的轻量相关度；不用于意图识别或授权判断。"""
import re


def memory_terms(text: str) -> set[str]:
    lowered = (text or '').lower()
    terms = set(re.findall(r'[a-z0-9_]{2,}', lowered))
    for chunk in re.findall(r'[\u4e00-\u9fff]+', lowered):
        if len(chunk) == 1:
            terms.add(chunk)
        else:
            terms.update(chunk[i:i+2] for i in range(len(chunk)-1))
    return terms


def relevance(text: str, query: str) -> float:
    query_terms = memory_terms(query)
    if not query_terms:
        return 0.0
    text_terms = memory_terms(text)
    if not text_terms:
        return 0.0
    score = len(query_terms & text_terms) / len(query_terms)
    if len(query.strip()) >= 2 and query.strip() in (text or ''):
        score += 0.5
    return score
