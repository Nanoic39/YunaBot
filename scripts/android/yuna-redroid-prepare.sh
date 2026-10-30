#!/bin/bash
set -euo pipefail
export TMPDIR=/YunaGames/Android/tmp
root=/YunaGames/Android/redroid
socket=unix://$root/run/docker.sock
image=docker.m.daocloud.io/redroid/redroid:12.0.0_64only-latest
adb=/YunaGames/Android/sdk/platform-tools/adb
serial=172.31.99.2:5555
fail(){ printf '{"ready":false,"stage":"failed"}\n' > "$root/ready.json"; }
trap fail ERR
printf '{"ready":false,"stage":"downloading"}\n' > "$root/ready.json"
if ! docker -H "$socket" image inspect "$image" >/dev/null 2>&1; then
 docker -H "$socket" pull "$image"
fi
actual=$(docker -H "$socket" image inspect "$image" --format '{{.Id}}')
test "$actual" = 'sha256:202754df4d668c2bd2467889ce74e25b69d366552f312391e0a1052a4e4ce605'
bash "$root/yuna-redroid-network.sh"
# 只停止旧的测试手机，保留 AVD 数据和所有游戏服务。
for task_pid in $(pgrep -f '^/YunaGames/Android/sdk/emulator/qemu/.*/qemu-system-x86_64-headless -avd yuna_phone ' || true); do kill -TERM "$task_pid"; done
if ! docker -H "$socket" container inspect yuna-redroid-phone >/dev/null 2>&1; then
 docker -H "$socket" run -d --name yuna-redroid-phone --restart unless-stopped --privileged --memory 2304m --memory-swap 2816m --cpus 2 --pids-limit 1536 --network yuna-redroid --ip 172.31.99.2 -v "$root/data:/data" "$image" androidboot.use_memfd=1 androidboot.redroid_gpu_mode=guest androidboot.redroid_width=720 androidboot.redroid_height=1280 androidboot.redroid_fps=10 androidboot.redroid_net_ndns=2 androidboot.redroid_net_dns1=223.5.5.5 androidboot.redroid_net_dns2=1.1.1.1
else docker -H "$socket" start yuna-redroid-phone; fi
printf '{"ready":false,"stage":"booting"}\n' > "$root/ready.json"
for task_try in $(seq 1 90); do
 "$adb" connect "$serial" >/dev/null 2>&1 || true
 if [ "$(timeout 8 "$adb" -s "$serial" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = 1 ]; then break; fi
 sleep 3
done
test "$(timeout 10 "$adb" -s "$serial" shell getprop sys.boot_completed | tr -d '\r')" = 1
"$adb" -s "$serial" shell getprop ro.product.cpu.abilist | grep -q arm64-v8a
# 国内节点使用可达的联网检测端点，避免Google检测失败让应用一直等网络。
"$adb" -s "$serial" shell settings put global captive_portal_https_url https://connectivitycheck.platform.hicloud.com/generate_204
"$adb" -s "$serial" shell settings put global captive_portal_http_url http://connectivitycheck.platform.hicloud.com/generate_204
"$adb" -s "$serial" shell settings put global private_dns_mode off

printf '{"ready":false,"stage":"installing"}\n' > "$root/ready.json"
timeout 600 "$adb" -s "$serial" install -r /YunaGames/Android/apks/xiaoheihe-1.3.398.apk
"$adb" -s "$serial" shell pm path com.max.xiaoheihe | grep -q package:
"$adb" -s "$serial" shell setprop ndk_translation.mode light-translate-or-interpret
"$adb" -s "$serial" shell setprop debug.hwui.renderer skiagl
"$adb" -s "$serial" shell am start -W -n com.max.xiaoheihe/.SplashActivity
"$adb" -s "$serial" exec-out screencap -p > "$root/logs/installed-app.png"
python3 - <<'PY'
from pathlib import Path
p=Path('/YunaGames/Android/bridge/phone_bridge.py');s=p.read_text()
if 'ENV["ANDROID_SERIAL"]' not in s:
 s=s.replace('\ndef token():','\nENV["ANDROID_SERIAL"] = "172.31.99.2:5555"\n\ndef token():');p.write_text(s)
PY
sh /YunaGames/Android/bridge/start.sh
printf '{"ready":true,"stage":"app_installed","serial":"172.31.99.2:5555"}\n' > "$root/ready.json"
echo 'ANDROID_CONTAINER_AND_APP_READY: login and human confirmation remain pending'
