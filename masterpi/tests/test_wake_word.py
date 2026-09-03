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
        listener = WakeWordListener(
            model,
            source,
            responder,
            threshold=0.5,
            cooldown=0.25,
            on_wake=lambda: wake_calls.append("wake"),
            sleeper=sleeps.append,
        )

        count = listener.run(max_detections=1)

        self.assertEqual(count, 1)
        self.assertEqual(responder.plays, 1)
        self.assertEqual(model.resets, 1)
        self.assertEqual(wake_calls, ["wake"])
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

    def test_voice_conversation_accepts_followup_until_goodbye(self):
        source = FakeSource()

        class Recorder:
            def __init__(self):
                self.timeouts = []

            def capture(self, _source, *, start_timeout):
                self.timeouts.append(start_timeout)
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
        conversation = VoiceConversation(
            source,
            recorder,
            chat,
            spoken.append,
            initial_timeout=10,
            followup_timeout=8,
            max_turns=4,
            settle_seconds=0,
        )

        conversation.run()

        self.assertTrue(chat.assert_wav)
        self.assertEqual(chat.messages, ["What can you see?"])
        self.assertEqual(spoken, ["I can see a cup.", "Goodbye."])
        self.assertEqual(recorder.timeouts, [10, 8])
        self.assertEqual(source.starts, 2)


if __name__ == "__main__":
    unittest.main()
