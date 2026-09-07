#!/usr/bin/env python3
"""Video-timed MasterPi dance with synchronized audio.

Default: print commands, without any connection or audio playback.

Python 3.9+, standard library only. See dance_progress.md for the video mapping
and execution instructions. The existing MasterPi server owns the hardware.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler


SCORE = Path(__file__).with_name("dance_score.json")
VIDEO = Path(__file__).with_name("dance_move.mp4")
DEFAULT_AUDIO_DEVICE = "plughw:CARD=ArrayUAC10,DEV=0"
HEARTBEAT = 0.15  # Existing controller watchdog: 0.6 seconds.
MAX_LATENESS = 0.35


class DanceError(RuntimeError):
    pass


class AudioPlayer:
    """Decode the MP4 audio track into the ReSpeaker ALSA output."""

    def __init__(self, path, device=DEFAULT_AUDIO_DEVICE, tempo=1.0,
                 popen=subprocess.Popen):
        self.path = Path(path)
        self.device = device
        self.tempo = number(tempo, 0.25, 1, "tempo")
        self._popen = popen
        self.decoder = None
        self.player = None
        if not self.path.is_file():
            raise DanceError(f"Audio source not found: {self.path}")
        for command in ("ffmpeg", "aplay"):
            if shutil.which(command) is None:
                raise DanceError(f"Audio playback requires {command} on PATH")

    def tempo_filter(self):
        """Build legal 0.5..100 atempo stages for the supported 0.25..1 range."""
        remaining = self.tempo
        factors = []
        while remaining < 0.5:
            factors.append(0.5)
            remaining /= 0.5
        factors.append(remaining)
        return ",".join(f"atempo={factor:g}" for factor in factors)

    def start(self):
        if self.decoder is not None or self.player is not None:
            raise DanceError("Audio playback was already started")
        try:
            self.decoder = self._popen(
                [
                    "ffmpeg", "-nostdin", "-v", "error", "-i", str(self.path),
                    "-vn", "-filter:a", self.tempo_filter(),
                    "-f", "s16le", "-acodec", "pcm_s16le",
                    "-ar", "48000", "-ac", "2", "pipe:1",
                ],
                stdout=subprocess.PIPE,
            )
            self.player = self._popen(
                [
                    "aplay", "-q", "-D", self.device, "-t", "raw",
                    "-f", "S16_LE", "-c", "2", "-r", "48000",
                ],
                stdin=self.decoder.stdout,
            )
            # Only aplay owns this pipe end now. Closing the parent's copy lets
            # ffmpeg receive SIGPIPE if aplay exits early.
            if self.decoder.stdout is not None:
                self.decoder.stdout.close()
        except OSError as exc:
            self.stop()
            raise DanceError(f"Could not start dance audio: {exc}") from exc

    def wait(self, timeout=1.0):
        if self.player is None or self.decoder is None:
            return
        try:
            player_status = self.player.wait(timeout=timeout)
            decoder_status = self.decoder.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise DanceError("Dance audio did not finish with the choreography") from exc
        if player_status != 0 or decoder_status != 0:
            raise DanceError(
                f"Dance audio failed (ffmpeg={decoder_status}, aplay={player_status})"
            )

    def stop(self):
        for process in (self.player, self.decoder):
            if process is not None and process.poll() is None:
                process.terminate()
        for process in (self.player, self.decoder):
            if process is None or process.poll() is not None:
                continue
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def number(value, low, high, label):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise DanceError(f"{label} must be a number")
    if not math.isfinite(value) or not low <= value <= high:
        raise DanceError(f"{label} must be within {low}..{high}")
    return value


def load_score(path):
    score = json.loads(Path(path).read_text())
    duration = number(score["duration"], 1, 120, "duration")
    poses = score["poses"]
    for name, pose in poses.items():
        if len(pose) != 4:
            raise DanceError(f"Pose {name}: expected [x, y, z, pitch]")
        for value, low, high in zip(pose, (-15, 3, 8, -60), (15, 20, 25, 75)):
            number(value, low, high, f"pose {name}")
    previous = -1
    busy_until = {"arm": 0, "gripper": 0}
    cues = score["cues"]
    if not cues or cues[0]["at"] != 0 or cues[-1]["at"] != duration:
        raise DanceError("Score must start at zero and end at its duration")
    for cue in cues:
        at = number(cue["at"], 0, duration, "cue time")
        if at <= previous:
            raise DanceError("Cue times must be strictly increasing")
        previous = at
        if not set(cue) <= {"at", "label", "arm", "move", "gripper", "grip_time", "drive"}:
            raise DanceError(f"Unknown cue fields at {at}")
        for channel, duration_key, default in (("arm", "move", 0.4), ("gripper", "grip_time", 0.2)):
            if channel in cue:
                seconds = number(cue.get(duration_key, default), 0.02, 3, duration_key)
                if at < busy_until[channel] - 1e-9 or at + seconds > duration + 1e-9:
                    raise DanceError(f"Overlapping or unfinished {channel} motion at {at}")
                busy_until[channel] = at + seconds
        if "arm" in cue and cue["arm"] not in poses:
            raise DanceError(f"Unknown arm pose: {cue['arm']}")
        if "gripper" in cue and type(cue["gripper"]) is not bool:
            raise DanceError("gripper must be true (open) or false (closed)")
        if "drive" in cue:
            if len(cue["drive"]) != 3:
                raise DanceError("drive must contain [speed, direction, angular_rate]")
            for value, low, high in zip(cue["drive"], (0, 0, -2), (100, 360, 2)):
                number(value, low, high, "drive")
            # The bundled mixer forwards these values directly as motor duty.
            speed, direction, yaw = cue["drive"]
            radians = math.radians(direction)
            peak = speed * (abs(math.sin(radians)) + abs(math.cos(radians))) + 126 * abs(yaw)
            if peak > 100 + 1e-9:
                raise DanceError(f"Wheel duty exceeds 100 at {at}s; reduce speed or angular_rate")
    if cues[-1].get("drive") != [0, 0, 0]:
        raise DanceError("Final cue must stop the chassis")
    return score


def compile_score(score, tempo=1.0, motion_scale=1.0, arm_only=False):
    """Keep wall-clock motion integrals unchanged when slowing the tempo."""
    number(tempo, 0.25, 1, "tempo")
    number(motion_scale, 0.25, 1, "motion scale")
    events = []
    for cue in score["cues"]:
        commands = []
        # Stop/drive first, then nonblocking arm and gripper transitions.
        if "drive" in cue:
            speed, direction, yaw = cue["drive"]
            factor = motion_scale * tempo
            if arm_only or (speed == 0 and yaw == 0):
                commands.append(("stop", {}))
            else:
                commands.append(("drive", dict(speed=speed * factor, direction=direction,
                                               angular_rate=yaw * factor)))
        if "arm" in cue:
            x, y, z, pitch = score["poses"][cue["arm"]]
            commands.append(("arm", dict(x=x, y=y, z=z, pitch=pitch,
                                         pitch_min=-90, pitch_max=90,
                                         duration=cue.get("move", 0.4) / tempo)))
        if "gripper" in cue:
            commands.append(("gripper", dict(opened=cue["gripper"],
                                             duration=cue.get("grip_time", 0.2) / tempo)))
        events.append((cue["at"] / tempo, cue.get("label", ""), commands))
    return events


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise DanceError("Controller redirected a command; use its direct URL")


class Controller:
    def __init__(self, url):
        parsed = urlsplit(url)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ("", "/")):
            raise DanceError("--url must be a controller origin, e.g. http://192.168.149.1:8000")
        self.url = url.rstrip("/")
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def post(self, action, body):
        request = Request(self.url + "/api/" + action,
                          data=json.dumps(body).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
        try:
            with self.opener.open(request, timeout=0.25) as response:
                result = json.loads(response.read())
        except HTTPError as exc:
            detail = exc.read(2048).decode(errors="replace")
            raise DanceError(f"{action}: HTTP {exc.code}: {detail}") from exc
        except (URLError, OSError, ValueError) as exc:
            raise DanceError(f"{action}: {exc}") from exc
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise DanceError(f"{action} rejected: {result}")


def play(events, post, clock=time.monotonic, sleep=time.sleep, log=print, audio=None):
    """Absolute deadlines; abort stalled playback instead of rushing old cues.

    No retry of motion commands: an HTTP failure may follow partial execution.
    Stop is attempted even when setup, playback, or an interrupt fails. The
    remote watchdog is the fallback if the connection is lost.
    """
    try:
        post("stop", {})
        post("home", {"duration": 1.5})
        post("gripper", {"opened": False, "duration": 0.3})
        sleep(1.5)
        for count in (3, 2, 1):
            log(f"Starting in {count}…")
            sleep(1)
        log("GO — video/audio time 0.00")
        if audio is not None:
            audio.start()
        origin = clock()
        index = 0
        drive = None
        refresh_at = float("inf")
        while index < len(events):
            now = clock()
            at, label, commands = events[index]
            deadline = origin + at
            if now >= deadline:
                if now - deadline > MAX_LATENESS:
                    raise DanceError(f"Playback fell {now - deadline:.2f}s behind at {at:.2f}s")
                log(f"{at:6.2f}s  {label}")
                for action, body in commands:
                    if clock() - deadline > MAX_LATENESS:
                        raise DanceError("Controller too slow to keep choreography timing")
                    sent_at = clock()
                    post(action, body)
                    if action == "drive":
                        drive = body
                        refresh_at = sent_at + HEARTBEAT
                    elif action == "stop":
                        drive = None
                        refresh_at = float("inf")
                index += 1
            elif drive is not None and now >= refresh_at:
                if now - refresh_at > MAX_LATENESS:
                    raise DanceError("Drive heartbeat delayed; stopping playback")
                post("drive", drive)
                refresh_at = now + HEARTBEAT
            else:
                sleep(max(0, min(deadline, refresh_at) - now))
        if audio is not None:
            # The MP4 is 0.01 seconds longer than the rounded score. Let its
            # final samples drain instead of clipping them at the last cue.
            audio.wait(timeout=1.0)
    finally:
        already_failing = sys.exc_info()[0] is not None
        try:
            post("stop", {})
        except Exception as exc:
            print(f"Stop request failed; controller watchdog must stop wheels: {exc}", file=sys.stderr)
            if not already_failing:
                raise
        finally:
            if audio is not None:
                audio.stop()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--score", type=Path, default=SCORE)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="send commands to the given controller")
    mode.add_argument("--dry-run", action="store_true", help="print timeline only (default)")
    parser.add_argument("--url", help="required with --execute; no default robot address")
    parser.add_argument("--tempo", type=float, default=1, help="0.25..1; 0.5 doubles runtime")
    parser.add_argument("--motion-scale", type=float, default=1, help="0.25..1; scales chassis speeds only")
    parser.add_argument("--arm-only", action="store_true", help="suppress all chassis motion")
    parser.add_argument("--no-audio", action="store_true", help="execute without the MP4 audio track")
    parser.add_argument("--audio-device", default=DEFAULT_AUDIO_DEVICE,
                        help="ALSA playback device (default: ReSpeaker output)")
    args = parser.parse_args(argv)
    if args.execute and not args.url:
        parser.error("--execute requires --url")
    try:
        score = load_score(args.score)
        events = compile_score(score, args.tempo, args.motion_scale, args.arm_only)
        if not args.execute:
            print("DRY RUN — no network, hardware, or audio; setup: home 1.5s, countdown 3s")
            for at, label, commands in events:
                print(f"{at:6.2f}s  {label}")
                for action, body in commands:
                    print(f"         POST /api/{action} {json.dumps(body, sort_keys=True)}")
            print(f"Duration: {events[-1][0]:.2f}s (+ setup). Drive refreshes every {HEARTBEAT}s in execution.")
            return 0
        controller = Controller(args.url)
        audio = None if args.no_audio else AudioPlayer(
            VIDEO, args.audio_device, tempo=args.tempo
        )
        previous = signal.getsignal(signal.SIGTERM)
        def interrupt(signum, frame):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, interrupt)
        try:
            play(events, controller.post, audio=audio)
        finally:
            signal.signal(signal.SIGTERM, previous)
        return 0
    except KeyboardInterrupt:
        print("Dance interrupted; chassis stop requested.", file=sys.stderr)
        return 130
    except (DanceError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Dance failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
