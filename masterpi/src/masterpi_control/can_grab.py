"""Camera-guarded pickup program for the staged yellow-and-blue can."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable, Dict, Optional
from urllib import error, request


Post = Callable[[str, Dict[str, Any]], Dict[str, Any]]


def _post_json(base_url: str, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    http_request = request.Request(
        base_url.rstrip("/") + path,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(http_request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"MasterPi request failed: {exc}") from exc
    if not isinstance(result, dict) or not result.get("ok"):
        message = result.get("error", "unknown controller error") if isinstance(result, dict) else "invalid response"
        raise RuntimeError(f"MasterPi rejected the request: {message}")
    return result


def plan_or_grab_can(
    base_url: str = "http://127.0.0.1:8000",
    *,
    execute: bool = False,
    post: Optional[Post] = None,
) -> Dict[str, Any]:
    """Use semantic image analysis to find a generally centered can.

    The semantic camera route identifies the object and supplies a normalized
    bounding box. Only an explicitly labelled can whose bounding-box center is
    within the central region may trigger the recorded can pickup. Hands must
    remain clear.
    """
    send = post or (lambda path, payload: _post_json(base_url, path, payload))
    scene_response = send("/api/camera/analyze", {})
    scene = scene_response.get("result", {})
    objects = scene.get("objects", []) if isinstance(scene, dict) else []

    cans = []
    for item in objects:
        if not isinstance(item, dict) or "can" not in str(item.get("label", "")).lower().split():
            continue
        box = item.get("bbox")
        if not isinstance(box, dict):
            continue
        try:
            x_min = float(box["x_min"])
            y_min = float(box["y_min"])
            x_max = float(box["x_max"])
            y_max = float(box["y_max"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 <= x_min < x_max <= 1 and 0 <= y_min < y_max <= 1):
            continue
        candidate = dict(item)
        candidate["center_x"] = round((x_min + x_max) / 2, 4)
        candidate["center_y"] = round((y_min + y_max) / 2, 4)
        cans.append(candidate)

    if not cans:
        return {
            "ready": False,
            "executed": False,
            "grabbed": False,
            "reason": "Image analysis did not identify a can with a valid bounding box.",
            "scene": scene,
        }

    target = max(cans, key=lambda item: float(item.get("confidence", 0) or 0))
    if abs(target["center_x"] - 0.5) > 0.25 or abs(target["center_y"] - 0.5) > 0.30:
        return {
            "ready": False,
            "executed": False,
            "grabbed": False,
            "reason": "Image analysis found the can outside the general center region.",
            "target": target,
            "scene": scene,
        }
    if not execute:
        return {
            "ready": True,
            "executed": False,
            "grabbed": False,
            "target": target,
            "scene": scene,
        }

    grab_response = send("/api/grab", {"force": True, "pickup": "can"})
    result = grab_response.get("result", {})
    if not isinstance(result, dict):
        raise RuntimeError("MasterPi returned an invalid grab result")
    return {"ready": True, "executed": True, "target": target, **result}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Use semantic image analysis to locate and grab the staged can with MasterPi."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Move the arm. Use only when the can is staged at the fixed pickup point and hands are clear.",
    )
    args = parser.parse_args(argv)
    try:
        result = plan_or_grab_can(args.base_url, execute=args.execute)
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 2
    print(json.dumps({"ok": True, "result": result}, indent=2))
    if args.execute and not result.get("grabbed"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
