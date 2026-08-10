import pyvisa, time
rm = pyvisa.ResourceManager()
scanner = rm.open_resource("GPIB0::17::INSTR")

# Check what channels are available on card 2 (EIS channels)
for ch in range(11, 21):
    scanner.write("R0X")
    time.sleep(0.2)
    scanner.write(f"C{ch}X")
    time.sleep(0.3)
    print(f"Channel {ch} closed")
    time.sleep(0.5)

scanner.write("R0X")