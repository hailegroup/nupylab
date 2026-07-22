import serial, time
s = serial.Serial("COM3", 9600, bytesize=8, stopbits=1, parity='N', timeout=1)
s.write(b"\x0201SVM1\r")  # CLOSE channel 1
time.sleep(0.3)
print(repr(s.read_all()))
s.write(b"\x0201SFD0.0\r")  # zero setpoint
time.sleep(0.3)
print(repr(s.read_all()))
s.close()