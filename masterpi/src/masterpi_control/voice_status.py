"""Small file-backed status feed shared by voice service and web controller."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Optional


DEFAULT_VOICE_STATUS_PATH = Path("/tmp/masterpi-voice-conversation.json")
MAX_EVENTS = 100
MAX_STATUS_BYTES = 256 * 1024


def voice_status_path(path: Optional[Path] = None) -> Path:
    if path is not None:
        return Path(path)
    configured = os.environ.get("MASTERPI_VOICE_STATUS_PATH", "").strip()
    return Path(configured) if configured else DEFAULT_VOICE_STATUS_PATH


def empty_voice_status() -> dict[str, Any]:
    return {
        "version": 1,
        "session": None,
        "active": False,
        "state": "idle",
        "status": "Waiting for “Hello HiBot”.",
        "revision": 0,
        "updated_at": 0.0,
        "events": [],
    }


class VoiceStatusWriter:
    """Publish bounded voice-session events through an atomic JSON snapshot."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = voice_status_path(path)
        self._lock = threading.Lock()
        self._value = empty_voice_status()

    def begin(self, session: str) -> None:
        with self._lock:
            self._value = empty_voice_status()
            self._value.update(session=session, active=True)
            self._append_locked(
                role="step",
                text="Wake word accepted; starting a new voice conversation.",
                stage="wake",
                tool="openWakeWord",
            )
            self._set_state_locked("listening", "Listening for your question…")
            self._write_locked()

    def step(self, state: str, text: str, *, tool: Optional[str] = None) -> None:
        with self._lock:
            self._append_locked(role="step", text=text, stage=state, tool=tool)
            self._set_state_locked(state, text)
            self._write_locked()

    def message(self, role: str, text: str) -> None:
        if role not in {"user", "hibot"}:
            raise ValueError("voice message role must be user or hibot")
        with self._lock:
            self._append_locked(role=role, text=text, stage="message")
            self._write_locked()

    def finish(self, text: str = "Voice conversation ended.") -> None:
        with self._lock:
            if self._value["active"]:
                self._append_locked(role="step", text=text, stage="idle")
            self._value["active"] = False
            self._set_state_locked("idle", "Waiting for “Hello HiBot”.")
            self._write_locked()

    def error(self, text: str) -> None:
        with self._lock:
            self._append_locked(role="error", text=text, stage="error")
            self._value["active"] = False
            self._set_state_locked("error", text)
            self._write_locked()

    def idle(self) -> None:
        with self._lock:
            self._value = empty_voice_status()
            self._write_locked()

    def _append_locked(
        self,
        *,
        role: str,
        text: str,
        stage: str,
        tool: Optional[str] = None,
    ) -> None:
        event_id = int(self._value["revision"]) + 1
        event = {
            "id": event_id,
            "role": role,
            "text": str(text)[:4000],
            "stage": stage,
            "timestamp": time.time(),
        }
        if tool:
            event["tool"] = str(tool)[:160]
        events = list(self._value["events"])
        events.append(event)
        self._value["events"] = events[-MAX_EVENTS:]
        self._value["revision"] = event_id

    def _set_state_locked(self, state: str, status: str) -> None:
        self._value["state"] = state
        self._value["status"] = str(status)[:500]
        self._value["updated_at"] = time.time()

    def _write_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self._value, separators=(",", ":"), ensure_ascii=False)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def read_voice_status(path: Optional[Path] = None) -> dict[str, Any]:
    target = voice_status_path(path)
    try:
        if target.stat().st_size > MAX_STATUS_BYTES:
            return empty_voice_status()
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return empty_voice_status()
    if not isinstance(value, dict) or not isinstance(value.get("events"), list):
        return empty_voice_status()
    try:
        revision = max(0, int(value.get("revision") or 0))
        updated_at = max(0.0, float(value.get("updated_at") or 0.0))
    except (TypeError, ValueError, OverflowError):
        return empty_voice_status()
    events = []
    for event in value["events"][-MAX_EVENTS:]:
        if not isinstance(event, dict):
            continue
        role = event.get("role")
        if role not in {"user", "hibot", "step", "error"}:
            continue
        clean = {
            "id": event.get("id"),
            "role": role,
            "text": str(event.get("text") or "")[:4000],
            "stage": str(event.get("stage") or "")[:40],
            "timestamp": event.get("timestamp"),
        }
        if event.get("tool"):
            clean["tool"] = str(event["tool"])[:160]
        events.append(clean)
    result = empty_voice_status()
    result.update(
        version=1,
        session=value.get("session") if isinstance(value.get("session"), str) else None,
        active=bool(value.get("active")),
        state=str(value.get("state") or "idle")[:40],
        status=str(value.get("status") or "")[:500],
        revision=revision,
        updated_at=updated_at,
        events=events,
    )
    return result
