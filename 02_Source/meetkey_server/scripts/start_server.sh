#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

if [[ -f meetkey_server.pid ]] && kill -0 "$(cat meetkey_server.pid)" >/dev/null 2>&1; then
  echo "MeetKey processing server is already running."
  exit 0
fi

nohup python3 app.py > meetkey_server.log 2>&1 &
echo $! > meetkey_server.pid
echo "MeetKey processing server started: http://localhost:8080/meetkey/history"
