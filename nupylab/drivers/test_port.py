import minimalmodbus

for addr in range(1, 11):
    for parity in ("N", "E"):
        try:
            inst = minimalmodbus.Instrument("COM4", addr)  # swap COM4 for your port
            inst.serial.parity = parity
            inst.serial.timeout = 0.3
            print("COM4", addr, parity, inst.read_register(1, 1))
        except Exception:
            print("COM4", addr, parity, "no answer")