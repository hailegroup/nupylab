import minimalmodbus, time
inst = minimalmodbus.Instrument('COM9', 1)
inst.serial.baudrate = 9600
inst.serial.stopbits = 1
inst.serial.timeout = 2
time.sleep(0.5)
print('OP.RATE reg 35:', inst.read_register(35, 1))
print('OP.RATE float:', inst.read_float(2*35 + 32768))