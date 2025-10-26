import leap
import time
import mido
import sys

# --- Configuration ---
MIDI_PORT_NAME = 'USB MIDI Interface 1'
LEFT_HAND_NOTE = 60
LEFT_HAND_CHANNEL = 8
LEFT_HAND_CC = 74

RIGHT_HAND_NOTE = 62
RIGHT_HAND_CHANNEL = 9
RIGHT_HAND_CC = 74

class RobustMidiController(leap.Listener):
    def __init__(self, port):
        super().__init__()
        self.port = port
        self.active_hands = {} # Stores {hand_id: "left" or "right"}
        self.last_cc_values = {} # Stores {(channel, cc): value}

    def on_tracking_event(self, event):
        current_hand_ids = {h.id for h in event.hands}

        # --- Handle New and Existing Hands ---
        for hand in event.hands:
            # First, check if this is a new hand to trigger a Note On.
            if hand.id not in self.active_hands:
                hand_type = "left" if hand.type == leap.HandType.Left else "right"
                self.active_hands[hand.id] = hand_type
                print(f"NEW {hand_type.capitalize()} Hand (ID: {hand.id}). Note On.")
                self.send_note(hand_id=hand.id, state="on")
            
            # For any active hand, always update its CC value.
            # This provides the continuous, expressive control.
            self.update_cc(hand)

        # --- Handle Hands That Have Disappeared ---
        lost_hand_ids = self.active_hands.keys() - current_hand_ids
        if lost_hand_ids:
            for hand_id in list(lost_hand_ids):
                print(f"Hand {hand_id} lost. Note Off.")
                self.send_note(hand_id=hand_id, state="off")
                # Remove the hand from the active state.
                del self.active_hands[hand_id]

    def send_note(self, hand_id, state):
        hand_type = self.active_hands[hand_id]

        if state == "on":
            note = LEFT_HAND_NOTE if hand_type == "left" else RIGHT_HAND_NOTE
            channel = LEFT_HAND_CHANNEL if hand_type == "left" else RIGHT_HAND_CHANNEL
            self.port.send(mido.Message('note_on', note=note, velocity=100, channel=channel))
        
        elif state == "off":
            note = LEFT_HAND_NOTE if hand_type == "left" else RIGHT_HAND_NOTE
            channel = LEFT_HAND_CHANNEL if hand_type == "left" else RIGHT_HAND_CHANNEL
            self.port.send(mido.Message('note_off', note=note, velocity=0, channel=channel))
            
            # --- IMPORTANT FIX ---
            # When a note is turned off, clear its associated CC values
            # so they don't get "stuck".
            cc = LEFT_HAND_CC if hand_type == "left" else RIGHT_HAND_CC
            cc_key = (channel, cc)
            if cc_key in self.last_cc_values:
                del self.last_cc_values[cc_key]

    def update_cc(self, hand):
        # This function is now only called for active hands.
        hand_type = self.active_hands[hand.id]
        pos = hand.palm.position

        if hand_type == "left":
            channel, control = LEFT_HAND_CHANNEL, LEFT_HAND_CC
            value = max(0, min(127, int((pos.x + 150) / 300 * 127)))
        else: # Right Hand
            channel, control = RIGHT_HAND_CHANNEL, RIGHT_HAND_CC
            value = max(0, min(127, int((pos.y - 50) / 300 * 127)))
        
        # This check provides efficiency without sacrificing responsiveness.
        cc_key = (channel, control)
        last_value = self.last_cc_values.get(cc_key)
        
        if value != last_value:
            self.port.send(mido.Message('control_change', channel=channel, control=control, value=value))
            self.last_cc_values[cc_key] = value

def main():
    # This main loop is stable and correct.
    try:
        with mido.open_output(MIDI_PORT_NAME) as port:
            print(f"Opened MIDI port: {port.name}")
            listener = RobustMidiController(port)
            connection = leap.Connection()
            connection.add_listener(listener)
            with connection.open():
                print("Setting tracking mode...")
                connection.set_tracking_mode(leap.TrackingMode.Desktop)
                print("Tracking mode set to Desktop.")
                print("Connection open. Press Ctrl+C to exit.")
                while True:
                    time.sleep(1)
    except KeyboardInterrupt:
        print("\nExiting gracefully.")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    main()