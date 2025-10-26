import leap
import time
import mido
import sys

# --- Configuration ---
MIDI_PORT_NAME = 'USB MIDI Interface 1'
LEAD_CHANNEL = 6 # MIDI Channel 7

DETUNE_CC = 18 
DETUNE_CENTER_VALUE = 64
# Active zone and gestures
HORIZONTAL_ACTIVE_RADIUS = 100.0 
MUTE_GRAB_THRESHOLD = 0.9

# --- Left Hand Pitch Configuration (5 notes) ---
VERTICAL_NOTE_RANGE = [75.0, 350.0] 
PITCH_SCALE = [
   45, # E3
    55, # G3
  #  57, # A3 (Middle Point)
 #   60, # C4
   # 62  # D4
]

# --- Right Hand Detune Configuration ---
# How far (in mm) you need to move UP or DOWN to go from center to max/min detune.
DETUNE_MOTION_RANGE_Y = 100.0 

class VerticalDetuneController(leap.Listener):
    def __init__(self, port):
        super().__init__()
        self.port = port
        self.left_hand_active = False
        self.last_note_played = None
        self.right_hand_active = False
        # --- UPDATED: We now track the Y-axis (height) ---
        self.right_hand_start_pos_y = None
        self.last_detune_value_sent = None

    def on_tracking_event(self, event):
        left_hand, right_hand = None, None
        for hand in event.hands:
            if hand.type == leap.HandType.Left: left_hand = hand
            elif hand.type == leap.HandType.Right: right_hand = hand

        # --- Left Hand Logic (Pitch) - Unchanged ---
        if left_hand:
            pos = left_hand.palm.position
            is_in_zone = (pos.x**2 + pos.z**2)**0.5 < HORIZONTAL_ACTIVE_RADIUS
            is_fist = left_hand.grab_strength > MUTE_GRAB_THRESHOLD

            if is_in_zone and not is_fist:
                note_to_play = self.calculate_note_for_height(pos.y)
                if not self.left_hand_active:
                    self.port.send(mido.Message('note_on', note=note_to_play, velocity=100, channel=LEAD_CHANNEL))
                    self.left_hand_active = True
                    self.last_note_played = note_to_play
                elif self.last_note_played != note_to_play:
                    self.port.send(mido.Message('note_off', note=self.last_note_played, velocity=0, channel=LEAD_CHANNEL))
                    self.port.send(mido.Message('note_on', note=note_to_play, velocity=100, channel=LEAD_CHANNEL))
                    self.last_note_played = note_to_play
            else:
                if self.left_hand_active:
                    self.port.send(mido.Message('note_off', note=self.last_note_played, velocity=0, channel=LEAD_CHANNEL))
                    self.left_hand_active = False
                    self.last_note_played = None
        else:
            if self.left_hand_active:
                self.port.send(mido.Message('note_off', note=self.last_note_played, velocity=0, channel=LEAD_CHANNEL))
                self.left_hand_active = False
                self.last_note_played = None

        # --- Right Hand Logic (Vertical Relative Detune) ---
        if right_hand:
            if not self.right_hand_active:
                print("Right Hand Active. Recording start height and resetting detune.")
                # --- UPDATED: Record the initial Y position ---
                self.right_hand_start_pos_y = right_hand.palm.position.y
                self.right_hand_active = True
                self.send_detune_cc(DETUNE_CENTER_VALUE)
            else:
                # --- UPDATED: Use Y position for calculation ---
                current_pos_y = right_hand.palm.position.y
                delta_y = current_pos_y - self.right_hand_start_pos_y
                
                # Moving UP (positive delta_y) increases value, DOWN decreases it.
                detune_offset = (delta_y / DETUNE_MOTION_RANGE_Y) * 63
                detune_value = int(DETUNE_CENTER_VALUE + detune_offset)
                detune_value = max(0, min(127, detune_value))
                self.send_detune_cc(detune_value)
        else:
            if self.right_hand_active:
                print("Right Hand Lost. Resetting detune to center.")
                self.send_detune_cc(DETUNE_CENTER_VALUE)
                self.right_hand_active = False
                # --- UPDATED: Reset the Y position ---
                self.right_hand_start_pos_y = None

    def calculate_note_for_height(self, height):
        clamped = max(VERTICAL_NOTE_RANGE[0], min(height, VERTICAL_NOTE_RANGE[1]))
        normalized = (clamped - VERTICAL_NOTE_RANGE[0]) / (VERTICAL_NOTE_RANGE[1] - VERTICAL_NOTE_RANGE[0])
        index = int(normalized * (len(PITCH_SCALE) - 1))
        return PITCH_SCALE[index]

    def send_detune_cc(self, value):
        if value != self.last_detune_value_sent:
            self.port.send(mido.Message('control_change', channel=LEAD_CHANNEL, control=DETUNE_CC, value=value))
            self.last_detune_value_sent = value
            print(f"Detune: {value}", end='\r')

def main():
    try:
        with mido.open_output(MIDI_PORT_NAME) as port:
            print(f"Opened MIDI port: {port.name}")
            listener = VerticalDetuneController(port)
            connection = leap.Connection()
            connection.add_listener(listener)
            
            with connection.open():
                connection.set_tracking_mode(leap.TrackingMode.Desktop)
                print(f"Detune CC is {DETUNE_CC} on Channel {LEAD_CHANNEL + 1}. Control is now on the RIGHT HAND'S VERTICAL axis.")
                print("Connection open. Left hand for pitch, Right hand for relative detune.")
                while True: time.sleep(1)
    except KeyboardInterrupt:
        print("\nExiting gracefully.")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    main()