import json
import subprocess
import unittest
from pathlib import Path

from masterpi_control.chat import ChatError, HermesChat


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

    def test_synthesize_returns_generated_mp3(self):
        runner = FakeRunner()
        audio, content_type = HermesChat(runner=runner).synthesize("Hello")
        self.assertEqual(audio, b"generated-mp3")
        self.assertEqual(content_type, "audio/mpeg")


if __name__ == "__main__":
    unittest.main()
