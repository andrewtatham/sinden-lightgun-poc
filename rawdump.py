"""Print every raw event from every Sinden gun, so you can see exactly what each button sends.
Run:  .venv/bin/python rawdump.py     (Ctrl-C to stop)"""
import time
import gunio
inp = gunio.make_input("evdev", grab=True)
print(inp.describe(), "- press buttons on the guns (Ctrl-C to stop)")
try:
    while True:
        for gi, name, pressed in inp.poll():
            print("gun %d  %-12s %s" % (gi + 1, name, "DOWN" if pressed else "up"), flush=True)
        time.sleep(0.005)
except KeyboardInterrupt:
    pass
finally:
    inp.close()
