"""Validated, thread-safe high-level MasterPi controls."""

from __future__ import annotations

import math
import threading
import time
from typing import Any, Dict, Optional

from .backends import HardwareBackend


class RobotError(RuntimeError):
    pass


class ValidationError(RobotError, ValueError):
    pass


BUTTON_EVENT_NAMES = {
    0x00: "click (SDK-mapped)",
    0x01: "pressed",
    0x02: "long press",
    0x04: "long-press repeat",
    0x08: "released after long press",
    0x10: "released after short press",
    0x20: "click",
    0x40: "double click",
    0x80: "triple click",
}

VOICE_COMMAND_NAMES = {
    0x01: "Go straight",
    0x02: "Go backward",
    0x03: "Turn left",
    0x04: "Turn right",
    0x09: "Stop",
}

# WonderEcho broadcasts IDs already compiled into its firmware. It does not
# synthesize arbitrary text, so expose only phrases documented by Hiwonder.
VOICE_BROADCASTS = {
    "forward": (0x00, 0x01, "Going forward"),
    "backward": (0x00, 0x02, "Going backward"),
    "left": (0x00, 0x03, "Turning left"),
    "right": (0x00, 0x04, "Turning right"),
    "received": (0x00, 0x09, "Received"),
}


def _number(name: str, value: Any, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise ValidationError(f"{name} must be a number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{name} must be a number") from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValidationError(f"{name} must be between {minimum:g} and {maximum:g}")
    return result


def _integer(name: str, value: Any, minimum: int, maximum: int) -> int:
    number = _number(name, value, minimum, maximum)
    if not number.is_integer():
        raise ValidationError(f"{name} must be an integer")
    return int(number)


class Robot:
    """Coordinates hardware access and enforces command limits.

    A dead-man watchdog is enabled by default. While the chassis is moving, a
    fresh drive command must arrive before ``watchdog_timeout`` expires or the
    motors are stopped automatically.
    """

    def __init__(self, backend: HardwareBackend, watchdog_timeout: float = 0.6) -> None:
        self.backend = backend
        self.watchdog_timeout = _number("watchdog_timeout", watchdog_timeout, 0.1, 10.0)
        self._lock = threading.RLock()
        self._gesture_lock = threading.Lock()
        self._drive_lock = threading.Lock()
        self._closed = threading.Event()
        self._moving = False
        self._last_drive = time.monotonic()
        self._last_chassis_stop = 0.0
        self._voice_active_id = 0
        self._state: Dict[str, Any] = {
            "backend": getattr(backend, "details", {"name": backend.name}),
            "drive": {"speed": 0.0, "direction": 0.0, "angular_rate": 0.0},
            "arm": None,
            "servos": {},
            "rgb": {"red": 0, "green": 0, "blue": 0},
            "distance": None,
            "sonar_rgb": {"red": 0, "green": 0, "blue": 0},
            "voice_last": None,
            "voice_detection_count": 0,
            "voice_broadcast": None,
            "last_button": None,
            "button_home_count": 0,
            "watchdog_stops": 0,
            "idle_stop_heartbeats": 0,
            "last_error": None,
        }
        # The expansion board can retain the last motor duties across a client
        # restart.  Synchronize the physical chassis with our initial stopped
        # state before accepting commands or starting the watchdog.
        self.backend.stop()
        self._last_chassis_stop = time.monotonic()
        try:
            self.backend.sonar_rgb(0, 0, 0)
        except Exception as exc:
            # The ultrasonic sensor is optional; a missing I2C device must not
            # prevent chassis and arm control from starting.
            self._state["last_error"] = f"initial sonar LED reset failed: {exc}"
        self._watchdog = threading.Thread(target=self._watchdog_loop, daemon=True)
        self._watchdog.start()
        self._button_listener = threading.Thread(target=self._button_loop, daemon=True)
        self._button_listener.start()

    def _ensure_open(self) -> None:
        if self._closed.is_set():
            raise RobotError("Robot controller is closed")

    def drive(self, speed: Any, direction: Any, angular_rate: Any = 0) -> Dict[str, float]:
        speed_value = _number("speed", speed, 0, 100)
        direction_value = _number("direction", direction, 0, 360)
        angular_value = _number("angular_rate", angular_rate, -2, 2)
        command = {
            "speed": speed_value,
            "direction": direction_value,
            "angular_rate": angular_value,
        }
        with self._lock:
            self._ensure_open()
            self.backend.drive(speed_value, direction_value, angular_value)
            self._last_drive = time.monotonic()
            self._moving = speed_value != 0 or angular_value != 0
            self._state["drive"] = command
        return command

    def stop(self) -> Dict[str, float]:
        command = {"speed": 0.0, "direction": 0.0, "angular_rate": 0.0}
        with self._lock:
            if not self._closed.is_set():
                self.backend.stop()
                self._last_chassis_stop = time.monotonic()
            self._moving = False
            self._state["drive"] = command
        return command

    def _run_drive_for(self, speed: float, direction: float, angular_rate: float, duration: float) -> None:
        """Refresh the dead-man watchdog during a finite chassis movement."""
        deadline = time.monotonic() + duration
        try:
            while time.monotonic() < deadline:
                self.drive(speed, direction, angular_rate)
                time.sleep(min(0.15, max(0.01, deadline - time.monotonic())))
        finally:
            self.stop()

    def drive_for(
        self, direction: Any, duration: Any = 1.0, speed: Any = 40
    ) -> Dict[str, Any]:
        """Drive like the web controls for a finite, watchdog-refreshed interval."""
        if not isinstance(direction, str):
            raise ValidationError("direction must be forward, backward, left, right, rotate_left, or rotate_right")
        directions = {
            "forward": (90.0, 0.0),
            "backward": (270.0, 0.0),
            "left": (180.0, 0.0),
            "right": (0.0, 0.0),
            # Match the webpage turn controls exactly: rotation has no linear
            # component, uses the forward heading placeholder, and yaws at
            # +/-0.6 rather than mixing translation into the turn.
            "rotate_left": (90.0, -0.6),
            "rotate_right": (90.0, 0.6),
        }
        key = direction.strip().lower()
        motion = directions.get(key)
        if motion is None:
            raise ValidationError("direction must be forward, backward, left, right, rotate_left, or rotate_right")
        # The webpage's known-working default is 40 mm/s. Lower duties can be
        # below the starting torque of some wheels, so agent motion rejects
        # values below that threshold instead of energizing only one wheel.
        speed_value = _number("speed", speed, 40, 100)
        duration_value = _number("duration", duration, 0.05, 8)
        linear_speed = 0.0 if key.startswith("rotate_") else speed_value
        with self._drive_lock:
            self._run_drive_for(linear_speed, motion[0], motion[1], duration_value)
        return {
            "direction": key,
            "speed": linear_speed,
            "heading": motion[0],
            "angular_rate": motion[1],
            "duration": duration_value,
        }

    def avoid_obstacles(self, duration: Any, speed: Any = 40, clearance_cm: Any = 30) -> Dict[str, Any]:
        """Move forward briefly, stopping and turning right at an ultrasonic obstacle.

        This is reactive obstacle avoidance, not mapping, localization, or
        distance-accurate navigation: the chassis has no verified odometry.
        """
        duration_value = _number("duration", duration, 0.1, 8)
        speed_value = _number("speed", speed, 40, 100)
        clearance_value = _number("clearance_cm", clearance_cm, 15, 80)
        obstacles_avoided = 0
        deadline = time.monotonic() + duration_value
        with self._drive_lock:
            try:
                while time.monotonic() < deadline:
                    reading = self.distance()
                    if reading["centimeters"] < clearance_value:
                        obstacles_avoided += 1
                        self.stop()
                        # A short, bounded turn is the only safe response we
                        # can make with a single forward-facing range sensor.
                        self._run_drive_for(0.0, 90.0, 0.6, 0.5)
                        break
                    self.drive(speed_value, 90.0, 0.0)
                    time.sleep(min(0.15, max(0.01, deadline - time.monotonic())))
            finally:
                self.stop()
        return {
            "mode": "reactive_obstacle_avoidance",
            "duration": duration_value,
            "speed": speed_value,
            "clearance_cm": clearance_value,
            "obstacles_avoided": obstacles_avoided,
        }

    def approach_bearing(
        self,
        relative_angle: Any,
        approach_duration: Any = 2.0,
        clearance_cm: Any = 45,
        speed: Any = 40,
    ) -> Dict[str, Any]:
        """Rotate toward a relative bearing, then approach until time or sonar limit."""
        angle = _number("relative_angle", relative_angle, -180, 180)
        duration = _number("approach_duration", approach_duration, 0.1, 3)
        clearance = _number("clearance_cm", clearance_cm, 25, 100)
        speed_value = _number("speed", speed, 40, 60)
        turn_rate = 0.6
        turn_duration = 0.0
        stopped_for_obstacle = False
        final_distance: Optional[float] = None
        with self._drive_lock:
            try:
                if abs(angle) > 10:
                    # Physical calibration: a 180-degree chassis turn takes
                    # one second at the webpage's validated yaw rate.
                    turn_duration = abs(angle) / 180.0
                    self._run_drive_for(
                        0.0,
                        90.0,
                        turn_rate if angle > 0 else -turn_rate,
                        turn_duration,
                    )
                deadline = time.monotonic() + duration
                while time.monotonic() < deadline:
                    reading = self.distance()
                    final_distance = reading["centimeters"]
                    if final_distance <= clearance:
                        stopped_for_obstacle = True
                        break
                    self.drive(speed_value, 90.0, 0.0)
                    time.sleep(min(0.15, max(0.01, deadline - time.monotonic())))
            finally:
                self.stop()
        return {
            "relative_angle": angle,
            "turn_duration": round(turn_duration, 3),
            "approach_duration": duration,
            "speed": speed_value,
            "clearance_cm": clearance,
            "stopped_for_obstacle": stopped_for_obstacle,
            "final_distance_cm": final_distance,
        }

    def servo(self, servo_id: Any, pulse: Any, duration: Any = 0.5) -> Dict[str, Any]:
        servo_value = _integer("servo_id", servo_id, 1, 6)
        pulse_value = _integer("pulse", pulse, 500, 2500)
        duration_value = _number("duration", duration, 0.02, 30)
        command = {"servo_id": servo_value, "pulse": pulse_value, "duration": duration_value}
        with self._lock:
            self._ensure_open()
            self.backend.servo(servo_value, pulse_value, duration_value)
            self._state["servos"][str(servo_value)] = command
        return command

    def arm(
        self,
        x: Any,
        y: Any,
        z: Any,
        pitch: Any = 0,
        pitch_min: Any = -90,
        pitch_max: Any = 90,
        duration: Any = 1.0,
    ) -> Dict[str, float]:
        command = {
            "x": _number("x", x, -50, 50),
            "y": _number("y", y, -50, 50),
            "z": _number("z", z, -10, 50),
            "pitch": _number("pitch", pitch, -90, 90),
            "pitch_min": _number("pitch_min", pitch_min, -180, 180),
            "pitch_max": _number("pitch_max", pitch_max, -180, 180),
            "duration": _number("duration", duration, 0.02, 30),
        }
        if command["pitch_min"] > command["pitch_max"]:
            raise ValidationError("pitch_min cannot be greater than pitch_max")
        with self._lock:
            self._ensure_open()
            self.backend.arm(**command)
            self._state["arm"] = command
        return command

    def home(self, duration: Any = 1.5) -> Dict[str, float]:
        return self.arm(0, 6, 18, 0, -90, 90, duration)

    def check_front(self, duration: Any = 0.8) -> Dict[str, Any]:
        """Move the arm servos to the user-defined forward-looking pose."""
        duration_value = _number("duration", duration, 0.02, 30)
        targets = ((3, 500), (4, 2500), (5, 810), (6, 1500))
        with self._gesture_lock:
            commands = [
                self.servo(servo_id, pulse, duration_value)
                for servo_id, pulse in targets
            ]
        return {
            "pose": "check_front",
            "duration": duration_value,
            "servos": commands,
        }

    def nod(self) -> Dict[str, Any]:
        """Move servo 3 through the safe nod range and return to Home."""
        with self._gesture_lock:
            self.home(0.8)
            time.sleep(0.82)
            try:
                # Servo 3 is 695 at the documented Home pose. A positive 30°
                # offset is 1028 pulses; the negative offset is clamped to its
                # validated 500-pulse mechanical minimum.
                self.servo(3, 1028, 0.35)
                time.sleep(0.37)
                self.servo(3, 500, 0.35)
                time.sleep(0.37)
            finally:
                self.home(0.8)
        return {"gesture": "nod", "cycles": 1}

    def shake(self) -> Dict[str, Any]:
        """Swing servo 6 twice through ±20° from Home, then return Home."""
        with self._gesture_lock:
            self.home(0.8)
            time.sleep(0.82)
            try:
                # Servo 6 is 1500 at Home; 20° is 222 pulses at 2000/180.
                for pulse in (1722, 1278, 1722, 1278):
                    self.servo(6, pulse, 0.35)
                    time.sleep(0.37)
            finally:
                self.home(0.8)
        return {"gesture": "shake", "cycles": 2}

    def gripper(self, opened: Any, duration: Any = 0.5) -> Dict[str, Any]:
        if not isinstance(opened, bool):
            raise ValidationError("opened must be true or false")
        # Tutorial defaults: servo 1, 2000 open and 1500 closed.
        return self.servo(1, 2000 if opened else 1500, duration)

    def rgb(self, red: Any, green: Any, blue: Any) -> Dict[str, int]:
        command = {
            "red": _integer("red", red, 0, 255),
            "green": _integer("green", green, 0, 255),
            "blue": _integer("blue", blue, 0, 255),
        }
        with self._lock:
            self._ensure_open()
            self.backend.rgb(**command)
            self._state["rgb"] = command
        return command

    def buzzer(
        self,
        frequency: Any = 1900,
        on_time: Any = 0.1,
        off_time: Any = 0.1,
        repeat: Any = 1,
    ) -> Dict[str, Any]:
        command = {
            "frequency": _integer("frequency", frequency, 50, 10000),
            "on_time": _number("on_time", on_time, 0.01, 5),
            "off_time": _number("off_time", off_time, 0, 5),
            "repeat": _integer("repeat", repeat, 1, 20),
        }
        with self._lock:
            self._ensure_open()
        # Legacy expansion boards implement buzzer timing with sleeps. Do not
        # hold the controller lock while they sound, or motion watchdog stops
        # would be delayed by the full beep sequence.
        self.backend.buzzer(**command)
        return command

    def distance(self) -> Dict[str, float]:
        with self._lock:
            self._ensure_open()
            try:
                millimeters = int(self.backend.distance_mm())
            except Exception as exc:
                raise RobotError(str(exc)) from exc
            if not 0 < millimeters <= 5000:
                raise RobotError("Ultrasonic sensor returned an invalid distance")
            reading = {
                "millimeters": millimeters,
                "centimeters": round(millimeters / 10.0, 1),
            }
            self._state["distance"] = reading
            return reading

    def sonar_rgb(self, red: Any, green: Any, blue: Any) -> Dict[str, int]:
        command = {
            "red": _integer("red", red, 0, 255),
            "green": _integer("green", green, 0, 255),
            "blue": _integer("blue", blue, 0, 255),
        }
        with self._lock:
            self._ensure_open()
            try:
                self.backend.sonar_rgb(**command)
            except Exception as exc:
                raise RobotError(str(exc)) from exc
            self._state["sonar_rgb"] = command
        return command

    def voice_result(self) -> Dict[str, Any]:
        """Poll WonderEcho without assigning recognized phrases to robot actions."""
        with self._lock:
            self._ensure_open()
            try:
                phrase_id = int(self.backend.voice_result())
            except Exception as exc:
                raise RobotError(str(exc)) from exc
            if not 0 <= phrase_id <= 255:
                raise RobotError("WonderEcho returned an invalid recognition ID")

            detected = phrase_id != 0 and phrase_id != self._voice_active_id
            if phrase_id == 0:
                self._voice_active_id = 0
            elif detected:
                self._voice_active_id = phrase_id
                event = {
                    "id": phrase_id,
                    "phrase": VOICE_COMMAND_NAMES.get(
                        phrase_id, f"Command ID 0x{phrase_id:02X}"
                    ),
                    "detected_at": time.time(),
                }
                self._state["voice_last"] = event
                self._state["voice_detection_count"] += 1

            last = self._state["voice_last"]
            return {
                "detected": detected,
                "current_id": phrase_id,
                "last": None if last is None else dict(last),
                "count": self._state["voice_detection_count"],
            }

    def voice_speak(self, phrase: Any) -> Dict[str, Any]:
        if not isinstance(phrase, str):
            raise ValidationError("phrase must be a supported phrase name")
        key = phrase.strip().lower()
        broadcast = VOICE_BROADCASTS.get(key)
        if broadcast is None:
            choices = ", ".join(VOICE_BROADCASTS)
            raise ValidationError(
                f"phrase must be one of: {choices}; WonderEcho cannot speak arbitrary text"
            )
        phrase_type, phrase_id, spoken_text = broadcast
        with self._lock:
            self._ensure_open()
            try:
                self.backend.voice_speak(phrase_type, phrase_id)
            except Exception as exc:
                raise RobotError(str(exc)) from exc
            result = {"phrase": key, "spoken_text": spoken_text}
            self._state["voice_broadcast"] = result
        return result

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "backend": dict(self._state["backend"]),
                "drive": dict(self._state["drive"]),
                "arm": None if self._state["arm"] is None else dict(self._state["arm"]),
                "servos": {key: dict(value) for key, value in self._state["servos"].items()},
                "rgb": dict(self._state["rgb"]),
                "distance": (
                    None
                    if self._state["distance"] is None
                    else dict(self._state["distance"])
                ),
                "sonar_rgb": dict(self._state["sonar_rgb"]),
                "voice_last": (
                    None
                    if self._state["voice_last"] is None
                    else dict(self._state["voice_last"])
                ),
                "voice_detection_count": self._state["voice_detection_count"],
                "voice_broadcast": (
                    None
                    if self._state["voice_broadcast"] is None
                    else dict(self._state["voice_broadcast"])
                ),
                "last_button": (
                    None
                    if self._state["last_button"] is None
                    else dict(self._state["last_button"])
                ),
                "button_home_count": self._state["button_home_count"],
                "watchdog_stops": self._state["watchdog_stops"],
                "idle_stop_heartbeats": self._state["idle_stop_heartbeats"],
                "last_error": self._state["last_error"],
                "closed": self._closed.is_set(),
            }

    def _watchdog_loop(self) -> None:
        interval = min(self.watchdog_timeout / 4, 0.1)
        while not self._closed.wait(interval):
            with self._lock:
                now = time.monotonic()
                if self._moving and now - self._last_drive > self.watchdog_timeout:
                    try:
                        self.backend.stop()
                    except Exception as exc:  # keep retrying a failed emergency stop
                        self._state["last_error"] = f"watchdog stop failed: {exc}"
                    else:
                        self._last_chassis_stop = now
                        self._moving = False
                        self._state["drive"] = {
                            "speed": 0.0,
                            "direction": 0.0,
                            "angular_rate": 0.0,
                        }
                        self._state["watchdog_stops"] += 1
                elif not self._moving and now - self._last_chassis_stop >= 0.5:
                    # The expansion board can be reset independently while
                    # this process keeps running. Reassert zero in both motor
                    # modes so stale or reset channel state cannot persist.
                    try:
                        self.backend.stop()
                    except Exception as exc:
                        self._state["last_error"] = f"idle safety stop failed: {exc}"
                    else:
                        self._last_chassis_stop = now
                        self._state["idle_stop_heartbeats"] += 1

    def _button_loop(self) -> None:
        """Map a press of expansion-board KEY2 to the arm Home pose."""
        poll_button = getattr(self.backend, "button_event", None)
        if not callable(poll_button):
            return
        last_key2_press = 0.0
        while not self._closed.wait(0.05):
            try:
                event = poll_button()
            except Exception as exc:
                with self._lock:
                    self._state["last_error"] = f"button read failed: {exc}"
                self._closed.wait(0.2)
                continue
            if event is None:
                continue
            key_id, event_state = event
            event_name = BUTTON_EVENT_NAMES.get(event_state, "unknown")
            print(
                f"[masterpi] button event: key_id={key_id}, "
                f"event={event_state} ({event_name})",
                flush=True,
            )
            # Hiwonder firmware versions differ here: some report the initial
            # press (1), while others report a raw click (32) or the SDK's
            # mapped click value (0).
            if key_id != 2 or event_state not in (0x00, 0x01, 0x20):
                continue
            now = time.monotonic()
            if now - last_key2_press < 1.0:
                continue
            last_key2_press = now
            print("[masterpi] KEY2 action: moving arm Home", flush=True)
            try:
                self.home(1.5)
            except Exception as exc:
                print(f"[masterpi] KEY2 Home failed: {exc}", flush=True)
                with self._lock:
                    self._state["last_error"] = f"KEY2 Home failed: {exc}"
            else:
                print("[masterpi] KEY2 Home command completed", flush=True)
                with self._lock:
                    self._state["last_button"] = {"button": 2, "action": "home"}
                    self._state["button_home_count"] += 1

    def close(self) -> None:
        with self._lock:
            if self._closed.is_set():
                return
            try:
                self.backend.stop()
            finally:
                self._moving = False
                self._state["drive"] = {
                    "speed": 0.0,
                    "direction": 0.0,
                    "angular_rate": 0.0,
                }
                self._last_chassis_stop = time.monotonic()
                self._closed.set()
        if threading.current_thread() is not self._watchdog:
            self._watchdog.join(timeout=1)
        if threading.current_thread() is not self._button_listener:
            self._button_listener.join(timeout=1)
        close_backend = getattr(self.backend, "close", None)
        if callable(close_backend):
            close_backend()

    def __enter__(self) -> "Robot":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()
