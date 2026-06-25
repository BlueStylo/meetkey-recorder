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

            write_json(
                paths.metadata,
                {
                    "session_id": session_id,
                    "chunk_id": chunk_id,
                    "chunk_index": index,
                    "chunk_start_seconds": start_seconds,
                    "audio_size": paths.audio.stat().st_size,
                    "created_at": now_iso(),
                },
            )

            with self.processing_lock:
                transcript = self.transcribe(paths)
                paths.transcript.write_text(transcript, encoding="utf-8")
                summary = self.summarize(transcript)
                paths.summary.write_text(summary, encoding="utf-8")

            return {
                "ok": True,
                "session_id": session_id,
                "chunk_id": chunk_id,
                "chunk_index": index,
                "chunk_start_seconds": start_seconds,
                "audio_size": paths.audio.stat().st_size,
                "transcript": transcript,
                "summary": summary,
                "summary_model": self.config["ollama_model"],
                "processed_at": now_iso(),
            }
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

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
아래는 하나의 긴 회의를 10분 단위로 나누어 전사/요약한 결과입니다.
구간 사이에는 짧은 오버랩이 있으므로 반복되는 내용은 한 번만 반영하세요.
전체 회의의 흐름을 기준으로 한국어 회의록을 다시 작성하세요.

형식:
## 회의 개요
- 회의 성격:
- 주요 목적:
- 핵심 주제:
- 전체 결론:

## 핵심 논의 사항
### [주제명]
- 논의 배경:
- 주요 내용:
- 의미 / 영향:
- 남은 이슈:

## 결정 사항
| 번호 | 결정 사항 | 근거 / 배경 | 비고 |
| -- | -- | -- | -- |

## 액션 아이템
| 번호 | 담당자 | 할 일 | 우선도 | 기한 | 비고 |
| -- | -- | -- | -- | -- | -- |

## 리스크 및 확인 필요 사항
| 구분 | 내용 | 영향 | 대응 방향 |
| -- | -- | -- | -- |

## 보류되었거나 추후 논의할 내용

## 용어 및 음성인식 보정
| 전사 표현 | 보정 가능 표현 | 근거 / 비고 |
| -- | -- | -- |

## 한 줄 요약

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
다음 회의 전사문을 한국어 회의록으로 정리해 주세요.
전사문에 [SPEAKER_00] 00:00-00:10 같은 화자와 시간이 있으면 적극 활용하세요.
겹치거나 반복된 발화는 한 번만 반영하고, 음성인식 오류로 보이는 단어는 문맥상 가능한 표현으로 보정하세요.

형식:
## 회의 개요
- 회의 성격:
- 주요 목적:
- 핵심 주제:
- 전체 결론:

## 핵심 논의 사항
### [주제명]
- 논의 배경:
- 주요 내용:
- 의미 / 영향:
- 남은 이슈:

## 결정 사항
| 번호 | 결정 사항 | 근거 / 배경 | 비고 |
| -- | -- | -- | -- |

## 액션 아이템
| 번호 | 담당자 | 할 일 | 우선도 | 기한 | 비고 |
| -- | -- | -- | -- | -- | -- |

## 리스크 및 확인 필요 사항
| 구분 | 내용 | 영향 | 대응 방향 |
| -- | -- | -- | -- |

## 보류되었거나 추후 논의할 내용

## 용어 및 음성인식 보정
| 전사 표현 | 보정 가능 표현 | 근거 / 비고 |
| -- | -- | -- |

## 한 줄 요약

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
            "options": {"temperature": 0.2},
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
