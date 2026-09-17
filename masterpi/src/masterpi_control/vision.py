"""Camera-guided recognition and grasping for the MasterPi gripper camera."""

from __future__ import annotations

import base64
import importlib
import logging
import re
import threading
import time
from collections import Counter
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from .camera import CameraStream, CameraUnavailable
from .robot import Robot, RobotError, ValidationError


LOG = logging.getLogger(__name__)

SUPPORTED_COLORS = ("red", "green", "blue", "yellow")
GROUND_PICKUP_XYZ = (2.0, 13.0, -1.0)
GUIDED_LIFT_XYZ = (0.0, 15.0, 20.0)

# Check ground calibration from docs/grab_object_plan.md: 272 px = 5.5 cm at
# the reference depth. It only holds near that plane, so it is used for small
# bounded corrections, never as a floor coordinate.
GROUND_PIXELS_PER_CM = 49.45
CENTER_TOLERANCE_CM = 1.0
MAX_ALIGNMENT_CORRECTIONS = 6
# Bounded per-step corrections; the chassis responds nonlinearly to short
# pulses, so each one is followed by a fresh observation.
LATERAL_STEP_RANGE_CM = (1.0, 4.0)
FORWARD_STEP_RANGE_CM = (2.0, 5.0)
# Check front calibration from docs/grab_object_plan.md: 124 px = 6 cm near
# centre-bottom, and the image's bottom edge is about 23 cm ahead.
FRONT_PIXELS_PER_CM = 20.67
FRONT_TOLERANCE_CM = 5.0
FRONT_APPROACH_CM = 20.0
# A box already touching the bottom edge is at the ~23 cm reference or nearer,
# so the full approach would drive into it.
FRONT_CLOSE_APPROACH_CM = 8.0
MAX_FRONT_CORRECTIONS = 3
# A box touching an edge is only a reason to close the distance when what is
# visible is a sliver of a distant object. A large object simply overflows the
# ground view when it is close enough to grasp, and its visible centre is the
# best estimate there is -- treating that as "clipped, correct again" walks the
# robot past the object forever.
SLIVER_AREA_RATIO = 0.30
# Two corrections that do not move the offset mean the model's boxes, not the
# chassis, are the limit; more pulses will not help.
MIN_CORRECTION_PROGRESS_CM = 0.3
CONFIRM_DELAY_SECONDS = 1.5
# A held object sits right at the gripper camera and fills the near field.
HELD_AREA_RATIO = 0.45

# Labels that describe the scene rather than a graspable object.
BACKGROUND_KEYWORDS = (
    "floor", "ground", "surface", "carpet", "rug", "tile", "wall", "shadow",
    "table", "room", "background", "light", "ceiling",
)


def _is_background(label: Any) -> bool:
    text = str(label).lower()
    return any(keyword in text for keyword in BACKGROUND_KEYWORDS)


def _box_metrics(bbox: Mapping[str, Any]) -> Dict[str, float]:
    x_min = float(bbox["x_min"])
    y_min = float(bbox["y_min"])
    x_max = float(bbox["x_max"])
    y_max = float(bbox["y_max"])
    return {
        "center_x": (x_min + x_max) / 2,
        "center_y": (y_min + y_max) / 2,
        "area": max(0.0, x_max - x_min) * max(0.0, y_max - y_min),
        "clipped": x_min <= 0.001 or y_min <= 0.001 or x_max >= 0.999 or y_max >= 0.999,
        "clipped_top": y_min <= 0.001,
        "clipped_bottom": y_max >= 0.999,
    }


# Words that carry no object identity when matching a spoken target.
TARGET_STOPWORDS = frozenset({
    "the", "a", "an", "my", "that", "this", "some", "please", "object", "thing",
    "it", "one", "there", "here", "up", "off", "from", "on", "floor", "ground",
})


def label_matches_target(label: Any, target: str) -> bool:
    """True when a Hermes label names what the operator asked for.

    The operator's words rarely match the model's: "the orange can" against
    "red beverage can". Matching on the significant words, not the phrase,
    keeps a colour or filler word from losing the object.
    """
    label_words = set(re.findall(r"[a-z0-9]+", str(label).lower()))
    target_text = target.lower()
    if target_text and target_text in str(label).lower():
        return True
    wanted = {word for word in re.findall(r"[a-z0-9]+", target_text)
              if word not in TARGET_STOPWORDS}
    return bool(wanted & label_words)


def select_ground_target(objects: Any, target: Any = None) -> Optional[Dict[str, Any]]:
    """Pick the object to grasp from one Hermes analysis.

    Named targets win by label match. Otherwise the largest non-background
    object wins, excluding boxes that cover nearly the whole frame: those are
    the floor or a backdrop the model labelled as a thing.
    """
    candidates = []
    wanted = str(target).strip().lower() if isinstance(target, str) and target.strip() else None
    for item in objects if isinstance(objects, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("bbox"), dict):
            continue
        label = str(item.get("label", ""))
        try:
            metrics = _box_metrics(item["bbox"])
        except (KeyError, TypeError, ValueError):
            continue
        if metrics["area"] <= 0 or metrics["area"] > 0.97:
            continue
        if wanted is not None:
            if not label_matches_target(label, wanted):
                continue
        elif _is_background(label):
            continue
        candidates.append({"label": label, "bbox": item["bbox"], **metrics,
                           "confidence": item.get("confidence")})
    if not candidates:
        return None
    return max(candidates, key=lambda item: item["area"])

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
        center_tolerance = 0.04
        clusters = [
            [
                candidate
                for candidate in matching
                if abs(float(candidate["center_x"]) - float(pivot["center_x"]))
                <= center_tolerance
                and abs(float(candidate["center_y"]) - float(pivot["center_y"]))
                <= center_tolerance
            ]
            for pivot in matching
        ]
        stable = max(clusters, key=len)
        if len(stable) < required:
            return {
                "recognized": False,
                "target": target_value,
                "samples": samples,
                "reason": "object moved between camera frames",
            }
        detection = dict(max(stable, key=lambda item: item["area_ratio"]))
        detection.update({"recognized": True, "matches": len(stable), "samples": samples})
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
        """Run the fixed ground-level pickup pose and finish at Home.

        The arm approaches from wherever it currently is. Homing first was
        removed so the operator can line the gripper up by hand or with the
        jog pads and keep that view until the pickup starts.
        """
        self.robot.stop()
        self.robot.gripper(True, 0.4)
        time.sleep(0.42)
        x, y, z = GROUND_PICKUP_XYZ
        self.robot.arm(x, y, 8, -90, -90, 0, 0.8)
        time.sleep(0.82)
        # -66 degrees exceeds servo 4's limit after its calibration offset.
        # -68 degrees is the closest valid pitch at this operator-defined XYZ
        # and stays valid at the lowered z (servo 4 reaches 2483 of 2500).
        self.robot.arm(x, y, z, -68, -90, -68, 0.5)
        time.sleep(0.52)
        self.robot.gripper(False, 0.5)
        time.sleep(0.52)
        self.robot.home(1.0)

    def _perform_can_pickup(self) -> None:
        """Use the operator-recorded direct-servo can pose and finish Home."""
        self.robot.stop()
        self.robot.home(0.8)
        time.sleep(0.82)
        self.robot.gripper(True, 0.4)
        time.sleep(0.42)
        for servo_id, pulse in ((3, 1550), (4, 1620), (5, 2500), (6, 1500)):
            self.robot.servo(servo_id, pulse, 0.8)
        time.sleep(0.82)
        self.robot.gripper(False, 0.5)
        time.sleep(0.52)
        self.robot.home(1.0)

    @staticmethod
    def _annotated_data_uri(frame: bytes, objects: Any) -> Optional[str]:
        """Annotated frame as a data URI, or None if annotation is unavailable."""
        try:
            annotated = annotate_object_detections(frame, objects)
        except CameraUnavailable:
            return None
        return "data:image/jpeg;base64," + base64.b64encode(annotated).decode("ascii")

    def _perform_guided_pickup(self, pickup_z: float) -> None:
        """Pick up at the aligned ground point and finish holding at the lift pose."""
        self.robot.stop()
        self.robot.gripper(True, 0.4)
        time.sleep(0.42)
        x, y, _ = GROUND_PICKUP_XYZ
        self.robot.arm(x, y, 8, -90, -90, 0, 0.8)
        time.sleep(0.82)
        self.robot.arm(x, y, pickup_z, -68, -90, -68, 1.0)
        time.sleep(1.05)
        self.robot.gripper(False, 0.5)
        time.sleep(0.52)
        # The skill finishes holding at the lift pose, not Home, and never
        # releases: Check front/ground would open the gripper and drop it.
        self.robot.arm(*GUIDED_LIFT_XYZ, 0, -90, 90, 1.5)
        time.sleep(1.55)

    def _observe_ground(self, analyze: Callable[[bytes], Dict[str, Any]]) -> Dict[str, Any]:
        _, frame = self.camera.next_frame(timeout=3.0)
        analysis = analyze(frame)
        return {"frame": frame, "analysis": analysis}

    def _confirm_hold(
        self, analyze: Callable[[bytes], Dict[str, Any]], label: Optional[str]
    ) -> Dict[str, Any]:
        """Judge retention from two spaced frames.

        The stream can stick on an older frame, so one capture right after the
        lift can still show the pre-lift view and read as a hold that never
        happened. Identical frames prove nothing either way.
        """
        _, first = self.camera.next_frame(timeout=3.0)
        time.sleep(CONFIRM_DELAY_SECONDS)
        _, second = self.camera.next_frame(timeout=3.0)
        if first == second:
            # Identical bytes mean a stale stream, not a still scene: neither
            # frame says anything about what the gripper is holding.
            return {"held": None, "reason": "camera stream is stuck on one frame"}
        analysis = analyze(second)
        image = self._annotated_data_uri(second, analysis.get("objects"))
        near = select_ground_target(analysis.get("objects"), label)
        if near is None:
            near = select_ground_target(analysis.get("objects"))
        if near is not None and near["area"] >= HELD_AREA_RATIO:
            return {
                "held": True,
                "near_field": {"label": near["label"], "area": round(near["area"], 3)},
                "description": analysis.get("description"),
                "image": image,
            }
        return {
            "held": False,
            "reason": "no object fills the near field at the lift pose",
            "description": analysis.get("description"),
            "image": image,
        }

    def _approach_from_front(
        self,
        analyze: Callable[[bytes], Dict[str, Any]],
        target: Any,
        emit: Callable[..., None],
        corrections: List[Dict[str, Any]],
    ) -> bool:
        """Find the object further out in Check front and drive up to it.

        The ground view only covers the few centimetres in front of the
        gripper, so an object outside it is not missing -- it is just further
        away. This is the skill's Check front stage: align on the object's
        base near image bottom-centre, then close the distance once.
        """
        emit(
            "check_front",
            "Nothing in the ground view. Looking further ahead from the Check front pose.",
        )
        self.robot.check_front(0.8)
        time.sleep(1.0)
        chosen = None
        for _ in range(MAX_FRONT_CORRECTIONS + 1):
            observation = self._observe_ground(analyze)
            analysis = observation["analysis"]
            chosen = select_ground_target(analysis.get("objects"), target)
            if chosen is None and target:
                chosen = select_ground_target(analysis.get("objects"))
            if chosen is None:
                emit(
                    "failed",
                    "Not in the Check front view either: "
                    + str(analysis.get("description") or "no description"),
                    image=self._annotated_data_uri(
                        observation["frame"], analysis.get("objects")
                    ),
                )
                return False
            # Front alignment is on the object's base at bottom-centre, and
            # only sideways: the approach itself closes the distance.
            offset_x_cm = (chosen["center_x"] - 0.5) * 640 / FRONT_PIXELS_PER_CM
            emit(
                "observe",
                f"Check front shows {chosen['label']} {offset_x_cm:+.1f} cm across"
                + (" - lined up, approaching." if abs(offset_x_cm) <= FRONT_TOLERANCE_CM
                   else " - strafing to line up."),
                target=chosen["label"],
                offset_x_cm=round(offset_x_cm, 2),
                image=self._annotated_data_uri(
                    observation["frame"], analysis.get("objects")
                ),
            )
            if abs(offset_x_cm) <= FRONT_TOLERANCE_CM:
                break
            direction = "right" if offset_x_cm > 0 else "left"
            low, high = LATERAL_STEP_RANGE_CM
            step = min(max(abs(offset_x_cm), low), high)
            emit("correct", f"Moving {direction} {step:.1f} cm.",
                 direction=direction, distance_cm=round(step, 2))
            self.robot.move(direction, distance_cm=round(step, 2))
            corrections.append({"view": "front", "direction": direction,
                                "distance_cm": round(step, 2)})
            time.sleep(0.8)

        approach = (
            FRONT_CLOSE_APPROACH_CM if chosen["clipped_bottom"] else FRONT_APPROACH_CM
        )
        emit(
            "correct",
            f"Driving forward {approach:g} cm to bring it into the ground view"
            + (" (it is already close, so a short hop)." if chosen["clipped_bottom"] else "."),
            direction="forward",
            distance_cm=approach,
        )
        self.robot.move("forward", distance_cm=approach)
        corrections.append({"view": "front", "direction": "forward",
                            "distance_cm": approach})
        time.sleep(0.8)
        emit("check_ground", "Back to the Check ground pose to line up the pickup.")
        self.robot.check_ground(0.8)
        time.sleep(1.2)
        return True

    def grab_object(
        self,
        analyze: Callable[[bytes], Dict[str, Any]],
        target: Any = None,
        pickup_z: Any = GROUND_PICKUP_XYZ[2],
        on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Run the hibot-ground-grab procedure: look, center, pick up, confirm.

        This is the guarded counterpart to the blind quick action: it finds the
        object in the Check ground view, drives the chassis in small bounded
        steps until the object's centre is at the frame centre, picks up, and
        judges retention from two spaced frames. One attempt only -- a failed
        grasp is reported for the operator to authorize a retry, never retried
        here.

        ``on_event`` receives each step as it happens -- what the camera saw,
        every chassis correction and why, the pickup, the verdict -- so an
        operator watching the chat panel sees the reasoning, not just a verdict
        minutes later. A callback that raises must not abort a run that is
        already moving the arm, so its errors are swallowed.
        """
        def emit(stage: str, text: str, **extra: Any) -> None:
            if on_event is None:
                return
            try:
                on_event({"stage": stage, "text": text, **extra})
            except Exception:
                LOG.debug("grab_object progress listener failed", exc_info=True)
        depth = float(pickup_z)
        if not -3.0 <= depth <= 2.0:
            raise ValidationError("pickup_z must be between -3 and 2 cm")
        if not self._lock.acquire(blocking=False):
            raise RobotError("A camera-guided grasp is already running")
        corrections: List[Dict[str, Any]] = []
        substituted = False
        front_stage_done = False
        previous_offsets: Optional[Tuple[float, float]] = None
        try:
            emit("start", "Grab object: looking at the ground first."
                 + (f" Target: {target}." if target else ""))
            self.robot.stop()
            self.robot.check_ground(0.8)
            time.sleep(1.2)
            # One extra pass covers the Check front detour, which consumes an
            # iteration without making a ground correction.
            for _ in range(MAX_ALIGNMENT_CORRECTIONS + 2):
                observation = self._observe_ground(analyze)
                analysis = observation["analysis"]
                chosen = select_ground_target(analysis.get("objects"), target)
                if chosen is None and target:
                    # The operator's word for it rarely matches the model's
                    # ("the toy" against "plush teddy bear"), so fall back to
                    # the one graspable object and say what was picked.
                    chosen = select_ground_target(analysis.get("objects"))
                    if chosen is not None and not substituted:
                        substituted = True
                        emit(
                            "observe",
                            f"Nothing here is labelled '{target}'. The ground view has "
                            f"{chosen['label']}, so that is what I will pick up.",
                            target=chosen["label"],
                            target_requested=str(target),
                        )
                if chosen is None:
                    emit(
                        "observe",
                        "Nothing graspable in the Check ground view: "
                        + str(analysis.get("description") or "no description"),
                        image=self._annotated_data_uri(
                            observation["frame"], analysis.get("objects")
                        ),
                    )
                    if not front_stage_done:
                        front_stage_done = True
                        if self._approach_from_front(analyze, target, emit, corrections):
                            continue
                        return {
                            "grabbed": False,
                            "reason": "object not found in the ground or front view",
                            "target_requested": str(target) if target else None,
                            "description": analysis.get("description"),
                            "corrections": corrections,
                        }
                    return {
                        "grabbed": False,
                        "reason": "no object found in the Check ground view",
                        "target_requested": str(target) if target else None,
                        "description": analysis.get("description"),
                        "corrections": corrections,
                    }
                offset_x_cm = (chosen["center_x"] - 0.5) * 640 / GROUND_PIXELS_PER_CM
                offset_y_cm = (chosen["center_y"] - 0.5) * 480 / GROUND_PIXELS_PER_CM
                # A clipped sliver hides most of the object, so its centre is
                # not the object's centre: close the gap before trusting it.
                # A large clipped box is a close object overflowing the view.
                sliver = chosen["area"] < SLIVER_AREA_RATIO
                if sliver and chosen["clipped_top"]:
                    offset_y_cm = min(offset_y_cm, -FORWARD_STEP_RANGE_CM[0])
                elif sliver and chosen["clipped_bottom"]:
                    offset_y_cm = max(offset_y_cm, FORWARD_STEP_RANGE_CM[0])
                aligned = (
                    abs(offset_x_cm) <= CENTER_TOLERANCE_CM
                    and abs(offset_y_cm) <= CENTER_TOLERANCE_CM
                    and not (chosen["clipped"] and sliver)
                )
                emit(
                    "observe",
                    f"Saw {chosen['label']} at {offset_x_cm:+.1f} cm across, "
                    f"{offset_y_cm:+.1f} cm away from the pickup point"
                    + (", box clipped at the frame edge" if chosen["clipped"] else "")
                    + (" - centred, picking it up." if aligned else " - correcting."),
                    target=chosen["label"],
                    offset_x_cm=round(offset_x_cm, 2),
                    offset_y_cm=round(offset_y_cm, 2),
                    clipped=chosen["clipped"],
                    image=self._annotated_data_uri(
                        observation["frame"], analysis.get("objects")
                    ),
                )
                if aligned:
                    break
                if previous_offsets is not None and all(
                    abs(now - before) < MIN_CORRECTION_PROGRESS_CM
                    for now, before in zip((offset_x_cm, offset_y_cm), previous_offsets)
                ):
                    emit(
                        "failed",
                        f"The last correction did not move {chosen['label']} "
                        f"({offset_x_cm:+.1f} cm across, {offset_y_cm:+.1f} cm away). "
                        "Stopping rather than pulsing at it.",
                    )
                    return {
                        "grabbed": False,
                        "reason": "corrections stopped changing the measured offset",
                        "target": chosen["label"],
                        "offset_x_cm": round(offset_x_cm, 2),
                        "offset_y_cm": round(offset_y_cm, 2),
                        "corrections": corrections,
                    }
                previous_offsets = (offset_x_cm, offset_y_cm)
                if len(corrections) >= MAX_ALIGNMENT_CORRECTIONS:
                    emit(
                        "failed",
                        f"Stopping after {MAX_ALIGNMENT_CORRECTIONS} corrections: "
                        f"{chosen['label']} is still {offset_x_cm:+.1f} cm across, "
                        f"{offset_y_cm:+.1f} cm away. Not picking up.",
                    )
                    return {
                        "grabbed": False,
                        "reason": "could not centre the object within the correction limit",
                        "target": chosen["label"],
                        "offset_x_cm": round(offset_x_cm, 2),
                        "offset_y_cm": round(offset_y_cm, 2),
                        "corrections": corrections,
                    }
                # Correct the dominant axis, then look again.
                if abs(offset_x_cm) >= abs(offset_y_cm):
                    direction = "right" if offset_x_cm > 0 else "left"
                    low, high = LATERAL_STEP_RANGE_CM
                else:
                    direction = "forward" if offset_y_cm < 0 else "backward"
                    low, high = FORWARD_STEP_RANGE_CM
                step = min(max(abs(offset_x_cm if direction in ("left", "right") else offset_y_cm), low), high)
                emit(
                    "correct",
                    f"Moving {direction} {step:.1f} cm to centre it, then looking again.",
                    direction=direction,
                    distance_cm=round(step, 2),
                )
                self.robot.move(direction, distance_cm=round(step, 2))
                corrections.append({
                    "direction": direction,
                    "distance_cm": round(step, 2),
                    "target": chosen["label"],
                    "offset_x_cm": round(offset_x_cm, 2),
                    "offset_y_cm": round(offset_y_cm, 2),
                })
                time.sleep(0.8)

            emit(
                "pickup",
                f"Opening the gripper, descending to (2, 13, {depth:g}) cm, closing, "
                "and lifting to (0, 15, 20) while holding.",
                pickup_z=depth,
            )
            self._perform_guided_pickup(depth)
            emit("confirm", "Checking two spaced camera frames to see if it is held.")
            confirmation = self._confirm_hold(analyze, chosen["label"])
            if confirmation["held"] is True:
                verdict = (
                    f"Held: {confirmation['near_field']['label']} fills the near field "
                    "at the lift pose."
                )
            elif confirmation["held"] is None:
                verdict = f"Cannot confirm: {confirmation['reason']}. Treating it as unproven."
            else:
                verdict = f"Not held: {confirmation['reason']}. One attempt only - ask before a retry."
            emit("result", verdict, held=confirmation["held"],
                 image=confirmation.get("image"))
            return {
                # The sequence ran; retention is the camera's verdict, and it
                # is None when the stream could not prove anything.
                "grabbed": confirmation["held"] is True,
                "confirmed": confirmation["held"],
                "confirmation": confirmation,
                "target": chosen["label"],
                "target_requested": str(target) if target else None,
                "target_substituted": substituted,
                "offset_x_cm": round(offset_x_cm, 2),
                "offset_y_cm": round(offset_y_cm, 2),
                "corrections": corrections,
                "pickup": {"x": GROUND_PICKUP_XYZ[0], "y": GROUND_PICKUP_XYZ[1],
                           "z": depth, "units": "cm"},
                "finished_at": "lift pose, still holding",
                "retry_requires_authorization": True,
            }
        finally:
            self.robot.stop()
            self._lock.release()

    def grab_front(self, pickup: Any = "default") -> Dict[str, Any]:
        """Execute a recorded pickup unconditionally, without camera checks."""
        if not isinstance(pickup, str) or pickup not in ("default", "can"):
            raise ValidationError("pickup must be default or can")
        if not self._lock.acquire(blocking=False):
            raise RobotError("A grasp is already running")
        try:
            if pickup == "can":
                self._perform_can_pickup()
                mode = "recorded can pickup"
                pickup_result: Dict[str, Any] = {
                    "profile": "can",
                    "servos": {"3": 1550, "4": 1620, "5": 2500, "6": 1500},
                }
            else:
                self._perform_fixed_pickup()
                mode = "fixed ground pickup"
                pickup_result = dict(zip(("x", "y", "z"), GROUND_PICKUP_XYZ), units="cm")
            return {
                "grabbed": True,
                "mode": mode,
                "pickup": pickup_result,
                "returned_home": True,
            }
        finally:
            self.robot.stop()
            self._lock.release()

    def recognize_and_grab(self, target: Any = "any", pickup: Any = "default") -> Dict[str, Any]:
        """Grab a stable object centered under the gripper-mounted camera.

        The pickup coordinate is the operator's Check ground center. The
        function deliberately does not drive the chassis or guess depth from a
        single camera.
        """
        if not isinstance(pickup, str) or pickup not in ("default", "can"):
            raise ValidationError("pickup must be default or can")
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

            # Fixed, operator-defined ground pickup or recorded can profile.
            # Every move is bounded and the chassis remains stopped throughout.
            if pickup == "can":
                self._perform_can_pickup()
            else:
                self._perform_fixed_pickup()
            return {
                "grabbed": True,
                "detection": detection,
                "pickup": (
                    {
                        "profile": "can",
                        "servos": {"3": 1550, "4": 1620, "5": 2500, "6": 1500},
                    }
                    if pickup == "can"
                    else dict(zip(("x", "y", "z"), GROUND_PICKUP_XYZ), units="cm")
                ),
            }
        finally:
            self.robot.stop()
            self._lock.release()
