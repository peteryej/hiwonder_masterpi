"""Local openWakeWord listener for the ReSpeaker USB microphone array."""

from __future__ import annotations

import argparse
import io
import logging
import subprocess
import sys
import tempfile
import time
import urllib.request
import wave
from collections import deque
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

import numpy as np

from .chat import ChatError


SAMPLE_RATE = 16_000
FRAME_SAMPLES = 1_280  # openWakeWord's preferred 80 ms frame at 16 kHz.
FRAME_BYTES = FRAME_SAMPLES * 2
DEFAULT_CAPTURE_DEVICE = "plughw:CARD=ArrayUAC10,DEV=0"
DEFAULT_PLAYBACK_DEVICE = DEFAULT_CAPTURE_DEVICE
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
                self.source.stop()
                self.responder.play()
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
        speech_start_frames: int = 2,
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

    def capture(self, source: ARecordSource, *, start_timeout: float) -> Optional[bytes]:
        if not 0 < start_timeout <= 120:
            raise WakeWordError("speech start timeout must be greater than 0 and at most 120 seconds")
        start_limit = max(1, round(start_timeout / 0.08))
        pre_roll: deque[bytes] = deque(maxlen=self.pre_roll_frames)
        frames: list[bytes] = []
        consecutive_speech = 0

        for _ in range(start_limit):
            frame = source.read()
            pre_roll.append(frame)
            rms = self._rms(frame)
            consecutive_speech = consecutive_speech + 1 if rms >= self.speech_threshold else 0
            if consecutive_speech >= self.speech_start_frames:
                frames.extend(pre_roll)
                logging.info("speech started (RMS %.0f)", rms)
                break
        else:
            return None

        consecutive_silence = 0
        for _ in range(max(0, self.max_frames - len(frames))):
            frame = source.read()
            frames.append(frame)
            if self._rms(frame) < self.speech_threshold:
                consecutive_silence += 1
                if consecutive_silence >= self.silence_frames:
                    break
            else:
                consecutive_silence = 0
        return b"".join(frames)


class HermesReplySpeaker:
    """Synthesize a Hermes reply, convert it to WAV, and play it locally."""

    def __init__(
        self,
        chat: Any,
        playback_device: str = DEFAULT_PLAYBACK_DEVICE,
        *,
        runner: Callable[..., Any] = subprocess.run,
    ) -> None:
        self.chat = chat
        self.playback_device = playback_device
        self._runner = runner

    def __call__(self, text: str) -> None:
        audio, content_type = self.chat.synthesize(text)
        suffix = ".ogg" if content_type.partition(";")[0] == "audio/ogg" else ".mp3"
        with tempfile.TemporaryDirectory(prefix="masterpi-voice-") as directory:
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
            APlayResponder(output, self.playback_device, runner=self._runner).play()


class VoiceConversation:
    """Run a short multi-turn spoken conversation through Hermes Agent."""

    EXIT_PHRASES = {
        "bye",
        "goodbye",
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
        settle_seconds: float = 0.25,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not 1 <= max_turns <= 30:
            raise WakeWordError("conversation max turns must be between 1 and 30")
        if not 0 <= settle_seconds <= 5:
            raise WakeWordError("conversation settle time must be between 0 and 5 seconds")
        self.source = source
        self.recorder = recorder
        self.chat = chat
        self.speaker = speaker
        self.initial_timeout = initial_timeout
        self.followup_timeout = followup_timeout
        self.max_turns = max_turns
        self.settle_seconds = settle_seconds
        self._sleep = sleeper

    @classmethod
    def _is_exit(cls, transcript: str) -> bool:
        normalized = transcript.lower().strip(" .!?\n\t")
        return normalized in cls.EXIT_PHRASES

    def run(self) -> None:
        timeout = self.initial_timeout
        try:
            for _ in range(self.max_turns):
                if self.settle_seconds:
                    self._sleep(self.settle_seconds)
                self.source.start()
                pcm = self.recorder.capture(self.source, start_timeout=timeout)
                self.source.stop()
                if pcm is None:
                    logging.info("voice conversation ended after %.1f seconds without speech", timeout)
                    return

                transcript = self.chat.transcribe(pcm_to_wav(pcm), "audio/wav")
                logging.info("user: %s", transcript)
                if self._is_exit(transcript):
                    self.speaker("Goodbye.")
                    return

                reply = self.chat.reply(transcript)
                logging.info("hibot: %s", reply)
                self.speaker(reply)
                timeout = self.followup_timeout
        except ChatError as exc:
            logging.error("Hermes voice conversation failed: %s", exc)
        finally:
            self.source.stop()


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
        help="after waking, transcribe speech, ask Hermes, speak replies, and accept follow-ups",
    )
    parser.add_argument("--speech-threshold", type=float, default=300)
    parser.add_argument("--speech-silence", type=float, default=1.0)
    parser.add_argument("--speech-start-timeout", type=float, default=10)
    parser.add_argument("--followup-timeout", type=float, default=8)
    parser.add_argument("--max-utterance", type=float, default=20)
    parser.add_argument("--conversation-max-turns", type=int, default=6)
    parser.add_argument(
        "--download-features",
        action="store_true",
        help="download the official ONNX melspectrogram and embedding models, then exit",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
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
        conversation = None
        if args.conversation:
            from .chat import HermesChat

            chat = HermesChat(session_name="hibot-voice")
            conversation = VoiceConversation(
                source,
                UtteranceRecorder(
                    speech_threshold=args.speech_threshold,
                    silence_seconds=args.speech_silence,
                    max_seconds=args.max_utterance,
                ),
                chat,
                HermesReplySpeaker(chat, args.playback_device),
                initial_timeout=args.speech_start_timeout,
                followup_timeout=args.followup_timeout,
                max_turns=args.conversation_max_turns,
            )
        listener = WakeWordListener(
            model,
            source,
            APlayResponder(args.reply_audio, args.playback_device),
            threshold=args.threshold,
            cooldown=args.cooldown,
            on_wake=conversation.run if conversation is not None else None,
        )
        logging.info("listening for wake word with %s", args.model)
        listener.run()
        return 0
    except KeyboardInterrupt:
        return 130
    except WakeWordError as exc:
        print(f"masterpi-wake-word: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
