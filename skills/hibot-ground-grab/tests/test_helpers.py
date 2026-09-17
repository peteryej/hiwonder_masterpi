"""Hardware-free checks for the skill's deterministic helpers."""

import importlib.util
import io
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
from pathlib import Path


def load(name):
    path = Path(__file__).resolve().parents[1] / "scripts" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


observe = load("observe")
grab = load("grab_ground")


class Response(io.BytesIO):
    headers = {"Content-Type": "multipart/x-mixed-replace; boundary=frame"}

    def read(self, size=-1):
        return super().read(min(size, 2))


class HelperTests(unittest.TestCase):
    def test_capture_reads_fragmented_frame_and_only_stream(self):
        jpeg = b"\xff\xd8test\xff\xd9"
        stream = b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 8\r\n\r\n" + jpeg
        calls = []

        def opener(url, timeout):
            calls.append((url, timeout))
            return Response(stream)

        self.assertEqual(observe.capture(opener), jpeg)
        self.assertEqual(calls, [("http://127.0.0.1:8000/api/camera/stream", 10)])

    def test_incomplete_capture_fails(self):
        response = Response(b"Content-Length: 8\r\n\r\n\xff\xd8")
        with self.assertRaisesRegex(RuntimeError, "Incomplete"):
            observe.capture(lambda *args, **kwargs: response)

    def test_exact_pickup_then_close_then_lift_without_home(self):
        calls = []
        waits = []
        output = io.StringIO()
        confirmation = {"images": ["/tmp/one.jpg", "/tmp/two.jpg"], "stale_stream": False}
        with redirect_stdout(output):
            result = grab.execute(
                lambda a, p: calls.append((a, p)) or {}, waits.append,
                lambda: confirmation,
            )
        self.assertEqual([a for a, _ in calls], ["stop", "gripper", "arm", "gripper", "arm", "stop"])
        self.assertEqual([calls[2][1][k] for k in ("x", "y", "z")], [2, 13, -1.0])
        self.assertEqual([calls[2][1][k] for k in ("pitch", "pitch_min", "pitch_max")], [-68, -90, -68])
        self.assertFalse(calls[3][1]["opened"])
        self.assertEqual([calls[4][1][k] for k in ("x", "y", "z")], [0, 15, 20])
        self.assertEqual(waits, [0.5, 1.6, 0.6, 1.6])
        # The lift must be confirmed from the camera, after the final Stop.
        self.assertEqual(result, confirmation)
        self.assertIn('"stale_stream": false', output.getvalue())
        self.assertIn("/tmp/two.jpg", output.getvalue())

    def test_confirmation_capture_failure_still_reports_the_pickup(self):
        def capture():
            raise RuntimeError("camera stream closed")

        output = io.StringIO()
        with redirect_stdout(output):
            result = grab.execute(lambda a, p: {}, lambda _: None, capture)
        self.assertIsNone(result)
        self.assertIn('"confirmation_error": "camera stream closed"', output.getvalue())

    def test_failed_pickup_never_captures_a_confirmation(self):
        def call(action, payload):
            if action == "arm":
                raise RuntimeError("unreachable")
            return {}

        captures = []
        with redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
            grab.execute(call, lambda _: None, lambda: captures.append(1))
        self.assertEqual(captures, [])

    def test_failed_pickup_never_closes_or_lifts(self):
        calls = []

        def call(action, payload):
            calls.append((action, payload))
            if action == "arm":
                raise RuntimeError("unreachable")
            return {}

        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, "unreachable"):
            grab.execute(call, lambda _: None, lambda: None)
        self.assertEqual([a for a, _ in calls], ["stop", "gripper", "arm", "stop"])
        self.assertTrue(calls[1][1]["opened"])

    def test_confirmation_captures_two_spaced_frames(self):
        frames = [b"first", b"second"]
        waits = []
        result = grab.capture_after_lift(lambda: frames.pop(0), waits.append, 1.5)
        self.assertEqual(len(result["images"]), 2)
        self.assertFalse(result["stale_stream"])
        # The pair is useless unless the second capture happens after a wait.
        self.assertEqual(waits, [1.5])
        self.assertEqual(
            [Path(image).read_bytes() for image in result["images"]], [b"first", b"second"]
        )

    def test_identical_frames_are_reported_as_a_stuck_stream(self):
        result = grab.capture_after_lift(lambda: b"same", lambda _: None, 0)
        self.assertTrue(result["stale_stream"])

    def test_retry_can_lower_the_pickup_without_touching_anything_else(self):
        calls = []
        with redirect_stdout(io.StringIO()):
            grab.execute(
                lambda a, p: calls.append((a, p)) or {}, lambda _: None,
                lambda: {"images": [], "stale_stream": False}, pickup_z=-2,
            )
        pickup, lift = [p for a, p in calls if a == "arm"]
        self.assertEqual([pickup[k] for k in ("x", "y", "z")], [2, 13, -2])
        self.assertEqual([pickup[k] for k in ("pitch", "pitch_min", "pitch_max")], [-68, -90, -68])
        # Only the depth moves: the lift finish and the approach stay put.
        self.assertEqual([lift[k] for k in ("x", "y", "z")], [0, 15, 20])

    def test_pickup_depth_outside_the_safe_band_is_refused(self):
        for depth in (-4, 3):
            with self.subTest(depth=depth), self.assertRaisesRegex(ValueError, "pickup_z"):
                grab.plan(depth)

    def test_default_invocation_is_dry_run(self):
        with patch("sys.argv", ["grab_ground.py"]), patch.object(grab, "execute") as execute:
            output = io.StringIO()
            with redirect_stdout(output):
                grab.main()
            execute.assert_not_called()
            self.assertIn('"executed": false', output.getvalue())

    def test_failed_stop_preserves_original_failed_stage(self):
        def call(action, payload):
            if action == "arm":
                raise RuntimeError("unreachable")
            if action == "stop" and calls:
                raise RuntimeError("controller offline")
            calls.append(action)
            return {}

        calls = []
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(
            RuntimeError, "failed at arm.*unreachable.*Stop also failed"
        ):
            grab.execute(call, lambda _: None, lambda: None)


if __name__ == "__main__":
    unittest.main()
