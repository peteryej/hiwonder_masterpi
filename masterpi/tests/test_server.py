import json
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from masterpi_control.backends import MockBackend
from masterpi_control.robot import Robot
from masterpi_control.server import make_handler


class FakeCamera:
    def frames(self):
        yield b"\xff\xd8camera-frame\xff\xd9"


class FakeChat:
    def __init__(self):
        self.messages = []
        self.audio = []

    def reply(self, message):
        self.messages.append(message)
        return f"hibot heard: {message}"

    def transcribe(self, audio, content_type):
        self.audio.append((audio, content_type))
        return "hello from microphone"

    def synthesize(self, text):
        return b"fake-mp3-audio", "audio/mpeg"


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.robot = Robot(MockBackend(), watchdog_timeout=0.3)
        self.camera = FakeCamera()
        self.chat = FakeChat()
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            make_handler(self.robot, self.camera, self.chat, b"test-masterpi-ca"),
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

    def raw_request(self, method, path, body, content_type):
        status, payload, _ = self.raw_request_with_type(
            method, path, body, content_type
        )
        return status, payload

    def raw_request_with_type(self, method, path, body, content_type):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        connection.request(method, path, body, {"Content-Type": content_type})
        response = connection.getresponse()
        payload = response.read()
        response_content_type = response.getheader("Content-Type")
        connection.close()
        return response.status, payload, response_content_type

    def test_control_page_and_state(self):
        status, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"MasterPi Control", page)
        self.assertIn(b'id="cameraFeed"', page)
        self.assertIn(b'id="cameraShell"', page)
        self.assertIn(b'id="toggleCamera"', page)
        self.assertIn(b"Hide camera", page)
        self.assertLess(page.find(b'id="toggleCamera"'), page.find(b'id="cameraShell"'))
        self.assertIn(b'id="distanceValue"', page)
        self.assertIn(b'id="sonarColor"', page)
        self.assertIn(b'class="control-card"', page)
        self.assertIn(b'class="control-layout"', page)
        self.assertIn(b'class="control-panel sensor-panel"', page)
        self.assertIn(b'class="control-panel drive-panel"', page)
        self.assertIn(b'class="control-panel quick-arm-panel"', page)
        self.assertIn(b'@media (max-width: 520px)', page)
        self.assertNotIn(
            b'@media (max-width: 760px)', page
        )
        self.assertNotIn(b"<h2>Chassis</h2>", page)
        self.assertNotIn(b"<h3>Ultrasonic distance</h3>", page)
        self.assertLess(page.find(b'class="pad"'), page.find(b'id="speed"'))
        self.assertLess(page.find(b'id="distanceValue"'), page.find(b'class="pad"'))
        self.assertLess(page.find(b'class="pad"'), page.find(b'id="quickHome"'))
        self.assertLess(page.find(b'id="quickClose"'), page.find(b'id="sonarColor"'))
        self.assertIn(b'id="quickHome"', page)
        self.assertIn(b'id="quickOpen"', page)
        self.assertIn(b'id="quickClose"', page)
        self.assertIn(b'id="quickNod"', page)
        self.assertIn(b'id="quickShake"', page)
        self.assertIn(b"'gesture/nod'", page)
        self.assertIn(b"'gesture/shake'", page)
        self.assertEqual(page.count(b'class="quick-arm-preset secondary"'), 2)
        self.assertNotIn(b"WonderEcho voice", page)
        self.assertNotIn(b'id="voiceDetected"', page)
        self.assertNotIn(b'id="voicePhrase"', page)
        self.assertNotIn(b'id="speakVoice"', page)
        self.assertNotIn(b"/api/voice", page)
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

    def test_ca_certificate_can_be_downloaded_for_browser_trust(self):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        connection.request("GET", "/masterpi-ca.crt")
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "application/x-x509-ca-cert")
        self.assertEqual(payload, b"test-masterpi-ca")

    def test_chat_section_is_immediately_below_chassis(self):
        status, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        chassis_end = page.find(b"</section>", page.find(b'class="control-card"'))
        chat_start = page.find(b'id="chatSection"')
        self.assertGreater(chat_start, chassis_end)
        self.assertIn(b'id="chatMessages"', page)
        self.assertIn(b'id="chatInput"', page)
        self.assertIn(b'id="recordAudio"', page)
        self.assertIn(b'aria-label="Record audio"', page)
        self.assertIn("🎤".encode(), page)
        self.assertNotIn(b'id="audioFile"', page)
        self.assertNotIn(b"capture", page)
        self.assertIn(b"getUserMedia({audio:true, video:false})", page)
        self.assertIn(b'id="secureAudioHelp"', page)
        self.assertIn(b'id="secureAudioLink"', page)
        self.assertIn(b'href="/masterpi-ca.crt"', page)
        self.assertIn(b'id="speakReplies"', page)
        self.assertIn(b'id="replyAudio" controls autoplay playsinline', page)
        self.assertIn(b"/api/chat/tts", page)
        self.assertIn(b"primeReplyAudio", page)
        self.assertIn(b"decodeAudioData", page)
        self.assertIn(b"MediaRecorder", page)

    def test_text_chat_api_returns_hibot_reply(self):
        status, payload = self.request(
            "POST", "/api/chat", {"message": "How are you?"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(payload),
            {"ok": True, "result": {"text": "hibot heard: How are you?"}},
        )
        self.assertEqual(self.chat.messages, ["How are you?"])

    def test_chat_tts_api_returns_playable_audio(self):
        status, payload, content_type = self.raw_request_with_type(
            "POST",
            "/api/chat/tts",
            json.dumps({"text": "Hello from hibot"}).encode(),
            "application/json",
        )
        self.assertEqual(status, 200)
        self.assertEqual(content_type, "audio/mpeg")
        self.assertEqual(payload, b"fake-mp3-audio")

    def test_audio_chat_api_transcribes_and_replies(self):
        status, payload = self.raw_request(
            "POST", "/api/chat/audio", b"recorded-webm", "audio/webm;codecs=opus"
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(payload),
            {
                "ok": True,
                "transcript": "hello from microphone",
                "result": {"text": "hibot heard: hello from microphone"},
            },
        )
        self.assertEqual(
            self.chat.audio, [(b"recorded-webm", "audio/webm;codecs=opus")]
        )
        self.assertEqual(self.chat.messages, ["hello from microphone"])

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

    def test_wonderecho_recognition_and_broadcast_api(self):
        self.robot.backend.recognize_voice(4)
        status, payload = self.request("GET", "/api/voice")
        self.assertEqual(status, 200)
        result = json.loads(payload)["result"]
        self.assertTrue(result["detected"])
        self.assertEqual(result["last"]["phrase"], "Turn right")

        status, payload = self.request(
            "POST", "/api/voice/speak", {"phrase": "forward"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["result"]["spoken_text"], "Going forward")
        self.assertEqual(self.robot.backend.events[-1]["action"], "voice_speak")

    def test_drive_and_stop_api(self):
        status, payload = self.request(
            "POST", "/api/drive", {"speed": 35, "direction": 90, "angular_rate": 0}
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["result"]["speed"], 35.0)
        status, _ = self.request("POST", "/api/stop", {})
        self.assertEqual(status, 200)
        self.assertEqual(self.robot.snapshot()["drive"]["speed"], 0.0)

    def test_nod_gesture_api(self):
        with patch("masterpi_control.robot.time.sleep"):
            status, payload = self.request("POST", "/api/gesture/nod", {})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["result"], {"gesture": "nod", "cycles": 2})

    def test_shake_gesture_api(self):
        with patch("masterpi_control.robot.time.sleep"):
            status, payload = self.request("POST", "/api/gesture/shake", {})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["result"], {"gesture": "shake", "cycles": 2})
        self.assertEqual(self.robot.snapshot()["arm"]["x"], 0.0)

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
