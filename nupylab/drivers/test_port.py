import serial, time
s = serial.Serial('COM3', 9600, bytesize=8, stopbits=1, parity='N', timeout=1)
time.sleep(0.5)
s.write(b"\x0201RFX\r")
time.sleep(0.3)
print(repr(s.read_all()))
s.close()