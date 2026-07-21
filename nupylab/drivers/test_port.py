import pyvisa
rm = pyvisa.ResourceManager()
for resource in rm.list_resources():
    try:
        inst = rm.open_resource(resource)
        inst.timeout = 1000
        print(resource, inst.query("*IDN?"))
        inst.close()
    except Exception as e:
        print(resource, str(e)[:60])
    