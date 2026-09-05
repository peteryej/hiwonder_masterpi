"""ReSpeaker USB 4-Mic Array LED controls.

The device boots into trace mode, where its LEDs follow voice activity and
direction of arrival.  Sending mono black selects a steady, all-off mode;
disabling the VAD LED separately also keeps the center LED dark.
"""

from __future__ import annotations

import argparse
import math
import struct
import sys
from typing import Any, Optional, Sequence


USB_VENDOR_ID = 0x2886
USB_PRODUCT_ID = 0x0018
USB_TIMEOUT_MS = 8000
THINKING_PRIMARY = (0, 80, 255)
THINKING_SECONDARY = (0, 220, 255)
THINKING_BRIGHTNESS = 8
VOICE_DIRECTION_COLOR = (0, 180, 60)
VOICE_DIRECTION_BRIGHTNESS = 8
RING_PIXELS = 12
DOA_PARAMETER_ID = 21
DOA_PARAMETER_OFFSET = 0


class ReSpeakerLedError(RuntimeError):
    """Raised when the ReSpeaker LED interface cannot be controlled."""


def _usb_target(device: Any, usb_module: Any) -> tuple[Any, Any, int]:
    if usb_module is None:
        try:
            import usb as usb_module
        except ImportError as exc:
            raise ReSpeakerLedError("PyUSB is not installed") from exc

    if device is None:
        try:
            device = usb_module.core.find(
                idVendor=USB_VENDOR_ID, idProduct=USB_PRODUCT_ID
            )
        except Exception as exc:
            raise ReSpeakerLedError(
                f"could not access the ReSpeaker USB interface: {exc}"
            ) from exc
    if device is None:
        raise ReSpeakerLedError("ReSpeaker USB 4-Mic Array was not found")

    request_type = (
        usb_module.util.CTRL_OUT
        | usb_module.util.CTRL_TYPE_VENDOR
        | usb_module.util.CTRL_RECIPIENT_DEVICE
    )
    return device, usb_module, request_type


def _rgb_payload(color: tuple[int, int, int]) -> list[int]:
    if len(color) != 3 or any(
        not isinstance(value, int) or not 0 <= value <= 255 for value in color
    ):
        raise ReSpeakerLedError("LED color values must be integers from 0 to 255")
    return [*color, 0]


def turn_off(device: Any = None, usb_module: Any = None) -> None:
    """Disable VAD lighting and set all twelve ring pixels to black."""

    device, usb_module, request_type = _usb_target(device, usb_module)
    try:
        # 0x22 controls the center VAD LED: zero forces it off.
        device.ctrl_transfer(request_type, 0, 0x22, 0x1C, [0], USB_TIMEOUT_MS)
        # Command 1 selects mono mode; black turns all twelve pixels off and
        # prevents trace-mode VAD/DOA updates until the device resets.
        device.ctrl_transfer(request_type, 0, 1, 0x1C, [0, 0, 0, 0], USB_TIMEOUT_MS)
    except Exception as exc:
        raise ReSpeakerLedError(f"could not turn off ReSpeaker LEDs: {exc}") from exc
    finally:
        usb_module.util.dispose_resources(device)


def spin(
    device: Any = None,
    usb_module: Any = None,
    *,
    primary: tuple[int, int, int] = THINKING_PRIMARY,
    secondary: tuple[int, int, int] = THINKING_SECONDARY,
    brightness: int = THINKING_BRIGHTNESS,
) -> None:
    """Start the firmware's rotating ring animation for agent thinking."""

    if not isinstance(brightness, int) or not 0 <= brightness <= 31:
        raise ReSpeakerLedError("LED brightness must be an integer from 0 to 31")
    device, usb_module, request_type = _usb_target(device, usb_module)
    try:
        # Keep the center VAD LED out of the thinking animation.
        device.ctrl_transfer(request_type, 0, 0x22, 0x1C, [0], USB_TIMEOUT_MS)
        # The ring firmware accepts brightness 0..31 and two RGBA palette colors.
        device.ctrl_transfer(
            request_type, 0, 0x20, 0x1C, [brightness], USB_TIMEOUT_MS
        )
        device.ctrl_transfer(
            request_type,
            0,
            0x21,
            0x1C,
            _rgb_payload(primary) + _rgb_payload(secondary),
            USB_TIMEOUT_MS,
        )
        # Command 5 is the built-in clockwise spin/rotating-circle pattern.
        device.ctrl_transfer(request_type, 0, 5, 0x1C, [0], USB_TIMEOUT_MS)
    except ReSpeakerLedError:
        raise
    except Exception as exc:
        raise ReSpeakerLedError(
            f"could not start ReSpeaker thinking animation: {exc}"
        ) from exc
    finally:
        usb_module.util.dispose_resources(device)


def direction_pixel(
    angle_degrees: float,
    *,
    offset_degrees: float = 0,
    clockwise: bool = True,
) -> int:
    """Map a DOA angle to the nearest of the twelve physical ring pixels."""

    if isinstance(angle_degrees, bool) or not isinstance(angle_degrees, (int, float)):
        raise ReSpeakerLedError("direction angle must be a finite number")
    angle = float(angle_degrees)
    offset = float(offset_degrees)
    if not math.isfinite(angle) or not math.isfinite(offset):
        raise ReSpeakerLedError("direction angle must be a finite number")
    normalized = (angle - offset) % 360
    if not clockwise:
        normalized = (-normalized) % 360
    return int((normalized + 15) // 30) % RING_PIXELS


def show_direction(
    angle_degrees: float,
    device: Any = None,
    usb_module: Any = None,
    *,
    color: tuple[int, int, int] = VOICE_DIRECTION_COLOR,
    brightness: int = VOICE_DIRECTION_BRIGHTNESS,
    offset_degrees: float = 0,
    clockwise: bool = True,
) -> int:
    """Light only the ring pixel nearest a ReSpeaker DOA angle."""

    if not isinstance(brightness, int) or not 0 <= brightness <= 31:
        raise ReSpeakerLedError("LED brightness must be an integer from 0 to 31")
    pixel = direction_pixel(
        angle_degrees,
        offset_degrees=offset_degrees,
        clockwise=clockwise,
    )
    active = _rgb_payload(color)
    pixels = [0] * (RING_PIXELS * 4)
    start = pixel * 4
    pixels[start : start + 4] = active
    device, usb_module, request_type = _usb_target(device, usb_module)
    try:
        device.ctrl_transfer(request_type, 0, 0x22, 0x1C, [0], USB_TIMEOUT_MS)
        device.ctrl_transfer(
            request_type, 0, 0x20, 0x1C, [brightness], USB_TIMEOUT_MS
        )
        # Command 6 is custom mode: one RGBA tuple per physical ring pixel.
        device.ctrl_transfer(request_type, 0, 6, 0x1C, pixels, USB_TIMEOUT_MS)
    except ReSpeakerLedError:
        raise
    except Exception as exc:
        raise ReSpeakerLedError(
            f"could not show ReSpeaker voice direction: {exc}"
        ) from exc
    finally:
        usb_module.util.dispose_resources(device)
    return pixel


def read_direction(device: Any = None, usb_module: Any = None) -> float:
    """Read the current raw XVF3000 DOA angle without robot-stack imports."""

    device, usb_module, _ = _usb_target(device, usb_module)
    request_type = (
        usb_module.util.CTRL_IN
        | usb_module.util.CTRL_TYPE_VENDOR
        | usb_module.util.CTRL_RECIPIENT_DEVICE
    )
    try:
        command = 0x80 | 0x40 | DOA_PARAMETER_OFFSET
        response = device.ctrl_transfer(
            request_type,
            0,
            command,
            DOA_PARAMETER_ID,
            8,
            USB_TIMEOUT_MS,
        )
        angle = int(struct.unpack("<ii", bytes(response))[0])
        if not 0 <= angle <= 359:
            raise ReSpeakerLedError(f"ReSpeaker returned invalid DOA angle {angle}")
        return float(angle)
    except ReSpeakerLedError:
        raise
    except Exception as exc:
        raise ReSpeakerLedError(f"could not read ReSpeaker voice direction: {exc}") from exc
    finally:
        usb_module.util.dispose_resources(device)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Turn off the ReSpeaker USB LED ring")
    parser.parse_args(argv)
    try:
        turn_off()
    except ReSpeakerLedError as exc:
        print(f"respeaker-leds: {exc}", file=sys.stderr)
        return 2
    print("ReSpeaker USB LED ring is off")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
