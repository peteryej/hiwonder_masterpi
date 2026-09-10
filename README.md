# Hiwonder MasterPi

This repository contains the original Hiwonder robot software together with a
separate browser and command-line controller built around the bundled hardware
SDK.

## Folder overview

### `MasterPi_original/` — original Hiwonder software and hardware SDK

[`MasterPi_original/`](MasterPi_original/) contains the unmodified robot
software supplied by Hiwonder. It
includes:

- Expansion-board, mecanum chassis, ultrasonic sensor, and RGB hardware drivers
- Arm inverse-kinematics code
- Camera calibration data and vision examples
- Board, servo, chassis, and arm demonstration scripts
- Color recognition, tracking, sorting, line-following, and avoidance examples

This folder is the low-level hardware implementation used by the controller in
`masterpi/`. Most programs here access physical hardware immediately and may
move the wheels or arm when started.

Example vendor demos:

```bash
python MasterPi_original/board_demo/hardware_test.py
python MasterPi_original/board_demo/control_by_servo.py
python MasterPi_original/board_demo/control_by_kinematics.py
```

Keep the robot supported, keep clear of the arm, and have the power switch
within reach before running hardware demonstrations.

### `masterpi/` — browser and command-line controller

[`masterpi/`](masterpi/) is the custom MasterPi control application. It provides:

- A responsive browser control panel
- Hold-to-drive mecanum controls with a dead-man watchdog
- Arm, gripper, direct-servo, RGB LED, and buzzer controls
- A live USB camera stream with show/hide control
- Text and recorded-audio chat with Hermes Agent, plus optional spoken replies
- Live ultrasonic distance readings and ultrasonic RGB LED control
- A validated JSON HTTP API
- A mock backend and hardware-free automated tests

It imports the hardware drivers and kinematics implementation from
`MasterPi_original/` while adding validation, concurrency control, safe stop
behavior, and the web interface.

## Setup

Create or activate the virtual environment in `masterpi/`, then install the
controller and bundled SDK packages:

```bash
cd masterpi
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/pip install -e ../MasterPi_original/masterpi_sdk/common_sdk
.venv/bin/pip install -e ../MasterPi_original/masterpi_sdk/kinematics_sdk
```

Enable Raspberry Pi I²C for the ultrasonic sensor:

```bash
sudo raspi-config nonint do_i2c 0
```

## Run the controller

From the repository root:

```bash
source masterpi/.venv/bin/activate
masterpi diagnose
masterpi serve
```

Open `http://ROBOT_IP:8000` in a browser. The MasterPi factory hotspot address
is commonly `http://192.168.149.1:8000`.

For development without robot hardware:

```bash
masterpi --mock serve --host 127.0.0.1
```

See [`masterpi/README.md`](masterpi/README.md) for the full CLI and HTTP API
reference, configuration options, and safety notes.
