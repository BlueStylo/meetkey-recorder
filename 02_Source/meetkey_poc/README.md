# MeetKey PoC

This is the first local Raspberry Pi proof of concept for the MeetKey recorder screen.

## Run

```bash
cd ~/meetkey_poc
./scripts/start_server.sh
```

Open:

```text
http://192.168.0.41:8000/device
```

For the Raspberry Pi screen, open Chromium in kiosk mode:

```bash
./scripts/open_kiosk.sh
```

Stop them with:

```bash
./scripts/close_kiosk.sh
./scripts/stop_server.sh
```

## Autostart

The Raspberry Pi deployment uses:

- `systemd/meetkey-server.service` for the local recording server.
- `labwc/autostart` to open Chromium kiosk mode when the desktop session starts.

The kiosk script waits for `http://localhost:8000/api/status` before opening the screen.

## Current Scope

- Detects Anker PowerConf through ALSA.
- Records WAV audio with `arecord`.
- Supports pause and resume as multiple WAV segments.
- Supports canceling a paused recording without uploading or saving it.
- Merges WAV segments after tapping `저장`.
- Stores meeting records under the Raspberry Pi recordings directory.
- Serves phone-facing record pages directly from the Raspberry Pi.
- Processes long recordings in 10-minute chunks with a short overlap, then merges chunk transcripts/summaries into the final meeting note.
- Uses the MeetKey processing server as an AI engine; recordings and final results remain owned by the Raspberry Pi.
- Shows the local history QR on the idle screen.
- Shows the local current meeting QR after finishing.
- Shows a device-side recording history list, then a selected record QR.
- Returns to the main screen after 3 minutes.
- Runs `wlan0` as a `MeetKey` hotspot at `10.42.0.1`.
- Uses the USB Wi-Fi dongle `wlan1` as upstream to `Server Test WiFi` and the AI server.
- Uses a captive portal redirect on port 80 so phone OS captive checks open the MeetKey records/current meeting page.

## Deferred

- Hardware button through GPIO.
- Robust retry queue for failed server uploads.

## Hotspot Mode

```text
SSID: MeetKey
Password: CHANGE_ME
Pi AP URL: http://10.42.0.1:8000/records
SSH/admin Wi-Fi IP: YOUR_PI_UPSTREAM_IP
```

Current interface split:

```text
wlan0 -> MeetKey-Hotspot, 10.42.0.1/24
wlan1 -> upstream Wi-Fi, YOUR_PI_UPSTREAM_IP/24
```
