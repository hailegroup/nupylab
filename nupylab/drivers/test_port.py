import pyvisa, time
rm = pyvisa.ResourceManager()
for r in rm.list_resources():
    try:
        inst = rm.open_resource(r)
        inst.timeout = 2000
        try:
            print(r, inst.query("*IDN?"))
        except:
            print(r, inst.read())
        inst.close()
    except Exception as e:
        print(r, str(e)[:50])