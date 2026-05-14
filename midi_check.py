import rtmidi
m = rtmidi.MidiOut()
print(m.get_ports())  # lists port names (USB names usually include "USB")
