import unittest
from unittest.mock import patch

from masterpi_control import hibot_mcp


class HibotMcpTests(unittest.TestCase):
    def test_dance_uses_shared_async_action(self):
        expected = {"started": True, "audio": True}
        with patch.object(hibot_mcp.client, "call", return_value=expected) as call:
            result = hibot_mcp.dance()

        self.assertEqual(result, expected)
        call.assert_called_once_with("dance")

    def test_check_front_uses_shared_exact_pose_action(self):
        expected = {"pose": "check_front"}
        with patch.object(hibot_mcp.client, "call", return_value=expected) as call:
            result = hibot_mcp.check_front()

        self.assertEqual(result, expected)
        call.assert_called_once_with("check_front", {"duration": 0.8})

    def test_drive_for_defaults_to_webpage_chassis_speed(self):
        expected = {"direction": "forward", "speed": 40.0}
        with patch.object(hibot_mcp.client, "call", return_value=expected) as call:
            result = hibot_mcp.drive_for("forward")

        self.assertEqual(result, expected)
        call.assert_called_once_with(
            "drive_for", {"direction": "forward", "speed": 40.0, "duration": 1.0}
        )

    def test_come_here_calls_bounded_sound_approach(self):
        expected = {"mode": "sound_source_approach"}
        with patch.object(hibot_mcp.client, "call", return_value=expected) as call:
            result = hibot_mcp.come_here()

        self.assertEqual(result, expected)
        call.assert_called_once_with(
            "come_here",
            {"approach_duration": 2.0, "clearance_cm": 45, "samples": 5},
        )


if __name__ == "__main__":
    unittest.main()
