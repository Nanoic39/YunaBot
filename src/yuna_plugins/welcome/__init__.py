from nonebot import get_plugin_config, logger, on_notice
from nonebot.adapters.onebot.v11 import Bot, GroupIncreaseNoticeEvent, Message, MessageSegment
from nonebot.matcher import Matcher
from nonebot.plugin import PluginMetadata

import base64, json, random
from pathlib import Path

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="入群欢迎",
    description="指定群聊中新人加入时发送欢迎文本和图片卡片",
    usage=("无指令,白名单群聊会自动根据规则触发"),
    type="application",
    config=Config,
    supported_adapters=None,
)

welcome_config = get_plugin_config(Config)

# 配置路径
PLUGIN_DIR = Path(__file__).parent
WITELIST_DIR = PLUGIN_DIR / "whitelist_group.json"
TEXT_DIR = PLUGIN_DIR / "template" / "text"
IMAGE_DIR = PLUGIN_DIR / "template" / "image"


welcome_matcher = on_notice(block=False)


# 加载配置
def _load_config() -> list:
    if not WITELIST_DIR.exists():  # 白名单列表文件不存在
        return []
    try:
        # 获取完整数据
        data = json.loads(WITELIST_DIR.read_text(encoding="utf-8"))

        if not isinstance(data, list):
            logger.error("【入群欢迎】白名单数据格式错误(非列表格式)")
            return []

        return data  # 返回全部配置数据

    except json.JSONDecodeError:
        # JSON 解析失败
        logger.error("【入群欢迎】白名单配置解析失败")
        return []
    except Exception as e:
        logger.error(f"【入群欢迎】白名单配置查询功能出现异常：{e}")
        # 其他异常
        return []


def _find_group_item(gid: str):
    """查找匹配且启用的群配置项，未找到返回默认回退配置"""
    for item in _load_config():
        if str(item.get("id_group")) == str(gid) and item.get("enabled") is True:
            return item.get("config", {})

    # 未找到匹配的 group_id 或 group_id 配置为禁用
    logger.debug(f"【入群欢迎】未找到群组 {gid} 的白名单配置或已禁用")
    # 全部禁用，回退到原始欢迎语
    return {"template_enabled": False}

def _save_config(data: list):
    WITELIST_DIR.write_text(json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8")

def _update_group_config(gid: str, update: dict):
    all_config = _load_config()
    for item in all_config:
        if str(item.get("id_group")) == str(gid):
            item.setdefault("config", {}).update(update)
            _save_config(all_config)
            return


def _file_index(path: Path):
    """提取文件名数字编号，如 1.txt -> '1'；非纯数字返回 None"""
    return path.stem if path.stem.isdigit() else None


def _list_files(directory: Path, suffixes: list) -> list:
    """列出目录下指定后缀的文件，按数字编号排序"""
    if not directory.exists():
        return []
    files = [
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower().lstrip(".") in suffixes
    ]
    files.sort(key=lambda p: int(_file_index(p) or 0))
    return files


def _apply_range_exclude(files: list, range_list: list, exclude_list: list) -> list:
    """range 非空时仅保留编号在 range 内的文件；exclude 非空时剔除对应编号"""
    range_set = {str(x) for x in (range_list or [])}
    exclude_set = {str(x) for x in (exclude_list or [])}
    result = []
    for f in files:
        idx = _file_index(f)
        if idx is not None and idx in exclude_set:
            continue
        if range_set and (idx is None or idx not in range_set):
            continue
        result.append(f)
    return result


def _select_file(files: list, mode: str, pointer):
    """按模式选择文件，返回 (文件路径或 None, 新的轮换指针或 None)"""
    if not files:
        return None, None
    if mode == "round_robin":
        idx = (pointer or 0) % len(files)
        return files[idx], (idx + 1) % len(files)
    return random.choice(files), None


async def _get_nickname(bot: Bot, event: GroupIncreaseNoticeEvent) -> str:
    """获取新成员群名片/昵称，失败回退 QQ 号"""
    try:
        info = await bot.get_group_member_info(
            group_id=event.group_id, user_id=event.user_id
        )
        return str(info.get("card") or info.get("nickname") or event.user_id)
    except Exception:
        return str(event.user_id)


# 触发事件
@welcome_matcher.handle()
async def handle_welcome(bot: Bot, event: GroupIncreaseNoticeEvent, matcher: Matcher):
    # 检查全局配置
    if not welcome_config.plugins_welcome_enabled:  # 如果全局开关关闭
        logger.info("【入群欢迎】全局开关已关闭，本次入群不会触发提示")
        return
    # 匹配具体群聊
    gid = str(event.group_id)
    group_config = _find_group_item(gid)

    if not group_config.get("template_enabled") or (
        not group_config.get("text_enabled") and not group_config.get("image_enabled")
    ):
        await matcher.finish(welcome_config.default_text)
        return

    nickname = await _get_nickname(bot, event)
    pointer_update = {}
    segments = []

    if group_config.get("text_enabled"):
        files = _list_files(TEXT_DIR, ["txt"])
        files = _apply_range_exclude(
            files, group_config.get("text_range", []), group_config.get("text_exclude", [])
        )
        path, ptr = _select_file(
            files, group_config.get("text_mode", "random"), group_config.get("text_round_pointer")
        )
        if path:
            content = path.read_text(encoding="utf-8").replace("{nickname}", nickname)
            segments.append(MessageSegment.text(content))
            if ptr is not None:
                pointer_update["text_round_pointer"] = ptr

    if group_config.get("image_enabled"):
        files = _list_files(IMAGE_DIR, welcome_config.image_suffix)
        files = _apply_range_exclude(
            files, group_config.get("image_range", []), group_config.get("image_exclude", [])
        )
        path, ptr = _select_file(
            files, group_config.get("image_mod", "random"), group_config.get("image_round_pointer")
        )
        if path:
            b64 = base64.b64encode(path.read_bytes()).decode()
            segments.append(MessageSegment.image(f"base64://{b64}"))
            if ptr is not None:
                pointer_update["image_round_pointer"] = ptr

    if pointer_update:
        _update_group_config(gid, pointer_update)

    if segments:
        await matcher.finish(Message(segments))
    else:
        await matcher.finish(welcome_config.default_text)

