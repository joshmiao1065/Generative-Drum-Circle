"""
Ultraleap Multi-Device Diagnostic v6
======================================
Tracks per-device tracking rate, reconnections, and USB stability.

What to look for:
  - "DUPLICATE event" lines → on_device_event firing twice for same ID (SDK quirk or USB churn)
  - "[Device -] LOST" lines → device dropped off USB
  - Hz column: should be ~120; <60 = USB bandwidth problem; 0 = dead connection
  - "STALE" status → device stopped sending events but was never reported lost
  - open_count > 1 → device has reconnected at least once
  - Devices with no tracking events but listed as connected → USB 2.0 / charge-only cable

Wave a hand over each module individually so you can match device IDs to physical units.
Press Ctrl+C to stop (or it will stop after RUN_DURATION seconds).
"""

import time
import contextlib
import threading
import collections
import leap

# ── Config ──────────────────────────────────────────────────────────────────
REPORT_INTERVAL = 4.0   # seconds between per-device stats printout
RUN_DURATION    = 0     # 0 = run until Ctrl+C; set to seconds for auto-stop

# ── Shared state ─────────────────────────────────────────────────────────────
connection   = leap.Connection(multi_device_aware=True)
device_stack = contextlib.ExitStack()

_lock           = threading.Lock()
_known_devices  = {}                          # device_id → {serial, open_count, lost_count, first_seen_t}
_event_counts   = collections.defaultdict(int)  # device_id → total tracking frames received
_hand_counts    = collections.defaultdict(int)  # device_id → frames that contained at least one hand
_last_event_t   = {}                          # device_id → time.time() of last tracking event received
_opened_ids     = set()                       # IDs currently open (cleared on lost event)
_event_log      = []                          # chronological list of connect/disconnect events


def _ts():
    return time.strftime("%H:%M:%S")


def _log(msg):
    print(f"[{_ts()}] {msg}")


# ── Listener ─────────────────────────────────────────────────────────────────
class DiagListener(leap.Listener):

    def on_device_event(self, event):
        device    = event.device
        device_id = device.id

        with _lock:
            is_dup = device_id in _opened_ids
            if device_id not in _known_devices:
                _known_devices[device_id] = {
                    'serial':     None,
                    'open_count': 0,
                    'lost_count': 0,
                    'first_seen': time.time(),
                }
            _known_devices[device_id]['open_count'] += 1
            if not is_dup:
                _opened_ids.add(device_id)

        if is_dup:
            _log(f"[Device DUPLICATE] id={device_id} — on_device_event fired again "
                 f"(open_count={_known_devices[device_id]['open_count']}). "
                 f"Skipping re-open to avoid double context.")
            _event_log.append({'t': time.time(), 'event': 'duplicate', 'id': device_id})
            return

        _log(f"[Device +] id={device_id} connecting...")
        _event_log.append({'t': time.time(), 'event': 'connect', 'id': device_id})

        try:
            device_stack.enter_context(device.open())
        except Exception as e:
            _log(f"  ERROR opening device {device_id}: {e}")
            return

        # Read serial number — requires the device to be open first
        serial = None
        try:
            info   = device.get_info()
            serial = (getattr(info, 'serial_number', None)
                      or getattr(info, 'serial', None)
                      or str(getattr(info, 'id', None)))
            with _lock:
                _known_devices[device_id]['serial'] = serial
            _log(f"  Serial: {serial}")
        except Exception as e:
            _log(f"  get_info() failed (serial unknown): {e}")

        try:
            connection.subscribe_events(device)
            _log(f"  Subscribed — tracking events will appear below")
        except Exception as e:
            _log(f"  ERROR subscribing to device {device_id}: {e}")

    def on_device_lost_event(self, event):
        device_id = getattr(event.device, 'id', '?')
        with _lock:
            if device_id in _known_devices:
                _known_devices[device_id]['lost_count'] += 1
                lc = _known_devices[device_id]['lost_count']
            else:
                lc = 1
            _opened_ids.discard(device_id)
        _log(f"[Device -] id={device_id} LOST (total lost_count={lc}) ← USB dropout?")
        _event_log.append({'t': time.time(), 'event': 'disconnect', 'id': device_id})

    def on_tracking_event(self, event):
        device_id = event.metadata.device_id
        now = time.time()
        with _lock:
            _event_counts[device_id] += 1
            if event.hands:
                _hand_counts[device_id] += 1
            _last_event_t[device_id] = now


# ── Reporter thread ──────────────────────────────────────────────────────────
def _reporter():
    """Print per-device stats every REPORT_INTERVAL seconds."""
    prev_counts = collections.defaultdict(int)
    prev_t      = time.time()

    while True:
        time.sleep(REPORT_INTERVAL)
        now     = time.time()
        elapsed = now - prev_t
        prev_t  = now

        with _lock:
            ids      = sorted(_event_counts.keys())
            counts   = dict(_event_counts)
            hand_c   = dict(_hand_counts)
            last_t   = dict(_last_event_t)
            known    = {k: dict(v) for k, v in _known_devices.items()}
            open_set = set(_opened_ids)

        if not ids and not known:
            print(f"\n  [{_ts()}] No tracking events yet — are the modules on and the Gemini service running?\n")
            prev_counts = dict(counts)
            continue

        # Also show devices that are known but not yet sending events
        all_ids = sorted(set(ids) | set(known.keys()))

        print(f"\n{'━'*72}")
        print(f"  Per-device stats  ({elapsed:.1f}s window)  —  {_ts()}")
        print(f"{'━'*72}")
        print(f"  {'ID':>4}  {'Hz':>6}  {'frames':>8}  {'w/hands':>8}  {'serial':<24}  status")
        print(f"  {'--':>4}  {'--':>6}  {'------':>8}  {'-------':>8}  {'------':<24}  ------")

        for did in all_ids:
            delta  = counts.get(did, 0) - prev_counts.get(did, 0)
            hz     = delta / elapsed
            serial = str(known.get(did, {}).get('serial', '?'))[:24]
            age    = now - last_t.get(did, now)
            oc     = known.get(did, {}).get('open_count', 0)
            lc     = known.get(did, {}).get('lost_count', 0)

            if did not in ids:
                status = "NO EVENTS"
            elif age < 2.0:
                status = "ACTIVE"
            else:
                status = f"STALE {age:.0f}s"

            if did not in open_set:
                status += " [DISCONNECTED]"

            flags = []
            if oc > 1:  flags.append(f"reconnected×{oc}")
            if lc > 0:  flags.append(f"lost×{lc}")
            flag_s = "  !" + " ".join(flags) if flags else ""

            hz_warn = "  ← LOW" if 0 < hz < 60 else ""
            print(f"  {did:>4}  {hz:>6.1f}  {counts.get(did,0):>8}  "
                  f"{hand_c.get(did,0):>8}  {serial:<24}  {status}{flag_s}{hz_warn}")

        prev_counts = dict(counts)

        n_active = sum(1 for did in all_ids
                       if did in ids and (now - last_t.get(did, now)) < 2.0)
        n_conn   = len(open_set)
        total_reconnects = sum(max(d.get('open_count',1)-1, 0) for d in known.values())
        total_lost       = sum(d.get('lost_count',0) for d in known.values())

        print(f"\n  Devices connected now: {n_conn}   |   "
              f"Active (events <2s): {n_active}   |   "
              f"Total reconnects: {total_reconnects}   |   "
              f"Total lost events: {total_lost}")
        print(f"{'━'*72}\n")


# ── Main ─────────────────────────────────────────────────────────────────────
listener = DiagListener()
connection.add_listener(listener)

reporter_thread = threading.Thread(target=_reporter, daemon=True)
reporter_thread.start()

print("=" * 72)
print("  Ultraleap Multi-Device Diagnostic v6")
print("  Wave a hand over each module one at a time to map IDs to positions.")
print("  Stats print every", REPORT_INTERVAL, "seconds.")
print("  Ctrl+C to stop.")
print("=" * 72 + "\n")

try:
    with device_stack:
        with connection.open():
            if RUN_DURATION > 0:
                time.sleep(RUN_DURATION)
            else:
                while True:
                    time.sleep(1)
except KeyboardInterrupt:
    pass

# ── Final summary ─────────────────────────────────────────────────────────────
print("\n\n" + "=" * 72)
print("  FINAL SUMMARY")
print("=" * 72)

with _lock:
    for did in sorted(_known_devices.keys()):
        info = _known_devices[did]
        evts = _event_counts.get(did, 0)
        hc   = _hand_counts.get(did, 0)
        print(f"  Device {did:>3}: serial={str(info['serial']):<30}  "
              f"open×{info['open_count']}  lost×{info['lost_count']}  "
              f"frames={evts:>8}  hand_frames={hc:>8}")

print()
print("  Connect/disconnect timeline:")
for entry in _event_log:
    t_str = time.strftime("%H:%M:%S", time.localtime(entry['t']))
    print(f"    {t_str}  {entry['event']:>12}  id={entry['id']}")

print("\nDone.")
