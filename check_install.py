print("Testing imports...")

try:
    import mido
    print("✓ mido works")
    print(f"  MIDI ports: {mido.get_output_names()}")
except Exception as e:
    print(f"✗ mido failed: {e}")

try:
    import rtmidi
    print("✓ rtmidi works")
except Exception as e:
    print(f"✗ rtmidi failed: {e}")

try:
    import leap
    print("✓ leap works")
except Exception as e:
    print(f"✗ leap failed: {e}")

print("\nIf all three show ✓, you're ready to run your code!")
