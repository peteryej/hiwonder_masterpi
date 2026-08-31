"""Bridge between the MasterPi web controller and Hermes Agent."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable


class ChatError(RuntimeError):
    """Raised when chat or speech transcription cannot complete."""


_MIME_SUFFIXES = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/aac": ".aac",
    "audio/flac": ".flac",
}


class HermesChat:
    """Keep a named Hermes conversation for text and recorded-audio requests."""

    def __init__(
        self,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        session_name: str = "hibot-web-safe",
    ) -> None:
        self._runner = runner
        self._session_name = session_name
        self._lock = threading.Lock()
        self._hermes = os.environ.get("MASTERPI_HERMES_BIN") or shutil.which("hermes")
        self._hermes_root = Path(
            os.environ.get("MASTERPI_HERMES_ROOT", "/home/pi/.hermes/hermes-agent")
        )
        self._hermes_python = Path(
            os.environ.get(
                "MASTERPI_HERMES_PYTHON", str(self._hermes_root / "venv/bin/python")
            )
        )
        self._workdir = Path(
            os.environ.get(
                "MASTERPI_CHAT_WORKDIR", "/home/pi/projs/hiwonder_masterpi"
            )
        )

    def reply(self, message: str) -> str:
        if not self._hermes:
            raise ChatError("Hermes Agent executable was not found")
        command = [
            self._hermes,
            "chat",
            "--query-file",
            "-",
            "-Q",
            "--continue",
            self._session_name,
            "--create-if-missing",
            "--source",
            "tool",
            "--toolsets",
            "safe",
            "--in",
            str(self._workdir),
            "--max-turns",
            "30",
            "--run-budget",
            "120",
        ]
        with self._lock:
            completed = self._runner(
                command,
                input=message,
                text=True,
                capture_output=True,
                timeout=150,
            )
        if completed.returncode:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise ChatError(detail or "Hermes Agent did not return a reply")
        lines = completed.stdout.strip().splitlines()
        if lines and lines[0].startswith("session_id:"):
            lines.pop(0)
        reply = "\n".join(lines).strip()
        if not reply:
            raise ChatError("Hermes Agent returned an empty reply")
        return reply

    def transcribe(self, audio: bytes, content_type: str) -> str:
        mime_type = content_type.partition(";")[0].strip().lower()
        suffix = _MIME_SUFFIXES.get(mime_type)
        if suffix is None:
            raise ChatError(f"Unsupported audio type: {mime_type}")
        if not self._hermes_python.is_file():
            raise ChatError("Hermes Agent Python environment was not found")

        script = (
            "import json,sys; "
            "from tools.transcription_tools import transcribe_audio; "
            "print(json.dumps(transcribe_audio(sys.argv[1], source='masterpi-web')))"
        )
        path = ""
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
                handle.write(audio)
                path = handle.name
            completed = self._runner(
                [str(self._hermes_python), "-c", script, path],
                text=True,
                capture_output=True,
                timeout=180,
                cwd=str(self._hermes_root),
            )
        finally:
            if path:
                Path(path).unlink(missing_ok=True)

        if completed.returncode:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise ChatError(detail or "Audio transcription failed")
        result = self._last_json_object(completed.stdout)
        if not result.get("success"):
            raise ChatError(str(result.get("error") or "Audio transcription failed"))
        transcript = str(result.get("transcript") or "").strip()
        if not transcript:
            raise ChatError("No speech was detected in the recording")
        return transcript

    def synthesize(self, text: str) -> tuple[bytes, str]:
        if not self._hermes_python.is_file():
            raise ChatError("Hermes Agent Python environment was not found")
        script = (
            "import sys; "
            "from tools.tts_tool import text_to_speech_tool; "
            "print(text_to_speech_tool(sys.argv[1], output_path=sys.argv[2]))"
        )
        initial_path = ""
        generated_paths: list[str] = []
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as handle:
                initial_path = handle.name
            completed = self._runner(
                [str(self._hermes_python), "-c", script, text, initial_path],
                text=True,
                capture_output=True,
                timeout=180,
                cwd=str(self._hermes_root),
            )
            if completed.returncode:
                detail = completed.stderr.strip() or completed.stdout.strip()
                raise ChatError(detail or "Reply speech generation failed")
            result = self._last_json_object(completed.stdout)
            if not result.get("success"):
                raise ChatError(str(result.get("error") or "Reply speech generation failed"))
            generated_paths = [str(path) for path in result.get("file_paths") or []]
            output_path = str(result.get("file_path") or initial_path)
            if output_path not in generated_paths:
                generated_paths.append(output_path)
            audio = Path(output_path).read_bytes()
            if not audio:
                raise ChatError("Reply speech generation produced empty audio")
            content_type = "audio/ogg" if Path(output_path).suffix.lower() == ".ogg" else "audio/mpeg"
            return audio, content_type
        finally:
            for path in {initial_path, *generated_paths}:
                if path:
                    Path(path).unlink(missing_ok=True)

    @staticmethod
    def _last_json_object(output: str) -> dict[str, Any]:
        for line in reversed(output.splitlines()):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
        raise ChatError("Hermes transcription returned an invalid response")
