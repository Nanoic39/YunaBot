import nonebot
from nonebot.adapters.onebot.v11 import Adapter as ONEBOT_V11Adapter
from nonebot.adapters.telegram import Adapter as TelegramAdapter
from nonebot.log import default_format, logger

logger.add(
    "logs/error.log",
    rotation="00:00",
    retention="1 week",
    diagnose=False,
    level="ERROR",
    format=default_format,
    encoding="utf-8",
)


nonebot.init()

driver = nonebot.get_driver()
driver.register_adapter(ONEBOT_V11Adapter) # type: ignore

# Telegram 拆分配置组装（telegram_bots 为 list，无法用 __ 嵌套拆分，故在此注入）
_tg_cfg = driver.config
if not getattr(_tg_cfg, "telegram_proxy", None):
    _tg_cfg.telegram_proxy = None  # 空代理会让 httpx 报错，置 None 表示直连

_tg_token = (getattr(_tg_cfg, "telegram_token", "") or "").strip()
if _tg_token:
    _is_webhook = getattr(_tg_cfg, "telegram_is_webhook", False)
    if isinstance(_is_webhook, str):
        _is_webhook = _is_webhook.strip().lower() in ("1", "true", "yes")
    _tg_cfg.telegram_bots = [{
        "token": _tg_token,
        "api_server": getattr(_tg_cfg, "telegram_api_server", "") or "https://api.telegram.org/",
        "is_webhook": bool(_is_webhook),
    }]

# driver.register_adapter(TelegramAdapter) # type: ignore

nonebot.load_from_toml("pyproject.toml")

# 加载插件
nonebot.load_plugins("src/sys_plugins")  # 系统插件
nonebot.load_plugins("src/other_plugins") # 其它第三方插件
nonebot.load_plugins("src/yuna_plugins")  # Yuna系列插件

from src.admin.server import install as install_admin

install_admin()

if __name__ == "__main__":
    nonebot.run()
