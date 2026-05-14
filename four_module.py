"""
four_module.py — Gesture-Controlled 4-Module Performance System
Four Ultraleap modules → Elektron Syntakt via USB MIDI

MODULE 1 (1st device seen):  2-zone drum — Ch 1 & 2
  Left  zone (x < 0):  Ch 1 — downward-strike impulse
  Right zone (x >= 0): Ch 2 — downward-strike impulse

MODULE 2 (2nd device seen):  2-zone drum — Ch 3 & 4
  Left  zone (x < 0):  Ch 3 — downward-strike impulse
  Right zone (x >= 0): Ch 4 — downward-strike impulse

MODULE 3 (3rd device seen):  split — Ch 6 drum (left) + Ch 8 synth (right)
  Left  zone (x < 0):  Ch 6 — downward-strike drum impulse
  Right zone (x >= 0): Ch 8 — sustained synth tone
      Scale:  C-minor blues — C, Eb, F, F#, G, Bb
      Range:  C4–C6 (MIDI 60–84), 13 discrete notes over 2 octaves
      Pitch:  Z-axis only — |z| near 0 (hand over module) = C6 (high)
                            |z| near 200 mm (far from module) = C4 (low)
      Note updates in real-time as the hand moves in Z.
      Open hand sustains; closing fist or leaving zone releases.

MODULE 4 (4th device seen):  2-zone drum — Ch 11 & 12
  Left  zone (x < 0):  Ch 11 — downward-strike impulse
  Right zone (x >= 0): Ch 12 — downward-strike impulse

SYNTAKT SETUP
  SETTINGS > MIDI > MIDI CHANNELS : Track N = Ch N  (for all 12 tracks)
  SETTINGS > MIDI > MIDI IN PORT  : USB
  SETTINGS > MIDI > RECEIVE NOTES : ON

  Channels used: 1, 2, 3, 4, 6, 8, 11, 12
  (Tracks 5, 7, 9, 10 are not addressed by this script)
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

MIDI_PORT_NAME      = 'USB MIDI Interface 1'   # ← exact string from mido.get_output_names()
ENABLE_DEBUG_PRINTS = False

# Play zone (mm, relative to each module's center)
X_RANGE = [-400, 400]
Z_RANGE = [-400, 400]
Y_MIN   = 50   # hands below this height are ignored

# Drum strike detection
DOWNWARD_STRIKE_THRESHOLD = 45.0    # mm drop from peak to fire a hit
OPEN_HAND_THRESHOLD       = 0.15    # grab_strength below this = open hand
RETRIGGER_COOLDOWN        = 0.119  # seconds — minimum time between hits on same channel
MAX_VELOCITY              = 127

# ---------------------------------------------------------------------------
# Module 1, 2, 4 — 2-zone drum configs
# Zone 0 = left  (x < 0)
# Zone 1 = right (x >= 0)
# mido channels are 0-indexed: channel=0 → Syntakt MIDI Ch 1
# ---------------------------------------------------------------------------

MODULE_DRUM_CONFIG: dict[int, list[dict]] = {
    1: [
        {'channel': 0,  'note': 60, 'name': 'Ch1'},   # zone 0 — left
        {'channel': 1,  'note': 60, 'name': 'Ch2'},   # zone 1 — right
    ],
    2: [
        {'channel': 2,  'note': 60, 'name': 'Ch3'},
        {'channel': 3,  'note': 60, 'name': 'Ch4'},
    ],
    4: [
        {'channel': 10, 'note': 60, 'name': 'Ch11'},
        {'channel': 11, 'note': 60, 'name': 'Ch12'},
    ],
}

# ---------------------------------------------------------------------------
# Module 3 — drum channel (left side) + synth channel (right side)
# ---------------------------------------------------------------------------

M3_DRUM_CHANNEL  = 5    # mido 5 → Syntakt Ch 6
M3_DRUM_NOTE     = 60

M3_SYNTH_CHANNEL = 7    # mido 7 → Syntakt Ch 8

# C-minor blues scale: C, Eb, F, F#, G, Bb  (semitone intervals from root)
_BLUES_INTERVALS: list[int] = [0, 3, 5, 6, 7, 10]
_SYNTH_ROOT_MIDI = 60   # C4
_SYNTH_OCTAVES   = 2    # two octaves: C4 through C6

# Build ordered ascending list of all synth notes across the 2-octave range.
# Octave 0 → C4, Eb4, F4, F#4, G4, Bb4
# Octave 1 → C5, Eb5, F5, F#5, G5, Bb5
# Top note  → C6
SYNTH_NOTES: list[int] = []
for _oct in range(_SYNTH_OCTAVES):
    for _iv in _BLUES_INTERVALS:
        SYNTH_NOTES.append(_SYNTH_ROOT_MIDI + _oct * 12 + _iv)
SYNTH_NOTES.append(_SYNTH_ROOT_MIDI + _SYNTH_OCTAVES * 12)   # C6 = MIDI 84
# Final list (13 notes): [60,63,65,66,67,70, 72,75,77,78,79,82, 84]

# Maximum |z| value within the play zone — used to normalise the pitch mapping.
_MAX_Z_DIST = float(max(abs(Z_RANGE[0]), abs(Z_RANGE[1])))   # 200.0 mm

_NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']


def _z_to_note(z: float) -> int:
    """
    Map a Z-position to a blues-scale MIDI note.

    |z| close to 0  (hand over module center) → highest note (C6, MIDI 84)
    |z| near 200 mm (hand far from center)    → lowest note  (C4, MIDI 60)

    X and Y have no effect on pitch.
    """
    z_dist = min(abs(z), _MAX_Z_DIST)
    ratio  = 1.0 - z_dist / _MAX_Z_DIST   # 1.0 at center, 0.0 at far edge
    idx    = round(ratio * (len(SYNTH_NOTES) - 1))
    return SYNTH_NOTES[idx]


# ---------------------------------------------------------------------------
# Hand state — 2-zone drum modules (1, 2, 4)
# ---------------------------------------------------------------------------

class TwoZoneHandState:
    """Per-hand state for 2-zone drum modules (modules 1, 2, 4)."""
    __slots__ = [
        'hand_id', 'is_active', 'current_zone',
        'last_trigger_height', 'last_trigger_time',
    ]

    def __init__(self, hand_id: int):
        self.hand_id             = hand_id
        self.is_active           = False
        self.current_zone        = None   # 0 = left, 1 = right
        self.last_trigger_height = 0.0
        self.last_trigger_time   = 0.0

    def activate(self, zone: int, height: float):
        self.is_active           = True
        self.current_zone        = zone
        self.last_trigger_height = height
        self.last_trigger_time   = time.time()

    def deactivate(self):
        self.is_active    = False
        self.current_zone = None


# ---------------------------------------------------------------------------
# Hand state — Module 3 (split left=drum / right=synth)
# ---------------------------------------------------------------------------

class Module3HandState:
    """
    Per-hand state for Module 3.

    Side 0 (x < 0, left):  drum impulse on Ch 6.
    Side 1 (x >= 0, right): sustained synth on Ch 8, pitch determined by |z|.

    A hand may cross the center line at any time; the listener handles the
    transition cleanly (releases the old behaviour, enters the new one).
    """
    __slots__ = [
        'hand_id', 'is_active', 'current_side',
        'is_sustaining', 'current_synth_note',
        'last_trigger_height', 'last_trigger_time',
    ]

    def __init__(self, hand_id: int):
        self.hand_id             = hand_id
        self.is_active           = False
        self.current_side        = None   # 0 = left/drum, 1 = right/synth
        self.is_sustaining       = False
        self.current_synth_note  = None
        self.last_trigger_height = 0.0
        self.last_trigger_time   = 0.0

    def activate(self, side: int, height: float):
        self.is_active           = True
        self.current_side        = side
        self.last_trigger_height = height
        self.last_trigger_time   = time.time()

    def deactivate(self):
        self.is_active          = False
        self.current_side       = None
        self.is_sustaining      = False
        self.current_synth_note = None


# ---------------------------------------------------------------------------
# Listener
# ---------------------------------------------------------------------------

class FourModuleListener(leap.Listener):
    """
    Handles up to 4 Ultraleap modules simultaneously.
    Devices are assigned player roles 1–4 in first-seen order.

    MIDI queue items are tuples: (mido.Message, auto_note_off: bool)
      auto_note_off=True  → sender fires note_on, sleeps 50 ms, sends note_off  (drums)
      auto_note_off=False → sender passes message through unchanged              (synth)
    """

    def __init__(self, port, device_stack, connection):
        super().__init__()
        self.port          = port
        self._device_stack = device_stack
        self._connection   = connection

        # device_id → player number (1–4).  Written in on_tracking_event (first-seen),
        # cleaned up in on_device_lost_event, re-mapped on reconnect via serial.
        self._device_map: dict[int, int] = {}
        self._device_map_lock = threading.Lock()

        # serial → player: stable across reconnects even when device_id changes.
        self._serial_to_player: dict[str, int] = {}
        # device_id → serial: populated in on_device_event after device.open().
        self._device_serials: dict[int, str]   = {}

        # device_ids we currently have open (entered into device_stack).
        # Prevents double-opening if on_device_event fires twice for the same ID.
        self._opened_device_ids: set[int] = set()

        # player → time of last tracking event (for health diagnostics).
        self._player_last_seen: dict[int, float] = {}

        # Hand state dicts keyed by (device_id, hand.id).
        # IDs can collide across devices — the tuple prevents confusion.
        self._drum_states: dict[tuple, TwoZoneHandState] = {}
        self._m3_states:   dict[tuple, Module3HandState] = {}

        self.midi_queue  = queue.Queue(maxsize=200)
        self.midi_thread = threading.Thread(target=self._midi_sender, daemon=True)
        self.midi_thread.start()

        if ENABLE_DEBUG_PRINTS:
            print("FourModuleListener initialised — waiting for devices...")

    # ------------------------------------------------------------------
    # Device lifecycle
    # ------------------------------------------------------------------

    def on_device_event(self, event):
        device    = event.device
        device_id = device.id

        with self._device_map_lock:
            if device_id in self._opened_device_ids:
                # on_device_event fired twice for the same ID — a known SDK quirk
                # when devices are on hubs, or caused by brief USB re-enumeration.
                # Re-entering the context manager on an already-open device corrupts
                # the C pointer. Skip it.
                print(f"[Device] id={device_id} duplicate event — already open, skipping")
                return
            self._opened_device_ids.add(device_id)

        print(f"[Device] id={device_id} detected — opening & subscribing...")
        # enter_context keeps the C device pointer alive for the program lifetime.
        # Calling device.open() as a plain function (not a context manager) leaves
        # the internal C pointer as None and crashes later — see CLAUDE.md §7.
        self._device_stack.enter_context(device.open())

        # Read serial number while the device is open.  Used to remap the same
        # physical module to the same player even when its device_id changes after
        # a USB reconnect.
        try:
            info   = device.get_info()
            serial = (getattr(info, 'serial_number', None)
                      or getattr(info, 'serial', None)
                      or str(device_id))
            with self._device_map_lock:
                self._device_serials[device_id] = serial
            print(f"[Device] id={device_id} serial={serial}")
        except Exception:
            pass   # serial unknown — reconnect remapping won't work for this device

        self._connection.subscribe_events(device)
        print(f"[Device] id={device_id} subscribed ✓")

    def on_device_lost_event(self, event):
        """Called when a device disconnects.
        - Clears it from the open-set so on_device_event will re-open it on reconnect.
        - Force-closes any sustained synth note on Module 3's device.
          Without this, a device dropout while a note is sustaining leaves the
          Syntakt holding an unresolved note_on — which silences the sequencer
          track exactly as described in CLAUDE.md §6, but triggered by device loss
          rather than a queue drop.
        - Cleans up all hand-state entries for the lost device.
        """
        device_id = getattr(event.device, 'id', None)
        if device_id is None:
            return

        with self._device_map_lock:
            self._opened_device_ids.discard(device_id)
            player = self._device_map.pop(device_id, None)

        # Release any sustained synth notes for hands belonging to this device
        # before the tracking events stop arriving.
        stale_m3 = {k: s for k, s in self._m3_states.items() if k[0] == device_id}
        for k, s in stale_m3.items():
            if s.is_sustaining:
                self._m3_close_synth(k, s)
            del self._m3_states[k]

        # Clean up drum hand states for this device (prevents memory accumulation
        # across multiple reconnect cycles during a long session).
        for k in [k for k in self._drum_states if k[0] == device_id]:
            del self._drum_states[k]

        if player is not None:
            print(f"[Device] id={device_id} LOST (was Player {player}) — "
                  f"will reassign by serial on reconnect")

    # ------------------------------------------------------------------
    # Player assignment (thread-safe, first-seen)
    # ------------------------------------------------------------------

    def _get_player(self, device_id: int) -> int | None:
        labels = {
            1: 'Module 1 — Ch 1 (left) / Ch 2 (right)        [drum]',
            2: 'Module 2 — Ch 3 (left) / Ch 4 (right)        [drum]',
            3: 'Module 3 — Ch 6 left drum / Ch 8 right synth',
            4: 'Module 4 — Ch 11 (left) / Ch 12 (right)      [drum]',
        }
        with self._device_map_lock:
            # Already mapped — return immediately.
            if device_id in self._device_map:
                return self._device_map[device_id]

            # Check if this device's serial matches a player we've seen before.
            # This handles the common case where a module reconnects with a new
            # device_id after a USB dropout.
            serial = self._device_serials.get(device_id)
            if serial and serial in self._serial_to_player:
                player = self._serial_to_player[serial]
                self._device_map[device_id] = player
                print(f"[Reassign] Device {device_id} → Player {player} "
                      f"(serial match — reconnect) : {labels[player]}")
                return player

            # Brand-new device.  Count unique players currently assigned.
            occupied = set(self._device_map.values())
            n = 1
            while n in occupied:
                n += 1
            if n > 4:
                return None   # All 4 player slots taken by still-active devices.

            self._device_map[device_id] = n
            if serial:
                self._serial_to_player[serial] = n
            print(f"[Assign] Device {device_id} → Player {n}: {labels[n]}")
            return n

    # ------------------------------------------------------------------
    # Tracking dispatcher
    # ------------------------------------------------------------------

    def on_tracking_event(self, event):
        device_id = event.metadata.device_id
        player    = self._get_player(device_id)
        if player is None:
            return

        self._player_last_seen[player] = time.time()

        if player == 3:
            self._process_module3(device_id, event.hands)
        else:
            self._process_2zone_drum(device_id, event.hands, MODULE_DRUM_CONFIG[player])

    def print_health(self):
        """Print which players are actively receiving tracking events."""
        now = time.time()
        with self._device_map_lock:
            device_map = dict(self._device_map)
        lines = []
        for player in range(1, 5):
            last = self._player_last_seen.get(player)
            if last is None:
                status = "NO DATA"
            elif now - last < 2.0:
                status = f"OK  ({now-last:.1f}s ago)"
            else:
                status = f"STALE ({now-last:.0f}s ago)"
            device_ids = [str(d) for d, p in device_map.items() if p == player]
            dev_str = ",".join(device_ids) or "—"
            lines.append(f"  Player {player}: device_id={dev_str:>6}  {status}")
        print("\n[Health]")
        for l in lines:
            print(l)
        print()

    # ------------------------------------------------------------------
    # 2-zone drum processing (modules 1, 2, 4)
    # ------------------------------------------------------------------

    def _process_2zone_drum(self, device_id: int, hands, config: list):
        current_keys = {(device_id, h.id) for h in hands}

        for hand in hands:
            pos     = hand.palm.position
            key     = (device_id, hand.id)
            in_zone = self._in_zone(pos)
            is_open = hand.grab_strength < OPEN_HAND_THRESHOLD
            zone    = self._x_zone(pos) if in_zone else None   # 0=left, 1=right

            if key not in self._drum_states:
                self._drum_states[key] = TwoZoneHandState(hand.id)
            state = self._drum_states[key]

            if not state.is_active:
                if in_zone and is_open and zone is not None:
                    state.activate(zone, pos.y)
                    self._fire_drum(config[zone], key, state)

            elif in_zone and is_open:
                if zone is not None and zone != state.current_zone:
                    # Hand crossed the center line — entry hit in new zone
                    state.current_zone        = zone
                    state.last_trigger_height = pos.y
                    self._fire_drum(config[zone], key, state)
                elif zone is not None:
                    self._check_strike(state, pos.y, config[zone], key)

            else:
                # Hand closed or left zone
                state.deactivate()

        # Clean up hands that left the sensor frame
        stale = {k for k in self._drum_states if k[0] == device_id} - current_keys
        for k in stale:
            del self._drum_states[k]

    # ------------------------------------------------------------------
    # Module 3 — left side = drum, right side = synth
    # ------------------------------------------------------------------

    def _process_module3(self, device_id: int, hands):
        current_keys = {(device_id, h.id) for h in hands}

        for hand in hands:
            pos     = hand.palm.position
            key     = (device_id, hand.id)
            in_zone = self._in_zone(pos)
            is_open = hand.grab_strength < OPEN_HAND_THRESHOLD
            side    = self._x_zone(pos) if in_zone else None   # 0=left/drum, 1=right/synth

            if key not in self._m3_states:
                self._m3_states[key] = Module3HandState(hand.id)
            state = self._m3_states[key]

            if not state.is_active:
                if in_zone and is_open and side is not None:
                    state.activate(side, pos.y)
                    if side == 0:
                        self._m3_fire_drum(key, state)
                    else:
                        self._m3_open_synth(_z_to_note(pos.z), key, state)

            elif in_zone and is_open:
                if side is not None and side != state.current_side:
                    # Hand crossed the x=0 boundary
                    if state.is_sustaining:
                        self._m3_close_synth(key, state)
                    state.current_side        = side
                    state.last_trigger_height = pos.y
                    if side == 0:
                        self._m3_fire_drum(key, state)
                    else:
                        self._m3_open_synth(_z_to_note(pos.z), key, state)

                elif side == 0:
                    # Left / drum side — check for downward strike
                    self._check_strike_m3(state, pos.y, key)

                elif side == 1:
                    # Right / synth side — update pitch if Z moved to a different note
                    new_note = _z_to_note(pos.z)
                    if new_note != state.current_synth_note:
                        self._m3_close_synth(key, state)
                        self._m3_open_synth(new_note, key, state)

            else:
                # Hand closed fist or left play zone
                if state.is_sustaining:
                    self._m3_close_synth(key, state)
                state.deactivate()

        # Clean up — release any synth note for hands that vanished from the frame
        stale = {k for k in self._m3_states if k[0] == device_id} - current_keys
        for k in stale:
            s = self._m3_states[k]
            if s.is_sustaining:
                self._m3_close_synth(k, s)
            del self._m3_states[k]

    # -- Module 3 synth helpers ------------------------------------------

    def _m3_open_synth(self, note: int, key: tuple, state: Module3HandState):
        self._enqueue(
            mido.Message('note_on', note=note, velocity=MAX_VELOCITY,
                         channel=M3_SYNTH_CHANNEL),
            auto_note_off=False   # sustained — explicit release via _m3_close_synth
        )
        state.is_sustaining      = True
        state.current_synth_note = note
        if ENABLE_DEBUG_PRINTS:
            name = _NOTE_NAMES[note % 12]
            oct  = note // 12 - 1
            print(f"  SYNTH ON  {key} → {name}{oct} (MIDI {note}) ch={M3_SYNTH_CHANNEL + 1}")

    def _m3_close_synth(self, key: tuple, state: Module3HandState):
        if state.current_synth_note is None:
            return
        self._enqueue(
            mido.Message('note_off', note=state.current_synth_note, velocity=0,
                         channel=M3_SYNTH_CHANNEL),
            auto_note_off=False
        )
        state.is_sustaining      = False
        state.current_synth_note = None
        if ENABLE_DEBUG_PRINTS:
            print(f"  SYNTH OFF {key} ch={M3_SYNTH_CHANNEL + 1}")

    def _m3_fire_drum(self, key: tuple, state: Module3HandState):
        self._enqueue(
            mido.Message('note_on', note=M3_DRUM_NOTE, velocity=MAX_VELOCITY,
                         channel=M3_DRUM_CHANNEL),
            auto_note_off=True   # sender closes it after 50 ms — no stuck notes
        )
        state.last_trigger_time = time.time()
        if ENABLE_DEBUG_PRINTS:
            print(f"  M3 DRUM   {key} ch={M3_DRUM_CHANNEL + 1}")

    def _check_strike_m3(self, state: Module3HandState, current_height: float, key: tuple):
        """Downward-strike detection for the module 3 drum side (ch 6)."""
        if current_height > state.last_trigger_height:
            state.last_trigger_height = current_height   # track rising peak
            return
        if (state.last_trigger_height - current_height > DOWNWARD_STRIKE_THRESHOLD and
                time.time() - state.last_trigger_time >= RETRIGGER_COOLDOWN):
            self._m3_fire_drum(key, state)
            state.last_trigger_height = current_height   # reset peak after strike

    # -- Shared drum helpers (modules 1, 2, 4) ----------------------------

    def _check_strike(self, state: TwoZoneHandState, current_height: float,
                      cfg: dict, key: tuple):
        if current_height > state.last_trigger_height:
            state.last_trigger_height = current_height
            return
        if (state.last_trigger_height - current_height > DOWNWARD_STRIKE_THRESHOLD and
                time.time() - state.last_trigger_time >= RETRIGGER_COOLDOWN):
            self._fire_drum(cfg, key, state)
            state.last_trigger_height = current_height

    def _fire_drum(self, cfg: dict, key: tuple, state: TwoZoneHandState):
        self._enqueue(
            mido.Message('note_on', note=cfg['note'], velocity=MAX_VELOCITY,
                         channel=cfg['channel']),
            auto_note_off=True
        )
        state.last_trigger_time = time.time()
        if ENABLE_DEBUG_PRINTS:
            print(f"  HIT  {key} → {cfg['name']} ch={cfg['channel'] + 1}")

    # ------------------------------------------------------------------
    # Spatial helpers
    # ------------------------------------------------------------------

    def _in_zone(self, pos) -> bool:
        return (X_RANGE[0] <= pos.x <= X_RANGE[1] and
                Z_RANGE[0] <= pos.z <= Z_RANGE[1] and
                pos.y >= Y_MIN)

    def _x_zone(self, pos) -> int | None:
        """0 = left zone (x < 0),  1 = right zone (x >= 0)."""
        if not self._in_zone(pos):
            return None
        return 0 if pos.x < 0 else 1

    # ------------------------------------------------------------------
    # MIDI queue + sender thread
    # ------------------------------------------------------------------

    def _enqueue(self, msg: mido.Message, auto_note_off: bool):
        try:
            self.midi_queue.put_nowait((msg, auto_note_off))
        except queue.Full:
            if ENABLE_DEBUG_PRINTS:
                print("MIDI queue full — dropping message")

    def _midi_sender(self):
        """
        Drain the MIDI queue. Each item: (mido.Message, auto_note_off: bool).

          auto_note_off=True  → send note_on, sleep 50 ms, send note_off.
                                Every drum hit is self-contained; the Syntakt
                                cannot be left holding an open note regardless
                                of what happens next.
          auto_note_off=False → send as-is.
                                Used for synth note_on (sustained until
                                _m3_close_synth) and for explicit note_off.
        """
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
    # Shutdown — all-notes-off panic, then stop sender thread
    # ------------------------------------------------------------------

    def shutdown(self):
        print("Sending all-notes-off on all 12 channels...")
        for channel in range(12):
            try:
                self.port.send(mido.Message('control_change',
                    channel=channel, control=123, value=0))
            except Exception:
                pass
        self.midi_queue.put(None)      # sentinel — tells sender thread to exit
        self.midi_thread.join(timeout=2.0)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    listener = None
    try:
        with mido.open_output(MIDI_PORT_NAME) as port:
            print(f"MIDI port : {port.name}")
            print(f"Debug     : {'ON' if ENABLE_DEBUG_PRINTS else 'OFF'}")
            print()
            print("  Player 1 (1st device) : Ch 1 left / Ch 2 right      [drum]")
            print("  Player 2 (2nd device) : Ch 3 left / Ch 4 right      [drum]")
            print("  Player 3 (3rd device) : Ch 6 left drum / Ch 8 right synth")
            print(f"    Synth scale : C-minor blues, C4–C6, {len(SYNTH_NOTES)} notes")
            print(f"    Notes       : {SYNTH_NOTES}")
            print("    Pitch map   : |z| 0 mm → C6 (high)  |  200 mm → C4 (low)")
            print("  Player 4 (4th device) : Ch 11 left / Ch 12 right    [drum]")
            print()
            print("Waiting for Ultraleap devices...\n")

            device_stack = contextlib.ExitStack()
            connection   = leap.Connection(multi_device_aware=True)
            listener     = FourModuleListener(port, device_stack, connection)
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
