# CLAUDE.md — Gesture-Controlled Drum Circle
## Project context for Claude Code

---

## 1. Project Overview

This project is a live performance system where up to four people stand around Ultraleap
Motion Controller 2 hand-tracking modules and use bare-hand gestures to trigger and shape
sounds on an Elektron Syntakt drum machine via MIDI over USB. The goal is a collaborative,
hands-free live drum/synth performance with no physical controllers.

Scripts in this codebase:

| File | Purpose |
|------|---------|
| `drum_circle.py` | 3-module drum circle — each module gives one player 4 quadrant-mapped drum sounds |
| `perc_and_chords.py` | 2-module hybrid — module 1 is percussion, module 2 is a height-mapped chord sustainer with scale selection |
| `four_module.py` | 4-module performance system — modules 1/2/4 are 2-zone drums, module 3 is split left-drum/right-synth |
| `diagnose.py` | Multi-device diagnostic — measures per-device tracking Hz, reconnection events, serial numbers |

---

## 2. Hardware

### Computer
- Windows 11
- Python 3.12.2
- VS Code with PowerShell terminal
- Virtual environment at `.venv` (activate with `.venv\Scripts\Activate.ps1`)

### Computer USB Ports (Samsung Galaxy Book 2 Pro 360)
- **Thunderbolt 4 port** — 40 Gbps, full bandwidth for Leap modules. **Use this for all modules.**
- **Standard USB-C ports (×2)** — one appears to negotiate USB 2.0 only (confirmed: 3–8 Hz tracking
  on directly-connected module vs 89–90 Hz on TB4 hub). Do NOT use standard USB-C ports for Leap modules.
- **Critical rule**: All Leap modules should connect through the Thunderbolt 4 port via a USB-C hub.
  Standard USB-C ports on this laptop are inadequate for Leap Controller 2 operation.

### USB Hub
- **Acer 5-in-1 USB-C Hub ODK4LO** — connected to Thunderbolt 4 port
- **BUS-POWERED** — draws current from the laptop port. With 4 Leap modules (4 × 500mA = 2A),
  only 1 module survives the power budget. The other 3 connect and immediately drop with 0 frames.
  With 2 modules, one is stable and one drops intermittently. With 1 module, stable at 89–90 Hz.
- The Syntakt does NOT go through this hub — it has its own direct USB connection

### Required for 4 modules: a powered USB hub
- Must have an **external AC power adapter** (not bus-powered)
- Must have USB 3.0 SuperSpeed ports (5 Gbps per port minimum)
- Must connect to the **Thunderbolt 4 port** via USB-C
- Power budget: hub AC adapter must supply ≥ 2.5A at 5V (4 × 500mA + hub overhead)
- Confirmed required because: bus-powered hubs (including the Acer ODK4LO) collapse under 4 modules.
  A powered hub fixes current starvation; USB 3.0 connection to TB4 fixes bandwidth.
- Note: "powered hub" fixes current starvation. It does NOT fix a USB 2.0 port's bandwidth limit —
  those are separate problems. If connecting the powered hub to a standard USB-C port that negotiates
  USB 2.0, the bandwidth throttling (3–8 Hz) persists regardless of power supply.

### Ultraleap Motion Controller 2 (×3 or ×4)
- USB-C connector, **requires USB 3.0 for gesture detection** — USB 2.0 is technically functional
  but delivers 3–8 Hz instead of 120 Hz, making downward-strike detection completely unreliable
  (a 200ms strike produces <1 frame at 3 Hz)
- Cables: **Anker USB-C to USB-C Model A8758** — confirmed data-transfer capable, not charge-only
  (cable type matters critically — charge-only cables will connect but produce no tracking data)
- Specs: 160° × 160° FOV, 10–110cm tracking range, 120Hz, dual 640×240 infrared cameras, 500mA @ 5VDC
- Must be positioned face-up on a flat surface in Desktop tracking mode for this project
- The Ultraleap Gemini tracking service must be **running** before any Python script that imports `leap` is executed
  — verify via the Ultraleap Control Panel tray icon
- **Expected Hz with 4 modules on TB4**: ~89–90 Hz per device (Gemini CPU budget is shared; 120 Hz
  per device is achievable only with 1–2 modules; 89 Hz is the practical ceiling with 4 active)

### Elektron Syntakt
- 12-track drum computer and synthesizer (8 digital + 4 analog voices)
- Connected to the computer via **USB-A to USB-B** (or USB-C) — standard MIDI over USB, class-compliant, no drivers needed
- Appears in Windows as a MIDI device — exact name varies, find it with:
  ```python
  import mido
  print(mido.get_output_names())
  # Example: 'Syntakt  Port 1 0'  (note the double space — copy exactly)
  ```
- The port name string must be copied character-for-character into `MIDI_PORT_NAME` in each script
- The Syntakt also exposes a MIDI input port (same name) which can be used to read MIDI clock

---

## 3. Software Stack

### Python packages (install in .venv)
```powershell
pip install mido python-rtmidi numpy
```

| Package | Role |
|---------|------|
| `mido` | MIDI message construction and port I/O |
| `python-rtmidi` | Backend that mido uses for actual port access on Windows |
| `numpy` | Available but not currently used in main scripts |
| `leap` | Ultraleap Python bindings (see installation below) |

### Ultraleap Python Bindings
The `leap` module is **not on PyPI in a form compatible with Python 3.12**. Installation:

```powershell
# Prerequisites: Gemini Tracking Software ≥5.17 installed, MSVC Build Tools installed
pip install build cffi
git clone https://github.com/ultraleap/leapc-python-bindings
cd leapc-python-bindings
python -m build leapc-cffi
pip install leapc-cffi/dist/leapc_cffi-0.0.1.tar.gz
pip install -e leapc-python-api
```

**If you get `ModuleNotFoundError: No module named 'leapc_cffi._leapc_cffi'`:**
Manually copy the entire `leapc_cffi/` folder and `LeapC.dll` from
`C:\Program Files\Ultraleap\LeapSDK\leapc_cffi\` into `.venv\Lib\site-packages\`.
This happens because the precompiled binary bundled with Gemini targets Python 3.8 only.
Building from source against Python 3.12 fixes it permanently.

---

## 4. Overall Data Flow

```
[Leap Module 1] ──USB-C──┐
[Leap Module 2] ──USB-C──┼──► [Acer USB-C Hub ODK4LO] ──USB-C──► [Windows 11 PC]
[Leap Module 3] ──USB-C──┘                                              │
[Leap Module 4] ──USB-C──────────────────────────────────────────────────┘
                                                                         │
                                                              Ultraleap Gemini service
                                                              (runs as Windows service,
                                                               must be active before
                                                               Python starts)
                                                                         │
                                                              leap.Connection (Python)
                                                              multi_device_aware=True
                                                                         │
                                                              Listener callbacks
                                                              on_tracking_event()
                                                                         │
                                                         ┌───────────────┴───────────────┐
                                                         │                               │
                                                   gesture logic                   chord logic
                                                   (strike detect,               (height band,
                                                    quadrant map)                 sustain state)
                                                         │                               │
                                                         └───────────┬───────────────────┘
                                                                     │
                                                              queue.Queue (thread-safe)
                                                              (mido.Message, auto_note_off)
                                                                     │
                                                              MIDI sender thread
                                                              (50ms sleep for drums)
                                                                     │
                                                              mido.open_output()
                                                                     │
                                              USB-A/C ──────────────────────────────────────────►
                                                              [Elektron Syntakt]
                                                              MIDI over USB (class-compliant)
                                                              Tracks 1–12 / Ch 1–12
```

---

## 5. Syntakt MIDI Configuration

Before running any script, configure the Syntakt physically:

1. `SETTINGS (FUNC+TEMPO) > MIDI > MIDI CHANNELS`: Set Track N = Channel N for all 12 tracks
2. `SETTINGS > MIDI > MIDI IN PORT`: Set to `USB` (not `MIDI` or `DISABLED`)
3. `SETTINGS > MIDI > RECEIVE NOTES`: `ON` for all tracks
4. `SETTINGS > MIDI > TRANSPORT`: Disable if you don't want external MIDI start/stop affecting the sequencer
5. Optionally: `SETTINGS > MIDI > CLOCK SEND: ON` if you want the script to read BPM from the Syntakt

### Triggering model
The Syntakt has two triggering approaches. This project uses **Option A — one channel per track**:
- Track 1 receives on MIDI channel 1, Track 2 on channel 2, etc.
- Any `note_on` on a channel fires that track's loaded sound regardless of note number
- mido uses **0-indexed channels**: `channel=0` means MIDI channel 1, `channel=11` means MIDI channel 12

### Track layout used in these scripts

| Tracks | MIDI Ch (Syntakt) | mido channel | Use |
|--------|------------------|--------------|-----|
| 1–4 | 1–4 | 0–3 | Drums / Percussion |
| 5–8 | 5–8 | 4–7 | Tonal / Chord voices |
| 9–12 | 9–12 | 8–11 | Additional (3-player config) |

### External MIDI priority (critical behavior)
The Syntakt gives **external MIDI note_on priority over its internal sequencer**. This means:
- If your code sends a `note_on` that is never followed by a `note_off`, the Syntakt treats it as
  a held note, which **permanently mutes the sequencer for that channel** until the note is released
- This manifests as: sequencer loop silenced, track button triggers on both press AND release
- The only recovery without code fix is reloading the song, or sending MIDI CC 123 (all notes off)

---

## 6. The Retriggering / Stuck Note Bug — How It Was Found and Fixed

### Symptom
After playing for a few minutes, one or more channels would behave strangely:
- The Syntakt's internal sequencer loop for that channel would go permanently silent
- Manually pressing the track button on the Syntakt would fire the sound on **both press and release**
- Reloading the song fixed it
- The bug only affected some channels, not all, and appeared randomly during play

### Root cause
The original code sent messages in this order per trigger:
```python
midi_queue.put_nowait(mido.Message('note_off', ...))  # clear previous
midi_queue.put_nowait(mido.Message('note_on', ...))   # fire sound
# ← nothing after this
```
The `note_off` before the `note_on` was intended to close the *previous* trigger's note.
This works most of the time but fails in two scenarios:

1. **Queue drop**: The queue had `maxsize=100`. During heavy use with multiple players, `put_nowait`
   raises `queue.Full`. The code caught this silently. If a `note_off` was dropped but its `note_on`
   wasn't (or vice versa), the channel was left with an unresolved open note on the Syntakt.

2. **Last trigger of a session / zone exit**: The final `note_on` sent on a channel had nothing
   that would ever close it. If the player stopped using that quadrant, the `note_off` only arrived
   if someone happened to trigger that exact channel again later.

In both cases, the Syntakt received an open `note_on` with no `note_off`. Since external MIDI has
priority, it muted the sequencer track as if a live performer was holding the note down.

The "press AND release triggers" symptom happens because when you press the track button manually
while the Syntakt holds an unresolved `note_on`, button release internally generates its own note
state resolution — triggering the voice a second time as the stuck state clears.

### The fix
The MIDI sender thread now owns the complete lifecycle of every drum hit:

```python
def _midi_sender(self):
    while True:
        item = self.midi_queue.get(timeout=1.0)
        if item is None:
            break
        msg, auto_note_off = item
        self.port.send(msg)
        if auto_note_off and msg.type == 'note_on' and msg.velocity > 0:
            time.sleep(0.05)   # 50ms — enough for AHD envelope to fire
            self.port.send(mido.Message('note_off',
                note=msg.note, velocity=0, channel=msg.channel))
```

Queue items are now tuples: `(mido.Message, auto_note_off: bool)`.
- `auto_note_off=True` → drum/impulse hits. Sender closes the note 50ms later, guaranteed.
- `auto_note_off=False` → tonal/sustained notes. Sender passes through as-is. Release is sent explicitly.

Every drum `note_on` is now self-contained. There is no path by which the Syntakt can be left
holding an open note from a drum channel regardless of what happens next.

### Panic on shutdown
A proper shutdown sends all-notes-off (CC 123) on all 12 channels to clear any in-flight state:
```python
def shutdown(self):
    for channel in range(12):
        self.port.send(mido.Message('control_change',
            channel=channel, control=123, value=0))
    self.midi_queue.put(None)
    self.midi_thread.join(timeout=2.0)
```

---

## 7. Multi-Device Ultraleap API — The Only Pattern That Works

This was the hardest part of the project. The correct pattern was discovered through systematic
debugging and is documented here in full. **Do not deviate from it.**

### Why the default `leap.Connection()` fails with multiple devices
By default, `leap.Connection()` operates in single-device mode — only one device streams tracking
events even if three are plugged in. The Ultraleap Control Panel's device selector reflects this.

### The four mandatory steps

#### Step 1 — `multi_device_aware=True`
```python
connection = leap.Connection(multi_device_aware=True)
```
This sets the `eLeapConnectionConfig_MultiDeviceAware` flag in the underlying LeapC C library.
Without it, `event.metadata.device_id` is meaningless and only one device streams regardless of
how many are connected.

#### Step 2 — `contextlib.ExitStack` for device contexts
`device.open()` is a **context manager**, not a regular method call. Calling it as a plain function
(`device.open()`) creates the context object but never executes the setup code inside it. The
device's internal C pointer (`device._device`) stays `None`, causing:
```
TypeError: initializer for ctype 'struct _LEAP_DEVICE *' must be a cdata pointer, not NoneType
```

You must enter the context manager AND keep it open for the entire program lifetime.
`contextlib.ExitStack` is the correct tool:

```python
device_stack = contextlib.ExitStack()

# Inside on_device_event:
device_stack.enter_context(device.open())  # enters AND holds open for program lifetime
```

The ExitStack must be kept alive via `with device_stack:` at the top level. When it closes,
all devices close cleanly.

#### Step 3 — `subscribe_events()` per device
After `device.open()` is entered and the C pointer is populated:
```python
connection.subscribe_events(device)
```
In multi-device-aware mode, **no tracking events flow from a device until you explicitly subscribe**.
Both `open()` and `subscribe_events()` are required. This must happen inside `on_device_event()`,
which fires once per detected device.

#### Step 4 — Read `event.metadata.device_id`
The `TrackingEvent` object does NOT have a `.device_id` attribute. The correct path is:
```python
device_id = event.metadata.device_id   # int: 1, 2, 3, ...
```
Device IDs are assigned by the Leap service at connection time (typically 1, 2, 3 in plug-in order).
They are stable for the duration of a session but may change between runs.

### Complete minimal working skeleton
```python
import contextlib, time, leap

connection   = leap.Connection(multi_device_aware=True)
device_stack = contextlib.ExitStack()

class MyListener(leap.Listener):
    def on_device_event(self, event):
        device = event.device
        device_stack.enter_context(device.open())
        connection.subscribe_events(device)

    def on_tracking_event(self, event):
        device_id = event.metadata.device_id
        for hand in event.hands:
            print(f"Device {device_id}: {hand.type} at y={hand.palm.position.y:.0f}mm")

connection.add_listener(MyListener())

with device_stack:
    with connection.open():
        time.sleep(10)
```

### Failure mode table

| Error / Symptom | Cause | Fix |
|-----------------|-------|-----|
| `ctype 'struct _LEAP_DEVICE *' ... NoneType` | `device.open()` called as plain function | Use `device_stack.enter_context(device.open())` |
| No tracking events from second/third device | `multi_device_aware=True` missing | Add the flag to `leap.Connection()` |
| `subscribe_events()` not called | Only one device streams | Call it inside `on_device_event` for each device |
| All events have same `device_id` | `multi_device_aware=True` missing | Add the flag |
| `AttributeError: event has no device_id` | Wrong attribute path | Use `event.metadata.device_id` |
| `TypeError: __init__() unexpected keyword 'multi_device_aware'` | Old leapc-python-api | Rebuild bindings from source |
| `ModuleNotFoundError: No module named 'leapc_cffi._leapc_cffi'` | Wrong Python version binary | Copy from SDK or build from source |
| Missing method on listener (e.g. `_get_player`) | Method defined outside class body (indentation) | Check that all methods are inside the class |

### Hand ID collision across devices
`hand.id` values are assigned per-device and can collide — both Device 1 and Device 2 can have
a hand with `id=1` simultaneously. Always key hand state by `(device_id, hand.id)` tuple:
```python
self.hand_states: dict[tuple[int, int], HandState] = {}
```

### Stale hand cleanup
`hand.id` is ephemeral — it resets when a hand leaves and re-enters the sensor field. Clean up
per-device by filtering on device_id:
```python
current_keys = {(device_id, h.id) for h in event.hands}
stale = {k for k in self.hand_states if k[0] == device_id} - current_keys
for k in stale:
    # release any sustained tonal notes before deleting
    del self.hand_states[k]
```

---

## 8. Gesture Detection

### Coordinate system
The Ultraleap coordinate system for Desktop mode:
- `x`: left–right (mm), positive = right
- `y`: height (mm), positive = up, zero = surface level
- `z`: depth (mm), positive = toward the user

All positions are in millimeters relative to the center of the module.

### Play zone
```python
X_RANGE = [-200, 200]   # 400mm wide band
Z_RANGE = [-200, 200]   # 400mm deep band
Y_MIN   = 100           # minimum height — hands on the desk don't trigger
```

### Quadrant mapping
The XZ plane is divided into 4 quadrants by the sign of x and z:
```
 Z+
 |  Q2 (x<0, z>0) | Q1 (x>0, z>0)
─┼────────────────|────────────────  X
 |  Q3 (x<0, z<0) | Q4 (x>0, z<0)
 Z-
```
Each quadrant maps to one drum track/channel.

### Strike detection (drum mode)
A downward strike is detected by tracking the peak height of each hand. When the hand drops
more than `DOWNWARD_STRIKE_THRESHOLD` (45mm) from its peak, a hit fires — subject to a
retrigger cooldown (`RETRIGGER_COOLDOWN = 0.1304s`) to prevent double-hits from one gesture.

```python
if current_height > state.last_trigger_height:
    state.last_trigger_height = current_height   # track peak
    return
if last_trigger_height - current_height > DOWNWARD_STRIKE_THRESHOLD:
    if time.time() - state.last_trigger_time >= RETRIGGER_COOLDOWN:
        # fire hit
```

### Open hand check
Only open hands trigger. Closed fists are ignored:
```python
is_open = hand.grab_strength < OPEN_HAND_THRESHOLD   # 0.15
```
For tonal (sustain) channels, **closing the fist releases the sustained note**.

### Tonal / sustain mode vs. drum / impulse mode
Each channel has a `'tonal': bool` flag:
- `False` (drum): downward strike fires a 50ms `note_on`/`note_off` pair
- `True` (tonal): entering the zone fires `note_on`, which sustains until hand closes, leaves zone,
  or disappears from the sensor frame

Tonal channels opt out of auto note_off by passing `auto_note_off=False` to the MIDI queue.

---

## 9. Threading Model

The scripts use three threads:

| Thread | What it does |
|--------|-------------|
| Leap poll thread (internal) | Managed by the leapc bindings; fires `on_tracking_event` callbacks |
| Main thread | Holds `with connection.open():` and `with device_stack:` open; sleeps in a loop |
| MIDI sender thread | Drains `queue.Queue`, sends `port.send()`, handles 50ms note_off delay for drums |

The tracking callback must be kept fast — no blocking calls. All MIDI sends go through the queue.

The queue uses `put_nowait` (non-blocking) with `maxsize=200`. If full, messages are dropped with
a debug print. In practice with 2–3 modules this queue never approaches capacity.

Lock usage:
- `_device_map_lock` / `_roles_lock`: protects the device→player assignment dict (written in
  `on_device_event` on the Leap thread, read in `on_tracking_event` on the same thread — but also
  potentially read from the input thread in `perc_and_chords.py`)
- `_config_lock` in `perc_and_chords.py`: protects `_chord_bands` and `_drum_config` during
  real-time scale swaps (written by the input thread, read by the Leap tracking thread)

---

## 10. Scale and Chord System (`perc_and_chords.py`)

### Scale bank
30 scales stored as lists of semitone intervals from the root across one octave. Organized as:
- 7 modes of the major scale (Ionian through Locrian)
- Harmonic Minor, Melodic Minor
- Pentatonic Major/Minor
- Blues Major/Minor
- Japanese scales: Hirajoshi, In Sen, Iwato, Yo
- Symmetric: Whole Tone, Diminished HW, Diminished WH
- Exotic heptatonic: Hungarian Minor/Major, Phrygian Dominant, Byzantine, Neapolitan Minor/Major,
  Persian, Arabic, Enigmatic, Prometheus, Tritone Scale

### Chord building — the correct algorithm
For each scale degree `d`, four voices are built by taking every other degree:
indices `d, d+2, d+4, d+6` — all taken **modulo the scale length** with octave shifts
added each time the index wraps around.

```python
for v in range(4):
    raw_idx      = degree + v * 2
    scale_idx    = raw_idx % n              # wrap within scale
    octave_shift = (raw_idx // n) * 12     # add octave each full wrap
    note         = root_midi + intervals[scale_idx] + octave_shift
    if prev_note is not None and note <= prev_note:
        note += 12   # guarantee strictly ascending
```

**Why this matters:** The old (broken) algorithm built an `extended` flat array and indexed it by
position-skip, which happened to work for 7-note scales but produced wrong intervals for pentatonic
and 5-note scales (the skip of 2 positions in a 5-note array is a wider interval than in a 7-note array).
The modulo approach is correct for all scale lengths.

### Height-to-chord mapping
`Y_MIN` to `Y_MAX` (100–500mm) is divided into `n` equal bands where `n = len(scale)`.
Moving the hand up selects higher-numbered scale degrees:
```python
ratio = (clamped_y - Y_MIN) / (Y_MAX - Y_MIN)
band  = min(int(ratio * n), n - 1)
```

### Real-time scale change
In `perc_and_chords.py`, a background daemon thread watches stdin. Typing `c` + Enter opens the
scale selector. The chord release happens before the config swap:
```python
def update_scale(self, new_chord_bands, new_drum_config):
    with self._config_lock:
        old_bands = self._chord_bands
        for key, state in list(self._chord_states.items()):
            if state.is_sustaining:
                self._close_chord_with_bands(old_bands, state.current_band, key, state)
        self._chord_bands = new_chord_bands
        self._drum_config = new_drum_config
```

### Key-relative drum pitches
Kick and snare note numbers shift with the root:
```python
KICK_BASE_NOTE  = 48   # C3
SNARE_BASE_NOTE = 50   # D3
# In key of F#: kick = 48 + 6 = 54 (F#3), snare = 50 + 6 = 56 (G#3)
```
Hat (42) and Clap (39) are fixed — less pitched, more textural.

---

## 11. Known Issues and Lessons Learned

### Things that work
- The `contextlib.ExitStack` + `multi_device_aware=True` + `subscribe_events()` pattern is solid
  and handles 2–4 devices reliably
- The async MIDI queue with `auto_note_off=True` in the sender thread completely eliminates stuck notes
- Keying hand state by `(device_id, hand.id)` tuples correctly handles cross-device ID collisions
- The `_config_lock` approach for real-time scale swaps is safe — no stuck notes on scale change
- Sending CC 123 (all notes off) on shutdown clears any in-flight state cleanly
- Serial numbers via `device.get_info()` are stable across reconnects; used in `four_module.py` to
  remap the same physical module to the same player even when the device_id changes

### Things that don't work / gotchas

- **`device.open()` as a plain call** — silently produces `None` for the C pointer. Always use
  `device_stack.enter_context(device.open())`

- **Methods accidentally defined outside the class body** — Python will not error at parse time;
  the AttributeError only appears at runtime when the method is first called from a callback.
  Always verify indentation when adding new methods to listener classes.

- **`event.device_id`** — does not exist. The correct path is `event.metadata.device_id`

- **First-seen device order** — device IDs (1, 2, 3) are assigned by the Leap service and are not
  guaranteed to be consistent across runs. Use first-seen assignment logic, not hardcoded IDs.
  To make assignments deterministic, use `device.get_info()` to read serial numbers (requires the
  device to be opened first via the same open+subscribe flow)

- **USB cable type is critical** — charge-only USB-C cables will enumerate the device but produce
  no tracking data. All cables must be data-transfer capable. The Anker A8758 USB-C to USB-C cables
  confirmed working.

- **USB bandwidth and USB power are two separate failure modes** — diagnose which you have before
  prescribing a fix:

  *Power starvation* (bus-powered hub, too many modules): devices connect then immediately drop
  with 0 tracking frames. Reconnect loop continues indefinitely. Fix: powered hub with AC adapter.
  Confirmed: Acer ODK4LO with 4 modules — 3 of 4 drop within seconds; only 1 survives per run.

  *Bandwidth throttling* (USB 2.0 connection): device stays connected, never drops, but delivers
  only 3–8 Hz instead of 120 Hz. 0 frames during stale detection; gestures effectively invisible.
  Fix: move module to USB 3.0 port (TB4 hub). A powered hub on a USB 2.0 port changes nothing.

  Measured Hz reference (Samsung Galaxy Book 2 Pro 360):
  - Standard USB-C port (direct, no hub): 3–8 Hz → USB 2.0
  - USB-A port → generic hub: 30–66 Hz → USB 3.0 marginal
  - TB4 → Acer hub (1 module): 89–90 Hz → USB 3.0 solid
  - TB4 → powered hub (4 modules, projected): ~89–90 Hz each

- **Double `on_device_event` for the same device_id** — the Leap SDK can fire `on_device_event`
  twice for the same device ID during startup or after a brief USB hiccup. If your listener calls
  `device_stack.enter_context(device.open())` a second time on an already-open device, it corrupts
  the underlying C handle and causes the Gemini service to drop and re-enumerate ALL devices (the
  reconnect loop and escalating device IDs seen in earlier sessions). Fix: guard with a set of
  already-opened device IDs and return early if the ID is already present. See `four_module.py`.

- **Stuck note via device loss (Module 3 synth)** — if the device running Module 3's synth side
  disconnects while a hand is sustaining a note (`is_sustaining=True`), tracking events stop and
  `_m3_close_synth` never fires. The Syntakt holds an open `note_on` indefinitely, muting the
  sequencer track exactly as in §6. Fix: `on_device_lost_event` must force-close any sustaining
  synth notes and clean up hand state for the lost device before returning. Implemented in
  `four_module.py`. This is a separate trigger path from the queue-drop bug (§6) — both require
  the auto-note-off / cleanup-on-loss defenses to be independent.

- **`_drum_states` and `_m3_states` memory leak on repeated reconnects** — hand state dict entries
  keyed by `(old_device_id, hand_id)` are only cleaned up by the per-frame stale-hand logic, which
  requires the device to be actively sending events. If a device disconnects, its entries accumulate.
  Fix: explicitly clean them up in `on_device_lost_event`.

- **The Syntakt stuck note / sequencer mute bug** — external MIDI note_on with no matching note_off
  permanently mutes the sequencer track for that channel. See Section 6 for full diagnosis and fix.

- **mido channel 0-indexing** — always remember `channel=0` = Syntakt MIDI channel 1. The Syntakt
  displays channels as 1-based. Off-by-one errors here cause silent failures (wrong track fires or nothing fires).

- **The Gemini service must be running** — if `import leap` succeeds but no `on_device_event` fires,
  check the Ultraleap Control Panel tray icon. The service may have crashed or not started.

- **Python 3.12 + precompiled leapc_cffi** — the binary bundled with Gemini targets Python 3.8 only.
  Building from source is mandatory for Python 3.12. See Section 3 for instructions.

### Diagnosing multi-module issues — quick checklist
Run `diagnose.py` first.

**Step 1 — count devices that appear in the stats at all:**
- Fewer than expected? → Some failed to open. Check physical connections and cables.

**Step 2 — read the Hz column for devices that do appear:**
- **0 Hz AND `lost×N`** → Power starvation. Device connected and immediately dropped with 0 frames.
  Fix: powered hub (AC adapter) on the TB4 port. This is the Acer ODK4LO symptom with 4 modules.
- **Hz < 10, no lost events** → USB 2.0 bandwidth. Device is stable but protocol is too slow.
  Fix: move module to a USB 3.0+ path. A powered hub won't help if the port is USB 2.0.
- **Hz 10–80** → USB 3.0 but congested or shared with other high-bandwidth devices.
- **Hz > 85** → Healthy. Gesture detection works correctly.

**Step 3 — check reconnect flags:**
- **`open_count > 1` or `lost_count > 0`** → Intermittent USB dropout. Usually power starvation
  from a bus-powered hub. The dedup guard in `four_module.py` prevents the reconnect cascade;
  the serial-based reassignment ensures the correct player slot is restored on reconnect.

**Step 4 — if all Hz OK but triggers still missing:**
- Check `four_module.py` health output: `STALE` player → device just reconnected or bandwidth lag
- Enable `ENABLE_DEBUG_PRINTS = True` to see per-hit console output

---

## 12. Development Environment

### Virtual environment (PowerShell)
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install mido python-rtmidi numpy build cffi
```

### Running scripts
```powershell
# Make sure Ultraleap Gemini service is running (check tray icon)
# Make sure Syntakt is powered on and connected via USB
python drum_circle.py
# or
python perc_and_chords.py
```

### Confirming MIDI port name
```python
import mido
print(mido.get_output_names())
# Update MIDI_PORT_NAME in the script with the exact string returned
```

### Confirming Syntakt triggers correctly (one-liner test)
```python
import mido, time
with mido.open_output('Syntakt  Port 1 0') as port:
    port.send(mido.Message('note_on',  channel=0, note=60, velocity=100))
    time.sleep(0.05)
    port.send(mido.Message('note_off', channel=0, note=60, velocity=0))
# Track 1 on the Syntakt should fire
```

### Debug mode
Set `ENABLE_DEBUG_PRINTS = True` in any script to enable verbose per-hit console output.
Turn off for performance — print calls on the tracking thread add measurable latency at 120Hz.

---

## 13. File Reference

| File | Status | Description |
|------|--------|-------------|
| `drum_circle.py` | ✅ Working | 3-module drum circle, tonal/drum per-channel config |
| `perc_and_chords.py` | ✅ Working | 2-module: drums + chord controller with 30-scale bank, real-time scale change, key-relative drum pitches |
| `four_module.py` | ✅ Working | 4-module: modules 1/2/4 are 2-zone drums, module 3 is split left-drum/right-synth (Ch 6 + Ch 8, C-minor blues). Includes serial-based reconnect remapping, dedup guard, device-loss cleanup |
| `diagnose.py` | ✅ Working | Per-device Hz, serial numbers, reconnect/lost event log. Run this first when debugging multi-module issues |
| `quad.py` | ✅ Working (original) | Single-device baseline — the confirmed starting point, no multi-device |
| `CLAUDE.md` | This file | Full project context for Claude Code |

---

## 14. Syntakt Physical Setup Checklist

Run through this before every session:

- [ ] Syntakt powered on and USB connected to computer (not through the Leap hub)
- [ ] Windows recognizes Syntakt as a MIDI device (check Device Manager or `mido.get_output_names()`)
- [ ] `SETTINGS > MIDI > MIDI CHANNELS`: Track N = Ch N for tracks 1–12
- [ ] `SETTINGS > MIDI > MIDI IN PORT`: `USB`
- [ ] `SETTINGS > MIDI > RECEIVE NOTES`: `ON` for all tracks
- [ ] Syntakt is in PLAY mode (sequencer running) if you want clock sync
- [ ] Ultraleap Gemini service running (tray icon visible and green)
- [ ] All Leap modules show LED activity (solid green = tracking, other = check connection)
- [ ] All modules connected through the **Thunderbolt 4 port** (not standard USB-C) — run `diagnose.py` and confirm all devices show Hz > 85
- [ ] `.venv` activated in PowerShell before running any script
- [ ] `MIDI_PORT_NAME` in the script matches the exact string from `mido.get_output_names()`