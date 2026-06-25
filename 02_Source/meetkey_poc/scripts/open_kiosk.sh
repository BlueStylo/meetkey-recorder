#!/usr/bin/env bash
set -euo pipefail

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"

if ps -C chromium -o args= 2>/dev/null | grep -F -- "--app=http://localhost:8000/device" >/dev/null; then
  echo "MeetKey kiosk is already open."
  exit 0
fi

for _ in $(seq 1 30); do
  if curl -fsS http://localhost:8000/api/status >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

nohup chromium \
  --ozone-platform=wayland \
  --user-data-dir="$HOME/.cache/meetkey-chromium" \
  --password-store=basic \
  --no-first-run \
  --disable-sync \
  --disable-translate \
  --disable-features=Translate \
  --kiosk \
  --app=http://localhost:8000/device \
  --noerrdialogs \
  --disable-infobars \
  --disable-session-crashed-bubble \
  > "$HOME/meetkey_poc/chromium.log" 2>&1 &

echo "MeetKey kiosk opened."
