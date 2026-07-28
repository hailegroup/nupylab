import minimalmodbus

for baud in (9600, 19200, 4800):
    for parity in ('N', 'E'):
        for stopbits in (1, 2):
            try:
                inst = minimalmodbus.Instrument('COM9', 1)
                inst.serial.baudrate = baud
                inst.serial.parity = parity
                inst.serial.stopbits = stopbits
                inst.serial.timeout = 1
                val = inst.read_register(1, 1)
                print(f'SUCCESS: baud={baud} parity={parity} stopbits={stopbits} temp={val}')
            except Exception as e:
                print(f'baud={baud} parity={parity} stopbits={stopbits}: {str(e)[:30]}')