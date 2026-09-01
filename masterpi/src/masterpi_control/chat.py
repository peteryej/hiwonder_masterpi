"""Bridge between the MasterPi web controller and Hermes Agent."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


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
        self._api_url = os.environ.get("MASTERPI_HERMES_API_URL", "").rstrip("/")
        self._workdir = Path(
            os.environ.get(
                "MASTERPI_CHAT_WORKDIR", "/home/pi/projs/hiwonder_masterpi"
            )
        )

    def reply(self, message: str) -> str:
        if self._api_url:
            return self._api_reply(message)
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

    def analyze_image(self, jpeg: bytes) -> dict[str, Any]:
        """Analyze one camera JPEG with Hermes' configured vision model."""
        if not isinstance(jpeg, (bytes, bytearray)) or not jpeg:
            raise ChatError("Camera returned an empty image")
        if len(jpeg) > 20 * 1024 * 1024:
            raise ChatError("Camera image is too large for Hermes vision")
        if not self._hermes_python.is_file():
            raise ChatError("Hermes Agent Python environment was not found")

        prompt = (
            "Analyze this robot camera image and identify every distinct physical object. "
            "Return ONLY JSON with this schema: "
            '{"description":"concise scene description","objects":['
            '{"label":"object name","confidence":0.0,'
            '"bbox":{"x_min":0.0,"y_min":0.0,"x_max":1.0,"y_max":1.0}}]}. '
            "Bounding-box coordinates must be normalized from 0 to 1 relative to the full image. "
            "Do not use markdown and do not invent objects that are not visible."
        )
        script = (
            "import asyncio,json,sys; "
            "from tools.vision_tools import vision_analyze_tool; "
            "from agent.auxiliary_client import resolve_vision_provider_client; "
            "provider,client,model=resolve_vision_provider_client(); "
            "result=json.loads(asyncio.run(vision_analyze_tool(sys.argv[1],sys.argv[2]))); "
            "result.update({'provider':provider,'model':model}); "
            "print(json.dumps(result))"
        )
        path = ""
        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as handle:
                handle.write(jpeg)
                path = handle.name
            completed = self._runner(
                [str(self._hermes_python), "-c", script, path, prompt],
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
            raise ChatError(detail or "Hermes vision analysis failed")
        result = self._last_json_object(completed.stdout)
        if not result.get("success"):
            raise ChatError(str(result.get("analysis") or "Hermes vision analysis failed"))
        structured = self._vision_object(result.get("analysis"))
        structured["provider"] = result.get("provider")
        structured["model"] = result.get("model")
        return structured

    @staticmethod
    def _vision_object(analysis: Any) -> dict[str, Any]:
        text = str(analysis or "").strip()
        candidates = [text]
        fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
        if fenced:
            candidates.insert(0, fenced.group(1))
        value: Any = None
        for candidate in candidates:
            try:
                value = json.loads(candidate)
            except json.JSONDecodeError:
                decoder = json.JSONDecoder()
                for index, character in enumerate(candidate):
                    if character != "{":
                        continue
                    try:
                        decoded, _ = decoder.raw_decode(candidate[index:])
                    except json.JSONDecodeError:
                        continue
                    if isinstance(decoded, dict) and "objects" in decoded:
                        value = decoded
                        break
            if isinstance(value, dict):
                break
        if not isinstance(value, dict):
            raise ChatError("Hermes vision did not return structured object detection")

        objects = []
        raw_objects = value.get("objects", [])
        if not isinstance(raw_objects, list):
            raw_objects = []
        for item in raw_objects[:20]:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip()
            bbox = item.get("bbox")
            if not label or not isinstance(bbox, dict):
                continue
            try:
                normalized_box = {
                    key: round(max(0.0, min(1.0, float(bbox[key]))), 4)
                    for key in ("x_min", "y_min", "x_max", "y_max")
                }
                confidence = round(max(0.0, min(1.0, float(item.get("confidence", 0.0)))), 3)
            except (KeyError, TypeError, ValueError):
                continue
            if (
                normalized_box["x_min"] >= normalized_box["x_max"]
                or normalized_box["y_min"] >= normalized_box["y_max"]
            ):
                continue
            objects.append(
                {"label": label, "confidence": confidence, "bbox": normalized_box}
            )
        description = str(value.get("description") or "").strip()
        return {"description": description, "objects": objects, "object_count": len(objects)}

    def _api_reply(self, message: str) -> str:
        env_path = Path.home() / ".hermes" / ".env"
        try:
            lines = env_path.read_text(encoding="utf-8").splitlines()
            api_key = next(
                line.split("=", 1)[1].strip()
                for line in lines
                if line.startswith("API_SERVER_KEY=")
            )
        except (OSError, StopIteration, IndexError) as exc:
            raise ChatError("Hermes API server credentials are unavailable") from exc
        payload = json.dumps(
            {
                "model": "hermes-agent",
                "stream": False,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are hibot, a MasterPi robot. Use only the hibot tools for physical actions. "
                            "For motion, state what you did and use bounded commands."
                        ),
                    },
                    {"role": "user", "content": message},
                ],
            }
        ).encode("utf-8")
        request = Request(
            f"{self._api_url}/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        try:
            with self._lock, urlopen(request, timeout=150) as response:
                result = json.loads(response.read())
            reply = str(result["choices"][0]["message"]["content"]).strip()
        except (HTTPError, URLError, KeyError, IndexError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ChatError(f"Hermes API request failed: {exc}") from exc
        if not reply:
            raise ChatError("Hermes API returned an empty reply")
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
