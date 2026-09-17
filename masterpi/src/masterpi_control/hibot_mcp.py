"""MCP server exposing hibot's bounded physical-control capabilities."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from .agent_client import HibotAgentClient
from .robot_tools import dispatch_robot_tool

mcp = FastMCP("hibot")
client = HibotAgentClient()


@mcp.tool()
def get_state() -> dict[str, Any]:
    """Return hibot's current controller state without moving the robot."""
    return dispatch_robot_tool(client, "get_state")


@mcp.tool()
def stop() -> dict[str, Any]:
    """Immediately stop hibot's chassis motion."""
    return dispatch_robot_tool(client, "stop")


@mcp.tool()
def nod() -> dict[str, Any]:
    """Perform hibot's validated arm nod gesture, starting and ending at Home."""
    return dispatch_robot_tool(client, "nod")


@mcp.tool()
def shake() -> dict[str, Any]:
    """Perform hibot's validated arm shake gesture, starting and ending at Home."""
    return dispatch_robot_tool(client, "shake")


@mcp.tool()
def dance() -> dict[str, Any]:
    """Start hibot's bundled choreography with synchronized music."""
    return dispatch_robot_tool(client, "dance")


@mcp.tool()
def home_arm(duration: float = 1.5) -> dict[str, Any]:
    """Move hibot's arm to its documented Home pose."""
    return dispatch_robot_tool(client, "home_arm", {"duration": duration})


@mcp.tool()
def check_front(duration: float = 0.8) -> dict[str, Any]:
    """Match Check ground except servo 3=1200; set 4=2500, 5=1500, 6=1500, 1=2200."""
    return dispatch_robot_tool(client, "check_front", {"duration": duration})


@mcp.tool()
def check_ground(duration: float = 0.8) -> dict[str, Any]:
    """Move to the recorded check-ground pose with gripper servo 1 at 2200."""
    return dispatch_robot_tool(client, "check_ground", {"duration": duration})


@mcp.tool()
def set_servo(servo_id: int, pulse: int, duration: float = 0.5) -> dict[str, Any]:
    """Set a confirmed arm servo: 1 gripper, 3 top, 4, 5, or 6 base."""
    return dispatch_robot_tool(
        client,
        "set_servo",
        {"servo_id": servo_id, "pulse": pulse, "duration": duration},
    )


@mcp.tool()
def set_gripper(opened: bool, duration: float = 0.5) -> dict[str, Any]:
    """Open gripper at servo 1=2500 or close at servo 1=500."""
    return dispatch_robot_tool(
        client, "set_gripper", {"opened": opened, "duration": duration}
    )


@mcp.tool()
def grab_from_ground() -> dict[str, Any]:
    """Blind fixed-point pickup at (2, 13, -1) cm, then Home. Finds nothing.

    This is the webpage's Grab object quick action: it closes the gripper at
    one hard-coded coordinate. It does not look for an object, center on one,
    or check whether anything was picked up, so it only works when the object
    is already staged at that exact point.

    Do NOT use it to grab a named object ("grab the toy", "pick up the can off
    the floor"). Load the hibot-ground-grab skill and follow it instead: it
    looks at the ground, centers the object in the frame, picks up, and
    confirms the hold from the camera.
    """
    return dispatch_robot_tool(client, "grab_from_ground")


@mcp.tool()
def grab_from_front() -> dict[str, Any]:
    """Blind recorded can pose (servos 3-6), then Home. Finds nothing.

    The operator-recorded can pose, run as-is with no camera check. Its
    webpage button has been removed, so this tool is the only caller left: use
    it for a can already staged at the recorded pickup point. To grab a named
    object off the floor, use the hibot-ground-grab skill.
    """
    return dispatch_robot_tool(client, "grab_from_front")


@mcp.tool()
def analyze_camera(samples: int = 3) -> dict[str, Any]:
    """Move to Check front and return vision objects with an annotated image."""
    return dispatch_robot_tool(client, "analyze_camera", {"samples": samples})


@mcp.tool()
def analyze_camera_color(samples: int = 3) -> dict[str, Any]:
    """Use local color detection without moving; only for explicit color requests."""
    return dispatch_robot_tool(client, "analyze_camera_color", {"samples": samples})


@mcp.tool()
def drive_for(
    direction: str, duration: float = 1.0, speed: float = 40.0
) -> dict[str, Any]:
    """Drive with the webpage chassis mapping; speed is 40-100 mm/s, duration max 8 s."""
    return dispatch_robot_tool(
        client,
        "drive_for",
        {"direction": direction, "speed": speed, "duration": duration},
    )


@mcp.tool()
def move(
    direction: str,
    distance_cm: float | None = None,
    seconds: float | None = None,
    speed: float = 40.0,
) -> dict[str, Any]:
    """Drive forward, backward, left, or right by distance_cm OR seconds.

    Give exactly one of distance_cm or seconds. Distance is converted with the
    operator calibration at speed 40 - about 40 cm/s forward/backward, 20 cm/s
    strafing - so it is a timed estimate, not odometry; the robot does not
    measure how far it actually went. Ranges: 2-320 cm forward/backward,
    1-160 cm left/right, 0.05-8 seconds. For a speed other than 40, pass
    seconds, since the calibration does not hold there.
    """
    arguments = {"direction": direction, "speed": speed, "distance_cm": distance_cm, "seconds": seconds}
    # Omit the unused one so the controller sees exactly one of the pair.
    return dispatch_robot_tool(
        client, "move", {k: v for k, v in arguments.items() if v is not None}
    )


@mcp.tool()
def rotate(
    direction: str,
    degrees: float | None = None,
    seconds: float | None = None,
    speed: float = 40.0,
) -> dict[str, Any]:
    """Rotate in place left or right by degrees OR seconds, with no translation.

    Give exactly one of degrees or seconds. Degrees are converted with the
    operator calibration of 190 degrees/s; there is no gyro or odometry, so the
    angle is an estimate. Ranges: 9.5-1520 degrees, 0.05-8 seconds.
    """
    arguments = {"direction": direction, "speed": speed, "degrees": degrees, "seconds": seconds}
    # Omit the unused one so the controller sees exactly one of the pair.
    return dispatch_robot_tool(
        client, "rotate", {k: v for k, v in arguments.items() if v is not None}
    )


@mcp.tool()
def avoid_obstacles(duration: float, speed: float = 40, clearance_cm: float = 30) -> dict[str, Any]:
    """Move forward with ultrasonic obstacle detection; stop, turn right once, and stop."""
    return dispatch_robot_tool(
        client,
        "avoid_obstacles",
        {"duration": duration, "speed": speed, "clearance_cm": clearance_cm},
    )


@mcp.tool()
def sound_direction(samples: int = 5) -> dict[str, Any]:
    """Read hibot's ReSpeaker sound bearing without moving the chassis."""
    return dispatch_robot_tool(client, "sound_direction", {"samples": samples})


@mcp.tool()
def come_here(
    approach_duration: float = 2.0,
    clearance_cm: float = 45,
    samples: int = 5,
) -> dict[str, Any]:
    """Turn toward the latest sound bearing, approach briefly, and stop before obstacles."""
    return dispatch_robot_tool(
        client,
        "come_here",
        {
            "approach_duration": approach_duration,
            "clearance_cm": clearance_cm,
            "samples": samples,
        },
    )


@mcp.tool()
def set_led(red: int, green: int, blue: int, target: str = "board") -> dict[str, Any]:
    """Set RGB LEDs on the expansion board or ultrasonic sensor (target sonar)."""
    return dispatch_robot_tool(
        client,
        "set_led",
        {"red": red, "green": green, "blue": blue, "target": target},
    )


@mcp.tool()
def buzzer(frequency: int = 1900, on_time: float = 0.1, off_time: float = 0.1, repeat: int = 1) -> dict[str, Any]:
    """Sound the validated expansion-board buzzer."""
    return dispatch_robot_tool(
        client,
        "buzzer",
        {"frequency": frequency, "on_time": on_time, "off_time": off_time, "repeat": repeat},
    )


if __name__ == "__main__":
    mcp.run()
