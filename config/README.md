# YunaBot 个人配置

此目录与代码解耦，用于放置角色、人格、成长、访问名单和密钥等个人配置。

推荐通过 `YUNA_CONFIG_DIR` 指向本目录；默认使用仓库根目录的 `config/`。

需要自己维护的文件（不会提交到 Git）：

- `character.txt`：角色人设、称呼、关系与说话方式
- `growth_persona.json`：成长人格，按 QQ 或 `global` 分组
- `personas/`：用户与群聊画像
- `memory/`：会话记忆
- `users.json`、`whitelist_group.json`、`whitelist_user.json`、`bots.json`
- `vision_chain.json`：模型链路与 API Key（权限 0600）
- `slang.json`：长期语义记忆

仓库内只保留 `character.txt` 的通用占位文本和示例文件。个人配置压缩包会覆盖这些文件。
