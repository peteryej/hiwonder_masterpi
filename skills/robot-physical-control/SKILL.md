---
name: robot-physical-control
description: Use when controlling a robot. Act safely and verify.
---

# Physical Robot Control

## Explicit commands execute as given

**When the operator names the action, just do it.** "Move left 10 cm", "rotate
right 90 degrees", "back up 20 cm", "open the gripper" - run it immediately
with the values given. Do not take a camera frame first, do not weigh what the
last frame showed, and do not ask the operator to clear the area. The operator
is looking at the robot and its surroundings; you are looking at one stale
low-angle frame, and refusing on that basis withholds a movement the operator
can see is safe.

- Never decline a bounded, explicitly specified movement over what a camera
  image seems to show. Every move here is finite, small, and watchdog-stopped.
- Do not substitute a smaller distance, a different direction, or an
  obstacle-avoidance action for what was asked.
- Mention something you noticed afterwards if it is genuinely useful ("that
  strafe brought the chair leg within a few centimetres"), as an observation
  after the fact, never as a precondition or a question first.
- This applies to the action the operator named. It does not license unrequested
  extra motion.

Refuse or pause only for a real blocker: the controller rejects the command, an
actuator fails mid-motion, the request is unbounded or has no stopping
condition, or the operator asked you to stop.

## Safety-first execution

1. Identify the requested actuator and whether the action is chassis motion, arm motion, gripper operation, or a harmless signal.
2. For **open-ended** motion you chose yourself - approaching a sound, exploring, closing on a target across the room - read fresh sensor/state first and treat a detected obstacle as real. An explicitly commanded bounded move needs no such gate; see above.
3. Prefer validated bounded control methods with built-in stopping or watchdog refresh. Never emulate an unbounded hold-to-drive command.
4. Use explicitly provided direction, distance, and duration exactly. If speed is omitted, use the platform's conservative documented default and state it after completion.
5. After a state-changing physical action, read back the controller state and confirm the chassis is stopped before reporting completion.

## Distance and navigation limits

- Do not present a time-based drive as a distance-accurate move unless verified wheel odometry or another closed-loop distance measurement is available.
- A single forward-facing range sensor cannot validate a rearward path. That is a reason to keep a commanded backward move finite and bounded, and to say the path was not sensor-validated - not a reason to withhold it or to ask the operator to clear the rear first.
- Use an obstacle-avoidance action only when its behavior matches the request. If it turns on obstacle detection, do not substitute it for a straight-line move without saying so.

## Camera-guided manipulation

**Check for a companion pickup skill first.** Robot-specific pickup procedures
live in their own skills, and this skill's generic cautions must not be used to
decline what one of them covers. On HiBot, any request to grab, pick up, fetch,
or get a named object from the floor or ground - "grab the toy", "pick up the
can" - means loading `hibot-ground-grab` (`skill_view`) and following it. That
skill looks at the ground, drives the chassis in small bounded steps to center
the object in the frame, runs the calibrated pickup, and confirms the hold from
the camera. Seeing the object and reporting that you cannot pick it up, without
having loaded that skill, is a failure to complete the request.

1. Capture a fresh frame immediately before assessing a grasp.
2. Confirm the target is centered, reachable, and has a clear gripper approach. Do not infer depth or a manipulable pose from a single 2D image.
3. Match the perception guard to the object class. Prefer semantic image analysis plus a normalized bounding-box center check for ordinary objects; use HSV/color checks only when color itself is the intended fiducial.
4. Use only a named, operator-calibrated pickup profile for the target geometry. Record direct-servo positions from live controller state rather than reverse-engineering or guessing IK coordinates.
5. Keep a dry-run perception path separate from execution, and require an explicit execution action before moving the arm.
6. Do not substitute arbitrary joint motion for a missing calibrated pickup. An object that is merely off the pickup point is not a missing pickup: the companion skill moves the robot until the object is at that point, so load and follow it. Ask the operator to stage the item only when no companion skill covers the target, or after that skill's alignment has failed - and say which.
7. Keep hands clear during arm movement and return the arm to its documented safe/Home pose.
8. Treat a controller response such as `grabbed: true` as routine completion, not proof of physical capture. Verify the post-action camera view when possible and report failure if the object remains on the surface.

## Explicit quick arm actions

- Treat **quick action** as a discoverable operator control, not merely an API route or a one-off invocation. When asked to add one, wire the named backend action into the robot's actual control surface, label it distinctly, and document it.
- A direct quick action may bypass camera, color, and centering checks only when the user explicitly requests no check and the action uses a named, operator-calibrated fixed pickup profile. Do not silently substitute the generic pickup profile.
- Preserve explicit routing in the control payload (for example, `force: true` plus the named pickup profile) so provider defaults cannot select the wrong geometry.
- Keep the action serialized, stop the chassis in the controller, return Home in `finally`, and read back controller state after execution. A post-action state read is safety verification, not a forbidden perception gate.
- Do not conflate **add the quick action** with **run the arm action**. If both are requested, implement and verify the control first, then execute only when the user's request explicitly authorizes physical motion.
- Use a vertical integration test: first assert the rendered control and exact request payload fail, then add the UI handler; retain a backend test for profile routing. After the full hardware-free suite passes, restart the service and fetch the live page to verify both the button and payload are actually deployed.

## Reporting

- For completed motion: state direction, duration, speed if used, and that the robot is stopped.
- For a declined action: give the specific blocker - a controller rejection, an actuator failure, a missing bound - and the minimum condition needed to proceed. Keep it concise. "The camera shows something nearby" is not a blocker for an explicitly commanded bounded move.
- Never decline a floor or ground pickup on the grounds that the object is unstaged until you have loaded the companion pickup skill and its alignment has actually failed. Asking the operator to stage an object the robot can drive up to and center is a refusal of the task, not a safety measure.

## Platform notes

See [MasterPi camera and manipulation notes](references/masterpi-camera-and-manipulation.md) for the validated local camera capture method and current controller limitations.
