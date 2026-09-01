"""MCP server exposing hibot's bounded physical-control capabilities."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from .agent_client import HibotAgentClient

mcp = FastMCP("hibot")
client = HibotAgentClient()


@mcp.tool()
def get_state() -> dict[str, Any]:
    """Return hibot's current controller state without moving the robot."""
    return client.call("state")


@mcp.tool()
def stop() -> dict[str, Any]:
    """Immediately stop hibot's chassis motion."""
    return client.call("stop")


@mcp.tool()
def nod() -> dict[str, Any]:
    """Perform hibot's validated arm nod gesture, starting and ending at Home."""
    return client.call("nod")


@mcp.tool()
def shake() -> dict[str, Any]:
    """Perform hibot's validated arm shake gesture, starting and ending at Home."""
    return client.call("shake")


@mcp.tool()
def home_arm(duration: float = 1.5) -> dict[str, Any]:
    """Move hibot's arm to its documented Home pose."""
    return client.call("home", {"duration": duration})


@mcp.tool()
def set_servo(servo_id: int, pulse: int, duration: float = 0.5) -> dict[str, Any]:
    """Set a confirmed arm servo: 1 gripper, 3 top, 4, 5, or 6 base."""
    return client.call("servo", {"servo_id": servo_id, "pulse": pulse, "duration": duration})


@mcp.tool()
def set_gripper(opened: bool, duration: float = 0.5) -> dict[str, Any]:
    """Open or close hibot's gripper using its validated presets."""
    return client.call("gripper", {"opened": opened, "duration": duration})


@mcp.tool()
def recognize_and_grab() -> dict[str, Any]:
    """Unconditionally grab the object directly in front and return Home."""
    return client.call("grab", {})


@mcp.tool()
def analyze_camera(samples: int = 3) -> dict[str, Any]:
    """Analyze live camera frames and list stable colored objects without moving."""
    return client.call("camera_analyze", {"samples": samples})


@mcp.tool()
def drive_for(direction: str, speed: float, duration: float) -> dict[str, Any]:
    """Drive only in a named direction for a bounded interval (max 35% speed, 8 s)."""
    return client.call("drive_for", {"direction": direction, "speed": speed, "duration": duration})


@mcp.tool()
def avoid_obstacles(duration: float, speed: float = 20, clearance_cm: float = 30) -> dict[str, Any]:
    """Move forward with ultrasonic obstacle detection; stop, turn right once, and stop."""
    return client.call(
        "avoid_obstacles",
        {"duration": duration, "speed": speed, "clearance_cm": clearance_cm},
    )


@mcp.tool()
def set_led(red: int, green: int, blue: int, target: str = "board") -> dict[str, Any]:
    """Set RGB LEDs on the expansion board or ultrasonic sensor (target sonar)."""
    action = "sonar_rgb" if target.strip().lower() == "sonar" else "rgb"
    return client.call(action, {"red": red, "green": green, "blue": blue})


@mcp.tool()
def buzzer(frequency: int = 1900, on_time: float = 0.1, off_time: float = 0.1, repeat: int = 1) -> dict[str, Any]:
    """Sound the validated expansion-board buzzer."""
    return client.call(
        "buzzer",
        {"frequency": frequency, "on_time": on_time, "off_time": off_time, "repeat": repeat},
    )


if __name__ == "__main__":
    mcp.run()
