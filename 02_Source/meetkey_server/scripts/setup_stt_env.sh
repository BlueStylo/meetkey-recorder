#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

python3 -m venv .venv-stt
. .venv-stt/bin/activate

python -m pip install --upgrade pip
python -m pip install -r requirements-stt.txt

echo "MeetKey STT environment is ready: $APP_DIR/.venv-stt"
