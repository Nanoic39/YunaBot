# YunaBot

一个带长期记忆、拟人化聊天、模型链路和可视化管理面板的 NoneBot2 机器人。

## 特性

- QQ 群 / 私聊聊天，支持引用、图片、表情和转发记录解析。
- 长期记忆、用户画像、关系与好感度。
- 语义化玩梗接梗与效果复核，不使用关键词命中。
- 可管理的模型链路，支持主对话、Agent、识图、表情判断和 OCR 多节点降级。
- 可管理的表情、开黑约局、限流、欢迎、直播、天气、空间等插件。
- Vue 3 管理面板。

## 代码与个人配置分离

个人人格、成长、访问名单、记忆、API Key 与运行时数据不进入 Git：

- `config/`：角色、成长人格、用户画像、记忆、访问名单、模型链路。
- `.env`：部署密钥与实例配置。
- `data/`：数据库和管理面板状态。
- `src/yuna_plugins/**/data/` 等：插件运行时数据。

部署时可通过 `YUNA_CONFIG_DIR` 指定独立配置目录；未设置时默认读取仓库根目录的 `config/`。

## 快速开始

```bash
cp .env.example .env
# 填写自己的 API Key、NoneBot/OneBot 配置
# 在 config/character.txt 写入自己的角色设定
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python bot.py
```

管理面板默认由 `ADMIN_PORT` 配置，源码位于 `admin-next/`，构建产物在 `src/admin/static/next/`。

## 配置包

个人配置压缩包解压到 `MYSELF_PATH/YunaBot/` 后，会恢复：

- `config/` 下的人格、记忆、访问名单和模型链路
- `.env`
- `data/` 下的管理面板与数据库状态
- 各插件原有的个人配置/模板

公开仓库不包含任何上述个人数据或密钥。

## 说明

仓库只提供代码和通用占位配置。第三方模型 API Key、QQ 号、群号、聊天记录、图片素材和服务器地址均由部署者自行配置。
