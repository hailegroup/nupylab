import pyvisa, time
rm = pyvisa.ResourceManager()
scanner = rm.open_resource("GPIB0::17::INSTR")
scanner.timeout = 5000

try:
    scanner.write("R0X")  # open all
    time.sleep(0.5)
    print("opened all OK")
    scanner.write("C11X")  # close channel 11
    time.sleep(0.5)
    print("closed ch11 OK")
except Exception as e:
    print(f"error: {e}")