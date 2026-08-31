"""Dependency-free HTTP server for the MasterPi control panel."""

from __future__ import annotations

import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from itertools import chain
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlsplit

from .camera import CameraStream, CameraUnavailable
from .robot import Robot, RobotError, ValidationError

LOG = logging.getLogger(__name__)
MAX_BODY_BYTES = 16 * 1024


def _index_html() -> bytes:
    return (
        resources.files("masterpi_control")
        .joinpath("static/index.html")
        .read_text(encoding="utf-8")
        .encode("utf-8")
    )


def make_handler(
    robot: Robot, camera: Optional[CameraStream] = None
) -> type[BaseHTTPRequestHandler]:
    index = _index_html()

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
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(index)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(index)
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
            elif path == "/api/camera/stream":
                self._send_camera_stream()
            else:
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})

        def do_POST(self) -> None:
            path = urlsplit(self.path).path
            try:
                data = self._read_json()
                actions: Dict[str, Callable[[Dict[str, Any]], Any]] = {
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
                    "/api/servo": lambda d: robot.servo(
                        d.get("servo_id"), d.get("pulse"), d.get("duration", 0.5)
                    ),
                    "/api/gripper": lambda d: robot.gripper(
                        d.get("opened"), d.get("duration", 0.5)
                    ),
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
                }
                action = actions.get(path)
                if action is None:
                    self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})
                    return
                result = action(data)
                self._send_json(HTTPStatus.OK, {"ok": True, "result": result})
            except ValidationError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
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
) -> None:
    camera_stream = camera or CameraStream()
    server = ThreadingHTTPServer((host, port), make_handler(robot, camera_stream))
    server.daemon_threads = True
    LOG.info("MasterPi control panel listening on http://%s:%d", host, port)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        camera_stream.close()
        robot.close()
