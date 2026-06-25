#!/usr/bin/env bash
set -euo pipefail

if pgrep -af "[c]hromium.*http://localhost:8000/device" >/dev/null 2>&1; then
  pkill -f "[c]hromium.*http://localhost:8000/device"
  echo "MeetKey kiosk closed."
else
  echo "MeetKey kiosk is not open."
fi
