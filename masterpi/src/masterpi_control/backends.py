"""Hardware adapters for current and legacy Hiwonder Raspberry Pi images."""

from __future__ import annotations

import importlib
import importlib.util
import math
import os
import queue
import sys
import threading
import time
import types
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple


class BackendUnavailable(RuntimeError):
    """Raised when the vendor libraries are not installed or usable."""


class HardwareBackend(Protocol):
    name: str

    def drive(self, speed: float, direction: float, angular_rate: float) -> None: ...
    def stop(self) -> None: ...
    def servo(self, servo_id: int, pulse: int, duration: float) -> None: ...
    def arm(
        self,
        x: float,
        y: float,
        z: float,
        pitch: float,
        pitch_min: float,
        pitch_max: float,
        duration: float,
    ) -> Any: ...
    def rgb(self, red: int, green: int, blue: int) -> None: ...
    def buzzer(self, frequency: int, on_time: float, off_time: float, repeat: int) -> None: ...
    def distance_mm(self) -> int: ...
    def sonar_rgb(self, red: int, green: int, blue: int) -> None: ...
    def button_event(self) -> Optional[Tuple[int, int]]: ...


def _load_class(candidates: Sequence[Tuple[str, str]]) -> Tuple[Any, str]:
    failures: List[str] = []
    for module_name, class_name in candidates:
        try:
            module = importlib.import_module(module_name)
            return getattr(module, class_name), module_name
        except (ImportError, AttributeError, OSError) as exc:
            failures.append(f"{module_name}: {exc}")
    raise BackendUnavailable("; ".join(failures))


def _provide_unused_plotting_stubs() -> None:
    """Satisfy unused plotting imports in Hiwonder's arm runtime.

    ``arm_move_ik.py`` imports Matplotlib and ``Axes3D`` but never references
    either. Requiring the large plotting stack on a headless robot is wasteful;
    these minimal modules preserve that vendor import without replacing any
    real Matplotlib installation.
    """
    if "matplotlib" in sys.modules or importlib.util.find_spec("matplotlib") is not None:
        return
    matplotlib = types.ModuleType("matplotlib")
    matplotlib.__path__ = []  # type: ignore[attr-defined]
    pyplot = types.ModuleType("matplotlib.pyplot")
    matplotlib.pyplot = pyplot  # type: ignore[attr-defined]
    toolkits = types.ModuleType("mpl_toolkits")
    toolkits.__path__ = []  # type: ignore[attr-defined]
    mplot3d = types.ModuleType("mpl_toolkits.mplot3d")
    mplot3d.Axes3D = object  # type: ignore[attr-defined]
    sys.modules.update(
        {
            "matplotlib": matplotlib,
            "matplotlib.pyplot": pyplot,
            "mpl_toolkits": toolkits,
            "mpl_toolkits.mplot3d": mplot3d,
        }
    )


def _provide_unused_vision_stub() -> None:
    """Allow arm-only use when OpenCV is absent from the headless runtime."""
    if "cv2" in sys.modules or importlib.util.find_spec("cv2") is not None:
        return
    # Hiwonder's transform module imports cv2 for optional camera-coordinate
    # helpers. ArmIK only imports and uses getAngle, which does not touch cv2.
    sys.modules["cv2"] = types.ModuleType("cv2")


def _configure_vendor_data_paths() -> None:
    """Point Hiwonder's hard-coded YAML paths at the discovered source tree."""
    try:
        yaml_handle = importlib.import_module("common.yaml_handle")
    except (ImportError, OSError):
        return
    configured = os.environ.get("MASTERPI_CONFIG_ROOT")
    roots = [Path(configured)] if configured else []
    roots.extend((Path("/home/pi/MasterPi"), Path.home() / "projs" / "MasterPi"))
    for root in roots:
        deviation = root / "Deviation.yaml"
        if deviation.is_file():
            yaml_handle.Deviation_file_path = str(deviation)
            lab_config = root / "lab_config.yaml"
            if lab_config.is_file():
                yaml_handle.lab_file_path = str(lab_config)
            try:
                calibration = importlib.import_module("CameraCalibration.CalibrationConfig")
            except (ImportError, OSError):
                pass
            else:
                calibration_root = root / "CameraCalibration"
                calibration.save_path = str(calibration_root / "calibration_images") + "/"
                calibration.calibration_param_path = str(calibration_root / "calibration_param")
                calibration.map_param_path = str(calibration_root / "map_param")
            return


class _BoardMecanumChassis:
    """Mecanum mixing using the already-open expansion-board connection."""

    def __init__(self, board: Any, modern_board: bool, a: float = 67, b: float = 59) -> None:
        self.board = board
        self.modern_board = modern_board
        self.a = a
        self.b = b

    def set_velocity(self, velocity: float, direction: float, angular_rate: float) -> None:
        if velocity == 0 and angular_rate == 0:
            self.stop()
            return
        radians = math.radians(direction)
        velocity_x = velocity * math.cos(radians)
        velocity_y = velocity * math.sin(radians)
        rotation = -angular_rate * (self.a + self.b)
        motor_1 = int(velocity_y + velocity_x - rotation)
        motor_2 = int(velocity_y - velocity_x + rotation)
        motor_3 = int(velocity_y - velocity_x - rotation)
        motor_4 = int(velocity_y + velocity_x + rotation)
        duties = ((1, -motor_1), (2, motor_2), (3, -motor_3), (4, motor_4))
        if self.modern_board:
            self.board.set_motor_duty([list(item) for item in duties])
        else:
            for motor_id, duty in duties:
                self.board.setMotor(motor_id, duty)

    def stop(self) -> None:
        """Clear every supported motor mode with redundant zero commands."""
        zeros = [[1, 0], [2, 0], [3, 0], [4, 0]]
        if self.modern_board:
            # A command left in the board's closed-loop speed mode may not be
            # represented by our duty-control state.  Clear it when supported,
            # then repeat the vendor's documented all-zero duty packet to make
            # an emergency stop resilient to a dropped UART frame.
            set_motor_speed = getattr(self.board, "set_motor_speed", None)
            if callable(set_motor_speed):
                set_motor_speed(zeros)
            for _ in range(3):
                self.board.set_motor_duty(zeros)
        else:
            for _ in range(3):
                for motor_id in range(1, 5):
                    self.board.setMotor(motor_id, 0)


class VendorBackend:
    """Adapter around libraries preinstalled on MasterPi/TurboPi images.

    Recent images expose snake_case methods on a ``Board`` instance. Older
    images expose camelCase functions from ``HiwonderSDK.Board``. Both are
    supported so this project can be copied to either generation.
    """

    name = "hiwonder"

    def __init__(self) -> None:
        self._add_vendor_paths()
        self._board: Any
        self._button_gpio: Any = None
        self._button_levels: Dict[int, int] = {}
        self.button_source = "UART"
        self.board_module: str
        self.serial_device = "legacy GPIO"
        self._modern_board = False
        try:
            board_type, self.board_module = _load_class(
                (
                    ("common.ros_robot_controller_sdk", "Board"),
                    ("HiwonderSDK.ros_robot_controller_sdk", "Board"),
                )
            )
            configured_device = os.environ.get("MASTERPI_SERIAL_DEVICE")
            if configured_device:
                self.serial_device = configured_device
                self._board = board_type(device=configured_device)
            elif Path("/dev/serial0").exists():
                self.serial_device = "/dev/serial0"
                self._board = board_type(device=self.serial_device)
            else:
                self.serial_device = "/dev/ttyAMA0"
                self._board = board_type()
            self._modern_board = True
        except BackendUnavailable:
            try:
                self._board = importlib.import_module("HiwonderSDK.Board")
                self.board_module = "HiwonderSDK.Board"
            except (ImportError, OSError) as exc:
                raise BackendUnavailable(
                    "Could not load the MasterPi expansion-board driver. Run this on "
                    "the vendor Raspberry Pi image, or use --mock."
                ) from exc
        except Exception as exc:
            raise BackendUnavailable(
                f"Found {self.board_module}, but could not open {self.serial_device}: {exc}. "
                "Disable the serial login console, enable UART hardware, reboot, and "
                "ensure this user can access the serial device."
            ) from exc

        if self._modern_board:
            enable_reception = getattr(self._board, "enable_reception", None)
            if callable(enable_reception):
                # Key events are received by the board's existing UART reader,
                # but the vendor SDK leaves that reader disabled by default.
                enable_reception(True)

        self._setup_gpio_buttons()

        self._chassis = _BoardMecanumChassis(self._board, self._modern_board)
        self.chassis_module = "masterpi_control built-in mecanum mixer"

        _configure_vendor_data_paths()
        _provide_unused_plotting_stubs()
        _provide_unused_vision_stub()
        try:
            arm_type, self.arm_module = _load_class(
                (
                    ("kinematics.arm_move_ik", "ArmIK"),
                    ("ArmIK.ArmMoveIK", "ArmIK"),
                )
            )
            self._arm = arm_type()
            if self._modern_board:
                # Current ArmIK creates no ``board`` attribute itself, but its
                # motion methods require one.  Attribute assignment is the
                # vendor-supported injection point.
                self._arm.board = self._board
        except BackendUnavailable:
            self._arm = None
            self.arm_module = "unavailable"

        try:
            sonar_type, self.sonar_module = _load_class(
                (
                    ("common.sonar", "Sonar"),
                    ("HiwonderSDK.Sonar", "Sonar"),
                )
            )
            self._sonar = sonar_type()
        except BackendUnavailable:
            self._sonar = None
            self.sonar_module = "unavailable"

    @staticmethod
    def _add_vendor_paths() -> None:
        """Make preinstalled SDK folders importable from any working directory."""
        configured = os.environ.get("MASTERPI_VENDOR_ROOT")
        roots = [Path(configured)] if configured else []
        roots.extend(
            (
                Path("/home/pi/MasterPi"),
                Path("/home/pi/TurboPi"),
                Path.home() / "projs" / "MasterPi",
            )
        )
        for root in roots:
            candidates = (
                root,
                root / "common_sdk",
                root / "kinematics_sdk",
                root / "masterpi_sdk" / "common_sdk",
                root / "masterpi_sdk" / "kinematics_sdk",
            )
            for candidate in candidates:
                if candidate.is_dir():
                    value = str(candidate)
                    if value not in sys.path:
                        sys.path.insert(0, value)

    def _setup_gpio_buttons(self) -> None:
        """Claim Hiwonder's active-low KEY1/KEY2 Raspberry Pi GPIO inputs."""
        configured_chip = os.environ.get("MASTERPI_GPIO_CHIP")
        candidates = (
            (configured_chip,)
            if configured_chip
            else ("/dev/gpiochip0", "/dev/gpiochip4")
        )
        for chip_path in candidates:
            if not chip_path or not Path(chip_path).exists():
                continue
            try:
                import gpiod

                settings = gpiod.LineSettings(
                    direction=gpiod.line.Direction.INPUT,
                    bias=gpiod.line.Bias.PULL_UP,
                    debounce_period=timedelta(milliseconds=20),
                )
                request = gpiod.request_lines(
                    chip_path,
                    consumer="masterpi-control-buttons",
                    config={(13, 23): settings},
                )
                values = request.get_values([13, 23])
            except (ImportError, OSError, RuntimeError, ValueError):
                continue
            self._button_gpio = request
            self._button_levels = {
                1: values[0].value,
                2: values[1].value,
            }
            self.button_source = f"{chip_path} (KEY1=GPIO13, KEY2=GPIO23)"
            return

    @property
    def details(self) -> Dict[str, str]:
        return {
            "name": self.name,
            "chassis_module": self.chassis_module,
            "board_module": self.board_module,
            "arm_module": self.arm_module,
            "sonar_module": self.sonar_module,
            "serial_device": self.serial_device,
            "button_source": self.button_source,
        }

    def drive(self, speed: float, direction: float, angular_rate: float) -> None:
        self._chassis.set_velocity(speed, direction, angular_rate)

    def stop(self) -> None:
        self._chassis.stop()

    def button_event(self) -> Optional[Tuple[int, int]]:
        """Return the next ``(key_id, event_code)`` event, if supported.

        The current SDK's ``get_button`` helper silently drops every event
        except PRESSED and CLICK. Read its public queue directly so callers can
        diagnose the complete event stream. Older drivers still use their
        helper as a fallback.
        """
        button_gpio = getattr(self, "_button_gpio", None)
        if button_gpio is not None:
            values = button_gpio.get_values([13, 23])
            levels = {1: values[0].value, 2: values[1].value}
            previous = self._button_levels
            self._button_levels = levels
            for key_id in (1, 2):
                if previous.get(key_id, 1) == 1 and levels[key_id] == 0:
                    return key_id, 0x01

        key_queue = getattr(self._board, "key_queue", None)
        if self._modern_board and key_queue is not None:
            try:
                data = key_queue.get_nowait()
            except queue.Empty:
                return None
            if len(data) < 2:
                return None
            return int(data[0]), int(data[1])
        get_button = getattr(self._board, "get_button", None)
        if not callable(get_button):
            return None
        event = get_button()
        if event is None:
            return None
        return int(event[0]), int(event[1])

    def close(self) -> None:
        button_gpio = getattr(self, "_button_gpio", None)
        if button_gpio is not None:
            button_gpio.release()
            self._button_gpio = None

    def servo(self, servo_id: int, pulse: int, duration: float) -> None:
        if self._modern_board:
            self._board.pwm_servo_set_position(duration, [[servo_id, pulse]])
        else:
            self._board.setPWMServoPulse(servo_id, pulse, int(duration * 1000))

    def arm(
        self,
        x: float,
        y: float,
        z: float,
        pitch: float,
        pitch_min: float,
        pitch_max: float,
        duration: float,
    ) -> Any:
        if self._arm is None:
            raise BackendUnavailable(
                "The arm inverse-kinematics module is unavailable. Expected "
                "kinematics.arm_move_ik or ArmIK.ArmMoveIK on the robot image."
            )
        result = self._arm.setPitchRangeMoving(
            (x, y, z), pitch, pitch_min, pitch_max, int(duration * 1000)
        )
        if result is False:
            raise ValueError("The requested arm position is not reachable")
        return result

    def rgb(self, red: int, green: int, blue: int) -> None:
        if self._modern_board:
            self._board.set_rgb([[1, red, green, blue], [2, red, green, blue]])
        else:
            color = self._board.PixelColor(red, green, blue)
            self._board.RGB.setPixelColor(0, color)
            self._board.RGB.setPixelColor(1, color)
            self._board.RGB.show()

    def buzzer(self, frequency: int, on_time: float, off_time: float, repeat: int) -> None:
        if self._modern_board:
            self._board.set_buzzer(frequency, on_time, off_time, repeat)
            return
        # Legacy boards expose only a digital on/off buzzer.
        for index in range(repeat):
            self._board.setBuzzer(1)
            time.sleep(on_time)
            self._board.setBuzzer(0)
            if index + 1 < repeat:
                time.sleep(off_time)

    def distance_mm(self) -> int:
        if self._sonar is None:
            raise BackendUnavailable(
                "The ultrasonic sensor module is unavailable. Expected common.sonar "
                "or HiwonderSDK.Sonar on the robot image."
            )
        bus_number = int(getattr(self._sonar, "i2c", 1))
        bus_device = Path(f"/dev/i2c-{bus_number}")
        if not bus_device.exists():
            raise BackendUnavailable(
                f"Ultrasonic sensor unavailable because {bus_device} is missing. "
                "Enable Raspberry Pi I2C and reboot."
            )
        distance = int(self._sonar.getDistance())
        if not 0 < distance <= 5000:
            cause = getattr(self._sonar, "last_error", None)
            detail = f": {cause}" if cause is not None else ""
            raise BackendUnavailable(
                "Ultrasonic sensor did not return a valid reading from I2C "
                f"address 0x77{detail}."
            )
        return distance

    def sonar_rgb(self, red: int, green: int, blue: int) -> None:
        if self._sonar is None:
            raise BackendUnavailable(
                "The ultrasonic sensor module is unavailable. Expected common.sonar "
                "or HiwonderSDK.Sonar on the robot image."
            )
        bus_number = int(getattr(self._sonar, "i2c", 1))
        bus_device = Path(f"/dev/i2c-{bus_number}")
        if not bus_device.exists():
            raise BackendUnavailable(
                f"Ultrasonic sensor unavailable because {bus_device} is missing."
            )
        results = (
            self._sonar.setRGBMode(0),
            self._sonar.setPixelColor(0, (red, green, blue)),
            self._sonar.setPixelColor(1, (red, green, blue)),
        )
        cause = getattr(self._sonar, "last_error", None)
        if any(result is False for result in results) or cause is not None:
            detail = f": {cause}" if cause is not None else ""
            raise BackendUnavailable(f"Could not set ultrasonic sensor LEDs{detail}")


@dataclass
class MockBackend:
    """In-memory backend for development, demos, and automated tests."""

    name: str = "mock"
    events: List[Dict[str, Any]] = field(default_factory=list)
    state: Dict[str, Any] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _button_events: List[Tuple[int, int]] = field(default_factory=list, repr=False)
    mock_distance_mm: int = 420

    @property
    def details(self) -> Dict[str, str]:
        return {"name": self.name}

    def _record(self, action: str, **values: Any) -> None:
        event = {"action": action, "time": time.monotonic(), **values}
        with self._lock:
            self.events.append(event)
            self.state[action] = values

    def drive(self, speed: float, direction: float, angular_rate: float) -> None:
        self._record("drive", speed=speed, direction=direction, angular_rate=angular_rate)

    def stop(self) -> None:
        self._record("drive", speed=0.0, direction=0.0, angular_rate=0.0)

    def press_button(self, button_id: int) -> None:
        """Queue a physical-button press for controller tests and mock demos."""
        with self._lock:
            self._button_events.append((button_id, 1))

    def click_button(self, button_id: int) -> None:
        """Queue the completed-click form emitted by some board firmware."""
        with self._lock:
            self._button_events.append((button_id, 0))

    def button_event(self) -> Optional[Tuple[int, int]]:
        with self._lock:
            if not self._button_events:
                return None
            return self._button_events.pop(0)

    def servo(self, servo_id: int, pulse: int, duration: float) -> None:
        self._record("servo", servo_id=servo_id, pulse=pulse, duration=duration)

    def arm(
        self,
        x: float,
        y: float,
        z: float,
        pitch: float,
        pitch_min: float,
        pitch_max: float,
        duration: float,
    ) -> Dict[str, Any]:
        values = {
            "x": x,
            "y": y,
            "z": z,
            "pitch": pitch,
            "pitch_min": pitch_min,
            "pitch_max": pitch_max,
            "duration": duration,
        }
        self._record("arm", **values)
        return values

    def rgb(self, red: int, green: int, blue: int) -> None:
        self._record("rgb", red=red, green=green, blue=blue)

    def buzzer(self, frequency: int, on_time: float, off_time: float, repeat: int) -> None:
        self._record(
            "buzzer", frequency=frequency, on_time=on_time, off_time=off_time, repeat=repeat
        )

    def distance_mm(self) -> int:
        return self.mock_distance_mm

    def sonar_rgb(self, red: int, green: int, blue: int) -> None:
        self._record("sonar_rgb", red=red, green=green, blue=blue)
