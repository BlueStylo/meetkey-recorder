# MeetKey Recorder

![MeetKey Recorder running on a Raspberry Pi touchscreen](assets/meetkey-device.jpg)

MeetKey Recorder is a Raspberry Pi based meeting recorder prototype. It turns a small touchscreen device and a USB conference microphone into a meeting capture appliance: tap record, save the session locally, scan a QR code, and review the audio, transcript, and meeting summary from a phone.

This repository contains the Raspberry Pi recorder UI, the phone-facing local web pages, and a companion processing server that runs speech-to-text and LLM summarization.

## What It Does

- Runs full-screen on a Raspberry Pi touchscreen in kiosk mode.
- Records audio from an Anker PowerConf USB conference microphone.
- Shows recording, pause, save, cancel, and live audio level states on the device.
- Stores the original WAV file on the Raspberry Pi.
- Splits long recordings into timed chunks with overlap for faster perceived processing.
- Sends temporary chunks to a processing server for Whisper transcription and Gemma summarization.
- Serves phone-friendly record pages directly from the Raspberry Pi.
- Shows QR flows for Wi-Fi connection and record access.
- Lets users download the original audio, transcript, and summary.
- Keeps the processing server as a temporary AI worker; the Raspberry Pi remains the owner of saved records.

## Current Hardware

- Raspberry Pi 4
- 5-inch touchscreen
- Anker PowerConf A3301 USB conference microphone
- USB Wi-Fi dongle for upstream network access
- Raspberry Pi built-in Wi-Fi configured as the MeetKey hotspot

## Architecture

```text
Raspberry Pi touchscreen
  -> local recorder UI
  -> stores original WAV and final meeting records
  -> serves phone pages and QR flows

Phone
  -> joins MeetKey hotspot
  -> opens record/history page by QR
  -> reviews or downloads results

Processing server
  -> receives temporary audio chunks
  -> runs Whisper STT
  -> runs Gemma/Ollama summary
  -> returns transcript and summary JSON
```

## Repository Layout

```text
02_Source/meetkey_poc/
  Raspberry Pi recorder app, kiosk UI, phone pages, systemd units, hotspot scripts

02_Source/meetkey_server/
  Processing server, STT wrappers, history/session pages, download/save/delete APIs

06_Docs/
  Product notes, PoC spec, and summary prompt references

assets/
  README images
```

## Raspberry Pi App

The Pi app is a small Python HTTP server with static HTML/CSS/JS screens.

```bash
cd 02_Source/meetkey_poc
cp config.example.json config.json
python3 app.py
```

Open the device UI:

```text
http://localhost:8000/device
```

The deployed appliance uses systemd and Chromium kiosk mode:

```bash
./scripts/start_server.sh
./scripts/open_kiosk.sh
```

## Processing Server

The processing server receives audio from the Raspberry Pi and returns meeting results.

```bash
cd 02_Source/meetkey_server
cp config.example.json config.json
python3 app.py
```

For real transcription, configure one of:

- an external transcription command
- WhisperX
- faster-whisper
- a local ASR API wrapper

For lightweight integration tests:

```bash
MEETKEY_STT_MODE=mock MEETKEY_SUMMARY_MODE=mock python3 app.py
```

## Configuration

Runtime config files are intentionally ignored by git.

Use the example files as templates:

```text
02_Source/meetkey_poc/config.example.json
02_Source/meetkey_server/config.example.json
```

Do not commit real Wi-Fi passwords, server addresses, recordings, transcripts, or local deployment files.

## Prototype Status

Working end to end:

- Raspberry Pi recording flow
- local WAV storage
- pause/resume/cancel/save
- phone QR access
- Markdown-rendered transcript and summary pages
- chunked transcription/summarization flow
- audio/transcript/summary downloads
- private processing server integration

Still planned:

- more robust retry queue for failed chunk/server jobs
- stronger record lifecycle controls
- hardware button integration through GPIO
- diarization and word alignment improvements
- installation automation for a fresh Raspberry Pi

## Notes

This is an active proof of concept, not a packaged product. The current goal is to validate the meeting-room workflow and the local-device UX before hardening installation, security, and long-term storage.
