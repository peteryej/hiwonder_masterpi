import json
import tempfile
import unittest
from pathlib import Path

from masterpi_control.voice_status import VoiceStatusWriter, read_voice_status


class VoiceStatusTests(unittest.TestCase):
    def test_writer_publishes_bounded_conversation_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "voice.json"
            status = VoiceStatusWriter(path)

            status.begin("hibot-voice-test")
            status.message("hibot", "I'm here.")
            status.message("user", "What can you do?")
            status.step("thinking", "Hermes is thinking…", tool="Hermes Agent")

            value = read_voice_status(path)
            self.assertTrue(value["active"])
            self.assertEqual(value["state"], "thinking")
            self.assertEqual(value["session"], "hibot-voice-test")
            self.assertEqual(value["events"][-2]["role"], "user")
            self.assertEqual(value["events"][-1]["tool"], "Hermes Agent")

            status.finish()
            self.assertFalse(read_voice_status(path)["active"])

    def test_reader_rejects_malformed_numeric_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "voice.json"
            path.write_text(
                json.dumps({"events": [], "revision": "invalid", "updated_at": 0}),
                encoding="utf-8",
            )

            value = read_voice_status(path)
            self.assertEqual(value["state"], "idle")
            self.assertEqual(value["revision"], 0)


if __name__ == "__main__":
    unittest.main()
