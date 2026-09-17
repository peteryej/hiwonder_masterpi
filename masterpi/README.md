# MasterPi Control

MasterPi Control is a safe browser and command-line controller for the Hiwonder
MasterPi robot. It controls the mecanum chassis, 5-DOF arm, gripper, PWM servos,
two expansion-board RGB LEDs, and buzzer.

The browser also includes a hibot chat panel directly below the chassis. It
accepts typed messages and microphone recordings, keeps a named Hermes Agent
conversation, displays text replies, and can read replies aloud through the
browser. Chat runs with Hermes' restricted `safe` toolset because this server
has no login. The web server separately recognizes explicit, non-negated chat
requests for the allowlisted **Nod** and **Shake** gestures (for example,
“nod if you understand”), executes the validated robot gesture, and shows the
completed action in the chat log. Other chat text remains text-only.

The implementation follows both generations of Hiwonder's Python API:

- Current MasterPi images: `common.mecanum`,
  `common.ros_robot_controller_sdk`, and `kinematics.arm_move_ik`.
- Legacy images covered by the supplied expansion-board tutorial:
  `HiwonderSDK.Board`, `HiwonderSDK.mecanum`, and `ArmIK.ArmMoveIK`.

The matching source is detected automatically. No third-party Python package is
needed at runtime.

## Safety first

Put the robot on blocks for the first test so the wheels cannot drive off a
table. Keep clear of the arm, do not force a powered servo, and have the robot's
power switch within reach.

The browser uses hold-to-drive controls and sends a fresh command every 180 ms.
The server has a dead-man watchdog: if updates stop for 600 ms, it stops all four
motors. While idle, it also reasserts zero speed and duty on every motor channel
twice per second, including after an expansion-board reset. `Ctrl+C`, normal
process shutdown, browser focus loss, and button/key release also issue a stop.
This is a software safety layer, not a substitute for the physical power switch.

## Install on the robot

Connect by SSH/VNC using the network instructions for the robot, copy this
folder to the Raspberry Pi, and run:

```bash
cd /path/to/masterpi-control
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/masterpi diagnose
```

The installation pulls in the small set of modules required by Hiwonder's SDK:
PySerial, NumPy, and PyYAML.

The last command should report the detected chassis, board, and arm modules. If
the Hiwonder software is somewhere other than this repository's
`MasterPi_original/`, `/home/pi/MasterPi`, or `/home/pi/TurboPi`, set its root
explicitly:

```bash
export MASTERPI_VENDOR_ROOT=/path/to/MasterPi_original
.venv/bin/masterpi diagnose
```

The repository layout
`MasterPi_original/masterpi_sdk/{common_sdk,kinematics_sdk}` is detected
automatically. Set `MASTERPI_SERIAL_DEVICE` only when the controller UART is
not exposed as `/dev/serial0` or `/dev/ttyAMA0`. Calibration YAML files are also
discovered in the repository; an unusual location can be supplied as
`MASTERPI_CONFIG_ROOT`.

Do not install replacement GPIO/serial drivers from PyPI. The hardware modules
are already included in Hiwonder's Raspberry Pi image.

## Browser control panel

Start the controller on the robot:

```bash
.venv/bin/masterpi serve
```

Open `http://ROBOT_IP:8000` from a device on the robot's Wi-Fi/LAN. In the
factory AP configuration, the documented robot address is usually
`http://192.168.149.1:8000`.

The page includes a live 640×480 camera view streamed from `/dev/video0`.
Set `MASTERPI_CAMERA_DEVICE` before starting the server when the camera uses a
different V4L2 device or stream URL:

```bash
export MASTERPI_CAMERA_DEVICE=/dev/video1
.venv/bin/masterpi serve
```

The **Open gripper** and **Close gripper** controls (including quick-arm,
arm-jog, and MCP `set_gripper`) use servo 1=`2500` for open and `500` for close.
Pickup routines use the same shared presets. Check front and Check ground
retain their separately recorded gripper observation setting of `2200`.

The chat panel's **New chat** button posts to `/api/chat/new`, which starts a
fresh named Hermes session. The web chat otherwise continues one long-lived
conversation, so an earlier refusal, a superseded instruction, or a scene
described minutes ago keeps shaping later replies; this drops that history and
clears the on-screen log. It does not affect voice conversations, which already
open their own session per wake word.

The **Grab object** quick-arm action runs the full `hibot-ground-grab`
procedure through `POST /api/grab/object`: it moves to Check ground, finds the
object with Hermes vision, drives the chassis in small bounded steps until the
object's centre is at the frame centre, picks up at `(2, 13, -1)` cm, lifts to
`(0, 15, 20)` still holding, and judges retention from two spaced camera
frames. It reports the target, the corrections it made, and whether the hold
was confirmed; identical frames mean a stuck stream and are never read as a
hold. It makes one attempt - a failed grasp is reported, not retried.

Its progress streams into the chat panel while it runs. The button posts to
`/api/grab/object/stream`, which returns newline-delimited JSON: one event per
step - what the camera saw with the annotated frame, each chassis correction
and why, the pickup, and the verdict - so a run that moves the robot for half a
minute is legible as it happens instead of resolving into a single line at the
end. `POST /api/grab/object` remains the plain blocking JSON call for agents.
Because the response is already committed when a step fails, errors arrive as a
final `{"stage": "error", "ok": false}` event rather than an HTTP status.

The **Quick grab action** (formerly *Grab object*) is the blind counterpart:
from wherever the arm currently is, it opens the gripper, descends onto the
fixed pickup coordinate `(2, 13, -1)` cm through a hover at `(2, 13, 8)`,
closes, and returns Home. It performs no camera, color, stability, or centering
check, so place the object at the calibrated pickup point before using it.

The recorded can pickup profile no longer has a webpage button. It remains
available to the `grab_from_front` agent/MCP tool, which opens the gripper,
moves servos 3–6 to `1550`, `1620`, `2500`, and `1500`, closes servo 1 at
`500`, then returns Home. Stage the can at the calibrated pickup point.

The **Check front** quick-arm preset matches Check ground except servo 3 is
`1200`: servo 4=`2500`, servo 5=`1500`, servo 6=`1500`, and gripper servo 1=
`2200`. It moves over 0.8 seconds and is also used by camera analysis and
MCP/API requests. It explicitly opens the gripper to the recorded `2200`
setting; do not run it while holding something that must not be released.

The **Check ground** quick-arm preset remains alongside Check front and
moves servos 3–6 to `500`, `2500`,
`1500`, and `1500`, and gripper servo 1 to `2200` over 0.8 seconds. It is also
available through the `check_ground` MCP/voice tool. Saving the preset does not
move the arm; it runs only when explicitly selected or requested.

The **Arm position** card includes two four-direction jog pads. On the left,
up/down changes Y and left/right changes X. On the right, up/down changes Z,
while left closes the gripper and right fully opens it. Cartesian buttons move
in 0.5 cm increments using the current X/Y/Z/pitch fields and selected movement
duration. A rejected coordinate is restored in the form, and the jog controls
remain disabled until the requested movement time has elapsed.

The chat panel requires the `hermes` command and a configured Hermes model.
Recorded messages use Hermes' configured speech-to-text provider. On browsers
that block microphone access from a plain HTTP robot address, use HTTPS or
localhost. The button uses browser `MediaRecorder` with `audio: true` and
`video: false`; there is no camera/file-capture fallback. The **Speak replies**
checkbox requests MP3 speech from Hermes' configured TTS provider. Enabling
it primes a browser Web Audio context during the checkbox gesture, allowing
later replies to play automatically after the asynchronous chat/TTS requests.
An audio player remains available as a fallback for browsers with stricter
site-level autoplay settings.

The installed robot service keeps HTTP on port 8000 and provides trusted-local
HTTPS on port 8443. From the HTTP page, use the certificate link in the chat
panel to install and trust the MasterPi CA certificate on the client device,
then open `https://10.0.0.102:8443/`. A private-IP certificate cannot be issued
by a public certificate authority, so installing the local CA is required once
on each browser device.

### ReSpeaker USB microphone LEDs

The ReSpeaker USB 4-Mic Array firmware starts in trace mode, so its ring reacts
to voice activity and direction of arrival. To keep the ring and center VAD LED
off, install the included system service and device-trigger rule once:

```bash
sudo install -m 0644 deploy/respeaker-led-off.service /etc/systemd/system/
sudo install -m 0644 deploy/99-respeaker-led-off.rules /etc/udev/rules.d/
sudo systemctl daemon-reload
sudo udevadm control --reload-rules
sudo udevadm trigger --settle --action=add --subsystem-match=usb \
  --attr-match=idVendor=2886 --attr-match=idProduct=0018
sudo systemctl start respeaker-led-off.service
```

The udev rule starts the one-shot service again after each USB reconnect and on
future boots. It matches only Seeed USB device `2886:0018` and grants the
`plugdev` group access to the vendor interface used for DOA. The targeted
`udevadm trigger` above reapplies it to an already connected array; alternatively,
unplug and reconnect the array or reboot.

The hibot MCP exposes `sound_direction` and `come_here`. The latter averages
five hardware DOA readings, rotates toward the relative bearing, approaches at
40 mm/s for at most two seconds, and stops when the front ultrasonic reading is
45 cm or less. DOA provides an angle, not source distance, so this is a bounded
reactive approach rather than sound-source localization in Cartesian space.

Seeed notes that DOA orientation depends on the microphone build and mounting.
Speak from directly in front of the robot and inspect the raw readings:

```bash
.venv/bin/masterpi sound-direction --samples 7
```

If straight ahead reports (for example) 90 degrees, add
`--sound-front-angle 90` to the installed `masterpi serve` command. If angles
increase toward the robot's left, also add `--sound-counterclockwise`.

### Local wake word with openWakeWord

Wake-word detection runs locally on Linux and uses the ReSpeaker's processed
mono 16 kHz stream. No Picovoice account, AccessKey, or cloud transcription is
involved. The response is also a cached local WAV file, so saying **hello
hibot** can produce **I'm here** without waiting for Hermes.

openWakeWord 0.6.0 does not ship a `hello hibot` model. Use its
[official automated training notebook](https://github.com/dscripka/openWakeWord/blob/main/notebooks/automatic_model_training.ipynb)
to train that phrase, export the ONNX model, and save it as:

```text
/home/pi/projs/hiwonder_masterpi/masterpi/models/openwakeword/hello_hibot.onnx
```

The controller uses Python 3.13, but openWakeWord's TFLite dependency currently
has no Python 3.13 Raspberry Pi wheel. Keep it isolated in the installed Python
3.11 environment:

```bash
cd /home/pi/projs/hiwonder_masterpi
/home/pi/.local/bin/python3.11 -m venv masterpi/.wakeword-venv
masterpi/.wakeword-venv/bin/pip install openwakeword==0.6.0 'pyusb>=1.3,<2' \
  'websocket-client>=1.8,<2'
PYTHONPATH=masterpi/src masterpi/.wakeword-venv/bin/python \
  -m masterpi_control.wake_word --download-features
```

Generate **I'm here** once with the existing Hermes TTS endpoint and convert it
to the WAV file used by `aplay`:

```bash
curl -sS -X POST http://127.0.0.1:8000/api/chat/tts \
  -H 'Content-Type: application/json' \
  --data "{\"text\":\"I'm here.\"}" \
  -o /tmp/im-here.mp3
ffmpeg -y -i /tmp/im-here.mp3 -ar 16000 -ac 1 \
  masterpi/assets/im-here.wav
```

The direct conversation requires OpenAI API access and a standard API key.
Store it outside the repository with owner-only permissions:

```bash
install -d -m 0700 ~/.config/masterpi
read -rsp 'OpenAI API key: ' HIBOT_OPENAI_KEY; echo
printf 'OPENAI_API_KEY=%s\n' "$HIBOT_OPENAI_KEY" > ~/.config/masterpi/openai.env
unset HIBOT_OPENAI_KEY
chmod 0600 ~/.config/masterpi/openai.env
```

An ignored repository-root `.env` containing `OPENAI_API_KEY=...` is also
supported. Keep it mode `0600`; when both files exist, the repository `.env`
takes precedence.

Connect an active speaker or headphones to the ReSpeaker 3.5 mm output, then
test in the foreground:

```bash
set -a
. ~/.config/masterpi/openai.env
set +a
PYTHONPATH=masterpi/src masterpi/.wakeword-venv/bin/python \
  -m masterpi_control.wake_word --conversation --threshold 0.5
```

The listener addresses the array by stable ALSA name
`plughw:CARD=ArrayUAC10,DEV=0`, feeds openWakeWord 80 ms frames, pauses capture
while the reply plays, resets the model, and waits 0.75 seconds before
listening again. Start with the default `0.5` score threshold. Raise it with
`--threshold 0.6` if normal conversation causes false activations, or lower it
slightly if the phrase is missed.

With `--conversation`, each wake opens a persistent direct OpenAI Realtime
speech-to-speech session and plays **I'm here**. It does not connect to Hermes.
Speech must remain above RMS 400 for four 80 ms frames before it is accepted;
recording then ends after three seconds of silence and has a 30-second hard
limit. The captured PCM is
resampled from 16 kHz to the Realtime API's 24 kHz PCM format, sent directly to
`gpt-realtime-2.1`, and returned audio chunks are streamed immediately to
`aplay` rather than waiting for a complete TTS file. Follow-up turns retain the
same Realtime conversation without requiring the wake phrase again. Two
consecutive 8-second silent or empty-transcript cycles, 30 user turns, or an exact
**stop**, **goodbye**, **never mind**, **cancel**, **stop listening**, **that's
all**, or **end conversation** returns it to wake-word mode.

The Realtime session prompt identifies the speaker as the physical **HiBot**
MasterPi robot, describes its mecanum chassis, arm, gripper-mounted camera,
ultrasonic sensor, LEDs, buzzer, microphone array, and supported software
actions, and normally limits answers to one or two short sentences. This fast
voice path does not connect to the Hermes agent. Instead, it registers the same
bounded action definitions used by the hibot MCP directly with Realtime and
dispatches requested actions through the loopback-only `/api/agent/*`
controller. The prompt calls physical tools only for explicit action requests
and does not claim success until the controller result is returned.

Voice actions include state, stop, chassis movement, obstacle avoidance, Home,
Check front, individual confirmed servos, gripper, unconditional ground and
recorded front-can grabs, the bundled Dance with synchronized music, camera
analysis, sound direction/approach, LEDs, and buzzer. Each tool start,
completion/failure, and elapsed time appears as an intermediate step in the
webpage conversation feed. The direct Grab action has no color-detection
guardrail; camera analysis is a separate action.

Normal loopback control calls retain a 15-second failure deadline. Semantic
camera analysis has a separate 180-second deadline because Check front arm
settling and the vision-model request can exceed 15 seconds.

Barge-in remains disabled in the installed service. While HiBot is thinking or
speaking, wait for the reply before asking the next question. Normal initial
speech detection remains local at RMS 400.

The controller's **Chat with hibot** panel also displays the current spoken
conversation. It shows OpenAI's input transcript, HiBot's output transcript, an
animated thinking indicator, and connecting, listening, thinking, streaming,
and error stages. The page reads the wake service's atomic status snapshot
through `GET /api/voice/conversation`.

Capture diagnostics persist in the rotating log
`~/.local/state/masterpi/voice-diagnostics.log`. Each attempt records its RMS
threshold and distribution, time waiting for speech, captured-audio duration,
and whether it ended through silence, start timeout, or the hard duration cap.
Realtime turn latency and empty transcripts are recorded in the same file.
Inspect recent measurements with:

```bash
tail -n 100 ~/.local/state/masterpi/voice-diagnostics.log
```

While Realtime is processing a submitted question, the ReSpeaker runs its
built-in clockwise spin animation with a low-brightness blue/cyan palette. The
ring is returned to mono black as soon as the first audio chunk arrives and on
every error path. Dynamic control runs in the unprivileged wake-word service,
so the installed udev rule must be the repository version that assigns the
device to `plugdev`; after updating the rule, reconnect the array or reboot.

When the wake phrase or a conversation utterance is detected, the service
reads the ReSpeaker's current `DOAANGLE` and lights only the nearest of its
twelve 30-degree ring pixels in low-brightness green. That direction pixel
stays on through utterance capture, then turns off or changes to the blue/cyan
thinking animation. The mapping uses the array's native raw-angle orientation.

Wake detection remains fully local. The service reads the key file through
systemd and defaults to
`--conversation-backend realtime --realtime-model gpt-realtime-2.1`. The old
Hermes pipeline remains available only as an explicit diagnostic fallback with
`--conversation-backend hermes`. If room noise starts recordings, raise
`--speech-threshold`; lower it if normal speech is not captured.

After foreground testing succeeds, install the included service:

```bash
install -D -m 0644 deploy/masterpi-wake-word.service \
  ~/.config/systemd/user/masterpi-wake-word.service
systemctl --user daemon-reload
systemctl --user enable --now masterpi-wake-word.service
journalctl --user -u masterpi-wake-word.service -f
```

The ReSpeaker LED-off service is independent and remains effective while the
wake-word listener is running.

The ultrasonic-distance card polls the Hiwonder I²C sensor at address `0x77`
and displays centimetres. Its color picker controls both RGB LEDs on the
sensor. Both ultrasonic LEDs are turned off whenever the controller starts.
Enable Raspberry Pi I²C before using it:

```bash
sudo raspi-config nonint do_i2c 0
```

The page reports the sensor as unavailable when `/dev/i2c-1` is missing or the
sensor does not acknowledge on the bus; robot controls and camera streaming
continue to operate normally.

The WonderEcho card polls the voice module at I²C address `0x34` and displays
recognized command IDs without assigning them to robot movement. Its broadcast
selector can play several phrases already compiled into the factory firmware.
WonderEcho is not a general text-to-speech engine, so arbitrary text requires
building and flashing customized WonderEcho firmware.

While the controller is running, pressing physical **KEY1** on the expansion
board launches the same asynchronous Dance action as the webpage button,
including synchronized audio and duplicate-run protection. Pressing **KEY2**
moves the arm to the Home pose (`x=0, y=6, z=18, pitch=0`). Hiwonder's keys are
active-low Raspberry Pi inputs (KEY1=GPIO13, KEY2=GPIO23); the controller
detects the correct GPIO chip automatically. Press and click events are
debounced so one physical press starts only one action.

The server intentionally has no login and binds to all interfaces for simple
robot-hotspot use. Only run it on the robot's private/trusted network. Bind to
the local machine with `--host 127.0.0.1` if remote access is not needed.

Use `--mock` before `serve` to try the complete interface without hardware:

```bash
.venv/bin/masterpi --mock serve --host 127.0.0.1
```

## Command-line examples

The operator-corrected chassis mapping for this robot is 90° forward, 0° left,
180° right, and 270° backward. Web left/right buttons, diagonal buttons,
keyboard controls, and MCP/voice `drive_for` agree on this mapping; rotation
controls and arm X jogs are unchanged. Raw numeric headings and the vendor
wheel mixer are not rewritten. Intermediate angles produce diagonal mecanum
motion. Linear speed is 0–100 mm/s and angular rate is -2–2 rad/s.

The agent/MCP `move` and `rotate` actions take a distance or angle instead of a
duration: `move` accepts `distance_cm` (2–320 cm forward/backward at 40 cm/s,
1–160 cm strafing at 20 cm/s) and `rotate` accepts `degrees` (9.5–1520 at
190°/s, the same constant `approach_bearing` turns with), or either takes `seconds` (0.05–8). Give exactly one of the two. The
conversions come from the operator calibrations in `docs/grab_object_plan.md`,
measured at speed setting 40: they are timed estimates, not odometry, so each
result reports `distance_measured`/`angle_measured` as `false`. A distance at
another speed is refused rather than converted, and an out-of-range amount is
refused with its callable range instead of being clamped to a different move.

```bash
# Move forward at 40 mm/s for one second, then stop.
.venv/bin/masterpi drive 40 90 --seconds 1

# Rotate counter-clockwise for half a second.
.venv/bin/masterpi drive 0 90 --angular -0.5 --seconds 0.5

# Move to the tutorial home pose, then open the gripper.
.venv/bin/masterpi home
.venv/bin/masterpi gripper open

# Recognize and pick up a centered colored object (or specify red/green/blue/yellow).
.venv/bin/masterpi grab any

# Move the arm endpoint to x=5, y=8, z=16 cm.
.venv/bin/masterpi arm 5 8 16 --pitch 0 --seconds 1.2

# Set a PWM servo, LEDs, and buzzer.
.venv/bin/masterpi servo 1 1500 --seconds 0.5
.venv/bin/masterpi rgb 30 120 255
.venv/bin/masterpi buzzer --repeat 2

# Immediately stop the chassis.
.venv/bin/masterpi stop
```

Every one-shot CLI invocation stops the chassis before it exits. Invalid servo
pulses, speeds, angles, RGB values, and durations are rejected before reaching
the hardware.

## HTTP API

The control page uses a small JSON API. All modifying endpoints require a JSON
object, including `/api/stop`.

| Endpoint | Example body |
| --- | --- |
| `GET /api/state` | none |
| `GET /api/camera/stream` | none (MJPEG stream) |
| `GET /api/distance` | none |
| `GET /api/voice` | none |
| `POST /api/chat` | `{"message":"Hello hibot"}` |
| `POST /api/chat/audio` | raw `audio/*` body, up to 25 MiB |
| `POST /api/sonar/rgb` | `{"red":0,"green":170,"blue":255}` |
| `POST /api/voice/speak` | `{"phrase":"forward"}` |
| `POST /api/drive` | `{"speed":40,"direction":90,"angular_rate":0}` |
| `POST /api/stop` | `{}` |
| `POST /api/arm` | `{"x":0,"y":6,"z":18,"pitch":0,"duration":1.5}` |
| `POST /api/home` | `{}` |
| `POST /api/pose/check_front` | `{"duration":0.8}` |
| `POST /api/pose/check_ground` | `{"duration":0.8}` |
| `POST /api/agent/check_ground` | `{"duration":0.8}` (loopback only) |
| `POST /api/servo` | `{"servo_id":1,"pulse":1500,"duration":0.5}` |
| `POST /api/gripper` | `{"opened":true}` |
| `POST /api/grab` | `{"target":"red"}` |
| `POST /api/camera/analyze` | `{}` (Check front + Hermes vision) |
| `POST /api/camera/analyze/color` | `{"samples":3}` |
| `POST /api/agent/sound_direction` | `{"samples":5}` (loopback only) |
| `POST /api/agent/come_here` | `{"approach_duration":2,"clearance_cm":45}` (loopback only) |
| `POST /api/rgb` | `{"red":255,"green":0,"blue":0}` |
| `POST /api/buzzer` | `{"frequency":1900,"on_time":0.1,"off_time":0.1,"repeat":1}` |

`grab` recognizes red, green, blue, and yellow regions in three consecutive
camera frames. It only moves the arm when one color is stable and the object is
near the image center, then uses the fixed table-height capture coordinate from
Hiwonder's color-sorting lesson. It does not identify arbitrary semantic object
classes, estimate depth, or drive the chassis. Start with a lightweight block,
keep the pickup area clear, and tune the fixed coordinate for your camera mount
and table height before trying fragile objects.

The Hermes/MCP and Realtime agents expose two unconditional grab actions. The
`grab_from_ground` tool uses the operator's Check ground image-center pickup
at `(2, 13, -1)` cm with a `-68°` pitch preference (offline IK checked including
servo calibration offsets) and starts from the arm's current pose. The
`grab_from_front` tool executes the operator-recorded servo profile used by the
recorded can pose: servos 3–6 move to `1550`, `1620`, `2500`, and `1500`. Neither tool performs a camera or color check, and both return Home
while holding the object.

The chat understands camera questions such as **“What do you see?”**, **“What
is in the camera?”**, and **“Describe the scene.”** By default it moves to the
Check front pose, waits for the arm, captures a fresh frame, and asks Hermes
vision for semantic object labels and normalized bounding boxes. The server
draws those boxes and labels on the captured frame and includes the annotated
image directly in the chat reply. Local OpenCV
color-region detection is used only when the request explicitly says **color
detection**, **color recognition**, or **detect colors**.

## Development and tests

The mock backend records commands in memory and never imports vendor modules.
Run all tests without robot hardware:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests
PYTHONPATH=src python3 -m masterpi_control --mock diagnose
```

Project layout:

```text
src/masterpi_control/backends.py  vendor compatibility and mock adapters
src/masterpi_control/robot.py     validation, locking, state, watchdog
src/masterpi_control/server.py    browser/API server
src/masterpi_control/static/      touch and keyboard control panel
src/masterpi_control/cli.py       command-line interface
tests/                            hardware-free unit/integration tests
```

## Tutorial references

- [Shared Hiwonder expansion-board lessons](https://drive.google.com/drive/folders/1wU6qAXwc4Gk2WQXB_NVY_CB3nGAa8iiy)
- [Current MasterPi documentation](https://docs.hiwonder.com/projects/MasterPi/en/latest/)
- [MasterPi mecanum motion course](https://docs.hiwonder.com/projects/MasterPi/en/latest/docs/6.motion_control_course.html)
- [MasterPi arm kinematics course](https://docs.hiwonder.com/projects/MasterPi/en/latest/docs/7.basic_course_kinematics.html)
