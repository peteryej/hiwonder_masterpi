"""Pose-preserving camera capture and Hermes estimated-box annotation."""

import json
import tempfile
from pathlib import Path
from urllib.request import urlopen


def capture(opener=urlopen):
    with opener("http://127.0.0.1:8000/api/camera/stream", timeout=10) as response:
        if "multipart/x-mixed-replace" not in response.headers.get("Content-Type", ""):
            raise RuntimeError("Expected an MJPEG camera stream")
        length = None
        for _ in range(16):
            line = response.readline(1024)
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":", 1)[1].strip())
            if line in (b"\r\n", b"\n") and length is not None:
                break
        else:
            raise RuntimeError("MJPEG frame headers incomplete")
        if not 0 < length <= 20 * 1024 * 1024:
            raise RuntimeError("Invalid JPEG frame length")
        chunks = []
        remaining = length
        while remaining:
            chunk = response.read(remaining)
            if not chunk:
                raise RuntimeError("Incomplete camera frame")
            chunks.append(chunk)
            remaining -= len(chunk)
        frame = b"".join(chunks)
    if not frame.startswith(b"\xff\xd8") or not frame.endswith(b"\xff\xd9"):
        raise RuntimeError("Invalid camera JPEG")
    return frame


def main():
    import cv2
    import numpy as np
    from masterpi_control.chat import HermesChat
    from masterpi_control.vision import annotate_object_detections

    frame = capture()
    decoded = cv2.imdecode(np.frombuffer(frame, dtype=np.uint8), cv2.IMREAD_COLOR)
    if decoded is None:
        raise RuntimeError("Camera JPEG cannot be decoded")
    height, width = decoded.shape[:2]
    directory = Path(tempfile.mkdtemp(prefix="hibot-ground-grab-"))
    raw = directory / "camera.jpg"
    raw.write_bytes(frame)
    print(json.dumps({"camera_image": str(raw)}), flush=True)
    result = HermesChat().analyze_image(frame)
    annotated = directory / "annotated.jpg"
    annotated.write_bytes(annotate_object_detections(frame, result.get("objects", [])))
    measurements = []
    for obj in result.get("objects", []):
        box = obj["bbox"]
        x = (box["x_min"] + box["x_max"]) * width / 2
        measurements.append({
            "label": obj["label"],
            "center_pixels": [x, (box["y_min"] + box["y_max"]) * height / 2],
            "bottom_center_pixels": [x, box["y_max"] * height],
            "width_pixels": (box["x_max"] - box["x_min"]) * width,
            "height_pixels": (box["y_max"] - box["y_min"]) * height,
        })
    result.update({"image_width": width, "image_height": height,
                   "annotated_image": str(annotated), "measurements": measurements})
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
