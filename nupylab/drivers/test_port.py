import pyvisa
import time

rm = pyvisa.ResourceManager()
scanner = rm.open_resource("GPIB0::17::INSTR")
hp = rm.open_resource("GPIB0::15::INSTR")
scanner.timeout = 2000
hp.timeout = 2000

for ch in range(1, 11):
    scanner.write(f"C{ch:02d}X")  # close channel
    time.sleep(2)
    voltage = hp.read()  # read voltage
    print(f"channel {ch}: {repr(voltage)}")
    scanner.write("N0X")  # open all channels
    time.sleep(0.3)