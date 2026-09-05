"""Direct OpenAI Realtime speech-to-speech conversation for HiBot."""

from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional
from urllib.parse import quote

import numpy as np

INPUT_SAMPLE_RATE = 16_000
REALTIME_SAMPLE_RATE = 24_000
DEFAULT_REALTIME_MODEL = "gpt-realtime-2.1"
DEFAULT_REALTIME_VOICE = "marin"
DEFAULT_TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"
DEFAULT_REALTIME_URL = "wss://api.openai.com/v1/realtime"

HIBOT_REALTIME_PROMPT = """You are HiBot, a physical Hiwonder MasterPi robot.
Always identify yourself as HiBot when asked who you are. You have a four-wheel
mecanum chassis, a six-servo arm with a gripper, a camera mounted above the
gripper, a front ultrasonic sensor, controllable LEDs and buzzer, and a
ReSpeaker USB four-microphone array connected to a speaker. The camera can be
moved to a check-front pose and the robot software can detect objects, annotate
their bounding boxes, grab an object directly in front, drive, turn, operate
the arm, and follow sound direction. This voice session is conversational only
and has no robot-control tools, so never claim that you performed or observed a
physical action. If asked to act, briefly explain that the user should use the
web controls or the tool-enabled chat. Speak naturally and concisely. Normally
answer in one or two short sentences. Do not use Markdown or read punctuation
aloud."""

HIBOT_TOOL_REALTIME_PROMPT = """You are HiBot, a physical Hiwonder MasterPi robot.
Always identify yourself as HiBot when asked who you are. You have a four-wheel
mecanum chassis, a six-servo arm with a gripper, a camera mounted above the
gripper, a front ultrasonic sensor, controllable LEDs and buzzer, and a
ReSpeaker USB four-microphone array connected to a speaker. You have tools for
reading robot state, driving, moving the arm, checking the camera, grabbing the
object directly in front, following sound, and controlling LEDs and the buzzer.
Call a physical-control tool only when the user explicitly asks you to perform
that action. Never say an action succeeded until its tool result confirms it.
Use the stop tool immediately when the user asks you to stop. The grab tool is
an unconditional quick action and must not be preceded by a color check. For a
camera question, use analyze_camera unless the user explicitly asks for color
detection, in which case use analyze_camera_color. Speak naturally and
concisely, normally in one or two short sentences. Do not use Markdown or read
punctuation aloud."""


class RealtimeVoiceError(RuntimeError):
    """Raised when direct Realtime transport or audio handling fails."""


@dataclass(frozen=True)
class RealtimeTurnResult:
    """Transcripts and measured timings for one streamed audio turn."""

    transcript: str
    reply: str
    upload_seconds: float
    first_audio_seconds: Optional[float]
    response_seconds: float
    total_seconds: float
    audio_seconds: float
    tool_calls: tuple["RealtimeToolResult", ...] = ()


@dataclass(frozen=True)
class RealtimeToolResult:
    """Observed result and latency for one model-requested robot action."""

    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    elapsed_seconds: float
    ok: bool


def resample_pcm16(
    pcm: bytes,
    input_rate: int = INPUT_SAMPLE_RATE,
    output_rate: int = REALTIME_SAMPLE_RATE,
) -> bytes:
    """Resample little-endian mono PCM16 with linear interpolation."""

    if len(pcm) % 2:
        raise RealtimeVoiceError("PCM16 input must contain complete two-byte samples")
    if input_rate <= 0 or output_rate <= 0:
        raise RealtimeVoiceError("audio sample rates must be positive")
    samples = np.frombuffer(pcm, dtype="<i2")
    if not samples.size or input_rate == output_rate:
        return pcm
    output_count = max(1, round(samples.size * output_rate / input_rate))
    source_positions = np.arange(samples.size, dtype=np.float64)
    target_positions = np.arange(output_count, dtype=np.float64) * input_rate / output_rate
    target_positions = np.minimum(target_positions, samples.size - 1)
    converted = np.interp(target_positions, source_positions, samples.astype(np.float64))
    return np.rint(converted).clip(-32_768, 32_767).astype("<i2").tobytes()


class RawPcmPlayer:
    """Stream raw PCM16 audio to ALSA without first creating an audio file."""

    def __init__(
        self,
        device: str,
        *,
        sample_rate: int = REALTIME_SAMPLE_RATE,
        popen: Callable[..., Any] = subprocess.Popen,
    ) -> None:
        self.device = device
        self.sample_rate = sample_rate
        self._popen = popen
        self._process: Optional[Any] = None

    def start(self) -> None:
        if self._process is not None:
            return
        try:
            self._process = self._popen(
                [
                    "aplay",
                    "-q",
                    "-D",
                    self.device,
                    "-t",
                    "raw",
                    "-f",
                    "S16_LE",
                    "-c",
                    "1",
                    "-r",
                    str(self.sample_rate),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            raise RealtimeVoiceError(
                f"could not start ReSpeaker streaming playback: {exc}"
            ) from exc

    def write(self, audio: bytes) -> None:
        self.start()
        if self._process is None or self._process.stdin is None:
            raise RealtimeVoiceError("ReSpeaker streaming playback is not running")
        try:
            self._process.stdin.write(audio)
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise RealtimeVoiceError(f"could not stream reply to ReSpeaker: {exc}") from exc

    def close(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        if process.stdin is not None:
            process.stdin.close()
        returncode = process.wait(timeout=30)
        if returncode:
            detail = ""
            if process.stderr is not None:
                detail = process.stderr.read().decode(errors="replace").strip()
            raise RealtimeVoiceError(detail or "ReSpeaker streaming playback failed")


class OpenAIRealtimeClient:
    """Persistent low-level WebSocket client for Realtime audio turns."""

    def __init__(
        self,
        api_key: str,
        playback_device: str,
        *,
        model: str = DEFAULT_REALTIME_MODEL,
        voice: str = DEFAULT_REALTIME_VOICE,
        transcription_model: str = DEFAULT_TRANSCRIPTION_MODEL,
        instructions: str = HIBOT_REALTIME_PROMPT,
        url: str = DEFAULT_REALTIME_URL,
        websocket_factory: Optional[Callable[..., Any]] = None,
        player_factory: Optional[Callable[[], Any]] = None,
        tool_dispatcher: Any = None,
        max_tool_rounds: int = 4,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not api_key.strip():
            raise RealtimeVoiceError(
                "OPENAI_API_KEY is required for direct GPT Realtime voice conversation"
            )
        self.api_key = api_key.strip()
        self.playback_device = playback_device
        self.model = model
        self.voice = voice
        self.transcription_model = transcription_model
        self.instructions = instructions
        self.url = url
        self._websocket_factory = websocket_factory
        self._player_factory = player_factory or (
            lambda: RawPcmPlayer(self.playback_device)
        )
        self.tool_dispatcher = tool_dispatcher
        self.max_tool_rounds = max_tool_rounds
        self._clock = clock
        self._socket: Optional[Any] = None

    @classmethod
    def from_environment(cls, playback_device: str, **kwargs: Any) -> "OpenAIRealtimeClient":
        return cls(os.environ.get("OPENAI_API_KEY", ""), playback_device, **kwargs)

    def connect(self) -> float:
        """Open and configure the socket, returning handshake duration."""

        if self._socket is not None:
            return 0.0
        factory = self._websocket_factory
        if factory is None:
            try:
                from websocket import create_connection
            except ImportError as exc:
                raise RealtimeVoiceError(
                    "websocket-client is not installed in the wake-word environment"
                ) from exc
            factory = create_connection

        started = self._clock()
        endpoint = f"{self.url}?model={quote(self.model, safe='')}"
        try:
            self._socket = factory(
                endpoint,
                header=[f"Authorization: Bearer {self.api_key}"],
                timeout=90,
            )
            session: dict[str, Any] = {
                "type": "realtime",
                "model": self.model,
                "instructions": self.instructions,
                "output_modalities": ["audio"],
                "audio": {
                    "input": {
                        "format": {
                            "type": "audio/pcm",
                            "rate": REALTIME_SAMPLE_RATE,
                        },
                        "transcription": {"model": self.transcription_model},
                        "turn_detection": None,
                    },
                    "output": {
                        "format": {
                            "type": "audio/pcm",
                            "rate": REALTIME_SAMPLE_RATE,
                        },
                        "voice": self.voice,
                    },
                },
            }
            if self.tool_dispatcher is not None:
                session["tools"] = self.tool_dispatcher.schemas
                session["tool_choice"] = "auto"
            self._send({"type": "session.update", "session": session})
            while True:
                event = self._receive()
                if event.get("type") == "session.updated":
                    break
                self._raise_for_error(event)
        except RealtimeVoiceError:
            self.close()
            raise
        except Exception as exc:
            self.close()
            raise RealtimeVoiceError(f"could not connect to OpenAI Realtime: {exc}") from exc
        return self._clock() - started

    def respond(
        self,
        pcm_16khz: bytes,
        *,
        on_transcript: Optional[Callable[[str], None]] = None,
        on_first_audio: Optional[Callable[[], None]] = None,
        on_tool_start: Optional[Callable[[str, dict[str, Any]], None]] = None,
        on_tool_result: Optional[Callable[[RealtimeToolResult], None]] = None,
    ) -> RealtimeTurnResult:
        """Send one captured utterance and play response audio as deltas arrive."""

        self.connect()
        pcm = resample_pcm16(pcm_16khz)
        started = self._clock()
        for offset in range(0, len(pcm), 9_600):
            self._send(
                {
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(pcm[offset : offset + 9_600]).decode("ascii"),
                }
            )
        self._send({"type": "input_audio_buffer.commit"})
        self._send({"type": "response.create"})
        uploaded = self._clock()

        return self._receive_turn(
            started,
            uploaded,
            on_transcript=on_transcript,
            on_first_audio=on_first_audio,
            on_tool_start=on_tool_start,
            on_tool_result=on_tool_result,
        )

    def respond_text(
        self,
        text: str,
        *,
        on_first_audio: Optional[Callable[[], None]] = None,
        on_tool_start: Optional[Callable[[str, dict[str, Any]], None]] = None,
        on_tool_result: Optional[Callable[[RealtimeToolResult], None]] = None,
    ) -> RealtimeTurnResult:
        """Send a text turn through the same tool/audio loop (useful for diagnostics)."""

        if not text.strip():
            raise RealtimeVoiceError("Realtime text input must not be empty")
        self.connect()
        started = self._clock()
        self._send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text.strip()}],
                },
            }
        )
        self._send({"type": "response.create"})
        uploaded = self._clock()
        return self._receive_turn(
            started,
            uploaded,
            initial_transcript=text.strip(),
            transcription_done=True,
            on_first_audio=on_first_audio,
            on_tool_start=on_tool_start,
            on_tool_result=on_tool_result,
        )

    def _receive_turn(
        self,
        started: float,
        uploaded: float,
        *,
        initial_transcript: str = "",
        transcription_done: bool = False,
        on_transcript: Optional[Callable[[str], None]] = None,
        on_first_audio: Optional[Callable[[], None]] = None,
        on_tool_start: Optional[Callable[[str, dict[str, Any]], None]] = None,
        on_tool_result: Optional[Callable[[RealtimeToolResult], None]] = None,
    ) -> RealtimeTurnResult:
        """Receive audio and complete any function-call rounds until a final reply."""

        transcript = initial_transcript
        reply_parts: list[str] = []
        current_reply_parts: list[str] = []
        first_audio_at: Optional[float] = None
        response_done_at: Optional[float] = None
        response_done = False
        audio_bytes = 0
        tool_rounds = 0
        tool_results: list[RealtimeToolResult] = []
        player: Optional[Any] = None
        try:
            while not (response_done and transcription_done):
                event = self._receive()
                self._raise_for_error(event)
                event_type = event.get("type")
                if event_type in {
                    "conversation.item.input_audio_transcription.completed",
                    "conversation.item.input_audio_transcription.done",
                }:
                    transcription_done = True
                    transcript = str(event.get("transcript") or "").strip()
                    if transcript and on_transcript is not None:
                        on_transcript(transcript)
                elif event_type == "conversation.item.input_audio_transcription.failed":
                    transcription_done = True
                    logging.warning("Realtime input transcription failed: %s", event.get("error"))
                elif event_type == "response.output_audio_transcript.delta":
                    current_reply_parts.append(str(event.get("delta") or ""))
                elif event_type == "response.output_audio_transcript.done":
                    final_reply = str(event.get("transcript") or "").strip()
                    if final_reply:
                        reply_parts.append(final_reply)
                        current_reply_parts = []
                elif event_type == "response.output_audio.delta":
                    try:
                        audio = base64.b64decode(event.get("delta") or "", validate=True)
                    except (ValueError, TypeError) as exc:
                        raise RealtimeVoiceError("Realtime returned invalid Base64 audio") from exc
                    if not audio:
                        continue
                    if first_audio_at is None:
                        first_audio_at = self._clock()
                        if on_first_audio is not None:
                            on_first_audio()
                    if player is None:
                        player = self._player_factory()
                    player.write(audio)
                    audio_bytes += len(audio)
                elif event_type == "response.done":
                    response = event.get("response") or {}
                    status = str(response.get("status") or "")
                    if status and status != "completed":
                        detail = response.get("status_details")
                        raise RealtimeVoiceError(
                            f"Realtime response ended with {status}: {detail}"
                        )
                    if current_reply_parts:
                        reply_parts.append("".join(current_reply_parts).strip())
                        current_reply_parts = []
                    function_calls = self._function_calls(response)
                    if function_calls:
                        if self.tool_dispatcher is None:
                            raise RealtimeVoiceError(
                                "Realtime requested a robot tool but no dispatcher is configured"
                            )
                        tool_rounds += 1
                        if tool_rounds > self.max_tool_rounds:
                            raise RealtimeVoiceError(
                                f"Realtime exceeded {self.max_tool_rounds} robot tool rounds"
                            )
                        for function_call in function_calls:
                            tool_result = self._execute_tool_call(
                                function_call,
                                on_tool_start=on_tool_start,
                            )
                            tool_results.append(tool_result)
                            if on_tool_result is not None:
                                on_tool_result(tool_result)
                            output = {
                                "ok": tool_result.ok,
                                "result": self._compact_tool_result(tool_result.result),
                            }
                            self._send(
                                {
                                    "type": "conversation.item.create",
                                    "item": {
                                        "type": "function_call_output",
                                        "call_id": function_call["call_id"],
                                        "output": json.dumps(
                                            output, separators=(",", ":"), ensure_ascii=False
                                        ),
                                    },
                                }
                            )
                        self._send({"type": "response.create"})
                    else:
                        response_done_at = self._clock()
                        response_done = True
        finally:
            if player is not None:
                player.close()
        finished = self._clock()
        response_done_at = response_done_at or finished
        return RealtimeTurnResult(
            transcript=transcript,
            reply=" ".join(part for part in reply_parts if part).strip(),
            upload_seconds=uploaded - started,
            first_audio_seconds=(
                None if first_audio_at is None else first_audio_at - started
            ),
            response_seconds=response_done_at - started,
            total_seconds=finished - started,
            audio_seconds=audio_bytes / (REALTIME_SAMPLE_RATE * 2),
            tool_calls=tuple(tool_results),
        )

    @staticmethod
    def _function_calls(response: dict[str, Any]) -> list[dict[str, str]]:
        calls: list[dict[str, str]] = []
        output = response.get("output") or []
        if not isinstance(output, list):
            return calls
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "function_call":
                continue
            name = str(item.get("name") or "").strip()
            call_id = str(item.get("call_id") or "").strip()
            if not name or not call_id:
                raise RealtimeVoiceError("Realtime returned an incomplete function call")
            arguments = item.get("arguments")
            calls.append(
                {
                    "name": name,
                    "call_id": call_id,
                    "arguments": arguments if isinstance(arguments, str) else "{}",
                }
            )
        return calls

    def _execute_tool_call(
        self,
        function_call: dict[str, str],
        *,
        on_tool_start: Optional[Callable[[str, dict[str, Any]], None]],
    ) -> RealtimeToolResult:
        name = function_call["name"]
        started = self._clock()
        try:
            arguments = json.loads(function_call["arguments"] or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("tool arguments must be a JSON object")
            if on_tool_start is not None:
                on_tool_start(name, arguments)
            result = self.tool_dispatcher.dispatch(name, arguments)
            if not isinstance(result, dict):
                raise ValueError("robot tool returned a non-object result")
            return RealtimeToolResult(
                name, arguments, result, self._clock() - started, True
            )
        except Exception as exc:
            logging.warning("HiBot tool %s failed: %s", name, exc)
            return RealtimeToolResult(
                name,
                locals().get("arguments", {}),
                {"error": str(exc)},
                self._clock() - started,
                False,
            )

    @classmethod
    def _compact_tool_result(cls, value: Any) -> Any:
        """Keep large camera data out of function outputs while retaining detections."""

        if isinstance(value, dict):
            compact: dict[str, Any] = {}
            for key, item in value.items():
                if key.lower() in {"annotated_image", "image", "image_base64", "frame"}:
                    compact[key] = "[annotated image omitted from voice model context]"
                else:
                    compact[key] = cls._compact_tool_result(item)
            return compact
        if isinstance(value, list):
            return [cls._compact_tool_result(item) for item in value]
        if isinstance(value, str) and len(value) > 4000:
            return value[:4000] + "…[truncated]"
        return value

    def close(self) -> None:
        socket, self._socket = self._socket, None
        if socket is not None:
            try:
                socket.close()
            except Exception as exc:
                logging.warning("could not close Realtime socket cleanly: %s", exc)

    def _send(self, event: dict[str, Any]) -> None:
        if self._socket is None:
            raise RealtimeVoiceError("Realtime WebSocket is not connected")
        self._socket.send(json.dumps(event, separators=(",", ":")))

    def _receive(self) -> dict[str, Any]:
        if self._socket is None:
            raise RealtimeVoiceError("Realtime WebSocket is not connected")
        try:
            raw = self._socket.recv()
            event = json.loads(raw)
        except Exception as exc:
            raise RealtimeVoiceError(f"could not receive a Realtime event: {exc}") from exc
        if not isinstance(event, dict):
            raise RealtimeVoiceError("Realtime returned a non-object event")
        return event

    @staticmethod
    def _raise_for_error(event: dict[str, Any]) -> None:
        if event.get("type") != "error":
            return
        error = event.get("error") or {}
        message = error.get("message") if isinstance(error, dict) else None
        raise RealtimeVoiceError(
            f"Realtime API error: {message or error or 'unknown error'}"
        )


class RealtimeVoiceConversation:
    """Run bounded follow-up turns without routing through Hermes Agent."""

    EXIT_PHRASES = {
        "bye",
        "cancel",
        "goodbye",
        "never mind",
        "stop",
        "stop listening",
        "that's all",
        "that is all",
        "end conversation",
    }

    def __init__(
        self,
        source: Any,
        recorder: Any,
        client: OpenAIRealtimeClient,
        *,
        initial_timeout: float = 10,
        followup_timeout: float = 8,
        max_turns: int = 6,
        max_silent_cycles: int = 3,
        thinking_start: Optional[Callable[[], None]] = None,
        thinking_stop: Optional[Callable[[], None]] = None,
        voice_direction_start: Optional[Callable[[], None]] = None,
        voice_direction_stop: Optional[Callable[[], None]] = None,
        status: Any = None,
        settle_seconds: float = 0.25,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.source = source
        self.recorder = recorder
        self.client = client
        self.initial_timeout = initial_timeout
        self.followup_timeout = followup_timeout
        self.max_turns = max_turns
        self.max_silent_cycles = max_silent_cycles
        self.thinking_start = thinking_start
        self.thinking_stop = thinking_stop
        self.voice_direction_start = voice_direction_start
        self.voice_direction_stop = voice_direction_stop
        self.status = status
        self.settle_seconds = settle_seconds
        self._sleep = sleeper
        self._clock = clock

    @classmethod
    def _is_exit(cls, transcript: str) -> bool:
        return transcript.lower().strip(" .!?\n\t") in cls.EXIT_PHRASES

    def run(self) -> None:
        session_name = f"hibot-realtime-{int(time.time())}"
        timeout = self.initial_timeout
        silent_cycles = 0
        turns = 0
        failed = False
        self._status("begin", session_name)
        self._status("message", "hibot", "I'm here.")
        try:
            self._status(
                "step",
                "connecting",
                "Connecting HiBot's streaming voice session…",
                tool=f"OpenAI Realtime · {self.client.model}",
            )
            handshake = self.client.connect()
            logging.info("Realtime session connected in %.2f s", handshake)
            while turns < self.max_turns:
                self._status(
                    "step",
                    "listening",
                    "Listening for your question…",
                    tool="ALSA · ReSpeaker USB 4 Mic Array",
                )
                if self.settle_seconds:
                    self._sleep(self.settle_seconds)
                capture_started = self._clock()
                self.source.start()
                try:
                    pcm = self.recorder.capture(
                        self.source,
                        start_timeout=timeout,
                        on_speech_start=lambda: self._set_voice_direction(True),
                    )
                finally:
                    self.source.stop()
                    self._set_voice_direction(False)
                capture_seconds = self._clock() - capture_started
                if pcm is None:
                    silent_cycles += 1
                    if silent_cycles >= self.max_silent_cycles:
                        return
                    timeout = self.followup_timeout
                    continue

                silent_cycles = 0
                turns += 1
                transcript_published = False

                def publish_transcript(text: str) -> None:
                    nonlocal transcript_published
                    if text and not transcript_published:
                        transcript_published = True
                        self._status("message", "user", text)

                def first_audio() -> None:
                    self._set_thinking_leds(False)
                    self._status(
                        "step",
                        "speaking",
                        "Streaming HiBot's spoken reply…",
                        tool="OpenAI Realtime audio · aplay",
                    )

                def tool_start(name: str, arguments: dict[str, Any]) -> None:
                    detail = json.dumps(arguments, separators=(",", ":"), ensure_ascii=False)
                    if len(detail) > 240:
                        detail = detail[:240] + "…"
                    self._status(
                        "step",
                        "tool",
                        f"Running robot action {name} with {detail}…",
                        tool=f"HiBot shared MCP action · {name}",
                    )

                def tool_result(result: RealtimeToolResult) -> None:
                    if result.ok:
                        text = (
                            f"Robot action {result.name} completed in "
                            f"{result.elapsed_seconds:.2f} s."
                        )
                    else:
                        text = (
                            f"Robot action {result.name} failed in "
                            f"{result.elapsed_seconds:.2f} s: "
                            f"{result.result.get('error', 'unknown error')}"
                        )
                    self._status(
                        "step",
                        "thinking",
                        text,
                        tool=f"HiBot shared MCP action · {result.name}",
                    )

                self._status(
                    "step",
                    "thinking",
                    "HiBot is thinking…",
                    tool=f"OpenAI Realtime · {self.client.model} · direct audio stream",
                )
                self._set_thinking_leds(True)
                try:
                    result = self.client.respond(
                        pcm,
                        on_transcript=publish_transcript,
                        on_first_audio=first_audio,
                        on_tool_start=tool_start,
                        on_tool_result=tool_result,
                    )
                finally:
                    self._set_thinking_leds(False)

                publish_transcript(result.transcript)
                if result.reply:
                    self._status("message", "hibot", result.reply)
                first_audio_label = (
                    f"{result.first_audio_seconds:.2f} s"
                    if result.first_audio_seconds is not None
                    else "not received"
                )
                self._status(
                    "step",
                    "complete",
                    (
                        f"Turn timing: capture {capture_seconds:.2f} s; "
                        f"speech sent to first audio {first_audio_label}; "
                        f"stream/playback complete {result.total_seconds:.2f} s."
                    ),
                    tool=f"OpenAI Realtime · {self.client.model} · latency metrics",
                )
                logging.info(
                    "Realtime turn: capture %.2f s, upload %.3f s, first audio %s, "
                    "response %.2f s, playback complete %.2f s, audio %.2f s",
                    capture_seconds,
                    result.upload_seconds,
                    (
                        f"{result.first_audio_seconds:.2f} s"
                        if result.first_audio_seconds is not None
                        else "none"
                    ),
                    result.response_seconds,
                    result.total_seconds,
                    result.audio_seconds,
                )
                if self._is_exit(result.transcript):
                    return
                timeout = self.followup_timeout
        except Exception as exc:
            failed = True
            logging.error("HiBot Realtime voice conversation failed: %s", exc)
            self._status("error", f"HiBot streaming voice failed: {exc}")
        finally:
            self.source.stop()
            self.client.close()
            self._set_thinking_leds(False)
            self._set_voice_direction(False)
            if not failed:
                self._status("finish", "Voice conversation ended.")

    def _status(self, method: str, *args: Any, **kwargs: Any) -> None:
        if self.status is None:
            return
        try:
            getattr(self.status, method)(*args, **kwargs)
        except Exception as exc:
            logging.warning("could not publish voice conversation status: %s", exc)

    def _set_thinking_leds(self, active: bool) -> None:
        action = self.thinking_start if active else self.thinking_stop
        if action is None:
            return
        try:
            action()
        except Exception as exc:
            logging.warning("could not update ReSpeaker thinking LEDs: %s", exc)

    def _set_voice_direction(self, active: bool) -> None:
        action = self.voice_direction_start if active else self.voice_direction_stop
        if action is None:
            return
        try:
            action()
        except Exception as exc:
            logging.warning("could not update ReSpeaker direction LED: %s", exc)
