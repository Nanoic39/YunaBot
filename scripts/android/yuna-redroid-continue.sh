#!/bin/bash
set -euo pipefail
export TMPDIR=/YunaGames/Android/tmp
root=/YunaGames/Android/redroid
printf '{"ready":false,"stage":"transferring"}\n' > "$root/ready.json"
for task_try in $(seq 1 180); do
 if [ "$(stat -c %s "$root/download/layer.gz" 2>/dev/null || true)" = 646059401 ]; then break; fi
 sleep 5
done
python3 "$root/yuna-redroid-import.py"
bash "$root/yuna-redroid-prepare.sh"
