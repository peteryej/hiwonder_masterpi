import json
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from masterpi_control.backends import MockBackend
from masterpi_control.robot import Robot, RobotError
from masterpi_control.server import (
    DanceBusy,
    DanceLauncher,
    DanceUnavailable,
    make_handler,
)
from masterpi_control.voice_status import VoiceStatusWriter


class FakeCamera:
    def __init__(self):
        image = np.zeros((120, 160, 3), dtype=np.uint8)
        cv2.rectangle(image, (32, 12), (96, 108), (255, 255, 255), -1)
        ok, encoded = cv2.imencode(".jpg", image)
        assert ok
        self.analysis_frame = encoded.tobytes()
        self.sequence = 0

    def frames(self):
        yield b"\xff\xd8camera-frame\xff\xd9"

    def next_frame(self, after=0, timeout=3.0):
        self.sequence += 1
        return self.sequence, self.analysis_frame


class FakeChat:
    def __init__(self):
        self.messages = []
        self.audio = []
        self.images = []
        self.sessions = []

    def reply(self, message):
        self.messages.append(message)
        return f"hibot heard: {message}"

    def new_session(self):
        self.sessions.append(len(self.sessions))
        return f"fake-session-{len(self.sessions)}"

    def transcribe(self, audio, content_type):
        self.audio.append((audio, content_type))
        return "hello from microphone"

    def synthesize(self, text):
        return b"fake-mp3-audio", "audio/mpeg"

    def analyze_image(self, jpeg):
        self.images.append(jpeg)
        return {
            "description": "A bottle is in front of the robot.",
            "objects": [
                {
                    "label": "bottle",
                    "confidence": 0.92,
                    "bbox": {"x_min": 0.2, "y_min": 0.1, "x_max": 0.6, "y_max": 0.9},
                }
            ],
            "object_count": 1,
            "provider": "openai-codex",
            "model": "gpt-5.6-terra",
        }


class FakeVisionGrasper:
    def __init__(self):
        self.targets = []
        self.analysis_calls = []
        self.front_pickups = []
        self.guided_calls = []

    def recognize_and_grab(self, target, pickup="default"):
        self.targets.append(target)
        return {"grabbed": True, "detection": {"color": target}, "pickup": pickup}

    def grab_front(self, pickup="default"):
        self.front_pickups.append(pickup)
        mode = "recorded can pickup" if pickup == "can" else "fixed ground pickup"
        return {"grabbed": True, "mode": mode, "returned_home": True, "pickup": pickup}

    def grab_object(self, analyze, target=None, pickup_z=-1.0, on_event=None):
        self.guided_calls.append({"target": target, "pickup_z": pickup_z})
        if on_event is not None:
            on_event({"stage": "observe", "text": "Saw stuffed toy at +0.3 cm across",
                      "image": "data:image/jpeg;base64,AAA"})
            on_event({"stage": "correct", "text": "Moving right 1.0 cm to centre it",
                      "direction": "right", "distance_cm": 1.0})
        return {
            "grabbed": True,
            "confirmed": True,
            "target": target or "stuffed toy",
            "pickup": {"x": 2.0, "y": 13.0, "z": pickup_z, "units": "cm"},
            "finished_at": "lift pose, still holding",
        }

    def analyze_scene(self, samples=3):
        self.analysis_calls.append(samples)
        return {
            "objects": [
                {"label": "red object", "color": "red", "count": 1},
                {"label": "blue object", "color": "blue", "count": 2},
            ],
            "object_count": 3,
            "samples": samples,
            "recognizer": "color regions",
        }


class FakeSoundTracker:
    def __init__(self):
        self.direction_calls = []
        self.come_here_calls = []

    def direction(self, samples=5):
        self.direction_calls.append(samples)
        return {"relative_angle": 42.0, "samples": samples, "voice_active": True}

    def come_here(self, approach_duration=2.0, clearance_cm=45, samples=5):
        self.come_here_calls.append((approach_duration, clearance_cm, samples))
        return {
            "mode": "sound_source_approach",
            "bearing": {"relative_angle": 42.0},
            "motion": {"stopped_for_obstacle": True},
        }


class FakeDanceLauncher:
    def __init__(self):
        self.urls = []

    def start(self, controller_url, *, wait_for_audio=False):
        self.urls.append((controller_url, wait_for_audio))
        return {
            "started": True,
            "pid": 4321,
            "audio": True,
            "wait_for_audio": wait_for_audio,
            "duration_seconds": 34.6,
        }


class FakeDanceProcess:
    pid = 2468

    def __init__(self):
        self.done = threading.Event()
        self.status = 0
        self.terminated = False

    def poll(self):
        return self.status if self.done.is_set() else None

    def wait(self, timeout=None):
        if not self.done.wait(timeout):
            raise TimeoutError("fake dance still running")
        return self.status

    def terminate(self):
        self.terminated = True
        self.status = 130
        self.done.set()


class DanceLauncherTests(unittest.TestCase):
    def test_launcher_runs_execute_mode_and_rejects_overlap(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "dance.py"
            script.write_text("# test dance\n")
            process = FakeDanceProcess()
            popen = unittest.mock.MagicMock(return_value=process)
            launcher = DanceLauncher(script, python="/test/python", popen=popen)

            result = launcher.start("http://127.0.0.1:8000")
            self.assertTrue(result["started"])
            self.assertTrue(result["audio"])
            popen.assert_called_once_with(
                [
                    "/test/python",
                    str(script),
                    "--execute",
                    "--url",
                    "http://127.0.0.1:8000",
                ],
                cwd=directory,
            )
            with self.assertRaises(DanceBusy):
                launcher.start("http://127.0.0.1:8000")
            launcher.stop()
            self.assertTrue(process.terminated)

    def test_launcher_rejects_missing_script_before_spawning(self):
        launcher = DanceLauncher(Path("/missing/dance.py"))
        with self.assertRaises(DanceUnavailable):
            launcher.start("http://127.0.0.1:8000")

    def test_agent_launcher_enables_audio_wait_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "dance.py"
            script.write_text("# test dance\n")
            process = FakeDanceProcess()
            popen = unittest.mock.MagicMock(return_value=process)
            launcher = DanceLauncher(script, python="/test/python", popen=popen)

            result = launcher.start(
                "http://127.0.0.1:8000", wait_for_audio=True
            )

            self.assertTrue(result["wait_for_audio"])
            self.assertEqual(popen.call_args.args[0][-1], "--wait-for-audio")
            launcher.stop()


class ServerTests(unittest.TestCase):
    def test_manual_and_mcp_gripper_routes_use_full_range_presets(self):
        for route in ("/api/gripper", "/api/agent/gripper"):
            for opened, pulse in ((True, 2500), (False, 500)):
                with self.subTest(route=route, opened=opened):
                    status, body = self.request("POST", route, {"opened": opened})
                    self.assertEqual(status, 200)
                    body = json.loads(body)
                    self.assertEqual(body["result"]["servo_id"], 1)
                    self.assertEqual(body["result"]["pulse"], pulse)

    def setUp(self):
        self.robot = Robot(MockBackend(), watchdog_timeout=0.3)
        self.camera = FakeCamera()
        self.chat = FakeChat()
        self.vision_grasper = FakeVisionGrasper()
        self.sound_tracker = FakeSoundTracker()
        self.dance_launcher = FakeDanceLauncher()
        self.temp_directory = tempfile.TemporaryDirectory()
        self.voice_status_path = Path(self.temp_directory.name) / "voice.json"
        self.voice_status = VoiceStatusWriter(self.voice_status_path)
        self.voice_status.begin("test-voice-session")
        self.voice_status.message("user", "What can you do?")
        self.voice_status.step(
            "thinking", "Hermes is thinking…", tool="Hermes Agent · safe toolset"
        )
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            make_handler(
                self.robot,
                self.camera,
                self.chat,
                b"test-masterpi-ca",
                self.vision_grasper,
                self.sound_tracker,
                self.voice_status_path,
                self.dance_launcher,
                "http://127.0.0.1:8000",
            ),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)
        self.robot.close()
        self.temp_directory.cleanup()

    def request(self, method, path, body=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        encoded = None if body is None else json.dumps(body)
        headers = {} if body is None else {"Content-Type": "application/json"}
        connection.request(method, path, encoded, headers)
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        return response.status, payload

    def request_raw(self, method, path, body=None):
        """POST and return the raw body plus content type, for NDJSON streams."""
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        encoded = None if body is None else json.dumps(body)
        headers = {} if body is None else {"Content-Type": "application/json"}
        connection.request(method, path, encoded, headers)
        response = connection.getresponse()
        payload = response.read().decode("utf-8")
        content_type = response.headers.get("Content-Type")
        connection.close()
        return response.status, payload, content_type

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
        self.assertIn(b'id="sonarColor" type="color" value="#000000"', page)
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
        for direction, arrow in ((0, "◀"), (180, "▶"), (45, "↖"), (135, "↗"), (315, "↙"), (225, "↘")):
            self.assertIn(f'data-direction="{direction}">{arrow}</button>'.encode(), page)
        self.assertIn(b'a:drive(0), ArrowLeft:drive(0)', page)
        self.assertIn(b'd:drive(180), ArrowRight:drive(180)', page)
        self.assertLess(page.find(b'id="quickClose"'), page.find(b'id="sonarColor"'))
        self.assertIn(b'id="quickHome"', page)
        self.assertIn(b'id="quickCheckFront"', page)
        self.assertEqual(page.count(b'id="quickCheckFront"'), 1)
        self.assertIn(b'id="quickCheckGround"', page)
        self.assertEqual(page.count(b'id="quickCheckGround"'), 1)
        # Grab object runs the guarded skill flow; the blind fixed-point
        # pickup keeps a button but is labelled as the quick action it is.
        self.assertIn(b'id="quickGrabObject"', page)
        self.assertIn(b'>Grab object</button>', page)
        self.assertIn(b'id="quickGrab"', page)
        self.assertIn(b'>Quick grab action</button>', page)
        self.assertNotIn(b'id="quickGrabCan"', page)
        self.assertNotIn(b'>Grab can</button>', page)
        self.assertIn(b'id="quickOpen"', page)
        self.assertIn(b'id="quickClose"', page)
        self.assertIn(b"document.querySelector('#quickOpen').onclick = () => api('gripper', {opened:true})", page)
        self.assertIn(b"document.querySelector('#quickClose').onclick = () => api('gripper', {opened:false})", page)
        self.assertIn(b"setServoSliderValue(1, opened ? 2500 : 500)", page)
        self.assertIn(b'id="quickNod"', page)
        self.assertIn(b'id="quickShake"', page)
        self.assertIn(b'id="quickDance"', page)
        self.assertIn(b"'gesture/nod'", page)
        self.assertIn(b"'gesture/shake'", page)
        self.assertIn(b"api('dance')", page)
        self.assertIn(b"api('grab', {force:true})", page)
        self.assertIn(b"'/api/grab/object/stream'", page)
        self.assertIn(b"addChatMessage(event.stage === 'error'", page)
        self.assertNotIn(b"pickup:'can'", page)
        self.assertIn(b"'pose/check_front'", page)
        self.assertIn(b"'pose/check_ground'", page)
        self.assertIn(b"arm returned Home", page)
        self.assertEqual(page.count(b'class="quick-arm-preset secondary"'), 0)
        self.assertNotIn(b"WonderEcho voice", page)
        self.assertNotIn(b'id="voiceDetected"', page)
        self.assertNotIn(b'id="voicePhrase"', page)
        self.assertNotIn(b'id="speakVoice"', page)
        self.assertNotIn(b"/api/voice/speak", page)
        self.assertNotIn(b"Reconnect camera", page)
        self.assertIn(b"Direct servo control", page)
        self.assertEqual(page.count(b'class="servo-slider"'), 5)
        for servo_id in (1, 3, 4, 5, 6):
            self.assertIn(f'id="servo{servo_id}"'.encode(), page)
        self.assertNotIn(b'id="servo2"', page)
        self.assertNotIn(b"Arm servo", page)
        self.assertLess(page.find(b"Servo 1 (gripper)"), page.find(b"Servo 3 (top)"))
        self.assertLess(page.find(b"Servo 3 (top)"), page.find(b"Servo 4"))
        self.assertLess(page.find(b"Servo 4"), page.find(b"Servo 5"))
        self.assertLess(page.find(b"Servo 5"), page.find(b"Servo 6 (base)"))
        self.assertIn(b"api('servo'", page)
        self.assertIn(b"Target position of the gripper tip", page)
        self.assertIn(b"positive moves right", page)
        self.assertIn(b'class="arm-jog-controls"', page)
        self.assertEqual(page.count(b'class="arm-jog secondary"'), 8)
        for control_id in (
            "armYUp",
            "armYDown",
            "armXLeft",
            "armXRight",
            "armZUp",
            "armZDown",
            "armGripClose",
            "armGripOpen",
        ):
            self.assertIn(f'id="{control_id}"'.encode(), page)
        self.assertIn(b"jogAxis('y', 0.5)", page)
        self.assertIn(b"jogAxis('x', -0.5)", page)
        self.assertIn(b"jogAxis('z', 0.5)", page)
        self.assertIn(b"jogGripper(false)", page)
        self.assertIn(b"jogGripper(true)", page)
        self.assertNotIn(b"Accepted limits:", page)
        self.assertGreater(page.find(b"Arm position (cm)"), page.find(b"LEDs and buzzer"))
        self.assertIn(b'id="armStatus"', page)
        self.assertEqual(page.count(b'class="arm-preset secondary"'), 0)
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
        self.assertIn(b'id="newChatSession"', page)
        self.assertIn(b">New chat</button>", page)
        self.assertIn(b"api('chat/new', {})", page)
        self.assertIn(b"/api/chat/tts", page)
        self.assertIn(b"primeReplyAudio", page)
        self.assertIn(b"decodeAudioData", page)
        self.assertIn(b"MediaRecorder", page)
        self.assertIn(b"body.result.action", page)
        self.assertIn(b"Action completed:", page)
        self.assertIn(b"body.result.vision?.annotated_image", page)
        self.assertIn(b"chat-vision-image", page)
        self.assertIn(b"/api/voice/conversation", page)
        self.assertIn(b"hibot is thinking", page)
        self.assertIn(b"Tool:", page)
        self.assertIn(b"thinking-dots", page)

    def test_voice_conversation_status_api_returns_transcript_and_thinking_stage(self):
        status, payload = self.request("GET", "/api/voice/conversation")

        self.assertEqual(status, 200)
        body = json.loads(payload)
        self.assertTrue(body["ok"])
        conversation = body["conversation"]
        self.assertEqual(conversation["session"], "test-voice-session")
        self.assertEqual(conversation["state"], "thinking")
        self.assertEqual(conversation["events"][-2]["text"], "What can you do?")
        self.assertEqual(conversation["events"][-1]["tool"], "Hermes Agent · safe toolset")

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

    def test_chat_camera_question_analyzes_live_frames(self):
        with patch("masterpi_control.server.time.sleep"):
            status, payload = self.request("POST", "/api/chat", {"message": "What do you see?"})
        self.assertEqual(status, 200)
        result = json.loads(payload)["result"]
        self.assertEqual(result["text"], "A bottle is in front of the robot.")
        self.assertEqual(result["vision"]["object_count"], 1)
        self.assertEqual(result["vision"]["objects"][0]["label"], "bottle")
        self.assertTrue(result["vision"]["annotated_image"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(self.vision_grasper.analysis_calls, [])
        self.assertEqual(self.chat.images, [self.camera.analysis_frame])
        self.assertEqual(self.chat.messages, [])
        servo_events = [event for event in self.robot.backend.events if event["action"] == "servo"]
        self.assertEqual(
            [(event["servo_id"], event["pulse"]) for event in servo_events],
            [(3, 1200), (4, 2500), (5, 1500), (6, 1500), (1, 2200)],
        )

    def test_explicit_color_detection_uses_local_analyzer_without_pose(self):
        before = len(self.robot.backend.events)
        status, payload = self.request(
            "POST", "/api/chat", {"message": "Use color detection; what do you see?"}
        )
        self.assertEqual(status, 200)
        result = json.loads(payload)["result"]
        self.assertEqual(result["text"], "I can see a red object and 2 blue objects.")
        self.assertEqual(self.vision_grasper.analysis_calls, [3])
        self.assertEqual(self.chat.images, [])
        self.assertEqual(len(self.robot.backend.events), before)

    def test_color_analysis_api_is_read_only(self):
        before = len(self.robot.backend.events)
        status, payload = self.request(
            "POST", "/api/camera/analyze/color", {"samples": 3}
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["result"]["object_count"], 3)
        self.assertEqual(len(self.robot.backend.events), before)

    def test_chat_request_can_run_the_nod_gesture(self):
        with patch("masterpi_control.robot.time.sleep"):
            status, payload = self.request("POST", "/api/chat", {"message": "nod if you understand"})
        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(payload),
            {
                "ok": True,
                "result": {
                    "text": "hibot heard: nod if you understand",
                    "action": {"name": "nod", "result": {"gesture": "nod", "cycles": 1}},
                },
            },
        )
        self.assertEqual(self.chat.messages, ["nod if you understand"])
        self.assertEqual(
            [(event["servo_id"], event["pulse"]) for event in self.robot.backend.events if event["action"] == "servo"],
            [(3, 1028), (3, 500)],
        )

    def test_negated_chat_gesture_request_remains_text_only(self):
        status, payload = self.request("POST", "/api/chat", {"message": "Please do not nod"})
        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(payload),
            {"ok": True, "result": {"text": "hibot heard: Please do not nod"}},
        )
        self.assertFalse(any(event["action"] == "servo" for event in self.robot.backend.events))

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

    def test_agent_routes_expose_only_bounded_motion_and_tools(self):
        status, payload = self.request(
            "POST", "/api/agent/drive_for", {"direction": "forward", "duration": 0.05}
        )
        self.assertEqual(status, 200)
        drive_result = json.loads(payload)["result"]
        self.assertEqual(drive_result["direction"], "forward")
        self.assertEqual(drive_result["speed"], 40.0)
        self.assertEqual(drive_result["heading"], 90.0)
        self.assertEqual(drive_result["angular_rate"], 0.0)
        self.assertEqual(self.robot.snapshot()["drive"]["speed"], 0.0)

        status, payload = self.request(
            "POST", "/api/agent/move", {"direction": "forward", "distance_cm": 4}
        )
        self.assertEqual(status, 200)
        move_result = json.loads(payload)["result"]
        self.assertEqual(move_result["duration"], 0.1)
        self.assertEqual(move_result["requested_distance_cm"], 4.0)
        self.assertFalse(move_result["distance_measured"])

        status, payload = self.request(
            "POST", "/api/agent/rotate", {"direction": "right", "degrees": 19}
        )
        self.assertEqual(status, 200)
        rotate_result = json.loads(payload)["result"]
        self.assertEqual(rotate_result["duration"], 0.1)
        self.assertEqual(rotate_result["angular_rate"], 0.6)
        self.assertEqual(rotate_result["speed"], 0.0)

        status, payload = self.request(
            "POST", "/api/agent/move", {"direction": "forward", "distance_cm": 4, "seconds": 0.2}
        )
        self.assertEqual(status, 400)
        self.assertIn("exactly one", json.loads(payload)["error"])

        self.robot.backend.mock_distance_mm = 200
        with patch("masterpi_control.robot.time.sleep"):
            status, payload = self.request(
                "POST", "/api/agent/avoid_obstacles", {"duration": 0.1, "speed": 40, "clearance_cm": 30}
            )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["result"]["obstacles_avoided"], 1)

        status, payload = self.request(
            "POST", "/api/agent/servo", {"servo_id": 2, "pulse": 1500, "duration": 0.5}
        )
        self.assertEqual(status, 400)
        self.assertIn("1, 3, 4, 5, or 6", json.loads(payload)["error"])

    def test_agent_sound_direction_and_come_here_routes(self):
        status, payload = self.request(
            "POST", "/api/agent/sound_direction", {"samples": 7}
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["result"]["relative_angle"], 42.0)
        self.assertEqual(self.sound_tracker.direction_calls, [7])

        status, payload = self.request(
            "POST",
            "/api/agent/come_here",
            {"approach_duration": 1.5, "clearance_cm": 50, "samples": 6},
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["result"]["mode"], "sound_source_approach")
        self.assertEqual(self.sound_tracker.come_here_calls, [(1.5, 50, 6)])

    def test_drive_and_stop_api(self):
        status, payload = self.request(
            "POST", "/api/drive", {"speed": 35, "direction": 90, "angular_rate": 0}
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["result"]["speed"], 35.0)
        status, _ = self.request("POST", "/api/stop", {})
        self.assertEqual(status, 200)
        self.assertEqual(self.robot.snapshot()["drive"]["speed"], 0.0)

    def test_check_front_pose_api_uses_exact_servo_targets(self):
        status, payload = self.request(
            "POST", "/api/pose/check_front", {"duration": 0.8}
        )
        self.assertEqual(status, 200)
        result = json.loads(payload)["result"]
        self.assertEqual(
            [(item["servo_id"], item["pulse"]) for item in result["servos"]],
            [(3, 1200), (4, 2500), (5, 1500), (6, 1500), (1, 2200)],
        )

        status, payload = self.request(
            "POST", "/api/agent/check_front", {"duration": 0.8}
        )
        self.assertEqual(status, 200)
        result = json.loads(payload)["result"]
        self.assertEqual(
            [(item["servo_id"], item["pulse"]) for item in result["servos"]],
            [(3, 1200), (4, 2500), (5, 1500), (6, 1500), (1, 2200)],
        )

    def test_check_ground_pose_and_agent_apis_use_recorded_targets(self):
        for route in ("/api/pose/check_ground", "/api/agent/check_ground"):
            with self.subTest(route=route):
                status, payload = self.request("POST", route, {"duration": 0.8})
                self.assertEqual(status, 200)
                result = json.loads(payload)["result"]
                self.assertEqual(result["pose"], "check_ground")
                self.assertEqual(
                    [(item["servo_id"], item["pulse"]) for item in result["servos"]],
                    [(3, 500), (4, 2500), (5, 1500), (6, 1500), (1, 2200)],
                )

    def test_camera_guided_grab_api(self):
        status, payload = self.request("POST", "/api/grab", {"target": "blue"})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(payload)["result"]["grabbed"])
        self.assertEqual(self.vision_grasper.targets, ["blue"])

        status, payload = self.request("POST", "/api/agent/grab_from_ground", {})
        self.assertEqual(status, 200)
        result = json.loads(payload)["result"]
        self.assertEqual(result["mode"], "fixed ground pickup")
        self.assertTrue(result["returned_home"])
        self.assertEqual(self.vision_grasper.front_pickups, ["default"])
        self.assertEqual(self.vision_grasper.targets, ["blue"])

        status, payload = self.request("POST", "/api/agent/grab_from_front", {})
        self.assertEqual(status, 200)
        result = json.loads(payload)["result"]
        self.assertTrue(result["returned_home"])
        self.assertEqual(self.vision_grasper.front_pickups, ["default", "can"])

        status, _payload = self.request("POST", "/api/agent/grab", {})
        self.assertEqual(status, 404)

    def test_forced_front_grab_skips_recognition(self):
        status, payload = self.request("POST", "/api/grab", {"force": True})
        self.assertEqual(status, 200)
        result = json.loads(payload)["result"]
        self.assertTrue(result["grabbed"])
        self.assertEqual(result["mode"], "fixed ground pickup")
        self.assertEqual(self.vision_grasper.targets, [])

    def test_new_chat_session_button_and_endpoint_drop_stale_context(self):
        status, payload = self.request("POST", "/api/chat/new", {})
        self.assertEqual(status, 200)
        session = json.loads(payload)["result"]["session"]
        self.assertTrue(session.startswith("fake-session-"))
        # A second click must not reuse the first session.
        status, payload = self.request("POST", "/api/chat/new", {})
        self.assertNotEqual(json.loads(payload)["result"]["session"], session)

    def test_web_chat_executes_an_explicit_move_instead_of_replying(self):
        for message, direction, duration in (
            ("move left 10 cm", "left", 0.5),
            ("go forward 20cm", "forward", 0.5),
            ("drive back 8 centimetres", "backward", 0.2),
            ("strafe right for 0.3 seconds", "right", 0.3),
        ):
            with self.subTest(message=message):
                with patch.object(self.robot, "_run_drive_for"):
                    status, payload = self.request("POST", "/api/chat", {"message": message})
                self.assertEqual(status, 200)
                result = json.loads(payload)["result"]
                self.assertEqual(result["action"]["name"], "move")
                self.assertEqual(result["action"]["result"]["direction"], direction)
                self.assertEqual(result["action"]["result"]["duration"], duration)
                self.assertIn("Moved " + direction, result["text"])
        # The move is performed, not narrated by the model.
        self.assertEqual(self.chat.messages, [])

    def test_web_chat_executes_an_explicit_rotation(self):
        with patch.object(self.robot, "_run_drive_for"):
            status, payload = self.request(
                "POST", "/api/chat", {"message": "turn right 95 degrees"}
            )
        self.assertEqual(status, 200)
        result = json.loads(payload)["result"]
        self.assertEqual(result["action"]["name"], "rotate")
        self.assertEqual(result["action"]["result"]["direction"], "rotate_right")
        self.assertEqual(result["action"]["result"]["duration"], 0.5)
        self.assertIn("no gyro", result["text"])

    def test_web_chat_move_without_an_amount_states_the_default(self):
        with patch.object(self.robot, "_run_drive_for"):
            status, payload = self.request("POST", "/api/chat", {"message": "move left"})
        result = json.loads(payload)["result"]
        self.assertEqual(result["action"]["result"]["requested_distance_cm"], 10.0)
        self.assertIn("used the default", result["text"])

    def test_web_chat_leaves_negated_and_ordinary_messages_alone(self):
        for message in ("do not move left", "what is your left wheel called?"):
            with self.subTest(message=message):
                status, payload = self.request("POST", "/api/chat", {"message": message})
                self.assertEqual(status, 200)
                self.assertNotIn("action", json.loads(payload)["result"])
        self.assertEqual(len(self.chat.messages), 2)

    def test_grab_object_runs_the_guided_skill_flow(self):
        status, payload = self.request("POST", "/api/grab/object", {})
        self.assertEqual(status, 200)
        result = json.loads(payload)["result"]
        self.assertTrue(result["grabbed"])
        self.assertEqual(result["finished_at"], "lift pose, still holding")
        # Defaults to the operator-tuned depth, and never runs the blind pickup.
        self.assertEqual(self.vision_grasper.guided_calls, [{"target": None, "pickup_z": -1.0}])
        self.assertEqual(self.vision_grasper.front_pickups, [])

        status, payload = self.request(
            "POST", "/api/agent/grab_object", {"target": "toy", "pickup_z": -2}
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["result"]["target"], "toy")
        self.assertEqual(
            self.vision_grasper.guided_calls[-1], {"target": "toy", "pickup_z": -2}
        )

    def test_grab_object_stream_reports_each_step_as_it_happens(self):
        status, payload, content_type = self.request_raw(
            "POST", "/api/grab/object/stream", {"target": "toy"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(content_type, "application/x-ndjson")
        events = [json.loads(line) for line in payload.splitlines() if line.strip()]
        self.assertEqual(
            [event["stage"] for event in events], ["observe", "correct", "done"]
        )
        # Intermediate steps carry the text and image the chat panel renders,
        # and arrive before the final result rather than with it.
        self.assertIn("Saw stuffed toy", events[0]["text"])
        self.assertTrue(events[0]["image"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(events[1]["distance_cm"], 1.0)
        self.assertTrue(events[-1]["ok"])
        self.assertEqual(events[-1]["result"]["target"], "toy")

    def test_grab_object_stream_reports_a_failure_as_a_final_event(self):
        def explode(*args, **kwargs):
            raise RobotError("A camera-guided grasp is already running")

        self.vision_grasper.grab_object = explode
        status, payload, _ = self.request_raw("POST", "/api/grab/object/stream", {})
        # The response is already committed, so the error is the last event.
        self.assertEqual(status, 200)
        last = json.loads(payload.splitlines()[-1])
        self.assertEqual(last["stage"], "error")
        self.assertFalse(last["ok"])
        self.assertIn("already running", last["text"])

    def test_forced_can_grab_uses_recorded_can_pickup(self):
        status, payload = self.request(
            "POST", "/api/grab", {"force": True, "pickup": "can"}
        )
        self.assertEqual(status, 200)
        result = json.loads(payload)["result"]
        self.assertTrue(result["grabbed"])
        self.assertEqual(result["pickup"], "can")
        self.assertEqual(self.vision_grasper.front_pickups, ["can"])

    def test_nod_gesture_api(self):
        with patch("masterpi_control.robot.time.sleep"):
            status, payload = self.request("POST", "/api/gesture/nod", {})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["result"], {"gesture": "nod", "cycles": 1})

    def test_shake_gesture_api(self):
        with patch("masterpi_control.robot.time.sleep"):
            status, payload = self.request("POST", "/api/gesture/shake", {})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["result"], {"gesture": "shake", "cycles": 2})
        self.assertEqual(self.robot.snapshot()["arm"]["x"], 0.0)

    def test_dance_api_starts_bundled_choreography(self):
        status, payload = self.request("POST", "/api/dance", {})
        self.assertEqual(status, 200)
        result = json.loads(payload)["result"]
        self.assertTrue(result["started"])
        self.assertTrue(result["audio"])
        self.assertEqual(result["duration_seconds"], 34.6)
        self.assertEqual(
            self.dance_launcher.urls,
            [("http://127.0.0.1:8000", False)],
        )

        status, payload = self.request("POST", "/api/agent/dance", {})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(payload)["result"]["started"])
        self.assertTrue(json.loads(payload)["result"]["wait_for_audio"])
        self.assertEqual(
            self.dance_launcher.urls,
            [
                ("http://127.0.0.1:8000", False),
                ("http://127.0.0.1:8000", True),
            ],
        )

    def test_button1_starts_same_dance_as_webpage(self):
        self.robot.backend.press_button(1)
        deadline = time.monotonic() + 0.5
        while (
            self.robot.snapshot()["button_dance_count"] == 0
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        self.assertEqual(
            self.dance_launcher.urls,
            [("http://127.0.0.1:8000", False)],
        )
        self.assertEqual(
            self.robot.snapshot()["last_button"],
            {"button": 1, "action": "dance"},
        )

    def test_dance_api_reports_conflict_while_dance_is_active(self):
        with patch.object(
            self.dance_launcher,
            "start",
            side_effect=DanceBusy("A dance is already running"),
        ):
            status, payload = self.request("POST", "/api/dance", {})
        self.assertEqual(status, 409)
        self.assertIn("already running", json.loads(payload)["error"])

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
