"""ReSpeaker USB 4-Mic Array LED controls.

The device boots into trace mode, where its LEDs follow voice activity and
direction of arrival.  Sending mono black selects a steady, all-off mode;
disabling the VAD LED separately also keeps the center LED dark.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, Optional, Sequence


USB_VENDOR_ID = 0x2886
USB_PRODUCT_ID = 0x0018
USB_TIMEOUT_MS = 8000


class ReSpeakerLedError(RuntimeError):
    """Raised when the ReSpeaker LED interface cannot be controlled."""


def turn_off(device: Any = None, usb_module: Any = None) -> None:
    """Disable VAD lighting and set all twelve ring pixels to black."""

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
