import minimalmodbus
inst = minimalmodbus.Instrument("COM4", 1)
inst.serial.baudrate = 9600
inst.serial.timeout = 1
print(inst.read_register(1, 1))