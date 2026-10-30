"""聊天文本分词与停用词过滤（供 datadetail 的词频统计 / 词云使用）。

jieba 为可选依赖：未安装时 `tokenize` 返回空列表，其余统计功能不受影响。
"""

from __future__ import annotations

import re

# 常见中文停用词 + 群聊高频无意义词（语气词、笑声、口水话等）
_DEFAULT_STOPWORDS = {
    "的", "了", "是", "在", "我", "你", "他", "她", "它", "们", "我们", "你们", "他们",
    "这", "那", "这个", "那个", "这些", "那些", "这样", "那样", "这里", "那里",
    "什么", "怎么", "怎样", "为什么", "如何", "哪个", "哪些", "多少", "几点",
    "因为", "所以", "但是", "可是", "不过", "而且", "然后", "以及", "或者", "还是",
    "就是", "不是", "没有", "不会", "不能", "不要", "不用", "别", "已经", "正在",
    "可以", "应该", "需要", "可能", "也许", "大概", "真的", "确实", "其实", "当然",
    "一个", "一些", "一下", "一起", "一直", "一点", "有点", "好多", "很多", "大家",
    "自己", "别人", "现在", "时候", "时间", "今天", "明天", "昨天", "最近", "刚才",
    "如果", "虽然", "可是", "只是", "只有", "还有", "就是", "于是", "因此",
    "哈哈", "哈哈哈", "哈哈哈哈", "呵呵", "嘿嘿", "嘻嘻", "吼吼", "emmm", "emm",
    "嗯", "哦", "啊", "呀", "吧", "呢", "吗", "啦", "哇", "诶", "唉", "额", "唔",
    "好吧", "好的", "好嘞", "可以", "没事", "谢谢", "感谢", "抱歉", "对不起",
    "感觉", "觉得", "好像", "貌似", "居然", "竟然", "难道", "反正", "总之",
    "233", "666", "2333", "23333", "hhhh", "hhh", "awa", "qwq", "orz",
}

# 纯符号 / 表情等非文字内容（CJK 与字母数字均视为 word 字符，不会命中）
_NOISE_RE = re.compile(r"^[\W_]+$", re.UNICODE)


def build_stopwords(extra: str = "") -> set[str]:
    """内置停用词 + 配置补充（空格 / 逗号 / 顿号 / 分号分隔）。"""
    words = set(_DEFAULT_STOPWORDS)
    for w in re.split(r"[\s,，、;；]+", extra or ""):
        w = w.strip()
        if w:
            words.add(w)
    return words


def tokenize(text: str, stopwords: set[str] | None = None) -> list[str]:
    """把一段聊天文本切分为可用于词频统计的词。

    过滤规则：长度 < 2、停用词、纯符号/表情、含 http 的链接片段。
    """
    text = (text or "").strip()
    if not text:
        return []
    try:
        import jieba
    except Exception:  # noqa: BLE001
        return []
    stop = stopwords if stopwords is not None else _DEFAULT_STOPWORDS
    out: list[str] = []
    for raw in jieba.cut(text):
        w = raw.strip()
        if len(w) < 2 or w in stop or _NOISE_RE.match(w) or "http" in w.lower():
            continue
        out.append(w)
    return out
