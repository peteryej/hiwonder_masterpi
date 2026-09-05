# MasterPi progress

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
  normal answers to one or two sentences and explicitly says this no-Hermes
  voice path has no control tools, preventing false claims that an action ran.
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

- Full test suite: 113 tests passing.
- Python bytecode compilation and `git diff --check`: passing.
- Installed and restarted the direct-Realtime wake listener; systemd reports it
  active with zero restarts and an explicit `--conversation-backend realtime`
  command. The MasterPi controller and webpage voice feed remain available.
- A real Realtime audio-in/audio-out smoke test sent the existing 1.776-second
  **“I'm here”** WAV, received the correct transcript, and audibly streamed a
  five-second HiBot reply through the ReSpeaker output.
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
