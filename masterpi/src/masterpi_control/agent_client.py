"""Local, bounded HTTP client used by hibot's MCP tools."""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class AgentControlError(RuntimeError):
    """Raised when the local MasterPi agent-control API rejects a tool call."""


DEFAULT_ACTION_TIMEOUT_SECONDS = 15.0
DEFAULT_SLOW_ACTION_TIMEOUTS = {
    # Check-front positioning plus a remote vision model can legitimately take
    # much longer than a normal motor or sensor command.
    "camera_analyze": 180.0,
    # The guarded grab is several of those analyses plus chassis and arm
    # motion: a normal timeout would abandon a run already moving the robot.
    "grab_object": 300.0,
}


class HibotAgentClient:
    """Call only the loopback-only, validated agent-control routes."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        opener: Callable[..., Any] = urlopen,
        default_timeout: float = DEFAULT_ACTION_TIMEOUT_SECONDS,
        action_timeouts: Mapping[str, float] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._opener = opener
        self._default_timeout = float(default_timeout)
        self._action_timeouts = dict(DEFAULT_SLOW_ACTION_TIMEOUTS)
        if action_timeouts is not None:
            self._action_timeouts.update(
                {str(action): float(timeout) for action, timeout in action_timeouts.items()}
            )

    def call(self, action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        timeout = self._action_timeouts.get(action, self._default_timeout)
        body = json.dumps(payload or {}).encode("utf-8")
        request = Request(
            f"{self._base_url}/api/agent/{action}",
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with self._opener(request, timeout=timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            raw = exc.read()
            try:
                message = json.loads(raw).get("error", str(exc))
            except (UnicodeDecodeError, json.JSONDecodeError):
                message = str(exc)
            raise AgentControlError(str(message)) from exc
        except URLError as exc:
            raise AgentControlError(f"MasterPi controller is unavailable: {exc.reason}") from exc
        except TimeoutError as exc:
            raise AgentControlError(
                f"MasterPi {action} timed out after {timeout:g} seconds"
            ) from exc
        try:
            reply = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AgentControlError("MasterPi controller returned invalid JSON") from exc
        if not reply.get("ok"):
            raise AgentControlError(str(reply.get("error") or "MasterPi command failed"))
        result = reply.get("result")
        if not isinstance(result, dict):
            raise AgentControlError("MasterPi controller returned an invalid result")
        return result
