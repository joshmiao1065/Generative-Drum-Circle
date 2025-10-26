import leap
import time
import mido
import sys
import math

# --- Configuration ---
MIDI_PORT_NAME = 'USB MIDI Interface 1'
DOWNWARD_STRIKE_THRESHOLD = 30.0 
OPEN_HAND_THRESHOLD = 0.2
HORIZONTAL_ACTIVE_RADIUS = 120.0 
LEFT_HAND_NOTE = 60
LEFT_HAND_CHANNEL = 8
RIGHT_HAND_NOTE = 20
RIGHT_HAND_CHANNEL = 9

class StableDrumController(leap.Listener):
    def __init__(self, port):
        super().__init__()
        self.port = port
        self.last_trigger_heights = {}
        self.active_hands = {}

    def on_tracking_event(self, event):
        # A set to keep track of which hands we've seen in this specific frame.
        seen_this_frame = set()

        # --- Phase 1: Process all currently VISIBLE hands ---
        for hand in event.hands:
            seen_this_frame.add(hand.id)
            pos = hand.palm.position
            
            horizontal_distance = math.sqrt(pos.x**2 + pos.z**2)
            is_over_sensor = horizontal_distance < HORIZONTAL_ACTIVE_RADIUS
            
            # If the hand is not active yet, check if it should become active.
            if hand.id not in self.active_hands:
                is_open_hand = hand.grab_strength < OPEN_HAND_THRESHOLD
                if is_open_hand and is_over_sensor:
                    hand_type = "left" if hand.type == leap.HandType.Left else "right"
                    print(f"NEW {hand_type.capitalize()} Hand (ID: {hand.id}) activated in zone.")
                    self.active_hands[hand.id] = hand_type
                    self.send_note(hand_id=hand.id, state="on")
                    self.last_trigger_heights[hand.id] = pos.y
            
            # If the hand is already active, check for a re-trigger.
            elif hand.id in self.active_hands:
                last_height = self.last_trigger_heights[hand.id]
                current_height = pos.y
                downward_distance = last_height - current_height
                
                is_downward_strike = downward_distance > DOWNWARD_STRIKE_THRESHOLD
                is_open_hand = hand.grab_strength < OPEN_HAND_THRESHOLD

                if is_downward_strike and is_open_hand and is_over_sensor:
                    print(f"Hand {hand.id} struck down in active zone. Re-triggering.")
                    self.send_note(hand_id=hand.id, state="on")
                    self.last_trigger_heights[hand.id] = current_height
                
                elif current_height > last_height:
                    self.last_trigger_heights[hand.id] = current_height

        # --- Phase 2: Process all LOST hands ---
        # Iterate over a copy of the keys, as we will be modifying the dictionary.
        for hand_id in list(self.active_hands.keys()):
            if hand_id not in seen_this_frame:
                # This hand was active, but is no longer visible.
                print(f"Hand {hand_id} lost. Sending Note Off.")
                self.send_note(hand_id=hand_id, state="off")
                # Safely remove it from our state tracking.
                del self.active_hands[hand_id]
                if hand_id in self.last_trigger_heights:
                    del self.last_trigger_heights[hand_id]

    def send_note(self, hand_id, state):
        hand_type = self.active_hands.get(hand_id)
        if not hand_type: return

        note = LEFT_HAND_NOTE if hand_type == "left" else RIGHT_HAND_NOTE
        channel = LEFT_HAND_CHANNEL if hand_type == "left" else RIGHT_HAND_CHANNEL

        if state == "on":
            self.port.send(mido.Message('note_off', note=note, velocity=0, channel=channel))
            self.port.send(mido.Message('note_on', note=note, velocity=100, channel=channel))
        elif state == "off":
            self.port.send(mido.Message('note_off', note=note, velocity=0, channel=channel))

def main():
    try:
        with mido.open_output(MIDI_PORT_NAME) as port:
            print(f"Opened MIDI port: {port.name}")
            listener = StableDrumController(port)
            connection = leap.Connection()
            connection.add_listener(listener)
            
            with connection.open():
                print("Setting tracking mode...")
                connection.set_tracking_mode(leap.TrackingMode.Desktop)
                print(f"Strike Threshold: {DOWNWARD_STRIKE_THRESHOLD}mm, Open Hand: <{OPEN_HAND_THRESHOLD}, Active Radius: {HORIZONTAL_ACTIVE_RADIUS}mm")
                print("Connection open. Press Ctrl+C to exit.")
                while True:
                    time.sleep(1)
    except KeyboardInterrupt:
        print("\nExiting gracefully.")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    main()