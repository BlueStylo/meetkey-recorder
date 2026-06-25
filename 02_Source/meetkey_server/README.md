# MeetKey Processing Server

This server receives WAV recordings from the Raspberry Pi and exposes the phone-facing QR pages.

## Run

```bash
cd /data/datasets/meetkey_recorder/app
./scripts/start_server.sh
```

Open:

```text
http://192.168.0.14:8080/meetkey/history
```

## Routes

- `POST /meetkey/api/sessions/{session_id}/audio`
- `POST /meetkey/api/chunks/process`
- `POST /meetkey/api/chunks/summarize`
- `GET /meetkey/api/sessions/{session_id}/download/audio`
- `GET /meetkey/api/sessions/{session_id}/download/transcript`
- `GET /meetkey/api/sessions/{session_id}/download/summary`
- `GET /meetkey/session/{session_id}`
- `GET /meetkey/history`
- `POST /meetkey/api/sessions/{session_id}/save`
- `POST /meetkey/api/sessions/{session_id}/delete`

## Processing

The server tries these STT options in order:

1. `MEETKEY_TRANSCRIBE_COMMAND`
2. `whisperx`
3. `whisper`

For integration testing without STT, run with:

```bash
MEETKEY_STT_MODE=mock MEETKEY_SUMMARY_MODE=mock python3 app.py
```

The summary step uses Ollama:

```text
http://127.0.0.1:11434
model: gemma4:31b-it-q8_0-128k on the 192.168.0.14 deployment
```

## 192.168.0.14 Notes

Current safe PoC config:

```text
config.192.168.0.14.mock.json
```

This keeps transcription and summary in mock mode, so Raspberry Pi upload, QR pages, save, delete, and history can be tested without running GPU-heavy work.

Real STT config template:

```text
config.192.168.0.14.faster-whisper.json
config.192.168.0.14.whisperx-large-v3.json
```

Before using it on the server:

```bash
cd /data/datasets/meetkey_recorder/app
./scripts/setup_stt_env.sh
cp config.192.168.0.14.faster-whisper.json config.json
./scripts/stop_server.sh
./scripts/start_server.sh
```

The faster-whisper wrapper writes `transcript.txt` to the session output directory. The high-quality target model is `large-v3` with CUDA `float16`.

If a `whisperx` executable is available to the MeetKey process, the built-in auto mode runs:

```bash
whisperx AUDIO --model large-v3 --language ko --device cuda --compute_type float16
```

Current WhisperX/ASR setup on `192.168.0.14`:

```text
Existing ASR API: http://127.0.0.1:19000/v1/audio/transcriptions
Loaded ASR model: large-v3
MeetKey wrapper: /data/datasets/meetkey_recorder/app/scripts/transcribe_with_asr_api.sh
Dedicated WhisperX source/env kept at: /data/datasets/whisperx/source, /data/datasets/whisperx/env
Ollama summary model: gemma4:31b-it-q8_0-128k
Ollama runtime context: 131072 tokens
```

MeetKey currently uses the existing Docker-backed ASR API because it already has `large-v3` loaded and avoids a second multi-GB model download. The wrapper posts the WAV with:

```bash
MEETKEY_ASR_URL=http://127.0.0.1:19000/v1/audio/transcriptions
MEETKEY_ASR_MODEL=large-v3
MEETKEY_ASR_LANGUAGE=ko
```

Word alignment and diarization are not enabled in the first MeetKey integration. Diarization can be added later with a Hugging Face token and model access approval.

The chunk APIs are intended for the Raspberry Pi-owned storage flow: the server receives a temporary chunk WAV, runs Whisper/Ollama, returns JSON, and deletes the temporary chunk workspace.

To protect the server while other services are running, MeetKey serializes processing with an in-process lock. If multiple recordings or chunks are uploaded, later jobs wait until the current Whisper/Ollama job finishes.

Do not restart the existing web service on `192.168.0.14:18899` for MeetKey deployment. MeetKey only calls the local ASR API on `127.0.0.1:19000` and otherwise leaves those Docker services untouched.

The service should live under `/data/datasets/meetkey_recorder/app`, and recordings/results should live under `/data/datasets/meetkey_recorder/data`, not under a small home directory.
