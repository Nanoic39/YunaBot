#!/bin/bash
set -euo pipefail
root=/YunaGames/Android/redroid
if [ -f "$root/run/dockerd.pid" ] && kill -0 "$(cat "$root/run/dockerd.pid")" 2>/dev/null; then
 echo 'Private daemon already running; preserve current session' >&2
 exit 1
fi
if [ ! -d /sys/module/binder_linux ]; then
 /sbin/insmod "$root/kernel/lib/modules/6.17.0-1021-azure/kernel/drivers/android/binder_linux.ko.zst" devices=binder,hwbinder,vndbinder
fi
if [ -L /etc/sysctl.d/90-yuna-android.conf ]; then
 /sbin/sysctl -p "$root/sysctl/90-yuna-android.conf"
fi
rm -f "$root/run/dockerd.pid"
exec bash "$root/yuna-redroid-daemon.sh"
