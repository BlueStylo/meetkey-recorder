#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

if [[ -f meetkey.pid ]] && kill -0 "$(cat meetkey.pid)" >/dev/null 2>&1; then
  kill "$(cat meetkey.pid)"
  rm -f meetkey.pid
  echo "MeetKey server stopped."
else
  echo "MeetKey server is not running."
fi
