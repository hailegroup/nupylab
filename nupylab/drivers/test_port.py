import minimalmodbus, time
inst = minimalmodbus.Instrument('COM9', 1)
inst.serial.baudrate = 9600
inst.serial.stopbits = 1
inst.serial.timeout = 2
time.sleep(0.5)
inst.write_register(30, 100)  # output high limit = 100%
inst.write_register(35, 0)    # output rate limit = no limit
print("done")
for reg, name in [(30,'output_high'), (35,'output_rate'), (6,'P'), (7,'I')]:
    print(f'{name}: {inst.read_register(reg, 1)}')