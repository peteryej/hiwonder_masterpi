import struct
import types
import unittest

from masterpi_control.respeaker_leds import (
    ReSpeakerLedError,
    direction_pixel,
    read_direction,
    show_direction,
    spin,
    turn_off,
)


class FakeDevice:
    def __init__(self):
        self.calls = []

    def ctrl_transfer(self, *args):
        self.calls.append(args)


class ReSpeakerLedTests(unittest.TestCase):
    def setUp(self):
        self.disposed = []
        self.usb = types.SimpleNamespace(
            core=types.SimpleNamespace(find=lambda **_kwargs: None),
            util=types.SimpleNamespace(
                CTRL_OUT=0x00,
                CTRL_IN=0x80,
                CTRL_TYPE_VENDOR=0x40,
                CTRL_RECIPIENT_DEVICE=0x00,
                dispose_resources=self.disposed.append,
            ),
        )

    def test_turn_off_disables_vad_then_sets_mono_black(self):
        device = FakeDevice()

        turn_off(device=device, usb_module=self.usb)

        self.assertEqual(
            device.calls,
            [
                (0x40, 0, 0x22, 0x1C, [0], 8000),
                (0x40, 0, 1, 0x1C, [0, 0, 0, 0], 8000),
            ],
        )
        self.assertEqual(self.disposed, [device])

    def test_turn_off_reports_missing_device(self):
        with self.assertRaisesRegex(ReSpeakerLedError, "was not found"):
            turn_off(usb_module=self.usb)

    def test_turn_off_wraps_usb_discovery_errors(self):
        self.usb.core.find = lambda **_kwargs: (_ for _ in ()).throw(
            PermissionError("access denied")
        )
        with self.assertRaisesRegex(ReSpeakerLedError, "access denied"):
            turn_off(usb_module=self.usb)

    def test_spin_sets_thinking_palette_and_starts_firmware_animation(self):
        device = FakeDevice()

        spin(device=device, usb_module=self.usb)

        self.assertEqual(
            device.calls,
            [
                (0x40, 0, 0x22, 0x1C, [0], 8000),
                (0x40, 0, 0x20, 0x1C, [8], 8000),
                (
                    0x40,
                    0,
                    0x21,
                    0x1C,
                    [0, 80, 255, 0, 0, 220, 255, 0],
                    8000,
                ),
                (0x40, 0, 5, 0x1C, [0], 8000),
            ],
        )
        self.assertEqual(self.disposed, [device])

    def test_direction_pixel_uses_nearest_thirty_degree_segment(self):
        self.assertEqual(direction_pixel(0), 0)
        self.assertEqual(direction_pixel(14.9), 0)
        self.assertEqual(direction_pixel(15), 1)
        self.assertEqual(direction_pixel(359), 0)
        self.assertEqual(direction_pixel(30, clockwise=False), 11)

    def test_show_direction_lights_only_one_custom_pixel(self):
        device = FakeDevice()

        pixel = show_direction(65, device=device, usb_module=self.usb)

        self.assertEqual(pixel, 2)
        self.assertEqual(device.calls[0], (0x40, 0, 0x22, 0x1C, [0], 8000))
        self.assertEqual(device.calls[1], (0x40, 0, 0x20, 0x1C, [8], 8000))
        custom = device.calls[2]
        self.assertEqual(custom[:4], (0x40, 0, 6, 0x1C))
        self.assertEqual(len(custom[4]), 48)
        self.assertEqual(custom[4][8:12], [0, 180, 60, 0])
        self.assertEqual(sum(1 for value in custom[4] if value), 2)
        self.assertEqual(self.disposed, [device])

    def test_read_direction_uses_xvf3000_doa_parameter(self):
        device = FakeDevice()

        def transfer(*args):
            device.calls.append(args)
            return struct.pack("<ii", 123, 0)

        device.ctrl_transfer = transfer

        angle = read_direction(device=device, usb_module=self.usb)

        self.assertEqual(angle, 123.0)
        self.assertEqual(device.calls, [(0xC0, 0, 0xC0, 21, 8, 8000)])
        self.assertEqual(self.disposed, [device])


if __name__ == "__main__":
    unittest.main()
