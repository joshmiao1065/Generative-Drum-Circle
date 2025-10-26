import time
import leap
import mido
import sys

# --- Configuration ---
MIDI_PORT_NAME = 'USB MIDI Interface 1'
CHORD_CHANNEL = 1
CHORD_TYPE_CC = 15
HORIZONTAL_ACTIVE_RADIUS = 100.0
VERTICAL_NOTE_RANGE = [75.0, 350.0]
GRAB_THRESHOLD = 0.9
MUTE_GRAB_THRESHOLD = 0.9
RETRIGGER_STRIKE_THRESHOLD = 15.0
STICKY_ZONE_HEIGHT = 20.0

# --- NEW: Cooldown period in seconds to prevent false re-triggers on appearance ---
INITIAL_NOTE_COOLDOWN = 0.2 # 200 milliseconds

# Define Chord Qualities
C_MAJ, C_MIN, C_DIM, C_MAJ7, C_MIN7, C_DOM7 = 0, 3, 6, 1, 4, 2

# 5-Chord Scales
SCALES = {
    "C_DIATONIC_HARMONY": [(48, C_MAJ), (50, C_MIN), (52, C_MIN), (53, C_MAJ), (55, C_DOM7)],
    "FUNKY_PROGRESSION_Cm": [(48, C_MIN7), (51, C_MAJ7), (53, C_DOM7), (56, C_MIN7), (55, C_DOM7)],
    "A_NATURAL_MINOR": [(45, C_MIN), (48, C_MAJ), (50, C_MIN), (52, C_MIN), (55, C_MAJ)]
}

class ResponsiveChordController(leap.Listener):
    def __init__(self, port):
        super().__init__()
        self.port = port
        self.active_chord_hand_id = None
        self.last_note_played = None
        self.last_quality_cc_sent = None
        self.scale_names = list(SCALES.keys())
        self.current_scale_index = 0
        self.right_hand_is_grabbing = False
        self.last_note_played_height = None
        self.strike_reference_height = None
        # --- NEW: State for the cooldown timer ---
        self.last_played_time = 0.0

    def get_current_scale(self):
        return SCALES[self.scale_names[self.current_scale_index]]

    def on_tracking_event(self, event):
        left_hand, right_hand = None, None
        for hand in event.hands:
            if hand.type == leap.HandType.Left: left_hand = hand
            elif hand.type == leap.HandType.Right: right_hand = hand

        if right_hand: # Scale switching
            if right_hand.grab_strength > GRAB_THRESHOLD and not self.right_hand_is_grabbing:
                self.right_hand_is_grabbing = True
                self.current_scale_index = (self.current_scale_index + 1) % len(self.scale_names)
                print(f"\n--- Scale Changed to: {self.scale_names[self.current_scale_index]} ---")
                self.stop_chord()
            elif right_hand.grab_strength < (GRAB_THRESHOLD - 0.2):
                self.right_hand_is_grabbing = False

        if left_hand:
            pos = left_hand.palm.position
            is_in_zone = (pos.x**2 + pos.z**2)**0.5 < HORIZONTAL_ACTIVE_RADIUS
            is_fist = left_hand.grab_strength > MUTE_GRAB_THRESHOLD

            if is_in_zone and not is_fist:
                # Check if this is a NEW hand appearing.
                if left_hand.id != self.active_chord_hand_id:
                    print(f"New Left Hand (ID: {left_hand.id}) in zone. Playing initial chord.")
                    note, quality = self.calculate_chord_for_height(pos.y)
                    self.play_chord(note, quality)
                    # Set the initial state for this new active hand
                    self.active_chord_hand_id = left_hand.id
                    self.strike_reference_height = pos.y
                    self.last_note_played_height = pos.y
                else: # It's the same hand, so run the re-trigger logic.
                    self.handle_retrigger(pos.y)
            else:
                if self.active_chord_hand_id is not None: self.stop_chord()
        else:
            if self.active_chord_hand_id is not None: self.stop_chord()

    def handle_retrigger(self, current_height):
        # --- NEW: Cooldown check ---
        # If a note was played very recently, do nothing. This prevents false triggers.
        if (time.time() - self.last_played_time) < INITIAL_NOTE_COOLDOWN:
            return

        # Upstroke logic
        if current_height > self.strike_reference_height:
            self.strike_reference_height = current_height
            return

        # Downstroke logic
        downward_distance = self.strike_reference_height - current_height
        if downward_distance > RETRIGGER_STRIKE_THRESHOLD:
            # A valid strike occurred. Decide which chord to play.
            if abs(current_height - self.last_note_played_height) <= STICKY_ZONE_HEIGHT:
                note, quality = self.last_note_played, self.last_quality_cc_sent
            else:
                note, quality = self.calculate_chord_for_height(current_height)
            
            self.play_chord(note, quality)
            self.strike_reference_height = current_height
            self.last_note_played_height = current_height

    def calculate_chord_for_height(self, current_height):
        scale = self.get_current_scale()
        clamped = max(VERTICAL_NOTE_RANGE[0], min(current_height, VERTICAL_NOTE_RANGE[1]))
        normalized = (clamped - VERTICAL_NOTE_RANGE[0]) / (VERTICAL_NOTE_RANGE[1] - VERTICAL_NOTE_RANGE[0])
        index = int(normalized * (len(scale) - 1))
        return scale[index]

    def play_chord(self, note, quality_cc):
        if note is None or quality_cc is None: return

        if self.last_note_played is not None and self.last_note_played != note:
             self.port.send(mido.Message('note_off', note=self.last_note_played, velocity=0, channel=CHORD_CHANNEL))
        
        if quality_cc != self.last_quality_cc_sent:
            self.port.send(mido.Message('control_change', channel=CHORD_CHANNEL, control=CHORD_TYPE_CC, value=quality_cc))
        
        self.port.send(mido.Message('note_on', note=note, velocity=100, channel=CHORD_CHANNEL))
        
        # --- NEW: Update the cooldown timer every time a note is played ---
        self.last_played_time = time.time()
        
        self.last_note_played = note
        self.last_quality_cc_sent = quality_cc

    def stop_chord(self):
        if self.last_note_played is not None:
            self.port.send(mido.Message('note_off', note=self.last_note_played, velocity=0, channel=CHORD_CHANNEL))
        
        self.active_chord_hand_id = None
        self.last_note_played = None
        self.last_quality_cc_sent = None
        self.last_note_played_height = None
        self.strike_reference_height = None
        self.last_played_time = 0.0 # Reset cooldown timer
        print("Chord Stopped.                      ", end='\r')

def main():
    # Main loop is stable and correct.
    try:
        with mido.open_output(MIDI_PORT_NAME) as port:
            print(f"Opened MIDI port: {port.name}")
            listener = ResponsiveChordController(port)
            connection = leap.Connection()
            connection.add_listener(listener)
            
            with connection.open():
                connection.set_tracking_mode(leap.TrackingMode.Desktop)
                print(f"Strike: {RETRIGGER_STRIKE_THRESHOLD}mm, Sticky: {STICKY_ZONE_HEIGHT}mm, Cooldown: {INITIAL_NOTE_COOLDOWN*1000}ms")
                print("Connection open...")
                while True: time.sleep(1)
    except KeyboardInterrupt:
        print("\nExiting gracefully.")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    main()