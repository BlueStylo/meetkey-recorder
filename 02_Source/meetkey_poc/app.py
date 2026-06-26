#!/usr/bin/env python3
from __future__ import annotations

import ctypes
import ctypes.util
import html
import json
import math
import os
import re
import secrets
import shutil
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"

DEFAULT_CONFIG = {
    "host": "0.0.0.0",
    "port": 8000,
    "server_base_url": "http://127.0.0.1:8080/meetkey",
    "device_base_url": "http://127.0.0.1:8000",
    "recordings_dir": str(Path.home() / "MeetKey_Recordings"),
    "mic_device": "plughw:CARD=PowerConf,DEV=0",
    "mic_label": "Anker PowerConf",
    "sample_rate": 16000,
    "channels": 1,
    "qr_timeout_seconds": 180,
    "upload_timeout_seconds": 1800,
    "chunk_processing_enabled": True,
    "chunk_seconds": 600,
    "chunk_overlap_seconds": 10,
    "chunk_min_seconds": 3,
    "chunk_upload_timeout_seconds": 3600,
    "hotspot_ssid": "MeetKey",
    "hotspot_password": "change-me",
    "hotspot_base_url": "http://10.42.0.1:8000",
    "captive_base_url": "http://10.42.0.1",
    "captive_target_file": str(Path.home() / "MeetKey_Recordings" / "captive_target.txt"),
}


STATIC_VERSION = "20260626-2"


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
            if isinstance(config[key], bool):
                value = value.strip().lower() in {"1", "true", "yes", "on"}
            elif isinstance(config[key], int):
                value = int(value)
            config[key] = value
    return config


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def safe_session_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", value.strip())
    return cleaned[:96] or time.strftime("%Y%m%d_%H%M%S")


def display_host(value: str) -> str:
    try:
        parsed = urlparse(str(value))
        return parsed.hostname or str(value)
    except Exception:
        return str(value)


def read_json(path: Path, fallback: dict | None = None) -> dict:
    if not path.exists():
        return dict(fallback or {})
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return dict(fallback or {})


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def read_text(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


class QRcode(ctypes.Structure):
    _fields_ = [
        ("version", ctypes.c_int),
        ("width", ctypes.c_int),
        ("data", ctypes.POINTER(ctypes.c_ubyte)),
    ]


class QRRenderer:
    def __init__(self) -> None:
        self.lib = None
        lib_name = ctypes.util.find_library("qrencode")
        if lib_name:
            self.lib = ctypes.CDLL(lib_name)
            self.lib.QRcode_encodeString.argtypes = [
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
            ]
            self.lib.QRcode_encodeString.restype = ctypes.POINTER(QRcode)
            self.lib.QRcode_free.argtypes = [ctypes.POINTER(QRcode)]

    def render_svg(self, data: str, size: int = 220) -> str:
        if not self.lib:
            return self._fallback_svg("QR unavailable", size)

        qr = self.lib.QRcode_encodeString(data.encode("utf-8"), 0, 1, 2, 1)
        if not qr:
            return self._fallback_svg("QR error", size)

        try:
            width = qr.contents.width
            modules = qr.contents.data
            quiet = 4
            total = width + quiet * 2
            cell = max(1, size // total)
            actual = cell * total
            parts = [
                f'<svg xmlns="http://www.w3.org/2000/svg" width="{actual}" height="{actual}" viewBox="0 0 {actual} {actual}">',
                '<rect width="100%" height="100%" rx="14" fill="#f8fafc"/>',
            ]
            for y in range(width):
                for x in range(width):
                    if modules[y * width + x] & 1:
                        parts.append(
                            f'<rect x="{(x + quiet) * cell}" y="{(y + quiet) * cell}" width="{cell}" height="{cell}" fill="#18181b"/>'
                        )
            parts.append("</svg>")
            return "".join(parts)
        finally:
            self.lib.QRcode_free(qr)

    def _fallback_svg(self, text: str, size: int) -> str:
        safe = html.escape(text)
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}">'
            '<rect width="100%" height="100%" rx="18" fill="#f8fafc"/>'
            '<rect x="14" y="14" width="192" height="192" rx="14" fill="none" stroke="#71717a" stroke-width="2"/>'
            f'<text x="50%" y="50%" text-anchor="middle" dominant-baseline="middle" fill="#27272a" font-size="16">{safe}</text>'
            "</svg>"
        )


@dataclass
class MicStatus:
    present: bool
    name: str
    detail: str = ""


@dataclass
class Session:
    state: str = "idle"
    session_id: str | None = None
    session_dir: Path | None = None
    segments: list[Path] = field(default_factory=list)
    segment_proc: subprocess.Popen | None = None
    segment_started_at: float | None = None
    accumulated_seconds: float = 0.0
    final_path: Path | None = None
    processing_ready_at: float | None = None
    last_error: str | None = None
    upload_status: str = "pending"
    upload_error: str | None = None
    chunk_index: int = 0
    chunk_consumed_count: int = 0
    chunk_previous_segment: Path | None = None
    chunk_workers: list[threading.Thread] = field(default_factory=list)
    chunk_supervisor_started: bool = False
    audio_level: float = 0.0
    audio_peak: float = 0.0
    audio_rms: int = 0
    audio_active: bool = False


class MeetKeyApp:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.lock = threading.RLock()
        self.session = Session()
        self.qr = QRRenderer()
        self.recordings_dir = Path(config["recordings_dir"]).expanduser()
        self.recordings_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir = self.recordings_dir / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.deleted_sessions: set[str] = set()
        self._write_captive_target("/records")

    def mic_status(self) -> MicStatus:
        try:
            out = subprocess.run(
                ["arecord", "-l"],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            ).stdout
        except Exception as exc:
            return MicStatus(False, "연결 안 됨", str(exc))

        if "PowerConf" in out:
            return MicStatus(True, self.config["mic_label"], "PowerConf")

        match = re.search(r"card\s+\d+:\s+([^\[]+)\[([^\]]+)\]", out)
        if match:
            return MicStatus(True, match.group(2).strip(), match.group(0))

        return MicStatus(False, "연결 안 됨")

    def status(self) -> dict:
        with self.lock:
            self._auto_reset_if_needed()
            self._refresh_audio_level_locked()
            mic = self.mic_status()
            elapsed = self._elapsed_locked()
            expires_in = None
            if self.session.state == "processing_ready" and self.session.processing_ready_at:
                passed = time.monotonic() - self.session.processing_ready_at
                expires_in = max(0, int(self.config["qr_timeout_seconds"] - passed))

            return {
                "state": self.session.state,
                "session_id": self.session.session_id,
                "elapsed_seconds": int(elapsed),
                "microphone": {
                    "present": mic.present,
                    "name": mic.name,
                    "detail": mic.detail,
                },
                "history_url": self.history_url(),
                "current_session_url": self.current_session_url(),
                "wifi_qr_payload": self.wifi_qr_payload(),
                "expires_in": expires_in,
                "segment_count": len(self.session.segments),
                "final_path": str(self.session.final_path) if self.session.final_path else None,
                "last_error": self.session.last_error,
                "upload_status": self.session.upload_status,
                "upload_error": self.session.upload_error,
                "chunk_processing": self.chunk_progress(self.session.session_id),
                "audio_level": {
                    "level": round(self.session.audio_level, 3),
                    "peak": round(self.session.audio_peak, 3),
                    "rms": self.session.audio_rms,
                    "active": self.session.audio_active,
                },
            }

    def audio_level_status(self) -> dict:
        with self.lock:
            self._auto_reset_if_needed()
            self._refresh_audio_level_locked()
            return {
                "state": self.session.state,
                "elapsed_seconds": int(self._elapsed_locked()),
                "audio_level": {
                    "level": round(self.session.audio_level, 3),
                    "peak": round(self.session.audio_peak, 3),
                    "rms": self.session.audio_rms,
                    "active": self.session.audio_active,
                },
            }

    def history_url(self) -> str:
        return self.device_url("/records")

    def current_session_url(self) -> str | None:
        if not self.session.session_id:
            return None
        return self.record_url(self.session.session_id)

    def device_url(self, path: str) -> str:
        base = str(self.config.get("device_base_url") or "").rstrip("/")
        if not base:
            base = f'http://127.0.0.1:{self.config["port"]}'
        return f"{base}/{path.lstrip('/')}"

    def hotspot_url(self, path: str) -> str:
        base = str(self.config.get("hotspot_base_url") or self.device_url("/")).rstrip("/")
        return f"{base}/{path.lstrip('/')}"

    def captive_url(self, path: str) -> str:
        base = str(self.config.get("captive_base_url") or "http://10.42.0.1").rstrip("/")
        return f"{base}/{path.lstrip('/')}"

    def wifi_qr_payload(self) -> str:
        ssid = self._escape_wifi_qr(str(self.config.get("hotspot_ssid") or "MeetKey"))
        password = self._escape_wifi_qr(str(self.config.get("hotspot_password") or "CHANGE_ME"))
        return f"WIFI:T:WPA;S:{ssid};P:{password};H:false;;"

    def _escape_wifi_qr(self, value: str) -> str:
        return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace(":", "\\:")

    def _write_captive_target(self, path: str) -> None:
        target_path = Path(str(self.config.get("captive_target_file") or "")).expanduser()
        if not target_path:
            return
        if not path.startswith("/"):
            path = "/" + path
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(self.captive_url(path), encoding="utf-8")
        except OSError:
            pass

    def record_url(self, session_id: str) -> str:
        return self.device_url(f"/record/{quote(session_id)}")

    def start(self) -> dict:
        with self.lock:
            if self.session.state != "idle":
                raise ValueError("이미 녹음 세션이 진행 중입니다.")
            mic = self.mic_status()
            if not mic.present:
                raise RuntimeError("마이크를 찾을 수 없습니다. 다시 연결한 뒤 녹음 시작을 눌러주세요.")

            session_id = time.strftime("%Y%m%d_%H%M%S") + "_" + secrets.token_hex(3)
            session_dir = self.sessions_dir / session_id
            session_dir.mkdir(parents=True, exist_ok=True)
            self.session = Session(
                state="recording",
                session_id=session_id,
                session_dir=session_dir,
            )
            self._write_captive_target("/records")
            self._write_metadata_locked("recording", "녹음 중")
            self._start_segment_locked()
            self._start_chunk_supervisor_locked()
            return self.status()

    def pause(self) -> dict:
        with self.lock:
            if self.session.state != "recording":
                raise ValueError("녹음중 상태가 아닙니다.")
            self._stop_segment_locked()
            self.session.state = "paused"
            self._write_metadata_locked("paused", "일시정지")
            return self.status()

    def resume(self) -> dict:
        with self.lock:
            if self.session.state != "paused":
                raise ValueError("일시정지 상태가 아닙니다.")
            self.session.state = "recording"
            self._start_segment_locked()
            self._write_metadata_locked("recording", "녹음 중")
            return self.status()

    def finish(self) -> dict:
        with self.lock:
            if self.session.state not in {"recording", "paused"}:
                raise ValueError("저장할 녹음이 없습니다.")
            self.session.state = "saving"
            self._write_metadata_locked("saving", "녹음 저장 중")
            if self.session.segment_proc:
                self._stop_segment_locked()
            self._merge_segments_locked()
            if self._chunk_processing_enabled():
                session_id = self.session.session_id
                unprocessed_segments = list(self.session.segments[self.session.chunk_consumed_count :])
                previous_segment = self.session.chunk_previous_segment
                next_index = self.session.chunk_index + 1
                workers = list(self.session.chunk_workers)
                self.session.upload_status = "chunked"
                self._write_metadata_locked("queued", "AI 구간 처리 마무리 중")
                self._start_chunk_finalize_thread_locked(
                    session_id,
                    unprocessed_segments,
                    previous_segment,
                    next_index,
                    workers,
                )
            else:
                self._write_metadata_locked("uploading", "AI 처리 서버로 전송 중")
                self._upload_to_server_locked()
                if self.session.upload_status == "uploaded":
                    self._write_metadata_locked("queued", "AI 처리 대기 중")
                    self._start_server_sync_thread_locked(self.session.session_id)
                else:
                    self._write_metadata_locked("upload_failed", "서버 전송 실패")
            self.session.state = "processing_ready"
            self.session.processing_ready_at = time.monotonic()
            if self.session.session_id:
                self._write_captive_target(f"/record/{quote(self.session.session_id)}")
            return self.status()

    def reset(self) -> dict:
        with self.lock:
            if self.session.state in {"recording", "paused", "saving"}:
                raise ValueError("녹음이 진행 중일 때는 메인으로 이동할 수 없습니다.")
            self.session = Session()
            self._write_captive_target("/records")
            return self.status()

    def cancel(self) -> dict:
        with self.lock:
            if self.session.state not in {"recording", "paused"}:
                raise ValueError("취소할 녹음이 없습니다.")
            if self.session.segment_proc:
                self._stop_segment_locked()
            if self.session.session_id:
                self.deleted_sessions.add(safe_session_id(self.session.session_id))
            session_dir = self.session.session_dir
            self.session = Session()
            if session_dir and session_dir.exists():
                shutil.rmtree(session_dir, ignore_errors=True)
            self._write_captive_target("/records")
            return self.status()

    def _start_segment_locked(self) -> None:
        if not self.session.session_dir:
            raise RuntimeError("세션 폴더가 없습니다.")
        index = len(self.session.segments) + 1
        segment_path = self.session.session_dir / f"segment_{index:03d}.wav"
        cmd = [
            "arecord",
            "-D",
            self.config["mic_device"],
            "-f",
            "S16_LE",
            "-r",
            str(self.config["sample_rate"]),
            "-c",
            str(self.config["channels"]),
            str(segment_path),
        ]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:
            self.session.last_error = str(exc)
            self.session.state = "error"
            raise RuntimeError(f"녹음을 시작할 수 없습니다: {exc}") from exc
        self.session.segment_proc = proc
        self.session.segment_started_at = time.monotonic()
        self.session.segments.append(segment_path)

    def _stop_segment_locked(self) -> None:
        proc = self.session.segment_proc
        if not proc:
            return
        if self.session.segment_started_at:
            self.session.accumulated_seconds += max(0.0, time.monotonic() - self.session.segment_started_at)
        try:
            proc.send_signal(signal.SIGINT)
            proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        finally:
            self.session.segment_proc = None
            self.session.segment_started_at = None
            self._clear_audio_level_locked()

    def _chunk_processing_enabled(self) -> bool:
        return bool(self.config.get("chunk_processing_enabled", True))

    def _start_chunk_supervisor_locked(self) -> None:
        if not self._chunk_processing_enabled() or self.session.chunk_supervisor_started:
            return
        session_id = self.session.session_id
        if not session_id:
            return
        self.session.chunk_supervisor_started = True
        threading.Thread(target=self._chunk_supervisor, args=(session_id,), daemon=True).start()

    def _chunk_supervisor(self, session_id: str) -> None:
        while True:
            job = None
            with self.lock:
                if self.session.session_id != session_id or self.session.state not in {"recording", "paused"}:
                    return
                if self.session.state == "recording" and self.session.segment_proc:
                    target = self._chunk_target_seconds_locked()
                    if self._unprocessed_duration_locked() >= target:
                        job = self._rotate_chunk_locked()
            if job:
                self._launch_chunk_worker(job)
                continue
            time.sleep(1)

    def _chunk_target_seconds_locked(self) -> float:
        chunk_seconds = max(30, int(self.config.get("chunk_seconds", 600)))
        overlap = max(0, int(self.config.get("chunk_overlap_seconds", 10)))
        if self.session.chunk_index == 0:
            return chunk_seconds + overlap
        return chunk_seconds

    def _unprocessed_duration_locked(self) -> float:
        total = 0.0
        current_segment = self.session.segments[-1] if self.session.segment_proc and self.session.segments else None
        for segment in self.session.segments[self.session.chunk_consumed_count :]:
            if current_segment and segment == current_segment:
                continue
            total += self._segment_duration(segment)
        if self.session.segment_started_at:
            total += max(0.0, time.monotonic() - self.session.segment_started_at)
        return total

    def _segment_duration(self, segment: Path) -> float:
        try:
            if not segment.exists() or segment.stat().st_size <= 44:
                return 0.0
            with wave.open(str(segment), "rb") as wav:
                rate = wav.getframerate()
                if rate <= 0:
                    return 0.0
                return wav.getnframes() / float(rate)
        except Exception:
            return 0.0

    def _rotate_chunk_locked(self) -> dict | None:
        if self.session.segment_proc:
            self._stop_segment_locked()

        session_id = self.session.session_id
        unprocessed_segments = [
            p
            for p in self.session.segments[self.session.chunk_consumed_count :]
            if p.exists() and p.stat().st_size > 44
        ]
        if not session_id or not unprocessed_segments:
            if self.session.state == "recording":
                self._start_segment_locked()
            return None

        index = self.session.chunk_index + 1
        start_seconds = self._chunk_start_seconds(index)
        previous_segment = self.session.chunk_previous_segment if index > 1 else None
        self.session.chunk_previous_segment = unprocessed_segments[-1]
        self.session.chunk_consumed_count = len(self.session.segments)
        self.session.chunk_index = index
        self._upsert_chunk_entry_locked(
            session_id,
            index,
            {
                "index": index,
                "status": "queued",
                "status_label": f"{index}구간 대기 중",
                "start_seconds": start_seconds,
                "final": False,
                "updated_at": now_iso(),
            },
        )
        self._write_metadata_locked("recording", f"{index}구간 선처리 중")

        if self.session.state == "recording":
            self._start_segment_locked()

        return {
            "session_id": session_id,
            "index": index,
            "start_seconds": start_seconds,
            "previous_segment": previous_segment,
            "segments": unprocessed_segments,
            "final": False,
        }

    def _chunk_start_seconds(self, index: int) -> int:
        chunk_seconds = max(30, int(self.config.get("chunk_seconds", 600)))
        return max(0, (index - 1) * chunk_seconds)

    def _start_chunk_finalize_thread_locked(
        self,
        session_id: str | None,
        unprocessed_segments: list[Path],
        previous_segment: Path | None,
        next_index: int,
        workers: list[threading.Thread],
    ) -> None:
        if not session_id:
            return
        args = (session_id, unprocessed_segments, previous_segment, next_index, workers)
        threading.Thread(target=self._finalize_chunked_session, args=args, daemon=True).start()

    def _launch_chunk_worker(self, job: dict) -> threading.Thread:
        thread = threading.Thread(target=self._process_chunk_job, args=(job,), daemon=True)
        with self.lock:
            if self.session.session_id == job.get("session_id"):
                self.session.chunk_workers.append(thread)
        thread.start()
        return thread

    def _process_chunk_job(self, job: dict) -> None:
        session_id = safe_session_id(str(job["session_id"]))
        index = int(job["index"])
        start_seconds = float(job.get("start_seconds") or 0)
        try:
            if self._is_deleted_session(session_id):
                return
            self._update_chunk_entry(
                session_id,
                index,
                {"status": "preparing", "status_label": f"{index}구간 오디오 준비 중", "updated_at": now_iso()},
            )
            chunk_path, duration = self._write_chunk_audio(
                session_id,
                index,
                job.get("previous_segment"),
                list(job.get("segments") or []),
            )
            min_seconds = max(1, int(self.config.get("chunk_min_seconds", 3)))
            if duration < min_seconds:
                self._update_chunk_entry(
                    session_id,
                    index,
                    {
                        "status": "skipped",
                        "status_label": f"{index}구간이 너무 짧아 건너뜀",
                        "duration_seconds": round(duration, 2),
                        "audio_path": str(chunk_path),
                        "updated_at": now_iso(),
                    },
                )
                return

            self._update_chunk_entry(
                session_id,
                index,
                {
                    "status": "processing",
                    "status_label": f"{index}구간 전사/요약 중",
                    "duration_seconds": round(duration, 2),
                    "end_seconds": int(start_seconds + duration),
                    "audio_path": str(chunk_path),
                    "updated_at": now_iso(),
                },
            )
            result = self._send_chunk_to_server(session_id, index, start_seconds, chunk_path)
            if self._is_deleted_session(session_id):
                return
            transcript = self._offset_transcript(str(result.get("transcript") or ""), start_seconds)
            summary = str(result.get("summary") or "").strip()
            transcript_path = self._chunk_text_path(session_id, index, "transcript")
            summary_path = self._chunk_text_path(session_id, index, "summary")
            transcript_path.write_text(transcript, encoding="utf-8")
            summary_path.write_text(summary, encoding="utf-8")
            self._update_chunk_entry(
                session_id,
                index,
                {
                    "status": "ready",
                    "status_label": f"{index}구간 완료",
                    "transcript_path": str(transcript_path),
                    "summary_path": str(summary_path),
                    "summary_model": result.get("summary_model"),
                    "processed_at": result.get("processed_at") or now_iso(),
                    "updated_at": now_iso(),
                },
            )
        except Exception as exc:
            self._update_chunk_entry(
                session_id,
                index,
                {
                    "status": "error",
                    "status_label": f"{index}구간 처리 실패",
                    "error": str(exc),
                    "updated_at": now_iso(),
                },
            )

    def _write_chunk_audio(
        self,
        session_id: str,
        index: int,
        previous_segment: Path | None,
        segments: list[Path],
    ) -> tuple[Path, float]:
        chunk_path = self._chunk_audio_path(session_id, index)
        frames: list[bytes] = []
        params = None
        overlap = max(0, int(self.config.get("chunk_overlap_seconds", 10)))

        if previous_segment and overlap > 0 and previous_segment.exists():
            current_params, pcm = self._read_segment_pcm(previous_segment)
            params = current_params
            channels, sample_width, frame_rate = current_params
            bytes_per_second = channels * sample_width * frame_rate
            tail_bytes = min(len(pcm), overlap * bytes_per_second)
            tail_bytes -= tail_bytes % max(1, channels * sample_width)
            if tail_bytes > 0:
                frames.append(pcm[-tail_bytes:])

        for segment in segments:
            if not segment.exists() or segment.stat().st_size <= 44:
                continue
            current_params, pcm = self._read_segment_pcm(segment)
            if params is None:
                params = current_params
            elif current_params != params:
                raise RuntimeError("구간 조각 오디오 형식이 서로 다릅니다.")
            frames.append(pcm)

        if params is None or not frames:
            raise RuntimeError("조각으로 만들 오디오 데이터가 없습니다.")

        channels, sample_width, frame_rate = params
        chunk_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(chunk_path), "wb") as out:
            out.setnchannels(channels)
            out.setsampwidth(sample_width)
            out.setframerate(frame_rate)
            for pcm in frames:
                out.writeframes(pcm)

        total_bytes = sum(len(pcm) for pcm in frames)
        duration = total_bytes / float(channels * sample_width * frame_rate)
        return chunk_path, duration

    def _send_chunk_to_server(self, session_id: str, index: int, start_seconds: float, chunk_path: Path) -> dict:
        url = f'{self.config["server_base_url"].rstrip("/")}/api/chunks/process'
        data = chunk_path.read_bytes()
        chunk_id = f"{session_id}_chunk_{index:03d}"
        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "audio/wav",
                "Content-Length": str(len(data)),
                "X-MeetKey-Source": "raspberry-pi",
                "X-MeetKey-Session-Id": session_id,
                "X-MeetKey-Chunk-Id": chunk_id,
                "X-MeetKey-Chunk-Index": str(index),
                "X-MeetKey-Chunk-Start-Seconds": str(int(start_seconds)),
            },
            method="POST",
        )
        timeout = int(self.config.get("chunk_upload_timeout_seconds", 3600))
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _finalize_chunked_session(
        self,
        session_id: str,
        unprocessed_segments: list[Path],
        previous_segment: Path | None,
        next_index: int,
        workers: list[threading.Thread],
    ) -> None:
        session_id = safe_session_id(session_id)
        try:
            if self._is_deleted_session(session_id):
                return
            if unprocessed_segments:
                self._upsert_chunk_entry_locked(
                    session_id,
                    next_index,
                    {
                        "index": next_index,
                        "status": "queued",
                        "status_label": f"{next_index}구간 대기 중",
                        "start_seconds": self._chunk_start_seconds(next_index),
                        "final": True,
                        "updated_at": now_iso(),
                    },
                )
                self._process_chunk_job(
                    {
                        "session_id": session_id,
                        "index": next_index,
                        "start_seconds": self._chunk_start_seconds(next_index),
                        "previous_segment": previous_segment if next_index > 1 else None,
                        "segments": unprocessed_segments,
                        "final": True,
                    }
                )

            for worker in workers:
                worker.join(timeout=int(self.config.get("chunk_upload_timeout_seconds", 3600)) + 60)

            self._update_record_status(session_id, "summarizing", "구간 요약 병합 중")
            if self._is_deleted_session(session_id):
                return
            chunks = self.chunk_progress(session_id)
            ready_chunks = [item for item in chunks if item.get("status") == "ready"]
            if not ready_chunks:
                raise RuntimeError("완료된 구간 처리 결과가 없습니다.")

            combined_transcript = self._combined_chunk_transcript(ready_chunks)
            combined_summary = self._summarize_chunks_on_server(session_id, ready_chunks, combined_transcript)
            if self._is_deleted_session(session_id):
                return
            self._transcript_path(session_id).write_text(combined_transcript, encoding="utf-8")
            self._summary_path(session_id).write_text(combined_summary, encoding="utf-8")

            metadata = read_json(self._metadata_path(session_id), {"session_id": session_id})
            metadata.update(
                {
                    "status": "ready",
                    "status_label": "회의록 생성 완료",
                    "updated_at": now_iso(),
                    "transcript_path": str(self._transcript_path(session_id)),
                    "summary_path": str(self._summary_path(session_id)),
                    "summary_model": metadata.get("summary_model") or "gemma4",
                    "title": self._derive_title(metadata, combined_summary),
                    "chunk_processing": {
                        "enabled": True,
                        "ready": len(ready_chunks),
                        "total": len(chunks),
                    },
                }
            )
            write_json(self._metadata_path(session_id), metadata)
        except Exception as exc:
            self._update_record_error_locked(session_id, f"구간 처리 병합 실패: {exc}")

    def _combined_chunk_transcript(self, chunks: list[dict]) -> str:
        parts: list[str] = []
        for item in sorted(chunks, key=lambda value: int(value.get("index") or 0)):
            transcript = read_text(Path(str(item.get("transcript_path") or "")))
            if not transcript:
                continue
            label = self._chunk_label(item)
            parts.append(f"## {label}\n\n{transcript.strip()}")
        return "\n\n".join(parts).strip() + "\n"

    def _summarize_chunks_on_server(self, session_id: str, chunks: list[dict], transcript: str) -> str:
        payload_chunks = []
        for item in sorted(chunks, key=lambda value: int(value.get("index") or 0)):
            summary = read_text(Path(str(item.get("summary_path") or ""))).strip()
            payload_chunks.append(
                {
                    "index": item.get("index"),
                    "label": self._chunk_label(item),
                    "summary": summary,
                }
            )
        if len(payload_chunks) == 1 and payload_chunks[0].get("summary"):
            return str(payload_chunks[0]["summary"]).strip()

        url = f'{self.config["server_base_url"].rstrip("/")}/api/chunks/summarize'
        data = json.dumps({"session_id": session_id, "chunks": payload_chunks, "transcript": transcript}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json", "Content-Length": str(len(data))},
            method="POST",
        )
        try:
            timeout = int(self.config.get("chunk_upload_timeout_seconds", 3600))
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            summary = str(payload.get("summary") or "").strip()
            if summary:
                metadata = read_json(self._metadata_path(session_id), {"session_id": session_id})
                metadata.update({"summary_model": payload.get("summary_model") or metadata.get("summary_model"), "updated_at": now_iso()})
                write_json(self._metadata_path(session_id), metadata)
                return summary
        except Exception as exc:
            fallback = self._fallback_chunk_summary(payload_chunks)
            if fallback:
                return fallback + f"\n\n> 최종 병합 요약 요청 실패: {exc}\n"
            raise
        raise RuntimeError("최종 요약 결과가 비어 있습니다.")

    def _fallback_chunk_summary(self, chunks: list[dict]) -> str:
        parts = ["# 구간별 회의 요약"]
        for item in chunks:
            summary = str(item.get("summary") or "").strip()
            if summary:
                parts.append(f"## {item.get('label') or item.get('index')}\n{summary}")
        return "\n\n".join(parts).strip()

    def _offset_transcript(self, transcript: str, offset_seconds: float) -> str:
        if not transcript or offset_seconds <= 0:
            return transcript.strip() + ("\n" if transcript.strip() else "")

        def replace(match: re.Match) -> str:
            return f"{match.group(1)}{self._format_transcript_time(self._parse_transcript_time(match.group(2)) + offset_seconds)}-{self._format_transcript_time(self._parse_transcript_time(match.group(3)) + offset_seconds)}"

        pattern = re.compile(r"(\[[^\]]+\]\s+)(\d{2}:\d{2}(?::\d{2})?)-(\d{2}:\d{2}(?::\d{2})?)")
        return pattern.sub(replace, transcript).strip() + "\n"

    def _parse_transcript_time(self, value: str) -> float:
        parts = [int(part) for part in value.split(":")]
        if len(parts) == 3:
            return float(parts[0] * 3600 + parts[1] * 60 + parts[2])
        return float(parts[0] * 60 + parts[1])

    def _format_transcript_time(self, seconds: float) -> str:
        seconds = max(0, int(round(seconds)))
        minutes, sec = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{sec:02d}"
        return f"{minutes:02d}:{sec:02d}"

    def _chunk_label(self, item: dict) -> str:
        index = int(item.get("index") or 0)
        start = self._format_transcript_time(float(item.get("start_seconds") or 0))
        end = self._format_transcript_time(float(item.get("end_seconds") or 0))
        if end == "00:00":
            return f"{index}구간"
        return f"{index}구간 {start}-{end}"

    def _update_record_status(self, session_id: str, status: str, label: str) -> None:
        if self._is_deleted_session(session_id):
            return
        metadata_path = self._metadata_path(session_id)
        metadata = read_json(metadata_path, {"session_id": session_id})
        metadata.update({"status": status, "status_label": label, "updated_at": now_iso()})
        write_json(metadata_path, metadata)

    def _refresh_audio_level_locked(self) -> None:
        if self.session.state != "recording" or not self.session.segments:
            self._clear_audio_level_locked()
            return

        segment = self.session.segments[-1]
        try:
            size = segment.stat().st_size
            if size <= 44:
                self._clear_audio_level_locked()
                return

            with segment.open("rb") as f:
                header = f.read(128)
                marker = header.find(b"data")
                data_start = marker + 8 if marker >= 0 else 44
                if size <= data_start:
                    self._clear_audio_level_locked()
                    return

                channels = max(1, int(self.config["channels"]))
                sample_width = 2
                frame_size = channels * sample_width
                window_bytes = int(int(self.config["sample_rate"]) * frame_size * 0.16)
                read_start = max(data_start, size - window_bytes)
                remainder = (read_start - data_start) % frame_size
                if remainder:
                    read_start += frame_size - remainder
                if read_start >= size:
                    self._clear_audio_level_locked()
                    return

                f.seek(read_start)
                pcm = f.read(size - read_start)

            pcm = pcm[: len(pcm) - (len(pcm) % frame_size)]
            if not pcm:
                self._clear_audio_level_locked()
                return

            rms, peak = self._pcm_stats(pcm, sample_width)
            self.session.audio_rms = int(rms)
            self.session.audio_peak = min(1.0, peak / 32768)
            self.session.audio_level = min(1.0, math.sqrt(rms / 12000)) if rms > 0 else 0.0
            self.session.audio_active = rms >= 180 or peak >= 1200
        except Exception:
            self._clear_audio_level_locked()

    def _pcm_stats(self, pcm: bytes, sample_width: int) -> tuple[int, int]:
        if sample_width != 2:
            return 0, 0

        count = len(pcm) // sample_width
        if count <= 0:
            return 0, 0

        peak = 0
        square_sum = 0
        for index in range(0, count * sample_width, sample_width):
            sample = int.from_bytes(pcm[index : index + sample_width], "little", signed=True)
            absolute = abs(sample)
            peak = max(peak, absolute)
            square_sum += sample * sample

        return int(math.sqrt(square_sum / count)), peak

    def _clear_audio_level_locked(self) -> None:
        self.session.audio_level = 0.0
        self.session.audio_peak = 0.0
        self.session.audio_rms = 0
        self.session.audio_active = False

    def _merge_segments_locked(self) -> None:
        valid_segments = [p for p in self.session.segments if p.exists() and p.stat().st_size > 44]
        if not valid_segments:
            raise RuntimeError("저장할 녹음 세그먼트가 없습니다.")
        if not self.session.session_dir or not self.session.session_id:
            raise RuntimeError("세션 정보가 없습니다.")

        final_path = self.session.session_dir / f"meetkey_{self.session.session_id}.wav"
        params = None
        frames: list[bytes] = []
        for segment in valid_segments:
            current_params, pcm = self._read_segment_pcm(segment)
            if params is None:
                params = current_params
            elif current_params != params:
                raise RuntimeError("녹음 세그먼트 형식이 서로 다릅니다.")
            frames.append(pcm)
        if params is None:
            raise RuntimeError("저장할 녹음 데이터가 없습니다.")

        channels, sample_width, frame_rate = params
        with wave.open(str(final_path), "wb") as out:
            out.setnchannels(channels)
            out.setsampwidth(sample_width)
            out.setframerate(frame_rate)
            for chunk in frames:
                out.writeframes(chunk)

        self.session.final_path = final_path

    def _read_segment_pcm(self, segment: Path) -> tuple[tuple[int, int, int], bytes]:
        with wave.open(str(segment), "rb") as wav:
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            frame_rate = wav.getframerate()

        raw = segment.read_bytes()
        marker = raw.find(b"data")
        if marker < 0 or marker + 8 > len(raw):
            raise RuntimeError(f"녹음 세그먼트에서 data chunk를 찾을 수 없습니다: {segment.name}")

        declared_size = int.from_bytes(raw[marker + 4 : marker + 8], "little")
        data_start = marker + 8
        available_size = len(raw) - data_start
        data_size = declared_size if 0 < declared_size <= available_size else available_size
        pcm = raw[data_start : data_start + data_size]

        frame_size = channels * sample_width
        if frame_size <= 0:
            raise RuntimeError(f"녹음 세그먼트 형식이 올바르지 않습니다: {segment.name}")
        pcm = pcm[: len(pcm) - (len(pcm) % frame_size)]
        if not pcm:
            raise RuntimeError(f"녹음 세그먼트에 오디오 데이터가 없습니다: {segment.name}")

        return (channels, sample_width, frame_rate), pcm

    def _write_metadata_locked(self, status: str | None = None, status_label: str | None = None) -> None:
        if not self.session.session_dir:
            return
        metadata_path = self.session.session_dir / "metadata.json"
        metadata = read_json(metadata_path, {"session_id": self.session.session_id})
        created_at = metadata.get("created_at") or now_iso()
        final_path = str(self.session.final_path) if self.session.final_path else metadata.get("final_path")
        audio_size = 0
        if self.session.final_path and self.session.final_path.exists():
            audio_size = self.session.final_path.stat().st_size
        elif final_path:
            try:
                audio_size = Path(final_path).stat().st_size
            except OSError:
                audio_size = int(metadata.get("audio_size") or 0)
        chunks = self.chunk_progress(self.session.session_id)

        metadata = {
            **metadata,
            "session_id": self.session.session_id,
            "created_at": created_at,
            "updated_at": now_iso(),
            "title": metadata.get("title") or self._fallback_title(created_at),
            "status": status or metadata.get("status") or self.session.state,
            "status_label": status_label or metadata.get("status_label") or self.session.state,
            "saved": bool(metadata.get("saved", False)),
            "deleted": False,
            "elapsed_seconds": int(self.session.accumulated_seconds),
            "segments": [str(p) for p in self.session.segments],
            "final_path": final_path,
            "audio_size": audio_size,
            "history_url": self.history_url(),
            "current_session_url": self.current_session_url(),
            "upload_status": self.session.upload_status,
            "upload_error": self.session.upload_error,
            "chunk_processing": {
                "enabled": self._chunk_processing_enabled(),
                "ready": len([item for item in chunks if item.get("status") == "ready"]),
                "total": len(chunks),
            },
            "ai_pipeline": {
                "server": display_host(str(self.config.get("server_base_url") or "")),
                "stt": "Whisper",
                "summary": "gemma4:31b",
                "status": self.session.upload_status,
            },
        }
        write_json(metadata_path, metadata)

    def _upload_to_server_locked(self) -> None:
        if not self.session.session_id or not self.session.final_path:
            return
        if not self.session.final_path.exists():
            self.session.upload_status = "failed"
            self.session.upload_error = "최종 WAV 파일을 찾을 수 없습니다."
            return

        session_id = quote(self.session.session_id)
        url = f'{self.config["server_base_url"].rstrip("/")}/api/sessions/{session_id}/audio'
        elapsed = str(int(self.session.accumulated_seconds))
        try:
            data = self.session.final_path.read_bytes()
            request = urllib.request.Request(
                url,
                data=data,
                headers={
                    "Content-Type": "audio/wav",
                    "Content-Length": str(len(data)),
                    "X-MeetKey-Source": "raspberry-pi",
                    "X-MeetKey-Elapsed-Seconds": elapsed,
                },
                method="POST",
            )
            timeout = int(self.config.get("upload_timeout_seconds", 1800))
            with urllib.request.urlopen(request, timeout=timeout) as response:
                response.read()
            self.session.upload_status = "uploaded"
            self.session.upload_error = None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self.session.upload_status = "failed"
            self.session.upload_error = str(exc)

    def _start_server_sync_thread_locked(self, session_id: str | None) -> None:
        if not session_id:
            return
        threading.Thread(target=self._sync_server_result, args=(session_id,), daemon=True).start()

    def _sync_server_result(self, session_id: str) -> None:
        session_id = safe_session_id(session_id)
        deadline = time.monotonic() + 60 * 60 * 6
        while time.monotonic() < deadline:
            try:
                payload = self._fetch_server_session(session_id)
                record = payload.get("session") or {}
                status = str(record.get("status") or "missing")
                transcript = str(payload.get("transcript") or "")
                summary = str(payload.get("summary") or "")

                with self.lock:
                    self._update_record_from_server_locked(session_id, record, transcript, summary)

                if status == "ready":
                    self._delete_server_session(session_id)
                    return
                if status in {"error", "missing"}:
                    return
            except Exception as exc:
                with self.lock:
                    self._update_record_error_locked(session_id, f"서버 처리 상태 확인 실패: {exc}")
                time.sleep(5)
                continue
            time.sleep(2)

        with self.lock:
            self._update_record_error_locked(session_id, "서버 처리 시간이 너무 오래 걸려 상태 확인을 중단했습니다.")

    def _fetch_server_session(self, session_id: str) -> dict:
        url = f'{self.config["server_base_url"].rstrip("/")}/api/sessions/{quote(session_id)}'
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    def _delete_server_session(self, session_id: str) -> None:
        url = f'{self.config["server_base_url"].rstrip("/")}/api/sessions/{quote(session_id)}/delete'
        request = urllib.request.Request(url, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                response.read()
            with self.lock:
                if self._is_deleted_session(session_id):
                    return
                metadata_path = self._metadata_path(session_id)
                metadata = read_json(metadata_path, {"session_id": session_id})
                metadata.update({"processing_server_cleaned": True, "updated_at": now_iso()})
                write_json(metadata_path, metadata)
        except Exception as exc:
            with self.lock:
                if self._is_deleted_session(session_id):
                    return
                metadata_path = self._metadata_path(session_id)
                metadata = read_json(metadata_path, {"session_id": session_id})
                metadata.update({"processing_server_cleanup_error": str(exc), "updated_at": now_iso()})
                write_json(metadata_path, metadata)

    def _update_record_from_server_locked(
        self,
        session_id: str,
        server_record: dict,
        transcript: str,
        summary: str,
    ) -> None:
        if self._is_deleted_session(session_id):
            return
        metadata_path = self._metadata_path(session_id)
        metadata = read_json(metadata_path, {"session_id": session_id})
        status = str(server_record.get("status") or metadata.get("status") or "queued")
        status_label = str(server_record.get("status_label") or metadata.get("status_label") or status)
        if transcript:
            self._transcript_path(session_id).write_text(transcript, encoding="utf-8")
        if summary:
            self._summary_path(session_id).write_text(summary, encoding="utf-8")

        metadata.update(
            {
                "session_id": session_id,
                "status": status,
                "status_label": status_label,
                "updated_at": now_iso(),
                "server_status": status,
                "transcript_path": str(self._transcript_path(session_id)) if transcript else metadata.get("transcript_path"),
                "summary_path": str(self._summary_path(session_id)) if summary else metadata.get("summary_path"),
                "summary_model": server_record.get("summary_model") or metadata.get("summary_model"),
                "title": self._derive_title(metadata, summary or read_text(self._summary_path(session_id))),
                "error": server_record.get("error", metadata.get("error", "")),
            }
        )
        write_json(metadata_path, metadata)

    def _update_record_error_locked(self, session_id: str, message: str) -> None:
        if self._is_deleted_session(session_id):
            return
        metadata_path = self._metadata_path(session_id)
        metadata = read_json(metadata_path, {"session_id": session_id})
        if metadata.get("status") == "ready":
            return
        metadata.update(
            {
                "status": "error",
                "status_label": "처리 상태 확인 실패",
                "error": message,
                "updated_at": now_iso(),
            }
        )
        write_json(metadata_path, metadata)

    def records(self) -> list[dict]:
        items: list[dict] = []
        for metadata_path in sorted(self.sessions_dir.glob("*/metadata.json"), reverse=True):
            metadata = read_json(metadata_path)
            if metadata.get("deleted"):
                continue
            session_id = safe_session_id(str(metadata.get("session_id") or metadata_path.parent.name))
            summary = read_text(self._summary_path(session_id))
            metadata["session_id"] = session_id
            metadata["title"] = self._derive_title(metadata, summary)
            metadata["record_url"] = self.record_url(session_id)
            metadata["has_audio"] = self._audio_path(session_id).exists()
            metadata["has_transcript"] = self._transcript_path(session_id).exists()
            metadata["has_summary"] = self._summary_path(session_id).exists()
            chunks = self.chunk_progress(session_id)
            metadata["chunk_total"] = len(chunks)
            metadata["chunk_ready"] = len([item for item in chunks if item.get("status") == "ready"])
            items.append(metadata)
        return sorted(items, key=lambda item: str(item.get("created_at") or ""), reverse=True)

    def record_payload(self, session_id: str) -> dict:
        session_id = safe_session_id(session_id)
        self._write_captive_target(f"/record/{quote(session_id)}")
        metadata = read_json(self._metadata_path(session_id), {"session_id": session_id, "status": "missing"})
        summary = read_text(self._summary_path(session_id))
        transcript = read_text(self._transcript_path(session_id))
        metadata.update(
            {
                "session_id": session_id,
                "title": self._derive_title(metadata, summary),
                "record_url": self.record_url(session_id),
                "hotspot_record_url": self.hotspot_url(f"/record/{quote(session_id)}"),
                "wifi_qr_payload": self.wifi_qr_payload(),
                "history_url": self.history_url(),
                "has_audio": self._audio_path(session_id).exists(),
                "has_transcript": bool(transcript),
                "has_summary": bool(summary),
                "audio_size": self._audio_path(session_id).stat().st_size if self._audio_path(session_id).exists() else metadata.get("audio_size", 0),
            }
        )
        chunks = self.chunk_progress(session_id)
        metadata["chunk_total"] = len(chunks)
        metadata["chunk_ready"] = len([item for item in chunks if item.get("status") == "ready"])
        return {"record": metadata, "transcript": transcript, "summary": summary, "chunks": chunks}

    def keep_record(self, session_id: str) -> dict:
        session_id = safe_session_id(session_id)
        metadata_path = self._metadata_path(session_id)
        metadata = read_json(metadata_path, {"session_id": session_id})
        if metadata.get("status") == "missing" or not metadata_path.exists():
            raise FileNotFoundError(session_id)
        metadata.update({"saved": True, "updated_at": now_iso()})
        write_json(metadata_path, metadata)
        return self.record_payload(session_id)

    def delete_record(self, session_id: str) -> dict:
        session_id = safe_session_id(session_id)
        session_dir = self.sessions_dir / session_id
        with self.lock:
            self.deleted_sessions.add(session_id)
        self._delete_server_session(session_id)
        if session_dir.exists():
            shutil.rmtree(session_dir, ignore_errors=True)
        with self.lock:
            if self.session.session_id == session_id:
                self.session = Session()
        return {"deleted": True, "session_id": session_id}

    def download_target(self, session_id: str, kind: str) -> tuple[Path, str, str]:
        session_id = safe_session_id(session_id)
        targets = {
            "audio": (self._audio_path(session_id), "audio/wav", f"meetkey_{session_id}.wav"),
            "transcript": (self._transcript_path(session_id), "text/plain; charset=utf-8", f"meetkey_{session_id}_transcript.txt"),
            "summary": (self._summary_path(session_id), "text/plain; charset=utf-8", f"meetkey_{session_id}_summary.txt"),
        }
        if kind not in targets:
            raise ValueError("다운로드 종류가 올바르지 않습니다.")
        path, content_type, filename = targets[kind]
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(kind)
        return path, content_type, filename

    def chunk_progress(self, session_id: str | None) -> list[dict]:
        if not session_id:
            return []
        payload = read_json(self._chunks_path(session_id), {"items": []})
        items = payload.get("items")
        if not isinstance(items, list):
            return []
        cleaned: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            entry = dict(item)
            entry["label"] = self._chunk_label(entry)
            cleaned.append(entry)
        return sorted(cleaned, key=lambda value: int(value.get("index") or 0))

    def _upsert_chunk_entry_locked(self, session_id: str, index: int, fields: dict) -> None:
        payload = read_json(
            self._chunks_path(session_id),
            {
                "enabled": True,
                "chunk_seconds": int(self.config.get("chunk_seconds", 600)),
                "overlap_seconds": int(self.config.get("chunk_overlap_seconds", 10)),
                "items": [],
            },
        )
        items = payload.get("items")
        if not isinstance(items, list):
            items = []
        found = False
        for item in items:
            if int(item.get("index") or 0) == index:
                item.update(fields)
                found = True
                break
        if not found:
            items.append({"index": index, **fields})
        payload["items"] = sorted(items, key=lambda value: int(value.get("index") or 0))
        payload["updated_at"] = now_iso()
        write_json(self._chunks_path(session_id), payload)

    def _update_chunk_entry(self, session_id: str, index: int, fields: dict) -> None:
        with self.lock:
            if self._is_deleted_session(session_id):
                return
            self._upsert_chunk_entry_locked(session_id, index, fields)
            metadata_path = self._metadata_path(session_id)
            metadata = read_json(metadata_path, {"session_id": session_id})
            chunks = self.chunk_progress(session_id)
            ready = len([item for item in chunks if item.get("status") == "ready"])
            active = next((item for item in chunks if item.get("status") in {"queued", "preparing", "processing"}), None)
            if metadata.get("status") != "ready":
                metadata.update(
                    {
                        "chunk_processing": {"enabled": True, "ready": ready, "total": len(chunks)},
                        "status": metadata.get("status") or "queued",
                        "status_label": active.get("status_label") if active else metadata.get("status_label", "AI 구간 처리 중"),
                        "updated_at": now_iso(),
                    }
                )
                write_json(metadata_path, metadata)

    def _metadata_path(self, session_id: str) -> Path:
        return self.sessions_dir / safe_session_id(session_id) / "metadata.json"

    def _is_deleted_session(self, session_id: str) -> bool:
        return safe_session_id(session_id) in self.deleted_sessions

    def _audio_path(self, session_id: str) -> Path:
        session_id = safe_session_id(session_id)
        metadata = read_json(self._metadata_path(session_id), {"session_id": session_id})
        final_path = metadata.get("final_path")
        if final_path and Path(str(final_path)).exists():
            return Path(str(final_path))
        return self.sessions_dir / session_id / f"meetkey_{session_id}.wav"

    def _transcript_path(self, session_id: str) -> Path:
        return self.sessions_dir / safe_session_id(session_id) / "transcript.md"

    def _summary_path(self, session_id: str) -> Path:
        return self.sessions_dir / safe_session_id(session_id) / "summary.md"

    def _chunks_path(self, session_id: str) -> Path:
        return self.sessions_dir / safe_session_id(session_id) / "chunks.json"

    def _chunk_dir(self, session_id: str) -> Path:
        return self.sessions_dir / safe_session_id(session_id) / "chunks"

    def _chunk_audio_path(self, session_id: str, index: int) -> Path:
        return self._chunk_dir(session_id) / f"chunk_{index:03d}.wav"

    def _chunk_text_path(self, session_id: str, index: int, kind: str) -> Path:
        suffix = "summary.md" if kind == "summary" else "transcript.md"
        path = self._chunk_dir(session_id) / f"chunk_{index:03d}_{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _derive_title(self, metadata: dict, summary: str) -> str:
        current = str(metadata.get("title") or "").strip()
        if current and not current.startswith("회의록 ") and not self._is_generic_title(current):
            return current

        summary = summary.strip()
        patterns = [
            r"##\s*한 줄 요약\s*\n+(.+)",
            r"핵심 주제:\s*(.+)",
            r"전체 결론:\s*(.+)",
            r"회의 성격:\s*(.+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, summary)
            if match:
                return self._clean_title(match.group(1))

        for line in summary.splitlines():
            line = line.strip().lstrip("#-*0123456789. \t")
            if line and not self._is_generic_title(line):
                return self._clean_title(line)

        return self._fallback_title(str(metadata.get("created_at") or ""))

    def _is_generic_title(self, value: str) -> bool:
        value = re.sub(r"\s+", " ", value).strip()
        if not value:
            return True
        generic_fragments = [
            "제시해주신",
            "제시해주신 전사문",
            "통합 재구성한 회의록",
            "정리한 회의록",
            "회의 요약",
            "핵심 요약",
            "회의 개요",
            "한 줄 요약",
        ]
        return any(fragment in value for fragment in generic_fragments)

    def _clean_title(self, value: str) -> str:
        value = re.sub(r"\s+", " ", value).strip(" -:|")
        value = re.sub(r"[*_`#>]+", "", value).strip(" -:|")
        value = re.sub(r"^(회의\s*성격|핵심\s*주제|전체\s*결론)\s*[:：]\s*", "", value)
        value = re.sub(r"^본\s+회의는\s*", "", value)
        value = re.sub(r"에\s+대해\s+논의하였.*$", " 논의", value)
        if len(value) > 30:
            value = value[:30].rstrip() + "..."
        return value or "회의록"

    def _fallback_title(self, created_at: str) -> str:
        if created_at:
            compact = created_at.replace("T", " ")[:16]
            return f"회의록 {compact}"
        return "제목 생성 중"

    def _elapsed_locked(self) -> float:
        elapsed = self.session.accumulated_seconds
        if self.session.state == "recording" and self.session.segment_started_at:
            elapsed += max(0.0, time.monotonic() - self.session.segment_started_at)
        return elapsed

    def _auto_reset_if_needed(self) -> None:
        if self.session.state != "processing_ready" or not self.session.processing_ready_at:
            return
        passed = time.monotonic() - self.session.processing_ready_at
        if passed >= self.config["qr_timeout_seconds"]:
            self.session = Session()


def json_response(handler: BaseHTTPRequestHandler, payload: dict, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def text_response(
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


def render_phone_shell(title: str) -> bytes:
    safe_title = html.escape(title)
    return f"""<!doctype html>
<html lang="ko" translate="no">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta name="google" content="notranslate" />
    <title>{safe_title} - MeetKey</title>
    <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Ccircle cx='16' cy='16' r='10' fill='none' stroke='%2322c55e' stroke-width='7'/%3E%3C/svg%3E" />
    <link rel="stylesheet" href="/static/mobile.css?v={STATIC_VERSION}" />
  </head>
  <body>
    <main id="phoneApp" class="phone-shell"></main>
    <script src="/static/mobile.js?v={STATIC_VERSION}"></script>
  </body>
</html>
""".encode("utf-8")


def make_handler(app: MeetKeyApp):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            print(f"{self.address_string()} - {fmt % args}")

        def do_HEAD(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path in {"/", "/device", "/records"} or parsed.path.startswith("/record/"):
                return self._head("text/html; charset=utf-8")
            if parsed.path in {"/api/status", "/api/records"} or parsed.path.startswith("/api/records/"):
                return self._head("application/json; charset=utf-8")
            if parsed.path == "/qr.svg":
                return self._head("image/svg+xml; charset=utf-8")
            if parsed.path.startswith("/static/"):
                if parsed.path.endswith(".css"):
                    return self._head("text/css; charset=utf-8")
                if parsed.path.endswith(".js"):
                    return self._head("application/javascript; charset=utf-8")
            return self.send_error(HTTPStatus.NOT_FOUND)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path in {"/", "/device"}:
                return self._serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            if parsed.path == "/records":
                return text_response(self, render_phone_shell("녹음 기록"), "text/html; charset=utf-8")
            if parsed.path.startswith("/record/"):
                return text_response(self, render_phone_shell("회의록"), "text/html; charset=utf-8")
            if parsed.path == "/api/status":
                return json_response(self, app.status())
            if parsed.path == "/api/audio-level":
                return json_response(self, app.audio_level_status())
            if parsed.path == "/api/records":
                return json_response(self, app.records())
            if parsed.path.startswith("/api/records/"):
                parts = parsed.path.split("/")
                if len(parts) >= 6 and parts[4] == "download":
                    session_id = unquote(parts[3])
                    kind = unquote(parts[5])
                    try:
                        target, content_type, filename = app.download_target(session_id, kind)
                    except FileNotFoundError:
                        return json_response(self, {"error": "다운로드할 파일을 찾을 수 없습니다."}, 404)
                    except ValueError as exc:
                        return json_response(self, {"error": str(exc)}, 400)
                    disposition = f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"
                    return text_response(
                        self,
                        target.read_bytes(),
                        content_type,
                        headers={"Content-Disposition": disposition},
                    )
                if len(parts) >= 4:
                    session_id = unquote(parts[3])
                    return json_response(self, app.record_payload(session_id))
            if parsed.path == "/qr.svg":
                query = parse_qs(parsed.query)
                data = query.get("data", [app.history_url()])[0]
                svg = app.qr.render_svg(data).encode("utf-8")
                return text_response(self, svg, "image/svg+xml; charset=utf-8")
            if parsed.path.startswith("/static/"):
                rel = parsed.path.replace("/static/", "", 1)
                target = (STATIC_DIR / rel).resolve()
                if not str(target).startswith(str(STATIC_DIR.resolve())):
                    return self.send_error(HTTPStatus.FORBIDDEN)
                if target.suffix == ".css":
                    return self._serve_file(target, "text/css; charset=utf-8")
                if target.suffix == ".js":
                    return self._serve_file(target, "application/javascript; charset=utf-8")
                return self._serve_file(target, "application/octet-stream")
            return self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            try:
                if parsed.path == "/api/start":
                    return json_response(self, app.start())
                if parsed.path == "/api/pause":
                    return json_response(self, app.pause())
                if parsed.path == "/api/resume":
                    return json_response(self, app.resume())
                if parsed.path == "/api/finish":
                    return json_response(self, app.finish())
                if parsed.path == "/api/cancel":
                    return json_response(self, app.cancel())
                if parsed.path == "/api/reset":
                    return json_response(self, app.reset())
                if parsed.path.startswith("/api/records/") and parsed.path.endswith("/keep"):
                    session_id = unquote(parsed.path[len("/api/records/") : -len("/keep")])
                    return json_response(self, app.keep_record(session_id))
                if parsed.path.startswith("/api/records/") and parsed.path.endswith("/delete"):
                    session_id = unquote(parsed.path[len("/api/records/") : -len("/delete")])
                    return json_response(self, app.delete_record(session_id))
            except FileNotFoundError:
                return json_response(self, {"error": "기록을 찾을 수 없습니다."}, 404)
            except RuntimeError as exc:
                return json_response(self, {"error": str(exc), "status": app.status()}, 409)
            except ValueError as exc:
                return json_response(self, {"error": str(exc), "status": app.status()}, 400)
            except Exception as exc:
                return json_response(self, {"error": f"처리 중 오류가 발생했습니다: {exc}", "status": app.status()}, 500)
            return self.send_error(HTTPStatus.NOT_FOUND)

        def _serve_file(self, path: Path, content_type: str) -> None:
            if not path.exists() or not path.is_file():
                return self.send_error(HTTPStatus.NOT_FOUND)
            text_response(self, path.read_bytes(), content_type)

        def _head(self, content_type: str) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", "0")
            self.end_headers()

    return Handler


def main() -> None:
    config = load_config()
    app = MeetKeyApp(config)
    server = ThreadingHTTPServer((config["host"], config["port"]), make_handler(app))
    print(f"MeetKey PoC running on http://{config['host']}:{config['port']}/device")
    print(f"Recording directory: {config['recordings_dir']}")
    server.serve_forever()


if __name__ == "__main__":
    main()
