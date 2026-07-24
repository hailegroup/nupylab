import minimalmodbus, serial.tools.list_ports

ports = [p.device for p in serial.tools.list_ports.comports()]
print("Available ports:", ports)

for port in ports:
    for addr in [1, 2]:
        try:
            inst = minimalmodbus.Instrument(port, addr)
            inst.serial.baudrate = 9600
            inst.serial.stopbits = 1
            inst.serial.timeout = 1
            val = inst.read_register(1, 1)
            print(f"FOUND: {port} addr={addr} temp={val}")
        except Exception:
            print(f"{port} addr={addr}: no response")