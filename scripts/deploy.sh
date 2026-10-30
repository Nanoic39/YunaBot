#!/usr/bin/env bash
# YunaBot 服务器部署脚本
#
# 设计目标：
#   * 只同步代码与文档，绝不覆盖 .env / data / memory / emotes / 用户画像；
#   * 部署前自动备份远端代码，失败可回滚；
#   * 同步后先做 Python 编译检查，再重启 screen 会话，最后探活；
#   * 不需要在仓库里保存密码，认证交给 ssh / ssh-agent / SSH_ASKPASS；
#   * 只依赖 ssh + tar，服务器与本地都不需要 rsync。
#
# 用法：
#   SSH_TARGET=root@1.2.3.4 REMOTE_DIR=/YunaNexus/YunaBot ./scripts/deploy.sh
#   SSH_TARGET=root@1.2.3.4 REMOTE_DIR=/YunaNexus/YunaBot ./scripts/deploy.sh --rollback
#
# 可选环境变量：
#   SSH_PORT=22             SSH 端口
#   SSH_KEY=~/.ssh/id_ed25519
#   SCREEN_NAME=yunabot     远端 screen 会话名
#   HEALTH_URL=http://127.0.0.1:14000/api/health
#   SKIP_HEALTH=1           跳过探活
#   SKIP_BACKUP=1           跳过远端备份（不推荐）

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SSH_TARGET="${SSH_TARGET:-}"
REMOTE_DIR="${REMOTE_DIR:-/YunaNexus/YunaBot}"
SSH_PORT="${SSH_PORT:-22}"
SCREEN_NAME="${SCREEN_NAME:-yunabot}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:14000/api/health}"
SSH_KEY="${SSH_KEY:-}"
MODE="${1:-deploy}"

if [[ -z "$SSH_TARGET" ]]; then
  echo "缺少 SSH_TARGET，例如：SSH_TARGET=root@1.2.3.4 REMOTE_DIR=$REMOTE_DIR $0" >&2
  exit 2
fi
if ! command -v ssh >/dev/null 2>&1 || ! command -v tar >/dev/null 2>&1; then
  echo "需要 ssh 与 tar。" >&2
  exit 2
fi

SSH_OPTS=(-p "$SSH_PORT" -o ConnectTimeout=15 -o ServerAliveInterval=20)
if [[ -n "$SSH_KEY" ]]; then
  SSH_OPTS+=(-i "$SSH_KEY")
fi

remote() {
  ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "$@"
}

backup_remote() {
  if [[ "${SKIP_BACKUP:-0}" == "1" ]]; then
    echo "==> 跳过备份"
    return 0
  fi
  local stamp
  stamp="$(date +%Y%m%d-%H%M%S)"
  echo "==> 备份远端代码（$stamp）"
  remote "set -e; cd '$REMOTE_DIR'; mkdir -p backups docs scripts; \
    tar --exclude='.venv' --exclude='data' --exclude='logs' --exclude='backups' \
        --exclude='*.zip' --exclude='__pycache__' --exclude='*.pyc' \
        --exclude='src/yuna_plugins/chat/emotes' \
        --exclude='src/yuna_plugins/chat/misc_emoji' \
        --exclude='src/yuna_plugins/chat/emotes_rejected' \
        --exclude='src/yuna_plugins/chat/memory' \
        --exclude='src/yuna_plugins/chat/personas' \
        --exclude='src/yuna_plugins/chat/*.json' \
        --exclude='src/yuna_plugins/*/cache' \
        -czf 'backups/code-$stamp.tgz' src docs scripts README.md bot.py; \
    ls -1t backups/code-*.tgz 2>/dev/null | tail -n +16 | xargs -r rm -f; \
    echo \"backup=backups/code-$stamp.tgz\""
}

sync_code() {
  echo "==> 同步代码到 $SSH_TARGET:$REMOTE_DIR"

  # 管理面板：先移除旧 static，避免旧 JS/CSS 残留；随后整包解压。
  # src/admin 下没有运行时数据，可以安全替换。
  tar -czf - -C "$ROOT" \
    --exclude='__pycache__' --exclude='*.pyc' \
    src/admin src/agent src/yuna_plugins/agent_control src/yuna_plugins/qq_access src/yuna_plugins/limit src/yuna_plugins/risk src/yuna_plugins/game_party src/qq_access.py src/qq_games.py docs scripts README.md \
    | remote "set -e; cd '$REMOTE_DIR'; rm -rf src/admin/static; tar -xzf -"

  # 聊天插件：只覆盖 .py，保留 character.txt / json / memory / emotes / personas
  ( cd "$ROOT/src/yuna_plugins/chat" && tar -czf - --exclude='__pycache__' *.py character_reference.png ) \
    | remote "set -e; cd '$REMOTE_DIR/src/yuna_plugins/chat'; tar -xzf -"
}

check_remote() {
  echo "==> 远端 Python 编译检查"
  remote "cd '$REMOTE_DIR' && test -x .venv/bin/python && \
    .venv/bin/python -m py_compile src/admin/server.py src/admin/media.py src/admin/backup.py src/admin/alerts.py src/admin/quality.py src/admin/__init__.py src/agent/*.py src/yuna_plugins/agent_control/*.py src/yuna_plugins/chat/*.py src/yuna_plugins/risk/*.py src/yuna_plugins/limit/*.py"
}

restart_bot() {
  echo "==> 重启 screen 会话：$SCREEN_NAME"
  remote "cd '$REMOTE_DIR'; \
    if screen -ls | grep -q '[.]$SCREEN_NAME'; then \
      screen -S '$SCREEN_NAME' -X stuff \"\$(printf '\\003')\"; \
      for i in \$(seq 1 30); do \
        pids=\$(ps -eo pid=,args= | awk '\$2==\".venv/bin/python\" && \$3==\"bot.py\" {print \$1}'); \
        if [ -z \"\$pids\" ] && ! ss -ltn 2>/dev/null | grep -qE ':(14000|14515)\\b'; then break; fi; \
        if [ -n \"\$pids\" ]; then kill \$pids 2>/dev/null || true; fi; \
        sleep 1; \
      done; \
      if ss -ltn 2>/dev/null | grep -qE ':(14000|14515)\\b'; then \
        echo '旧进程未在 30 秒内释放端口，停止部署'; exit 1; \
      fi; \
      screen -S '$SCREEN_NAME' -X quit 2>/dev/null || true; \
      sleep 2; \
    fi; \
    screen -dmS '$SCREEN_NAME' bash -lc 'cd $REMOTE_DIR && exec .venv/bin/python bot.py'"
}

wait_health() {
  if [[ "${SKIP_HEALTH:-0}" == "1" ]]; then
    echo "==> 跳过探活"
    return 0
  fi
  echo "==> 等待服务端口、健康接口与机器人连接"
  remote "for i in \$(seq 1 45); do \
      body=\$(curl -fsS --max-time 3 '$HEALTH_URL' 2>/dev/null || true); \
      if printf '%s' \"\$body\" | grep -q '\"bots\":[1-9]'; then \
        printf '%s\n' \"\$body\"; exit 0; \
      fi; \
      sleep 1; \
    done; \
    echo '警告：管理服务已启动，但 45 秒内未检测到机器人连接。'; \
    curl -fsS --max-time 5 '$HEALTH_URL' 2>/dev/null || true; echo; exit 0"
}

rollback() {
  echo "==> 回滚到最近一次备份"
  remote "set -e; cd '$REMOTE_DIR'; \
    latest=\$(ls -1t backups/code-*.tgz 2>/dev/null | head -1); \
    test -n \"\$latest\" || { echo '没有可用备份'; exit 1; }; \
    echo \"使用备份：\$latest\"; \
    tar -xzf \"\$latest\"; \
    test -x .venv/bin/python; \
    .venv/bin/python -m py_compile src/admin/server.py src/admin/media.py src/yuna_plugins/chat/*.py"
  restart_bot
  wait_health
  echo "==> 回滚完成"
}

case "$MODE" in
  deploy)
    backup_remote
    sync_code
    check_remote
    restart_bot
    wait_health
    echo "==> 部署完成：$SSH_TARGET:$REMOTE_DIR"
    ;;
  --rollback|rollback)
    rollback
    ;;
  *)
    echo "未知模式：$MODE（可用：deploy / rollback）" >&2
    exit 2
    ;;
esac
