# MeetKey PoC Specification

Created: 2026-06-22

## Goal

Build the first Raspberry Pi proof of concept for MeetKey as a dedicated meeting recording appliance.

The Raspberry Pi screen should stay focused on recording control. Meeting results, final save/delete decisions, and historical records should be handled from a phone or browser through QR links.

## Device Roles

### Raspberry Pi Screen

- Start a new recording.
- Show recording state.
- Pause and resume recording.
- Finish the recording and show a QR link for the current meeting.
- Show a local recording history list.
- Let the user pick a previous recording and show a QR link for that recording.
- Return to the main screen after 3 minutes or when the user taps `메인으로`.
- Show the connected microphone name in a small status line.

### Phone / Browser QR Pages

- Open the Raspberry Pi's saved meeting history page.
- Open the current meeting processing page.
- Show transcription and summary progress.
- Let the user choose `보관` or `삭제` for the Raspberry Pi record.

### AI Server

Target server:

```text
192.168.0.14
```

Planned pipeline:

```text
Raspberry Pi recording
-> Raspberry Pi 10-minute chunk files with short overlap
-> 192.168.0.14 server receives a temporary chunk
-> Whisper transcription
-> gemma4:31b chunk summary
-> chunk result copied back to Raspberry Pi
-> final chunk summaries merged into one meeting note
-> user views/saves/deletes the Raspberry Pi record from phone
```

The Raspberry Pi is the owner of recordings and meeting results. The AI server is treated as a processing engine.

## Hotspot / Phone Access

Current deployed network split:

```text
wlan0, built-in Wi-Fi: MeetKey hotspot, 10.42.0.1/24
wlan1, USB Realtek dongle: upstream Wi-Fi, YOUR_PI_UPSTREAM_IP/24
AI server route: 192.168.0.14 via wlan1
```

Phone QR policy for the appliance flow:

```text
QR content: WIFI:T:WPA;S:MeetKey;P:CHANGE_ME;H:false;;
Captive portal: http://10.42.0.1/* -> http://10.42.0.1:8000/records or current record
```

The phone still asks the user to approve joining the Wi-Fi network. After approval, iOS/Android captive portal checks should be redirected to the MeetKey page.

## Main Screen

The idle screen is the only screen that shows the meeting history QR.

Expected content:

```text
MeetKey

[ 녹음 시작 ]

녹음 기록
[history QR]

마이크: Anker PowerConf
```

Rules:

- `녹음 시작` is the main action.
- `녹음 기록` opens a local list of saved recordings.
- The QR below `녹음 기록` opens the Raspberry Pi's phone-facing meeting history page.
- The microphone status is small and secondary.
- If no microphone is detected and the user taps `녹음 시작`, show:

```text
마이크를 찾을 수 없습니다.

다시 연결한 뒤
녹음 시작을 눌러주세요.
```

## Recording Screen

Expected content:

```text
녹음중
00:03:21

[ 일시정지 ] [ 저장 ]
```

Rules:

- Screen border is green.
- No QR is shown while recording.
- `일시정지` pauses the recording.
- `저장` finishes the recording and moves to the current meeting QR screen.

## Paused Screen

Expected content:

```text
일시정지
00:03:21

[ 재개 ] [ 저장 ]
```

Rules:

- Screen border is red.
- No QR is shown while paused.
- `재개` resumes recording.
- `저장` finishes the recording and moves to the current meeting QR screen.

Implementation note:

For the PoC, pause/resume should record multiple WAV segments and merge them when the user taps `저장`.

## Current Meeting QR Screen

Expected content:

```text
[ 메인으로 ]

회의록 처리 QR
[current meeting QR]

휴대폰에서 전사/요약 진행 상황을 확인하세요.
03:00 후 메인 화면으로 이동
```

Rules:

- This is the only state where `메인으로` appears.
- QR opens the current meeting page served by the Raspberry Pi.
- The screen automatically returns to the main screen after 3 minutes.
- The user can tap `메인으로` to return immediately.
- The phone page is responsible for `보관` and `삭제`.

## Device Recording History Screen

Expected flow:

```text
Main
-> 녹음 기록
-> saved recording list
-> selected recording
-> selected recording QR
```

Rules:

- The list shows automatically generated titles when summary text is available.
- Before title generation finishes, use a timestamp-based fallback title.
- Each row shows title, created time, duration, and processing status.
- The selected recording screen shows only one QR for the selected record.
- The user can go back from selected record to list, and from list to main.

## QR Policy

Only one QR should be visible at a time.

```text
Idle: history QR
Recording: no QR
Paused: no QR
ProcessingReady: current meeting QR
RecordList: no QR
RecordDetail: selected record QR
```

## State Machine

```text
Idle
-> Recording
-> Paused
-> Recording
-> Saving
-> ProcessingReady
-> Idle
```

Error states:

- Microphone unavailable.
- Recording start failed.
- Segment merge failed.
- Server processing link unavailable.

## PoC Implementation Scope

Implemented in first PoC:

- Local Raspberry Pi web UI.
- Microphone detection.
- Start recording from Anker PowerConf.
- Pause and resume using WAV segments.
- Finish and merge segments.
- Show history QR on idle screen.
- Show device-side recording history list.
- Show selected record QR from device-side history.
- Show current meeting QR after finishing.
- 3-minute auto return.
- Manual `메인으로` return.
- Raspberry Pi phone-facing processing/history pages.
- Raspberry Pi local server and kiosk autostart setup.
- Raspberry Pi hotspot/AP mode with USB Wi-Fi as upstream.
- Captive portal redirect for Wi-Fi QR onboarding.
- 10-minute chunked recording and overlap processing.
- Chunk transcript/summary merge into final meeting note.
- Server upload receiver retained as fallback AI processing bridge.
- Mock processing mode for safe end-to-end integration tests.
- WhisperX/ASR API integration on `192.168.0.14`.
- Serialized server processing queue so Whisper/Ollama jobs do not run concurrently.

Deferred:

- GPIO hardware button.
- Robust retry queue for failed uploads.

## Initial Configuration

```text
Raspberry Pi IP: 192.168.0.41
AI server base URL: http://192.168.0.14:8080/meetkey
Device base URL: http://192.168.0.41:8000
Microphone device: plughw:CARD=PowerConf,DEV=0
Recording directory: /home/gunwoo/MeetKey_Recordings
Screen size: 800x480
QR display timeout: 180 seconds
```

## Current Server Status

As of 2026-06-22, `192.168.0.14` is running other services as well as MeetKey. The safe current MeetKey server mode is:

```text
Port: 8080
Path: /meetkey
App path: /data/datasets/meetkey_recorder/app
Data path: /data/datasets/meetkey_recorder/data
STT: mock
Summary: mock
Ollama summary model: gemma4:31b-it-q8_0-128k
Ollama summary context: 131072 tokens
Target transcription model: Whisper large-v3
Existing unrelated web service: 192.168.0.14:18899
WhisperX source/env path: /data/datasets/whisperx
ASR API: http://127.0.0.1:19000/v1/audio/transcriptions
```

The existing `192.168.0.14:18899` web service is separate and should not be restarted or repurposed for this PoC. MeetKey keeps its own WhisperX source/env under `/data/datasets/whisperx`, but currently uses the already-running local ASR API on `127.0.0.1:19000` because that service has `large-v3` loaded.

Updated implementation note:

- A dedicated `m-bain/whisperX` install lives under `/data/datasets/whisperx`.
- MeetKey calls `/data/datasets/meetkey_recorder/app/scripts/transcribe_with_asr_api.sh`.
- The first integration uses `large-v3`, Korean, and no diarization.
- The ASR model is served by the existing Docker-backed API at `127.0.0.1:19000`; MeetKey does not restart or modify that service.
