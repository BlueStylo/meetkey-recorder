#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path

from faster_whisper import WhisperModel


def env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip() or default


def main() -> None:
    parser = argparse.ArgumentParser(description="Transcribe a MeetKey WAV file with faster-whisper.")
    parser.add_argument("--audio", required=True, help="Input WAV path")
    parser.add_argument("--out-dir", required=True, help="Directory for transcript.txt")
    args = parser.parse_args()

    audio_path = Path(args.audio).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    model_name = env("MEETKEY_FASTER_WHISPER_MODEL", "base")
    device = env("MEETKEY_FASTER_WHISPER_DEVICE", "cuda")
    compute_type = env("MEETKEY_FASTER_WHISPER_COMPUTE_TYPE", "float16")
    language = env("MEETKEY_WHISPER_LANGUAGE", "ko")
    beam_size = int(env("MEETKEY_WHISPER_BEAM_SIZE", "5"))

    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    segments, info = model.transcribe(
        str(audio_path),
        language=language,
        beam_size=beam_size,
        vad_filter=True,
    )

    lines = [
        "# Transcript",
        "",
        f"- language: {info.language}",
        f"- language_probability: {info.language_probability:.3f}",
        "",
    ]
    for segment in segments:
        text = segment.text.strip()
        if text:
            lines.append(f"[{segment.start:0.2f} -> {segment.end:0.2f}] {text}")

    transcript = "\n".join(lines).strip() + "\n"
    (out_dir / "transcript.txt").write_text(transcript, encoding="utf-8")
    print(transcript)


if __name__ == "__main__":
    main()
