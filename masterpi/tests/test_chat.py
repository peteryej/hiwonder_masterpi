import json
import subprocess
import threading
import unittest
from pathlib import Path

from masterpi_control.chat import ChatError, ChatInterrupted, HermesChat, NoSpeechDetected


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.audio_seen = None

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if command[0].endswith("hermes"):
            return subprocess.CompletedProcess(
                command, 0, "session_id: test-session\nHello from hibot\n", ""
            )
        if "vision_analyze_tool" in command[2]:
            analysis = {
                "description": "A bottle is in front of the robot.",
                "objects": [
                    {
                        "label": "bottle",
                        "confidence": 0.92,
                        "bbox": {"x_min": 0.2, "y_min": 0.1, "x_max": 0.6, "y_max": 0.9},
                    }
                ],
            }
            result = {
                "success": True,
                "analysis": json.dumps(analysis),
                "provider": "openai-codex",
                "model": "gpt-5.6-terra",
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(result), "")
        if "text_to_speech_tool" in command[2]:
            output_path = Path(command[-1])
            output_path.write_bytes(b"generated-mp3")
            result = {
                "success": True,
                "file_path": str(output_path),
                "file_paths": [str(output_path)],
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(result), "")
        audio_path = Path(command[-1])
        self.audio_seen = (audio_path.suffix, audio_path.read_bytes())
        result = {"success": True, "transcript": "spoken request"}
        return subprocess.CompletedProcess(command, 0, json.dumps(result) + "\n", "")


class HermesChatTests(unittest.TestCase):
    def test_reply_uses_persistent_web_session_and_returns_only_text(self):
        runner = FakeRunner()
        chat = HermesChat(runner=runner)
        self.assertEqual(chat.reply("Hello"), "Hello from hibot")
        command, options = runner.calls[0]
        self.assertIn("--continue", command)
        self.assertIn("hibot-web-safe", command)
        self.assertIn("--create-if-missing", command)
        self.assertIn("--toolsets", command)
        self.assertEqual(command[command.index("--toolsets") + 1], "safe")
        self.assertEqual(options["input"], "Hello")

    def test_new_session_uses_unique_name_with_configured_prefix(self):
        runner = FakeRunner()
        chat = HermesChat(runner=runner, session_name="hibot-voice")

        first = chat.new_session()
        second = chat.new_session()

        self.assertTrue(first.startswith("hibot-voice-"))
        self.assertTrue(second.startswith("hibot-voice-"))
        self.assertNotEqual(first, second)
        chat.reply("Hello")
        self.assertIn(second, runner.calls[0][0])

    def test_voice_model_and_reasoning_can_be_scoped_to_one_chat_client(self):
        runner = FakeRunner()
        chat = HermesChat(
            runner=runner,
            session_name="hibot-voice",
            model="gpt-5.6-luna",
            provider="openai-codex",
            reasoning="low",
        )

        chat.reply("Hello")

        command = runner.calls[0][0]
        self.assertEqual(command[command.index("--model") + 1], "gpt-5.6-luna")
        self.assertEqual(command[command.index("--provider") + 1], "openai-codex")
        self.assertEqual(command[command.index("--reasoning") + 1], "low")

    def test_interruptible_reply_terminates_hermes_process(self):
        class Process:
            def __init__(self):
                self.returncode = None
                self.terminated = False
                self.done = threading.Event()

            def communicate(self, input=None):
                self.input = input
                self.done.wait(2)
                return "", ""

            def terminate(self):
                self.terminated = True
                self.returncode = -15
                self.done.set()

            def kill(self):
                self.returncode = -9
                self.done.set()

        process = Process()
        chat = HermesChat(
            runner=FakeRunner(),
            popen=lambda *_args, **_kwargs: process,
        )
        cancel = threading.Event()
        cancel.set()

        with self.assertRaises(ChatInterrupted):
            chat.reply_interruptible("new question", cancel)

        self.assertTrue(process.terminated)
        self.assertEqual(process.input, "new question")

    def test_transcribe_passes_recording_to_hermes_stt(self):
        runner = FakeRunner()
        chat = HermesChat(runner=runner)
        self.assertEqual(
            chat.transcribe(b"webm-audio", "audio/webm;codecs=opus"),
            "spoken request",
        )
        self.assertEqual(runner.audio_seen, (".webm", b"webm-audio"))

    def test_transcribe_reports_stt_failure(self):
        def failing_runner(command, **kwargs):
            result = {"success": False, "transcript": "", "error": "STT unavailable"}
            return subprocess.CompletedProcess(command, 0, json.dumps(result), "")

        with self.assertRaisesRegex(ChatError, "STT unavailable"):
            HermesChat(runner=failing_runner).transcribe(b"audio", "audio/ogg")

    def test_transcribe_treats_filtered_whisper_hallucination_as_silence(self):
        def silent_runner(command, **kwargs):
            result = {"success": True, "transcript": "", "filtered": True}
            return subprocess.CompletedProcess(command, 0, json.dumps(result), "")

        with self.assertRaises(NoSpeechDetected):
            HermesChat(runner=silent_runner).transcribe(b"audio", "audio/wav")

    def test_synthesize_returns_generated_mp3(self):
        runner = FakeRunner()
        audio, content_type = HermesChat(runner=runner).synthesize("Hello")
        self.assertEqual(audio, b"generated-mp3")
        self.assertEqual(content_type, "audio/mpeg")

    def test_analyze_image_returns_structured_objects_and_boxes(self):
        runner = FakeRunner()
        result = HermesChat(runner=runner).analyze_image(b"jpeg-data")
        self.assertEqual(result["description"], "A bottle is in front of the robot.")
        self.assertEqual(result["objects"][0]["label"], "bottle")
        self.assertEqual(
            result["objects"][0]["bbox"],
            {"x_min": 0.2, "y_min": 0.1, "x_max": 0.6, "y_max": 0.9},
        )
        self.assertEqual(result["model"], "gpt-5.6-terra")


if __name__ == "__main__":
    unittest.main()
