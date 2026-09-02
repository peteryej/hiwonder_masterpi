"""ReSpeaker direction-of-arrival sensing and bounded sound following."""

from __future__ import annotations

import math
import struct
import time
from typing import Any, Dict, Optional

from .robot import Robot, RobotError, ValidationError, _integer, _number


USB_VENDOR_ID = 0x2886
USB_PRODUCT_ID = 0x0018
USB_TIMEOUT_MS = 8000
DOA_PARAMETER_ID = 21
DOA_PARAMETER_OFFSET = 0
VOICE_PARAMETER_ID = 19
VOICE_PARAMETER_OFFSET = 32


class SoundUnavailable(RobotError):
    """Raised when the ReSpeaker direction interface cannot be read."""


class ReSpeakerDirection:
    """Read the XVF3000's processed DOA and VAD values over USB."""

    def __init__(
        self,
        front_angle: float = 0,
        clockwise: bool = True,
        usb_module: Any = None,
    ) -> None:
        self.front_angle = _number("front_angle", front_angle, 0, 359)
        if not isinstance(clockwise, bool):
            raise ValidationError("clockwise must be true or false")
        self.clockwise = clockwise
        self._usb = usb_module

    def _module(self) -> Any:
        if self._usb is not None:
            return self._usb
        try:
            import usb
        except ImportError as exc:
            raise SoundUnavailable("PyUSB is not installed") from exc
        return usb

    @staticmethod
    def _read_int(device: Any, usb_module: Any, parameter_id: int, offset: int) -> int:
        request_type = (
            usb_module.util.CTRL_IN
            | usb_module.util.CTRL_TYPE_VENDOR
            | usb_module.util.CTRL_RECIPIENT_DEVICE
        )
        command = 0x80 | 0x40 | offset
        response = device.ctrl_transfer(
            request_type,
            0,
            command,
            parameter_id,
            8,
            USB_TIMEOUT_MS,
        )
        return int(struct.unpack("<ii", bytes(response))[0])

    def read(self) -> Dict[str, Any]:
        usb_module = self._module()
        try:
            device = usb_module.core.find(
                idVendor=USB_VENDOR_ID, idProduct=USB_PRODUCT_ID
            )
        except Exception as exc:
            raise SoundUnavailable(f"could not access ReSpeaker USB control: {exc}") from exc
        if device is None:
            raise SoundUnavailable("ReSpeaker USB 4-Mic Array was not found")
        try:
            raw_angle = self._read_int(
                device, usb_module, DOA_PARAMETER_ID, DOA_PARAMETER_OFFSET
            )
            voice_active = bool(
                self._read_int(
                    device, usb_module, VOICE_PARAMETER_ID, VOICE_PARAMETER_OFFSET
                )
            )
        except Exception as exc:
            raise SoundUnavailable(f"could not read ReSpeaker direction: {exc}") from exc
        finally:
            usb_module.util.dispose_resources(device)
        if not 0 <= raw_angle <= 359:
            raise SoundUnavailable(f"ReSpeaker returned invalid DOA angle {raw_angle}")
        relative = (raw_angle - self.front_angle + 180) % 360 - 180
        if not self.clockwise:
            relative = -relative
        return {
            "raw_angle": float(raw_angle),
            "relative_angle": float(relative),
            "voice_active": voice_active,
            "front_angle": self.front_angle,
            "clockwise": self.clockwise,
        }

    def sample(self, samples: Any = 5, interval: Any = 0.04) -> Dict[str, Any]:
        sample_count = _integer("samples", samples, 1, 15)
        interval_value = _number("interval", interval, 0, 0.25)
        readings = []
        for index in range(sample_count):
            readings.append(self.read())
            if index + 1 < sample_count and interval_value:
                time.sleep(interval_value)
        angles = [math.radians(reading["relative_angle"]) for reading in readings]
        sine = sum(math.sin(angle) for angle in angles)
        cosine = sum(math.cos(angle) for angle in angles)
        stability = math.hypot(sine, cosine) / sample_count
        if stability < 0.5:
            raise SoundUnavailable(
                "sound bearing was unstable; ask the speaker to repeat from one position"
            )
        mean = math.degrees(math.atan2(sine, cosine))
        return {
            "relative_angle": round(mean, 1),
            "raw_angles": [reading["raw_angle"] for reading in readings],
            "voice_active": any(reading["voice_active"] for reading in readings),
            "samples": sample_count,
            "front_angle": self.front_angle,
            "clockwise": self.clockwise,
            "stability": round(stability, 3),
        }


class SoundTracker:
    """Turn toward the latest sound bearing and make a short sonar-limited approach."""

    def __init__(self, robot: Robot, locator: Optional[ReSpeakerDirection] = None) -> None:
        self.robot = robot
        self.locator = locator or ReSpeakerDirection()

    def direction(self, samples: Any = 5) -> Dict[str, Any]:
        return self.locator.sample(samples=samples)

    def come_here(
        self,
        approach_duration: Any = 2.0,
        clearance_cm: Any = 45,
        samples: Any = 5,
    ) -> Dict[str, Any]:
        bearing = self.direction(samples=samples)
        motion = self.robot.approach_bearing(
            bearing["relative_angle"],
            approach_duration=approach_duration,
            clearance_cm=clearance_cm,
        )
        return {
            "mode": "sound_source_approach",
            "bearing": bearing,
            "motion": motion,
            "limitation": "DOA supplies bearing only; approach distance is time- and sonar-bounded.",
        }
