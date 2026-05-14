# Gesture-Controlled Drum Circle

A live performance system where multiple performers use bare-hand gestures to trigger and shape sounds on an Elektron Syntakt drum machine via MIDI. Up to 4 Ultraleap Motion Controller 2 hand-tracking modules enable hands-free, collaborative performance.

## Quick Start

### 1. Install Dependencies

**Windows (PowerShell):**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

The Leap bindings (`leapc-cffi` and `leapc-python-api`) are included in `requirements.txt`. If the precompiled binary fails to load, see **Troubleshooting** below.

### 2. Verify Installation

```powershell
python check_install.py
```

This confirms mido, rtmidi, and leap are working. You should see ✓ marks for all three if everything is installed correctly.

### 3. Check MIDI Connection

```powershell
python midi_check.py
```

Find the Elektron Syntakt in the list and note its exact name (e.g., `'Syntakt  Port 1 0'`). Copy this into the `MIDI_PORT_NAME` variable in your script (see **Configuration** below).

### 4. Configure the Syntakt

Before running any script, set up your Syntakt:

- `SETTINGS > MIDI > MIDI CHANNELS`: Set Track N = Channel N (e.g., Track 1 = Ch 1, Track 2 = Ch 2, etc.) for all 12 tracks
- `SETTINGS > MIDI > MIDI IN PORT`: Set to `USB`
- `SETTINGS > MIDI > RECEIVE NOTES`: Turn `ON` for all tracks
- `SETTINGS > MIDI > TRANSPORT`: Optional — disable if you don't want external MIDI start/stop

### 5. Run the Main Script

```powershell
python three.py
```

When the script starts, it will prompt you to configure the melodic channel (Ch 8 left zone):
- Choose a musical key (root note, e.g., C, F#, etc.)
- Choose a scale (e.g., major, minor, pentatonic, blues, etc.)

Then perform by moving your hands over the modules. See **Gesture Mapping** below.

## Hardware Setup

### Computer Requirements
- Windows 10/11
- USB Thunderbolt 4 port (required for Leap modules — standard USB-C ports are too slow)
- Python 3.12.2

### Devices

| Device | Connection | Requirements |
|--------|-----------|--------------|
| **Ultraleap Motion Controller 2** (×3 or ×4) | USB-C to Thunderbolt 4 hub | USB 3.0 for 120Hz tracking; USB 2.0 delivers ~3–8Hz (unreliable for strikes) |
| **Elektron Syntakt** | USB-A or USB-C direct to PC | Class-compliant MIDI over USB; separate from Leap hub |

### USB Hub (For 2+ Modules)
- **Minimum: USB 3.0 hub** connected to Thunderbolt 4 port
- **For 4 modules: Powered USB hub** with external AC adapter (≥2.5A @ 5V)
  - Bus-powered hubs cannot supply 4 × 500mA + hub overhead — devices will drop with 0 Hz
  - A powered hub on a USB 2.0 port still throttles to 3–8 Hz; move to USB 3.0 connection

### USB Cable Gotcha
- **All Leap cables must be data-transfer capable**, not charge-only
- Confirmed working: Anker USB-C to USB-C Model A8758

### Ultraleap Gemini Service
The Ultraleap Tracking Service must be **running before launching any script**:
- Windows: Check the system tray for the Ultraleap Control Panel icon
- Gemini must be actively tracking (LED on modules should be solid green)
- Verify devices appear in the Control Panel's device list

## Scripts

### `three.py` — Main Performance Script (Admitted Students' Day 2026)

**Configuration:** 3 modules with mixed percussion and melodic control
- **Module 1:** Channels 1 (left) and 3 (right) — both percussion
- **Module 2:** Channels 2 (left) and 4 (right) — both percussion  
- **Module 3:** Channel 8 (left) melodic + Channel 12 (right) percussion

**Melodic Channel (Ch 8, Module 3 left zone):**
- Hand height (Y position) maps to scale degrees — higher hand = higher note
- Open hand sustains the note; close your fist to release
- Sustain is maintained as long as the hand remains in the zone and open

**Percussion Channels (1, 2, 3, 4, 12):**
- Downward strike (rapid drop) triggers a drum hit
- Four quadrants per module map to different drum sounds on the Syntakt
- Retrigger cooldown (~120ms) prevents double-hits from a single gesture

**At startup:** Choose a root note and scale for melodic control. Options include major, minor, blues, pentatonic, whole tone, Japanese scales, and more.

### `diagnose.py` — Multi-Device Diagnostics

Measures per-device tracking frame rate (Hz), reconnection events, and serial numbers. Run this **first** when debugging connection issues:

```powershell
python diagnose.py
```

**Expected output:**
```
Device 1: 89-90 Hz ✓
Device 2: 89-90 Hz ✓
Device 3: 89-90 Hz ✓
```

**Red flags:**
- **0 Hz + frequent `lost` events** → Power starvation (bus-powered hub). Switch to a powered hub.
- **3–8 Hz (stable, no drops)** → Bandwidth throttling (USB 2.0). Move module to Thunderbolt 4 hub.
- **< 50 frames total** → Device not fully initialized. Check physical connections and Gemini service.

### `check_install.py` — Dependency Checker

Verifies that mido, rtmidi, and leap are importable and working:

```powershell
python check_install.py
```

Also lists available MIDI output ports. If Syntakt doesn't appear, power-cycle it or reconnect the USB cable.

### `midi_check.py` — MIDI Port Lister

Quickly lists all MIDI ports detected on the system:

```powershell
python midi_check.py
```

## Configuration

### Setting the MIDI Port Name

Each script has a `MIDI_PORT_NAME` variable at the top. Find the exact string:

```powershell
python midi_check.py
```

Copy the Syntakt's port name (e.g., `'Syntakt  Port 1 0'`) **exactly** into your script:

```python
MIDI_PORT_NAME = 'Syntakt  Port 1 0'
```

### Play Zone and Strike Detection

All positions are in millimeters relative to each module's center:

```python
X_RANGE = [-400, 400]   # left–right band (800mm wide)
Z_RANGE = [-400, 400]   # depth band (800mm deep)
Y_MIN   = 50            # minimum height (hands on desk ignored)
Y_MAX   = 500           # upper bound for melodic height mapping
```

**Strike threshold:** A downward motion of 50mm triggers a drum hit.

```python
DOWNWARD_STRIKE_THRESHOLD = 50.0   # mm drop from peak
OPEN_HAND_THRESHOLD       = 0.20   # grab strength < 0.20 = open hand
```

### Enabling Debug Output

Set `ENABLE_DEBUG_PRINTS = True` in any script to print per-hit details to the console. Useful for tuning but adds latency — disable for performance.

## Gesture Mapping

### Quadrant Zones (Percussion)

Each module's play area is divided into 4 quadrants:

```
 +Z
  |   Q2  |  Q1
 ─┼───────|────── +X
  |   Q3  |  Q4
 -Z
```

Each quadrant can be mapped to a different Syntakt track/drum sound.

### Melodic Zone (Ch 8, Module 3 left)

- **Zone:** Left half of Module 3 sensor (0 < X < 400mm)
- **Height mapping:** 50–500mm Y → scale degrees (low to high)
- **Sustain:** Open hand holds note; closed fist releases
- **Release:** Fist close, zone exit, or hand disappearing all release the note

### Percussion Zones (Ch 1, 2, 3, 4, 12)

- **Trigger:** Downward hand motion > 50mm within the play zone
- **Cooldown:** ~120ms between re-triggers on the same zone (prevents fluttering)
- **Velocity:** Proportional to strike speed (max 127)

## Troubleshooting

### "ModuleNotFoundError: No module named 'leapc_cffi._leapc_cffi'"

The precompiled binary in the Leap SDK targets Python 3.8 only. You must build from source:

```powershell
pip install build cffi
git clone https://github.com/ultraleap/leapc-python-bindings
cd leapc-python-bindings
python -m build leapc-cffi
pip install leapc-cffi/dist/leapc_cffi-0.0.1.tar.gz
pip install -e leapc-python-api
```

If building fails (missing MSVC), install **Microsoft C++ Build Tools** and retry.

### No Devices Detected

1. **Check Gemini:** Is the Ultraleap Control Panel tray icon visible and green?
2. **Check USB connections:** All modules connected via Thunderbolt 4 port (not standard USB-C)?
3. **Run `diagnose.py`:** See if devices enumerate at all.
4. **Restart Gemini:** Right-click tray icon → Restart Service, or reboot.

### Only 1–2 Devices Show Up (3+ Connected)

- **Bus-powered hub with 4 modules?** Devices drop immediately due to power starvation. Switch to a powered hub.
- **Running on standard USB-C port?** Throttles to USB 2.0 (~3–8 Hz). Move to Thunderbolt 4 hub.

### Syntakt Responds But Sequencer Goes Silent After a Few Minutes

This indicates a stuck note. The Syntakt holds an open MIDI note_on with no matching note_off, permanently muting that channel's sequencer. Recovery:

- **Immediate:** Reload the song on the Syntakt
- **Or:** Send MIDI CC 123 (all notes off) — script shutdown should do this automatically
- **Or:** Power-cycle the Syntakt

**To prevent:** Ensure the script was not killed with Ctrl+Z (which doesn't trigger cleanup). Always exit cleanly with Ctrl+C.

### Low Tracking Frame Rate (< 50 Hz)

Run `diagnose.py` and check the Hz column:

- **0 Hz:** Power starvation or complete device failure — check cables and hub power
- **3–8 Hz:** USB 2.0 bandwidth limit — move to USB 3.0 (Thunderbolt 4 hub)
- **30–80 Hz:** Congestion or CPU load — close other USB-heavy apps, or reduce the number of active modules

### MIDI Messages Not Reaching Syntakt

- **Syntakt powered on?** Check LED and USB cable.
- **Port name correct?** Run `midi_check.py` and verify you're using the exact string.
- **Syntakt MIDI IN set to USB?** Check `SETTINGS > MIDI > MIDI IN PORT`.
- **Channels mapped correctly?** Verify `SETTINGS > MIDI > MIDI CHANNELS` are 1:1.

## Performance Tips

1. **Use a powered hub** for 3+ modules. A bus-powered hub cannot sustain 4 modules on Syntakt.
2. **Thunderbolt 4 only.** Standard USB-C ports are too slow.
3. **Keep Gemini running** — don't minimize or close the Control Panel.
4. **Close other USB devices** when testing — bandwidth is shared.
5. **Disable debug prints** (`ENABLE_DEBUG_PRINTS = False`) unless actively tuning.
6. **Test with `diagnose.py`** before assuming a performance issue is in your script.

## Additional Resources

See `CLAUDE.md` for:
- Complete hardware pinout and USB port diagnostics
- The multi-device API pattern (contextlib.ExitStack, subscribe_events, etc.)
- Scale and chord system details
- Known issues and their fixes
- Threading model and MIDI queue design
- Full Syntakt MIDI configuration guide

## Author Notes
All scripts tested with Windows 11, Python 3.12, Elektron Syntakt, and Ultraleap Motion Controller 2 (Gemini 5.17+).