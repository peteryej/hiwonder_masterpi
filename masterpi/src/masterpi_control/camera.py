"""Shared OpenCV camera capture for the browser control panel."""

from __future__ import annotations

import importlib
import logging
import os
import threading
from typing import Any, Iterator, Optional, Tuple, Union


LOG = logging.getLogger(__name__)


class CameraUnavailable(RuntimeError):
    """Raised when the configured camera cannot produce a frame."""


class CameraStream:
    """Read one camera in the background and share its latest JPEG frame."""

    def __init__(
        self,
        device: Optional[Union[int, str]] = None,
        width: int = 640,
        height: int = 480,
        fps: int = 20,
        jpeg_quality: int = 75,
    ) -> None:
        configured = os.environ.get("MASTERPI_CAMERA_DEVICE", "0")
        if device is None:
            device = int(configured) if configured.isdecimal() else configured
        self.device = device
        self.width = width
        self.height = height
        self.fps = fps
        self.jpeg_quality = jpeg_quality
        self._condition = threading.Condition()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._frame: Optional[bytes] = None
        self._sequence = 0
        self._error: Optional[str] = None
        self._closed = False

    def start(self) -> None:
        with self._condition:
            if self._closed:
                raise CameraUnavailable("Camera stream is closed")
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._capture_loop,
                name="masterpi-camera",
                daemon=True,
            )
            self._thread.start()

    def _set_error(self, message: str) -> None:
        with self._condition:
            self._error = message
            self._condition.notify_all()

    def _open_capture(self, cv2: Any) -> Any:
        if isinstance(self.device, int):
            capture = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
        else:
            capture = cv2.VideoCapture(self.device)
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        capture.set(cv2.CAP_PROP_FPS, self.fps)
        if hasattr(cv2, "VideoWriter_fourcc"):
            capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc("Y", "U", "Y", "V"))
        return capture

    def _capture_loop(self) -> None:
        try:
            cv2 = importlib.import_module("cv2")
        except (ImportError, OSError) as exc:
            self._set_error(f"OpenCV is unavailable: {exc}")
            return

        while not self._stop.is_set():
            capture = None
            try:
                capture = self._open_capture(cv2)
                if not capture.isOpened():
                    raise CameraUnavailable(f"Could not open camera {self.device!r}")
                LOG.info(
                    "Camera %r opened at %dx%d, requested %d fps",
                    self.device,
                    self.width,
                    self.height,
                    self.fps,
                )
                with self._condition:
                    self._error = None
                while not self._stop.is_set():
                    ok, image = capture.read()
                    if not ok or image is None:
                        raise CameraUnavailable("Camera stopped returning frames")
                    encoded_ok, encoded = cv2.imencode(
                        ".jpg",
                        image,
                        [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality],
                    )
                    if not encoded_ok:
                        raise CameraUnavailable("Could not encode camera frame as JPEG")
                    with self._condition:
                        self._frame = encoded.tobytes()
                        self._sequence += 1
                        self._error = None
                        self._condition.notify_all()
            except (CameraUnavailable, OSError) as exc:
                self._set_error(str(exc))
                LOG.warning("Camera capture failed: %s", exc)
            except Exception as exc:
                self._set_error(f"Camera capture failed: {exc}")
                LOG.exception("Unexpected camera capture failure")
            finally:
                if capture is not None:
                    capture.release()
            if not self._stop.wait(1.0):
                LOG.info("Retrying camera %r", self.device)

    def next_frame(self, after: int = 0, timeout: float = 5.0) -> Tuple[int, bytes]:
        self.start()
        with self._condition:
            ready = self._condition.wait_for(
                lambda: self._closed
                or (self._frame is not None and self._sequence > after),
                timeout=timeout,
            )
            if ready and self._frame is not None and self._sequence > after:
                return self._sequence, self._frame
            if self._closed:
                raise CameraUnavailable("Camera stream is closed")
            raise CameraUnavailable(self._error or "Timed out waiting for a camera frame")

    def frames(self) -> Iterator[bytes]:
        sequence = 0
        while not self._stop.is_set():
            sequence, frame = self.next_frame(sequence)
            yield frame

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._stop.set()
            self._condition.notify_all()
            thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)

