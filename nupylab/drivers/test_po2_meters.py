"""Probe the S2 pO2 meters over GPIB and report exactly what answers.

Run this on the S2 station with the GUI closed, so that nothing else holds a
VISA session on the bus:

    python nupylab/drivers/test_po2_meters.py

It lists every resource the board can see, asks each GPIB instrument who it is,
and then exercises the two pO2 meters one at a time, never holding more than one
session open. Every command is timed and reported on its own line, so a stall
says which transfer caused it rather than only which instrument.

The Keithley 182 is tried under three configurations, since the one the GUI uses
is in question: a free-running series at a half-second interval, the same at the
0.01 s minimum, and one reading per talk. Each starts from a device clear, so an
earlier stall does not decide the next result.

Nothing here is saved to either instrument's power-on setup.
"""

import sys
import time

import pyvisa

NANOVOLTMETER = "GPIB0::7::INSTR"  # Keithley 2182A, sensor thermocouple
VOLTMETER = "GPIB0::8::INSTR"  # Keithley 182, sensor potential

IDENTIFY_TIMEOUT_MS = 3000
# A stalled GPIB transfer can take longer than this to give up, since the board
# rounds the timeout up to one of its own settings
METER_TIMEOUT_MS = 5000

# What the GUI sends the 182, as (command, what it does). The trigger interval
# and mode are appended per scenario.
SETUP_182 = (
    ("L1X", "recall factory defaults"),
    ("R0X", "auto-range"),
    ("B1X", "6.5-digit resolution"),
    ("S2X", "100 ms integration"),
    ("N1X", "filters on"),
    ("P2X", "medium digital filter"),
    ("Z0X", "reading relative off"),
    ("F0X", "readings from the A/D"),
    ("G0X", "reading only, no prefix"),
)

SCENARIOS_182 = (
    (
        "free-running, 0.5 s interval (what the GUI now sends)",
        (("Q500X", "trigger interval 500 ms"), ("T0X", "multiple on talk")),
        True,
    ),
    (
        "free-running, 0.01 s interval (what the GUI sent before)",
        (("Q10X", "trigger interval 10 ms"), ("T0X", "multiple on talk")),
        True,
    ),
    (
        "one reading per talk, no interval set",
        (("T1X", "one-shot on talk"),),
        False,
    ),
)


def report(step, result=None, error=None, seconds=None):
    """Print one step's outcome, with how long it took."""
    timing = "" if seconds is None else "[{:6.0f} ms] ".format(seconds * 1000)
    if error is None:
        print(
            "    ok    {}{:<34} {}".format(
                timing, step, "" if result is None else result
            )
        )
    else:
        print(
            "    FAIL  {}{:<34} {}: {}".format(
                timing, step, type(error).__name__, error
            )
        )


def write_only(meter, command):
    """Write one command, returning nothing so its report line stays clean."""
    meter.write(command)


def timed(step, call):
    """Run one transfer, reporting its result or its error. Returns success."""
    start = time.monotonic()
    try:
        result = call()
    except Exception as e:
        report(step, error=e, seconds=time.monotonic() - start)
        return False
    report(step, result, seconds=time.monotonic() - start)
    return True


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
            instrument = rm.open_resource(resource, read_termination="\n")
            instrument.timeout = IDENTIFY_TIMEOUT_MS
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
        # PyMeasure's class defaults to a CR terminator, but the 2182 ends its
        # replies with LF: the mismatch is what warns about termination
        # characters on every read
        meter = rm.open_resource(resource, read_termination="\n")
        meter.timeout = METER_TIMEOUT_MS
        report("open session")
    except Exception as e:
        report("open session", error=e)
        return
    try:
        timed("*IDN?", lambda: meter.query("*IDN?").strip())
        timed("*CLS", lambda: write_only(meter, "*CLS"))
        timed("*RST", lambda: write_only(meter, "*RST"))
        time.sleep(0.5)
        timed(":SENS:TEMP:TC S", lambda: write_only(meter, ":SENS:TEMP:TC S"))
        timed(
            "channel 1 temperature",
            lambda: write_only(meter, ":SENS:CHAN 1;:SENS:FUNC 'TEMP'"),
        )
        for i in range(3):
            timed(
                "reading {}".format(i + 1),
                lambda: "{} degC".format(meter.query(":READ?").strip()),
            )
        timed("error queue", lambda: meter.query(":SYST:ERR?").strip())
    finally:
        try:
            meter.close()
        except Exception:
            pass
    print()


def probe_182_scenario(meter, label, trigger_commands, interval_set):
    """Configure the 182 one way and try to read it three times."""
    print("  {}:".format(label))
    if not timed("device clear", lambda: meter.clear()):
        return
    for command, meaning in SETUP_182 + tuple(trigger_commands):
        if not timed(
            "{:<6} {}".format(command, meaning),
            lambda c=command: write_only(meter, c),
        ):
            print("    stopped: the command above did not go through\n")
            return
    # A multiple trigger mode needs one stimulus before it starts converting
    if interval_set and not timed(
        "H0X    trigger", lambda: write_only(meter, "H0X")
    ):
        return
    for i in range(3):
        if not timed("read {}".format(i + 1), lambda: meter.read().strip()):
            print("    stopped: no reading came back\n")
            return
    print()


def probe_182(rm, resource=VOLTMETER):
    """Try the Keithley 182 under each trigger configuration in turn."""
    print("Keithley 182 on {}:".format(resource))
    meter = None
    try:
        # No terminator: a GPIB message ends on EOI, which the 182 asserts with
        # its last character, and the CR LF it adds is stripped when parsed
        meter = rm.open_resource(resource)
        meter.timeout = METER_TIMEOUT_MS
        report("open session")
    except Exception as e:
        report("open session", error=e)
        return
    try:
        # A serial poll is a bus command rather than data, so it tells apart an
        # instrument that is wedged, holding the handshake and answering
        # nothing, from one that is listening but not accepting data
        timed("serial poll (status byte)", lambda: meter.read_stb())
        timed("U2X    firmware revision", lambda: write_only(meter, "U2X"))
        timed("read revision", lambda: meter.read().strip())
        print()
        for label, trigger_commands, interval_set in SCENARIOS_182:
            probe_182_scenario(meter, label, trigger_commands, interval_set)
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
