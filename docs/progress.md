# MasterPi progress

2026-09-04

## Done

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
  tool or subsystem when available, Hermes replies, interruptions, stop/silence
  exits, and errors.
- Added `GET /api/voice/conversation` and connected the webpage chat log to it.
  The page now shows spoken conversations alongside typed chat, polls live while
  Hermes works, renders an animated thinking bubble, and displays tool-stage
  details without inventing internal Hermes tool calls that its quiet CLI does
  not expose.
- The typed web chat now uses the same animated thinking bubble while awaiting
  its response.

## Verification

- Full test suite: 107 tests passing.
- Python bytecode compilation and `git diff --check`: passing.
- Installed the updated wake-word unit and restarted both user services. The
  wake listener and MasterPi controller are active with zero restarts; the live
  status API reports idle/ready and the served page contains the voice poller
  and thinking animation.
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

## Measured voice latency

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

### Realtime migration TODO

1. First validate the unmodified web demo with a LiveKit server, token API,
   worker, and an `OPENAI_API_KEY`; `voice-agent/.env`, `node_modules`, and the
   runtime services are not present yet.
2. Add a MasterPi-specific voice-agent definition with a one-or-two-sentence
   response rule and explicit bounded robot action schemas.
3. Add a token-proxy route and SDK client to the MasterPi webpage for an early
   end-to-end latency test. This uses the browser device's microphone and
   speaker, not necessarily the robot's ReSpeaker.
4. For the actual headless robot, implement a Pi LiveKit participant that
   publishes the processed ReSpeaker ALSA capture and sends subscribed audio to
   the ReSpeaker ALSA playback device. Keep the Realtime/LiveKit session warm,
   but publish microphone audio only after local openWakeWord detection.
5. Relay Realtime transcript and session-state events into the existing
   `GET /api/voice/conversation` feed so the current webpage continues to show
   listening, thinking, tool, and reply activity.
6. Benchmark wake-to-first-transcript, end-of-speech-to-first-audio, and full
   response duration before replacing the current Hermes pipeline. Retain the
   current service as a fallback until ReSpeaker capture/playback and robot
   actions are physically validated.

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
