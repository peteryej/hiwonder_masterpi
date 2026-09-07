# Dance program progress

## 2026-09-07

- Added synchronized playback of the AAC audio track embedded in
  `dance_move.mp4`. Execution now starts FFmpeg-to-ALSA audio at `GO`, alongside
  video time zero; dry-run remains silent.
- Audio is decoded to 48 kHz stereo PCM and played through the ReSpeaker output
  `plughw:CARD=ArrayUAC10,DEV=0`. Both `ffmpeg` and `aplay` are available on the
  robot, and ALSA confirms the ReSpeaker as playback card 3, device 0.
- Playback drains the MP4's final 0.01 seconds after the rounded 20-second score
  and is stopped on completion, Ctrl+C, controller failure, or audio failure.
- `--tempo` now slows the soundtrack by the same factor as the choreography;
  FFmpeg chains valid `atempo` filters for the full supported 0.25–1 range.
- Added `--no-audio` for silent execution and `--audio-device` for overriding
  the ALSA destination.
- Verification passes 15 offline tests plus Python compilation; the optional
  vendor-source IK test is skipped because its expected SDK path is absent. No
  physical motion or speaker playback was triggered during verification.

## 2026-09-05

- Confirmed `dance_move.mp4` is 20.010 seconds, 1280×720, 24 fps, with audio.
- Inspected 80 frames at quarter-second intervals across the full clip, plus
  overview contact sheets, to identify arm gestures, gripper pulses, chassis
  turns, and forward/backward phrases. Timing is a manual visual transcription.
- Read `../hiwonder_masterpi/MasterPi/src/masterpi_control/{robot,server,backends}.py`
  and the vendor servo/kinematics demos. The root README refers to an older
  `masterpi/` layout; the actual controller is under `MasterPi/`.
- Selected the existing HTTP API (`/api/drive`, `/api/stop`, `/api/arm`,
  `/api/gripper`, `/api/home`). Coordinates are cm; API durations are seconds.
  Chassis headings: right 0°, forward 90°, left 180°, backward 270°;
  negative angular rate turns left, positive turns right.
- Finished `dance.py` (Python 3.9+, standard library only) and
  `dance_score.json` (41 cues, nine named poses, 20-second choreography).
- Implemented dry-run by default, explicit execution option, absolute playback
  deadlines, 0.15-second drive refreshes for the controller's 0.6-second
  watchdog, and chassis stop on completion, Ctrl+C, SIGTERM, or failure.
- Added `--arm-only`, `--tempo`, and `--motion-scale` for testing and tuning.
- Offline IK verification caught candidate poses whose nominal servo targets
  became larger than 2500 after applying `Deviation.yaml`. Adjusted the pitch
  of those poses; all nine now solve at the requested pitch and remain within
  500–2500 after applying the supplied offsets (3: +54, 4: +53, 5: +89, 6: +64).
- Inspected the chassis mixer: it sends mixed wheel values directly to motor
  duty commands. Bounded the score to an absolute duty of 100 per wheel and
  added validation for combined translation/rotation, which can exceed that
  limit even when individual API parameters pass validation.
- Verification complete: `python3 -m unittest -v` passes all 12 tests;
  `python3 -m py_compile dance.py test_dance.py` passes. Checked normal preview
  and the 40-second arm-only preview at half tempo.
- No robot connections or physical commands have been made.

## Video-to-program mapping

Directions described as screen left/right refer to the recorded camera view.
Arm coordinates and chassis headings in the score are robot-relative.

| Video time | Observed phrase | Program interpretation |
| --- | --- | --- |
| 0–1.75 s | Compact front pose, raised claw and small waves | Alternating arm X positions and open claw |
| 1.75–3.75 s | Fast alternating side-facing chassis turns | Left/right/left yaw pulses, ending with raised claw |
| 3.75–5.75 s | Approach camera, arm waves across front | Short forward drive, stop, alternating arm poses |
| 5.75–7.25 s | Low diagonal reach and claw pulses | Low-right pose with close/open/close |
| 7.25–9.35 s | Pointing arm, chassis sway, retreat | Raised reach, opposing yaw pulses, backward drive |
| 9.35–11.5 s | High salute, then side wave and center | Salute, left wave, home |
| 11.5–14 s | Turn side-on, bow and lift, turn back | Bounded yaw, low-front/reach poses, opposing yaw |
| 14–17.5 s | Raised-claw sway followed by low sweeping bow | Salute with yaw pulses, then low-right sweep |
| 17.5–19.5 s | Rise, approach, final claw gesture | Home, forward pulse, wave and claw pulse |
| 19.5–20 s | High diagonal claw finish | Salute-left pose and short yaw; stop at 20 s |

This is an executable approximation of the clip's gesture order and timing,
not recovered joint telemetry. Fast turns have reduced amplitude to keep wheel
commands bounded; the side-on labels describe the source phrase, not a verified
90-degree result. Translation distances and rotations need physical tuning.
No audio analysis or automatic music playback is included. The timeline follows
visible motions, not an estimated musical beat. The last 0.010 s of container
duration is rounded away.

## Run instructions

From this directory, preview every intended API command without connecting:

```bash
python3 dance.py
python3 dance.py --dry-run --arm-only --tempo 0.5
python3 -m unittest -v
```

For your later hardware test, start the existing controller on the Raspberry Pi
using its already-configured environment:

```bash
cd /path/to/hiwonder_masterpi/MasterPi
.venv/bin/masterpi serve
```

If it is already running, use that instance. Do not run competing driving or
arm-control programs during the dance. From the computer containing `dance.py`
and `dance_score.json`, use the server's real address in place of `ROBOT_IP`:

```bash
# First arm-only test, taking 40 seconds plus setup:
python3 dance.py --execute --url http://ROBOT_IP:8000 --arm-only --tempo 0.5

# Full 20-second choreography plus setup and synchronized MP4 audio:
python3 dance.py --execute --url http://ROBOT_IP:8000

# Optional silent execution:
python3 dance.py --execute --url http://ROBOT_IP:8000 --no-audio
```

When running the script on the Pi itself, the URL can instead be
`http://127.0.0.1:8000`. `--execute` and `--url` are both required to send
commands. The client itself needs no SDK installation; it uses the neighboring
project's running controller, which owns the hardware and calibration. Audio
execution requires `ffmpeg` and `aplay`; by default it uses the ReSpeaker's
`plughw:CARD=ArrayUAC10,DEV=0` playback output.

Execution first stops the chassis, sends the documented home pose and closes
the gripper, allows 1.5 seconds to settle, then counts down 3–2–1. Video time zero
begins at `GO`. At completion it stops the wheels and holds the final arm pose.
Ctrl+C stops the chassis; an arm transition already issued may finish. It does
not automatically send a new home movement after an error or interruption.

## Tuning and test limitations

- Edit `dance_score.json`: `at` is video seconds, `arm` selects a named
  `[x_cm, y_cm, z_cm, pitch_degrees]` pose, `move` is transition seconds,
  `gripper: true` opens and `false` closes, and `drive` is
  `[speed, heading_degrees, angular_rate]`. A missing field preserves that
  channel's previous command. `[0, 0, 0]` explicitly stops the wheels.
- The controller maps gripper open/closed to servo 1 pulses 2000/1500. IK drives
  servos 3–6 and applies the robot's calibration offsets. No invented servo IDs
  or action-group files are used.
- `--motion-scale 0.75` reduces chassis commands to 75%; it does not alter the
  arm poses. `--tempo 0.5` doubles event and servo durations and halves chassis
  speeds, preserving nominal displacement. Low duties can fail to start some
  wheels, as noted in the neighboring controller; physical displacement is not
  guaranteed, especially when slowing the routine.
- The duty validation uses the bundled mixer's default dimensions, `a+b=126`.
  IK tests use the neighboring source and its current `Deviation.yaml`. Rerun
  them after pose edits; a different robot calibration needs its own check.
  When the neighboring SDK is absent, the IK test is explicitly skipped.
- Tests use fake transport/clock and the vendor's mathematical IK class only.
  They verify timing, refresh intervals with 0/25/60 ms simulated request latency,
  arm-only mode, tempo scaling, validation, API serialization, and cleanup after
  failure/interruption. No real HTTP server or hardware backend was contacted.
- Requests time out at 0.25 s; playback aborts if a cue is more than 0.35 s late.
  On connection loss, the program attempts a stop and relies on the existing
  server watchdog if that stop cannot be delivered. Endpoint reachability does
  not verify collision-free arm paths, stability, or accurate wheel motion.

## Remaining for the user's physical test

- Verify the poses with an empty gripper and space around the arm; check that
  the robot's installed calibration agrees with the inspected source.
- Check wheel direction and turn amplitude, then tune motion durations or
  magnitudes for the floor and battery level. Use a clear floor area; the dance
  does not use obstacle avoidance or return to its starting position.
- Compare against video playback starting at `GO`, then adjust cues as needed.
  Program writing and offline verification are complete; physical execution is
  intentionally left to the user.
