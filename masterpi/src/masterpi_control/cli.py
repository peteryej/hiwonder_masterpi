"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from typing import Any, Optional, Sequence

from .backends import BackendUnavailable, MockBackend, VendorBackend
from .camera import CameraStream, CameraUnavailable
from .robot import Robot, RobotError, ValidationError
from .server import serve
from .vision import SUPPORTED_COLORS, VisionGrasper


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="masterpi", description="Control a Hiwonder MasterPi robot"
    )
    parser.add_argument("--mock", action="store_true", help="use simulated hardware")
    sub = parser.add_subparsers(dest="command", required=True)

    web = sub.add_parser("serve", help="run the browser control panel")
    web.add_argument("--host", default="0.0.0.0")
    web.add_argument("--port", type=int, default=8000)
    web.add_argument("--watchdog", type=float, default=0.6, help="motion timeout in seconds")
    web.add_argument("--tls-port", type=int, help="also serve HTTPS on this port")
    web.add_argument("--certfile", help="HTTPS server certificate PEM")
    web.add_argument("--keyfile", help="HTTPS private key PEM")
    web.add_argument("--ca-certfile", help="CA certificate exposed for client installation")

    drive = sub.add_parser("drive", help="drive briefly, then stop")
    drive.add_argument("speed", type=float, help="linear speed, 0..100 mm/s")
    drive.add_argument("direction", type=float, help="0 right, 90 forward, 180 left, 270 back")
    drive.add_argument("--angular", type=float, default=0, help="yaw rate, -2..2 rad/s")
    drive.add_argument("--seconds", type=float, default=1.0, help="duration before automatic stop")

    sub.add_parser("stop", help="stop all chassis motors")
    sub.add_parser("home", help="move the arm to the tutorial home pose")

    arm = sub.add_parser("arm", help="move the gripper to an XYZ coordinate")
    arm.add_argument("x", type=float)
    arm.add_argument("y", type=float)
    arm.add_argument("z", type=float)
    arm.add_argument("--pitch", type=float, default=0)
    arm.add_argument("--pitch-min", type=float, default=-90)
    arm.add_argument("--pitch-max", type=float, default=90)
    arm.add_argument("--seconds", type=float, default=1.0)

    servo = sub.add_parser("servo", help="set one PWM servo pulse")
    servo.add_argument("servo_id", type=int, choices=range(1, 7))
    servo.add_argument("pulse", type=int, help="pulse width, 500..2500")
    servo.add_argument("--seconds", type=float, default=0.5)

    gripper = sub.add_parser("gripper", help="open or close the gripper")
    gripper.add_argument("position", choices=("open", "close"))
    gripper.add_argument("--seconds", type=float, default=0.5)

    grab = sub.add_parser("grab", help="recognize and grab a centered colored object")
    grab.add_argument("target", nargs="?", default="any", choices=("any", *SUPPORTED_COLORS))

    rgb = sub.add_parser("rgb", help="set both expansion-board RGB LEDs")
    rgb.add_argument("red", type=int)
    rgb.add_argument("green", type=int)
    rgb.add_argument("blue", type=int)

    buzzer = sub.add_parser("buzzer", help="sound the expansion-board buzzer")
    buzzer.add_argument("--frequency", type=int, default=1900)
    buzzer.add_argument("--on", type=float, default=0.1)
    buzzer.add_argument("--off", type=float, default=0.1)
    buzzer.add_argument("--repeat", type=int, default=1)

    sub.add_parser("diagnose", help="show the detected vendor modules")
    return parser


def _backend(mock: bool) -> Any:
    return MockBackend() if mock else VendorBackend()


def _print(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    robot: Optional[Robot] = None
    try:
        backend = _backend(args.mock)
        if args.command == "diagnose":
            _print(getattr(backend, "details", {"name": backend.name}))
            return 0

        watchdog = args.watchdog if args.command == "serve" else 10.0
        robot = Robot(backend, watchdog_timeout=watchdog)
        if args.command == "serve":
            if args.tls_port and (not args.certfile or not args.keyfile):
                raise ValidationError("--tls-port requires --certfile and --keyfile")
            serve(
                robot,
                args.host,
                args.port,
                tls_port=args.tls_port,
                certfile=args.certfile,
                keyfile=args.keyfile,
                ca_certfile=args.ca_certfile,
            )
            return 0
        if args.command == "drive":
            if not 0 < args.seconds <= 60:
                raise ValidationError("seconds must be between 0 and 60")
            result = robot.drive(args.speed, args.direction, args.angular)
            try:
                time.sleep(args.seconds)
            finally:
                robot.stop()
            result["seconds"] = args.seconds
        elif args.command == "stop":
            result = robot.stop()
        elif args.command == "home":
            result = robot.home()
        elif args.command == "arm":
            result = robot.arm(
                args.x,
                args.y,
                args.z,
                args.pitch,
                args.pitch_min,
                args.pitch_max,
                args.seconds,
            )
        elif args.command == "servo":
            result = robot.servo(args.servo_id, args.pulse, args.seconds)
        elif args.command == "gripper":
            result = robot.gripper(args.position == "open", args.seconds)
        elif args.command == "grab":
            camera = CameraStream()
            try:
                result = VisionGrasper(robot, camera).recognize_and_grab(args.target)
            finally:
                camera.close()
        elif args.command == "rgb":
            result = robot.rgb(args.red, args.green, args.blue)
        elif args.command == "buzzer":
            result = robot.buzzer(args.frequency, args.on, args.off, args.repeat)
        else:  # argparse guarantees this is unreachable.
            raise AssertionError(args.command)
        _print(result)
        return 0
    except KeyboardInterrupt:
        return 130
    except (BackendUnavailable, CameraUnavailable, RobotError, ValidationError, ValueError, OSError) as exc:
        print(f"masterpi: {exc}", file=sys.stderr)
        return 2
    finally:
        if robot is not None:
            robot.close()
