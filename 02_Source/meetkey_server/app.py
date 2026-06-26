#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
STATIC_VERSION = "20260625-3"

DEFAULT_CONFIG = {
    "host": "0.0.0.0",
    "port": 8080,
    "base_path": "/meetkey",
    "data_dir": str(Path.home() / "MeetKey_Server"),
    "ollama_url": "http://127.0.0.1:11434",
    "ollama_model": "gemma4:31b",
    "stt_mode": "auto",
    "summary_mode": "ollama",
    "transcribe_command": "",
    "whisper_model": "large-v3",
    "whisper_device": "cuda",
    "whisper_compute_type": "float16",
}


def load_config() -> dict:
    config = dict(DEFAULT_CONFIG)
    config_path = ROOT / "config.json"
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as f:
            config.update(json.load(f))
    for key in list(config):
        env_key = f"MEETKEY_{key.upper()}"
        if env_key in os.environ:
            value = os.environ[env_key]
            if isinstance(config[key], int):
                value = int(value)
            config[key] = value
    config["base_path"] = "/" + str(config["base_path"]).strip("/")
    return config


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def format_seconds(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"


def safe_session_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", value.strip())
    return cleaned[:96] or time.strftime("%Y%m%d_%H%M%S")


def read_json(path: Path, fallback: dict | None = None) -> dict:
    if not path.exists():
        return dict(fallback or {})
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


@dataclass
class Paths:
    session_dir: Path
    audio: Path
    metadata: Path
    transcript: Path
    summary: Path


class MeetKeyServer:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.data_dir = Path(config["data_dir"]).expanduser()
        self.sessions_dir = self.data_dir / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.processing_lock = threading.Lock()

    def paths(self, session_id: str) -> Paths:
        session_dir = self.sessions_dir / safe_session_id(session_id)
        return Paths(
            session_dir=session_dir,
            audio=session_dir / "audio.wav",
            metadata=session_dir / "metadata.json",
            transcript=session_dir / "transcript.md",
            summary=session_dir / "summary.md",
        )

    def public_session_url(self, session_id: str) -> str:
        return f'{self.config["base_path"]}/session/{quote(session_id)}'

    def create_or_update_session(self, session_id: str, headers: dict, body_reader) -> dict:
        session_id = safe_session_id(session_id)
        paths = self.paths(session_id)
        paths.session_dir.mkdir(parents=True, exist_ok=True)

        metadata = read_json(paths.metadata, {"session_id": session_id})
        length = int(headers.get("content-length", "0") or "0")
        metadata.update(
            {
                "session_id": session_id,
                "status": "uploading",
                "status_label": "녹음 파일 업로드 중",
                "updated_at": now_iso(),
                "created_at": metadata.get("created_at") or now_iso(),
                "saved": bool(metadata.get("saved", False)),
                "deleted": False,
                "source": headers.get("x-meetkey-source", "raspberry-pi"),
                "elapsed_seconds": headers.get("x-meetkey-elapsed-seconds", ""),
                "audio_path": str(paths.audio),
                "expected_audio_size": length,
                "session_url": self.public_session_url(session_id),
            }
        )
        write_json(paths.metadata, metadata)

        if length <= 0:
            metadata.update(
                {
                    "status": "error",
                    "status_label": "업로드 실패",
                    "error": "Content-Length가 없어 녹음 파일 크기를 확인할 수 없습니다.",
                    "updated_at": now_iso(),
                }
            )
            write_json(paths.metadata, metadata)
            raise RuntimeError(metadata["error"])

        received = 0
        with paths.audio.open("wb") as f:
            remaining = length
            while remaining > 0:
                chunk = body_reader(min(1024 * 1024, remaining))
                if not chunk:
                    break
                f.write(chunk)
                received += len(chunk)
                remaining -= len(chunk)

        metadata["audio_size"] = paths.audio.stat().st_size
        metadata["received_audio_size"] = received
        if received != length:
            metadata.update(
                {
                    "status": "error",
                    "status_label": "업로드 실패",
                    "error": f"녹음 파일이 일부만 업로드되었습니다. expected={length} received={received}",
                    "updated_at": now_iso(),
                }
            )
            write_json(paths.metadata, metadata)
            raise RuntimeError(metadata["error"])

        metadata.update(
            {
                "status": "uploaded",
                "status_label": "녹음 파일 업로드 완료",
                "error": "",
                "updated_at": now_iso(),
            }
        )
        write_json(paths.metadata, metadata)
        threading.Thread(target=self.process_session, args=(session_id,), daemon=True).start()
        return self.session_payload(session_id)

    def process_session(self, session_id: str) -> None:
        paths = self.paths(session_id)
        try:
            self._set_status(session_id, "queued", "처리 대기 중")
            with self.processing_lock:
                self._set_status(session_id, "transcribing", "Whisper 전사 진행 중")
                transcript = self.transcribe(paths)
                paths.transcript.write_text(transcript, encoding="utf-8")

                self._set_status(session_id, "summarizing", "gemma4 요약 진행 중")
                summary = self.summarize(transcript)
                paths.summary.write_text(summary, encoding="utf-8")

                metadata = read_json(paths.metadata, {"session_id": session_id})
                metadata.update(
                    {
                        "status": "ready",
                        "status_label": "회의록 생성 완료",
                        "updated_at": now_iso(),
                        "transcript_path": str(paths.transcript),
                        "summary_path": str(paths.summary),
                        "summary_model": self.config["ollama_model"],
                    }
                )
                write_json(paths.metadata, metadata)
        except Exception as exc:
            metadata = read_json(paths.metadata, {"session_id": session_id})
            metadata.update(
                {
                    "status": "error",
                    "status_label": "처리 실패",
                    "error": str(exc),
                    "updated_at": now_iso(),
                }
            )
            write_json(paths.metadata, metadata)

    def process_chunk(self, headers: dict, body_reader) -> dict:
        chunk_id = safe_session_id(headers.get("x-meetkey-chunk-id", f"chunk_{time.strftime('%Y%m%d_%H%M%S')}"))
        session_id = safe_session_id(headers.get("x-meetkey-session-id", "adhoc"))
        index = int(headers.get("x-meetkey-chunk-index", "0") or "0")
        start_seconds = float(headers.get("x-meetkey-chunk-start-seconds", "0") or "0")
        length = int(headers.get("content-length", "0") or "0")
        if length <= 0:
            raise RuntimeError("Content-Length가 없어 조각 오디오 크기를 확인할 수 없습니다.")

        chunk_root = self.data_dir / "chunk_jobs"
        chunk_root.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(tempfile.mkdtemp(prefix=f"{chunk_id}_", dir=str(chunk_root)))
        paths = Paths(
            session_dir=temp_dir,
            audio=temp_dir / "audio.wav",
            metadata=temp_dir / "metadata.json",
            transcript=temp_dir / "transcript.md",
            summary=temp_dir / "summary.md",
        )
        try:
            received = 0
            with paths.audio.open("wb") as f:
                remaining = length
                while remaining > 0:
                    chunk = body_reader(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    f.write(chunk)
                    received += len(chunk)
                    remaining -= len(chunk)
            if received != length:
                raise RuntimeError(f"조각 오디오가 일부만 업로드되었습니다. expected={length} received={received}")

            duration_seconds = self._audio_duration_seconds(paths.audio)
            write_json(
                paths.metadata,
                {
                    "session_id": session_id,
                    "chunk_id": chunk_id,
                    "chunk_index": index,
                    "chunk_start_seconds": start_seconds,
                    "chunk_end_seconds": start_seconds + duration_seconds,
                    "duration_seconds": duration_seconds,
                    "audio_size": paths.audio.stat().st_size,
                    "created_at": now_iso(),
                },
            )

            with self.processing_lock:
                transcript = self.transcribe(paths)
                paths.transcript.write_text(transcript, encoding="utf-8")
                summary = self.summarize_chunk(transcript, index, start_seconds, duration_seconds)
                paths.summary.write_text(summary, encoding="utf-8")

            return {
                "ok": True,
                "session_id": session_id,
                "chunk_id": chunk_id,
                "chunk_index": index,
                "chunk_start_seconds": start_seconds,
                "chunk_end_seconds": start_seconds + duration_seconds,
                "duration_seconds": duration_seconds,
                "audio_size": paths.audio.stat().st_size,
                "transcript": transcript,
                "summary": summary,
                "summary_model": self.config["ollama_model"],
                "processed_at": now_iso(),
            }
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _audio_duration_seconds(self, audio_path: Path) -> float:
        try:
            with wave.open(str(audio_path), "rb") as audio:
                frame_rate = audio.getframerate()
                if frame_rate <= 0:
                    return 0.0
                return audio.getnframes() / float(frame_rate)
        except Exception:
            return 0.0

    def summarize_chunk(self, transcript: str, index: int, start_seconds: float, duration_seconds: float) -> str:
        if str(self.config.get("summary_mode", "ollama")).lower() == "mock":
            return (
                "## 구간 메타\n"
                f"- 구간 번호: {index}\n"
                f"- 시간 범위: {format_seconds(start_seconds)} ~ {format_seconds(start_seconds + duration_seconds)}\n"
                "- 구간 성격: mock\n\n"
                "## 구간 핵심 요약\n"
                "이 구간은 MeetKey chunk 처리 테스트용 mock 요약입니다.\n"
            )

        prompt = f"""
다음은 긴 회의를 10분 단위로 나눈 한 구간의 Whisper 전사문입니다.
이 출력은 최종 회의록이 아니라, 나중에 전체 회의록으로 병합하기 위한 “구간 요약 데이터”입니다.

공통 원칙:
- 원문에 없는 결론, 담당자, 기한, 적용 범위를 만들지 마세요.
- 결정 / 잠정 합의 / 결정 후보 / 검토 필요 / 제안 / 단순 언급을 구분하세요.
- 수치, 날짜, 고유명사, 기술 용어는 맥락과 확정성을 함께 기록하세요.
- 중복 발화와 추임새는 제거하되, 쟁점과 입장 차이는 보존하세요.

목표:
이 구간에서 나온 논점, 결정 후보, 액션 후보, 수치, 용어, 불확실성을 구조화하세요.
문장을 예쁘게 다듬기보다 최종 병합에 필요한 정보를 정확히 남기는 것을 우선하세요.

중요 원칙:
1. 이 구간만 보고 전체 회의 결론을 단정하지 마세요.
2. 결정처럼 들리는 내용도 명시적 합의가 약하면 “결정 후보” 또는 “검토 필요”로 표시하세요.
3. 담당자, 기한, 적용 범위가 명시되지 않았다면 임의로 만들지 마세요.
4. 수치값은 반드시 맥락과 함께 기록하세요.
5. 전사 오류 가능성이 있는 용어는 보정하되, 확신이 낮으면 “추정”으로 표시하세요.
6. 다음 구간으로 이어질 것 같은 미완성 논의는 “이어지는 논의”에 따로 적으세요.
7. 구간 내 중요한 발화의 뉘앙스나 입장 차이를 지나치게 평탄화하지 마세요.

출력 형식:

## 구간 메타
- 구간 번호: {index}
- 시간 범위: {format_seconds(start_seconds)} ~ {format_seconds(start_seconds + duration_seconds)}
- 주요 화자: 확인 가능 시 작성, 불명확하면 “화자 불명”
- 구간 성격: 신규 주제 / 기존 주제 심화 / 의사결정 논의 / 잡담·부가 이슈 / 혼합 중 선택

## 구간 핵심 요약
이 구간의 핵심을 3~5문장으로 요약하세요.
확정되지 않은 내용은 “논의됨”, “제안됨”, “검토 필요”로 표현하세요.

## 주제별 정리

### [주제명]
- 내용 요약:
- 쟁점:
- 관련 입장:
  - 입장 A:
  - 입장 B:
- 확정성: 확정 / 잠정 합의 / 결정 후보 / 검토 필요 / 제안 / 단순 언급 중 하나
- 후속 확인 필요:

필요한 만큼 주제를 추가하세요.

## 결정 후보
이 구간에서 결정처럼 들린 내용만 적으세요.
명확한 결정이 아니면 “결정 후보” 또는 “검토 필요”로 표시하세요.

| 번호 | 내용 | 확정성 | 근거/맥락 | 적용 범위 |
| -- | -- | -- | -- | -- |

## 액션 후보
담당자와 기한이 명확하지 않으면 “담당 미정”, “기한 미정”으로 표시하세요.

| 번호 | 액션 후보 | 담당자 | 기한 | 확정성 | 비고 |
| -- | -- | -- | -- | -- | -- |

## 수치 / 날짜 / 고유명사
수치, 날짜, 병원명, 제품명, 파일명, 사람 이름 등 최종 병합에서 중요한 정보를 적으세요.

| 항목 | 값 | 맥락 | 확정성 |
| -- | -- | -- | -- |

## 전사 오류 및 용어 보정 후보

| 전사 표현 | 보정 가능 표현 | 확신도 | 이유 |
| -- | -- | -- | -- |

## 이어지는 논의
다음 구간에서 계속 확인해야 할 내용을 적으세요.

## 구간 중요도
- 중요도: 높음 / 중간 / 낮음
- 이유:

전사문:
{transcript}
""".strip()
        return self._ollama_generate(prompt, timeout_seconds=60 * 20)

    def summarize_chunks(self, payload: dict) -> dict:
        if str(self.config.get("summary_mode", "ollama")).lower() == "mock":
            return {
                "summary": (
                    "# 회의 요약\n\n"
                    "## 핵심 요약\n"
                    "- 조각 처리 결과를 합쳐 최종 요약을 생성하는 mock 결과입니다.\n\n"
                    "## 한 줄 요약\n"
                    "MeetKey 조각 처리 파이프라인이 정상 동작했습니다.\n"
                ),
                "summary_model": "mock",
            }

        chunks = payload.get("chunks") or []
        transcript = str(payload.get("transcript") or "").strip()
        lines: list[str] = []
        for item in chunks:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or f"{item.get('index', '?')}구간")
            summary = str(item.get("summary") or "").strip()
            if summary:
                lines.append(f"### {label}\n{summary}")
        chunk_summaries = "\n\n".join(lines).strip()
        if not chunk_summaries and not transcript:
            raise RuntimeError("최종 요약에 사용할 구간 요약 또는 전사문이 없습니다.")

        prompt = f"""
아래는 하나의 긴 회의를 10분 단위로 나누어 요약한 결과입니다.
구간 사이에는 오버랩이 있어 반복 내용이 포함될 수 있습니다.
또한 각 구간 요약은 전체 맥락 없이 작성되었으므로, 일부 내용이 과확정되었거나 후속 구간에서 수정되었을 수 있습니다.

가능하면 전체 전사문도 함께 참고하세요.
전체 전사문이 제공된 경우, 결정 사항·액션 아이템·수치·담당자·기한은 전체 전사문과 구간 요약을 함께 검토하여 보수적으로 확정하세요.

목표:
전체 회의의 흐름에 따라 한국어 회의록을 통합 재구성하세요.
단순히 구간 요약을 이어붙이지 말고, 중복을 제거하고 주제별로 재분류하세요.

최우선 원칙:
1. 구간 요약은 전체 맥락 없이 작성되었기 때문에 일부 항목이 과확정되었을 수 있습니다.
2. 최종 병합 단계에서는 구간 요약의 “결정 사항”도 다시 검토하고, 근거가 약하면 “검토 필요”로 낮추세요.
3. 반복 언급된 주제는 중요도가 높은 것으로 보되, 반복되었다는 이유만으로 결정 사항으로 승격하지 마세요.
4. 명시적으로 합의되거나 지시된 내용만 “결정 사항”으로 작성하세요.
5. 단순 제안, 아이디어, 우려, 가능성, 검토 의견은 “검토 필요” 또는 “제안”으로 유지하세요.
6. 구간 요약 중 하나라도 “검토”, “제안”, “후속 확인”으로 표시한 내용은 최종본에서 확정으로 바꾸지 마세요.
7. 담당자, 기한, 적용 범위는 명확한 근거가 있을 때만 작성하세요.
8. “즉시 적용”, “전 케이스 적용”, “확정”, “완료”, “반드시” 같은 단정 표현은 매우 엄격하게 사용하세요.
9. 수치값은 반드시 적용 범위와 확정성을 함께 작성하세요.
10. 서로 충돌하는 내용이 있으면 하나를 임의 선택하지 말고 “상충/해석 필요”로 표시하세요.
11. 회의록을 보기 좋게 만들기 위해 원문에 없는 결론을 보강하지 마세요.
12. 최종 회의록은 실무자가 바로 읽을 수 있게 간결하게 작성하되, 쟁점과 불확실성은 삭제하지 마세요.

# 회의록

## 1. 회의 개요
- 회의 성격:
- 주요 목적:
- 핵심 주제:
- 전체 결론:
  - 확정된 내용:
  - 방향성이 잡힌 내용:
  - 추가 검토가 필요한 내용:

## 2. 회의 흐름 요약
회의가 어떤 순서로 전개되었는지 5~8문장으로 설명하세요.
중간에 다른 업무 이슈로 전환된 경우도 자연스럽게 표시하세요.

## 3. 핵심 논의 사항

### 3.1 [주제명]
- 논의 배경:
- 주요 내용:
- 쟁점:
- 각 입장:
  - 관점 A:
  - 관점 B:
- 현재 정리:
- 확정성: 확정 / 잠정 합의 / 검토 필요 / 제안 / 단순 언급 중 하나
- 남은 이슈:

필요한 만큼 주제를 추가하세요.

## 4. 결정 사항
아래 조건을 만족하는 항목만 포함하세요.
- 회의에서 명확히 합의되었거나 지시됨
- 적용 범위가 비교적 분명함
- 단순 제안이나 아이디어가 아님

| 번호 | 결정 사항 | 근거 / 배경 | 적용 범위 | 주의할 점 |
| -- | -- | -- | -- | -- |

결정 사항이 명확하지 않은 경우, 억지로 채우지 말고 “명확한 결정 사항 없음”이라고 작성하세요.

## 5. 액션 아이템
담당자와 기한을 임의 생성하지 마세요.

| 번호 | 액션 | 담당자 | 기한 | 확정성 | 비고 |
| -- | -- | -- | -- | -- | -- |

확정성 값:
- 확정
- 잠정
- 검토 필요
- 제안
- 담당 미정

## 6. 리스크 및 확인 필요 사항

| 구분 | 내용 | 영향 | 대응 방향 | 확정성 |
| -- | -- | -- | -- | -- |

## 7. 보류되었거나 추후 논의할 내용

## 8. 다음 회의에서 확인할 질문
- 질문 1:
- 질문 2:
- 질문 3:

## 9. 용어 및 음성인식 보정

| 전사 표현 | 보정 가능 표현 | 확신도 | 근거 / 비고 |
| -- | -- | -- | -- |

## 10. 한 줄 요약
회의의 본질을 한 문장으로 요약하세요.
확정되지 않은 내용을 확정처럼 쓰지 마세요.

마지막 자체 검토:
1. 결정 사항 표에 “제안”, “아이디어”, “검토 필요”가 섞여 있지 않은지 확인하세요.
2. 액션 아이템에 원문에 없는 담당자나 기한을 만들지 않았는지 확인하세요.
3. 수치값의 적용 범위를 과장하지 않았는지 확인하세요.
4. “확정”, “즉시 적용”, “전 케이스 적용” 같은 표현을 사용했다면 근거가 충분한지 확인하세요.
5. 근거가 약한 항목은 결정 사항에서 제거하고 “검토 필요”로 이동하세요.

구간별 요약:
{chunk_summaries}

전체 전사문:
{transcript}
""".strip()
        with self.processing_lock:
            summary = self._ollama_generate(prompt, timeout_seconds=60 * 20)
        return {"summary": summary, "summary_model": self.config["ollama_model"]}

    def transcribe(self, paths: Paths) -> str:
        mode = str(self.config["stt_mode"]).lower()
        if mode == "mock":
            return (
                "# Mock Transcript\n\n"
                "- MeetKey 녹음 업로드가 정상적으로 들어왔습니다.\n"
                "- Whisper 서버 배포 전 통합 테스트용 전사문입니다.\n"
            )

        custom_command = str(self.config.get("transcribe_command") or "").strip()
        if custom_command:
            return self._run_custom_transcribe(custom_command, paths)

        if shutil.which("whisperx"):
            out_dir = paths.session_dir / "whisperx"
            out_dir.mkdir(exist_ok=True)
            cmd = [
                "whisperx",
                str(paths.audio),
                "--model",
                str(self.config["whisper_model"]),
                "--language",
                "ko",
                "--device",
                str(self.config["whisper_device"]),
                "--compute_type",
                str(self.config["whisper_compute_type"]),
                "--output_format",
                "txt",
                "--output_dir",
                str(out_dir),
            ]
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=60 * 60)
            return self._read_first_text(out_dir)

        if shutil.which("whisper"):
            out_dir = paths.session_dir / "whisper"
            out_dir.mkdir(exist_ok=True)
            cmd = [
                "whisper",
                str(paths.audio),
                "--language",
                "Korean",
                "--model",
                str(self.config["whisper_model"]),
                "--output_format",
                "txt",
                "--output_dir",
                str(out_dir),
            ]
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=60 * 60)
            return self._read_first_text(out_dir)

        raise RuntimeError("whisperx 또는 whisper 명령을 찾을 수 없습니다.")

    def _run_custom_transcribe(self, command_template: str, paths: Paths) -> str:
        out_dir = paths.session_dir / "custom_transcribe"
        out_dir.mkdir(exist_ok=True)
        command = command_template.format(audio=str(paths.audio), out_dir=str(out_dir))
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True, timeout=60 * 60)
        text_files = sorted(out_dir.glob("*.txt")) + sorted(out_dir.glob("*.md"))
        if text_files:
            return text_files[0].read_text(encoding="utf-8", errors="replace")
        return result.stdout.strip()

    def _read_first_text(self, out_dir: Path) -> str:
        files = sorted(out_dir.glob("*.txt"))
        if not files:
            raise RuntimeError("전사 결과 txt 파일을 찾을 수 없습니다.")
        return files[0].read_text(encoding="utf-8", errors="replace").strip()

    def summarize(self, transcript: str) -> str:
        if str(self.config.get("summary_mode", "ollama")).lower() == "mock":
            return (
                "# 회의 요약\n\n"
                "## 핵심 요약\n"
                "- MeetKey 서버 업로드와 처리 화면이 정상적으로 동작했습니다.\n\n"
                "## 결정 사항\n"
                "- 라즈베리파이는 녹음 단말기 역할에 집중합니다.\n"
                "- 서버 저장과 삭제는 휴대폰 QR 페이지에서 처리합니다.\n\n"
                "## 액션 아이템\n"
                "- 192.168.0.14 서버에 실제 Whisper 실행 환경을 연결합니다.\n"
            )

        prompt = f"""
다음은 Whisper로 전사된 회의 전사문입니다.
전사문에는 음성인식 오류, 중복 발화, 화자 오인식, 불완전한 문장, 잡담, 추임새가 포함될 수 있습니다.

목표:
전사문을 한국어 회의록으로 재구성하세요.
문장 순서대로 단순 축약하지 말고, 논점·쟁점·결정·액션 중심으로 정리하세요.

중요 원칙:
1. 회의에서 명시적으로 합의되거나 지시된 내용만 “결정 사항”으로 분류하세요.
2. 단순 아이디어, 우려, 가능성, 검토 의견은 “제안/아이디어” 또는 “검토 필요”로 분류하세요.
3. 담당자, 기한, 적용 범위가 명시되지 않았다면 임의로 만들지 말고 “담당 미정”, “기한 미정”, “적용 범위 미정”으로 표시하세요.
4. “확정”, “즉시 적용”, “전 케이스 적용”, “반드시”, “완료” 같은 단정 표현은 원문 근거가 명확할 때만 사용하세요.
5. 숫자값, 수치, 날짜, 버전, 병원명, 제품명, 사람 이름은 반드시 맥락과 확정성을 함께 적으세요.
6. Whisper 오류로 보이는 용어는 문맥상 보정하되, 확신이 낮으면 “추정”이라고 표시하세요.
7. 잡담, 반복 응답, 의미 없는 추임새는 제거하되, 회의의 쟁점과 입장 차이는 보존하세요.
8. 보고서처럼 보기 좋게 만들기 위해 원문에 없는 담당자, 기한, 결론을 보강하지 마세요.

출력 형식:

## 회의 개요
- 회의 성격:
- 주요 목적:
- 핵심 주제:
- 전체 결론:
  - 확정된 내용:
  - 검토가 필요한 내용:
  - 단순 아이디어/제안:

## 핵심 논의 사항

### 1. [주제명]
- 논의 배경:
- 주요 내용:
- 쟁점:
- 각 입장:
  - 관점 A:
  - 관점 B:
- 현재까지의 정리:
- 확정성: 확정 / 잠정 합의 / 검토 필요 / 제안 / 단순 언급 중 하나
- 남은 이슈:

필요한 만큼 주제를 추가하세요.

## 결정 사항
회의에서 실제로 합의되거나 지시된 내용만 작성하세요.
근거가 약하면 이 표에 넣지 말고 “검토 필요 사항”으로 이동하세요.

| 번호 | 결정 사항 | 근거 / 배경 | 적용 범위 | 주의할 점 |
| -- | -- | -- | -- | -- |

## 액션 아이템
담당자와 기한이 원문에 명확하지 않으면 임의로 만들지 마세요.

| 번호 | 액션 | 담당자 | 기한 | 확정성 | 비고 |
| -- | -- | -- | -- | -- | -- |

확정성 값은 다음 중 하나만 사용하세요:
- 확정
- 잠정
- 검토 필요
- 제안
- 담당 미정

## 리스크 및 확인 필요 사항

| 구분 | 내용 | 영향 | 대응 방향 | 확정성 |
| -- | -- | -- | -- | -- |

## 보류되었거나 추후 논의할 내용

## 다음 회의에서 확인할 질문
- 질문 1:
- 질문 2:

## 용어 및 음성인식 보정

| 전사 표현 | 보정 가능 표현 | 확신도 | 근거 / 비고 |
| -- | -- | -- | -- |

확신도는 높음 / 중간 / 낮음 중 하나로 표시하세요.

## 한 줄 요약
확정되지 않은 내용을 확정처럼 쓰지 말고, 회의의 본질을 한 문장으로 요약하세요.

마지막 자체 검토:
출력하기 전에 “결정 사항”과 “액션 아이템”을 다시 확인하세요.
원문 근거가 약한 항목은 “검토 필요” 또는 “제안”으로 낮추세요.

전사문:
{transcript}
""".strip()
        return self._ollama_generate(prompt, timeout_seconds=60 * 20)

    def _ollama_generate(self, prompt: str, timeout_seconds: int) -> str:
        url = self.config["ollama_url"].rstrip("/") + "/api/generate"
        payload = {
            "model": self.config["ollama_model"],
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.2, "top_p": 0.9},
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
                text = str(data.get("response", "")).strip()
                if text:
                    return text
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Ollama 요약 요청 실패: {exc}") from exc
        raise RuntimeError("Ollama 요약 결과가 비어 있습니다.")

    def _set_status(self, session_id: str, status: str, label: str) -> None:
        paths = self.paths(session_id)
        metadata = read_json(paths.metadata, {"session_id": session_id})
        metadata.update({"status": status, "status_label": label, "updated_at": now_iso()})
        write_json(paths.metadata, metadata)

    def session_payload(self, session_id: str) -> dict:
        paths = self.paths(session_id)
        metadata = read_json(paths.metadata, {"session_id": session_id, "status": "missing"})
        transcript = paths.transcript.read_text(encoding="utf-8", errors="replace") if paths.transcript.exists() else ""
        summary = paths.summary.read_text(encoding="utf-8", errors="replace") if paths.summary.exists() else ""
        return {
            "session": metadata,
            "transcript": transcript,
            "summary": summary,
        }

    def download_target(self, session_id: str, kind: str) -> tuple[Path, str, str]:
        session_id = safe_session_id(session_id)
        paths = self.paths(session_id)
        targets = {
            "audio": (paths.audio, "audio/wav", f"meetkey_{session_id}.wav"),
            "transcript": (paths.transcript, "text/plain; charset=utf-8", f"meetkey_{session_id}_transcript.txt"),
            "summary": (paths.summary, "text/plain; charset=utf-8", f"meetkey_{session_id}_summary.txt"),
        }
        if kind not in targets:
            raise ValueError("다운로드 종류가 올바르지 않습니다.")
        path, content_type, filename = targets[kind]
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(kind)
        return path, content_type, filename

    def save_session(self, session_id: str) -> dict:
        paths = self.paths(session_id)
        metadata = read_json(paths.metadata, {"session_id": session_id})
        if metadata.get("status") == "missing":
            raise FileNotFoundError(session_id)
        metadata.update({"saved": True, "updated_at": now_iso()})
        write_json(paths.metadata, metadata)
        return self.session_payload(session_id)

    def delete_session(self, session_id: str) -> dict:
        paths = self.paths(session_id)
        if paths.session_dir.exists():
            shutil.rmtree(paths.session_dir)
        return {"deleted": True, "session_id": session_id}

    def history(self) -> list[dict]:
        items: list[dict] = []
        for metadata_path in sorted(self.sessions_dir.glob("*/metadata.json"), reverse=True):
            metadata = read_json(metadata_path)
            if metadata.get("saved") and not metadata.get("deleted"):
                items.append(metadata)
        return items


def json_response(handler: BaseHTTPRequestHandler, payload: dict | list, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def bytes_response(
    handler: BaseHTTPRequestHandler,
    body: bytes,
    content_type: str,
    status: int = 200,
    headers: dict[str, str] | None = None,
) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    for key, value in (headers or {}).items():
        handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(body)


def render_shell(title: str, app_script: str, config: dict) -> bytes:
    html_doc = f"""<!doctype html>
<html lang="ko" translate="no">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta name="google" content="notranslate" />
    <title>{html.escape(title)} - MeetKey</title>
    <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Ccircle cx='16' cy='16' r='10' fill='none' stroke='%2322c55e' stroke-width='7'/%3E%3C/svg%3E" />
    <link rel="stylesheet" href="{config['base_path']}/static/server.css?v={STATIC_VERSION}" />
  </head>
  <body>
    <main id="app" class="page-shell"></main>
    <script src="{config['base_path']}/static/{app_script}?v={STATIC_VERSION}"></script>
  </body>
</html>
"""
    return html_doc.encode("utf-8")


def make_handler(app: MeetKeyServer):
    base = app.config["base_path"]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            print(f"{self.address_string()} - {fmt % args}")

        def do_HEAD(self) -> None:
            if self.path.startswith(base):
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            if path == f"{base}/history":
                return bytes_response(self, render_shell("녹음 기록", "history.js", app.config), "text/html; charset=utf-8")
            if path.startswith(f"{base}/session/"):
                return bytes_response(self, render_shell("회의록 처리", "session.js", app.config), "text/html; charset=utf-8")
            if path == f"{base}/api/health":
                return json_response(
                    self,
                    {
                        "ok": True,
                        "model": app.config["ollama_model"],
                        "stt_mode": app.config["stt_mode"],
                        "summary_mode": app.config["summary_mode"],
                        "whisper_model": app.config["whisper_model"],
                        "time": now_iso(),
                    },
                )
            if path == f"{base}/api/history":
                return json_response(self, app.history())
            if path.startswith(f"{base}/api/sessions/"):
                parts = path.split("/")
                if len(parts) >= 7 and parts[5] == "download":
                    session_id = unquote(parts[4])
                    kind = unquote(parts[6])
                    try:
                        target, content_type, filename = app.download_target(session_id, kind)
                    except FileNotFoundError:
                        return json_response(self, {"error": "다운로드할 파일을 찾을 수 없습니다."}, 404)
                    except ValueError as exc:
                        return json_response(self, {"error": str(exc)}, 400)
                    disposition = f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"
                    return bytes_response(
                        self,
                        target.read_bytes(),
                        content_type,
                        headers={"Content-Disposition": disposition},
                    )
                if len(parts) >= 5:
                    session_id = unquote(parts[4])
                    return json_response(self, app.session_payload(session_id))
            if path.startswith(f"{base}/static/"):
                rel = path.replace(f"{base}/static/", "", 1)
                target = (STATIC_DIR / rel).resolve()
                if not str(target).startswith(str(STATIC_DIR.resolve())):
                    return self.send_error(HTTPStatus.FORBIDDEN)
                if target.suffix == ".css":
                    return self._serve_file(target, "text/css; charset=utf-8")
                if target.suffix == ".js":
                    return self._serve_file(target, "application/javascript; charset=utf-8")
            return self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            try:
                if path == f"{base}/api/chunks/process":
                    payload = app.process_chunk({k.lower(): v for k, v in self.headers.items()}, self.rfile.read)
                    return json_response(self, payload)
                if path == f"{base}/api/chunks/summarize":
                    length = int(self.headers.get("content-length", "0") or "0")
                    if length <= 0:
                        return json_response(self, {"error": "요약할 JSON 본문이 없습니다."}, 400)
                    body = self.rfile.read(length)
                    payload = json.loads(body.decode("utf-8"))
                    return json_response(self, app.summarize_chunks(payload))
                if path.startswith(f"{base}/api/sessions/") and path.endswith("/audio"):
                    session_id = unquote(path[len(f"{base}/api/sessions/") : -len("/audio")])
                    payload = app.create_or_update_session(session_id, {k.lower(): v for k, v in self.headers.items()}, self.rfile.read)
                    return json_response(self, payload)
                if path.startswith(f"{base}/api/sessions/") and path.endswith("/save"):
                    session_id = unquote(path[len(f"{base}/api/sessions/") : -len("/save")])
                    return json_response(self, app.save_session(session_id))
                if path.startswith(f"{base}/api/sessions/") and path.endswith("/delete"):
                    session_id = unquote(path[len(f"{base}/api/sessions/") : -len("/delete")])
                    return json_response(self, app.delete_session(session_id))
            except FileNotFoundError:
                return json_response(self, {"error": "세션을 찾을 수 없습니다."}, 404)
            except Exception as exc:
                return json_response(self, {"error": str(exc)}, 500)
            return self.send_error(HTTPStatus.NOT_FOUND)

        def _serve_file(self, path: Path, content_type: str) -> None:
            if not path.exists() or not path.is_file():
                return self.send_error(HTTPStatus.NOT_FOUND)
            bytes_response(self, path.read_bytes(), content_type)

    return Handler


def main() -> None:
    config = load_config()
    app = MeetKeyServer(config)
    server = ThreadingHTTPServer((config["host"], config["port"]), make_handler(app))
    print(f"MeetKey server running on http://{config['host']}:{config['port']}{config['base_path']}/history")
    print(f"Data directory: {config['data_dir']}")
    print(f"Ollama: {config['ollama_url']} / {config['ollama_model']}")
    server.serve_forever()


if __name__ == "__main__":
    main()
