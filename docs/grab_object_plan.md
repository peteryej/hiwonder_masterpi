# Ground-object grab plan

Date: 2026-09-16

## Goal and scope

Define a repeatable staged ground pickup with the gripper-mounted camera:
Check ground first → (only if the target is not already in that view) Check
front alignment → approximately 20 cm forward → back to Check ground → center
within 1 cm → pickup at `(2, 13, 0)` → close gripper → lift to `(0, 15, 20)`.

Coordinates are centimetres in the arm's coordinate frame, not distances
measured from the camera. Servo values are pulse widths in microseconds.
This is an operator-defined experimental skill, not an autonomous
visual-servoing service. The `grab_from_ground` MCP quick action now picks up
at `(2, 13, -1)` instead, starts from the arm's current pose, and retains its
Home return.
The earlier `(0, 15, 1)` proposal below is retained as historical context.

## Repeatable tracking-and-grabbing skill

The maintained procedure is
[`hibot-ground-grab`](../skills/hibot-ground-grab/SKILL.md).

1. Move to Check ground first, settle, and capture without changing that arm
   pose. If the target is already in this view, skip steps 2-3 and align for
   pickup directly; the ground view is the one whose center maps to the pickup
   point. Use Check front only when the target is absent or clearly too far.
2. Use Hermes estimated boxes and a rough long-axis estimate in Check front.
   Accept alignment within 10 degrees of vertical and within approximately
   5 cm of image bottom-center; do not aim for perfect pixels.
3. Move forward approximately 20 cm once: an initial 0.5-second pulse at
   speed setting 40 using the operator's 8 cm/0.2-second calibration, then
   return to Check ground, settle, and capture again.
4. Align the target's center within approximately 1 cm of image center. The
   operator defines the Check ground image center as arm `(2, 13, 0)` cm.
   This supersedes the previous `(0, 12, 0)` center calibration; the recorded
   Check ground servo preset itself is unchanged.
   Pixel-to-centimetre estimates require the matching pose/depth calibration;
   do not transfer Check front's scale to Check ground.
5. Stop the chassis, open gripper, move to `(2, 13, -1)`, close gripper, then
   lift to `(0, 15, 20)` while keeping it closed. Finish at the lift pose,
   not Home. The skill supplies a dry-run-by-default helper for this sequence,
   with `--pickup-z` for an authorized retry (default `-1`, floor `-3`).
   Each retry needs its own operator authorization.

Current shared gripper presets: servo 1=`2500` open, servo 1=`500` closed.
Check front and Check ground keep their recorded observation pulse `2200`.

The `grab_from_ground` MCP action now approaches `(2, 13, 8)` from the arm's
current pose (no initial Home move), picks up at `(2, 13, -1)` with a `-68°`
pitch preference, closes at servo 1=`500`, and returns Home. The webpage's
generic Grab object action shares this routine.
Use the skill helper instead when the required finish is `(0, 15, 20)`.
Offline IK finds valid calibrated pulses for the updated approach/pickup;
this does not establish collision clearance or prove a physical capture.
At the new pickup coordinate, the old `-66°` pitch would command calibrated
servo 4 to `2538`; `-68°` gives `2491`, within the `500–2500` limits. The skill
helper uses the same pickup pitch and retains its `(0, 15, 20)` lift target.
The new full workflow/pickup has not yet been physically executed or validated.

## Recorded observation pose

Use the saved `check_ground` preset, with a 0.8-second movement:

| Servo | Pulse |
|---|---:|
| 3 | 500 |
| 4 | 2500 |
| 5 | 1500 |
| 6 | 1500 |
| 1 — gripper | 2200 |

This explicitly opens the gripper to the recorded observation setting. Do not
run it while holding something that must not be released. This is a direct
servo preset; its Cartesian XYZ has not been established, and the controller's
cached previous XYZ is not a measurement of this pose.

## Check ground camera calibration

Recorded on 2026-09-16 using the Check ground servo pose above and the
uncropped 640 × 480 camera image.

### Pixel scale at the reference can's distance

- The red beverage can's detected horizontal width is approximately
  `(0.679 - 0.254) × 640 = 272` pixels.
- Operator-supplied physical width: **5.5 cm**.
- Horizontal scale: `5.5 / 272 ≈ 0.0202 cm/pixel`, or approximately
  `49.45 pixels/cm`.
- Assuming equal horizontal and vertical pixel scale, the full image's
  vertical span at the same object distance is
  `480 × 5.5 / 272 ≈ 9.7 cm`.

This is an approximate scale at the reference can's depth, not a universal
conversion for floor distances or objects at different depths.

### Object-bottom placement rule

Operator-provided observation: **when the object's actual bottom is just
visible at the bottom edge of the Check ground image, the object is
approximately 2 cm in front of the car.**

Use this as a placement reference with the same camera pose, mounting,
resolution, uncropped field of view, and ground level. The car-relative
2 cm distance is not automatically the arm's Cartesian Y coordinate; its
relationship to the proposed `y=15` pickup point still needs physical testing.

Confirm that the physical bottom is visible: a detected bounding box touching
`y_max=1` can also mean the object is cut off and does not alone satisfy this
rule. The first captured can image was cropped at the bottom, so it does not
independently confirm the 2 cm placement reference.

## Check front camera calibration

Operator-provided reference, recorded on 2026-09-16: **in the current Check
front pose, the bottom edge of the camera image corresponds to a ground
position approximately 23 cm in front of the robot.**

This reference uses servo 3=`1200`, servo 4=`2500`, servo 5=`1500`, servo
6=`1500`, and gripper servo 1=`2200`, with the same mounting and uncropped
640 × 480 field of view. If a ground object's actual bottom is visible at the
image's bottom edge, use approximately 23 cm as the car-relative placement
reference. A cropped object or bounding box touching the edge is not enough
to establish that its physical bottom is there.

This is separate from Check ground's 2 cm reference. The 23 cm value is not an
arm Y coordinate, camera height, or image-height measurement. Do not transfer
Check ground's 272-pixel/5.5 cm scale to Check front without a new measurement.
The reference is supplied by the operator; it was not independently measured
by this documentation update.

### Horizontal scale at the bottom edge

Operator-provided calibration: **in Check front, a horizontal span of
124 pixels at the bottom of the image corresponds to 6 cm**.

- Horizontal scale: `6 / 124 ≈ 0.0484 cm/pixel`, or approximately
  `20.67 pixels/cm`.
- For the uncropped 640 × 480 image, the full bottom-edge width corresponds
  to `640 × 6 / 124 ≈ 31.0 cm`.

Use this bottom-edge calibration with the Check front pose and camera setup
above. It supersedes the earlier 5.5 cm/124-pixel estimate for this reference;
do not apply it to Check ground or other image rows/depths without calibration.
Operator clarification: this conversion holds only for objects near the
image's **center-bottom**; the 31.0 cm full-width value is an extrapolation,
not a verified uniform scale across the whole bottom edge.
This is an operator-supplied relationship, not an independent measurement.

### Vertical reference for the can near center-bottom

The latest current-position Hermes box has `y_min=0.494` and `y_max=0.923`
in a 640 × 480 frame. Its vertical span is
`(0.923 - 0.494) × 480 = 205.92`, or approximately **206 pixels**.
Operator-supplied physical can length: **14 cm**.

- Approximate image-vertical scale: `14 / 205.92 ≈ 0.0680 cm/pixel`, or
  `14.71 pixels/cm`.
- This relationship applies only when the object is near the **center-bottom**
  with the same camera pose, resolution, depth, and object orientation. The
  latest box's bottom-center is approximately `(194, 443)`, left of center;
  its measured span is a reference, not a validation of the conversion there.
- The visible can lies on its side, so its physical length is not an upright
  object height. Perspective/foreshortening and orientation affect its image
  span. Do not use this ratio as a calibrated forward-travel or floor-distance
  conversion without validating that mapping.

## Chassis movement calibration

### Forward/backward

Operator-provided observation, recorded on 2026-09-16: **a 0.2-second
forward or backward move corresponds to approximately 8 cm of travel**.
The recent commands used `drive_for` with the speed setting `40`.

Treat this as the observed calibration for the current robot/setup, not an
independently measured or universally linear duration-to-distance conversion.
It differs by a factor of ten from the nominal `40 mm/s × 0.2 s = 8 mm`
estimate; the discrepancy has not been diagnosed. No motor mapping or speed
setting was changed by recording this observation.

### Left/right

Operator-provided observation, recorded on 2026-09-16: **a 0.1-second
left or right move corresponds to approximately 2 cm of lateral travel**.
The recent commands used `drive_for` with the speed setting `40`.

Keep this separate from the forward/backward calibration: lateral and
longitudinal travel have different observed scales. This is a calibration
for the current setup and tested duration, not an independently measured or
universally linear conversion. No motor mapping or speed setting was changed.

### Rotate left/right

Operator-set calibration: **rotation runs at 190 degrees per second**, so a
0.1-second left or right rotation is about 19 degrees of yaw. This value
replaces both earlier figures - the 20 degrees/0.1 s observation here and the
`180 degrees = 1.0 s` figure in the platform notes - and is now the single
constant shared by `rotate` and `approach_bearing`.
This applies to the current `drive_for` rotation commands, matching the
webpage's pure rotation settings: linear speed `0`, angular command `-0.6`
for `rotate_left` and `+0.6` for `rotate_right`.

Treat this as an observed calibration for the current setup and tested
duration, not an independently measured or universally linear angular-speed
conversion. It is separate from left/right strafing. No rotation settings or
motor mapping were changed by recording this observation.

## Webpage actions

- **Grab object** posts `POST /api/grab/object/stream`, which runs the skill's
  procedure server-side: Check ground, Hermes object selection, bounded
  chassis centring to the 1 cm tolerance, pickup at `(2, 13, -1)`, lift to
  `(0, 15, 20)` still holding, then two spaced confirmation frames. One
  attempt; a failed grasp is reported with the corrections it made. Every step
  streams to the chat panel as newline-delimited JSON while it runs: the
  observation with its annotated frame, each correction and its reason, the
  pickup, and the verdict. `POST /api/grab/object` is the same run without the
  stream, for agents that just want the result.
- **Quick grab action** (formerly *Grab object*) is the blind fixed-point
  pickup: no look, no centring, returns Home.
- **Grab can** has been removed. The recorded can pose survives only behind the
  `grab_from_front` agent/MCP tool.

## Distance and angle chassis actions

The calibrations above are wired into two agent/MCP actions so a caller can ask
for centimetres or degrees instead of converting by hand. Each takes **exactly
one** of the amount or `seconds`; supplying both, or neither, is an error.

| Action | Amount | Conversion | Range |
|---|---|---|---|
| `move` (`forward`, `backward`) | `distance_cm` | `40 cm/s` | 2-320 cm |
| `move` (`left`, `right`) | `distance_cm` | `20 cm/s` | 1-160 cm |
| `rotate` (`left`, `right`) | `degrees` | `190 degrees/s` | 9.5-1520 degrees |
| either | `seconds` | none | 0.05-8 s |

HTTP: `POST /api/agent/move` and `POST /api/agent/rotate`. Both return the
computed `duration`, the requested amount, the calibration used, and
`distance_measured`/`angle_measured` set to `false`.

These conversions inherit every limitation of the calibrations: they are
operator-measured at **speed setting 40** for the current floor and battery
state, with no wheel odometry or gyro behind them. A distance request at
another speed is refused rather than converted with a calibration that does not
hold there; pass `seconds` for that case. An amount whose duration would fall
below the 0.05 s chassis minimum or above the 8 s bound is refused with the
callable range, because clamping would quietly move a different amount than
the caller asked for.

The earlier conflict is resolved by operator decision: `rotate` and
`approach_bearing` both use `ROTATION_CALIBRATION_DEGREES_PER_SECOND = 190`,
replacing the old 200 degrees/s and `180 degrees = 1.0 s` figures. 190 is a
chosen value, not a fresh measurement, so treat it as the shared estimate both
paths now agree on.

## Default object-detection and measurement method

Use **Hermes vision's estimated bounding boxes** by default for object
recognition, annotated images, and horizontal pixel-width measurements. This
is the same method used for the original annotated can image: capture a JPEG,
pass it to `HermesChat.analyze_image`, and draw its boxes and labels with
`annotate_object_detections`. Compute horizontal width as
`(x_max - x_min) × image_width` from the normalized Hermes box.

"Hermes segmentation" here means an estimated rectangular object box, not a
pixel-level segmentation mask. Do not substitute GrabCut or manually seeded
masks by default. Use color detection only when the user explicitly requests
it; use another segmentation method only when explicitly requested.
For a current-position observation, capture the stream without moving the
arm; the normal camera-analysis action otherwise moves to Check front.

## First test: observe only

1. Leave the chassis stationary and keep the arm's working area clear.
2. Run `POST /api/pose/check_ground` with `{"duration":0.8}`.
3. Wait at least 1 second for the movement to settle.
4. Capture a fresh JPEG from the existing webpage camera stream.
5. Analyze that image with Hermes vision, returning object labels, normalized
   bounding boxes, and an annotated image.
6. Describe the scene and identify any object closest to image center
   `(0.5, 0.5)`. For each candidate box, compute its center as
   `((x_min+x_max)/2, (y_min+y_max)/2)`.
7. Stop here: no Cartesian pickup move, gripper close, lift, or Home command.

Do not use `/api/camera/analyze` or the normal `analyze_camera` agent tool for
this test: those currently move to **Check front** before taking the image.
Check front now uses the same pulses as Check ground except servo 3=`1200`
instead of `500`, and also opens gripper servo 1 to `2200`. The pixel scale
and 2 cm placement rule above apply only to Check ground, not Check front.
Instead, capture from `/api/camera/stream` after Check ground and pass that
JPEG directly to `HermesChat.analyze_image`.

## Earlier pickup proposal (superseded)

The following records the original proposal. Use the repeatable skill above,
not this older `(0, 15, 1)` sequence, for new tracking-and-grabbing requests.

1. Stage one small object at the calibrated pickup point and observe it using
   the steps above. Record its image location and dimensions.
2. Validate inverse-kinematics solutions and calibrated servo limits for both
   `(0, 15, 1)` and `(0, 15, 20)` before issuing either physical movement.
   Select and record the pickup/lift pitches: XYZ alone does not specify the
   gripper orientation. A requested pitch of 0° is only a preference when the
   controller searches a pitch range.
3. Confirm on the actual robot that the open jaws align with the object at
   `z=1`, the arm clears the ground/chassis, and the lift path is clear. A box
   centered in the image does not by itself establish the correct distance,
   object height, or jaw alignment because the camera sits above the gripper.
4. With the gripper open, move to `x=0, y=15, z=1`, initially using a slow
   1.5–2 second movement and the validated pitch. If needed, first test an
   elevated approach at the same X/Y and descend separately.
5. After the arm settles, close gripper servo 1 to the then-current `1500` preset
   over 0.5 seconds; wait for closure to finish.
6. Lift while keeping the gripper closed to `x=0, y=15, z=20` using the
   validated lift pitch, initially over 1.5–2 seconds.
7. Check that the object is held and the arm is stable. Stop at the lift pose;
   do not automatically release it or return Home.

Only execute this pickup phase when explicitly requested. If a command fails,
do not blindly continue to subsequent steps. The chassis Stop action does not
cancel an already-issued PWM arm movement.

## Test record

- Observation-only test: completed on 2026-09-16. The controller accepted all
  five saved pulses; a JPEG was captured after the 1.1-second settling wait.
- Hermes vision (`openai-codex`, `gpt-5.6-terra`) detected a red beverage can
  and a light wooden surface (labelled "wooden tabletop" by the model). The
  image does not establish whether this surface is floor- or table-height.
- Can box: normalized `(x_min=0.254, y_min=0, x_max=0.679, y_max=1)`.
  The visible box's center is `(0.4665, 0.5)`, close to the image's horizontal
  center. The can is clipped at both the top and bottom; consequently, its
  true vertical center, full height, distance, and jaw alignment are unknown.
- Captured image for this run:
  `/tmp/masterpi-check-ground-cufgco9j/camera.jpg`.
  Annotated image: `/tmp/masterpi-check-ground-cufgco9j/annotated.jpg`.
  These are temporary artifacts, not persistent calibration data.
- Pickup and lift sequence: not executed.
