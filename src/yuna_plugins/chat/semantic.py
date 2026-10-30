"""轻量语义还原：处理明显的梗、省略和指代方向，不长期改变人设。"""

from __future__ import annotations

import re


def _clean(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def build_semantic_hint(text: str, recent: list | None = None) -> str:
    raw = str(text or "").strip()
    low = _clean(raw)
    if not low:
        return ""
    claude = bool(re.search(r"(克劳德|claude)", low))
    ban = bool(re.search(r"(封号|封禁|ban|封了)", low))
    china = bool(re.search(r"(中文|中国|国内|国人)", low))
    roleplay = bool(
        re.search(r"(模仿|扮演|装作|语气|说话|聊天)", low)
        and re.search(r"(克劳德|claude|ai|人工智能|助手)", low)
    )
    if claude and (ban or china):
        return (
            "【本轮语义还原（临时，不写入长期人设）】"
            "用户大概率在玩“Claude 检测到中国/中文用户就封号”的梗。"
            "这类句子里“封号”的对象是用户/中国用户，不是 bot 被系统封号；"
            "不要按字面理解成自己要被封，也不要真的宣称会封禁谁。"
            "如果对方要求模仿 Claude，可以用一两句礼貌、官方、略带机械感的客服腔，"
            "最后轻轻带出“检测到中文/中国用户 → 封号”的梗即可；"
            "只限本轮，不要改变后续对话，也不要对其他用户延续这个风格。"
        )
    if roleplay and claude:
        return (
            "【本轮语义还原（临时）】用户是在要求你短暂模仿 Claude 的说话方式，"
            "不是让你永久变成 Claude。用一两句礼貌、官方、略机械的客服腔回应，"
            "保持芸Yuna的底色；本轮结束后恢复正常，不要把这个风格带给其他用户。"
        )
    # 只对明显的省略/指代句做轻提示，避免过度揣测
    if re.search(r"(你是.+的话|如果我是|你应该.+我|我应该.+你)", low):
        return (
            "【本轮语义提示】先还原这句话里谁对谁做什么，注意省略的主语/宾语；"
            "不要把“你应该封号”机械理解成“bot 被封号”。如果不确定，"
            "用一句自然的话确认，而不是自行补出一个完整故事。"
        )
    return ""
