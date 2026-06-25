#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

if [[ -f meetkey_server.pid ]] && kill -0 "$(cat meetkey_server.pid)" >/dev/null 2>&1; then
  kill "$(cat meetkey_server.pid)"
  rm -f meetkey_server.pid
  echo "MeetKey processing server stopped."
else
  echo "MeetKey processing server is not running."
fi
