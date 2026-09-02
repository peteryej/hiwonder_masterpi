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
the Hiwonder software is somewhere other than `/home/pi/MasterPi` or
`/home/pi/TurboPi`, set its root explicitly:

```bash
export MASTERPI_VENDOR_ROOT=/path/to/MasterPi
.venv/bin/masterpi diagnose
```

The repository layout `MasterPi/masterpi_sdk/{common_sdk,kinematics_sdk}` is
detected automatically. Set `MASTERPI_SERIAL_DEVICE` only when the controller
UART is not exposed as `/dev/serial0` or `/dev/ttyAMA0`. Calibration YAML files
are also discovered in the repository; an unusual location can be supplied as
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

The **Grab object** quick-arm action immediately closes the gripper at the
fixed front pickup coordinate and returns the arm to Home while holding the
object. It intentionally performs no camera, color, stability, or centering
check; place the object directly in front of the gripper before selecting it.

The **Check front** quick-arm preset moves servo 3 to `500`, servo 4 to `2500`,
servo 5 to `1350`, and servo 6 to `1500` over 0.8 seconds. It leaves the
gripper on servo 1 unchanged.

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
sudo systemctl start respeaker-led-off.service
```

The udev rule starts the one-shot service again after each USB reconnect and on
future boots. It matches only Seeed USB device `2886:0018` and grants the
`plugdev` group access to the vendor interface used for DOA. Unplug and reconnect
the array (or reboot) after installing or changing the rule.

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

While the controller is running, pressing physical **KEY2** on the expansion
board moves the arm to the Home pose (`x=0, y=6, z=18, pitch=0`). Hiwonder's
keys are active-low Raspberry Pi inputs (KEY1=GPIO13, KEY2=GPIO23); the
controller detects the correct GPIO chip automatically.

The server intentionally has no login and binds to all interfaces for simple
robot-hotspot use. Only run it on the robot's private/trusted network. Bind to
the local machine with `--host 127.0.0.1` if remote access is not needed.

Use `--mock` before `serve` to try the complete interface without hardware:

```bash
.venv/bin/masterpi --mock serve --host 127.0.0.1
```

## Command-line examples

Hiwonder's direction convention is 90° forward, 0° right, 180° left, and 270°
backward; intermediate angles produce diagonal mecanum motion. Linear speed is
0–100 mm/s and angular rate is -2–2 rad/s.

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

The Hermes/MCP agent `recognize_and_grab` action intentionally bypasses camera
and color checks. Like the webpage's Quick Arm action, it always executes the
fixed front pickup and returns Home while holding the object.

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
