import io
import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np

from masterpi_control.wake_word import (
    APlayResponder,
    ARecordSource,
    FRAME_BYTES,
    FRAME_SAMPLES,
    HermesReplySpeaker,
    UtteranceRecorder,
    VoiceConversation,
    WakeWordError,
    WakeWordListener,
    download_feature_models,
    pcm_to_wav,
)


class FakePipe:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    def read(self, size=-1):
        if not self.chunks:
            return b""
        chunk = self.chunks.pop(0)
        if size >= 0 and len(chunk) > size:
            self.chunks.insert(0, chunk[size:])
            return chunk[:size]
        return chunk


class FakeProcess:
    def __init__(self, chunks):
        self.stdout = FakePipe(chunks)
        self.stderr = io.BytesIO()
        self.running = True
        self.terminated = False

    def poll(self):
        return None if self.running else 0

    def terminate(self):
        self.terminated = True
        self.running = False

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.running = False


class FakeSource:
    def __init__(self):
        self.starts = 0
        self.stops = 0

    def start(self):
        self.starts += 1

    def read(self):
        return bytes(FRAME_BYTES)

    def stop(self):
        self.stops += 1


class FakeModel:
    def __init__(self, scores):
        self.scores = iter(scores)
        self.frames = []
        self.resets = 0

    def predict(self, frame):
        self.frames.append(frame)
        return {"hello-hibot": next(self.scores)}

    def reset(self):
        self.resets += 1


class FakeResponder:
    def __init__(self):
        self.plays = 0

    def play(self):
        self.plays += 1


def pcm_frame(value):
    return np.full(FRAME_SAMPLES, value, dtype="<i2").tobytes()


class FrameSource(FakeSource):
    def __init__(self, frames):
        super().__init__()
        self.frames = iter(frames)

    def read(self):
        return next(self.frames)


class WakeWordTests(unittest.TestCase):
    def test_arecord_uses_processed_mono_format_and_assembles_partial_reads(self):
        process = FakeProcess([bytes(100), bytes(FRAME_BYTES - 100)])
        calls = []

        def popen(command, **kwargs):
            calls.append((command, kwargs))
            return process

        source = ARecordSource("plughw:test", popen=popen)
        source.start()
        frame = source.read()
        source.stop()

        self.assertEqual(len(frame), FRAME_BYTES)
        self.assertIn("16000", calls[0][0])
        self.assertIn("S16_LE", calls[0][0])
        self.assertIn("plughw:test", calls[0][0])
        self.assertTrue(process.terminated)

    def test_listener_responds_only_above_threshold_and_resets_model(self):
        source = FakeSource()
        model = FakeModel([0.2, 0.81])
        responder = FakeResponder()
        sleeps = []
        wake_calls = []
        direction_events = []
        listener = WakeWordListener(
            model,
            source,
            responder,
            threshold=0.5,
            cooldown=0.25,
            on_wake=lambda: wake_calls.append("wake"),
            voice_direction_start=lambda: direction_events.append("direction"),
            voice_direction_stop=lambda: direction_events.append("off"),
            sleeper=sleeps.append,
        )

        count = listener.run(max_detections=1)

        self.assertEqual(count, 1)
        self.assertEqual(responder.plays, 1)
        self.assertEqual(model.resets, 1)
        self.assertEqual(wake_calls, ["wake"])
        self.assertEqual(direction_events, ["direction", "off"])
        self.assertEqual(sleeps, [0.25])
        self.assertEqual(source.starts, 1)
        self.assertGreaterEqual(source.stops, 2)
        self.assertTrue(all(frame.dtype == np.dtype("int16") for frame in model.frames))

    def test_responder_surfaces_aplay_errors(self):
        result = subprocess.CompletedProcess([], 1, "", "device busy")
        responder = APlayResponder(Path("reply.wav"), runner=lambda *_a, **_k: result)
        with self.assertRaisesRegex(WakeWordError, "device busy"):
            responder.play()

    def test_download_feature_models_is_idempotent(self):
        payloads = {
            "melspectrogram.onnx": b"mel",
            "embedding_model.onnx": b"embedding",
        }

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

        def opener(url, timeout):
            return Response(payloads[url.rsplit("/", 1)[-1]])

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            downloaded = download_feature_models(target, opener=opener)
            again = download_feature_models(target, opener=opener)

            self.assertEqual(len(downloaded), 2)
            self.assertEqual(again, [])
            self.assertEqual((target / "embedding_model.onnx").read_bytes(), b"embedding")

    def test_utterance_recorder_waits_for_speech_then_stops_after_silence(self):
        source = FrameSource(
            [
                pcm_frame(0),
                pcm_frame(0),
                pcm_frame(900),
                pcm_frame(900),
                pcm_frame(900),
                pcm_frame(900),
                pcm_frame(800),
                pcm_frame(0),
                pcm_frame(0),
                pcm_frame(0),
            ]
        )
        recorder = UtteranceRecorder(
            speech_threshold=300,
            silence_seconds=0.24,
            max_seconds=2,
            pre_roll_seconds=0.16,
        )

        pcm = recorder.capture(source, start_timeout=1)

        self.assertIsNotNone(pcm)
        self.assertGreaterEqual(len(pcm), FRAME_BYTES * 6)
        wav = pcm_to_wav(pcm)
        self.assertEqual(wav[:4], b"RIFF")
        self.assertEqual(wav[8:12], b"WAVE")

    def test_utterance_recorder_logs_timeout_noise_levels(self):
        source = FrameSource([pcm_frame(180), pcm_frame(220), pcm_frame(200)])
        recorder = UtteranceRecorder(
            speech_threshold=400,
            silence_seconds=0.24,
            max_seconds=1,
        )

        with self.assertLogs(level="INFO") as messages:
            pcm = recorder.capture(source, start_timeout=0.24)

        self.assertIsNone(pcm)
        output = "\n".join(messages.output)
        self.assertIn("reason=start_timeout", output)
        self.assertIn("threshold=400", output)
        self.assertIn("rms_median=200", output)

    def test_voice_conversation_accepts_followup_until_goodbye(self):
        source = FakeSource()

        class Recorder:
            def __init__(self):
                self.timeouts = []

            def capture(self, _source, *, start_timeout, on_speech_start=None):
                self.timeouts.append(start_timeout)
                if on_speech_start is not None:
                    on_speech_start()
                return pcm_frame(700)

        class Chat:
            def __init__(self):
                self.transcripts = iter(["What can you see?", "goodbye"])
                self.messages = []

            def transcribe(self, audio, content_type):
                self.assert_wav = audio[:4] == b"RIFF" and content_type == "audio/wav"
                return next(self.transcripts)

            def reply(self, message):
                self.messages.append(message)
                return "I can see a cup."

        recorder = Recorder()
        chat = Chat()
        spoken = []
        direction_events = []
        conversation = VoiceConversation(
            source,
            recorder,
            chat,
            spoken.append,
            initial_timeout=10,
            followup_timeout=8,
            max_turns=4,
            barge_in=False,
            voice_direction_start=lambda: direction_events.append("direction"),
            voice_direction_stop=lambda: direction_events.append("off"),
            settle_seconds=0,
        )

        conversation.run()

        self.assertTrue(chat.assert_wav)
        self.assertEqual(chat.messages, ["What can you see?"])
        self.assertEqual(spoken, ["I can see a cup."])
        self.assertEqual(recorder.timeouts, [10, 8])
        self.assertEqual(source.starts, 2)
        self.assertEqual(direction_events[:4], ["direction", "off", "direction", "off"])

    def test_voice_conversation_starts_fresh_session_and_allows_three_silent_cycles(self):
        source = FakeSource()

        class Recorder:
            speech_threshold = 200

            def __init__(self):
                self.timeouts = []

            def capture(self, _source, *, start_timeout, on_speech_start=None):
                self.timeouts.append(start_timeout)
                return None

        class Chat:
            def __init__(self):
                self.sessions = 0

            def new_session(self):
                self.sessions += 1
                return "hibot-voice-new"

        recorder = Recorder()
        chat = Chat()
        conversation = VoiceConversation(
            source,
            recorder,
            chat,
            lambda _text: None,
            initial_timeout=15,
            followup_timeout=15,
            max_silent_cycles=3,
            barge_in=False,
            settle_seconds=0,
        )

        conversation.run()

        self.assertEqual(chat.sessions, 1)
        self.assertEqual(recorder.timeouts, [15, 15, 15])
        self.assertEqual(source.starts, 3)
        self.assertGreaterEqual(source.stops, 3)

    def test_generation_barge_in_cancels_hermes_and_returns_audio(self):
        source = FakeSource()
        interruption = pcm_frame(800)

        class Recorder:
            speech_threshold = 200

            def calibrate(self, _source, *, seconds):
                self.calibration_seconds = seconds
                return 50

            def capture(self, _source, **kwargs):
                self.threshold = kwargs["speech_threshold"]
                kwargs["on_speech_start"]()
                return interruption

        class Chat:
            def reply_interruptible(self, _message, cancel):
                cancel.wait(1)
                from masterpi_control.chat import ChatInterrupted

                raise ChatInterrupted("interrupted")

        recorder = Recorder()
        led_events = []
        conversation = VoiceConversation(
            source,
            recorder,
            Chat(),
            lambda _text: None,
            barge_in=True,
            thinking_start=lambda: led_events.append("spin"),
            thinking_stop=lambda: led_events.append("off"),
            settle_seconds=0,
        )

        reply, pcm, threshold = conversation._reply("hello")

        self.assertEqual(reply, "")
        self.assertEqual(pcm, interruption)
        self.assertEqual(threshold, 500)
        self.assertEqual(recorder.threshold, 500)
        self.assertEqual(led_events, ["spin", "off"])

    def test_playback_barge_in_stops_aplay_and_returns_audio(self):
        interruption = pcm_frame(800)

        class Chat:
            def synthesize(self, _text):
                return b"audio", "audio/mpeg"

        class Recorder:
            def capture(self, _source, **kwargs):
                kwargs["on_speech_start"]()
                return interruption

        class Playback:
            def __init__(self):
                self.stderr = io.StringIO()
                self.running = True
                self.terminated = False

            def poll(self):
                return None if self.running else 0

            def terminate(self):
                self.terminated = True
                self.running = False

            def wait(self, timeout=None):
                self.running = False
                return 0

            def kill(self):
                self.running = False

        playback = Playback()

        def runner(_command, **_kwargs):
            return subprocess.CompletedProcess([], 0, "", "")

        speaker = HermesReplySpeaker(
            Chat(),
            runner=runner,
            popen=lambda *_args, **_kwargs: playback,
            barge_in_grace_seconds=0,
        )
        source = FakeSource()

        pcm = speaker.speak_interruptible(
            "Hello",
            source,
            Recorder(),
            speech_threshold=200,
        )

        self.assertEqual(pcm, interruption)
        self.assertTrue(playback.terminated)
        self.assertEqual(source.starts, 1)
        self.assertEqual(source.stops, 1)


if __name__ == "__main__":
    unittest.main()
