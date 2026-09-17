"""Explicit ground pickup/lift; dry-run by default, no camera or color gate.

After the lift the helper captures two spaced gripper-camera frames so the
caller can confirm the object is actually held. Two are needed because the
stream can stick on an older frame: a single capture can show the pre-lift
view and read as a hold that never happened. Identical frames mean the stream
is stale, not that the scene is still. Both captures are pose-preserving: they
read the existing MJPEG stream and command no movement.
"""

import argparse
import importlib.util
import json
import tempfile
import time
from pathlib import Path
from urllib.request import Request, urlopen


# Pickup depth in cm. -1 is the operator-tuned default, matching the webpage
# quick action and the one physically confirmed pickup. An authorized retry on
# flat plush may go one step lower (-2); below -3 the jaws drive into the floor.
DEFAULT_PICKUP_Z = -1.0
MIN_PICKUP_Z = -3.0
MAX_PICKUP_Z = 2.0


def plan(pickup_z=DEFAULT_PICKUP_Z):
    """The exact pickup/lift sequence at one pickup depth."""
    if not MIN_PICKUP_Z <= pickup_z <= MAX_PICKUP_Z:
        raise ValueError(
            f"pickup_z must be between {MIN_PICKUP_Z:g} and {MAX_PICKUP_Z:g} cm"
        )
    return (
        ("stop", {}, 0),
        ("gripper", {"opened": True, "duration": 0.4}, 0.5),
        ("arm", {"x": 2, "y": 13, "z": pickup_z, "pitch": -68, "pitch_min": -90,
                 "pitch_max": -68, "duration": 1.5}, 1.6),
        ("gripper", {"opened": False, "duration": 0.5}, 0.6),
        ("arm", {"x": 0, "y": 15, "z": 20, "pitch": 0, "pitch_min": -90,
                 "pitch_max": 90, "duration": 1.5}, 1.6),
    )


def post(action, payload):
    request = Request(
        "http://127.0.0.1:8000/api/" + action,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urlopen(request, timeout=15) as response:
        result = json.loads(response.read())
    if not result.get("ok"):
        raise RuntimeError(result.get("error") or "Controller command failed")
    return result["result"]


CONFIRM_DELAY = 1.5


def _observe_capture():
    path = Path(__file__).resolve().with_name("observe.py")
    spec = importlib.util.spec_from_file_location("observe", path)
    observe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(observe)
    return observe.capture()


def capture_after_lift(capture_frame=_observe_capture, sleep=time.sleep, delay=CONFIRM_DELAY):
    """Save two spaced post-lift frames. Commands no movement.

    Byte-identical frames mean the stream is stuck, so neither frame proves
    anything about the current scene; the caller must say so rather than read
    a hold out of a stale image.
    """
    directory = Path(tempfile.mkdtemp(prefix="hibot-ground-grab-lift-"))
    frames = []
    images = []
    for index in range(2):
        if index:
            sleep(delay)
        frame = capture_frame()
        image = directory / f"after_lift_{index + 1}.jpg"
        image.write_bytes(frame)
        frames.append(frame)
        images.append(str(image))
    return {"images": images, "stale_stream": frames[0] == frames[1]}


def execute(call=post, sleep=time.sleep, capture=capture_after_lift, pickup_z=DEFAULT_PICKUP_Z):
    stage = "stop"
    try:
        for stage, payload, settling in plan(pickup_z):
            result = call(stage, payload)
            print(json.dumps({"stage": stage, "payload": payload, "result": result}), flush=True)
            if settling:
                sleep(settling)
    except Exception as error:
        # Preserve the failed stage even if the best-effort Stop also fails.
        detail = f"Pickup failed at {stage}; do not retry blindly: {error}"
        try:
            call("stop", {})
        except Exception as stop_error:
            detail += f"; chassis Stop also failed: {stop_error}"
        raise RuntimeError(detail) from error
    else:
        # Do not open the gripper or return Home after completion.
        call("stop", {})
    # A failed confirmation capture is not a failed pickup: the object may
    # still be held, so report the missing evidence instead of raising.
    try:
        confirmation = capture()
    except Exception as error:
        print(json.dumps({"stage": "confirm", "confirmation_error": str(error)}), flush=True)
        return None
    print(json.dumps({"stage": "confirm", **confirmation}), flush=True)
    return confirmation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Perform the physical pickup/lift")
    parser.add_argument(
        "--pickup-z",
        type=float,
        default=DEFAULT_PICKUP_Z,
        help=(
            f"pickup depth in cm (default {DEFAULT_PICKUP_Z:g}); an authorized "
            "retry on flat plush may use -2"
        ),
    )
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({"executed": False, "pickup_z": args.pickup_z, "plan": plan(args.pickup_z)}))
        return
    confirmation = execute(pickup_z=args.pickup_z)
    print(json.dumps({
        "sequence_completed": True,
        "physical_capture_verified": False,
        "pickup_z": args.pickup_z,
        **(confirmation or {}),
        "next": "Judge retention from the LAST confirmation image; identical frames mean a stuck stream proves nothing",
    }))


if __name__ == "__main__":
    main()
