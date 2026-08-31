import time
import unittest

from masterpi_control.backends import MockBackend
from masterpi_control.robot import Robot, ValidationError


class RobotTests(unittest.TestCase):
    def setUp(self):
        self.backend = MockBackend()
        self.robot = Robot(self.backend, watchdog_timeout=0.12)

    def tearDown(self):
        self.robot.close()

    def test_startup_stops_all_chassis_motion(self):
        self.assertEqual(
            self.backend.events[0],
            {
                "action": "drive",
                "time": self.backend.events[0]["time"],
                "speed": 0.0,
                "direction": 0.0,
                "angular_rate": 0.0,
            },
        )

    def test_drive_uses_tutorial_coordinate_convention(self):
        result = self.robot.drive(40, 90, 0)
        self.assertEqual(result, {"speed": 40.0, "direction": 90.0, "angular_rate": 0.0})
        self.assertEqual(self.backend.events[-1]["direction"], 90.0)

    def test_invalid_commands_do_not_reach_hardware(self):
        before = len(self.backend.events)
        with self.assertRaises(ValidationError):
            self.robot.drive(101, 90, 0)
        with self.assertRaises(ValidationError):
            self.robot.servo(1, 499)
        self.assertEqual(len(self.backend.events), before)

    def test_arm_home_and_gripper_defaults(self):
        home = self.robot.home()
        self.assertEqual((home["x"], home["y"], home["z"]), (0.0, 6.0, 18.0))
        opened = self.robot.gripper(True)
        closed = self.robot.gripper(False)
        self.assertEqual(opened["pulse"], 2000)
        self.assertEqual(closed["pulse"], 1500)

    def test_key2_click_moves_arm_home_once(self):
        self.backend.click_button(2)
        deadline = time.monotonic() + 0.5
        while self.robot.snapshot()["button_home_count"] == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        state = self.robot.snapshot()
        self.assertEqual(state["button_home_count"], 1)
        self.assertEqual(state["last_button"], {"button": 2, "action": "home"})
        self.assertEqual(
            (state["arm"]["x"], state["arm"]["y"], state["arm"]["z"]),
            (0.0, 6.0, 18.0),
        )

    def test_key2_press_and_click_are_debounced(self):
        self.backend.press_button(2)
        self.backend.click_button(2)
        time.sleep(0.2)
        self.assertEqual(self.robot.snapshot()["button_home_count"], 1)

    def test_key2_raw_click_moves_arm_home(self):
        with self.backend._lock:
            self.backend._button_events.append((2, 0x20))
        deadline = time.monotonic() + 0.5
        while self.robot.snapshot()["button_home_count"] == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.robot.snapshot()["button_home_count"], 1)

    def test_key1_press_does_not_move_arm(self):
        self.backend.press_button(1)
        time.sleep(0.1)
        self.assertIsNone(self.robot.snapshot()["arm"])

    def test_ultrasonic_distance_is_reported_in_both_units(self):
        self.robot.backend.mock_distance_mm = 437
        reading = self.robot.distance()
        self.assertEqual(reading, {"millimeters": 437, "centimeters": 43.7})
        self.assertEqual(self.robot.snapshot()["distance"], reading)

    def test_ultrasonic_rgb_validates_and_records_color(self):
        result = self.robot.sonar_rgb(12, 34, 56)
        self.assertEqual(result, {"red": 12, "green": 34, "blue": 56})
        self.assertEqual(self.backend.events[-1]["action"], "sonar_rgb")
        self.assertEqual(self.robot.snapshot()["sonar_rgb"], result)

    def test_voice_result_reports_recognized_phrase_without_moving(self):
        self.backend.recognize_voice(3)
        before_arm = self.robot.snapshot()["arm"]
        result = self.robot.voice_result()
        self.assertTrue(result["detected"])
        self.assertEqual(result["last"]["phrase"], "Turn left")
        self.assertEqual(result["count"], 1)
        self.assertEqual(self.robot.snapshot()["arm"], before_arm)
        self.assertEqual(self.robot.snapshot()["drive"]["speed"], 0.0)

    def test_voice_broadcast_is_limited_to_firmware_phrases(self):
        result = self.robot.voice_speak("forward")
        self.assertEqual(result["spoken_text"], "Going forward")
        self.assertEqual(
            self.backend.events[-1],
            {
                "action": "voice_speak",
                "time": self.backend.events[-1]["time"],
                "phrase_type": 0,
                "phrase_id": 1,
            },
        )
        with self.assertRaisesRegex(ValidationError, "cannot speak arbitrary text"):
            self.robot.voice_speak("hello from the webpage")

    def test_watchdog_stops_stale_motion(self):
        self.robot.drive(40, 90, 0)
        time.sleep(0.25)
        state = self.robot.snapshot()
        self.assertEqual(state["drive"]["speed"], 0.0)
        self.assertEqual(state["watchdog_stops"], 1)

    def test_idle_controller_reasserts_motor_stop(self):
        initial_stops = len(self.backend.events)
        time.sleep(0.6)
        state = self.robot.snapshot()
        self.assertGreater(len(self.backend.events), initial_stops)
        self.assertGreaterEqual(state["idle_stop_heartbeats"], 1)
        self.assertEqual(self.backend.events[-1]["speed"], 0.0)

    def test_close_stops_chassis(self):
        self.robot.drive(20, 0, 0)
        self.robot.close()
        self.assertEqual(self.backend.events[-1]["speed"], 0.0)
        self.assertTrue(self.robot.snapshot()["closed"])


if __name__ == "__main__":
    unittest.main()
