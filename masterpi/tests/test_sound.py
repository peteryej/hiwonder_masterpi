import itertools
import struct
import types
import unittest
from unittest.mock import patch

from masterpi_control.backends import MockBackend
from masterpi_control.robot import Robot
from masterpi_control.sound import ReSpeakerDirection, SoundTracker, SoundUnavailable


class FakeUsbDevice:
    def __init__(self, angles, voice=1):
        self.angles = iter(angles)
        self.voice = voice
        self.calls = []

    def ctrl_transfer(self, request_type, request, command, parameter, length, timeout):
        self.calls.append((request_type, request, command, parameter, length, timeout))
        value = next(self.angles) if parameter == 21 else self.voice
        return struct.pack("<ii", value, 0)


def fake_usb(device):
    return types.SimpleNamespace(
        core=types.SimpleNamespace(find=lambda **_kwargs: device),
        util=types.SimpleNamespace(
            CTRL_IN=0x80,
            CTRL_TYPE_VENDOR=0x40,
            CTRL_RECIPIENT_DEVICE=0,
            dispose_resources=lambda _device: None,
        ),
    )


class FakeLocator:
    def sample(self, samples=5):
        return {
            "relative_angle": 90.0,
            "raw_angles": [90.0] * samples,
            "voice_active": True,
            "samples": samples,
        }


class SoundTests(unittest.TestCase):
    def test_reads_and_circularly_averages_doa(self):
        device = FakeUsbDevice([358, 2, 0])
        locator = ReSpeakerDirection(usb_module=fake_usb(device))

        result = locator.sample(samples=3, interval=0)

        self.assertAlmostEqual(result["relative_angle"], 0.0, places=1)
        self.assertTrue(result["voice_active"])
        doa_calls = [call for call in device.calls if call[3] == 21]
        self.assertTrue(all(call[2] == 0xC0 for call in doa_calls))

    def test_front_calibration_and_orientation_are_applied(self):
        device = FakeUsbDevice([120])
        locator = ReSpeakerDirection(
            front_angle=90, clockwise=False, usb_module=fake_usb(device)
        )
        self.assertEqual(locator.read()["relative_angle"], -30.0)

    def test_unstable_bearings_do_not_produce_a_direction(self):
        device = FakeUsbDevice([0, 90, 180, 270])
        locator = ReSpeakerDirection(usb_module=fake_usb(device))
        with self.assertRaisesRegex(SoundUnavailable, "unstable"):
            locator.sample(samples=4, interval=0)

    def test_come_here_turns_then_approaches_and_stops_for_sonar(self):
        robot = Robot(MockBackend(), watchdog_timeout=0.3)
        robot.backend.mock_distance_mm = 400
        tracker = SoundTracker(robot, FakeLocator())
        try:
            with patch("masterpi_control.robot.time.sleep"), patch(
                "masterpi_control.robot.time.monotonic",
                side_effect=itertools.count(0, 0.1),
            ):
                result = tracker.come_here(approach_duration=1, clearance_cm=45)
        finally:
            robot.close()

        self.assertTrue(result["motion"]["stopped_for_obstacle"])
        drive_events = [
            event for event in robot.backend.events if event["action"] == "drive"
        ]
        self.assertTrue(any(event["angular_rate"] == 0.6 for event in drive_events))
        self.assertFalse(any(event["speed"] > 0 for event in drive_events))
        self.assertEqual(drive_events[-1]["speed"], 0.0)


if __name__ == "__main__":
    unittest.main()
