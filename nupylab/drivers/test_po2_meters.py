"""Probe the S2 pO2 meters over GPIB and report exactly what answers.

Run this on the S2 station with the GUI closed, so that nothing else holds a
VISA session on the bus:

    python nupylab/drivers/test_po2_meters.py

It lists every resource the board can see, asks each GPIB instrument who it is,
and then exercises the two pO2 meters one at a time, never holding more than one
session open. Each step prints either its result or the exact VISA error, so a
failure says which instrument and which transfer caused it.

The 2182 probe sets channel 1 to temperature and the 182 probe configures DC
volts, i.e. what the GUI does on connect. Neither saves anything to the
instrument's power-on setup.
"""

import sys
import time

import pyvisa

NANOVOLTMETER = "GPIB0::7::INSTR"  # Keithley 2182A, sensor thermocouple
VOLTMETER = "GPIB0::8::INSTR"  # Keithley 182, sensor potential

TIMEOUT_MS = 3000


def report(step, result=None, error=None):
    """Print one step's outcome."""
    if error is None:
        print("    ok    {:<22} {}".format(step, "" if result is None else result))
    else:
        print("    FAIL  {:<22} {}: {}".format(step, type(error).__name__, error))


def list_resources(rm):
    """Print every VISA resource the machine can see."""
    print("Resources visible to VISA:")
    try:
        resources = rm.list_resources()
    except Exception as e:
        print("    could not list resources: {}: {}".format(type(e).__name__, e))
        return ()
    if not resources:
        print("    none found")
    for resource in resources:
        print("    {}".format(resource))
    for expected in (NANOVOLTMETER, VOLTMETER):
        if expected not in resources:
            print("    NOTE: {} is not in the list above.".format(expected))
    print()
    return resources


def identify(rm, resources):
    """Ask every GPIB instrument for its SCPI identity.

    An instrument that times out here is not necessarily absent: the Keithley
    182 predates SCPI and ignores `*IDN?`, so it stays silent and the bus holds
    off until the timeout.
    """
    print("Identifying GPIB instruments with *IDN?:")
    for resource in resources:
        if not resource.startswith("GPIB"):
            continue
        instrument = None
        try:
            instrument = rm.open_resource(resource)
            instrument.timeout = TIMEOUT_MS
            print("    {:<20} {}".format(resource, instrument.query("*IDN?").strip()))
        except Exception as e:
            print(
                "    {:<20} no SCPI answer ({})".format(resource, type(e).__name__)
            )
        finally:
            if instrument is not None:
                try:
                    instrument.close()
                except Exception:
                    pass
    print()


def probe_2182(rm, resource=NANOVOLTMETER):
    """Configure the Keithley 2182 for temperature on channel 1 and read it."""
    print("Keithley 2182 on {}:".format(resource))
    meter = None
    try:
        meter = rm.open_resource(resource)
        meter.timeout = TIMEOUT_MS
        report("open session")
    except Exception as e:
        report("open session", error=e)
        return
    try:
        report("*IDN?", meter.query("*IDN?").strip())
        meter.write("*CLS")
        report("*CLS")
        meter.write("*RST")
        time.sleep(0.5)
        report("*RST")
        meter.write(":SENS:TEMP:TC S")
        report("thermocouple type S")
        meter.write(":SENS:CHAN 1;:SENS:FUNC 'TEMP'")
        report("channel 1 temperature")
        report("reading", "{} degC".format(meter.query(":READ?").strip()))
        report("error queue", meter.query(":SYST:ERR?").strip())
    except Exception as e:
        report("transfer", error=e)
    finally:
        try:
            meter.close()
        except Exception:
            pass
    print()


def probe_182(rm, resource=VOLTMETER):
    """Configure the Keithley 182 for DC volts and read it.

    Uses the device-dependent commands the driver sends: auto-range, 100 ms
    integration, filters on with a medium digital response, reading relative
    off, readings from the A/D converter with a prefix, and multiple on talk.
    """
    print("Keithley 182 on {}:".format(resource))
    meter = None
    try:
        meter = rm.open_resource(resource, read_termination="\n")
        meter.timeout = TIMEOUT_MS
        report("open session")
    except Exception as e:
        report("open session", error=e)
        return
    try:
        meter.write("U2X")
        report("firmware revision", meter.read().strip())
        meter.write("R0S2N1P2Z0F0G1T0X")
        time.sleep(0.5)
        report("configure DC volts")
        meter.write("H0X")
        report("trigger")
        report("reading", meter.read().strip())
        report("second reading", meter.read().strip())
        meter.write("U1X")
        report("error conditions", meter.read().strip())
    except Exception as e:
        report("transfer", error=e)
    finally:
        try:
            meter.close()
        except Exception:
            pass
    print()


def main():
    """Run every probe in turn."""
    rm = pyvisa.ResourceManager()
    print("PyVISA {}, backend {}\n".format(pyvisa.__version__, rm.visalib))
    resources = list_resources(rm)
    identify(rm, resources)
    probe_2182(rm)
    probe_182(rm)
    print("Done. Paste this whole output back.")


if __name__ == "__main__":
    sys.exit(main())
