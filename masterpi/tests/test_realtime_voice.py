import base64
import json
import unittest

import numpy as np

from masterpi_control.realtime_voice import (
    HIBOT_REALTIME_PROMPT,
    OpenAIRealtimeClient,
    RawPcmPlayer,
    RealtimeVoiceError,
    RealtimeTurnResult,
    RealtimeVoiceConversation,
    resample_pcm16,
)


class FakeSocket:
    def __init__(self, events):
        self.events = iter(json.dumps(event) for event in events)
        self.sent = []
        self.closed = False

    def send(self, payload):
        self.sent.append(json.loads(payload))

    def recv(self):
        return next(self.events)

    def close(self):
        self.closed = True


class FakePlayer:
    def __init__(self):
        self.audio = bytearray()
        self.closed = False

    def write(self, audio):
        self.audio.extend(audio)

    def close(self):
        self.closed = True


class RealtimeVoiceTests(unittest.TestCase):
    def test_resample_pcm16_converts_16khz_to_24khz(self):
        source = np.array([0, 1000, -1000, 500], dtype="<i2").tobytes()

        result = resample_pcm16(source)

        self.assertEqual(len(result), 12)
        self.assertEqual(np.frombuffer(result, dtype="<i2").size, 6)

    def test_client_streams_audio_and_collects_transcripts_and_timings(self):
        reply_audio = b"\x01\x02\x03\x04"
        socket = FakeSocket(
            [
                {"type": "session.created"},
                {"type": "session.updated"},
                {"type": "response.output_audio_transcript.delta", "delta": "I can help."},
                {
                    "type": "response.output_audio.delta",
                    "delta": base64.b64encode(reply_audio).decode("ascii"),
                },
                {
                    "type": "response.output_audio_transcript.done",
                    "transcript": "I can help.",
                },
                {"type": "response.done", "response": {"status": "completed"}},
                {
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": "What can you do?",
                },
            ]
        )
        factory_calls = []
        player = FakePlayer()
        ticks = iter(float(value) for value in range(20))
        transcripts = []
        first_audio = []

        def factory(url, **kwargs):
            factory_calls.append((url, kwargs))
            return socket

        client = OpenAIRealtimeClient(
            "test-key",
            "plughw:test",
            websocket_factory=factory,
            player_factory=lambda: player,
            clock=lambda: next(ticks),
        )

        handshake = client.connect()
        result = client.respond(
            np.array([0, 1000, -1000, 0], dtype="<i2").tobytes(),
            on_transcript=transcripts.append,
            on_first_audio=lambda: first_audio.append(True),
        )

        self.assertEqual(handshake, 1.0)
        self.assertIn("model=gpt-realtime-2.1", factory_calls[0][0])
        self.assertEqual(factory_calls[0][1]["header"], ["Authorization: Bearer test-key"])
        session = socket.sent[0]["session"]
        self.assertIn("You are HiBot", session["instructions"])
        self.assertEqual(session["audio"]["input"]["turn_detection"], None)
        self.assertEqual(session["audio"]["input"]["format"]["rate"], 24_000)
        self.assertEqual(socket.sent[-2]["type"], "input_audio_buffer.commit")
        self.assertEqual(socket.sent[-1]["type"], "response.create")
        appended = base64.b64decode(socket.sent[1]["audio"])
        self.assertEqual(len(appended), 12)
        self.assertEqual(transcripts, ["What can you do?"])
        self.assertEqual(first_audio, [True])
        self.assertEqual(player.audio, reply_audio)
        self.assertTrue(player.closed)
        self.assertEqual(result.transcript, "What can you do?")
        self.assertEqual(result.reply, "I can help.")
        self.assertEqual(result.upload_seconds, 1.0)
        self.assertEqual(result.first_audio_seconds, 2.0)
        self.assertEqual(result.response_seconds, 3.0)
        self.assertEqual(result.total_seconds, 4.0)

    def test_missing_api_key_fails_before_opening_socket(self):
        with self.assertRaisesRegex(RealtimeVoiceError, "OPENAI_API_KEY"):
            OpenAIRealtimeClient("", "plughw:test")

    def test_prompt_identifies_hibot_and_discloses_no_tools(self):
        self.assertIn("physical Hiwonder MasterPi robot", HIBOT_REALTIME_PROMPT)
        self.assertIn("camera mounted above the", HIBOT_REALTIME_PROMPT)
        self.assertIn("has no robot-control tools", HIBOT_REALTIME_PROMPT)
        self.assertIn("one or two short sentences", HIBOT_REALTIME_PROMPT)

    def test_raw_player_uses_24khz_mono_pcm(self):
        calls = []

        class Pipe:
            def __init__(self):
                self.data = bytearray()

            def write(self, data):
                self.data.extend(data)

            def flush(self):
                pass

            def close(self):
                pass

        class Process:
            def __init__(self):
                self.stdin = Pipe()
                self.stderr = None

            def wait(self, timeout=None):
                return 0

        process = Process()

        def popen(command, **kwargs):
            calls.append((command, kwargs))
            return process

        player = RawPcmPlayer("plughw:test", popen=popen)
        player.write(b"audio")
        player.close()

        self.assertIn("24000", calls[0][0])
        self.assertIn("S16_LE", calls[0][0])
        self.assertIn("plughw:test", calls[0][0])
        self.assertEqual(process.stdin.data, b"audio")

    def test_conversation_publishes_direct_realtime_stages(self):
        class Source:
            def __init__(self):
                self.starts = 0
                self.stops = 0

            def start(self):
                self.starts += 1

            def stop(self):
                self.stops += 1

        class Recorder:
            def capture(self, _source, *, start_timeout):
                return b"\x00\x00" * 100

        class Client:
            model = "gpt-realtime-2.1"

            def __init__(self):
                self.closed = False

            def connect(self):
                return 0.25

            def respond(self, _pcm, *, on_transcript, on_first_audio):
                on_transcript("goodbye")
                on_first_audio()
                return RealtimeTurnResult(
                    "goodbye", "Goodbye!", 0.01, 0.4, 0.8, 1.0, 0.5
                )

            def close(self):
                self.closed = True

        class Status:
            def __init__(self):
                self.calls = []

            def __getattr__(self, name):
                return lambda *args, **kwargs: self.calls.append((name, args, kwargs))

        source = Source()
        client = Client()
        status = Status()
        leds = []
        conversation = RealtimeVoiceConversation(
            source,
            Recorder(),
            client,
            status=status,
            thinking_start=lambda: leds.append("spin"),
            thinking_stop=lambda: leds.append("off"),
            settle_seconds=0,
        )

        conversation.run()

        texts = [args[1] for name, args, _kwargs in status.calls if name == "step"]
        tools = [kwargs.get("tool", "") for name, _args, kwargs in status.calls if name == "step"]
        messages = [args for name, args, _kwargs in status.calls if name == "message"]
        self.assertTrue(any("streaming voice session" in text for text in texts))
        self.assertTrue(any("Turn timing:" in text for text in texts))
        self.assertTrue(any("OpenAI Realtime" in tool for tool in tools))
        self.assertIn(("user", "goodbye"), messages)
        self.assertIn(("hibot", "Goodbye!"), messages)
        self.assertTrue(client.closed)
        self.assertIn("spin", leds)
        self.assertEqual(leds[-1], "off")


if __name__ == "__main__":
    unittest.main()
