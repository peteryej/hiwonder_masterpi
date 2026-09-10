"""Local openWakeWord listener for the ReSpeaker USB microphone array."""

from __future__ import annotations

import argparse
import io
import logging
from logging.handlers import RotatingFileHandler
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import wave
from collections import deque
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

import numpy as np

from .chat import ChatError, ChatInterrupted, NoSpeechDetected
from .realtime_voice import RealtimeVoiceError


SAMPLE_RATE = 16_000
FRAME_SAMPLES = 1_280  # openWakeWord's preferred 80 ms frame at 16 kHz.
FRAME_BYTES = FRAME_SAMPLES * 2
DEFAULT_CAPTURE_DEVICE = "plughw:CARD=ArrayUAC10,DEV=0"
DEFAULT_PLAYBACK_DEVICE = DEFAULT_CAPTURE_DEVICE
DEFAULT_DIAGNOSTIC_LOG = Path.home() / ".local/state/masterpi/voice-diagnostics.log"
FEATURE_MODEL_URLS = {
    "melspectrogram.onnx": (
        "https://github.com/dscripka/openWakeWord/releases/download/"
        "v0.5.1/melspectrogram.onnx"
    ),
    "embedding_model.onnx": (
        "https://github.com/dscripka/openWakeWord/releases/download/"
        "v0.5.1/embedding_model.onnx"
    ),
}


class WakeWordError(RuntimeError):
    """Raised when wake-word capture, inference, or response fails."""


class ARecordSource:
    """Read fixed-size raw PCM frames from an ALSA capture device."""

    def __init__(
        self,
        device: str = DEFAULT_CAPTURE_DEVICE,
        *,
        popen: Callable[..., Any] = subprocess.Popen,
    ) -> None:
        self.device = device
        self._popen = popen
        self._process: Optional[Any] = None

    def start(self) -> None:
        if self._process is not None:
            return
        try:
            self._process = self._popen(
                [
                    "arecord",
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
                    str(SAMPLE_RATE),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            raise WakeWordError(f"could not start ReSpeaker capture: {exc}") from exc

    def read(self) -> bytes:
        if self._process is None or self._process.stdout is None:
            raise WakeWordError("ReSpeaker capture is not running")
        chunks = bytearray()
        while len(chunks) < FRAME_BYTES:
            chunk = self._process.stdout.read(FRAME_BYTES - len(chunks))
            if not chunk:
                detail = ""
                if self._process.stderr is not None:
                    detail = self._process.stderr.read().decode(errors="replace").strip()
                raise WakeWordError(detail or "ReSpeaker capture ended unexpectedly")
            chunks.extend(chunk)
        return bytes(chunks)

    def stop(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
        finally:
            for stream in (process.stdout, process.stderr):
                close = getattr(stream, "close", None)
                if close is not None:
                    close()


class APlayResponder:
    """Play a cached response while wake-word capture is paused."""

    def __init__(
        self,
        audio_path: Path,
        device: str = DEFAULT_PLAYBACK_DEVICE,
        *,
        runner: Callable[..., Any] = subprocess.run,
    ) -> None:
        self.audio_path = Path(audio_path)
        self.device = device
        self._runner = runner

    def play(self) -> None:
        try:
            completed = self._runner(
                ["aplay", "-q", "-D", self.device, str(self.audio_path)],
                text=True,
                capture_output=True,
            )
        except OSError as exc:
            raise WakeWordError(f"could not play wake-word response: {exc}") from exc
        if completed.returncode:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise WakeWordError(detail or "wake-word response playback failed")


class WakeWordListener:
    """Feed ReSpeaker frames into one openWakeWord model."""

    def __init__(
        self,
        model: Any,
        source: ARecordSource,
        responder: APlayResponder,
        *,
        threshold: float = 0.5,
        cooldown: float = 0.75,
        on_wake: Optional[Callable[[], None]] = None,
        voice_direction_start: Optional[Callable[[], None]] = None,
        voice_direction_stop: Optional[Callable[[], None]] = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not 0 < threshold <= 1:
            raise WakeWordError("threshold must be greater than 0 and at most 1")
        if not 0 <= cooldown <= 30:
            raise WakeWordError("cooldown must be between 0 and 30 seconds")
        self.model = model
        self.source = source
        self.responder = responder
        self.threshold = threshold
        self.cooldown = cooldown
        self.on_wake = on_wake
        self.voice_direction_start = voice_direction_start
        self.voice_direction_stop = voice_direction_stop
        self._sleep = sleeper

    def run(self, *, max_detections: Optional[int] = None) -> int:
        detections = 0
        self.source.start()
        try:
            while max_detections is None or detections < max_detections:
                frame = np.frombuffer(self.source.read(), dtype="<i2")
                scores: Mapping[str, float] = self.model.predict(frame)
                if not scores:
                    continue
                label, score = max(scores.items(), key=lambda item: float(item[1]))
                if float(score) < self.threshold:
                    continue

                logging.info("wake word detected: %s (%.3f)", label, score)
                self._set_voice_direction(True)
                self.source.stop()
                try:
                    self.responder.play()
                finally:
                    self._set_voice_direction(False)
                if self.on_wake is not None:
                    self.on_wake()
                detections += 1
                if self.cooldown:
                    self._sleep(self.cooldown)
                self.model.reset()
                if max_detections is None or detections < max_detections:
                    self.source.start()
        finally:
            self.source.stop()
        return detections

    def _set_voice_direction(self, active: bool) -> None:
        action = self.voice_direction_start if active else self.voice_direction_stop
        if action is None:
            return
        try:
            action()
        except Exception as exc:
            logging.warning(
                "could not %s ReSpeaker voice-direction LED: %s",
                "show" if active else "clear",
                exc,
            )


def pcm_to_wav(pcm: bytes) -> bytes:
    """Wrap mono 16-bit 16 kHz PCM in a WAV container for Hermes STT."""

    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm)
    return output.getvalue()


class UtteranceRecorder:
    """Capture one utterance using PCM energy and end-of-speech silence."""

    def __init__(
        self,
        *,
        speech_threshold: float = 300,
        silence_seconds: float = 1.0,
        max_seconds: float = 20,
        speech_start_frames: int = 4,
        pre_roll_seconds: float = 0.4,
    ) -> None:
        if not 0 < speech_threshold <= 32_767:
            raise WakeWordError("speech threshold must be greater than 0 and at most 32767")
        if not 0.24 <= silence_seconds <= 5:
            raise WakeWordError("speech silence must be between 0.24 and 5 seconds")
        if not 1 <= max_seconds <= 120:
            raise WakeWordError("maximum utterance length must be between 1 and 120 seconds")
        if not 1 <= speech_start_frames <= 10:
            raise WakeWordError("speech start frames must be between 1 and 10")
        self.speech_threshold = speech_threshold
        self.silence_frames = max(1, round(silence_seconds / 0.08))
        self.max_frames = max(1, round(max_seconds / 0.08))
        self.speech_start_frames = speech_start_frames
        self.pre_roll_frames = max(1, round(pre_roll_seconds / 0.08))

    @staticmethod
    def _rms(frame: bytes) -> float:
        samples = np.frombuffer(frame, dtype="<i2").astype(np.float64)
        return float(np.sqrt(np.mean(samples * samples))) if samples.size else 0.0

    def calibrate(self, source: ARecordSource, *, seconds: float = 0.4) -> float:
        """Measure the quiet-room RMS before reply audio starts."""

        frame_count = max(1, round(seconds / 0.08))
        levels = [self._rms(source.read()) for _ in range(frame_count)]
        return float(np.median(levels))

    def capture(
        self,
        source: ARecordSource,
        *,
        start_timeout: float,
        stop_when: Optional[Callable[[], bool]] = None,
        speech_threshold: Optional[float] = None,
        on_speech_start: Optional[Callable[[], None]] = None,
    ) -> Optional[bytes]:
        if not 0 < start_timeout <= 120:
            raise WakeWordError("speech start timeout must be greater than 0 and at most 120 seconds")
        threshold = self.speech_threshold if speech_threshold is None else speech_threshold
        if not 0 < threshold <= 32_767:
            raise WakeWordError("speech threshold must be greater than 0 and at most 32767")
        start_limit = max(1, round(start_timeout / 0.08))
        pre_roll: deque[bytes] = deque(maxlen=self.pre_roll_frames)
        frames: list[bytes] = []
        consecutive_speech = 0
        pre_speech_levels: list[float] = []

        for _ in range(start_limit):
            frame = source.read()
            pre_roll.append(frame)
            rms = self._rms(frame)
            pre_speech_levels.append(rms)
            consecutive_speech = consecutive_speech + 1 if rms >= threshold else 0
            if consecutive_speech >= self.speech_start_frames:
                frames.extend(pre_roll)
                logging.info(
                    "speech started: rms=%.0f threshold=%.0f wait=%.2f s",
                    rms,
                    threshold,
                    len(pre_speech_levels) * 0.08,
                )
                if on_speech_start is not None:
                    on_speech_start()
                break
            if stop_when is not None and stop_when():
                return None
        else:
            median, percentile_95, maximum = self._level_summary(pre_speech_levels)
            logging.info(
                "utterance capture ended: reason=start_timeout wait=%.2f s "
                "threshold=%.0f rms_median=%.0f rms_p95=%.0f rms_max=%.0f",
                len(pre_speech_levels) * 0.08,
                threshold,
                median,
                percentile_95,
                maximum,
            )
            return None

        consecutive_silence = 0
        recording_levels: list[float] = []
        reason = "max_duration"
        for _ in range(max(0, self.max_frames - len(frames))):
            frame = source.read()
            frames.append(frame)
            rms = self._rms(frame)
            recording_levels.append(rms)
            if rms < threshold:
                consecutive_silence += 1
                if consecutive_silence >= self.silence_frames:
                    reason = "ending_silence"
                    break
            else:
                consecutive_silence = 0
        trailing = recording_levels[-self.silence_frames :]
        median, percentile_95, maximum = self._level_summary(trailing)
        logging.info(
            "utterance capture ended: reason=%s wait=%.2f s audio=%.2f s "
            "threshold=%.0f tail_rms_median=%.0f tail_rms_p95=%.0f tail_rms_max=%.0f",
            reason,
            len(pre_speech_levels) * 0.08,
            len(frames) * 0.08,
            threshold,
            median,
            percentile_95,
            maximum,
        )
        return b"".join(frames)

    @staticmethod
    def _level_summary(levels: Sequence[float]) -> tuple[float, float, float]:
        if not levels:
            return 0.0, 0.0, 0.0
        values = np.asarray(levels, dtype=np.float64)
        return float(np.median(values)), float(np.percentile(values, 95)), float(np.max(values))


class HermesReplySpeaker:
    """Synthesize a Hermes reply, convert it to WAV, and play it locally."""

    def __init__(
        self,
        chat: Any,
        playback_device: str = DEFAULT_PLAYBACK_DEVICE,
        *,
        runner: Callable[..., Any] = subprocess.run,
        popen: Callable[..., Any] = subprocess.Popen,
        barge_in_grace_seconds: float = 0.5,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.chat = chat
        self.playback_device = playback_device
        self._runner = runner
        self._popen = popen
        if not 0 <= barge_in_grace_seconds <= 5:
            raise WakeWordError("barge-in grace must be between 0 and 5 seconds")
        self.barge_in_grace_seconds = barge_in_grace_seconds
        self._sleep = sleeper

    def _convert_reply(self, text: str, directory: str) -> Path:
        audio, content_type = self.chat.synthesize(text)
        suffix = ".ogg" if content_type.partition(";")[0] == "audio/ogg" else ".mp3"
        source = Path(directory) / f"reply{suffix}"
        output = Path(directory) / "reply.wav"
        source.write_bytes(audio)
        try:
            completed = self._runner(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(source),
                    "-ar",
                    str(SAMPLE_RATE),
                    "-ac",
                    "1",
                    str(output),
                ],
                text=True,
                capture_output=True,
            )
        except OSError as exc:
            raise WakeWordError(f"could not convert Hermes reply audio: {exc}") from exc
        if completed.returncode:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise WakeWordError(detail or "Hermes reply audio conversion failed")
        return output

    def __call__(self, text: str) -> None:
        with tempfile.TemporaryDirectory(prefix="masterpi-voice-") as directory:
            output = self._convert_reply(text, directory)
            APlayResponder(output, self.playback_device, runner=self._runner).play()

    def speak_interruptible(
        self,
        text: str,
        source: ARecordSource,
        recorder: UtteranceRecorder,
        *,
        speech_threshold: float,
    ) -> Optional[bytes]:
        """Play a reply while listening for echo-cancelled user speech."""

        with tempfile.TemporaryDirectory(prefix="masterpi-voice-") as directory:
            output = self._convert_reply(text, directory)
            try:
                process = self._popen(
                    ["aplay", "-q", "-D", self.playback_device, str(output)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            except OSError as exc:
                raise WakeWordError(f"could not play Hermes reply: {exc}") from exc

            if self.barge_in_grace_seconds:
                self._sleep(self.barge_in_grace_seconds)
            if process.poll() is not None:
                self._check_playback(process)
                return None

            def stop_playback() -> None:
                if process.poll() is None:
                    process.terminate()

            source.start()
            try:
                pcm = recorder.capture(
                    source,
                    start_timeout=120,
                    stop_when=lambda: process.poll() is not None,
                    speech_threshold=speech_threshold,
                    on_speech_start=stop_playback,
                )
            finally:
                source.stop()

            if pcm is not None:
                stop_playback()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
                return pcm
            self._check_playback(process)
            return None

    @staticmethod
    def _check_playback(process: Any) -> None:
        returncode = process.wait(timeout=2)
        if returncode:
            detail = ""
            if process.stderr is not None:
                detail = process.stderr.read()
                if isinstance(detail, bytes):
                    detail = detail.decode(errors="replace")
                detail = detail.strip()
            raise WakeWordError(detail or "Hermes reply playback failed")


class VoiceConversation:
    """Run a short multi-turn spoken conversation through Hermes Agent."""

    INTERRUPTION_NOTE = (
        "[The user interrupted the previous response while it was being generated or spoken. "
        "Respond to their new request instead.] "
    )

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
        source: ARecordSource,
        recorder: UtteranceRecorder,
        chat: Any,
        speaker: Callable[[str], None],
        *,
        initial_timeout: float = 10,
        followup_timeout: float = 8,
        max_turns: int = 6,
        max_silent_cycles: int = 3,
        barge_in: bool = True,
        barge_in_min_threshold: float = 500,
        barge_in_threshold_multiplier: float = 3.0,
        barge_in_calibration_seconds: float = 0.4,
        thinking_start: Optional[Callable[[], None]] = None,
        thinking_stop: Optional[Callable[[], None]] = None,
        voice_direction_start: Optional[Callable[[], None]] = None,
        voice_direction_stop: Optional[Callable[[], None]] = None,
        status: Any = None,
        settle_seconds: float = 0.25,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not 1 <= max_turns <= 30:
            raise WakeWordError("conversation max turns must be between 1 and 30")
        if not 1 <= max_silent_cycles <= 10:
            raise WakeWordError("silent cycle limit must be between 1 and 10")
        if not 1 <= barge_in_min_threshold <= 32_767:
            raise WakeWordError("minimum barge-in threshold must be between 1 and 32767")
        if not 1 <= barge_in_threshold_multiplier <= 20:
            raise WakeWordError("barge-in threshold multiplier must be between 1 and 20")
        if not 0.08 <= barge_in_calibration_seconds <= 5:
            raise WakeWordError("barge-in calibration must be between 0.08 and 5 seconds")
        if not 0 <= settle_seconds <= 5:
            raise WakeWordError("conversation settle time must be between 0 and 5 seconds")
        self.source = source
        self.recorder = recorder
        self.chat = chat
        self.speaker = speaker
        self.initial_timeout = initial_timeout
        self.followup_timeout = followup_timeout
        self.max_turns = max_turns
        self.max_silent_cycles = max_silent_cycles
        self.barge_in = barge_in
        self.barge_in_min_threshold = barge_in_min_threshold
        self.barge_in_threshold_multiplier = barge_in_threshold_multiplier
        self.barge_in_calibration_seconds = barge_in_calibration_seconds
        self.thinking_start = thinking_start
        self.thinking_stop = thinking_stop
        self.voice_direction_start = voice_direction_start
        self.voice_direction_stop = voice_direction_stop
        self.status = status
        self.settle_seconds = settle_seconds
        self._sleep = sleeper

    @classmethod
    def _is_exit(cls, transcript: str) -> bool:
        normalized = transcript.lower().strip(" .!?\n\t")
        return normalized in cls.EXIT_PHRASES

    def run(self) -> None:
        timeout = self.initial_timeout
        pending_pcm: Optional[bytes] = None
        silent_cycles = 0
        turns = 0
        previous_reply_interrupted = False
        status_started = False
        status_failed = False
        end_status = "Voice conversation ended."
        try:
            session_name = f"hibot-voice-{int(time.time())}"
            new_session = getattr(self.chat, "new_session", None)
            if callable(new_session):
                session_name = new_session()
                logging.info("started fresh Hermes voice session: %s", session_name)
            self._status_call("begin", session_name)
            status_started = True
            self._status_call("message", "hibot", "I'm here.")

            while turns < self.max_turns:
                pcm, pending_pcm = pending_pcm, None
                if pcm is None:
                    self._status_call(
                        "step",
                        "listening",
                        "Listening for your question…",
                        tool="ALSA · ReSpeaker USB 4 Mic Array",
                    )
                    if self.settle_seconds:
                        self._sleep(self.settle_seconds)
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
                if pcm is None:
                    silent_cycles += 1
                    if silent_cycles >= self.max_silent_cycles:
                        logging.info(
                            "voice conversation ended after %d silent cycles",
                            silent_cycles,
                        )
                        end_status = "Voice conversation ended after no speech was heard."
                        return
                    self._status_call(
                        "step",
                        "listening",
                        f"No speech heard ({silent_cycles}/{self.max_silent_cycles}); listening again…",
                        tool="Voice activity detector",
                    )
                    timeout = self.followup_timeout
                    continue

                silent_cycles = 0
                self._status_call(
                    "step",
                    "transcribing",
                    "Transcribing your speech…",
                    tool="Hermes voice STT · local faster-whisper base",
                )
                try:
                    transcript = self.chat.transcribe(pcm_to_wav(pcm), "audio/wav")
                except NoSpeechDetected:
                    silent_cycles += 1
                    if silent_cycles >= self.max_silent_cycles:
                        logging.info(
                            "voice conversation ended after %d silent STT cycles",
                            silent_cycles,
                        )
                        end_status = "Voice conversation ended after no speech was recognized."
                        return
                    self._status_call(
                        "step",
                        "listening",
                        f"No words recognized ({silent_cycles}/{self.max_silent_cycles}); listening again…",
                        tool="Hermes voice STT",
                    )
                    timeout = self.followup_timeout
                    continue
                logging.info("user: %s", transcript)
                self._status_call("message", "user", transcript)
                if self._is_exit(transcript):
                    logging.info("voice conversation ended by stop phrase")
                    end_status = "Voice conversation ended by your stop phrase."
                    return

                turns += 1
                message = (
                    self.INTERRUPTION_NOTE + transcript
                    if previous_reply_interrupted
                    else transcript
                )
                previous_reply_interrupted = False
                self._status_call(
                    "step",
                    "thinking",
                    "Hermes is thinking…",
                    tool=self._agent_status_label(),
                )
                reply, pending_pcm, barge_threshold = self._reply(message)
                if pending_pcm is not None:
                    previous_reply_interrupted = True
                    self._status_call(
                        "step",
                        "interrupted",
                        "You interrupted Hermes while it was thinking; transcribing the new request…",
                        tool="Voice activity detector · barge-in",
                    )
                    timeout = self.followup_timeout
                    continue
                logging.info("hibot: %s", reply)
                self._status_call("message", "hibot", reply)
                self._status_call(
                    "step",
                    "speaking",
                    "Generating and playing the spoken reply…",
                    tool="Hermes TTS · Edge TTS + aplay",
                )
                speak_interruptible = getattr(self.speaker, "speak_interruptible", None)
                if self.barge_in and callable(speak_interruptible):
                    pending_pcm = speak_interruptible(
                        reply,
                        self.source,
                        self.recorder,
                        speech_threshold=barge_threshold,
                    )
                    previous_reply_interrupted = pending_pcm is not None
                else:
                    self.speaker(reply)
                if pending_pcm is not None:
                    self._status_call(
                        "step",
                        "interrupted",
                        "You interrupted the spoken reply; transcribing the new request…",
                        tool="Voice activity detector · barge-in",
                    )
                timeout = self.followup_timeout
        except ChatError as exc:
            logging.error("Hermes voice conversation failed: %s", exc)
            status_failed = True
            self._status_call("error", f"Hermes voice conversation failed: {exc}")
        except Exception as exc:
            status_failed = True
            self._status_call("error", f"Voice conversation failed: {exc}")
            raise
        finally:
            self.source.stop()
            self._set_voice_direction(False)
            if status_started and not status_failed:
                self._status_call("finish", end_status)

    def _reply(self, transcript: str) -> tuple[str, Optional[bytes], float]:
        if not self.barge_in:
            self._set_thinking_leds(True)
            try:
                return self.chat.reply(transcript), None, getattr(
                    self.recorder, "speech_threshold", 200
                )
            finally:
                self._set_thinking_leds(False)

        self.source.start()
        try:
            quiet_floor = self.recorder.calibrate(
                self.source,
                seconds=self.barge_in_calibration_seconds,
            )
            threshold = min(
                32_767,
                max(
                    self.recorder.speech_threshold,
                    self.barge_in_min_threshold,
                    quiet_floor * self.barge_in_threshold_multiplier,
                ),
            )
            logging.info(
                "barge-in calibrated: floor %.0f, threshold %.0f",
                quiet_floor,
                threshold,
            )
            cancel = threading.Event()
            done = threading.Event()
            result: dict[str, str] = {}
            failure: list[Exception] = []

            def ask_hermes() -> None:
                try:
                    interruptible = getattr(self.chat, "reply_interruptible", None)
                    if callable(interruptible):
                        result["reply"] = interruptible(transcript, cancel)
                    else:
                        result["reply"] = self.chat.reply(transcript)
                except Exception as exc:
                    failure.append(exc)
                finally:
                    done.set()

            worker = threading.Thread(target=ask_hermes, daemon=True)
            self._set_thinking_leds(True)
            worker.start()
            pcm = self.recorder.capture(
                self.source,
                start_timeout=120,
                stop_when=done.is_set,
                speech_threshold=threshold,
                on_speech_start=cancel.set,
            )
            if pcm is not None:
                cancel.set()
                worker.join(timeout=10)
                if worker.is_alive():
                    raise ChatError("Hermes Agent did not stop after voice interruption")
                unexpected = [exc for exc in failure if not isinstance(exc, ChatInterrupted)]
                if unexpected:
                    raise unexpected[0]
                logging.info("Hermes reply interrupted while generating")
                return "", pcm, threshold

            worker.join(timeout=30)
            if worker.is_alive():
                raise ChatError("Hermes Agent did not finish its reply")
            if failure:
                raise failure[0]
            return result.get("reply", ""), None, threshold
        finally:
            self._set_thinking_leds(False)
            self.source.stop()

    def _status_call(self, method: str, *args: Any, **kwargs: Any) -> None:
        if self.status is None:
            return
        try:
            getattr(self.status, method)(*args, **kwargs)
        except Exception as exc:
            logging.warning("could not publish voice conversation status: %s", exc)

    def _agent_status_label(self) -> str:
        details = ["Hermes Agent"]
        if getattr(self.chat, "model", None):
            details.append(str(self.chat.model))
        if getattr(self.chat, "reasoning", None):
            details.append(f"{self.chat.reasoning} reasoning")
        details.append("safe toolset")
        return " · ".join(details)

    def _set_thinking_leds(self, active: bool) -> None:
        action = self.thinking_start if active else self.thinking_stop
        if action is None:
            return
        try:
            action()
        except Exception as exc:
            # LEDs are status-only: a missing/permission-denied USB control
            # endpoint must never break the spoken conversation.
            logging.warning(
                "could not %s ReSpeaker thinking LEDs: %s",
                "start" if active else "stop",
                exc,
            )

    def _set_voice_direction(self, active: bool) -> None:
        action = self.voice_direction_start if active else self.voice_direction_stop
        if action is None:
            return
        try:
            action()
        except Exception as exc:
            logging.warning(
                "could not %s ReSpeaker voice-direction LED: %s",
                "show" if active else "clear",
                exc,
            )


def build_model(model_path: Path, feature_model_dir: Path) -> Any:
    model_path = Path(model_path)
    feature_model_dir = Path(feature_model_dir)
    required = [
        model_path,
        feature_model_dir / "melspectrogram.onnx",
        feature_model_dir / "embedding_model.onnx",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise WakeWordError("missing openWakeWord model file(s): " + ", ".join(missing))
    if model_path.suffix.lower() != ".onnx":
        raise WakeWordError("the Raspberry Pi listener requires an ONNX wake-word model")

    try:
        from openwakeword.model import Model
    except ImportError as exc:
        raise WakeWordError(
            "openWakeWord is not installed; follow the README ONNX installation steps"
        ) from exc

    try:
        return Model(
            wakeword_models=[str(model_path)],
            inference_framework="onnx",
            melspec_model_path=str(feature_model_dir / "melspectrogram.onnx"),
            embedding_model_path=str(feature_model_dir / "embedding_model.onnx"),
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise WakeWordError(f"could not load openWakeWord model: {exc}") from exc


def download_feature_models(
    target_dir: Path,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> list[Path]:
    """Download openWakeWord's two official ONNX feature models."""

    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    downloaded: list[Path] = []
    for filename, url in FEATURE_MODEL_URLS.items():
        destination = target_dir / filename
        if destination.is_file() and destination.stat().st_size:
            continue
        temporary = destination.with_suffix(destination.suffix + ".part")
        try:
            with opener(url, timeout=60) as response, temporary.open("wb") as output:
                while chunk := response.read(64 * 1024):
                    output.write(chunk)
            temporary.replace(destination)
        except (OSError, ValueError) as exc:
            temporary.unlink(missing_ok=True)
            raise WakeWordError(f"could not download {filename}: {exc}") from exc
        downloaded.append(destination)
    return downloaded


def _default_project_path(*parts: str) -> Path:
    return Path(__file__).resolve().parents[2].joinpath(*parts)


def _enable_diagnostic_log(path: Path) -> None:
    """Keep recent voice diagnostics even when the user journal is volatile."""

    target = Path(path).expanduser()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            target,
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
    except OSError as exc:
        logging.warning("could not open voice diagnostic log %s: %s", target, exc)
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.info("voice diagnostic log: %s", target)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="masterpi-wake-word",
        description="Listen for a local openWakeWord model using the ReSpeaker array",
    )
    parser.add_argument(
        "--model",
        type=Path,
        default=_default_project_path("models", "openwakeword", "hello_hibot.onnx"),
    )
    parser.add_argument(
        "--feature-model-dir",
        type=Path,
        default=_default_project_path("models", "openwakeword"),
    )
    parser.add_argument(
        "--reply-audio",
        type=Path,
        default=_default_project_path("assets", "im-here.wav"),
    )
    parser.add_argument("--capture-device", default=DEFAULT_CAPTURE_DEVICE)
    parser.add_argument("--playback-device", default=DEFAULT_PLAYBACK_DEVICE)
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--cooldown", type=float, default=0.75)
    parser.add_argument(
        "--conversation",
        action="store_true",
        help="after waking, start a direct streaming voice conversation and accept follow-ups",
    )
    parser.add_argument(
        "--conversation-backend",
        choices=("auto", "realtime", "hermes"),
        default="auto",
        help="voice backend; auto selects direct Realtime when OPENAI_API_KEY is set",
    )
    parser.add_argument("--realtime-model", default="gpt-realtime-2.1")
    parser.add_argument("--realtime-voice", default="marin")
    parser.add_argument("--speech-threshold", type=float, default=400)
    parser.add_argument("--speech-silence", type=float, default=3.0)
    parser.add_argument("--speech-start-timeout", type=float, default=15)
    parser.add_argument("--followup-timeout", type=float, default=8)
    parser.add_argument("--max-utterance", type=float, default=30)
    parser.add_argument("--conversation-max-turns", type=int, default=30)
    parser.add_argument("--max-silent-cycles", type=int, default=2)
    parser.add_argument(
        "--diagnostic-log",
        type=Path,
        default=DEFAULT_DIAGNOSTIC_LOG,
        help="rotating log for capture levels, end reasons, and turn timing",
    )
    parser.add_argument(
        "--barge-in",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="allow interruptions in the legacy Hermes conversation backend",
    )
    parser.add_argument("--barge-in-threshold-multiplier", type=float, default=3.0)
    parser.add_argument("--barge-in-min-threshold", type=float, default=500)
    parser.add_argument("--barge-in-grace", type=float, default=0.5)
    parser.add_argument(
        "--download-features",
        action="store_true",
        help="download the official ONNX melspectrogram and embedding models, then exit",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _enable_diagnostic_log(args.diagnostic_log)
    try:
        if args.download_features:
            downloaded = download_feature_models(args.feature_model_dir)
            if downloaded:
                for path in downloaded:
                    print(path)
            else:
                print(f"openWakeWord feature models already present in {args.feature_model_dir}")
            return 0
        if not args.reply_audio.is_file():
            raise WakeWordError(f"wake-word response audio was not found: {args.reply_audio}")
        model = build_model(args.model, args.feature_model_dir)
        source = ARecordSource(args.capture_device)
        from .respeaker_leds import (
            read_direction,
            show_direction,
            turn_off as stop_voice_leds,
        )

        def start_voice_direction_led() -> None:
            angle = read_direction()
            pixel = show_direction(angle)
            logging.info(
                "voice direction %.0f degrees -> ReSpeaker LED %d",
                angle,
                pixel,
            )

        conversation = None
        if args.conversation:
            from .respeaker_leds import spin as start_thinking_leds
            from .voice_status import VoiceStatusWriter

            stop_thinking_leds = stop_voice_leds

            voice_status = VoiceStatusWriter()
            voice_status.idle()
            recorder = UtteranceRecorder(
                speech_threshold=args.speech_threshold,
                silence_seconds=args.speech_silence,
                max_seconds=args.max_utterance,
            )
            conversation_backend = args.conversation_backend
            if conversation_backend == "auto":
                conversation_backend = (
                    "realtime" if os.environ.get("OPENAI_API_KEY", "").strip() else "hermes"
                )
                logging.info("auto-selected %s voice backend", conversation_backend)
            if conversation_backend == "realtime":
                from .agent_client import HibotAgentClient
                from .realtime_voice import (
                    HIBOT_TOOL_REALTIME_PROMPT,
                    OpenAIRealtimeClient,
                    RealtimeVoiceConversation,
                )
                from .robot_tools import RobotToolDispatcher

                realtime = OpenAIRealtimeClient.from_environment(
                    args.playback_device,
                    model=args.realtime_model,
                    voice=args.realtime_voice,
                    instructions=HIBOT_TOOL_REALTIME_PROMPT,
                    tool_dispatcher=RobotToolDispatcher(HibotAgentClient()),
                )
                conversation = RealtimeVoiceConversation(
                    source,
                    recorder,
                    realtime,
                    initial_timeout=args.speech_start_timeout,
                    followup_timeout=args.followup_timeout,
                    max_turns=args.conversation_max_turns,
                    max_silent_cycles=args.max_silent_cycles,
                    thinking_start=start_thinking_leds,
                    thinking_stop=stop_thinking_leds,
                    voice_direction_start=start_voice_direction_led,
                    voice_direction_stop=stop_voice_leds,
                    status=voice_status,
                )
            else:
                from .chat import HermesChat

                chat = HermesChat(
                    session_name="hibot-voice",
                    model="gpt-5.6-luna",
                    provider="openai-codex",
                    reasoning="low",
                )
                conversation = VoiceConversation(
                    source,
                    recorder,
                    chat,
                    HermesReplySpeaker(
                        chat,
                        args.playback_device,
                        barge_in_grace_seconds=args.barge_in_grace,
                    ),
                    initial_timeout=args.speech_start_timeout,
                    followup_timeout=args.followup_timeout,
                    max_turns=args.conversation_max_turns,
                    max_silent_cycles=args.max_silent_cycles,
                    barge_in=args.barge_in,
                    barge_in_min_threshold=args.barge_in_min_threshold,
                    barge_in_threshold_multiplier=args.barge_in_threshold_multiplier,
                    thinking_start=start_thinking_leds,
                    thinking_stop=stop_thinking_leds,
                    voice_direction_start=start_voice_direction_led,
                    voice_direction_stop=stop_voice_leds,
                    status=voice_status,
                )
        listener = WakeWordListener(
            model,
            source,
            APlayResponder(args.reply_audio, args.playback_device),
            threshold=args.threshold,
            cooldown=args.cooldown,
            on_wake=conversation.run if conversation is not None else None,
            voice_direction_start=start_voice_direction_led,
            voice_direction_stop=stop_voice_leds,
        )
        logging.info("listening for wake word with %s", args.model)
        listener.run()
        return 0
    except KeyboardInterrupt:
        return 130
    except (WakeWordError, RealtimeVoiceError) as exc:
        print(f"masterpi-wake-word: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
