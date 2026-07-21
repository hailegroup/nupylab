import minimalmodbus

for addr in range(1, 32):
    try:
        inst = minimalmodbus.Instrument("COM9", addr)
        inst.serial.parity = "E"
        inst.serial.baudrate = 9600
        inst.serial.timeout = 0.5
        val = inst.read_register(1, 1)
        print(f"SUCCESS: addr={addr} temp={val}")
    except Exception as e:
        print(f"addr={addr} -- {str(e)[:40]}")