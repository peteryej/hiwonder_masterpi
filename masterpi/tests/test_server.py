import json
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

from masterpi_control.backends import MockBackend
from masterpi_control.robot import Robot
from masterpi_control.server import make_handler


class FakeCamera:
    def frames(self):
        yield b"\xff\xd8camera-frame\xff\xd9"


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.robot = Robot(MockBackend(), watchdog_timeout=0.3)
        self.camera = FakeCamera()
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), make_handler(self.robot, self.camera)
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)
        self.robot.close()

    def request(self, method, path, body=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        encoded = None if body is None else json.dumps(body)
        headers = {} if body is None else {"Content-Type": "application/json"}
        connection.request(method, path, encoded, headers)
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        return response.status, payload

    def test_control_page_and_state(self):
        status, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"MasterPi Control", page)
        self.assertIn(b'id="cameraFeed"', page)
        self.assertIn(b'id="cameraShell"', page)
        self.assertIn(b'id="toggleCamera"', page)
        self.assertIn(b"Hide camera", page)
        self.assertIn(b'id="distanceValue"', page)
        self.assertIn(b'id="sonarColor"', page)
        self.assertNotIn(b"Reconnect camera", page)
        self.assertNotIn(b"Direct servo control", page)
        self.assertNotIn(b'id="setServo"', page)
        self.assertIn(b"Target position of the gripper tip", page)
        self.assertIn(b"positive moves right", page)
        self.assertNotIn(b"Accepted limits:", page)
        self.assertGreater(page.find(b"Arm position (cm)"), page.find(b"LEDs and buzzer"))
        self.assertIn(b'id="armStatus"', page)
        self.assertEqual(page.count(b'class="arm-preset secondary"'), 4)
        status, payload = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(payload)["ok"])

    def test_camera_stream_is_multipart_jpeg(self):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        connection.request("GET", "/api/camera/stream")
        response = connection.getresponse()
        content_type = response.getheader("Content-Type")
        payload = response.read()
        connection.close()
        self.assertEqual(response.status, 200)
        self.assertEqual(content_type, "multipart/x-mixed-replace; boundary=frame")
        self.assertIn(b"Content-Type: image/jpeg", payload)
        self.assertIn(b"\xff\xd8camera-frame\xff\xd9", payload)

    def test_ultrasonic_distance_api(self):
        self.robot.backend.mock_distance_mm = 615
        status, payload = self.request("GET", "/api/distance")
        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(payload)["result"],
            {"millimeters": 615, "centimeters": 61.5},
        )

    def test_ultrasonic_rgb_api(self):
        status, payload = self.request(
            "POST", "/api/sonar/rgb", {"red": 12, "green": 34, "blue": 56}
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(payload)["result"],
            {"red": 12, "green": 34, "blue": 56},
        )
        self.assertEqual(self.robot.backend.events[-1]["action"], "sonar_rgb")

    def test_drive_and_stop_api(self):
        status, payload = self.request(
            "POST", "/api/drive", {"speed": 35, "direction": 90, "angular_rate": 0}
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["result"]["speed"], 35.0)
        status, _ = self.request("POST", "/api/stop", {})
        self.assertEqual(status, 200)
        self.assertEqual(self.robot.snapshot()["drive"]["speed"], 0.0)

    def test_validation_error_is_400(self):
        status, payload = self.request(
            "POST", "/api/drive", {"speed": 999, "direction": 90, "angular_rate": 0}
        )
        self.assertEqual(status, 400)
        self.assertFalse(json.loads(payload)["ok"])

    def test_unexpected_hardware_error_is_json_500(self):
        def fail_arm(**command):
            raise AttributeError("hardware integration failed")

        self.robot.backend.arm = fail_arm
        status, payload = self.request(
            "POST", "/api/arm", {"x": 0, "y": 6, "z": 18}
        )
        self.assertEqual(status, 500)
        self.assertEqual(
            json.loads(payload),
            {"ok": False, "error": "hardware integration failed"},
        )

    def test_unreachable_arm_position_is_json_422(self):
        def unreachable(**command):
            raise ValueError("The requested arm position is not reachable")

        self.robot.backend.arm = unreachable
        status, payload = self.request(
            "POST", "/api/arm", {"x": 40, "y": 40, "z": 40, "pitch": 0}
        )
        self.assertEqual(status, 422)
        self.assertEqual(
            json.loads(payload),
            {"ok": False, "error": "The requested arm position is not reachable"},
        )


if __name__ == "__main__":
    unittest.main()
