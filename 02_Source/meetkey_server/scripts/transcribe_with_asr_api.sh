#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 AUDIO_PATH OUT_DIR" >&2
  exit 2
fi

AUDIO_PATH="$1"
OUT_DIR="$2"

ASR_URL="${MEETKEY_ASR_URL:-http://127.0.0.1:19000/asr}"
ASR_MODEL="${MEETKEY_ASR_MODEL:-large-v3}"
ASR_LANGUAGE="${MEETKEY_ASR_LANGUAGE:-ko}"
ASR_TIMEOUT_SECONDS="${MEETKEY_ASR_TIMEOUT_SECONDS:-1800}"
ASR_DIARIZE="${MEETKEY_ASR_DIARIZE:-true}"
ASR_MIN_SPEAKERS="${MEETKEY_ASR_MIN_SPEAKERS:-1}"
ASR_MAX_SPEAKERS="${MEETKEY_ASR_MAX_SPEAKERS:-8}"
ASR_WORD_TIMESTAMPS="${MEETKEY_ASR_WORD_TIMESTAMPS:-true}"
ASR_INITIAL_PROMPT="${MEETKEY_ASR_INITIAL_PROMPT:-}"
ASR_HOTWORDS="${MEETKEY_ASR_HOTWORDS:-}"

mkdir -p "$OUT_DIR"

RESPONSE_PATH="$OUT_DIR/asr_response.json"
TRANSCRIPT_PATH="$OUT_DIR/transcript.txt"

REQUEST_URL="$(python3 - "$ASR_URL" "$ASR_LANGUAGE" "$ASR_MODEL" "$ASR_DIARIZE" "$ASR_MIN_SPEAKERS" "$ASR_MAX_SPEAKERS" "$ASR_WORD_TIMESTAMPS" "$ASR_INITIAL_PROMPT" "$ASR_HOTWORDS" <<'PY'
from __future__ import annotations

import sys
from urllib.parse import urlencode

base_url, language, model, diarize, min_speakers, max_speakers, word_timestamps, initial_prompt, hotwords = sys.argv[1:]
query = {
    "task": "transcribe",
    "language": language,
    "output_format": "json",
    "model": model,
    "diarize": diarize,
    "min_speakers": min_speakers,
    "max_speakers": max_speakers,
    "word_timestamps": word_timestamps,
}
if initial_prompt:
    query["initial_prompt"] = initial_prompt
if hotwords:
    query["hotwords"] = hotwords
separator = "&" if "?" in base_url else "?"
print(base_url + separator + urlencode(query))
PY
)"

curl -fsS --max-time "$ASR_TIMEOUT_SECONDS" -X POST "$REQUEST_URL" \
  -F "audio_file=@$AUDIO_PATH" \
  > "$RESPONSE_PATH"

python3 - "$RESPONSE_PATH" "$TRANSCRIPT_PATH" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

response_path = Path(sys.argv[1])
transcript_path = Path(sys.argv[2])
payload = json.loads(response_path.read_text(encoding="utf-8"))


def as_seconds(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_time(value: object) -> str:
    seconds = as_seconds(value)
    if seconds is None:
        return "--:--"
    seconds = max(0, int(round(seconds)))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"


def speaker_for(segment: dict) -> str:
    direct = str(segment.get("speaker") or "").strip()
    if direct:
        return direct
    counts: dict[str, int] = {}
    for word in segment.get("words") or []:
        speaker = str(word.get("speaker") or "").strip()
        if speaker:
            counts[speaker] = counts.get(speaker, 0) + 1
    if counts:
        return max(counts, key=counts.get)
    return "SPEAKER_UNKNOWN"


segments = payload.get("segments")
if not isinstance(segments, list):
    text_value = payload.get("text")
    segments = text_value if isinstance(text_value, list) else []

lines: list[str] = []
for segment in segments:
    if not isinstance(segment, dict):
        continue
    text = str(segment.get("text") or "").strip()
    if not text:
        continue
    start = format_time(segment.get("start"))
    end = format_time(segment.get("end"))
    speaker = speaker_for(segment)
    lines.append(f"[{speaker}] {start}-{end}\n{text}")

if lines:
    text = "\n\n".join(lines)
else:
    text_value = payload.get("text", "")
    text = str(text_value).strip()

if not text:
    raise SystemExit("ASR API response did not contain text")

transcript_path.write_text(text + "\n", encoding="utf-8")
print(text)
PY
