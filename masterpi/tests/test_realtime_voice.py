import base64
import json
import unittest

import numpy as np

from masterpi_control.realtime_voice import (
    HIBOT_REALTIME_PROMPT,
    HIBOT_TOOL_REALTIME_PROMPT,
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

    def test_tool_prompt_requires_explicit_action_and_confirmed_result(self):
        self.assertIn("Call a physical-control tool only", HIBOT_TOOL_REALTIME_PROMPT)
        self.assertIn("Never say an action succeeded", HIBOT_TOOL_REALTIME_PROMPT)
        # Voice must reach for the guarded grab, not the blind quick actions.
        self.assertIn("always use grab_object", HIBOT_TOOL_REALTIME_PROMPT)
        self.assertIn("blind fixed-pose quick actions", HIBOT_TOOL_REALTIME_PROMPT)
        self.assertIn("ask before trying again", HIBOT_TOOL_REALTIME_PROMPT)
        self.assertIn("grab_from_ground", HIBOT_TOOL_REALTIME_PROMPT)
        self.assertIn("grab_from_front", HIBOT_TOOL_REALTIME_PROMPT)

    def test_client_executes_function_call_and_returns_output_to_model(self):
        reply_audio = b"\x01\x02"
        socket = FakeSocket(
            [
                {"type": "session.created"},
                {"type": "session.updated"},
                {
                    "type": "response.done",
                    "response": {
                        "status": "completed",
                        "output": [
                            {
                                "type": "function_call",
                                "name": "get_state",
                                "call_id": "call-1",
                                "arguments": "{}",
                            }
                        ],
                    },
                },
                {
                    "type": "response.output_audio_transcript.done",
                    "transcript": "The chassis is stopped.",
                },
                {
                    "type": "response.output_audio.delta",
                    "delta": base64.b64encode(reply_audio).decode("ascii"),
                },
                {"type": "response.done", "response": {"status": "completed", "output": []}},
            ]
        )

        class Dispatcher:
            schemas = [
                {
                    "type": "function",
                    "name": "get_state",
                    "description": "Read state",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                        "additionalProperties": False,
                    },
                }
            ]

            def __init__(self):
                self.calls = []

            def dispatch(self, name, arguments):
                self.calls.append((name, arguments))
                return {"drive": {"moving": False}}

        dispatcher = Dispatcher()
        player = FakePlayer()
        ticks = iter(float(value) for value in range(20))
        starts = []
        results = []
        client = OpenAIRealtimeClient(
            "test-key",
            "plughw:test",
            instructions=HIBOT_TOOL_REALTIME_PROMPT,
            websocket_factory=lambda _url, **_kwargs: socket,
            player_factory=lambda: player,
            tool_dispatcher=dispatcher,
            clock=lambda: next(ticks),
        )

        client.connect()
        result = client.respond_text(
            "Is your chassis stopped?",
            on_tool_start=lambda name, arguments: starts.append((name, arguments)),
            on_tool_result=results.append,
        )

        session = socket.sent[0]["session"]
        self.assertEqual(session["tool_choice"], "auto")
        self.assertEqual(session["tools"][0]["name"], "get_state")
        self.assertEqual(dispatcher.calls, [("get_state", {})])
        self.assertEqual(starts, [("get_state", {})])
        self.assertTrue(results[0].ok)
        outputs = [
            event for event in socket.sent if event.get("item", {}).get("type") == "function_call_output"
        ]
        self.assertEqual(outputs[0]["item"]["call_id"], "call-1")
        self.assertEqual(
            json.loads(outputs[0]["item"]["output"]),
            {"ok": True, "result": {"drive": {"moving": False}}},
        )
        self.assertEqual(
            len([event for event in socket.sent if event["type"] == "response.create"]),
            2,
        )
        self.assertEqual(result.transcript, "Is your chassis stopped?")
        self.assertEqual(result.reply, "The chassis is stopped.")
        self.assertEqual(result.tool_calls[0].name, "get_state")
        self.assertEqual(player.audio, reply_audio)

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
            def capture(self, _source, *, start_timeout, on_speech_start=None):
                if on_speech_start is not None:
                    on_speech_start()
                return b"\x00\x00" * 100

        class Client:
            model = "gpt-realtime-2.1"

            def __init__(self):
                self.closed = False

            def connect(self):
                return 0.25

            def respond(
                self,
                _pcm,
                *,
                on_transcript,
                on_first_audio,
                on_tool_start,
                on_tool_result,
            ):
                on_transcript("goodbye")
                on_tool_start("get_state", {})
                on_tool_result(
                    type(
                        "Result",
                        (),
                        {
                            "name": "get_state",
                            "elapsed_seconds": 0.02,
                            "ok": True,
                            "result": {},
                        },
                    )()
                )
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
        directions = []
        conversation = RealtimeVoiceConversation(
            source,
            Recorder(),
            client,
            status=status,
            thinking_start=lambda: leds.append("spin"),
            thinking_stop=lambda: leds.append("off"),
            voice_direction_start=lambda: directions.append("direction"),
            voice_direction_stop=lambda: directions.append("off"),
            settle_seconds=0,
        )

        conversation.run()

        texts = [args[1] for name, args, _kwargs in status.calls if name == "step"]
        tools = [kwargs.get("tool", "") for name, _args, kwargs in status.calls if name == "step"]
        messages = [args for name, args, _kwargs in status.calls if name == "message"]
        self.assertTrue(any("streaming voice session" in text for text in texts))
        self.assertTrue(any("Turn timing:" in text for text in texts))
        self.assertTrue(any("OpenAI Realtime" in tool for tool in tools))
        self.assertTrue(any("HiBot shared MCP action" in tool for tool in tools))
        self.assertIn(("user", "goodbye"), messages)
        self.assertIn(("hibot", "Goodbye!"), messages)
        self.assertTrue(client.closed)
        self.assertIn("spin", leds)
        self.assertEqual(leds[-1], "off")
        self.assertEqual(directions[:2], ["direction", "off"])

    def test_empty_transcript_counts_toward_silence_limit(self):
        class Source:
            def __init__(self):
                self.starts = 0
                self.stops = 0

            def start(self):
                self.starts += 1

            def stop(self):
                self.stops += 1

        class Recorder:
            def __init__(self):
                self.timeouts = []
                self.results = iter([b"\x00\x00" * 100, None])

            def capture(self, _source, *, start_timeout, on_speech_start=None):
                self.timeouts.append(start_timeout)
                return next(self.results)

        class Client:
            model = "gpt-realtime-2.1"
            transcription_model = "gpt-4o-mini-transcribe"

            def __init__(self):
                self.responses = 0

            def connect(self):
                return 0.1

            def respond(self, _pcm, **_callbacks):
                self.responses += 1
                return RealtimeTurnResult("", "", 0.01, None, 0.2, 0.2, 0)

            def close(self):
                pass

        class Status:
            def __init__(self):
                self.calls = []

            def __getattr__(self, name):
                return lambda *args, **kwargs: self.calls.append((name, args, kwargs))

        source = Source()
        recorder = Recorder()
        client = Client()
        status = Status()
        conversation = RealtimeVoiceConversation(
            source,
            recorder,
            client,
            initial_timeout=15,
            followup_timeout=8,
            max_silent_cycles=2,
            status=status,
            settle_seconds=0,
        )

        conversation.run()

        self.assertEqual(recorder.timeouts, [15, 8])
        self.assertEqual(client.responses, 1)
        step_text = [args[1] for name, args, _kwargs in status.calls if name == "step"]
        self.assertIn("No words recognized (1/2).", step_text)
        self.assertIn("No speech heard (2/2).", step_text)
        self.assertEqual(source.starts, 2)
        self.assertGreaterEqual(source.stops, 2)


if __name__ == "__main__":
    unittest.main()
