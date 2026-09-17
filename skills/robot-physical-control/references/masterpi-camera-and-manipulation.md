# MasterPi camera, chassis, and manipulation notes

## Live camera frame capture

The local controller exposes MJPEG at:

`http://127.0.0.1:8000/api/camera/stream`

Fetch it for a bounded interval, extract a complete JPEG from `FF D8` through `FF D9`, verify decoding, then inspect it. A stream timeout after receiving bytes is expected for an endless MJPEG response; preserve the received bytes and extract a frame rather than treating that timeout as an empty capture.

## Chassis actions aligned with the webpage

The webpage controls are the user-confirmed reference for correct wheel mixing:

- Forward: heading `90°`
- Backward: heading `270°`
- Left strafe: heading `180°`
- Right strafe: heading `0°`
- Rotate left/right: zero linear speed, heading placeholder `90°`, angular rate `-0.6`/`+0.6`
- Use the webpage's validated motion speed floor/default of `40`; lower values may fail to overcome every wheel's starting torque.
- Operator-set rotation calibration: `190°/s`, therefore convert requested bearing magnitude with `seconds = abs(degrees) / 190`. This replaces the earlier `180° = 1.0 s` figure and is the same constant `rotate` and `approach_bearing` use.

Finite chassis movement must refresh the dead-man watchdog and stop in `finally`. The chassis has no verified wheel odometry, so do not present time-based translation as centimeter-accurate. The forward ultrasonic sensor does not validate a rearward path.

## Recorded arm poses

Only expose the user-confirmed direct controls: servo 1 (gripper), servo 3 (top), servos 4–5, and servo 6 (base). Do not expose servo 2.

### Check-front and check-ground poses

Current recorded presets (`robot.check_front` / `robot.check_ground`):

| Servo | Check front | Check ground |
|---|---:|---:|
| 3 | `1200` | `500` |
| 4 | `2500` | `2500` |
| 5 | `1500` | `1500` |
| 6 | `1500` | `1500` |
| 1 - gripper | `2200` | `2200` |

Both open the gripper to the `2200` observation setting, so never call either
while holding an object.

### Can pickup profile

The operator-recorded pose for the staged beverage can is:

1. Stop the chassis and move Home.
2. Open gripper, servo 1: `2500` (the shared open preset).
3. Move servo 3: `1550`, servo 4: `1620`, servo 5: `2500`, servo 6: `1500`.
4. Close gripper, servo 1: `500` (the shared closed preset).
5. Return Home and stop the chassis in `finally`.

Keep this sequence behind one serialized, named controller action. Do not compose it as unrelated remote servo calls that can interleave or strand the arm after a partial failure.

### Recorded can pickup (no webpage button)

The webpage's **Grab can** button has been removed; the recorded can pose now has one caller, the `grab_from_front` tool. It performs no perception check and posts the explicit payload:

```json
{"force": true, "pickup": "can"}
```

to `POST /api/grab`. The backend must route forced actions as `grab_front("can")`; omitting `pickup` selects the generic fixed-front geometry instead. Disable the button while the request is active, report completion/error in the arm-status region, and re-enable it in `finally`.

Keep this direct action separate from the semantic guarded flow below. Use the guarded command when the operator asks to locate or validate the can; use the direct quick action when the operator explicitly says to skip checks and perform the recorded arm motion.

## Ground pickup

For an object on the floor, use the `hibot-ground-grab` skill rather than
anything in this file: it owns the Check ground look, the chassis alignment,
the calibrated pickup coordinate, and the post-lift camera confirmation. The
`grab_from_ground` tool is that pickup's final motion only - a blind close at a
fixed coordinate - and cannot find or center on an object by itself.

## Semantic can guard

For the staged can, do not use HSV/color-region detection as the object-identity or readiness check. The reliable flow is:

1. Capture a fresh frame and ensure no hands are near the arm.
2. Call semantic image analysis (`/api/camera/analyze`) and require an object label containing the word `can` with a valid normalized bounding box.
3. Compute the bounding-box center. The current broad center gate accepts `abs(center_x - 0.5) <= 0.25` and `abs(center_y - 0.5) <= 0.30`.
4. A dry run ends after perception. An explicit execute path invokes the named can pickup profile without repeating the old color check.
5. A centered 2D box does not establish depth; the operator must stage the can at the recorded pickup point.
6. After motion, capture another frame. A successful API response proves the sequence ran, not that the gripper physically retained the can.

This flow was exercised with a semantic result of `mango beverage can`, confidence `0.98`, center `(0.486, 0.664)`, which passed the general-center gate.

## Testing and deployment

Use TDD for pose, guard, or control-surface changes: observe the focused test fail, implement the minimum change, then run the full hardware-free suite. For a quick action, assert both the rendered button and the exact API payload, retain the backend profile-routing test, restart `masterpi.service`, fetch the live page, and confirm `/api/state` reports a stopped chassis. Do not invoke a live pickup merely to verify deployment; execute physical motion only when the operator explicitly requests it.
