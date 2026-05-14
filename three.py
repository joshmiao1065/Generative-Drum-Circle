"""
three.py — 3-Module Percussion + Melodic System
Three Ultraleap modules → Elektron Syntakt via USB MIDI

MODULE 1 (1st device seen):  Ch 1 left / Ch 3 right    [drum / drum]
MODULE 2 (2nd device seen):  Ch 2 left / Ch 4 right    [drum / drum]
MODULE 3 (3rd device seen):  Ch 8 left / Ch 12 right   [melodic / drum]

  Ch 8 (left zone of Module 3) is tonal: hand height maps to a note in
  the scale and key chosen at startup.  Sustains while hand is open and
  in zone; releases on fist-close or zone-exit.

  Ch 12 (right zone of Module 3) is a normal downward-strike drum.

SYNTAKT SETUP
  SETTINGS > MIDI > MIDI CHANNELS : Track N = Ch N
  SETTINGS > MIDI > MIDI IN PORT  : USB
  SETTINGS > MIDI > RECEIVE NOTES : ON

  Channels used: 1, 2, 3, 4, 8, 12
"""

import contextlib
import leap
import time
import mido
import threading
import queue

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MIDI_PORT_NAME      = 'USB MIDI Interface 1'
ENABLE_DEBUG_PRINTS = False

# Play zone (mm, relative to each module's center)
X_RANGE = [-400, 400]
Z_RANGE = [-400, 400]
Y_MIN   = 50
Y_MAX   = 500   # upper bound for melodic height mapping

# Strike detection
DOWNWARD_STRIKE_THRESHOLD = 50.0
OPEN_HAND_THRESHOLD       = 0.20
RETRIGGER_COOLDOWN        = 0.1190
MAX_VELOCITY              = 127

# ---------------------------------------------------------------------------
# Scale bank and note names (for Ch 8 melodic setup)
# ---------------------------------------------------------------------------

NOTE_NAMES: dict[str, int] = {
    'C': 0, 'C#': 1, 'Db': 1, 'D': 2, 'D#': 3, 'Eb': 3,
    'E': 4, 'F': 5, 'F#': 6, 'Gb': 6, 'G': 7, 'G#': 8,
    'Ab': 8, 'A': 9, 'A#': 10, 'Bb': 10, 'B': 11,
}

SCALES: dict[str, list[int]] = {
    'major':            [0, 2, 4, 5, 7, 9, 11],
    'minor':            [0, 2, 3, 5, 7, 8, 10],
    'dorian':           [0, 2, 3, 5, 7, 9, 10],
    'phrygian':         [0, 1, 3, 5, 7, 8, 10],
    'lydian':           [0, 2, 4, 6, 7, 9, 11],
    'mixolydian':       [0, 2, 4, 5, 7, 9, 10],
    'locrian':          [0, 1, 3, 5, 6, 8, 10],
    'harmonic_minor':   [0, 2, 3, 5, 7, 8, 11],
    'melodic_minor':    [0, 2, 3, 5, 7, 9, 11],
    'pentatonic_major': [0, 2, 4, 7, 9],
    'pentatonic_minor': [0, 3, 5, 7, 10],
    'blues_major':      [0, 2, 3, 4, 7, 9],
    'blues_minor':      [0, 3, 5, 6, 7, 10],
    'whole_tone':       [0, 2, 4, 6, 8, 10],
    'hirajoshi':        [0, 2, 3, 7, 8],
    'in_sen':           [0, 1, 5, 7, 10],
    'iwato':            [0, 1, 5, 6, 10],
    'hungarian_minor':  [0, 2, 3, 6, 7, 8, 11],
    'phrygian_dominant':[0, 1, 4, 5, 7, 8, 10],
    'persian':          [0, 1, 4, 5, 6, 8, 11],
}

# Populated at startup by prompt_melodic_config(); read by _height_to_note()
MELODIC_NOTES: list[int] = []

# ---------------------------------------------------------------------------
# Per-module channel assignments
# mido channels are 0-indexed: channel=0 → Syntakt MIDI Ch 1
#
# Each zone dict:
#   channel  : mido 0-indexed channel
#   note     : MIDI note (drums only; tonal mode ignores this)
#   name     : display label
#   tonal    : True → sustain/height-mapped melody; False → drum impulse
# ---------------------------------------------------------------------------

MODULE_CONFIG: dict[int, list[dict]] = {
    1: [
        {'channel': 0,  'note': 60, 'name': 'Ch1',  'tonal': False},  # left
        {'channel': 2,  'note': 60, 'name': 'Ch3',  'tonal': False},  # right
    ],
    2: [
        {'channel': 1,  'note': 60, 'name': 'Ch2',  'tonal': False},  # left
        {'channel': 3,  'note': 60, 'name': 'Ch4',  'tonal': False},  # right
    ],
    3: [
        {'channel': 7,  'note': 60, 'name': 'Ch8',  'tonal': True},   # left  — melodic
        {'channel': 11, 'note': 60, 'name': 'Ch12', 'tonal': False},  # right — drum
    ],
}

# ---------------------------------------------------------------------------
# Hand state
# ---------------------------------------------------------------------------

class HandState:
    __slots__ = [
        'hand_id', 'is_active', 'current_zone',
        'last_trigger_height', 'last_trigger_time',
        'is_sustaining', 'current_note', 'current_channel',
    ]

    def __init__(self, hand_id: int):
        self.hand_id             = hand_id
        self.is_active           = False
        self.current_zone        = None
        self.last_trigger_height = 0.0
        self.last_trigger_time   = 0.0
        self.is_sustaining       = False
        self.current_note        = None
        self.current_channel     = None

    def activate(self, zone: int, height: float):
        self.is_active           = True
        self.current_zone        = zone
        self.last_trigger_height = height
        self.last_trigger_time   = time.time()

    def deactivate(self):
        self.is_active    = False
        self.current_zone = None


# ---------------------------------------------------------------------------
# Listener
# ---------------------------------------------------------------------------

class ThreeModuleListener(leap.Listener):

    def __init__(self, port, device_stack, connection):
        super().__init__()
        self.port          = port
        self._device_stack = device_stack
        self._connection   = connection

        self._device_map: dict[int, int]  = {}
        self._device_map_lock             = threading.Lock()
        self._serial_to_player: dict[str, int] = {}
        self._device_serials:   dict[int, str] = {}
        self._opened_device_ids: set[int]      = set()
        self._player_last_seen:  dict[int, float] = {}

        self._hand_states: dict[tuple, HandState] = {}

        self.midi_queue  = queue.Queue(maxsize=200)
        self.midi_thread = threading.Thread(target=self._midi_sender, daemon=True)
        self.midi_thread.start()

    # ------------------------------------------------------------------
    # Device lifecycle
    # ------------------------------------------------------------------

    def on_device_event(self, event):
        device    = event.device
        device_id = device.id

        with self._device_map_lock:
            if device_id in self._opened_device_ids:
                print(f"[Device] id={device_id} duplicate event — already open, skipping")
                return
            self._opened_device_ids.add(device_id)

        print(f"[Device] id={device_id} detected — opening & subscribing...")
        self._device_stack.enter_context(device.open())

        try:
            info   = device.get_info()
            serial = (getattr(info, 'serial_number', None)
                      or getattr(info, 'serial', None)
                      or str(device_id))
            with self._device_map_lock:
                self._device_serials[device_id] = serial
            print(f"[Device] id={device_id} serial={serial}")
        except Exception:
            pass

        self._connection.subscribe_events(device)
        print(f"[Device] id={device_id} subscribed ✓")

    def on_device_lost_event(self, event):
        device_id = getattr(event.device, 'id', None)
        if device_id is None:
            return

        with self._device_map_lock:
            self._opened_device_ids.discard(device_id)
            player = self._device_map.pop(device_id, None)

        for k in [k for k in self._hand_states if k[0] == device_id]:
            state = self._hand_states[k]
            if state.is_sustaining:
                self._release_tonal(state)
            del self._hand_states[k]

        if player is not None:
            print(f"[Device] id={device_id} LOST (was Player {player}) — "
                  f"will reassign by serial on reconnect")

    # ------------------------------------------------------------------
    # Player assignment
    # ------------------------------------------------------------------

    def _get_player(self, device_id: int) -> int | None:
        labels = {
            1: 'Ch 1 (left) / Ch 3 (right)   [drum/drum]',
            2: 'Ch 2 (left) / Ch 4 (right)   [drum/drum]',
            3: 'Ch 8 (left) / Ch 12 (right)  [melodic/drum]',
        }
        with self._device_map_lock:
            if device_id in self._device_map:
                return self._device_map[device_id]

            serial = self._device_serials.get(device_id)
            if serial and serial in self._serial_to_player:
                player = self._serial_to_player[serial]
                self._device_map[device_id] = player
                print(f"[Reassign] Device {device_id} → Player {player} "
                      f"(serial match — reconnect) : {labels[player]}")
                return player

            occupied = set(self._device_map.values())
            n = 1
            while n in occupied:
                n += 1
            if n > 3:
                return None

            self._device_map[device_id] = n
            if serial:
                self._serial_to_player[serial] = n
            print(f"[Assign] Device {device_id} → Player {n}: {labels[n]}")
            return n

    # ------------------------------------------------------------------
    # Tracking
    # ------------------------------------------------------------------

    def on_tracking_event(self, event):
        device_id = event.metadata.device_id
        player    = self._get_player(device_id)
        if player is None:
            return

        self._player_last_seen[player] = time.time()
        self._process(device_id, event.hands, MODULE_CONFIG[player])

    def _process(self, device_id: int, hands, config: list):
        current_keys = {(device_id, h.id) for h in hands}

        for hand in hands:
            pos     = hand.palm.position
            key     = (device_id, hand.id)
            in_zone = self._in_zone(pos)
            is_open = hand.grab_strength < OPEN_HAND_THRESHOLD
            zone    = self._x_zone(pos) if in_zone else None

            if key not in self._hand_states:
                self._hand_states[key] = HandState(hand.id)
            state = self._hand_states[key]

            if not state.is_active:
                if in_zone and is_open and zone is not None:
                    state.activate(zone, pos.y)
                    cfg = config[zone]
                    if cfg['tonal']:
                        self._fire_tonal(cfg, pos.y, key, state)
                    else:
                        self._fire(cfg, key, state)

            elif in_zone and is_open:
                if zone is not None and zone != state.current_zone:
                    # Hand crossed into the other zone
                    if state.is_sustaining:
                        self._release_tonal(state)
                    state.current_zone        = zone
                    state.last_trigger_height = pos.y
                    cfg = config[zone]
                    if cfg['tonal']:
                        self._fire_tonal(cfg, pos.y, key, state)
                    else:
                        self._fire(cfg, key, state)

                elif zone is not None:
                    cfg = config[zone]
                    if cfg['tonal']:
                        # Retrigger only when the mapped note changes
                        new_note = self._height_to_note(pos.y)
                        if new_note != state.current_note:
                            self._release_tonal(state)
                            self._fire_tonal(cfg, pos.y, key, state)
                    else:
                        self._check_strike(state, pos.y, cfg, key)

            else:
                # Zone exit or fist close
                if state.is_sustaining:
                    self._release_tonal(state)
                state.deactivate()

        stale = {k for k in self._hand_states if k[0] == device_id} - current_keys
        for k in stale:
            state = self._hand_states[k]
            if state.is_sustaining:
                self._release_tonal(state)
            del self._hand_states[k]

    # ------------------------------------------------------------------
    # Gesture + MIDI helpers
    # ------------------------------------------------------------------

    def _check_strike(self, state: HandState, current_height: float,
                      cfg: dict, key: tuple):
        if current_height > state.last_trigger_height:
            state.last_trigger_height = current_height
            return
        if (state.last_trigger_height - current_height > DOWNWARD_STRIKE_THRESHOLD and
                time.time() - state.last_trigger_time >= RETRIGGER_COOLDOWN):
            self._fire(cfg, key, state)
            state.last_trigger_height = current_height

    def _fire(self, cfg: dict, key: tuple, state: HandState):
        self._enqueue(
            mido.Message('note_on', note=cfg['note'],
                         velocity=MAX_VELOCITY, channel=cfg['channel']),
            auto_note_off=True
        )
        state.last_trigger_time = time.time()
        if ENABLE_DEBUG_PRINTS:
            print(f"  HIT {key} → {cfg['name']} ch={cfg['channel'] + 1}")

    def _fire_tonal(self, cfg: dict, y: float, key: tuple, state: HandState):
        note = self._height_to_note(y)
        self._enqueue(
            mido.Message('note_on', note=note,
                         velocity=MAX_VELOCITY, channel=cfg['channel']),
            auto_note_off=False
        )
        state.is_sustaining   = True
        state.current_note    = note
        state.current_channel = cfg['channel']
        state.last_trigger_time = time.time()
        if ENABLE_DEBUG_PRINTS:
            print(f"  TONAL ON  {key} → {cfg['name']} note={note} ch={cfg['channel']+1}")

    def _release_tonal(self, state: HandState):
        if state.current_note is not None and state.current_channel is not None:
            self._enqueue(
                mido.Message('note_off', note=state.current_note,
                             velocity=0, channel=state.current_channel),
                auto_note_off=False
            )
            if ENABLE_DEBUG_PRINTS:
                print(f"  TONAL OFF note={state.current_note} ch={state.current_channel+1}")
        state.is_sustaining   = False
        state.current_note    = None
        state.current_channel = None

    def _height_to_note(self, y: float) -> int:
        """Map hand height (mm) to a MIDI note in MELODIC_NOTES."""
        y_clamped = max(float(Y_MIN), min(float(Y_MAX), y))
        ratio     = (y_clamped - Y_MIN) / (Y_MAX - Y_MIN)
        idx       = min(int(ratio * len(MELODIC_NOTES)), len(MELODIC_NOTES) - 1)
        return MELODIC_NOTES[idx]

    def _in_zone(self, pos) -> bool:
        return (X_RANGE[0] <= pos.x <= X_RANGE[1] and
                Z_RANGE[0] <= pos.z <= Z_RANGE[1] and
                pos.y >= Y_MIN)

    def _x_zone(self, pos) -> int | None:
        if not self._in_zone(pos):
            return None
        return 0 if pos.x < 0 else 1

    # ------------------------------------------------------------------
    # MIDI sender thread
    # ------------------------------------------------------------------

    def _enqueue(self, msg: mido.Message, auto_note_off: bool):
        try:
            self.midi_queue.put_nowait((msg, auto_note_off))
        except queue.Full:
            if ENABLE_DEBUG_PRINTS:
                print("MIDI queue full — dropping message")

    def _midi_sender(self):
        while True:
            try:
                item = self.midi_queue.get(timeout=1.0)
                if item is None:
                    break
                msg, auto_note_off = item
                self.port.send(msg)
                if auto_note_off and msg.type == 'note_on' and msg.velocity > 0:
                    time.sleep(0.05)
                    self.port.send(mido.Message('note_off',
                        note=msg.note, velocity=0, channel=msg.channel))
            except queue.Empty:
                continue
            except Exception as e:
                if ENABLE_DEBUG_PRINTS:
                    print(f"MIDI send error: {e}")

    # ------------------------------------------------------------------
    # Health + shutdown
    # ------------------------------------------------------------------

    def print_health(self):
        now = time.time()
        with self._device_map_lock:
            device_map = dict(self._device_map)
        print("\n[Health]")
        for player in range(1, 4):
            last = self._player_last_seen.get(player)
            if last is None:
                status = "NO DATA"
            elif now - last < 2.0:
                status = f"OK  ({now-last:.1f}s ago)"
            else:
                status = f"STALE ({now-last:.0f}s ago)"
            device_ids = [str(d) for d, p in device_map.items() if p == player]
            dev_str = ",".join(device_ids) or "—"
            print(f"  Player {player}: device_id={dev_str:>6}  {status}")
        print()

    def shutdown(self):
        print("Sending all-notes-off on all 12 channels...")
        for channel in range(12):
            try:
                self.port.send(mido.Message('control_change',
                    channel=channel, control=123, value=0))
            except Exception:
                pass
        self.midi_queue.put(None)
        self.midi_thread.join(timeout=2.0)


# ---------------------------------------------------------------------------
# Melodic config prompt
# ---------------------------------------------------------------------------

def prompt_melodic_config() -> list[int]:
    """Ask for root key and scale; return sorted list of MIDI notes (3 octaves)."""
    print("=== Channel 8 — Melodic Setup ===")
    print(f"Scales: {', '.join(sorted(SCALES.keys()))}")
    print()

    while True:
        raw = input("Root note (e.g. C, D#, Bb): ").strip()
        if raw in NOTE_NAMES:
            root_semitone = NOTE_NAMES[raw]
            break
        print(f"  Unknown '{raw}'. Options: {', '.join(sorted(NOTE_NAMES.keys()))}")

    while True:
        name = input("Scale name: ").strip().lower().replace(' ', '_')
        if name in SCALES:
            intervals = SCALES[name]
            break
        print(f"  Unknown '{name}'.")

    # 3 octaves starting from the root at octave 3 (MIDI 48 = C3)
    base  = 48 + root_semitone
    notes = sorted({
        base + octave * 12 + interval
        for octave in range(3)
        for interval in intervals
        if 0 <= base + octave * 12 + interval <= 127
    })

    print(f"\n  {raw} {name} — {len(notes)} notes, "
          f"MIDI {notes[0]}–{notes[-1]}: {notes}")
    print()
    return notes


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    global MELODIC_NOTES
    MELODIC_NOTES = prompt_melodic_config()

    listener = None
    try:
        with mido.open_output(MIDI_PORT_NAME) as port:
            print(f"MIDI port : {port.name}")
            print(f"Debug     : {'ON' if ENABLE_DEBUG_PRINTS else 'OFF'}")
            print()
            print("  Player 1 (1st device) : Ch 1 left  / Ch 3 right   [drum / drum]")
            print("  Player 2 (2nd device) : Ch 2 left  / Ch 4 right   [drum / drum]")
            print("  Player 3 (3rd device) : Ch 8 left  / Ch 12 right  [melodic / drum]")
            print()
            print("Waiting for Ultraleap devices...\n")

            device_stack = contextlib.ExitStack()
            connection   = leap.Connection(multi_device_aware=True)
            listener     = ThreeModuleListener(port, device_stack, connection)
            connection.add_listener(listener)

            with device_stack:
                with connection.open():
                    connection.set_tracking_mode(leap.TrackingMode.Desktop)
                    print("Ready — Ctrl+C to exit\n")
                    last_health = time.time()
                    while True:
                        time.sleep(1)
                        if time.time() - last_health >= 10.0:
                            listener.print_health()
                            last_health = time.time()

    except KeyboardInterrupt:
        print("\nShutting down...")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if listener:
            listener.shutdown()


if __name__ == "__main__":
    main()
