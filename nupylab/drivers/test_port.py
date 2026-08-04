import minimalmodbus, time
inst = minimalmodbus.Instrument('COM9', 1)
inst.serial.baudrate = 9600
inst.serial.stopbits = 1
inst.serial.timeout = 2
time.sleep(0.5)

print("reg 7 decimal 0:", inst.read_register(7, 0))
print("reg 7 decimal 1:", inst.read_register(7, 1))
print("reg 7 decimal 2:", inst.read_register(7, 2))

for val in [18, 180, 1800]:
    try:
        inst.write_register(7, val)
        print(f"write {val}: OK, readback:", inst.read_register(7, 1))
    except Exception as e:
        print(f"write {val}: FAILED - {e}")