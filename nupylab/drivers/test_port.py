import minimalmodbus

for parity in ("N", "E", "O"):
    for baud in (9600, 19200):
        try:
            inst = minimalmodbus.Instrument("COM9", 1)  # your port
            inst.serial.parity = parity
            inst.serial.baudrate = baud
            inst.serial.timeout = 1
            val = inst.read_register(1, 1)
            print(f"SUCCESS: parity={parity} baud={baud} temp={val}")
        except Exception as e:
            print(f"FAIL: parity={parity} baud={baud} -- {str(e)[:50]}")