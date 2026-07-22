import pyvisa
import time

rm = pyvisa.ResourceManager()
scanner = rm.open_resource("GPIB0::17::INSTR")
hp = rm.open_resource("GPIB0::15::INSTR")
scanner.timeout = 3000
hp.timeout = 3000

# Reset all channels open
scanner.write("RX")
time.sleep(1)

for ch in [1, 2, 3, 4, 5]:
    scanner.write(f"C{ch}X")  # no zero padding
    time.sleep(1.0)
    try:
        voltage = hp.read()
        print(f"channel {ch}: {repr(voltage)}")
    except Exception as e:
        print(f"channel {ch}: error {e}")
    scanner.write("RX")  # reset/open all
    time.sleep(0.5)