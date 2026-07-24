import serial, time
s = serial.Serial('COM3', 9600, bytesize=8, stopbits=1, parity='N', timeout=1)
time.sleep(0.5)
for i in range(1, 5):
    s.read_all()
    s.write(f"\x02{i:02d}RFX\r".encode())
    time.sleep(0.3)
    print(f"ch{i} flow:", repr(s.read_all()))
s.close()