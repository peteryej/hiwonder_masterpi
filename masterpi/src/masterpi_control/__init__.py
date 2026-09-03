"""High-level controls for the Hiwonder MasterPi robot."""

from __future__ import annotations

from typing import Any

__all__ = ["MockBackend", "Robot", "RobotError", "ValidationError", "VendorBackend"]
__version__ = "0.1.0"


def __getattr__(name: str) -> Any:
    """Load hardware-facing exports only when callers request them."""

    if name in {"MockBackend", "VendorBackend"}:
        from .backends import MockBackend, VendorBackend

        return {"MockBackend": MockBackend, "VendorBackend": VendorBackend}[name]
    if name in {"Robot", "RobotError", "ValidationError"}:
        from .robot import Robot, RobotError, ValidationError

        return {
            "Robot": Robot,
            "RobotError": RobotError,
            "ValidationError": ValidationError,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
