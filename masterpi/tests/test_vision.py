import unittest
from unittest.mock import patch

import cv2
import numpy as np

from masterpi_control.backends import MockBackend
from masterpi_control.robot import Robot, ValidationError
from masterpi_control.vision import (
    VisionGrasper,
    select_ground_target,
    annotate_object_detections,
    recognize_colored_object,
    recognize_colored_objects,
)


def colored_jpeg(color=(0, 0, 255), center=(320, 240)):
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.rectangle(
        image,
        (center[0] - 60, center[1] - 60),
        (center[0] + 60, center[1] + 60),
        color,
        -1,
    )
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    return encoded.tobytes()


def hermes_objects(center=(0.5, 0.5), size=0.4, label="stuffed toy"):
    half = size / 2
    return {
        "description": f"a {label} on the floor",
        "objects": [
            {"label": "wooden floor", "confidence": 0.98,
             "bbox": {"x_min": 0.0, "y_min": 0.0, "x_max": 1.0, "y_max": 1.0}},
            {"label": label, "confidence": 0.93,
             "bbox": {"x_min": center[0] - half, "y_min": center[1] - half,
                      "x_max": center[0] + half, "y_max": center[1] + half}},
        ],
    }


class FakeCamera:
    def __init__(self, frames):
        self.frames = list(frames)
        self.index = 0

    def next_frame(self, after=0, timeout=3.0):
        frame = self.frames[min(self.index, len(self.frames) - 1)]
        self.index += 1
        return self.index, frame


class GuidedGrabTests(unittest.TestCase):
    """The Grab object action: look, centre, pick up, confirm from the camera."""

    def setUp(self):
        self.robot = Robot(MockBackend())
        self.addCleanup(self.robot.close)
        self.frames = [b"\xff\xd8ground", b"\xff\xd8lift-1", b"\xff\xd8lift-2"]
        self.camera = FakeCamera(self.frames)
        self.grasper = VisionGrasper(self.robot, self.camera)

    def run_grab(self, analyses, **kwargs):
        calls = []

        def analyze(frame):
            calls.append(frame)
            return analyses[min(len(calls) - 1, len(analyses) - 1)]

        with patch("masterpi_control.vision.time.sleep"):
            result = self.grasper.grab_object(analyze, **kwargs)
        return result, calls

    def test_centred_object_is_picked_up_and_confirmed_from_the_near_field(self):
        held = {"description": "toy filling the view", "objects": [
            {"label": "stuffed toy", "confidence": 0.9,
             "bbox": {"x_min": 0.02, "y_min": 0.02, "x_max": 0.95, "y_max": 0.95}}]}
        self.camera.frames = [b"\xff\xd8a", b"\xff\xd8b", b"\xff\xd8c"]
        result, _ = self.run_grab([hermes_objects(), held])
        self.assertTrue(result["grabbed"])
        self.assertTrue(result["confirmed"])
        self.assertEqual(result["target"], "stuffed toy")
        self.assertEqual(result["corrections"], [])
        self.assertEqual(result["pickup"], {"x": 2.0, "y": 13.0, "z": -1.0, "units": "cm"})
        self.assertEqual(result["finished_at"], "lift pose, still holding")
        arm_events = [e for e in self.robot.backend.events if e["action"] == "arm"]
        self.assertEqual(
            [(e["x"], e["y"], e["z"]) for e in arm_events],
            [(2, 13, 8), (2, 13, -1), (0, 15, 20)],
        )

    def test_offset_object_is_centred_with_bounded_chassis_steps(self):
        # Right of centre by ~1.8 cm, then centred on the next look.
        result, _ = self.run_grab(
            [hermes_objects(center=(0.64, 0.5)), hermes_objects(), hermes_objects()]
        )
        self.assertEqual(len(result["corrections"]), 1)
        correction = result["corrections"][0]
        self.assertEqual(correction["direction"], "right")
        self.assertGreaterEqual(correction["distance_cm"], 1.0)
        self.assertLessEqual(correction["distance_cm"], 4.0)

    def test_clipped_sliver_drives_forward_then_stops_when_nothing_changes(self):
        # A small box against the top edge is a sliver of something further
        # out, so close the distance; an unchanging offset then ends the loop.
        clipped = {"description": "part of a toy", "objects": [
            {"label": "stuffed toy", "confidence": 0.9,
             "bbox": {"x_min": 0.3, "y_min": 0.0, "x_max": 0.7, "y_max": 0.38}}]}
        result, _ = self.run_grab([clipped])
        self.assertFalse(result["grabbed"])
        self.assertEqual([c["direction"] for c in result["corrections"]], ["forward"])
        self.assertIn("stopped changing", result["reason"])
        self.assertEqual([e for e in self.robot.backend.events if e["action"] == "arm"], [])

    def test_large_clipped_box_is_a_close_object_and_is_picked_up(self):
        # The can fills the ground view and touches an edge. Trusting its
        # visible centre is the whole point; forcing another approach walked
        # the robot past it in a real run.
        close = {"description": "a can right under the gripper", "objects": [
            {"label": "red beverage can", "confidence": 0.93,
             "bbox": {"x_min": 0.18, "y_min": 0.0, "x_max": 0.82, "y_max": 0.98}}]}
        held = {"description": "can filling the view", "objects": [
            {"label": "red beverage can", "confidence": 0.9,
             "bbox": {"x_min": 0.02, "y_min": 0.02, "x_max": 0.95, "y_max": 0.95}}]}
        self.camera.frames = [b"\xff\xd8a", b"\xff\xd8b", b"\xff\xd8c"]
        result, _ = self.run_grab([close, held], target="can")
        self.assertEqual(result["corrections"], [])
        self.assertTrue(result["grabbed"])

    def test_empty_ground_view_falls_through_to_check_front(self):
        floor_only = {"description": "just the floor", "objects": [
            {"label": "wooden floor", "confidence": 0.98,
             "bbox": {"x_min": 0.0, "y_min": 0.0, "x_max": 1.0, "y_max": 1.0}}]}
        # Ground empty -> Check front sees it far out -> approach -> ground pickup.
        far = hermes_objects(center=(0.5, 0.45), size=0.2)
        self.camera.frames = [b"\xff\xd8a", b"\xff\xd8b", b"\xff\xd8c", b"\xff\xd8d", b"\xff\xd8e"]
        held = {"description": "toy filling the view", "objects": [
            {"label": "stuffed toy", "confidence": 0.9,
             "bbox": {"x_min": 0.02, "y_min": 0.02, "x_max": 0.95, "y_max": 0.95}}]}
        result, _ = self.run_grab([floor_only, far, hermes_objects(), held])
        poses = [
            (event["servo_id"], event["pulse"])
            for event in self.robot.backend.events
            if event["action"] == "servo" and event["servo_id"] == 3
        ]
        # Servo 3: 500 is Check ground, 1200 is Check front. Ground, then
        # front, then back to ground before the pickup.
        self.assertEqual(poses, [(3, 500), (3, 1200), (3, 500)])
        forward = [c for c in result["corrections"] if c.get("direction") == "forward"]
        self.assertEqual(len(forward), 1)
        self.assertEqual(forward[0]["view"], "front")
        self.assertEqual(forward[0]["distance_cm"], 20.0)
        self.assertTrue(result["grabbed"])

    def test_close_object_in_front_gets_a_short_approach(self):
        floor_only = {"description": "floor", "objects": []}
        # Box clipped at the bottom edge: at or nearer than the ~23 cm
        # reference, so the full 20 cm approach would drive into it.
        close = {"description": "toy right in front", "objects": [
            {"label": "stuffed toy", "confidence": 0.9,
             "bbox": {"x_min": 0.4, "y_min": 0.55, "x_max": 0.65, "y_max": 1.0}}]}
        result, _ = self.run_grab([floor_only, close, hermes_objects(), hermes_objects()])
        forward = [c for c in result["corrections"] if c.get("direction") == "forward"]
        self.assertEqual(forward[0]["distance_cm"], 8.0)

    def test_front_view_strafes_before_approaching(self):
        floor_only = {"description": "floor", "objects": []}
        off_to_the_side = hermes_objects(center=(0.85, 0.5), size=0.2)
        result, _ = self.run_grab(
            [floor_only, off_to_the_side, hermes_objects(), hermes_objects()]
        )
        front = [c for c in result["corrections"] if c.get("view") == "front"]
        self.assertEqual(front[0]["direction"], "right")
        self.assertEqual(front[-1]["direction"], "forward")

    def test_object_in_neither_view_reports_without_moving_the_arm(self):
        floor_only = {"description": "just the floor", "objects": [
            {"label": "wooden floor", "confidence": 0.98,
             "bbox": {"x_min": 0.0, "y_min": 0.0, "x_max": 1.0, "y_max": 1.0}}]}
        result, _ = self.run_grab([floor_only])
        self.assertFalse(result["grabbed"])
        self.assertIn("ground or front view", result["reason"])
        self.assertEqual([e for e in self.robot.backend.events if e["action"] == "arm"], [])

    def test_identical_confirmation_frames_are_never_read_as_a_hold(self):
        self.camera.frames = [b"\xff\xd8ground", b"\xff\xd8same", b"\xff\xd8same"]
        result, _ = self.run_grab([hermes_objects()])
        self.assertFalse(result["grabbed"])
        self.assertIsNone(result["confirmed"])
        self.assertIn("stuck", result["confirmation"]["reason"])

    def test_empty_near_field_after_the_lift_reports_a_miss(self):
        far = {"description": "the room", "objects": [
            {"label": "chair", "confidence": 0.8,
             "bbox": {"x_min": 0.0, "y_min": 0.0, "x_max": 0.2, "y_max": 0.3}}]}
        self.camera.frames = [b"\xff\xd8a", b"\xff\xd8b", b"\xff\xd8c"]
        result, _ = self.run_grab([hermes_objects(), far])
        self.assertFalse(result["grabbed"])
        self.assertFalse(result["confirmed"])
        self.assertIn("near field", result["confirmation"]["reason"])

    def test_retry_depth_is_bounded(self):
        with self.assertRaisesRegex(ValidationError, "pickup_z"):
            self.grasper.grab_object(lambda frame: hermes_objects(), pickup_z=-5)

    def test_named_target_beats_the_largest_object(self):
        analysis = hermes_objects()
        analysis["objects"].append({
            "label": "red boot", "confidence": 0.8,
            "bbox": {"x_min": 0.45, "y_min": 0.45, "x_max": 0.6, "y_max": 0.6}})
        self.assertEqual(select_ground_target(analysis["objects"], "boot")["label"], "red boot")
        self.assertEqual(select_ground_target(analysis["objects"])["label"], "stuffed toy")


class VisionTests(unittest.TestCase):
    def test_recognizes_largest_red_object(self):
        result = recognize_colored_object(colored_jpeg())
        self.assertEqual(result["color"], "red")
        self.assertAlmostEqual(result["center_x"], 0.5, places=2)
        self.assertAlmostEqual(result["center_y"], 0.5, places=2)

    def test_rejects_unknown_target(self):
        with self.assertRaises(ValidationError):
            recognize_colored_object(colored_jpeg(), "coffee cup")

    def test_recognizes_multiple_objects_for_scene_analysis(self):
        image = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.rectangle(image, (60, 160), (180, 280), (0, 0, 255), -1)
        cv2.rectangle(image, (420, 160), (540, 280), (255, 0, 0), -1)
        ok, encoded = cv2.imencode(".jpg", image)
        self.assertTrue(ok)
        detections = recognize_colored_objects(encoded.tobytes())
        self.assertEqual({item["color"] for item in detections}, {"red", "blue"})

    def test_scene_analysis_requires_stable_objects(self):
        black = colored_jpeg(color=(0, 0, 0))
        camera = FakeCamera([colored_jpeg(), colored_jpeg(), black])
        robot = Robot(MockBackend())
        self.addCleanup(robot.close)
        result = VisionGrasper(robot, camera).analyze_scene()
        self.assertEqual(
            result["objects"],
            [{"label": "red object", "color": "red", "count": 1}],
        )
        self.assertEqual(result["object_count"], 1)

    def test_annotates_hermes_bounding_box(self):
        source = colored_jpeg(color=(0, 0, 0))
        annotated = annotate_object_detections(
            source,
            [
                {
                    "label": "bottle",
                    "confidence": 0.9,
                    "bbox": {"x_min": 0.2, "y_min": 0.2, "x_max": 0.8, "y_max": 0.8},
                }
            ],
        )
        original_image = cv2.imdecode(np.frombuffer(source, np.uint8), cv2.IMREAD_COLOR)
        annotated_image = cv2.imdecode(np.frombuffer(annotated, np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(annotated_image.shape, original_image.shape)
        self.assertGreater(np.count_nonzero(annotated_image), 0)

    def test_centered_stable_object_runs_fixed_pickup(self):
        robot = Robot(MockBackend())
        self.addCleanup(robot.close)
        grasper = VisionGrasper(robot, FakeCamera([colored_jpeg()] * 3))
        with patch("masterpi_control.vision.time.sleep"):
            result = grasper.recognize_and_grab("red")
        self.assertTrue(result["grabbed"])
        arm_events = [event for event in robot.backend.events if event["action"] == "arm"]
        self.assertEqual(
            [(event["x"], event["y"], event["z"]) for event in arm_events],
            [(2, 13, 8), (2, 13, -1), (0, 6, 18)],
        )
        self.assertEqual(arm_events[1]["pitch"], -68)
        self.assertEqual((arm_events[1]["pitch_min"], arm_events[1]["pitch_max"]), (-90, -68))
        servo_events = [event for event in robot.backend.events if event["action"] == "servo"]
        self.assertEqual([event["pulse"] for event in servo_events], [2500, 500])
        self.assertEqual(result["pickup"], {"x": 2.0, "y": 13.0, "z": -1.0, "units": "cm"})

    def test_unconditional_front_grab_skips_camera_and_returns_home(self):
        robot = Robot(MockBackend())
        self.addCleanup(robot.close)
        camera = FakeCamera([])
        grasper = VisionGrasper(robot, camera)
        with patch("masterpi_control.vision.time.sleep"):
            result = grasper.grab_front()
        self.assertTrue(result["grabbed"])
        self.assertTrue(result["returned_home"])
        self.assertEqual(camera.index, 0)
        arm_events = [event for event in robot.backend.events if event["action"] == "arm"]
        self.assertEqual(
            [(event["x"], event["y"], event["z"]) for event in arm_events],
            [(2, 13, 8), (2, 13, -1), (0, 6, 18)],
        )
        self.assertEqual(arm_events[1]["pitch"], -68)
        self.assertEqual(result["pickup"], {"x": 2.0, "y": 13.0, "z": -1.0, "units": "cm"})

    def test_centered_can_uses_recorded_direct_servo_pickup_pose(self):
        robot = Robot(MockBackend())
        self.addCleanup(robot.close)
        yellow = colored_jpeg(color=(0, 255, 255))
        grasper = VisionGrasper(robot, FakeCamera([yellow] * 3))
        with patch("masterpi_control.vision.time.sleep"):
            result = grasper.recognize_and_grab("yellow", pickup="can")
        self.assertTrue(result["grabbed"])
        servo_events = [event for event in robot.backend.events if event["action"] == "servo"]
        self.assertEqual(
            [(event["servo_id"], event["pulse"]) for event in servo_events],
            [(1, 2500), (3, 1550), (4, 1620), (5, 2500), (6, 1500), (1, 500)],
        )
        arm_events = [event for event in robot.backend.events if event["action"] == "arm"]
        self.assertEqual(
            [(event["x"], event["y"], event["z"]) for event in arm_events],
            [(0, 6, 18), (0, 6, 18)],
        )

    def test_off_center_object_does_not_move_arm(self):
        robot = Robot(MockBackend())
        self.addCleanup(robot.close)
        grasper = VisionGrasper(robot, FakeCamera([colored_jpeg(center=(100, 100))] * 3))
        result = grasper.recognize_and_grab("red")
        self.assertFalse(result["grabbed"])
        self.assertIn("not centered", result["reason"])
        self.assertFalse(any(event["action"] == "arm" for event in robot.backend.events))

    def test_one_segmentation_outlier_does_not_mark_stationary_object_unstable(self):
        robot = Robot(MockBackend())
        self.addCleanup(robot.close)
        frames = [
            colored_jpeg(center=(320, 240)),
            colored_jpeg(center=(324, 242)),
            colored_jpeg(center=(500, 350)),
        ]
        result = VisionGrasper(robot, FakeCamera(frames)).recognize("red", samples=3)
        self.assertTrue(result["recognized"])
        self.assertEqual(result["matches"], 2)
        self.assertLess(result["center_x"], 0.55)

    def test_moving_object_does_not_move_arm(self):
        robot = Robot(MockBackend())
        self.addCleanup(robot.close)
        frames = [
            colored_jpeg(center=(280, 240)),
            colored_jpeg(center=(320, 240)),
            colored_jpeg(center=(360, 240)),
        ]
        result = VisionGrasper(robot, FakeCamera(frames)).recognize_and_grab("red")
        self.assertFalse(result["grabbed"])
        self.assertEqual(result["reason"], "object moved between camera frames")
        self.assertFalse(any(event["action"] == "arm" for event in robot.backend.events))

    def test_missing_object_does_not_move_arm(self):
        robot = Robot(MockBackend())
        self.addCleanup(robot.close)
        black = colored_jpeg(color=(0, 0, 0))
        result = VisionGrasper(robot, FakeCamera([black] * 3)).recognize_and_grab()
        self.assertFalse(result["grabbed"])
        self.assertFalse(result["recognized"])
        self.assertFalse(any(event["action"] == "arm" for event in robot.backend.events))


if __name__ == "__main__":
    unittest.main()
