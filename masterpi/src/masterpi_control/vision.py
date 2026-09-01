"""Camera-guided recognition and grasping for the MasterPi gripper camera."""

from __future__ import annotations

import importlib
import threading
import time
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .camera import CameraStream, CameraUnavailable
from .robot import Robot, RobotError, ValidationError


SUPPORTED_COLORS = ("red", "green", "blue", "yellow")

# OpenCV HSV ranges. Red wraps around hue zero and therefore needs two masks.
HSV_RANGES: Dict[str, Tuple[Tuple[Tuple[int, int, int], Tuple[int, int, int]], ...]] = {
    "red": (((0, 100, 60), (10, 255, 255)), ((170, 100, 60), (180, 255, 255))),
    "green": (((35, 80, 50), (85, 255, 255)),),
    "blue": (((90, 90, 50), (135, 255, 255)),),
    "yellow": (((18, 100, 70), (35, 255, 255)),),
}


def _target_color(value: Any) -> str:
    if not isinstance(value, str):
        raise ValidationError("target must be any, red, green, blue, or yellow")
    target = value.strip().lower()
    if target not in ("any", *SUPPORTED_COLORS):
        raise ValidationError("target must be any, red, green, blue, or yellow")
    return target


def recognize_colored_object(
    jpeg: bytes,
    target: str = "any",
    min_area_ratio: float = 0.012,
) -> Optional[Dict[str, Any]]:
    """Return the largest supported colored object in one JPEG frame.

    The result contains normalized image coordinates so it is independent of
    camera resolution. This recognizes color/shape regions, not semantic
    object classes.
    """
    detections = recognize_colored_objects(jpeg, target, min_area_ratio)
    return detections[0] if detections else None


def recognize_colored_objects(
    jpeg: bytes,
    target: str = "any",
    min_area_ratio: float = 0.012,
) -> List[Dict[str, Any]]:
    """Return all supported colored objects in one JPEG frame, largest first."""
    target = _target_color(target)
    if not isinstance(jpeg, (bytes, bytearray)) or not jpeg:
        raise CameraUnavailable("Camera returned an empty JPEG frame")
    if not 0 < min_area_ratio <= 0.5:
        raise ValidationError("min_area_ratio must be greater than 0 and at most 0.5")

    try:
        cv2 = importlib.import_module("cv2")
        numpy = importlib.import_module("numpy")
    except (ImportError, OSError) as exc:
        raise CameraUnavailable(f"OpenCV vision support is unavailable: {exc}") from exc

    encoded = numpy.frombuffer(jpeg, dtype=numpy.uint8)
    image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if image is None:
        raise CameraUnavailable("Camera returned an invalid JPEG frame")
    height, width = image.shape[:2]
    hsv = cv2.cvtColor(cv2.GaussianBlur(image, (5, 5), 0), cv2.COLOR_BGR2HSV)
    kernel = numpy.ones((5, 5), numpy.uint8)
    colors: Iterable[str] = SUPPORTED_COLORS if target == "any" else (target,)
    detections: List[Dict[str, Any]] = []

    for color in colors:
        mask = None
        for lower, upper in HSV_RANGES[color]:
            part = cv2.inRange(hsv, lower, upper)
            mask = part if mask is None else cv2.bitwise_or(mask, part)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        contours = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2]
        if not contours:
            continue
        for contour in contours:
            area_ratio = float(cv2.contourArea(contour)) / float(width * height)
            if area_ratio < min_area_ratio:
                continue
            x, y, object_width, object_height = cv2.boundingRect(contour)
            detections.append(
                {
                    "label": f"{color} object",
                    "color": color,
                    "center_x": round((x + object_width / 2) / width, 4),
                    "center_y": round((y + object_height / 2) / height, 4),
                    "width": round(object_width / width, 4),
                    "height": round(object_height / height, 4),
                    "area_ratio": round(area_ratio, 4),
                }
            )
    return sorted(detections, key=lambda item: item["area_ratio"], reverse=True)


def annotate_object_detections(jpeg: bytes, objects: Any) -> bytes:
    """Draw normalized Hermes object boxes and labels on a camera JPEG."""
    try:
        cv2 = importlib.import_module("cv2")
        numpy = importlib.import_module("numpy")
    except (ImportError, OSError) as exc:
        raise CameraUnavailable(f"OpenCV annotation support is unavailable: {exc}") from exc
    image = cv2.imdecode(numpy.frombuffer(jpeg, dtype=numpy.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise CameraUnavailable("Camera returned an invalid JPEG frame")
    height, width = image.shape[:2]
    for item in objects if isinstance(objects, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("bbox"), dict):
            continue
        box = item["bbox"]
        try:
            x1 = int(float(box["x_min"]) * width)
            y1 = int(float(box["y_min"]) * height)
            x2 = int(float(box["x_max"]) * width)
            y2 = int(float(box["y_max"]) * height)
        except (KeyError, TypeError, ValueError):
            continue
        x1, x2 = sorted((max(0, min(width - 1, x1)), max(0, min(width - 1, x2))))
        y1, y2 = sorted((max(0, min(height - 1, y1)), max(0, min(height - 1, y2))))
        if x1 == x2 or y1 == y2:
            continue
        label = str(item.get("label") or "object")
        confidence = item.get("confidence")
        if isinstance(confidence, (int, float)) and confidence > 0:
            label = f"{label} {confidence:.0%}"
        cv2.rectangle(image, (x1, y1), (x2, y2), (0, 220, 255), 2)
        (text_width, text_height), baseline = cv2.getTextSize(
            label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2
        )
        text_top = max(0, y1 - text_height - baseline - 4)
        cv2.rectangle(
            image,
            (x1, text_top),
            (min(width - 1, x1 + text_width + 6), y1),
            (0, 220, 255),
            -1,
        )
        cv2.putText(
            image,
            label,
            (x1 + 3, max(text_height, y1 - baseline - 3)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (20, 20, 20),
            2,
            cv2.LINE_AA,
        )
    encoded_ok, encoded = cv2.imencode(
        ".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 88]
    )
    if not encoded_ok:
        raise CameraUnavailable("Could not encode the annotated camera image")
    return encoded.tobytes()


class VisionGrasper:
    """Recognize a centered colored object and execute a bounded pickup pose."""

    def __init__(self, robot: Robot, camera: CameraStream) -> None:
        self.robot = robot
        self.camera = camera
        self._lock = threading.Lock()

    def recognize(self, target: Any = "any", samples: Any = 3) -> Dict[str, Any]:
        target_value = _target_color(target)
        if isinstance(samples, bool) or not isinstance(samples, int) or not 1 <= samples <= 5:
            raise ValidationError("samples must be an integer between 1 and 5")

        sequence = 0
        detections = []
        for _ in range(samples):
            sequence, frame = self.camera.next_frame(after=sequence, timeout=3.0)
            detection = recognize_colored_object(frame, target_value)
            if detection is not None:
                detections.append(detection)

        if not detections:
            return {"recognized": False, "target": target_value, "samples": samples}

        color, count = Counter(item["color"] for item in detections).most_common(1)[0]
        matching = [item for item in detections if item["color"] == color]
        required = samples // 2 + 1
        if count < required:
            return {
                "recognized": False,
                "target": target_value,
                "samples": samples,
                "reason": "unstable detection",
            }
        center_x_values = [float(item["center_x"]) for item in matching]
        center_y_values = [float(item["center_y"]) for item in matching]
        if (
            max(center_x_values) - min(center_x_values) > 0.08
            or max(center_y_values) - min(center_y_values) > 0.08
        ):
            return {
                "recognized": False,
                "target": target_value,
                "samples": samples,
                "reason": "object moved between camera frames",
            }
        detection = dict(max(matching, key=lambda item: item["area_ratio"]))
        detection.update({"recognized": True, "matches": count, "samples": samples})
        return detection

    def analyze_scene(self, samples: Any = 3) -> Dict[str, Any]:
        """Describe stable colored objects in the live camera without moving."""
        if isinstance(samples, bool) or not isinstance(samples, int) or not 1 <= samples <= 5:
            raise ValidationError("samples must be an integer between 1 and 5")

        sequence = 0
        frame_counts = []
        for _ in range(samples):
            sequence, frame = self.camera.next_frame(after=sequence, timeout=3.0)
            frame_counts.append(Counter(item["color"] for item in recognize_colored_objects(frame)))

        required = samples // 2 + 1
        objects = []
        for color in SUPPORTED_COLORS:
            counts = [counts_by_color[color] for counts_by_color in frame_counts]
            visible_counts = [count for count in counts if count > 0]
            if len(visible_counts) < required:
                continue
            visible_counts.sort()
            count = visible_counts[len(visible_counts) // 2]
            objects.append({"label": f"{color} object", "color": color, "count": count})
        return {
            "objects": objects,
            "object_count": sum(item["count"] for item in objects),
            "samples": samples,
            "recognizer": "color regions",
        }

    def _perform_fixed_pickup(self) -> None:
        """Run the fixed front pickup pose and finish at Home."""
        self.robot.stop()
        self.robot.home(0.8)
        time.sleep(0.82)
        self.robot.gripper(True, 0.4)
        time.sleep(0.42)
        self.robot.arm(0, 16.5, 8, -90, -90, 0, 0.8)
        time.sleep(0.82)
        self.robot.arm(0, 16.5, 2, -90, -90, 0, 0.5)
        time.sleep(0.52)
        self.robot.gripper(False, 0.5)
        time.sleep(0.52)
        self.robot.home(1.0)

    def grab_front(self) -> Dict[str, Any]:
        """Execute the fixed pickup unconditionally, without camera checks."""
        if not self._lock.acquire(blocking=False):
            raise RobotError("A grasp is already running")
        try:
            self._perform_fixed_pickup()
            return {
                "grabbed": True,
                "mode": "fixed front pickup",
                "pickup": {"x": 0.0, "y": 16.5, "z": 2.0, "units": "cm"},
                "returned_home": True,
            }
        finally:
            self.robot.stop()
            self._lock.release()

    def recognize_and_grab(self, target: Any = "any") -> Dict[str, Any]:
        """Grab a stable object centered under the gripper-mounted camera.

        The pickup coordinate comes from Hiwonder's color-sorting lesson. The
        function deliberately does not drive the chassis or guess depth from a
        single camera.
        """
        if not self._lock.acquire(blocking=False):
            raise RobotError("A camera-guided grasp is already running")
        try:
            detection = self.recognize(target, samples=3)
            if not detection.get("recognized"):
                return {"grabbed": False, **detection}

            offset_x = float(detection["center_x"]) - 0.5
            offset_y = float(detection["center_y"]) - 0.5
            if abs(offset_x) > 0.18 or abs(offset_y) > 0.22:
                return {
                    "grabbed": False,
                    "reason": "object is not centered under the gripper camera",
                    "offset_x": round(offset_x, 4),
                    "offset_y": round(offset_y, 4),
                    "detection": detection,
                }

            # Fixed, table-height pickup from the vendor color-sorting course.
            # Every move is bounded and the chassis remains stopped throughout.
            self._perform_fixed_pickup()
            return {
                "grabbed": True,
                "detection": detection,
                "pickup": {"x": 0.0, "y": 16.5, "z": 2.0, "units": "cm"},
            }
        finally:
            self.robot.stop()
            self._lock.release()
