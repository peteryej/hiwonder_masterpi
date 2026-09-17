import unittest
from unittest.mock import patch

from masterpi_control import hibot_mcp


class HibotMcpTests(unittest.TestCase):
    def test_gripper_routes_both_presets_to_shared_controller(self):
        for opened, pulse in ((True, 2500), (False, 500)):
            with self.subTest(opened=opened):
                with patch.object(hibot_mcp.client, "call", return_value={"pulse": pulse}) as call:
                    self.assertEqual(hibot_mcp.set_gripper(opened)["pulse"], pulse)
                    call.assert_called_once_with("gripper", {"opened": opened, "duration": 0.5})

    def test_grab_tools_use_distinct_pickup_actions(self):
        with patch.object(hibot_mcp.client, "call", return_value={"grabbed": True}) as call:
            self.assertTrue(hibot_mcp.grab_from_ground()["grabbed"])
            call.assert_called_once_with("grab_from_ground")

        with patch.object(hibot_mcp.client, "call", return_value={"grabbed": True}) as call:
            self.assertTrue(hibot_mcp.grab_from_front()["grabbed"])
            call.assert_called_once_with("grab_from_front")

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

    def test_check_ground_uses_shared_recorded_pose_action(self):
        expected = {"pose": "check_ground"}
        with patch.object(hibot_mcp.client, "call", return_value=expected) as call:
            result = hibot_mcp.check_ground()

        self.assertEqual(result, expected)
        call.assert_called_once_with("check_ground", {"duration": 0.8})

    def test_drive_for_defaults_to_webpage_chassis_speed(self):
        expected = {"direction": "forward", "speed": 40.0}
        with patch.object(hibot_mcp.client, "call", return_value=expected) as call:
            result = hibot_mcp.drive_for("forward")

        self.assertEqual(result, expected)
        call.assert_called_once_with(
            "drive_for", {"direction": "forward", "speed": 40.0, "duration": 1.0}
        )

    def test_move_and_rotate_send_only_the_supplied_amount(self):
        with patch.object(hibot_mcp, "dispatch_robot_tool") as call:
            call.return_value = {"duration": 0.5}
            hibot_mcp.move("forward", distance_cm=20)
            self.assertEqual(
                call.call_args[0][2],
                {"direction": "forward", "speed": 40.0, "distance_cm": 20},
            )
            hibot_mcp.rotate("left", seconds=0.3)
            self.assertEqual(
                call.call_args[0][2], {"direction": "left", "speed": 40.0, "seconds": 0.3}
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
