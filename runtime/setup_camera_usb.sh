#!/usr/bin/env bash
set -euo pipefail
if (( EUID != 0 )); then exec sudo bash "$0" "$@"; fi
root=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
install -m 644 "$root/udev/40-livo2-flir.rules" /etc/udev/rules.d/40-livo2-flir.rules
udevadm control --reload-rules
udevadm trigger --action=add --subsystem-match=usb --attr-match=idVendor=1e10 --attr-match=idProduct=4000
printf '1024\n' > /sys/module/usbcore/parameters/usbfs_memory_mb
printf 'FLIR USB 权限及缓冲初始化完成\n'
