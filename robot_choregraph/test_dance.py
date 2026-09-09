"""Offline checks only: fake clock/transport and vendor mathematical IK.

Run: python3 -m unittest -v
No server, board imports, sockets, or physical commands are used.
"""

import ast
import contextlib
import copy
import importlib.util
import io
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import dance


class FakePlayback:
    def __init__(self, latency=0):
        self.now = 0
        self.calls = []
        self.latency = latency

    def clock(self):
        return self.now

    def sleep(self, seconds):
        assert seconds >= 0
        self.now += seconds

    def post(self, action, body):
        self.calls.append((self.now, action, body))
        self.now += self.latency

    def run(self, events, post=None):
        dance.play(events, post or self.post, self.clock, self.sleep, lambda _: None)


class FakeAudio:
    def __init__(self, playback):
        self.playback = playback
        self.calls = []

    def start(self):
        self.calls.append((self.playback.now, "start"))

    def wait_until_available(self):
        self.calls.append((self.playback.now, "available"))

    def wait(self, timeout):
        self.calls.append((self.playback.now, "wait", timeout))

    def stop(self):
        self.calls.append((self.playback.now, "stop"))


class DanceTests(unittest.TestCase):
    def setUp(self):
        self.score = dance.load_score(dance.SCORE)
        self.events = dance.compile_score(self.score)

    def test_default_preview_cannot_connect(self):
        with patch.object(dance, "Controller", side_effect=AssertionError("network")), \
                patch.object(dance, "AudioPlayer", side_effect=AssertionError("audio")):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(dance.main([]), 0)
        self.assertIn("30.00s", output.getvalue())

    def test_audio_starts_at_go_and_drains_after_final_cue(self):
        fake = FakePlayback()
        audio = FakeAudio(fake)
        dance.play(self.events, fake.post, fake.clock, fake.sleep, lambda _: None, audio)
        self.assertEqual(audio.calls, [
            (4.5, "start"),
            (34.5, "wait", 1.0),
            (34.5, "stop"),
        ])

    def test_agent_mode_waits_for_audio_before_go(self):
        fake = FakePlayback()
        audio = FakeAudio(fake)
        dance.play(self.events, fake.post, fake.clock, fake.sleep, lambda _: None,
                   audio, wait_for_audio=True)
        self.assertEqual(audio.calls[:2], [
            (4.5, "available"),
            (4.5, "start"),
        ])

    def test_audio_stops_if_motion_fails(self):
        fake = FakePlayback()
        audio = FakeAudio(fake)
        def failing_post(action, body):
            fake.post(action, body)
            if action == "drive":
                raise dance.DanceError("injected failure")
        with self.assertRaises(dance.DanceError):
            dance.play(self.events, failing_post, fake.clock, fake.sleep,
                       lambda _: None, audio)
        self.assertEqual(audio.calls[-1][1], "stop")

    def test_audio_player_decodes_mp4_to_respeaker(self):
        decoder = unittest.mock.MagicMock()
        decoder.stdout = unittest.mock.MagicMock()
        decoder.wait.return_value = 0
        decoder.poll.return_value = 0
        player = unittest.mock.MagicMock()
        player.wait.return_value = 0
        player.poll.return_value = 0
        popen = unittest.mock.MagicMock(side_effect=[decoder, player])
        with patch.object(dance.shutil, "which", return_value="/usr/bin/tool"):
            audio = dance.AudioPlayer(dance.VIDEO, popen=popen)
        audio.start()
        audio.wait()
        decoder_command = popen.call_args_list[0].args[0]
        player_command = popen.call_args_list[1].args[0]
        self.assertEqual(decoder_command[0], "ffmpeg")
        self.assertIn(str(dance.VIDEO), decoder_command)
        self.assertEqual(player_command[:5], [
            "aplay", "-q", "-D", dance.DEFAULT_AUDIO_DEVICE, "-t",
        ])
        self.assertIs(popen.call_args_list[1].kwargs["stdin"], decoder.stdout)
        decoder.stdout.close.assert_called_once_with()

    def test_audio_follows_slow_choreography_tempo(self):
        with patch.object(dance.shutil, "which", return_value="/usr/bin/tool"):
            audio = dance.AudioPlayer(dance.VIDEO, tempo=0.25,
                                      popen=unittest.mock.MagicMock())
        self.assertEqual(audio.tempo_filter(), "atempo=0.5,atempo=0.5")

    def test_audio_availability_waits_for_two_successful_probes(self):
        fake = FakePlayback()
        busy = SimpleNamespace(returncode=1, stderr="Device or resource busy", stdout="")
        ready = SimpleNamespace(returncode=0, stderr="", stdout="")
        runner = unittest.mock.MagicMock(side_effect=[busy, ready, ready])
        with patch.object(dance.shutil, "which", return_value="/usr/bin/tool"):
            audio = dance.AudioPlayer(
                dance.VIDEO,
                runner=runner,
                clock=fake.clock,
                sleeper=fake.sleep,
            )
        audio.wait_until_available(timeout=5, settle=0.25)
        self.assertEqual(runner.call_count, 3)
        self.assertEqual(fake.now, 0.5)

    def test_execute_requires_explicit_address(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            dance.main(["--execute"])

    def test_exact_duration_and_heartbeat_with_latency(self):
        for latency in (0, 0.025, 0.06):
            with self.subTest(latency=latency):
                fake = FakePlayback(latency)
                fake.run(self.events)
                origin = 4.5 + 3 * latency  # Three setup requests.
                final_stop = fake.calls[-2]
                self.assertEqual(final_stop[1], "stop")
                # One in-flight heartbeat may finish just after a cue deadline;
                # request latency must not accumulate across the whole score.
                self.assertGreaterEqual(final_stop[0] - origin, 30 - 1e-9)
                self.assertLessEqual(final_stop[0] - origin, 30 + latency + 1e-9)
                last_drive = None
                for at, action, body in fake.calls:
                    if action in ("drive", "stop"):
                        if last_drive is not None:
                            self.assertLess(at - last_drive, 0.6)
                        last_drive = at if action == "drive" else None

    def test_arm_only_suppresses_all_driving(self):
        fake = FakePlayback()
        fake.run(dance.compile_score(self.score, arm_only=True))
        self.assertNotIn("drive", [action for _, action, _ in fake.calls])
        self.assertIn("arm", [action for _, action, _ in fake.calls])

    def test_slow_tempo_preserves_nominal_displacement(self):
        slow = dance.compile_score(self.score, tempo=0.5)
        self.assertEqual(slow[-1][0], 60)
        for normal, slowed in zip(self.events, slow):
            self.assertEqual(slowed[0], 2 * normal[0])
            for (action, body), (_, other) in zip(normal[2], slowed[2]):
                if action == "drive":
                    self.assertEqual(other["speed"], body["speed"] / 2)
                    self.assertEqual(other["angular_rate"], body["angular_rate"] / 2)
                if "duration" in body:
                    self.assertEqual(other["duration"], body["duration"] * 2)

    def test_failure_and_interrupt_stop_without_more_motion(self):
        for failure in (dance.DanceError("injected failure"), KeyboardInterrupt()):
            fake = FakePlayback()
            def failing_post(action, body):
                fake.post(action, body)
                if action == "drive":
                    raise failure
            with self.assertRaises(type(failure)):
                fake.run(self.events, failing_post)
            self.assertEqual(fake.calls[-1][1], "stop")

    def test_setup_failure_still_stops(self):
        fake = FakePlayback()
        def failing_post(action, body):
            fake.post(action, body)
            if action == "home":
                raise dance.DanceError("unreachable")
        with self.assertRaises(dance.DanceError):
            fake.run(self.events, failing_post)
        self.assertEqual([c[1] for c in fake.calls], ["stop", "home", "stop"])

    def test_stall_aborts_without_replaying_missed_cues(self):
        fake = FakePlayback()
        def slow_post(action, body):
            fake.post(action, body)
            if action == "drive":
                fake.now += 0.8
        with self.assertRaises(dance.DanceError):
            fake.run(self.events, slow_post)
        self.assertEqual(fake.calls[-1][1], "stop")
        self.assertEqual(sum(c[1] == "drive" for c in fake.calls), 1)

    def test_cleanup_failure_is_reported(self):
        fake = FakePlayback()
        end_stops = 0
        def failing_stop(action, body):
            nonlocal end_stops
            fake.post(action, body)
            if fake.now >= 34.5 and action == "stop":
                end_stops += 1
                if end_stops == 2:
                    raise dance.DanceError("stop unavailable")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(dance.DanceError):
            fake.run(self.events, failing_stop)

    def test_validation_rejects_bad_parameters(self):
        for kwargs in ({"tempo": 0}, {"tempo": float("nan")}, {"motion_scale": 2}):
            with self.assertRaises(dance.DanceError):
                dance.compile_score(self.score, **kwargs)
        bad = copy.deepcopy(self.score)
        bad["cues"][1]["move"] = 1.0
        with patch("pathlib.Path.read_text", return_value=dance.json.dumps(bad)):
            with self.assertRaisesRegex(dance.DanceError, "Overlapping"):
                dance.load_score("unused.json")
        bad = copy.deepcopy(self.score)
        bad["cues"][0]["drive"] = [60, 270, 0.7]
        with patch("pathlib.Path.read_text", return_value=dance.json.dumps(bad)):
            with self.assertRaisesRegex(dance.DanceError, "Wheel duty"):
                dance.load_score("unused.json")

    def test_controller_serializes_existing_api_and_rejects_errors(self):
        controller = dance.Controller("http://robot.example:8000")
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok":true,"result":{}}'
        with patch.object(controller.opener, "open", return_value=response) as opening:
            controller.post("drive", {"speed": 60, "direction": 90, "angular_rate": 0})
            request = opening.call_args.args[0]
            self.assertEqual(request.full_url, "http://robot.example:8000/api/drive")
            self.assertEqual(request.method, "POST")
            self.assertEqual(dance.json.loads(request.data)["speed"], 60)
            response.__enter__.return_value.read.return_value = b'{"ok":false,"error":"unreachable"}'
            with self.assertRaises(dance.DanceError):
                controller.post("arm", {})

    def test_vendor_ik_and_calibrated_servo_limits(self):
        root = Path(__file__).resolve().parent.parent / "hiwonder_masterpi" / "MasterPi"
        kinematics = root / "masterpi_sdk/kinematics_sdk/kinematics"
        if not kinematics.is_dir():
            self.skipTest("Neighboring vendor source is required for this offline IK check")
        # inversekinematics.py only imports math/logging; never import a board.
        spec = importlib.util.spec_from_file_location("vendor_math", kinematics / "inversekinematics.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        ik = module.IK("arm")
        ik.setLinkLength(L1=ik.l1 + 1.3, L4=ik.l4)
        # Extract the unmodified vendor class, avoiding top-level YAML/NumPy
        # imports. Its integer pitch search uses range as np.arange's stand-in.
        tree = ast.parse((kinematics / "arm_move_ik.py").read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ArmIK")
        namespace = dict(ik=ik, np=SimpleNamespace(arange=range), logger=logging.getLogger("IK"))
        exec(compile(ast.Module(body=[cls], type_ignores=[]), "vendor_class", "exec"), namespace)
        arm = namespace["ArmIK"]()
        offsets = {}
        for line in (root / "Deviation.yaml").read_text().splitlines():
            key, value = line.split(":", 1)
            offsets[int(key.strip("'\" "))] = int(value.strip())
        for name, (x, y, z, pitch) in self.score["poses"].items():
            with self.subTest(pose=name):
                candidates = [result for result in (
                    arm.setPitchRange((x, y, z), pitch, -90),
                    arm.setPitchRange((x, y, z), pitch, 90)) if result is not False]
                self.assertTrue(candidates, f"Unreachable: {name}")
                pulses, actual_pitch = min(candidates, key=lambda result: abs(result[1] - pitch))
                self.assertEqual(actual_pitch, pitch)
                for servo, pulse in pulses.items():
                    calibrated = pulse + offsets[int(servo[-1])]
                    self.assertGreaterEqual(calibrated, 500)
                    self.assertLessEqual(calibrated, 2500)


if __name__ == "__main__":
    unittest.main()
