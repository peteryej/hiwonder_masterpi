import unittest
import queue
from unittest.mock import MagicMock, patch

from masterpi_control.backends import VendorBackend, _BoardMecanumChassis


class FakeChassis:
    def __init__(self):
        self.command = None

    def set_velocity(self, speed, direction, angular):
        self.command = (speed, direction, angular)


class FakeModernBoard:
    def __init__(self):
        self.calls = []

    def pwm_servo_set_position(self, duration, positions):
        self.calls.append(("servo", duration, positions))

    def set_rgb(self, pixels):
        self.calls.append(("rgb", pixels))

    def set_buzzer(self, frequency, on_time, off_time, repeat):
        self.calls.append(("buzzer", frequency, on_time, off_time, repeat))

    def set_motor_duty(self, duties):
        self.calls.append(("motors", duties))

    def set_motor_speed(self, speeds):
        self.calls.append(("motor_speeds", speeds))

    def enable_reception(self, enabled=True):
        self.calls.append(("reception", enabled))

    def get_button(self):
        return (2, 1)


class FakeLegacyPixels:
    def __init__(self):
        self.calls = []

    def setPixelColor(self, index, color):
        self.calls.append(("pixel", index, color))

    def show(self):
        self.calls.append(("show",))


class FakeLegacyBoard:
    def __init__(self):
        self.calls = []
        self.RGB = FakeLegacyPixels()

    def setPWMServoPulse(self, servo_id, pulse, milliseconds):
        self.calls.append(("servo", servo_id, pulse, milliseconds))

    def PixelColor(self, red, green, blue):
        return (red, green, blue)

    def setBuzzer(self, value):
        self.calls.append(("buzzer", value))

    def setMotor(self, motor_id, duty):
        self.calls.append(("motor", motor_id, duty))


class FakeArm:
    def __init__(self, result=True):
        self.calls = []
        self.result = result

    def setPitchRangeMoving(self, *values):
        self.calls.append(values)
        return self.result


class FakeArmWithoutBoard(FakeArm):
    pass


class FakeSonar:
    i2c = 1

    def __init__(self):
        self.calls = []
        self.last_error = None

    def getDistance(self):
        return 420

    def setRGBMode(self, mode):
        self.calls.append(("mode", mode))
        return True

    def setPixelColor(self, index, color):
        self.calls.append(("pixel", index, color))
        return True


class FakeGpioValue:
    def __init__(self, value):
        self.value = value


class FakeGpioButtons:
    def __init__(self, key1=1, key2=1):
        self.levels = [key1, key2]

    def get_values(self, offsets):
        return [FakeGpioValue(value) for value in self.levels]


def backend(board, modern=True):
    value = VendorBackend.__new__(VendorBackend)
    value._chassis = FakeChassis()
    value._board = board
    value._modern_board = modern
    value._arm = FakeArm()
    return value


class VendorBackendMappingTests(unittest.TestCase):
    def test_modern_board_is_injected_into_arm_runtime(self):
        with (
            patch.object(VendorBackend, "_add_vendor_paths"),
            patch("masterpi_control.backends.Path.exists", return_value=False),
            patch("masterpi_control.backends._configure_vendor_data_paths"),
            patch("masterpi_control.backends._provide_unused_plotting_stubs"),
            patch("masterpi_control.backends._provide_unused_vision_stub"),
            patch(
                "masterpi_control.backends._load_class",
                side_effect=[
                    (FakeModernBoard, "fake.board"),
                    (FakeArmWithoutBoard, "fake.arm"),
                    (FakeSonar, "fake.sonar"),
                ],
            ),
        ):
            value = VendorBackend()

        self.assertIs(value._arm.board, value._board)
        self.assertEqual(value.sonar_module, "fake.sonar")
        self.assertIn(("reception", True), value._board.calls)

    def test_modern_button_event_mapping(self):
        value = backend(FakeModernBoard())
        self.assertEqual(value.button_event(), (2, 1))

    def test_modern_raw_button_event_is_not_filtered_by_sdk(self):
        board = FakeModernBoard()
        board.key_queue = queue.Queue()
        board.key_queue.put(bytes((2, 0x20)))
        value = backend(board)
        self.assertEqual(value.button_event(), (2, 0x20))

    def test_gpio23_falling_edge_reports_key2_press(self):
        board = FakeModernBoard()
        board.get_button = lambda: None
        value = backend(board)
        value._button_gpio = FakeGpioButtons()
        value._button_levels = {1: 1, 2: 1}
        self.assertIsNone(value.button_event())
        value._button_gpio.levels[1] = 0
        self.assertEqual(value.button_event(), (2, 0x01))

    def test_built_in_mecanum_mixer_matches_vendor_mapping(self):
        board = FakeModernBoard()
        chassis = _BoardMecanumChassis(board, modern_board=True)
        chassis.set_velocity(40, 90, 0)
        self.assertEqual(
            board.calls[-1],
            ("motors", [[1, -40], [2, 40], [3, -40], [4, 40]]),
        )

    def test_modern_stop_clears_speed_and_repeats_zero_duty(self):
        board = FakeModernBoard()
        chassis = _BoardMecanumChassis(board, modern_board=True)
        chassis.stop()
        zeros = [[1, 0], [2, 0], [3, 0], [4, 0]]
        self.assertEqual(board.calls[0], ("motor_speeds", zeros))
        self.assertEqual(board.calls[1:], [("motors", zeros)] * 3)

    def test_legacy_stop_repeats_zero_for_every_motor(self):
        board = FakeLegacyBoard()
        chassis = _BoardMecanumChassis(board, modern_board=False)
        chassis.stop()
        self.assertEqual(
            board.calls,
            [("motor", motor_id, 0) for _ in range(3) for motor_id in range(1, 5)],
        )

    def test_modern_board_mapping(self):
        board = FakeModernBoard()
        value = backend(board)
        value.drive(40, 90, -0.3)
        value.servo(1, 1500, 0.5)
        value.rgb(10, 20, 30)
        value.buzzer(1900, 0.1, 0.2, 2)
        self.assertEqual(value._chassis.command, (40, 90, -0.3))
        self.assertEqual(board.calls[0], ("servo", 0.5, [[1, 1500]]))
        self.assertEqual(board.calls[1], ("rgb", [[1, 10, 20, 30], [2, 10, 20, 30]]))
        self.assertEqual(board.calls[2], ("buzzer", 1900, 0.1, 0.2, 2))

    def test_ultrasonic_rgb_sets_both_sensor_leds(self):
        value = backend(FakeModernBoard())
        value._sonar = FakeSonar()
        with patch("masterpi_control.backends.Path.exists", return_value=True):
            value.sonar_rgb(12, 34, 56)
        self.assertEqual(
            value._sonar.calls,
            [
                ("mode", 0),
                ("pixel", 0, (12, 34, 56)),
                ("pixel", 1, (12, 34, 56)),
            ],
        )

    def test_wonderecho_recognition_reads_documented_register(self):
        value = backend(FakeModernBoard())
        bus = MagicMock()
        bus.__enter__.return_value = bus
        bus.read_i2c_block_data.return_value = [3]
        with (
            patch("masterpi_control.backends.Path.exists", return_value=True),
            patch("masterpi_control.backends.SMBus", return_value=bus),
        ):
            self.assertEqual(value.voice_result(), 3)
        bus.read_i2c_block_data.assert_called_once_with(0x34, 0x64, 1)

    def test_wonderecho_broadcast_writes_documented_register(self):
        value = backend(FakeModernBoard())
        bus = MagicMock()
        bus.__enter__.return_value = bus
        with (
            patch("masterpi_control.backends.Path.exists", return_value=True),
            patch("masterpi_control.backends.SMBus", return_value=bus),
        ):
            value.voice_speak(0x00, 0x01)
        bus.write_i2c_block_data.assert_called_once_with(0x34, 0x6E, [0x00, 0x01])

    def test_legacy_tutorial_mapping(self):
        board = FakeLegacyBoard()
        value = backend(board, modern=False)
        value.servo(2, 1750, 0.25)
        value.rgb(255, 0, 10)
        value.buzzer(1900, 0, 0, 1)
        self.assertEqual(board.calls[0], ("servo", 2, 1750, 250))
        self.assertEqual(
            board.RGB.calls,
            [("pixel", 0, (255, 0, 10)), ("pixel", 1, (255, 0, 10)), ("show",)],
        )
        self.assertEqual(board.calls[-2:], [("buzzer", 1), ("buzzer", 0)])

    def test_arm_duration_is_converted_to_milliseconds(self):
        value = backend(FakeModernBoard())
        value.arm(0, 6, 18, 0, -90, 90, 1.5)
        self.assertEqual(value._arm.calls[0], ((0, 6, 18), 0, -90, 90, 1500))

    def test_unreachable_arm_position_raises(self):
        value = backend(FakeModernBoard())
        value._arm = FakeArm(result=False)
        with self.assertRaisesRegex(ValueError, "not reachable"):
            value.arm(50, 50, 50, 0, -90, 90, 1)


if __name__ == "__main__":
    unittest.main()
