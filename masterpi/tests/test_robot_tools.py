import unittest

from masterpi_control.robot_tools import RobotToolDispatcher


class FakeClient:
    def __init__(self):
        self.calls = []

    def call(self, action, payload=None):
        self.calls.append((action, payload))
        return {"action": action, "payload": payload or {}}


class RobotToolDispatcherTests(unittest.TestCase):
    def test_realtime_schemas_are_closed_and_include_all_robot_actions(self):
        schemas = RobotToolDispatcher(FakeClient()).schemas
        by_name = {schema["name"]: schema for schema in schemas}

        self.assertIn("check_front", by_name)
        self.assertIn("dance", by_name)
        self.assertIn("grab_from_ground", by_name)
        self.assertIn("grab_from_front", by_name)
        self.assertNotIn("recognize_and_grab", by_name)
        self.assertIn("analyze_camera", by_name)
        self.assertIn("analyze_camera_color", by_name)
        self.assertEqual(
            by_name["drive_for"]["parameters"]["properties"]["direction"]["enum"],
            ["forward", "backward", "left", "right", "rotate_left", "rotate_right"],
        )
        self.assertTrue(
            all(not schema["parameters"]["additionalProperties"] for schema in schemas)
        )
        self.assertEqual(by_name["dance"]["parameters"]["properties"], {})

    def test_dispatch_applies_defaults_and_uses_loopback_action(self):
        client = FakeClient()
        dispatcher = RobotToolDispatcher(client)

        result = dispatcher.dispatch("drive_for", {"direction": "forward"})

        self.assertEqual(
            client.calls,
            [("drive_for", {"direction": "forward", "duration": 1, "speed": 40})],
        )
        self.assertEqual(result["action"], "drive_for")

        result = dispatcher.dispatch("dance")
        self.assertEqual(client.calls[-1], ("dance", None))
        self.assertEqual(result["action"], "dance")

        dispatcher.dispatch("grab_from_ground")
        self.assertEqual(client.calls[-1], ("grab_from_ground", None))
        dispatcher.dispatch("grab_from_front")
        self.assertEqual(client.calls[-1], ("grab_from_front", None))

    def test_led_target_routes_to_sonar_and_is_not_forwarded(self):
        client = FakeClient()

        RobotToolDispatcher(client).dispatch(
            "set_led", {"red": 1, "green": 2, "blue": 3, "target": "sonar"}
        )

        self.assertEqual(
            client.calls,
            [("sonar_rgb", {"red": 1, "green": 2, "blue": 3})],
        )

    def test_dispatch_rejects_unknown_missing_and_extra_arguments(self):
        dispatcher = RobotToolDispatcher(FakeClient())

        with self.assertRaisesRegex(ValueError, "unknown HiBot tool"):
            dispatcher.dispatch("launch_missile", {})
        with self.assertRaisesRegex(ValueError, "missing required"):
            dispatcher.dispatch("set_gripper", {})
        with self.assertRaisesRegex(ValueError, "unexpected arguments"):
            dispatcher.dispatch("stop", {"duration": 1})


if __name__ == "__main__":
    unittest.main()
