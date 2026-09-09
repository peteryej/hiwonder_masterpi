"""Dependency-free HTTP server for the MasterPi control panel."""

from __future__ import annotations

import base64
import json
import logging
import re
import ssl
import subprocess
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from itertools import chain
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlsplit

from .camera import CameraStream, CameraUnavailable
from .chat import HermesChat
from .robot import Robot, RobotError, ValidationError
from .sound import ReSpeakerDirection, SoundTracker, SoundUnavailable
from .vision import VisionGrasper, annotate_object_detections
from .voice_status import read_voice_status

LOG = logging.getLogger(__name__)
MAX_BODY_BYTES = 16 * 1024
MAX_AUDIO_BYTES = 25 * 1024 * 1024


class DanceUnavailable(RuntimeError):
    """Raised when the bundled choreography cannot be launched."""


class DanceBusy(DanceUnavailable):
    """Raised when a dance process is already active."""


def _default_dance_script() -> Path:
    return Path(__file__).resolve().parents[3] / "robot_choregraph" / "dance.py"


class DanceLauncher:
    """Own at most one asynchronous dance.py process."""

    def __init__(
        self,
        script: Optional[Path] = None,
        *,
        python: str = sys.executable,
        popen: Callable[..., Any] = subprocess.Popen,
    ) -> None:
        self.script = Path(script) if script is not None else _default_dance_script()
        self.python = python
        self._popen = popen
        self._lock = threading.Lock()
        self._process: Any = None

    def start(self, controller_url: str, *, wait_for_audio: bool = False) -> Dict[str, Any]:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                raise DanceBusy("A dance is already running")
            if not self.script.is_file():
                raise DanceUnavailable(f"Dance script was not found: {self.script}")
            command = [
                self.python,
                str(self.script),
                "--execute",
                "--url",
                controller_url,
            ]
            if wait_for_audio:
                command.append("--wait-for-audio")
            try:
                process = self._popen(command, cwd=str(self.script.parent))
            except OSError as exc:
                raise DanceUnavailable(f"Could not start dance: {exc}") from exc
            self._process = process
            threading.Thread(
                target=self._watch,
                args=(process,),
                name="masterpi-dance",
                daemon=True,
            ).start()
            return {
                "started": True,
                "pid": process.pid,
                "audio": True,
                "wait_for_audio": wait_for_audio,
                "duration_seconds": 34.6,
            }

    def _watch(self, process: Any) -> None:
        status = process.wait()
        LOG.info("Dance process %s exited with status %s", process.pid, status)
        with self._lock:
            if self._process is process:
                self._process = None

    def stop(self) -> None:
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            process.terminate()


def _index_html() -> bytes:
    return (
        resources.files("masterpi_control")
        .joinpath("static/index.html")
        .read_text(encoding="utf-8")
        .encode("utf-8")
    )


def make_handler(
    robot: Robot,
    camera: Optional[CameraStream] = None,
    chat: Any = None,
    ca_certificate: Optional[bytes] = None,
    vision_grasper: Any = None,
    sound_tracker: Any = None,
    voice_status_file: Optional[Path] = None,
    dance_launcher: Any = None,
    dance_controller_url: str = "http://127.0.0.1:8000",
) -> type[BaseHTTPRequestHandler]:
    index = _index_html()
    chat_service = chat or HermesChat()
    grasper = (
        vision_grasper
        if vision_grasper is not None
        else (VisionGrasper(robot, camera) if camera is not None else None)
    )
    tracker = sound_tracker or SoundTracker(robot)
    dancer = dance_launcher or DanceLauncher()

    def recognize_and_grab(data: Dict[str, Any]) -> Dict[str, Any]:
        if grasper is None:
            raise CameraUnavailable("Camera-guided grasping is not configured")
        force = data.get("force", False)
        if not isinstance(force, bool):
            raise ValidationError("force must be true or false")
        if force:
            return grasper.grab_front(data.get("pickup", "default"))
        return grasper.recognize_and_grab(
            data.get("target", "any"), data.get("pickup", "default")
        )

    def agent_grab(data: Dict[str, Any]) -> Dict[str, Any]:
        if grasper is None:
            raise CameraUnavailable("Grasping is not configured")
        return grasper.grab_front()

    def analyze_color_camera(data: Dict[str, Any]) -> Dict[str, Any]:
        if grasper is None:
            raise CameraUnavailable("Camera analysis is not configured")
        return grasper.analyze_scene(data.get("samples", 3))

    def analyze_hermes_camera(data: Dict[str, Any]) -> Dict[str, Any]:
        if camera is None:
            raise CameraUnavailable("Camera analysis is not configured")
        pose = robot.check_front(0.8)
        time.sleep(0.85)
        _, frame = camera.next_frame(timeout=3.0)
        result = chat_service.analyze_image(frame)
        annotated = annotate_object_detections(frame, result.get("objects", []))
        result["annotated_image"] = (
            "data:image/jpeg;base64," + base64.b64encode(annotated).decode("ascii")
        )
        result["pose"] = pose["pose"]
        return result

    def camera_question(message: str) -> bool:
        normalized = message.lower().strip()
        if re.search(r"\b(?:do not|don't|never)\s+(?:look|check|analy[sz]e|describe)\b", normalized):
            return False
        return bool(
            re.search(
                r"\b(?:what (?:do|can) you see|"
                r"what(?:'s| is) (?:in front of you|in (?:the )?(?:camera|image|picture))|"
                r"(?:look at|check|analy[sz]e|describe) (?:the |your )?"
                r"(?:camera|view|image|picture|scene))\b",
                normalized,
            )
        )

    def color_detection_request(message: str) -> bool:
        normalized = message.lower()
        return bool(
            re.search(
                r"\b(?:colou?r (?:detection|detector|recognition|analysis)|"
                r"detect (?:the )?colou?rs?)\b",
                normalized,
            )
        )

    def camera_reply(scene: Dict[str, Any]) -> str:
        objects = scene.get("objects", [])
        if not objects:
            return "I don't detect any red, green, blue, or yellow objects in the camera view."
        descriptions = []
        for item in objects:
            count = int(item["count"])
            description = str(item["label"])
            descriptions.append(
                f"{count} {description}s" if count != 1 else f"a {description}"
            )
        if len(descriptions) == 1:
            visible = descriptions[0]
        else:
            visible = ", ".join(descriptions[:-1]) + f" and {descriptions[-1]}"
        return f"I can see {visible}."

    def chat_action_name(message: str) -> Optional[str]:
        """Recognize only explicit, non-negated conversational gesture requests."""
        normalized = message.lower()
        if re.search(r"\b(?:do not|don't|never)\s+(?:please\s+)?(?:nod|shake)\b", normalized):
            return None
        requested = set(
            re.findall(
                r"(?:^\s*(?:hibot[,:]?\s*)?|\bplease\s+|\b(?:can|could|would|will)\s+you\s+)(nod|shake)\b",
                normalized,
            )
        )
        return requested.pop() if len(requested) == 1 else None

    def chat_reply(data: Dict[str, Any]) -> Dict[str, Any]:
        message = data.get("message")
        if not isinstance(message, str) or not message.strip():
            raise ValidationError("message must be a non-empty string")
        message = message.strip()
        if len(message) > 4000:
            raise ValidationError("message must be at most 4000 characters")
        if camera_question(message) or color_detection_request(message):
            if color_detection_request(message):
                scene = analyze_color_camera({"samples": 3})
                return {"text": camera_reply(scene), "vision": scene}
            scene = analyze_hermes_camera({})
            text = str(scene.get("description") or "").strip()
            if not text:
                labels = [str(item["label"]) for item in scene.get("objects", [])]
                text = "I can see " + ", ".join(labels) + "." if labels else "I could not identify any objects."
            return {"text": text, "vision": scene}
        result: Dict[str, Any] = {"text": chat_service.reply(message)}
        action_name = chat_action_name(message)
        if action_name == "nod":
            action_result = robot.nod()
        elif action_name == "shake":
            action_result = robot.shake()
        else:
            return result
        result["action"] = {"name": action_name, "result": action_result}
        return result

    def agent_servo(data: Dict[str, Any]) -> Dict[str, Any]:
        servo_id = data.get("servo_id")
        if servo_id not in (1, 3, 4, 5, 6):
            raise ValidationError("servo_id must be one of: 1, 3, 4, 5, or 6")
        return robot.servo(servo_id, data.get("pulse"), data.get("duration", 0.5))

    class Handler(BaseHTTPRequestHandler):
        server_version = "MasterPiControl/0.1"

        def _send_json(self, status: HTTPStatus, value: Dict[str, Any]) -> None:
            body = json.dumps(value, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_bytes(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> Dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ValidationError("Invalid Content-Length") from exc
            if length <= 0 or length > MAX_BODY_BYTES:
                raise ValidationError("Request body must be between 1 byte and 16 KiB")
            if "application/json" not in self.headers.get("Content-Type", ""):
                raise ValidationError("Content-Type must be application/json")
            try:
                value = json.loads(self.rfile.read(length))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValidationError("Request body is not valid JSON") from exc
            if not isinstance(value, dict):
                raise ValidationError("Request body must be a JSON object")
            return value

        def _read_audio(self) -> tuple[bytes, str]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ValidationError("Invalid Content-Length") from exc
            if length <= 0 or length > MAX_AUDIO_BYTES:
                raise ValidationError("Audio must be between 1 byte and 25 MiB")
            content_type = self.headers.get("Content-Type", "").strip().lower()
            if not content_type.startswith("audio/"):
                raise ValidationError("Content-Type must be audio/*")
            return self.rfile.read(length), content_type

        def _send_camera_stream(self) -> None:
            if camera is None:
                self._send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"ok": False, "error": "Camera streaming is not configured"},
                )
                return
            frames = camera.frames()
            try:
                first_frame = next(frames)
            except CameraUnavailable as exc:
                self._send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"ok": False, "error": str(exc)},
                )
                return

            boundary = b"frame"
            self.send_response(HTTPStatus.OK)
            self.send_header(
                "Content-Type", "multipart/x-mixed-replace; boundary=frame"
            )
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                for frame in chain((first_frame,), frames):
                    self.wfile.write(b"--" + boundary + b"\r\n")
                    self.wfile.write(b"Content-Type: image/jpeg\r\n")
                    self.wfile.write(
                        f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii")
                    )
                    self.wfile.write(frame)
                    self.wfile.write(b"\r\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            except CameraUnavailable as exc:
                LOG.warning("Camera stream ended: %s", exc)
            finally:
                frames.close()

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == "/":
                self._send_bytes(HTTPStatus.OK, index, "text/html; charset=utf-8")
            elif path == "/masterpi-ca.crt" and ca_certificate is not None:
                self._send_bytes(
                    HTTPStatus.OK, ca_certificate, "application/x-x509-ca-cert"
                )
            elif path == "/api/state":
                self._send_json(HTTPStatus.OK, {"ok": True, "state": robot.snapshot()})
            elif path == "/api/distance":
                try:
                    self._send_json(
                        HTTPStatus.OK, {"ok": True, "result": robot.distance()}
                    )
                except RobotError as exc:
                    self._send_json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"ok": False, "error": str(exc)},
                    )
            elif path == "/api/voice":
                try:
                    self._send_json(
                        HTTPStatus.OK, {"ok": True, "result": robot.voice_result()}
                    )
                except RobotError as exc:
                    self._send_json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"ok": False, "error": str(exc)},
                    )
            elif path == "/api/voice/conversation":
                self._send_json(
                    HTTPStatus.OK,
                    {"ok": True, "conversation": read_voice_status(voice_status_file)},
                )
            elif path == "/api/camera/stream":
                self._send_camera_stream()
            else:
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})

        def do_POST(self) -> None:
            path = urlsplit(self.path).path
            if path.startswith("/api/agent/") and self.client_address[0] not in {"127.0.0.1", "::1"}:
                self._send_json(
                    HTTPStatus.FORBIDDEN,
                    {"ok": False, "error": "Agent control is available only from localhost"},
                )
                return
            try:
                if path == "/api/chat/audio":
                    audio, content_type = self._read_audio()
                    transcript = chat_service.transcribe(audio, content_type)
                    result = chat_reply({"message": transcript})
                    self._send_json(
                        HTTPStatus.OK,
                        {"ok": True, "transcript": transcript, "result": result},
                    )
                    return
                if path == "/api/chat/tts":
                    data = self._read_json()
                    text = data.get("text")
                    if not isinstance(text, str) or not text.strip():
                        raise ValidationError("text must be a non-empty string")
                    text = text.strip()
                    if len(text) > 4000:
                        raise ValidationError("text must be at most 4000 characters")
                    audio, content_type = chat_service.synthesize(text)
                    self._send_bytes(HTTPStatus.OK, audio, content_type)
                    return
                data = self._read_json()
                actions: Dict[str, Callable[[Dict[str, Any]], Any]] = {
                    "/api/chat": chat_reply,
                    "/api/drive": lambda d: robot.drive(
                        d.get("speed"), d.get("direction"), d.get("angular_rate", 0)
                    ),
                    "/api/stop": lambda d: robot.stop(),
                    "/api/arm": lambda d: robot.arm(
                        d.get("x"),
                        d.get("y"),
                        d.get("z"),
                        d.get("pitch", 0),
                        d.get("pitch_min", -90),
                        d.get("pitch_max", 90),
                        d.get("duration", 1.0),
                    ),
                    "/api/home": lambda d: robot.home(d.get("duration", 1.5)),
                    "/api/pose/check_front": lambda d: robot.check_front(
                        d.get("duration", 0.8)
                    ),
                    "/api/gesture/nod": lambda d: robot.nod(),
                    "/api/gesture/shake": lambda d: robot.shake(),
                    "/api/servo": lambda d: robot.servo(
                        d.get("servo_id"), d.get("pulse"), d.get("duration", 0.5)
                    ),
                    "/api/gripper": lambda d: robot.gripper(
                        d.get("opened"), d.get("duration", 0.5)
                    ),
                    "/api/grab": recognize_and_grab,
                    "/api/dance": lambda d: dancer.start(dance_controller_url),
                    "/api/camera/analyze": analyze_hermes_camera,
                    "/api/camera/analyze/color": analyze_color_camera,
                    "/api/rgb": lambda d: robot.rgb(d.get("red"), d.get("green"), d.get("blue")),
                    "/api/sonar/rgb": lambda d: robot.sonar_rgb(
                        d.get("red"), d.get("green"), d.get("blue")
                    ),
                    "/api/voice/speak": lambda d: robot.voice_speak(d.get("phrase")),
                    "/api/buzzer": lambda d: robot.buzzer(
                        d.get("frequency", 1900),
                        d.get("on_time", 0.1),
                        d.get("off_time", 0.1),
                        d.get("repeat", 1),
                    ),
                    "/api/agent/state": lambda d: robot.snapshot(),
                    "/api/agent/drive_for": lambda d: robot.drive_for(
                        d.get("direction"), d.get("duration", 1.0), d.get("speed", 40)
                    ),
                    "/api/agent/avoid_obstacles": lambda d: robot.avoid_obstacles(
                        d.get("duration"), d.get("speed", 40), d.get("clearance_cm", 30)
                    ),
                    "/api/agent/sound_direction": lambda d: tracker.direction(
                        d.get("samples", 5)
                    ),
                    "/api/agent/come_here": lambda d: tracker.come_here(
                        d.get("approach_duration", 2.0),
                        d.get("clearance_cm", 45),
                        d.get("samples", 5),
                    ),
                    "/api/agent/stop": lambda d: robot.stop(),
                    "/api/agent/home": lambda d: robot.home(d.get("duration", 1.5)),
                    "/api/agent/check_front": lambda d: robot.check_front(
                        d.get("duration", 0.8)
                    ),
                    "/api/agent/nod": lambda d: robot.nod(),
                    "/api/agent/shake": lambda d: robot.shake(),
                    "/api/agent/dance": lambda d: dancer.start(
                        dance_controller_url, wait_for_audio=True
                    ),
                    "/api/agent/servo": agent_servo,
                    "/api/agent/gripper": lambda d: robot.gripper(
                        d.get("opened"), d.get("duration", 0.5)
                    ),
                    "/api/agent/grab": agent_grab,
                    "/api/agent/camera_analyze": analyze_hermes_camera,
                    "/api/agent/camera_analyze_color": analyze_color_camera,
                    "/api/agent/rgb": lambda d: robot.rgb(
                        d.get("red"), d.get("green"), d.get("blue")
                    ),
                    "/api/agent/sonar_rgb": lambda d: robot.sonar_rgb(
                        d.get("red"), d.get("green"), d.get("blue")
                    ),
                    "/api/agent/buzzer": lambda d: robot.buzzer(
                        d.get("frequency", 1900),
                        d.get("on_time", 0.1),
                        d.get("off_time", 0.1),
                        d.get("repeat", 1),
                    ),
                }
                action = actions.get(path)
                if action is None:
                    self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})
                    return
                result = action(data)
                self._send_json(HTTPStatus.OK, {"ok": True, "result": result})
            except ValidationError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            except CameraUnavailable as exc:
                self._send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"ok": False, "error": str(exc)},
                )
            except SoundUnavailable as exc:
                self._send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"ok": False, "error": str(exc)},
                )
            except DanceBusy as exc:
                self._send_json(
                    HTTPStatus.CONFLICT, {"ok": False, "error": str(exc)}
                )
            except DanceUnavailable as exc:
                self._send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"ok": False, "error": str(exc)},
                )
            except ValueError as exc:
                self._send_json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"ok": False, "error": str(exc)},
                )
            except Exception as exc:
                LOG.exception("Robot command failed")
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)}
                )

        def log_message(self, message: str, *args: Any) -> None:
            LOG.info("%s - %s", self.client_address[0], message % args)

    return Handler


def serve(
    robot: Robot,
    host: str = "0.0.0.0",
    port: int = 8000,
    camera: Optional[CameraStream] = None,
    tls_port: Optional[int] = None,
    certfile: Optional[str] = None,
    keyfile: Optional[str] = None,
    ca_certfile: Optional[str] = None,
    sound_front_angle: float = 0,
    sound_clockwise: bool = True,
) -> None:
    camera_stream = camera or CameraStream()
    ca_certificate = Path(ca_certfile).read_bytes() if ca_certfile else None
    dance_launcher = DanceLauncher()
    handler = make_handler(
        robot,
        camera_stream,
        ca_certificate=ca_certificate,
        sound_tracker=SoundTracker(
            robot,
            ReSpeakerDirection(
                front_angle=sound_front_angle,
                clockwise=sound_clockwise,
            ),
        ),
        dance_launcher=dance_launcher,
        dance_controller_url=f"http://127.0.0.1:{port}",
    )
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    tls_server: Optional[ThreadingHTTPServer] = None
    tls_thread: Optional[threading.Thread] = None
    if tls_port is not None:
        if not certfile or not keyfile:
            raise ValidationError("TLS requires a certificate and private key")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=certfile, keyfile=keyfile)
        tls_server = ThreadingHTTPServer((host, tls_port), handler)
        tls_server.daemon_threads = True
        tls_server.socket = context.wrap_socket(tls_server.socket, server_side=True)
        tls_thread = threading.Thread(target=tls_server.serve_forever, daemon=True)
        tls_thread.start()
        LOG.info("MasterPi secure control panel listening on https://%s:%d", host, tls_port)
    LOG.info("MasterPi control panel listening on http://%s:%d", host, port)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        dance_launcher.stop()
        server.server_close()
        if tls_server is not None:
            tls_server.shutdown()
            tls_server.server_close()
        if tls_thread is not None:
            tls_thread.join(timeout=2)
        camera_stream.close()
        robot.close()
