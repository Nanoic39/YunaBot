"""消息指向分析：区分对 bot 说、对别人说、介绍/提及 bot，以及指向不明。

只做保守判断，输出给提示词，不直接替模型决定回复内容。
"""

from __future__ import annotations

import re

_INTRO_RE = re.compile(r"(介绍|这是|这个是|这只|它叫|她叫|名字叫|新来的|机器人|bot|助手|ai)", re.I)
_ASK_BOT_RE = re.compile(
    r"(你觉得|你怎么看|你说呢|你说说|你回答|你叫什么|你是谁|介绍一下你|"
    r"帮我|问你|问你一下|打个招呼|说句话|来一句|你来说|你回|你解释|"
    r"Yuna.{0,8}(?:呢|吗|怎么|觉得|说说)|bot.{0,8}(?:呢|吗|怎么|觉得|说说))",
    re.I,
)
_QUESTION_RE = re.compile(r"[?？]|吗|呢|怎么|为什么|什么|谁|哪|多少|如何")


def _clean(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def _names_in(text: str, names: list[str], exclude: set[str] | None = None) -> list[str]:
    low = _clean(text)
    exclude = exclude or set()
    found = []
    for name in names:
        clean = _clean(name)
        if len(clean) < 2 or clean in exclude:
            continue
        if clean and clean in low and name not in found:
            found.append(name)
    return found


def analyze(
    text: str,
    *,
    speaker_qq: str,
    self_id: str,
    reply_to_qq: str = "",
    at_qqs: list[str] | tuple[str, ...] = (),
    character_names: list[str] | tuple[str, ...] = (),
    known_members: list[tuple[str, str]] | tuple[tuple[str, str], ...] = (),
) -> dict:
    """返回结构化的指向分析，confidence 用 high/medium/low。"""
    text = str(text or "")
    speaker_qq = str(speaker_qq or "")
    self_id = str(self_id or "")
    ats = [str(q) for q in at_qqs if str(q).isdigit()]
    at_bot = self_id in ats
    at_others = [q for q in ats if q != self_id and q]
    reply_to_bot = bool(reply_to_qq and reply_to_qq == self_id)
    reply_to_other = bool(reply_to_qq and reply_to_qq != self_id)
    bots = [str(n) for n in character_names if str(n).strip()] or ["Yuna"]
    bot_names = _names_in(text, bots)
    name_mentions_bot = bool(bot_names)
    intro = bool(_INTRO_RE.search(text))
    asks_bot = bool(_ASK_BOT_RE.search(text)) or (bool(_QUESTION_RE.search(text)) and (at_bot or name_mentions_bot))
    intro_about_bot = intro and (at_bot or name_mentions_bot)

    names = {str(q): str(n or "").strip() for q, n in known_members if str(q).isdigit()}
    content_others = [
        (q, names.get(q, "")) for q in names
        if q not in (speaker_qq, self_id) and names.get(q) and _clean(names[q]) in _clean(text)
    ]
    # 昵称可能重复，去重并排除 bot 名
    dedup = []
    for q, name in content_others:
        if _clean(name) in {_clean(x) for x in bots}:
            continue
        if (q, name) not in dedup:
            dedup.append((q, name))
    content_others = dedup

    addressee = ""
    addressee_qq = ""
    direct_to_bot = False
    bot_role = "not_mentioned"
    confidence = "low"

    if reply_to_bot:
        addressee, addressee_qq = "Yuna", self_id
        direct_to_bot = True
        bot_role = "direct"
        confidence = "high"
    elif at_bot and at_others:
        # @ 了别人又 @ bot：默认主对象是别人；只有明确在问 bot 才把 bot 当共同对象
        other = at_others[0]
        addressee_qq = other
        addressee = names.get(other) or f"QQ{other}"
        if asks_bot:
            direct_to_bot = True
            bot_role = "co_addressed"
            addressee = f"{addressee} 和Yuna"
            confidence = "medium"
        else:
            bot_role = "introduced_or_mentioned" if intro_about_bot else "mentioned"
            confidence = "high"
    elif at_bot:
        addressee, addressee_qq = "Yuna", self_id
        if asks_bot:
            direct_to_bot = True
            bot_role = "co_addressed" if reply_to_other else "direct"
            if reply_to_other:
                other_name = names.get(reply_to_qq) or f"QQ{reply_to_qq}"
                addressee = f"{other_name} 和Yuna"
            confidence = "high"
        elif intro_about_bot:
            direct_to_bot = False
            bot_role = "introduced"
            addressee = "群里"
            confidence = "medium"
        else:
            direct_to_bot = True
            bot_role = "direct"
            confidence = "high"
    elif reply_to_other:
        addressee_qq = reply_to_qq
        addressee = names.get(reply_to_qq) or f"QQ{reply_to_qq}"
        if intro_about_bot:
            bot_role = "introduced_or_mentioned"
            confidence = "high"
        elif name_mentions_bot:
            if asks_bot:
                direct_to_bot = True
                bot_role = "co_addressed"
                addressee = f"{addressee} 和Yuna"
            else:
                bot_role = "mentioned"
            confidence = "medium"
        else:
            bot_role = "not_mentioned"
            confidence = "high"
    elif at_others:
        other = at_others[0]
        addressee_qq = other
        addressee = names.get(other) or f"QQ{other}"
        bot_role = "mentioned" if name_mentions_bot or at_bot else "not_mentioned"
        confidence = "medium"
    elif content_others:
        q, name = content_others[0]
        addressee_qq, addressee = q, name
        bot_role = "mentioned" if name_mentions_bot else "not_mentioned"
        confidence = "low"
    elif name_mentions_bot:
        if intro_about_bot and not asks_bot:
            addressee = "群里"
            bot_role = "introduced_or_mentioned"
            confidence = "medium"
        else:
            addressee, addressee_qq = "Yuna", self_id
            direct_to_bot = True
            bot_role = "direct"
            confidence = "medium"
    else:
        addressee = "不明确"
        bot_role = "not_mentioned"
        confidence = "low"

    # @bot/reply bot 的置信度最高；文字点名但没有 @ 时保持中等
    if direct_to_bot and not (reply_to_bot or at_bot):
        confidence = "medium"

    if direct_to_bot:
        conclusion = "主对象是你，可以直接回应；不要把它当成别人之间的对话。"
    elif bot_role in ("introduced", "introduced_or_mentioned", "mentioned"):
        if intro_about_bot:
            conclusion = (
                f"主对象是{addressee}，你在这里是被介绍/被讨论的对象，不是主对象；"
                "不要抢答、不要替对方回答或自我介绍到停不下来；只有明确问你时才回应。"
            )
        else:
            conclusion = (
                f"主对象是{addressee}，你只是被提及；不要把这个消息当成在直接问你，"
                "不要抢答或替对方回答；确有必要再用一句自然的话接住。"
            )
    elif addressee != "不明确":
        conclusion = (
            f"主对象是{addressee}，不是在直接问你；不要替对方回答或抢话。"
            "如果群里安静时想参与，只能轻轻接一句，不要假装自己是这句话的对象。"
        )
    else:
        conclusion = (
            "指向不明确，没有明确在问你；不要默认整段都在对你说，也不要把每个“你”都当成自己。"
            "需要时用一句自然的话确认。"
        )

    return {
        "text": text,
        "speaker_qq": speaker_qq,
        "addressee": addressee,
        "addressee_qq": addressee_qq,
        "reply_to_qq": str(reply_to_qq or ""),
        "at_bot": at_bot,
        "at_others": at_others,
        "name_mentions_bot": name_mentions_bot,
        "bot_names_in_text": bot_names,
        "content_targets": content_others,
        "intro_about_bot": intro_about_bot,
        "asks_bot": asks_bot,
        "direct_to_bot": direct_to_bot,
        "bot_role": bot_role,
        "confidence": confidence,
        "conclusion": conclusion,
    }


def prompt_hint(info: dict) -> str:
    if not info:
        return ""
    lines = [
        f"【本条消息指向｜置信度 {info.get('confidence', 'low')}】",
        f"说话人：QQ{info.get('speaker_qq') or '?'}",
        f"主对象：{info.get('addressee') or '不明确'}",
        f"回复对象：{('QQ' + info['reply_to_qq']) if info.get('reply_to_qq') else '无'}",
        f"@：{'、'.join(['QQ' + q for q in info.get('at_others') or []] + (['Yuna'] if info.get('at_bot') else [])) or '无'}",
        f"bot 角色：{info.get('bot_role')}",
        info.get("conclusion") or "",
        "指向不明时先旁听；不要为了参与每个话题而追问是不是在叫你。",
    ]
    if info.get('semantic_segments'):
        lines.append('逐句指向：'+str(info['semantic_segments']))
        lines.append('判定依据：'+str(info.get('semantic_reason') or ''))
    return "\n".join(line for line in lines if line)
