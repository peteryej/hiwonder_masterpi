# MasterPi progress

2026-09-16

## Guided grab now runs the Check front stage too

- The server-side `grab_object` only implemented the skill's ground phase, so
  an object outside the Check ground view (which covers a few cm in front of
  the gripper) was reported missing instead of approached. It now follows the
  skill: ground look, and when that finds nothing, `_approach_from_front` moves
  to Check front, selects the target there, strafes until its base is within
  the 5 cm bottom-centre tolerance (front calibration 20.67 px/cm), drives
  forward once, and returns to Check ground. The stage runs at most once per
  attempt, as the skill requires.
- The approach distance is 20 cm, or 8 cm when the box touches the bottom edge:
  that means the object is at or nearer than the ~23 cm bottom-edge reference,
  where the full approach would drive into it.
- A live run exposed two bugs in the ground loop, both now fixed and covered:
  a large box always touches an edge, and the clipping rule kept forcing
  "forward 2 cm" on a can that filled the view, burning all six corrections and
  walking the robot past it - clipping now only forces an approach when the
  visible part is a sliver (area < 0.30). And two corrections that leave the
  measured offset unchanged now stop the run, instead of pulsing at an object
  the boxes cannot localise better.
- Tests 181 to 185: the front fallthrough with its pose sequence (ground,
  front, ground), the short approach for a close object, the front strafe, a
  target in neither view, the sliver-then-stall case, and a large clipped box
  being picked up without corrections.

## Chat grab requests run the skill, not the can profile

- Asked in web chat to "grab the orange can", the reply described running the
  recorded can pickup - the blind pose - rather than the guarded ground-grab
  procedure. Same shape as the movement bug: the chat path had no grab action,
  so the request went to the model, which answered from whatever it believed it
  had.
- `chat_grab_request` now recognises grab/pick up/fetch/retrieve with an object
  phrase (negations excluded, "get" deliberately not a trigger so "get the
  state" stays a question), and `run_chat_grab` runs `grab_object` with that
  target and summarises the outcome. The blind can pose is unreachable from
  chat now.
- Target words are matched by significant word, not exact phrase
  (`label_matches_target`): "the orange can" matches "red beverage can", since
  the operator's word for a thing rarely matches the model's. When nothing
  matches the named target but one graspable object is present, it picks that
  and says so - result carries `target_requested` and `target_substituted`,
  and the reply states the swap - rather than refusing over vocabulary.
- Typed chat now goes through `/api/chat/stream`, so a chat-triggered grab
  prints its steps live in the log like the button does; plain messages stream
  a single final event and render exactly as before. The NDJSON writer was
  generalised to `_stream_ndjson` and shared by both routes.
- Tests 177 to 181. Verified live: "grab the orange can" streamed start ->
  observe (with annotated frame) -> done, reported "no object found in the
  Check ground view" with nothing in view, and never touched the can profile.

## New chat button

- The web chat continues one long-lived Hermes session (`--continue
  hibot-web-safe`), so stale context kept steering replies - the obstacle
  refusal survived in that conversation even after the files behind it were
  fixed. Added a **New chat** button in the chat panel header: it posts to
  `/api/chat/new`, which calls the existing `HermesChat.new_session()` to switch
  to a fresh uuid-suffixed session, then clears the on-screen log and confirms
  the change. Voice is untouched; it already opens a session per wake word.
- Tests 176 to 177: the endpoint returns a new session each call, and the page
  carries the button and its handler. Verified live: two clicks returned
  distinct sessions (`hibot-web-safe-0e7bec68...`, `...-dd9a2b37...`) and a
  chat message on the fresh session answered normally.

## Web chat can actually move the robot

- Root cause of the web chat's "I can't safely strafe left": the chat path had
  no movement action at all. `chat_action_name` recognised only `nod` and
  `shake`, so "move left 10 cm" went to Hermes as plain text with no robot
  tools attached, and the model wrote a refusal, reaching for the camera
  context in its session to justify it. Reproduced directly: the same CLI
  invocation the web chat uses answers "the robot-control interface isn't
  available in this session" - the MCP tools belong to the gateway, not to
  `hermes chat --toolsets safe`.
- Added `chat_motion_request` + `run_chat_motion` to the chat path: an
  explicitly named move or rotation is parsed (direction, plus cm, degrees, or
  seconds) and executed through `robot.move`/`robot.rotate` before the message
  ever reaches the model. No camera check, no refusal path. The reply states
  what was done, the converted duration, and that it is a timed estimate rather
  than odometry. A bare "move left" uses a stated default (10 cm, 45 degrees)
  instead of asking. Negations ("do not move left") and ordinary questions
  still go to Hermes untouched.
- Also fixed the two places that taught the agent to gate movement on
  perception: `~/.hermes/SOUL.md`, whose Safety section said "Check relevant
  sensor or camera context before movement", is now an Acting section saying an
  explicitly commanded bounded action runs immediately and is never declined
  over what an image seems to show; sensor checks are for motion the agent
  chose itself. In `~/.hermes/memories/MEMORY.md`, the "physical actions
  require safety care" line was rewritten the same way, and the stale memory
  claiming `drive_for` only turns one wheel was replaced with the corrected
  mapping and a pointer to `move`/`rotate`.
- Tests 172 to 176. Verified live: "move left 10 cm" -> "Moved left 10 cm
  (0.5 s at speed 40)", "turn right 90 degrees" -> "Rotated right 90 degrees
  (0.474 s at 190 degrees/s)", and "do not move left" still just replies.

## Explicit movement commands are no longer perception-gated

- Asked to "move left 10 cm", the Hermes agent replied that it did not move
  because the camera showed a chair leg and a person nearby, and asked the
  operator to clear the left side. The source was `robot-physical-control`
  item 2 ("Before chassis motion, obtain fresh sensor/state information ...
  Treat any detected obstacle as real") plus the navigation bullet telling it
  to ask the operator to clear the rear before backing up.
- The skill now opens with "Explicit commands execute as given": when the
  operator names the action and its values, run it immediately - no camera
  frame first, no weighing of the last frame, no asking to clear the area. The
  stated reason is that the operator can see the robot and its surroundings
  while the agent has one stale low-angle frame, so refusing on that basis
  withholds a movement the operator can see is safe. It also forbids
  substituting a smaller distance, another direction, or obstacle avoidance,
  and allows noting what it saw afterwards as an observation, never as a
  precondition.
- Item 2 now applies only to open-ended motion the agent chose itself
  (approaching a sound, exploring, crossing a room). Refusals are reserved for
  real blockers: a controller rejection, an actuator failure mid-motion, an
  unbounded request, or a stop instruction. The rearward-sensor bullet now says
  to keep the move bounded and note it was not sensor-validated, rather than
  withhold it. The reporting section adds that "the camera shows something
  nearby" is not a blocker for a commanded bounded move.
- The autonomous approach inside `hibot-ground-grab` keeps its own clearance
  rule: that motion is the agent's choice, not an operator command. Installed
  copy synced; skills are read per session, so no gateway restart.

## Grab object streams its steps into the chat panel

- A guided grab moves the robot for tens of seconds. Returning one verdict at
  the end hid what it saw and why it moved, so `grab_object` now takes an
  `on_event` callback and emits a step at each stage: the Check ground look
  (with the annotated frame), every observation as "saw <label> at +x cm
  across, y cm away - correcting", each chassis correction and its distance,
  the pickup coordinate, and the verdict. Callback errors are swallowed: a
  listener must not abort a run that is already moving the arm.
- `POST /api/grab/object/stream` writes those events as newline-delimited JSON
  and flushes each one. `POST /api/grab/object` stays the plain blocking call
  for agents. Because the stream's response is committed before the work runs,
  a failure arrives as a final `{"stage":"error","ok":false}` event rather than
  an HTTP status the client would never see.
- The webpage reads the stream and renders each event in the chat log -
  annotated frames included, via the existing `addChatImage` - with the arm
  status line following along. Failures and the correction-limit stop render as
  error messages.
- Tests 170 to 172: the stream reports each step before the result with its
  image payload, and a mid-run failure lands as a final error event. Verified
  live against the robot camera: asking for a target that is not there streamed
  `start` then `observe` with a 19 KB annotated frame and stopped without
  moving the arm.

## Webpage grab actions: guarded Grab object, blind Quick grab action

- Removed the **Grab can** button and its handler. The recorded can pose is not
  deleted - `grab_from_front` still runs it - but every description that called
  it "the webpage's Grab can button" was corrected, in the MCP docstring, the
  shared tool description, the realtime voice prompt, the README, and the
  robot-physical-control reference.
- Renamed the old **Grab object** button to **Quick grab action**. Same blind
  fixed-point behaviour, a name that matches what it does, and demoted to the
  secondary style so it no longer reads as the default way to pick something up.
- New **Grab object** button runs the skill's procedure server-side via
  `POST /api/grab/object` (`VisionGrasper.grab_object`): Check ground, pick the
  target from Hermes boxes, drive bounded chassis corrections until the centre
  is within 1 cm, pick up at `(2, 13, -1)`, lift to `(0, 15, 20)` still
  holding, then confirm from two spaced frames. It reports the target, the
  corrections, and the verdict; the button surfaces all three.
- Details that carry the lessons of the failed grabs: a box touching any image
  edge is treated as clipped and drives a correction instead of trusting its
  centre; background labels (floor, wall, shadow, table...) and near-full-frame
  boxes are excluded from target selection; identical confirmation frames
  report `confirmed: null` (stuck stream) and never count as a hold; the
  alignment loop stops after six corrections and reports the remaining offset.
  One attempt only - `retry_requires_authorization: true` in the result.
- `pickup_z` is a parameter (default `-1`, band `-3`..`2`), so an authorized
  retry can lower it exactly as the skill helper does.
- Tests 161 to 170: eight for the guided flow (centred pickup, bounded
  correction, clipped box, missing object, stuck stream, empty near field,
  depth bound, named target) plus the page and route assertions. Verified live:
  the page shows Grab object and Quick grab action with no Grab can, and the
  new route rejects an out-of-band depth.

## Retries need operator permission, and the helper has a depth knob

- `hibot-ground-grab` now states "One pickup per authorization. A failed or
  unconfirmed grasp does not authorize another one." The original request does
  not carry over, because a failed attempt usually moved the object and the
  operator can see what the camera cannot. New step 8 requires stopping,
  reporting the failed stage and what the camera showed, saying where the
  object is now, and asking before any retry.
- The ask must offer one adjustment, one thing at a time: re-approach (the most
  common cause - a miss shoves the object out of the pickup zone), lower the
  pickup 1 cm to `-2` for a flat or soft target the jaws slid over, or aim at a
  bulkier part. After an authorized retry, report the adjustment and outcome.
- `scripts/grab_ground.py` grew `--pickup-z` (default `-1`, band `-3` to `2`),
  so a retry adjusts depth through the tested helper instead of a scratchpad
  override. The default moves from `0` to `-1`, matching the webpage quick
  action and the one confirmed pickup; the skill records what each depth did
  (`0` pushed the toy away, `-1` held once and missed once on flat plush).
- Corrected the stale note claiming the skill still picks up at `(2,13,0)`, and
  the plan doc's step 5. Helper tests 10 to 12: the retry depth changes only
  the pickup (lift and approach unmoved) and the safe band is enforced.

## Single 190 degrees/s yaw calibration

- Operator decision: both rotation paths now use 190 degrees/s.
  `ROTATION_CALIBRATION_DEGREES_PER_SECOND` is 190, and `approach_bearing`
  divides by that same constant instead of its old hard-coded 180, so a
  bearing turn and a `rotate(degrees=...)` turn can no longer disagree.
- Replaces two conflicting figures: this plan's 200 degrees/s (0.1 s = 20
  degrees) and the platform notes' `180 degrees = 1.0 s`. 190 is a chosen
  value, not a fresh measurement; docs say so rather than implying it was
  measured.
- Callable range moved with it: 9.5-1520 degrees (0.05-8 s at 190/s). The
  generic amount bound was widened so the calibration check is what reports
  the limit - asking for 1600 degrees now says "at most 1520 degrees" instead
  of a generic range error. Tool schemas carry the real maxima (320 cm, 1520
  degrees).
- Updated in code, tool descriptions, MCP docstrings, README,
  `docs/grab_object_plan.md`, the ground-grab skill's calibration table, and
  the robot-physical-control reference; installed skill copies synced. 161
  tests pass.

## Distance and angle chassis actions (move, rotate)

- Added `move` and `rotate` to the shared tool set, so MCP/voice callers can
  ask for centimetres or degrees instead of hand-converting to seconds. Each
  takes exactly one of the amount or `seconds`; both or neither is an error.
  `move` covers forward/backward/left/right, `rotate` is pure yaw.
- Conversions are the `docs/grab_object_plan.md` calibrations: 40 cm/s
  forward/backward, 20 cm/s strafing, 200 degrees/s rotation, all at speed
  setting 40. Results carry the requested amount, the calibration used, and
  `distance_measured`/`angle_measured: false` - these are timed estimates with
  no odometry or gyro behind them.
- Two deliberate refusals instead of silent fudging: a distance request at a
  speed other than 40 is refused (the calibration does not hold there - pass
  seconds), and an amount whose duration falls outside 0.05-8 s is refused
  with its callable range rather than clamped, since clamping would move a
  different amount than asked. Ranges: 2-320 cm forward/backward, 1-160 cm
  strafe, 10-1600 degrees.
- Wired through `Robot.move`/`Robot.rotate`, `/api/agent/move`,
  `/api/agent/rotate`, `ROBOT_TOOL_DEFINITIONS`, and MCP wrappers that omit the
  unused half of the pair. Tests 153 to 161, covering the conversions, both
  refusals, the schemas, the HTTP routes, and the MCP payloads.
- Unreconciled conflict, left as-is and documented: `approach_bearing` still
  uses the older `180 degrees = 1.0 s` platform figure while `rotate` uses this
  plan's 200 degrees/s. Both are operator-supplied; neither was re-measured.

## The refusal came from robot-physical-control, not the tool descriptions

- After the tool/description fixes, the Hermes agent stopped calling the blind
  quick action but still declined: "the available pickup action is a blind
  fixed-coordinate grab ... Please place the toy at my marked pickup point."
  That wording traces to the OTHER robotics skill, `robot-physical-control`,
  whose camera-guided item 6 said "Ask the operator to stage the item on a
  clear surface at the calibrated pickup point." Its description ("Use when
  controlling a robot") matches any robot request, so it loads every time.
- `robot-physical-control` now opens its camera-guided section with "Check for
  a companion pickup skill first": a request to grab a named object off the
  floor means loading `hibot-ground-grab` and following it, and seeing the
  object but reporting it cannot be picked up without having loaded that skill
  is called out as a failure to complete the request. Item 6 now distinguishes
  a missing calibrated pickup from an object merely off the pickup point - the
  companion skill drives the robot until the object is at that point. The
  reporting section forbids declining a floor pickup as "unstaged" until that
  skill's alignment has actually failed.
- Fixed stale values in its MasterPi reference while there: check-front was
  listed as servo 3=500, 5=810 with no gripper entry (actual: 3=1200, 5=1500,
  1=2200), and the can profile's gripper as 2000/1500 (actual: the shared
  2500/500). Added a ground-pickup section pointing at the skill.
- That skill lived only in `~/.hermes/skills/`; copied it into the repo at
  `skills/robot-physical-control/` so it is version-controlled like the other.
  Skill files are read from disk per session, so no gateway restart is needed -
  a new Hermes conversation picks this up.

## Why the Hermes agent ignored the ground-grab skill

- Hermes truncates every skill description to 60 characters in its
  system-prompt index (`SKILL_PROMPT_DESC_LIMIT` in
  `agent/skill_utils.py`). Ours rendered as "Track and pick up a staged ground
  object with HiBot's gri..." - the trigger clause "Use for requests to locate,
  align with, or grab a ground object" was cut off entirely, so a "grab the
  toy" request matched nothing in the index.
- Meanwhile `grab_from_ground` advertised itself as a ready-made pickup, so the
  agent called the blind quick action, which closes the gripper at a fixed
  coordinate and never looks for the object.
- Fixes: the skill description now leads with "Grab an object off the floor:
  find, center, pick up, verify" (first 57 characters carry the trigger) and
  states that the quick action cannot find or center on an object. Both grab
  tools - MCP docstrings and the shared `robot_tools` descriptions - now start
  with "Blind" and point at the hibot-ground-grab skill for a named object.
  A `test_robot_tools` assertion pins both properties.
- Verified the rendered index via Hermes's own `build_skills_system_prompt`:
  the line now reads "Grab an object off the floor: find, center, pick up,
  veri...". The MCP tool descriptions reach Hermes from the `hibot_mcp`
  subprocess the gateway spawned at 18:24, so they need a gateway restart.

## Confirmed ground grab of the toy at z=-1

- Ran the updated workflow: Check ground, one 0.1 s forward pulse to bring the
  whole toy into frame, full-object center at `(308, 238)` against `(320, 240)`
  - about 0.25 cm, the first attempt today with an unclipped box - then the
  pickup at `(2, 13, -1)` with the frame center on the furry torso below the
  plaid cape.
- Confirmed held. Two capture pairs, seconds apart, both `stale_stream: false`,
  all four frames differing: the toy fills the near field with the floor
  receding behind it, against the bare-floor signature of the failed attempts.
  Controller state: chassis stopped, gripper `500`, arm at `(0, 15, 20)`, no
  error. The jaws are still not in view, so the hold is inferred from near-field
  fill, but it is corroborated across two independent pairs.
- What changed between the failures and this run: the whole object in frame
  rather than a clipped box, a centered grasp point on thick plush rather than
  the flat cape, and the 1 cm-lower pickup. The helper still ships `z=0`; this
  run used a scratchpad override.

## Paired confirmation capture and a hard centering gate

- The camera stream can stick on an older frame, so a single post-lift capture
  can show the pre-lift view - the object filling the near field while the arm
  was still down at it - and read as a hold that never happened. This bit a
  real run today: the retry's one confirmation frame showed the toy filling the
  frame, every later frame showed the empty room, and the toy is now on the
  floor beyond the pickup zone. The stale-frame reading is the likelier one;
  that run should not have been reported as a confirmed grab.
- `capture_after_lift` now takes **two** frames about 1.5 s apart, returns
  `{"images": [...], "stale_stream": bool}`, and flags byte-identical frames as
  a stuck stream. Step 7 of the skill says to judge from the later frame, treat
  a stuck stream as proving nothing either way, and read a hold-then-room
  sequence as a stale first frame rather than a hold that was lost.
- Step 5 is now a gate, "Fine-align until the object center is at the frame
  center": the object's own grasp center within 1 cm on both axes (about 49
  pixels at the ground reference), a clipped box explicitly has no usable
  center, and corrections must be re-measured on a fresh frame because short
  pulses respond nonlinearly. Both failed grabs today had the jaws closing
  beside a target whose center was off or whose box was clipped.
- Helper tests 8 to 10: two spaced captures with the wait between them, and
  stuck-stream detection. `capture_after_lift` takes the frame source, sleep,
  and delay as arguments so the tests stay hardware-free. Live check against
  the arm at the lift pose returned two differing frames, `stale_stream:
  false`, both showing the room - nothing held. Installed copies synced.

## Ground-grab skill confirms the hold from the camera after the lift

- Step 7 of `hibot-ground-grab` is now "Confirm with the camera, then report":
  a post-lift camera check is required, not optional. It states the read rule -
  the gripper camera sits above the jaws, so a held object fills the near field
  while the room recedes - and the three outcomes (held / failed or dropped /
  ambiguous), with an explicit ban on upgrading an inference to a confirmed
  hold. API success is called out as proof that commands ran, nothing more.
- `scripts/grab_ground.py` now captures one pose-preserving frame after the
  lift (reusing `observe.capture`, no movement commanded) and prints its path
  as `confirmation_image`; `main` echoes it with a "inspect before reporting"
  note. A capture failure prints `confirmation_error` and does not fail the
  pickup, since the object may still be held. A failed pickup captures nothing.
- Helper tests grew from six to eight: the confirmation path, the
  capture-failure path, and no-capture-on-failure. `execute` takes the capture
  as an injectable third argument, so tests stay hardware-free. Both installed
  copies (Codex, Hermes) synced.
- Live check of the new capture against the arm still at the lift pose: it
  returned a frame with no arm movement. That frame showed bare floor and the
  gripper reading `2500` (open) - the toy had been released after the earlier
  run, so it is the "not held" signature, not a regression of the pickup.

## Ground-grab skill now looks at the ground first, and first live pickup

- Reordered `hibot-ground-grab`: step 1 is now Check ground + observation. If
  the target is already in that view, the Check front alignment stage and the
  20 cm approach are skipped and the run goes straight to fine-alignment. The
  ground view is the one whose center maps to the pickup point, so the front
  stage is only for targets that are absent or too far for small strafes.
  Renumbered the remaining steps; synced both installed copies (Codex,
  Hermes). `docs/grab_object_plan.md` summary and step list match.
- First physical run of the pickup, on a stuffed toy: Check ground showed it
  at image `(285, 240)` against center `(320, 240)` - about 0.7 cm left, inside
  the 1 cm tolerance - so no chassis correction was needed. The ground-first
  order found it immediately; the earlier Check front frame had shown only
  floor and furniture.
- `scripts/grab_ground.py --execute` ran all five stages: stop, open, arm
  `(2,13,0)` at -68 degrees, close at 500, lift to `(0,15,20)`. Controller
  reported chassis stopped, gripper 500, arm at the lift pose, no error. The
  post-lift camera frame shows the toy filling the near field at lift height,
  consistent with a successful hold; the jaws themselves are not in view, so
  retention is probable but not visually proven.

## Grab object starts from the current pose and picks up 1 cm lower

- The web **Grab object** quick action (shared `grab_from_ground` routine) no
  longer moves Home before picking up. It stops the chassis, opens the gripper,
  and approaches from wherever the arm already is, so a hand- or jog-pad
  alignment survives into the pickup.
- `GROUND_PICKUP_XYZ` is now `(2, 13, -1)` cm, one centimetre lower. The hover
  at `(2, 13, 8)`, the `-68` degree pickup pitch with range `[-90,-68]`, the
  gripper presets, and the Home return while holding are unchanged.
- Offline IK with the vendor SDK and the saved calibration offsets: `(2,13,-1)`
  solves for pitches -86 through -68; at -68 the adjusted pulses are servo
  3=1346, 4=2483, 5=2207, 6=1467, all inside `500-2500`. Pitches below -68 are
  still rejected by servo 4, so -68 remains the preference.
- Updated the tool description, MCP docstring, README, plan doc, and the
  `hibot-ground-grab` skill cross-reference. The skill itself keeps its own
  `(2,13,0)` pickup and `(0,15,20)` lift finish.
- Verification: 153 controller tests and the six skill-helper tests pass. No
  arm movement was commanded; the lowered pickup is not yet physically tried.

## Revised Check ground center and ground-grab pickup

- The operator's Check ground image-center reference is now `(2, 13, 0)` cm,
  replacing `(0, 12, 0)`. The observation servo preset is unchanged.
- Updated the shared ground-grab routine (web Grab object and MCP/voice
  `grab_from_ground`) to approach `(2, 13, 8)` and pick up at `(2, 13, 0)`.
  Used a -68-degree pickup pitch, full-range gripper presets,
  no camera/color gate, and Home return. Returned pickup metadata matches.
- Updated `docs/grab_object_plan.md`, README, tool descriptions, and the saved
  skill/helper. The skill retains its distinct `(0, 15, 20)` lift finish.
  Offline IK finds solutions for the new approach and pickup; no live pickup
  or arm movement was performed during implementation.
- Including calibration offsets, the old -66-degree pitch would command servo
  4 to 2538; -68 degrees is the nearest valid pitch, commanding it to 2491.
  Updated both the quick action and skill helper to use that pickup preference.
- Verification: all 153 controller tests, six skill-helper tests, skill
  validation, dry run, and diff checks pass. Offline SDK execution used a
  fake board and checked adjusted pulses for approach, pickup, and lift.
  Synced installed Hermes/Codex skills and reloaded controller, voice, and
  gateway; all are active, with stopped chassis and no controller error.

## Full-range gripper presets for web quick actions and MCP

- Updated shared `Robot.gripper`: servo 1=2500 open, servo 1=500 closed.
  Web quick buttons, standard gripper controls, arm jogs, MCP/voice, and pickup
  routines all use these presets. Corrected the jog slider display and tool
  descriptions. Check front/ground observation pulses remain at 2200.
- Updated README, grab-plan notes, and the saved ground-grabbing skill to
  match. Verification uses mock hardware only; no live gripper action runs.
- Verification: 153 controller tests and six skill-helper tests pass, along
  with JavaScript syntax, skill validation, and diff checks. Synced installed
  Hermes/Codex skill notes and reloaded controller, voice, and Hermes gateway;
  all three services are active. Live webpage has the updated presets;
  read-only state confirms stopped chassis with no controller error.

## Repeatable Hermes ground-object tracking/grabbing skill

- Added `skills/hibot-ground-grab/SKILL.md` for the operator's staged workflow:
  front alignment within 10 degrees/5 cm, approximately 20 cm forward, Check
  ground, center within 1 cm, pickup `(0, 12, 0)`, close, lift `(0, 15, 20)`.
  Ground image center is the operator's new `(0, 12, 0)` arm reference.
- Added a pose-preserving camera/Hermes observation helper and an exact
  Cartesian pickup/lift helper that performs no I/O unless `--execute` is
  supplied. The existing MCP ground quick action remains unchanged and is
  explicitly not substituted for the new geometry. No color-detector gate.
- Recorded bounded correction loops, empirical distance/angle limitations,
  target-loss/failure stopping, and post-action verification without opening
  the gripper. Updated the grab plan; the original pickup proposal is historical.
- Hardware-free helper tests, skill validation, and dry-run verification pass.
  Installed and validated copies under the local Hermes robotics skills and
  Codex skills directories. Six helper tests pass; no service restart needed.
  No live approach, new ground pickup, gripper close, or lift was executed.

## Operator-observed rotation calibration

- Recorded in `docs/grab_object_plan.md`: a 0.1-second left/right rotation
  corresponds to approximately 20 degrees at the current pure-rotation
  commands (angular setting -0.6/+0.6, linear speed 0). This is an operator
  observation, separate from lateral travel. Documentation only: no movement
  or controller changes were performed for this update.

## Can vertical reference and center-bottom calibration scope

- Recorded the latest Hermes can box's vertical span: approximately 206 pixels
  (`y_min=0.494`, `y_max=0.923`, frame height 480), with the operator's 14 cm
  physical-length reference, giving approximately 0.0680 cm/pixel.
- Recorded that image conversions hold only near center-bottom with matching
  pose/depth/orientation, not throughout the image. The current can is left of
  center; its span does not independently validate that mapping. The vertical
  object ratio is not a calibrated forward-travel conversion. No movement or
  controller changes were performed.

## Operator-observed left/right travel calibration

- Recorded in `docs/grab_object_plan.md`: a 0.1-second left/right move
  corresponds to approximately 2 cm of lateral travel at speed setting 40.
  Kept this separate from the forward/backward observation. Documentation
  only: no movement or controller changes were performed.

## Operator-observed forward/backward travel calibration

- Recorded in `docs/grab_object_plan.md` that a 0.2-second forward/backward
  move corresponds to approximately 8 cm at the recent speed setting of 40.
  Identified this as an operator observation, distinct from the nominal 8 mm
  speed-based estimate; the factor-of-ten discrepancy remains undiagnosed.
  Documentation only: no movement or controller changes were performed.

## Check front bottom-edge scale and default Hermes box method

- Recorded the operator's calibration in `docs/grab_object_plan.md`: at the
  Check front image bottom, 124 horizontal pixels correspond to 6 cm, giving
  approximately 0.0484 cm/pixel and 31.0 cm across the 640-pixel bottom edge.
  This supersedes the earlier 5.5 cm/124-pixel estimate for that reference.
- Documented Hermes estimated bounding boxes as the default recognition,
  annotation, and pixel-width method, not GrabCut or manually seeded masks.
  Color detection remains an explicitly requested alternative. No controller
  behavior, services, or robot position were changed for this documentation.

## Check front distance reference and corrected lateral controls

- Recorded the operator's Check front camera reference in
  `docs/grab_object_plan.md`: the bottom image edge corresponds to a ground
  position approximately 23 cm ahead of the robot, distinct from Check
  ground's 2 cm reference and pixel scale.
- Swapped chassis left/right strafing to left=0° and right=180° in the web
  buttons, keyboard mappings, and MCP/voice `drive_for`. Mirrored the web's
  diagonals consistently. Forward/backward, rotation, arm jogs, raw numeric
  drive commands, and the four-wheel vendor mixer are unchanged.
- Updated CLI help, agent tool descriptions, and documentation to match.
- Verification passes all 151 controller tests, JavaScript syntax, and diff
  checks. Reloaded the controller, wake-word service, and Hermes gateway;
  verified the live lateral/diagonal/keyboard mappings and shared tool
  description. All services are active with no controller error. No physical
  motion or independent camera-distance measurement was performed.

## Revised Check front pose and restored quick action

- Check front now matches Check ground except servo 3=1200: servo 4=2500,
  servo 5=1500, servo 6=1500, and gripper servo 1=2200, over 0.8 seconds.
  Updated the shared controller pose, MCP/voice tool descriptions, camera
  analysis expectations, and documentation. Check ground remains unchanged.
- Restored the Check front quick-arm button alongside Check ground. Camera
  analysis continues to use Check front, now including the explicit gripper
  setting; the ground-camera calibration does not transfer to this new pose.
- Verification passes all 150 controller tests, JavaScript syntax, and diff
  checks. Reloaded all three robot/voice/gateway services and verified both
  live quick buttons and Hermes's cached Check front description with the
  exact new pulses. Services are active with no controller error; no pose or
  physical movement was executed.

## Ground-grab plan and observation-only test

- Wrote `docs/grab_object_plan.md` for the proposed Check ground → center
  inspection → `(0, 15, 1)` pickup → gripper close → `(0, 15, 20)` lift test.
  It distinguishes image centering from distance/pose calibration and leaves
  pickup/lift pitch validation and execution for a separately requested test.
- Executed only the requested Check ground observation pose and captured the
  existing camera stream after settling. Hermes vision detected a red drink
  can near horizontal image center on a wooden surface; the can is cropped at
  the top and bottom, so pickup alignment/distance are not established.
- Generated an annotated image and recorded the detections in the plan. No
  pickup, close-gripper, lift, chassis motion, or Home command was executed.

## Check ground replaces the Check front quick button

- Removed the Check front button and its JavaScript handler from the webpage.
  Check ground now occupies its quick-arm slot, with the saved servo/gripper
  values unchanged. Check front remains available to camera analysis and
  MCP/API requests.
- Verification passes all 149 controller tests, JavaScript syntax, and diff
  checks. Restarted the web controller and confirmed its live page contains
  exactly one Check ground button and no Check front button/handler. No arm
  movement was triggered.

## Recorded Check ground arm preset

- Saved Check ground as servo pulses 3=500, 4=2500, 5=1500, 6=1500,
  and gripper servo 1=2200. Added the quick-arm button, manual pose API,
  loopback agent route, and shared MCP/voice tool with a 0.8-second default.
- Check front remains 3=500, 4=2500, 5=810, 6=1500 and leaves the gripper
  unchanged. Corrected its stale agent-tool description, which said 5=1350.
- Standard gripper open/close presets and both grab actions are unchanged.
- Verification passes all 149 controller tests and the embedded JavaScript
  syntax check. Reloaded the controller, wake-word listener, and Hermes gateway;
  all are active, the live webpage includes Check ground, and Hermes caches
  20 tools including `check_ground`. No pose or physical movement was executed.

2026-09-10

## Physical Button 1 dance control

- Connected expansion-board KEY1/Button 1 (GPIO13) to the same asynchronous
  Dance launcher used by the webpage, including synchronized audio and
  duplicate-run protection. KEY2 continues to move the arm Home.
- KEY1 accepts the board's press and click event variants, debounces duplicate
  events, records successful starts in controller state, and reports launcher
  failures through `last_error`.
- Verification passes all 145 MasterPi tests. The controller was restarted and
  is active; its live state exposes `button_dance_count` with no error. No real
  button press, dance, audio playback, or robot movement was invoked.

2026-09-09

## Original vendor directory rename

- Renamed the bundled Hiwonder vendor tree from `MasterPi/` to
  `MasterPi_original/` so its role is distinct from the custom `masterpi/`
  controller.
- Updated controller SDK/config discovery, service configuration, editable SDK
  installation paths, documentation, and the offline dance IK check to use the
  new directory while retaining compatibility with standard Hiwonder
  `/home/pi/MasterPi` and `/home/pi/TurboPi` installations.
- Verification passes all 142 controller tests and all 18 choreography tests,
  including the real offline vendor-IK check. The restarted live controller
  reports the Hiwonder board, four-wheel mixer, arm IK, sonar, voice module,
  serial device, and buttons with no error; its webpage and state API return
  HTTP 200. The controller, wake-word listener, and Hermes gateway remain
  active, and no motion action was invoked.

## Arm-position directional controls

- Added two four-direction jog pads to the webpage's **Arm position** card. The
  left pad maps up/down to Y ±0.5 cm and left/right to X ±0.5 cm. The right pad
  maps up/down to Z ±0.5 cm, left to fully closed gripper, and right to fully
  open gripper.
- Cartesian jogs use the visible X/Y/Z/pitch fields and movement duration,
  remain within the input bounds, and restore the previous field value when
  the validated `/api/arm` endpoint rejects an unreachable target.
- All eight controls are disabled for the requested move interval to avoid
  racing arm commands. Gripper controls use the existing validated 1500/2000
  closed/open presets and update the direct servo-1 slider after success.
- Verification passes all 141 MasterPi tests, the embedded JavaScript syntax
  check, and `git diff --check`. The controller was restarted and the live page
  exposes all eight controls and both jog handlers; no arm command was sent.

## Separate ground and front MCP grabs

- Renamed the previous unconditional MCP/Realtime grab to `grab_from_ground`.
  It uses the low Cartesian pickup coordinate `(0, 16.5, 0)` cm and returns
  Home while holding the object.
- Lowered the ground-grab contact height from `z=2` to `z=0` cm at the user's
  request. The contact pitch is `-66°`, whose calibrated IK solution remains
  inside the 500–2500 servo range; the `z=8` cm approach point and front-can
  servo profile are unchanged.
- Added `grab_from_front`, which uses the exact operator-recorded servo profile
  behind the webpage's **Grab can** action: servos 3–6 at `1550`, `1620`,
  `2500`, and `1500`, then gripper close and Home.
- Added distinct loopback-only `/api/agent/grab_from_ground` and
  `/api/agent/grab_from_front` routes. The former `recognize_and_grab` tool and
  `/api/agent/grab` route are no longer exposed; neither replacement performs
  camera/color checks.
- Verification passes all 141 MasterPi tests. The controller, Realtime
  wake-word service, and Hermes gateway were restarted without moving the
  robot. Hermes now caches 19 HiBot tools, including both new grabs and no old
  `recognize_and_grab` entry.
- Offline vendor IK resolves the new `(0, 16.5, 0)` contact pose at `-66°` to
  calibrated servo pulses approximately `963`, `1814`, `2470`, and `1564`, all
  within 500–2500. The controller was reloaded without executing the arm.

## Bounded voice listening and capture diagnostics

- Diagnosed the latest apparent listening stall. The question capture ended in
  4.64 seconds, but Realtime returned an empty input transcript and the old
  configuration then waited through three 15-second follow-up windows (46.64
  seconds total). CPU pressure was ruled out: the four-core Pi remained
  62–77% idle with no swap, I/O wait, thermal throttling, or ALSA stream error.
- Raised the normal utterance threshold from RMS 200 to 400, reduced the hard
  utterance limit from 120 to 30 seconds, and reduced idle follow-up handling
  from three 15-second windows to two 8-second windows.
- Empty Realtime transcripts now count toward the same two-cycle silence limit,
  and the webpage reports **No words recognized** or **No speech heard** rather
  than displaying an undifferentiated listening state.
- Added a rotating persistent diagnostic log at
  `~/.local/state/masterpi/voice-diagnostics.log`. It records threshold, RMS
  median/95th-percentile/maximum, speech-start delay, captured duration,
  termination reason, empty transcripts, and Realtime stage timing.
- Verification passes all 141 MasterPi tests. The packaged user unit was
  installed and restarted; systemd confirms the listener and ReSpeaker capture
  are active with RMS 400, 30-second capture, 8-second follow-up, and two-cycle
  limits. The persistent diagnostic log was created successfully.

## Extended dance soundtrack and choreography

- Updated `robot_choregraph/dance.py` to play `dance_move_1.mp4`, whose AAC
  soundtrack and video run for 30.016 seconds.
- Extended the synchronized score through the new ten-second ending and kept
  the existing ReSpeaker availability wait for MCP/voice-triggered dances.
- Updated the web quick action and MCP metadata to use the 34.6-second total
  runtime (30-second score plus Home setup and countdown).
- Removed the superseded `dance_1.py` and 20-second `dance_move.mp4` files;
  `dance.py` and `dance_move_1.mp4` are now the canonical pair.
- Verification passes all 138 MasterPi tests and 17 executable dance tests;
  the optional vendor-SDK path check is skipped. The user-level controller was
  restarted and its web/state endpoints returned HTTP 200. No dance, physical
  motion, or speaker playback was invoked during verification.

2026-09-07

## Web dance quick action

- Added a **Dance** button to the web controller's Quick arm controls.
- Added `POST /api/dance`, which asynchronously launches
  `robot_choregraph/dance.py --execute` against the local controller. The dance
  includes synchronized MP4 audio and overlapping launches are rejected.
- Registered the same action as the `dance` MCP and OpenAI Realtime tool through
  the shared robot-action schema and loopback-only `POST /api/agent/dance`.
- Fixed ReSpeaker contention specific to MCP/voice dance requests. The agent
  route launches `dance.py --wait-for-audio`, which waits for two consecutive
  successful ALSA probes before setting choreography time zero; the webpage
  button keeps its immediate startup behavior.
- The web server owns the dance child process and terminates it during
  controller shutdown; the choreography retains its chassis and audio cleanup.
- Verification passes all 130 MasterPi tests. The restarted MCP server
  advertises 18 tools including `dance`; the controller, wake-word listener,
  and Hermes gateway were restarted successfully without invoking the dance.

2026-09-04

## Done

- Replaced the wake conversation's serialized local-STT → Hermes CLI → Edge
  TTS path with a direct OpenAI Realtime speech-to-speech implementation. The
  local openWakeWord detector and utterance gate remain, but one persistent
  `gpt-realtime-2.1` WebSocket now receives 24 kHz PCM and streams returned PCM
  chunks directly into `aplay` as they arrive.
- Added a dedicated HiBot voice prompt that identifies itself as the physical
  Hiwonder MasterPi robot and describes its mecanum chassis, arm, gripper camera,
  ultrasonic sensor, LEDs, buzzer, ReSpeaker, and supported actions. It limits
  normal answers to one or two sentences and requires a confirmed tool result
  before claiming that a physical action succeeded.
- Added robot actions directly to the Realtime session without reconnecting the
  Hermes agent. A shared registry now supplies OpenAI function schemas and the
  MCP wrappers, while both dispatch through the existing loopback-only,
  validated `/api/agent/*` controller. Realtime handles complete function-call
  rounds (`function_call` → local action → `function_call_output` → final spoken
  response), caps a turn at four tool rounds, and reports each action and its
  elapsed time in the webpage voice feed.
- Exposed `check_front` consistently through MCP, Realtime, and
  `/api/agent/check_front`. The unconditional grab tools have no color
  guardrail. Large annotated camera
  image data is omitted from the model's tool-result context while semantic
  detections remain available, avoiding unnecessary latency and context use.
- Replaced the agent client's universal 15-second HTTP deadline with an
  action-aware timeout: bounded motor/sensor commands retain 15 seconds, while
  semantic `camera_analyze` gets 180 seconds for arm settling and Hermes vision.
  Timeout errors now identify both the action and applied limit.
- Added a single-pixel ReSpeaker voice-direction indicator. When openWakeWord
  detects **Hello HiBot**, or the conversation recorder confirms speech, the
  service reads the XVF3000 `DOAANGLE`, rounds it to the nearest of the twelve
  30-degree ring segments, and uses USB LED custom mode to light only that
  pixel in low-brightness green. The pixel is cleared after capture; the
  existing blue/cyan rotating animation still takes over while HiBot thinks.
  LED/DOA failures remain non-fatal to the voice conversation.
- Kept input/output transcripts and connecting, listening, thinking, streaming,
  completion, and error stages in the webpage conversation feed. Each completed
  turn now displays capture, end-of-speech-to-first-audio, and complete-stream
  timing, and the thinking LEDs switch off on the first returned audio chunk.
- Added PCM resampling, raw streaming playback, Realtime event/error handling,
  multi-turn conversation, prompt, and timing tests. Installed
  `websocket-client` 1.9.2 in the dedicated wake-word environment and added it
  to package dependencies.
- Disabled voice barge-in at the user's request. The packaged wake-word unit
  now starts with `--no-barge-in`; Hermes' own `voice.barge_in` setting is also
  disabled so both the custom listener and Hermes voice mode agree.
- Scoped robot voice conversations to the installed fast GPT-5.6 tier,
  `gpt-5.6-luna`, through `openai-codex` with low reasoning effort. Other
  Hermes surfaces retain their configured default model and reasoning level.
- Diagnosed the live wake-word failure after **“What can you do?”**: STT heard
  the question correctly, but generation-phase barge-in treated sustained RMS
  `202` background as speech at the old RMS `200` threshold and terminated the
  Hermes subprocess before it could answer.
- Split normal utterance detection from interruption detection. Normal
  listening remains at RMS `200`, while generation and playback barge-in now
  use `max(500, quiet-floor * 3)`. The observed question was near RMS `3049`,
  so genuine nearby speech remains well above the new floor.
- Added a file-backed live voice-conversation feed shared by the wake service
  and web controller. It records the wake, **I'm here**, user transcripts,
  intermediate listening/transcribing/thinking/speaking stages, the responsible
  tool or subsystem when available, HiBot replies, interruptions, stop/silence
  exits, and errors.
- Added `GET /api/voice/conversation` and connected the webpage chat log to it.
  The page now shows spoken conversations alongside typed chat, polls live while
  the voice backend works, renders an animated thinking bubble, and displays
  tool-stage details reported by the active backend.
- The typed web chat now uses the same animated thinking bubble while awaiting
  its response.

## Verification

- Full test suite: 124 tests passing.
- Python bytecode compilation and `git diff --check`: passing.
- Installed and restarted the direct-Realtime wake listener; systemd reports it
  active with zero restarts and an explicit `--conversation-backend realtime`
  command. The MasterPi controller and webpage voice feed remain available.
- A real Realtime audio-in/audio-out smoke test sent the existing 1.776-second
  **“I'm here”** WAV, received the correct transcript, and audibly streamed a
  five-second HiBot reply through the ReSpeaker output.
- A live direct-Realtime tool test explicitly requested the read-only
  `get_state` action. The model selected only `get_state`, the loopback
  controller returned successfully in 0.008 seconds, and the model correctly
  answered that the chassis was stopped. No movement tool was called.
- A live `camera_analyze` retry completed in 23.471 seconds, beyond the former
  15-second deadline but comfortably inside the new 180-second limit. It moved
  to Check front, returned five semantic object detections, and included the
  annotated image.
- A direct Hermes smoke test successfully answered **“What can you do?”** using
  the same chat integration. Live wake-to-answer and browser rendering remain
  to be physically checked with the microphone and browser.
- The scoped `gpt-5.6-luna`/low route was verified in Hermes' session database:
  one API call, provider `openai-codex`, model `gpt-5.6-luna`, and zero reported
  reasoning tokens. Its measured end-to-end Hermes call was still 35.92 seconds
  versus roughly 34 seconds for the earlier terra/medium probe, showing that
  current provider response time—not local CLI startup or reasoning depth—is
  the main remaining latency. Hermes CLI startup measured 1.13 seconds.
- ReSpeaker thinking-ring writes remain non-fatal but still require the udev
  permission update documented below.
- The direction-pixel hardware smoke test correctly attempted custom LED mode
  but the current `/etc/udev/rules.d/99-respeaker-led-off.rules` was confirmed
  to be the older LED-off-only copy: active USB node `2886:0018` remains
  `root:root` and PyUSB returns `Access denied`. Installing the repository rule
  needs the Pi's interactive sudo password; the attempted passwordless install
  was rejected. After installation/reload, reconnect or trigger the device and
  physically verify the native raw-angle orientation.

## Measured voice latency

The direct Realtime code now records these values for every physical turn:

| Realtime metric | Measurement point |
| --- | --- |
| Capture | Start listening through the local three-second ending silence |
| First audio | Committed input through the first `response.output_audio.delta` |
| Response complete | Committed input through `response.done` |
| Playback complete | Committed input through the final streamed `aplay` drain |

A deterministic local stress check processed a synthetic seven-second input
plus fifty PCM output events (ten seconds of reply audio) in **37.98 ms median**
and **43.60 ms p95** over 100 runs in the deployed Python 3.11 environment. This
measures Pi-side resampling, Base64, JSON, and event dispatch overhead only.

The first real `gpt-realtime-2.1` audio test measured:

| Stage | Elapsed time |
| --- | ---: |
| Secure WebSocket and session handshake | 0.407 s |
| Upload/resample dispatch | 0.069 s |
| Committed speech to first audible audio | 0.745 s |
| Committed speech to `response.done` | 4.016 s |
| Committed speech through playback drain | 5.941 s |
| Generated reply audio duration | 5.000 s |

The input was the existing 1.776-second **“I'm here”** WAV. The complete
wall-clock turn was 5.973 seconds, and the returned input transcript was
correct. A separate initial authentication/schema probe took 1.639 seconds;
subsequent connections were faster. The persistent connection is reused for
all follow-up turns after each wake.

The first live Realtime tool round used text input and suppressed speaker
playback so it could not retrigger the microphone:

| Tool-enabled Realtime stage | Elapsed time |
| --- | ---: |
| Committed request to first returned audio | 0.714 s |
| Read-only `get_state` controller call | 0.008 s |
| Request through final `response.done` | 3.224 s |
| Generated reply audio duration | 10.550 s |

For comparison, the previous serialized Hermes pipeline measured:

The latest physical **“What can you do?”** turn produced these timestamps:

| Stage | Elapsed time |
| --- | ---: |
| Speech capture, including the configured three-second end silence | 7.13 s |
| Local STT (`faster-whisper base`) | 19.33 s |
| Hermes agent (`openai-codex`, `gpt-5.6-luna`, low reasoning) | 39.42 s |
| Edge TTS generation plus FFmpeg conversion | approximately 4–6 s |
| Playback of the 120-word response | approximately 66 s |
| Combined TTS generation, conversion, and playback | 72.12 s |

The robot therefore began speaking roughly 70 seconds after listening began,
and the full turn took about 138 seconds. An isolated local STT check took
24.57 seconds for the existing 1.776-second **“I'm here”** WAV. A neutral
19-word Edge TTS check took 3.89 seconds to generate, 0.55 seconds to convert,
and produced 10.42 seconds of audio. This confirms that all three serial
boundaries matter: local Whisper model startup/inference, the non-streaming
Hermes provider turn, and waiting for a long reply to be fully synthesized and
played.

## Faster voice architecture reference

Reviewed `voice-agent/` and its `examples/demo` implementation. Its response
path is materially faster by design:

- the browser connects once to a persistent LiveKit room over WebRTC instead
  of creating a WAV and launching a new STT/agent/TTS subprocess per turn;
- `server/worker/src/main.ts` creates one persistent
  `voice.AgentSession` backed by OpenAI `RealtimeModel`, so input audio,
  transcription, response generation, and output audio share one streaming
  session;
- response audio streams back through the LiveKit track and can start playing
  before the complete response text and audio file exist, eliminating the
  Edge-TTS-to-MP3, FFmpeg, and `aplay` serialization boundary;
- final user and agent transcripts are sent over the reliable data channel and
  rendered immediately by `examples/demo/src/App.tsx` through
  `useTranscript`;
- the default agent prompt explicitly limits normal replies to one or two
  sentences, preventing a 120-word answer from adding roughly a minute of
  playback.

The demo's `ai_agent` bridge is not the fast path for this robot if it still
forwards every turn to the current Hermes CLI: it has a 30-second application
query timeout, while the measured Hermes turn alone takes 35–39 seconds. The
direct Realtime `voice` mode is the useful reference. Robot actions should be
exposed as narrow worker tools or mapped from the existing reliable
`send_client_action` channel to MasterPi's bounded `/api/agent/*` endpoints.
That mapping is now implemented directly in the persistent Realtime session;
Hermes remains outside the fast voice path.

### Direct Realtime remaining TODO

1. Physically say **“Hello HiBot”** followed by **“What can you do?”** to verify
   the complete microphone-triggered path.
2. Confirm that the webpage shows the live transcript and its capture,
   first-audio, response-complete, and playback-complete timing row.

2026-09-03

## Done

- Aligned the headless **Hello HiBot** conversation service with Hermes voice
  mode while keeping the existing wake listener as the sole microphone owner:
  - every wake starts a fresh uniquely named Hermes session, while follow-up
    turns within that wake retain context;
  - speech confirmation now requires four 80 ms frames (0.32 seconds), the base
    RMS threshold is 200, end-of-speech silence is three seconds, and recording
    can run for up to 120 seconds;
  - three consecutive 15-second silent cycles or 30 user turns end a session;
  - exact stop phrases include `stop`, `goodbye`, `never mind`, `cancel`,
    `stop listening`, `that's all`, and `end conversation`;
  - STT now uses Hermes' voice-mode transcription wrapper, including its
    Whisper hallucination filter; filtered/empty transcripts count as silent
    cycles instead of aborting the conversation;
  - full-turn barge-in samples the quiet-room RMS floor and uses `max(200,
    floor * 3)`; confirmed speech terminates in-flight Hermes generation or
    cuts `aplay`, captures the interjection, and tells the next Hermes turn that
    its previous response was interrupted;
  - reply playback uses a 0.5-second onset grace period, with barge-in enabled
    because ReSpeaker one-channel echo cancellation was physically confirmed.
- Updated the packaged user service with explicit voice settings and added
  cancellation, session freshness, silence-cycle, and playback interruption
  tests.
- Added a ReSpeaker thinking indicator using the firmware's built-in spin
  command: a low-brightness blue/cyan circle runs only during Hermes response
  generation and returns to mono black in a `finally` path before playback,
  interruption handling, or idle listening. LED failures remain non-fatal to
  voice conversations. PyUSB 1.3.1 is installed in the wake-word environment.

## Verification

- Full test suite: 103 tests passing.
- Python bytecode compilation and `git diff --check`: passing.
- The updated user service is installed, enabled, active with zero restarts,
  and owns the ReSpeaker through one `arecord` process. Live wake, multi-turn
  conversation, and barge-in playback remain to be physically verified.
- Thinking-ring live validation is blocked until the installed root-only udev
  rule is replaced with `masterpi/deploy/99-respeaker-led-off.rules` and the
  array is reconnected; the repository rule grants the user service `plugdev`
  write access while retaining the boot/reconnect all-off action.

2026-09-02

## Done

- Added local wake-word detection with openWakeWord instead of Picovoice:
  - captures the ReSpeaker's processed one-channel, 16-bit, 16 kHz ALSA stream;
  - runs a supplied ONNX wake-word model in local inference with no account,
    key, or cloud transcription;
  - uses openWakeWord's preferred 80 ms frames and configurable score
    threshold;
  - pauses capture while playing the cached **I'm here** response, resets model
    history, and applies a cooldown to prevent feedback activation;
  - provides a `masterpi_control.wake_word --download-features` setup command
    for the two official ONNX feature models and an installable systemd service;
  - installs openWakeWord in a separate Python 3.11 environment because its
    Raspberry Pi TFLite dependency has no Python 3.13 wheel;
  - downloaded the two required official ONNX feature models and generated the
    cached 16-bit mono, 16 kHz `assets/im-here.wav` response with Hermes TTS;
  - added a bounded multi-turn voice session after wake: energy/silence-based
    utterance capture, Hermes STT and persistent agent chat, spoken Hermes TTS
    replies, follow-up turns without repeating the wake phrase, exit phrases,
    silence timeout, and a six-turn maximum.

## Wake-word deployment

- The trained and foreground-tested model is installed at
  `masterpi/models/openwakeword/hello_hibot.onnx`; threshold `0.5` was
  physically validated by the user.
- The service is packaged as a per-user systemd unit because system-wide
  installation requires the Pi's interactive sudo password. It was installed
  at `~/.config/systemd/user/masterpi-wake-word.service`, enabled, and started;
  systemd reports it active with zero restarts.

2026-09-01

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

- Full test suite: 96 tests passing.
- openWakeWord ONNX inference smoke test passed on this Raspberry Pi using an
  official `hey jarvis` model and the downloaded feature models.
- The user physically validated live ReSpeaker wake detection at threshold
  `0.5`; the installed conversational service remains to be voice-tested.
- Python bytecode compilation and `git diff --check`: passing.
- Physical “come here” validation is intentionally pending the USB permission
  update and DOA mounting calibration above.
