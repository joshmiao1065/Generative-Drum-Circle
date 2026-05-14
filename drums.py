import contextlib
import leap
import time
import mido
import threading
import queue

# --- Configuration ---
MIDI_PORT_NAME      = 'USB MIDI Interface 1'  # ← exact string from mido.get_output_names()
ENABLE_DEBUG_PRINTS = False                   # ← turn off for performance

# Zone / play area (mm)
X_RANGE = [-200, 200]
Z_RANGE = [-200, 200]
Y_MIN   = 100

# Strike detection (drum channels only)
DOWNWARD_STRIKE_THRESHOLD = 45.0
OPEN_HAND_THRESHOLD       = 0.15
RETRIGGER_COOLDOWN        = 0.1304
MAX_VELOCITY              = 127

# Per-player Syntakt track/channel mapping.
#
# 'tonal': False  →  drum/impulse mode: fires a 50ms note_on/note_off on downward strike
# 'tonal': True   →  sustain mode: holds note_on while hand is open in zone,
#                    releases note_off when hand closes, leaves zone, or disappears
#
# mido channels are 0-indexed (channel 0 = Syntakt MIDI Ch 1)
PLAYER_TRACK_CONFIG = {
    1: [
        {'channel': 0,  'note': 60, 'name': 'Kick',   'tonal': False},
        {'channel': 1,  'note': 60, 'name': 'Snare',  'tonal': False},
        {'channel': 2,  'note': 60, 'name': 'Hat',    'tonal': False},
        {'channel': 3,  'note': 60, 'name': 'Clap',   'tonal': False},
    ],
    2: [
        {'channel': 4,  'note': 60, 'name': 'Perc1',  'tonal': True},
        {'channel': 5,  'note': 60, 'name': 'Perc2',  'tonal': True},
        {'channel': 6,  'note': 60, 'name': 'Tom',    'tonal': True},
        {'channel': 7,  'note': 60, 'name': 'Rim',    'tonal': True},
    ],
    3: [
        {'channel': 8,  'note': 60, 'name': 'Cymbal', 'tonal': False},
        {'channel': 9,  'note': 60, 'name': 'Bell',   'tonal': False},
        {'channel': 10, 'note': 60, 'name': 'Bass',   'tonal': True },  # ← sustain
        {'channel': 11, 'note': 60, 'name': 'Lead',   'tonal': True },  # ← sustain
    ],
}


# ---------------------------------------------------------------------------
# Hand State
# ---------------------------------------------------------------------------

class HandState:
    """
    Per-hand tracking state. Dict key is (device_id, hand.id).

    is_sustaining: True when a tonal note_on is currently held open on the Syntakt.
    A matching note_off must be sent before this hand is cleaned up or deactivated.
    """
    __slots__ = [
        'hand_id', 'is_active', 'current_quadrant',
        'last_trigger_height', 'last_trigger_time', 'is_sustaining',
    ]

    def __init__(self, hand_id):
        self.hand_id             = hand_id
        self.is_active           = False
        self.current_quadrant    = None
        self.last_trigger_height = 0.0
        self.last_trigger_time   = 0.0
        self.is_sustaining       = False

    def activate(self, quadrant, height):
        self.is_active           = True
        self.current_quadrant    = quadrant
        self.last_trigger_height = height
        self.last_trigger_time   = time.time()

    def deactivate(self):
        self.is_active        = False
        self.current_quadrant = None
        self.is_sustaining    = False


# ---------------------------------------------------------------------------
# Listener
# ---------------------------------------------------------------------------

class DrumCircleListener(leap.Listener):
    """
    Handles up to 3 Ultraleap modules simultaneously.

    MIDI queue items are tuples: (mido.Message, auto_note_off: bool)
      auto_note_off=True  → sender waits 50ms then sends matching note_off (drums)
      auto_note_off=False → sender passes message through as-is (tonal note_on/note_off)
    """

    def __init__(self, port, device_stack, connection):
        super().__init__()
        self.port          = port
        self._device_stack = device_stack
        self._connection   = connection

        self._device_map: dict[int, int] = {}
        self._device_map_lock = threading.Lock()

        self.hand_states: dict[tuple[int, int], HandState] = {}

        self.midi_queue  = queue.Queue(maxsize=200)
        self.midi_thread = threading.Thread(target=self._midi_sender, daemon=True)
        self.midi_thread.start()

        if ENABLE_DEBUG_PRINTS:
            print("DrumCircleListener ready — waiting for devices...")

    # ------------------------------------------------------------------
    # Device lifecycle
    # ------------------------------------------------------------------

    def on_device_event(self, event):
        device    = event.device
        device_id = device.id
        print(f"[Device] id={device_id} detected — opening & subscribing...")
        self._device_stack.enter_context(device.open())
        self._connection.subscribe_events(device)
        print(f"[Device] id={device_id} subscribed ✓")

    # ------------------------------------------------------------------
    # Tracking
    # ------------------------------------------------------------------

    def on_tracking_event(self, event):
        device_id = event.metadata.device_id
        player    = self._get_player(device_id)
        if player is None:
            return

        quad_cfg     = PLAYER_TRACK_CONFIG[player]
        current_keys = {(device_id, h.id) for h in event.hands}

        for hand in event.hands:
            pos      = hand.palm.position
            key      = (device_id, hand.id)
            in_zone  = self._is_in_play_zone(pos)
            is_open  = hand.grab_strength < OPEN_HAND_THRESHOLD
            quadrant = self._get_quadrant(pos) if in_zone else None

            if key not in self.hand_states:
                self.hand_states[key] = HandState(hand.id)
            state = self.hand_states[key]

            if not state.is_active:
                if in_zone and is_open and quadrant:
                    state.activate(quadrant, pos.y)
                    self._on_zone_entry(quad_cfg, quadrant, key, state)

            elif in_zone and is_open:
                if quadrant and quadrant != state.current_quadrant:
                    # Crossed into a new zone — release old tonal note first
                    if state.is_sustaining:
                        self._release_tonal(quad_cfg, state.current_quadrant, key, state)
                    state.current_quadrant = quadrant
                    self._on_zone_entry(quad_cfg, quadrant, key, state)
                elif quadrant:
                    cfg = quad_cfg[quadrant - 1]
                    if not cfg['tonal']:
                        self._check_strike(state, pos.y, quad_cfg, quadrant, key)

            else:
                # Hand closed or left zone
                if state.is_sustaining:
                    self._release_tonal(quad_cfg, state.current_quadrant, key, state)
                state.deactivate()

        # Stale hand cleanup — release any held tonal notes for hands that vanished
        stale = {k for k in self.hand_states if k[0] == device_id} - current_keys
        for k in stale:
            s = self.hand_states[k]
            if s.is_sustaining:
                self._release_tonal(quad_cfg, s.current_quadrant, k, s)
            del self.hand_states[k]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_player(self, device_id: int) -> int | None:
        """Assign player numbers in first-seen order. Thread-safe."""
        with self._device_map_lock:
            if device_id not in self._device_map:
                n = len(self._device_map) + 1
                if n > 3:
                    return None
                self._device_map[device_id] = n
                print(f"[Assign] Device {device_id} → Player {n}")
            return self._device_map[device_id]

    def _is_in_play_zone(self, pos) -> bool:
        return (X_RANGE[0] <= pos.x <= X_RANGE[1] and
                Z_RANGE[0] <= pos.z <= Z_RANGE[1] and
                pos.y >= Y_MIN)

    def _get_quadrant(self, pos) -> int | None:
        if not self._is_in_play_zone(pos):
            return None
        if pos.x >= 0 and pos.z >= 0: return 1
        if pos.x <  0 and pos.z >= 0: return 2
        if pos.x <  0 and pos.z <  0: return 3
        return 4

    # ------------------------------------------------------------------
    # Zone entry — dispatches to tonal or drum behaviour
    # ------------------------------------------------------------------

    def _on_zone_entry(self, quad_cfg, quadrant, key, state):
        cfg = quad_cfg[quadrant - 1]
        if cfg['tonal']:
            self._queue((
                mido.Message('note_on', note=cfg['note'],
                             velocity=MAX_VELOCITY, channel=cfg['channel']),
                False  # do NOT auto-close — sustain until explicit release
            ))
            state.is_sustaining     = True
            state.last_trigger_time = time.time()
            if ENABLE_DEBUG_PRINTS:
                print(f"  SUSTAIN ON  {key} → {cfg['name']} ch={cfg['channel']+1}")
        else:
            self._fire_drum(cfg, key, state)

    def _release_tonal(self, quad_cfg, quadrant, key, state):
        """Send note_off for a currently sustained tonal channel."""
        if quadrant is None:
            return
        cfg = quad_cfg[quadrant - 1]
        if not cfg['tonal']:
            return
        self._queue((
            mido.Message('note_off', note=cfg['note'],
                         velocity=0, channel=cfg['channel']),
            False
        ))
        state.is_sustaining = False
        if ENABLE_DEBUG_PRINTS:
            print(f"  SUSTAIN OFF {key} → {cfg['name']} ch={cfg['channel']+1}")

    # ------------------------------------------------------------------
    # Drum strike detection
    # ------------------------------------------------------------------

    def _check_strike(self, state, current_height, quad_cfg, quadrant, key):
        if current_height > state.last_trigger_height:
            state.last_trigger_height = current_height
            return
        if (state.last_trigger_height - current_height > DOWNWARD_STRIKE_THRESHOLD and
                time.time() - state.last_trigger_time >= RETRIGGER_COOLDOWN):
            self._fire_drum(quad_cfg[quadrant - 1], key, state)
            state.last_trigger_height = current_height

    def _fire_drum(self, cfg, key, state):
        self._queue((
            mido.Message('note_on', note=cfg['note'],
                         velocity=MAX_VELOCITY, channel=cfg['channel']),
            True  # auto-close after 50ms
        ))
        state.last_trigger_time = time.time()
        if ENABLE_DEBUG_PRINTS:
            print(f"  HIT  {key} → {cfg['name']} ch={cfg['channel']+1}")

    # ------------------------------------------------------------------
    # Queue helper
    # ------------------------------------------------------------------

    def _queue(self, item):
        try:
            self.midi_queue.put_nowait(item)
        except queue.Full:
            if ENABLE_DEBUG_PRINTS:
                print("MIDI queue full — dropping message")

    # ------------------------------------------------------------------
    # MIDI sender thread
    # ------------------------------------------------------------------

    def _midi_sender(self):
        """
        Each queue item is (mido.Message, auto_note_off: bool).
        auto_note_off=True  → send note_on, sleep 50ms, send note_off  (drums)
        auto_note_off=False → send message as-is                        (tonal)
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
    # Shutdown
    # ------------------------------------------------------------------

    def shutdown(self):
        print("Sending all-notes-off...")
        for channel in range(12):
            try:
                self.port.send(mido.Message('control_change',
                    channel=channel, control=123, value=0))
            except Exception:
                pass
        self.midi_queue.put(None)
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
            print("Waiting for Ultraleap devices...\n")

            device_stack = contextlib.ExitStack()
            connection   = leap.Connection(multi_device_aware=True)
            listener     = DrumCircleListener(port, device_stack, connection)
            connection.add_listener(listener)

            with device_stack:
                with connection.open():
                    connection.set_tracking_mode(leap.TrackingMode.Desktop)
                    print("Ready — Ctrl+C to exit\n")
                    while True:
                        time.sleep(1)

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