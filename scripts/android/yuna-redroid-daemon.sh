#!/bin/bash
set -euo pipefail
export TMPDIR=/YunaGames/Android/tmp
exec /usr/bin/dockerd --data-root=/YunaGames/Android/redroid/docker --exec-root=/YunaGames/Android/redroid/run --pidfile=/YunaGames/Android/redroid/run/dockerd.pid --host=unix:///YunaGames/Android/redroid/run/docker.sock --bridge=none --iptables=false --ip-masq=false --ip-forward=false --storage-driver=overlay2
