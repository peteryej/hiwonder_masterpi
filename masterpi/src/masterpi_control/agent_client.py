"""Local, bounded HTTP client used by hibot's MCP tools."""

from __future__ import annotations

import json
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class AgentControlError(RuntimeError):
    """Raised when the local MasterPi agent-control API rejects a tool call."""


class HibotAgentClient:
    """Call only the loopback-only, validated agent-control routes."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._opener = opener

    def call(self, action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = json.dumps(payload or {}).encode("utf-8")
        request = Request(
            f"{self._base_url}/api/agent/{action}",
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with self._opener(request, timeout=15) as response:
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
