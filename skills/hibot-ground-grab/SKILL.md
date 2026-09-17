---
name: hibot-ground-grab
description: Grab an object off the floor: find, center, pick up, verify. Camera-guided ground pickup for HiBot - a toy, can, block, or any object lying on the floor or ground. Required for any request to grab, pick up, fetch, or get a named object from the floor: the grab_from_ground quick action is blind and cannot find or center on it. Uses Hermes estimated boxes, a Check ground look first, Check front chassis alignment only when needed, and the operator's Cartesian pickup/lift sequence. Observation-only requests do not authorize motion.
---

# HiBot ground-object tracking and pickup

Run one requested pickup at a time. Writing/updating this skill or asking what
the robot sees does not authorize a live pickup. A request to perform this whole
workflow authorizes its staged motions; honor later stop/observation-only
instructions immediately.

**One pickup per authorization. A failed or unconfirmed grasp does not
authorize another one.** Stop, report what the camera showed, and ask the
operator before retrying - the original "grab the toy" does not carry over,
because a failed attempt usually moved the object and the operator can see
things the camera cannot. Never retry silently, and never chain attempts.

## Current robot setup

- Project: `/home/pi/projs/hiwonder_masterpi`; Python:
  `masterpi/.venv/bin/python`. Controller: `http://127.0.0.1:8000`.
- Use `masterpi_control.agent_client.HibotAgentClient` or the shared MCP tools
  for named actions. `drive_for` stops after each bounded movement. Use speed
  setting `40`, durations at least `0.05` seconds; never emulate a held button.
- Current names: `forward`, `backward`, `left`, `right`, `rotate_left`,
  `rotate_right`. Left/right strafe are distinct from left/right rotation.
  Current raw headings are left `0`, right `180`, forward `90`, backward `270`.
- Shared gripper presets: servo 1=`2500` open, servo 1=`500` closed. Check
  front/ground retain their separate observation setting of `2200`.
- Check front: servos 3=`1200`, 4=`2500`, 5=`1500`, 6=`1500`, gripper 1=`2200`.
  Check ground: same except servo 3=`500`. Both open the gripper; do not invoke
  them while carrying an object. Allow about one second to settle.
- These are the current operator-defined settings; older platform skill notes
  may contain superseded headings, poses, and rotation calibrations.

## Perception without unintended arm movement

Use **Hermes vision estimated bounding boxes by default**, not GrabCut, manual
masks, or HSV/color detection. Use color detection only if explicitly requested.
Rough visual tilt estimates suffice; do not add fine-grained segmentation just
to optimize small residual errors.

Run `scripts/observe.py` with the project Python. It captures the current MJPEG
stream, calls `HermesChat.analyze_image`, saves raw/annotated JPEGs, and prints
objects and pixel measurements. Inspect the image as well as the estimates.
This helper never commands motion. Installed scripts are relative to this skill.

Do **not** call `analyze_camera` or `/api/camera/analyze` during ground alignment:
they automatically move to Check front. Use the observation helper instead.
For a frame of width W and height H, compute:

- Box center: `((x_min+x_max)*W/2, (y_min+y_max)*H/2)`.
- Box bottom-center: `((x_min+x_max)*W/2, y_max*H)`.
- Span: `((x_max-x_min)*W, (y_max-y_min)*H)`.

Identify the requested object semantically and keep the same target throughout.
If multiple similar objects make identification ambiguous, ask which to use.
An axis-aligned box does not determine tilt: estimate the long axis from the
image (near-base center to far-end center). Signed image tilt is approximately
`atan2(far_x-near_x, near_y-far_y)` in degrees, negative left, positive right.
This is image orientation, not an independently calibrated floor yaw angle.
A box at the edge may indicate clipping, not a visible physical object bottom.

## Operator-observed calibrations

At the current setup and speed setting 40:

| Command | Observed travel |
|---|---|
| Forward/backward, 0.2 s | approximately 8 cm |
| Left/right strafe, 0.1 s | approximately 2 cm |
| Left/right rotation | 190 degrees/s (0.1 s is about 19 degrees) |

Initial duration estimates are `forward_cm/40`, `lateral_cm/20`, and
`rotation_degrees/190`. The `move` and `rotate` actions apply exactly these
conversions for you: `move(direction, distance_cm=...)`,
`rotate(direction, degrees=...)`, or either with `seconds=...` instead. They
are the same open-loop estimates, so keep observing after each correction. These are empirical starting estimates, not odometry
or guarantees of linear response, especially for short pulses. Translation
differs from the nominal speed label; do not substitute `40 mm/s` arithmetic.
Clamp fine corrections to at most `0.1` seconds, then observe again. If a
calculated correction is below 0.05 s and already within tolerance, stop.

For the uncropped 640 x 480 camera image:

- Check front near **center-bottom**: 124 horizontal pixels = 6 cm;
  approximately 0.0484 cm/pixel. Can reference length: 14 cm over approximately
  206 vertical pixels, or 0.0680 cm/pixel. These conversions hold only near
  center-bottom with matching depth and orientation, not across the whole
  image. The vertical object ratio is not a validated floor-travel mapping.
- Check front bottom edge: approximately 23 cm in front of the robot.
- Check ground reference: 272 horizontal pixels = 5.5 cm; approximately
  0.0202 cm/pixel (49.45 pixels/cm) at the reference object's depth. Equal
  vertical scale is only an approximation at that same depth/plane.
- Check ground physical object bottom just visible at the bottom edge:
  approximately 2 cm in front of the car.
- **New operator pickup reference:** the **center** of the Check ground image
  corresponds to arm `(x=2, y=13, z=0)` cm. This supersedes `(0,12,0)`.
  Do not confuse this with the
  bottom-edge distance reference, or transfer front pixel scales to ground.

## Staged workflow

1. **Look at the ground first.** Run `check_ground(duration=0.8)`, wait about
   a second, then use the pose-preserving observation helper. The object is
   often already under or just ahead of the gripper, and the ground view is
   the one whose center maps to the pickup point. If the target is visible
   here, skip the Check front stage and the 20 cm approach and go straight to
   **Fine-align for pickup**. Only when the target is absent, clipped at the
   top edge, or clearly too far to correct with small strafes do you continue
   to the Check front stage below.
2. **Observe and align in Check front.** If not already there, run
   `check_front(duration=0.8)` and wait for settling, then observe. Target the
   object's near-base/bottom-center at image `(W/2, H-1)` and roughly vertical
   long axis. Stop aligning once tilt is **within 10 degrees** and approximate
   positional offset is **within 5 cm of bottom-center**. Do not chase perfect
   pixels. Treat centimetre estimates outside the calibrated center-bottom
   region as unreliable; use small corrections and fresh images instead.
   Object left in image usually needs a left strafe; right needs right strafe.
   Forward tends to move its base downward; backward upward. Rotation changes
   position as well as tilt, so rotate, observe, and then recenter rather than
   issuing a precomputed multi-step blind trajectory.
3. **Approach approximately 20 cm once.** After front alignment succeeds,
   `drive_for(direction="forward", duration=0.5, speed=40)` is the starting
   estimate from the operator's 8 cm/0.2 s calibration. Stop. Do not accidentally
   repeat this full approach when resuming or correcting ground alignment.
   Keep the approach clear; a person/foot intersecting it requires stopping.
4. **Return to Check ground.** Run `check_ground(duration=0.8)`, wait about a
   second, then use the pose-preserving observation helper again.
5. **Fine-align until the object center is at the frame center.** The pickup
   coordinate is fixed, and it is the point the **center of the Check ground
   image** maps to. So the alignment target is the selected object's own
   center - its grasp center, not a visible fragment, a limb, or a bounding
   box that happens to include one - sitting at image center `(W/2, H/2)`.
   This is a gate on the pickup, not a preference: if the center is off, the
   jaws close beside the object and push it away.

   - Require the center offset **within 1 cm on both axes**. At the ground
     reference that is about **49 pixels per cm**, so about 49 pixels radial.
     Use the reference only when its depth/plane matches.
   - **A clipped box has no usable center.** A box touching any image edge
     (`x_min=0`, `y_min=0`, `x_max=1`, `y_max=1`) means part of the object is
     outside the frame and its true center is farther out than the box says.
     Correct until the whole object is inside the frame, then judge the center.
   - Corrections: object left of center needs a left strafe, right needs
     right. An object high in the frame is too far away and needs a small
     forward pulse; low in the frame is too close and needs backward. Move,
     re-observe, and re-measure - short pulses respond nonlinearly, so a
     0.1 s strafe may shift the target far less than its 2 cm nominal.
   - Re-observe after every correction and stop only when a fresh frame shows
     the center inside tolerance. Keep tilt within 10 degrees; no new
     color-detector gate is needed.
6. **Grab and lift.** Stop the chassis; with the gripper open, move the arm to
   **`(2, 13, 0)`**, wait for settling, close gripper using its shared `500`
   preset, wait, then move to **`(0, 15, 20)`** while keeping it closed. Finish
   at the lift pose, not Home, and do not release. Use
   `scripts/grab_ground.py --execute` with the project Python for this exact
   sequence; without `--execute` it prints the plan and performs no I/O. After
   the lift it captures **two** pose-preserving frames about 1.5 seconds apart
   and prints them as `confirmation_images` with a `stale_stream` flag; step 7
   requires them.
   It uses `/api/arm`: pickup pitch preference `-68`, range `[-90,-68]`; lift
   pitch preference `0`, range `[-90,90]`. Offline IK including servo calibration
   offsets finds -68 degrees valid at `(2,13,0)`; the old -66 degrees exceeded
   servo 4's 2500 limit. XYZ does not establish gripper orientation. Respect
   IK errors and do not guess substitute joints or coordinates.

   Pickup depth is `--pickup-z`, default `-1` cm: the operator-tuned value that
   matches the webpage quick action and the one confirmed pickup. Observed so
   far on a stuffed toy: `0` pushed it away without gripping, `-1` held once
   and missed once on the flatter middle. Treat the depth as lightly validated,
   not proven across object shapes.
7. **Confirm with the camera, then report.** API completion proves commands
   ran, never that the object is held, so a camera check after the lift is
   required, not optional. Look at the helper's `confirmation_images` yourself
   and analyze them (`scripts/observe.py` captures and analyzes a fresh frame
   the same way); both only read the stream and command no movement. Read
   controller state too: chassis stopped, gripper at `500`, arm at the lift
   pose, no `last_error`.

   **The stream can stick on an older frame**, which is why the helper takes
   two. A single frame captured right after the lift can still be showing the
   pre-lift view - the object filling the near field while the arm was down at
   it - and that reads as a hold that never happened. Rules:

   - Judge from the **later** frame, and only when it differs from the first.
   - `stale_stream: true`, or two frames that look the same, means the stream
     is stuck: it proves nothing either way. Capture again with
     `scripts/observe.py` until frames change, and report the grasp as
     unconfirmed until they do.
   - A first frame showing a hold followed by a later frame showing the room
     is **not** evidence of a hold that was then lost. The likelier reading is
     a stale first frame; say which frames you used and what each showed.

   The gripper camera sits just above the jaws, so at the lift pose a held
   object **fills the near field** while the room and floor recede behind it.
   Treat these as the outcomes:

   - Target large and close, moving with the arm: report it as held.
   - Target small, far below, or on the floor with the background at normal
     scale: the grasp failed or it was dropped. Say so.
   - Jaws or contact not actually visible, or the view is ambiguous: report
     retention as probable or unconfirmed and say what the frame does show.
     Do not upgrade an inference into a confirmed hold.

   If the confirmation capture itself fails, the helper prints
   `confirmation_error` and the pickup still stands: report the missing
   evidence rather than assuming either outcome. The same goes for a stuck
   stream: missing evidence is not evidence of either outcome. Do not call
   Check front/ground after a successful grasp: they open the gripper and would
   drop the object.
8. **On a failure, stop and ask before retrying.** Keep the current gripper and
   arm state, report which stage failed and what the camera showed, say where
   the object is now, and **ask the operator for permission to try again**. Do
   not start another attempt on your own.

   Offer one concrete adjustment with the ask, changing **one thing at a time**
   so the outcome stays attributable:

   - **Re-approach.** A missed grasp usually shoves the object forward, out of
     the pickup zone. Re-center it before anything else; alignment is the most
     common cause, not depth.
   - **Lower the pickup 1 cm** (`--pickup-z -2`). This is the adjustment for a
     flat or soft target the jaws slid over - they need to close under the
     object, not on top of it. Stay within the helper's `-3` floor; deeper
     drives the jaws into the ground.
   - **Aim at a bulkier part.** Center a limb, boot, or head rather than a flat
     middle, so the jaws have something with thickness to close around.

   After an authorized retry, report the adjustment you made and its outcome,
   so the depth and alignment notes above stay grounded in what happened.

The webpage's **Grab object** button and a chat request to grab something both
run this same procedure server-side (`POST /api/grab/object`,
`POST /api/chat/stream`): Check ground first, the Check front stage and
approach when the ground view is empty, bounded centring, pickup, paired
confirmation - with every step streamed into the chat log. Its **Quick grab
action** button is the blind fixed-point pickup, not this.

Two rules that implementation learned the hard way, and that apply when you run
the stages by hand too:

- **A large box touching an edge is a close object overflowing the view, not a
  sliver of a distant one.** Only treat clipping as "approach again" when the
  visible part is small; otherwise trust its centre. Forcing another approach
  on a can that filled the ground view walked the robot straight past it.
- **If a correction does not change the measured offset, stop.** Two identical
  readings mean the model's boxes, not the chassis, are the limit; further
  pulses only push the object around.

**Do not substitute the `grab_from_ground` MCP quick action for step 6.**
It picks up at the same `(2,13,-1)` depth but skips the Check ground look, the
alignment, the camera confirmation, and the `(0,15,20)` lift finish: it is the
final motion alone. Use the helper.

Do not issue other robot commands concurrently with this sequence. Stop on
target loss, actuator failure, a blocked approach, or lack of progress; do not
keep pulsing indefinitely. Limit each alignment stage to eight corrective
steps, then report the remaining error and request direction. On partial
pickup failure, stop the chassis, preserve the current gripper/arm state, and
report the failed stage rather than opening the gripper. Every retry needs its
own operator authorization, however small the adjustment.

Example invocation: "Use hibot-ground-grab to track and pick up the orange can."
