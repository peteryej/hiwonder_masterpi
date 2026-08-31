# MasterPi Control

MasterPi Control is a safe browser and command-line controller for the Hiwonder
MasterPi robot. It controls the mecanum chassis, 5-DOF arm, gripper, PWM servos,
two expansion-board RGB LEDs, and buzzer.

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

The ultrasonic-distance card polls the Hiwonder I²C sensor at address `0x77`
and displays centimetres. Its color picker controls both RGB LEDs on the
sensor. Enable Raspberry Pi I²C before using it:

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
| `POST /api/sonar/rgb` | `{"red":0,"green":170,"blue":255}` |
| `POST /api/voice/speak` | `{"phrase":"forward"}` |
| `POST /api/drive` | `{"speed":40,"direction":90,"angular_rate":0}` |
| `POST /api/stop` | `{}` |
| `POST /api/arm` | `{"x":0,"y":6,"z":18,"pitch":0,"duration":1.5}` |
| `POST /api/home` | `{}` |
| `POST /api/servo` | `{"servo_id":1,"pulse":1500,"duration":0.5}` |
| `POST /api/gripper` | `{"opened":true}` |
| `POST /api/rgb` | `{"red":255,"green":0,"blue":0}` |
| `POST /api/buzzer` | `{"frequency":1900,"on_time":0.1,"off_time":0.1,"repeat":1}` |

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
