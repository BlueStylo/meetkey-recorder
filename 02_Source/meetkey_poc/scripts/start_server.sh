#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

if [[ -f meetkey.pid ]] && kill -0 "$(cat meetkey.pid)" >/dev/null 2>&1; then
  echo "MeetKey server is already running."
  exit 0
fi

nohup python3 app.py > meetkey.log 2>&1 &
echo $! > meetkey.pid
echo "MeetKey server started: http://localhost:8000/device"
