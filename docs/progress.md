# MasterPi progress

Last updated: 2026-09-01

## Done

- Added a responsive webpage controller that works from a phone on the home
  network, including camera, mecanum chassis, arm, gripper, ultrasonic, LEDs,
  buzzer, and WonderEcho controls.
- Added Hermes chat through Telegram and the webpage. The agent uses a
  loopback-only hibot MCP for bounded physical actions.
- Added Hermes vision for “what do you see,” annotated images with object
  bounding boxes, and explicit color-detector fallback.
- Added quick Home, Check front, Grab, gripper, Nod, and Shake arm actions.
- Corrected MCP chassis motion to match the webpage: 40 mm/s default
  translation, all four wheels, webpage direction angles, and pure +/-0.6
  rotation.
- Flashed the ReSpeaker USB 4-Mic Array with the one-channel processed-ASR
  firmware and added automatic LED-off behavior on Linux boot/USB reconnect.
- Implemented sound-source bearing and bounded “come here” software:
  - reads the ReSpeaker XVF3000 `DOAANGLE` and `VOICEACTIVITY` values over USB;
  - circularly averages several bearings and rejects an unstable direction;
  - rotates toward the relative bearing with the webpage chassis mapping;
  - approaches at 40 mm/s for at most three seconds;
  - stops at the configured front-ultrasonic clearance and always stops in a
    `finally` path;
  - exposes `sound_direction` and `come_here` through the local agent API and
    hibot MCP;
  - provides `masterpi sound-direction` for mounting calibration.

## Deployment and physical validation still required

- Reinstall the updated narrow ReSpeaker udev rule, then reconnect the array or
  reboot. The rule matches only USB `2886:0018`, keeps the LED-off trigger, and
  grants the `plugdev` group access required for the MasterPi process to read
  DOA:

  ```bash
  cd /home/pi/projs/hiwonder_masterpi/masterpi
  sudo install -m 0644 deploy/99-respeaker-led-off.rules /etc/udev/rules.d/
  sudo udevadm control --reload-rules
  ```

- Calibrate the installed array because Seeed documents that DOA orientation
  depends on the build/mounting. Speak from directly in front and run:

  ```bash
  cd /home/pi/projs/hiwonder_masterpi/masterpi
  .venv/bin/masterpi sound-direction --samples 7
  ```

  Put the reported straight-ahead raw angle in the MasterPi service as
  `--sound-front-angle ANGLE`; add `--sound-counterclockwise` if increasing
  angles point left. Restart MasterPi and Hermes, then physically test “come
  here” with the chassis raised first and finally on a clear floor.

## Blocked: room SLAM and named-room navigation

True SLAM is not implementable safely with the currently detected hardware:

- no LiDAR or depth/range camera is attached;
- the expansion-board API exposes motor commands but no wheel encoder position
  or chassis odometry;
- the only range measurement is one forward ultrasonic ray;
- the IMU exposes acceleration and angular velocity, not absolute position;
- the monocular camera has no metric scale and is mounted on a moving arm,
  which changes its camera-to-chassis transform.

Command-time dead reckoning would drift and cannot close loops, so saving its
output as a “room map” or navigating autonomously to a `kitchen` annotation
would be misleading and collision-prone. No fake SLAM or unverified room
navigation was added.

To resume this item, add one supported metric localization source—preferably a
fixed 2D LiDAR, or a fixed depth/stereo camera—and expose wheel odometry if the
motors have encoders. Then implement, in order:

1. calibrated sensor and chassis transforms;
2. timestamped odometry plus range scans;
3. occupancy-grid SLAM with loop closure and map save/load;
4. webpage map display and named point/polygon annotations;
5. localization against the saved map and collision-aware path planning;
6. an MCP `navigate_to_location(name)` tool that rejects unknown or
   unreachable annotations.

## Verification

- Full test suite: 90 tests passing.
- Python bytecode compilation and `git diff --check`: passing.
- Physical “come here” validation is intentionally pending the USB permission
  update and DOA mounting calibration above.
