import unittest

from masterpi_control.can_grab import plan_or_grab_can


def semantic_scene(objects):
    return {
        "ok": True,
        "result": {
            "description": "A beverage can is in front of the robot.",
            "objects": objects,
            "object_count": len(objects),
            "provider": "test",
            "model": "test",
        },
    }


CENTERED_CAN = {
    "label": "beverage can",
    "confidence": 0.94,
    "bbox": {"x_min": 0.35, "y_min": 0.25, "x_max": 0.65, "y_max": 0.75},
}


class CanGrabProgramTests(unittest.TestCase):
    def test_dry_run_uses_semantic_image_analysis_without_grabbing(self):
        calls = []

        def post(path, payload):
            calls.append((path, payload))
            return semantic_scene([CENTERED_CAN])

        result = plan_or_grab_can("http://127.0.0.1:8000", execute=False, post=post)

        self.assertEqual(calls, [("/api/camera/analyze", {})])
        self.assertTrue(result["ready"])
        self.assertFalse(result["executed"])
        self.assertEqual(result["target"]["label"], "beverage can")

    def test_execute_uses_semantic_guard_then_recorded_can_pickup(self):
        calls = []

        def post(path, payload):
            calls.append((path, payload))
            if path == "/api/camera/analyze":
                return semantic_scene([CENTERED_CAN])
            return {"ok": True, "result": {"grabbed": True, "returned_home": True}}

        result = plan_or_grab_can("http://127.0.0.1:8000", execute=True, post=post)

        self.assertEqual(
            calls,
            [
                ("/api/camera/analyze", {}),
                ("/api/grab", {"force": True, "pickup": "can"}),
            ],
        )
        self.assertTrue(result["grabbed"])

    def test_execute_refuses_when_image_analysis_finds_no_can(self):
        calls = []

        def post(path, payload):
            calls.append((path, payload))
            return semantic_scene([])

        result = plan_or_grab_can("http://127.0.0.1:8000", execute=True, post=post)

        self.assertEqual(calls, [("/api/camera/analyze", {})])
        self.assertFalse(result["grabbed"])
        self.assertFalse(result["executed"])
        self.assertIn("can", result["reason"])

    def test_execute_refuses_when_can_is_outside_general_center(self):
        calls = []
        off_center = {
            "label": "can",
            "confidence": 0.95,
            "bbox": {"x_min": 0.0, "y_min": 0.2, "x_max": 0.2, "y_max": 0.7},
        }

        def post(path, payload):
            calls.append((path, payload))
            return semantic_scene([off_center])

        result = plan_or_grab_can("http://127.0.0.1:8000", execute=True, post=post)

        self.assertEqual(calls, [("/api/camera/analyze", {})])
        self.assertFalse(result["grabbed"])
        self.assertFalse(result["executed"])
        self.assertIn("center", result["reason"])


if __name__ == "__main__":
    unittest.main()
