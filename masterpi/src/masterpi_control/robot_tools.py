"""Shared HiBot tool definitions for MCP and OpenAI Realtime."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping

from .agent_client import HibotAgentClient


@dataclass(frozen=True)
class RobotToolDefinition:
    """One public robot tool and its loopback controller action."""

    name: str
    description: str
    action: str
    parameters: dict[str, Any]

    def openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": copy.deepcopy(self.parameters),
        }


def _object_schema(
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": False,
    }


_DURATION = {
    "type": "number",
    "minimum": 0.02,
    "maximum": 30,
    "default": 0.5,
}


ROBOT_TOOL_DEFINITIONS: tuple[RobotToolDefinition, ...] = (
    RobotToolDefinition(
        "get_state",
        "Read HiBot's current controller, chassis, arm, sensor, and error state without moving it.",
        "state",
        _object_schema(),
    ),
    RobotToolDefinition(
        "stop",
        "Immediately stop all HiBot chassis motion.",
        "stop",
        _object_schema(),
    ),
    RobotToolDefinition(
        "nod",
        "Perform HiBot's validated arm nod gesture, starting and ending at Home.",
        "nod",
        _object_schema(),
    ),
    RobotToolDefinition(
        "shake",
        "Perform HiBot's validated arm shake gesture, starting and ending at Home.",
        "shake",
        _object_schema(),
    ),
    RobotToolDefinition(
        "dance",
        "Start HiBot's bundled 30-second choreography with synchronized music. The action returns immediately after the dance process starts.",
        "dance",
        _object_schema(),
    ),
    RobotToolDefinition(
        "home_arm",
        "Move HiBot's arm to its documented Home pose.",
        "home",
        _object_schema(
            {"duration": {**_DURATION, "default": 1.5}},
        ),
    ),
    RobotToolDefinition(
        "check_front",
        "Move the camera arm to the check-front pose: servos 3=500, 4=2500, 5=1350, 6=1500.",
        "check_front",
        _object_schema(
            {"duration": {**_DURATION, "default": 0.8}},
        ),
    ),
    RobotToolDefinition(
        "set_servo",
        "Set one confirmed arm servo: 1 gripper, 3 top, 4 or 5 arm, or 6 base.",
        "servo",
        _object_schema(
            {
                "servo_id": {"type": "integer", "enum": [1, 3, 4, 5, 6]},
                "pulse": {"type": "integer", "minimum": 500, "maximum": 2500},
                "duration": dict(_DURATION),
            },
            ["servo_id", "pulse"],
        ),
    ),
    RobotToolDefinition(
        "set_gripper",
        "Open or close HiBot's gripper using its validated presets.",
        "gripper",
        _object_schema(
            {
                "opened": {"type": "boolean"},
                "duration": dict(_DURATION),
            },
            ["opened"],
        ),
    ),
    RobotToolDefinition(
        "grab_from_ground",
        "Unconditionally pick up an object from HiBot's fixed ground-level coordinate and return the arm Home while holding it.",
        "grab_from_ground",
        _object_schema(),
    ),
    RobotToolDefinition(
        "grab_from_front",
        "Unconditionally run the recorded front can-pickup servo pose used by the webpage's Grab can quick action, then return the arm Home while holding it.",
        "grab_from_front",
        _object_schema(),
    ),
    RobotToolDefinition(
        "analyze_camera",
        "Move to check-front, analyze the live camera with vision, and return detected objects and an annotated image.",
        "camera_analyze",
        _object_schema(
            {"samples": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3}},
        ),
    ),
    RobotToolDefinition(
        "analyze_camera_color",
        "Use the local color detector without moving the arm. Call only when the user explicitly requests color detection.",
        "camera_analyze_color",
        _object_schema(
            {"samples": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3}},
        ),
    ),
    RobotToolDefinition(
        "drive_for",
        "Drive using the webpage's four-wheel chassis mapping, then stop. Duration is capped at 8 seconds.",
        "drive_for",
        _object_schema(
            {
                "direction": {
                    "type": "string",
                    "enum": [
                        "forward",
                        "backward",
                        "left",
                        "right",
                        "rotate_left",
                        "rotate_right",
                    ],
                },
                "duration": {"type": "number", "minimum": 0.05, "maximum": 8, "default": 1},
                "speed": {"type": "number", "minimum": 40, "maximum": 100, "default": 40},
            },
            ["direction"],
        ),
    ),
    RobotToolDefinition(
        "avoid_obstacles",
        "Move forward with ultrasonic obstacle detection; stop, turn right once if blocked, and stop.",
        "avoid_obstacles",
        _object_schema(
            {
                "duration": {"type": "number", "minimum": 0.1, "maximum": 8},
                "speed": {"type": "number", "minimum": 40, "maximum": 100, "default": 40},
                "clearance_cm": {"type": "number", "minimum": 15, "maximum": 80, "default": 30},
            },
            ["duration"],
        ),
    ),
    RobotToolDefinition(
        "sound_direction",
        "Read HiBot's ReSpeaker sound bearing without moving the chassis.",
        "sound_direction",
        _object_schema(
            {"samples": {"type": "integer", "minimum": 1, "maximum": 15, "default": 5}},
        ),
    ),
    RobotToolDefinition(
        "come_here",
        "Turn toward the latest sound bearing, approach briefly, and stop before obstacles.",
        "come_here",
        _object_schema(
            {
                "approach_duration": {"type": "number", "minimum": 0.1, "maximum": 3, "default": 2},
                "clearance_cm": {"type": "number", "minimum": 25, "maximum": 100, "default": 45},
                "samples": {"type": "integer", "minimum": 1, "maximum": 15, "default": 5},
            },
        ),
    ),
    RobotToolDefinition(
        "set_led",
        "Set RGB LEDs on the expansion board or front ultrasonic sensor.",
        "rgb",
        _object_schema(
            {
                "red": {"type": "integer", "minimum": 0, "maximum": 255},
                "green": {"type": "integer", "minimum": 0, "maximum": 255},
                "blue": {"type": "integer", "minimum": 0, "maximum": 255},
                "target": {"type": "string", "enum": ["board", "sonar"], "default": "board"},
            },
            ["red", "green", "blue"],
        ),
    ),
    RobotToolDefinition(
        "buzzer",
        "Sound HiBot's expansion-board buzzer with bounded frequency and timing.",
        "buzzer",
        _object_schema(
            {
                "frequency": {"type": "integer", "minimum": 50, "maximum": 10000, "default": 1900},
                "on_time": {"type": "number", "minimum": 0.01, "maximum": 5, "default": 0.1},
                "off_time": {"type": "number", "minimum": 0, "maximum": 5, "default": 0.1},
                "repeat": {"type": "integer", "minimum": 1, "maximum": 20, "default": 1},
            },
        ),
    ),
)

_TOOLS_BY_NAME = {definition.name: definition for definition in ROBOT_TOOL_DEFINITIONS}


class RobotToolDispatcher:
    """Validate a model tool call and dispatch it through the loopback agent API."""

    def __init__(self, client: HibotAgentClient | None = None) -> None:
        self.client = client or HibotAgentClient()

    @property
    def schemas(self) -> list[dict[str, Any]]:
        return [definition.openai_schema() for definition in ROBOT_TOOL_DEFINITIONS]

    def dispatch(self, name: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        definition = _TOOLS_BY_NAME.get(name)
        if definition is None:
            raise ValueError(f"unknown HiBot tool: {name}")
        if arguments is None:
            supplied: dict[str, Any] = {}
        elif isinstance(arguments, Mapping):
            supplied = dict(arguments)
        else:
            raise ValueError(f"arguments for {name} must be a JSON object")

        schema = definition.parameters
        properties = schema["properties"]
        unexpected = sorted(set(supplied) - set(properties))
        if unexpected:
            raise ValueError(f"unexpected arguments for {name}: {', '.join(unexpected)}")
        missing = [key for key in schema.get("required", []) if key not in supplied]
        if missing:
            raise ValueError(f"missing required arguments for {name}: {', '.join(missing)}")

        payload = {}
        for key, property_schema in properties.items():
            if key in supplied:
                payload[key] = supplied[key]
            elif "default" in property_schema:
                payload[key] = property_schema["default"]
        action = definition.action
        if name == "set_led":
            target = str(payload.pop("target", "board")).strip().lower()
            action = "sonar_rgb" if target == "sonar" else "rgb"
        if payload:
            return self.client.call(action, payload)
        return self.client.call(action)


def dispatch_robot_tool(
    client: HibotAgentClient,
    name: str,
    arguments: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Dispatch one shared tool using the supplied client (useful to MCP wrappers)."""

    return RobotToolDispatcher(client).dispatch(name, arguments)
