import unittest
from unittest.mock import patch

import cv2
import numpy as np

from masterpi_control.backends import MockBackend
from masterpi_control.robot import Robot, ValidationError
from masterpi_control.vision import (
    VisionGrasper,
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


class FakeCamera:
    def __init__(self, frames):
        self.frames = list(frames)
        self.index = 0

    def next_frame(self, after=0, timeout=3.0):
        frame = self.frames[min(self.index, len(self.frames) - 1)]
        self.index += 1
        return self.index, frame


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
            [(0, 6, 18), (0, 16.5, 8), (0, 16.5, 2), (0, 6, 18)],
        )
        servo_events = [event for event in robot.backend.events if event["action"] == "servo"]
        self.assertEqual([event["pulse"] for event in servo_events], [2000, 1500])

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
            [(0, 6, 18), (0, 16.5, 8), (0, 16.5, 2), (0, 6, 18)],
        )

    def test_off_center_object_does_not_move_arm(self):
        robot = Robot(MockBackend())
        self.addCleanup(robot.close)
        grasper = VisionGrasper(robot, FakeCamera([colored_jpeg(center=(100, 100))] * 3))
        result = grasper.recognize_and_grab("red")
        self.assertFalse(result["grabbed"])
        self.assertIn("not centered", result["reason"])
        self.assertFalse(any(event["action"] == "arm" for event in robot.backend.events))

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
