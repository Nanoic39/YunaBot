"""语义提示词的轻量格式化；不在这里做任何关键词命中或梗判定。"""
from __future__ import annotations

import json
import math
import statistics


def safe_profile(profile):
    if not isinstance(profile, dict):
        return {}
    style = profile.get("style", {})
    return {"style": style if isinstance(style, (dict, str)) else {}}


def audience_context(personas, qq, recent, *, group=False):
    own = safe_profile((personas or {}).get(str(qq), {}))
    human = [r for r in recent[-24:] if r.get("role") == "user" and not r.get("is_bot")]
    lengths = [len(str(r.get("content") or "")) for r in human if r.get("content")]
    return {
        "user_profile": json.dumps(own, ensure_ascii=False)[:700],
        "scene": "群聊" if group else "私聊",
        "group_observation": {
            "samples": len(lengths),
            "median_message_chars": int(statistics.median(lengths)) if lengths else None,
        },
        "rules": "画像是弱背景，不是固定人格；当前严肃求助/明确偏好优先。短句、分条、倒装按情境选，不模仿辱骂、不归因他人话为用户。",
    }


def meme_hint(analysis):
    """把语义分析结果转成最终回复模型的“怎么接”提示。"""
    analysis = analysis if isinstance(analysis, dict) else {}
    mode = analysis.get("mode") or "uncertain"
    strategy = analysis.get("reply_strategy") or "ask_back"
    if mode == "literal":
        return ("【本轮语义判断】当前是字面表达/正常聊天，不要因为某个词像梗就强行玩梗；"
                "按真实意思准确回答。若只是错别字，按更正后的语义理解。"
                "若是请求展示/继续/解释引用内容，就按要求直接做，不要反问或多解释。")
    if mode == "typo":
        return ("【本轮语义判断】这更像普通打错字/输入失误，不是故意玩梗；"
                "先按能还原的意思正常回应，不要把错字当笑点，也不要编造梗的出处。")
    if mode == "uncertain":
        return ("【本轮语义判断】当前可能是在玩梗，也可能只是字面；先按对话气氛给一个自然反应，"
                "不要把不确定的谐音硬解释成某个梗，不要背百科或编造出处。"
                "除非确实接不上，不要用“什么意思/哪句”占满整条回复，最多轻问半句。")
    wants = bool(analysis.get("wants_explanation"))
    strategy_text = {
        "play_along": "接着对方的话往下说，给一个具体的反应或继续玩",
        "deadpan": "一本正经地配合离谱，不戳破",
        "self_deprecate": "先自嘲或认领这个说法",
        "callout": "顺着离谱点吐槽一句，但不解释梗",
        "ask_back": "只在确实接不上时轻问半句，不要连续追问",
        "ignore": "不必强行接梗，自然回应",
    }.get(strategy, "先给具体反应")
    variant = analysis.get("variant_type")
    lines = [
        "【本轮玩梗判断】对方是在跟你玩/接梗，不是在考你或求科普。"
        "把梗当成你们已经共享的语言，直接用角色自己的话接住、延续、反转或自嘲。",
        "不要先解释、定义、标注“这是梗/你在玩梗”，不要报百科、出处或含义，"
        "也不要用“你说的是……”开头；直接把梗自然用在对话里。",
        f"接法：{strategy_text}。",
    ]
    if wants:
        lines.append("对方明确在问含义/出处：可以简短说明，但仍用角色口吻，不要长篇百科。")
        if analysis.get("meaning"):
            lines.append("语境含义参考：" + str(analysis.get("meaning"))[:180])
    else:
        lines.append("对方没有明确问含义/出处，所以绝不要主动解释；把相关角色/作品/台词当成自然语境带过。")
    if variant == "lyric":
        lines.append(
            "这是歌词/台词引用：优先接下一句、用同一节奏哼回去，或自然点一下作品；"
            "不要主动报歌名/出处，除非对方明确问。不要把它当天气/日常话题，不要问“哪首歌”。"
        )
    elif variant == "reference":
        lines.append("这是作品/典故引用：用作品语境自然接，不要科普；不确定就只接语气。")
    if variant in ("none", "unknown") and strategy in ("play_along", "deadpan", "self_deprecate"):
        lines.append("【硬性】这是纯情绪/起哄，没有具体问题：整条回复必须是具体反应，不要用问句，不要要求对方解释笑点。")
    if analysis.get("reply_goal") and (wants or not any(x in str(analysis.get("reply_goal")) for x in ("解释", "含义", "出处", "说明", "科普"))):
        lines.append("本轮目标：" + analysis["reply_goal"])
    if analysis.get("reply_seed"):
        lines.append("可参考的短句（不要照抄）：" + analysis["reply_seed"])
    if analysis.get("web_sources"):
        lines.append("联网只用于确认出处；不要向用户罗列来源。")
    return "\n".join(lines)


def pragmatics_hint(plan, text, candidates=None):
    """兼容意图路由的语用提示；只解释结构化计划，不做词面匹配。"""
    if not isinstance(plan, dict):
        return ""
    confidence = plan.get("confidence")
    evidence = plan.get("evidence")
    if (type(confidence) not in (int, float) or not math.isfinite(confidence)
            or not .6 <= confidence <= 1 or not isinstance(evidence, str)
            or not evidence):
        return ""
    mode = plan.get("mode")
    if mode == "literal":
        return "【本轮语用】当前在认真说具体事情，不要因为某个词像梗就强行接梗。"
    if mode == "typo":
        return "【本轮语用】这更像普通打错字/输入失误，按能还原的意思正常回应，不要把错字当笑点。"
    if mode == "uncertain":
        return "【本轮语用】可能在玩梗但出处不确定；先顺着语气轻接或确认一句，别按字面给建议，也别装懂出处。"
    if mode != "banter":
        return ""
    strategy = str(plan.get("strategy") or "").strip()
    text_map = {
        "play_along": "顺着接住再补半句",
        "deadpan": "一本正经地配合离谱",
        "self_deprecate": "先自嘲或认领",
        "callout": "点破具体哪里离谱",
        "ask_back": "轻问一句具体所指",
    }
    return (
        "【本轮语用】这是在逗趣/接梗。" + text_map.get(strategy, "先给具体反应")
        + "；先给反应，不要一开口反问“哪句/什么意思”；不要写百科、理财或生活建议，"
          "不要解释自己识别了梗，不要装懂没把握的出处。"
    )


def expression_hint(audience):
    return (
        "【当前说话场景】" + json.dumps(audience, ensure_ascii=False) + "\n"
        "像即时聊天：需要时先反应再补一句，但这不是固定模板；一条能说清楚就一条。"
        "例如“这也太早了[[SPLIT]]我还没醒呢”，“可以啊[[SPLIT]]八点是吧”；示例只是节奏，不照抄。"
        "只有真有第二个自然反应才用[[SPLIT]]，不要把一句完整的话切成前后两截；每条都要能独立看懂。"
        "熟人闲聊可省略共同已知主语、把评价放前面再补对象；多人指向不清和真实操作信息必须写清。"
        "认真求助、审批、支付、群规和引用保持完整准确，不套短句或玩梗模板。"
        "口语自然不等于编造经历：没有实际证据，不说我打过、去过、我这边机厅多少钱；可以表达主观看法，不伪造所在地或消费经验。"
    )
