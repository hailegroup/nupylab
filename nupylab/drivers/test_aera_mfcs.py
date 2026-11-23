"""Find which serial port the Aera MFCs are on, and which addresses answer.

Run this on the station with the GUI closed, so that nothing else holds a
session on the port:

    python nupylab/drivers/test_aera_mfcs.py

With no arguments it tries every serial port either VISA backend can see.
Name ports to try only those, whether or not they were listed:

    python nupylab/drivers/test_aera_mfcs.py ASRL6::INSTR

Two backends are tried for each port, because they do not agree about which
ports exist. National Instruments' VISA only knows the serial ports it
enumerated, and a USB RS-485 converter that Windows has given a COM number is
not always among them: it is missing from `list_resources` and opening it
fails with `VI_ERROR_RSRC_NFOUND`, however well the converter works for
everything else on the machine. `pyvisa-py` reaches a COM port through
`pyserial` instead, and is not affected.

Every command sent here is a read: `RFX` asks an MFC what it is flowing and
`RVN` asks its version and serial number. Nothing is set, no valve moves, and
nothing is written to any EEPROM, so this is safe to run while gas flows.

An Aera answers a read with a status character, one of `N`, `A`, `E`, `X` or
`Z`, followed by the value. Anything else on the line, a Eurotherm say, either
does not answer or answers with something that does not parse, and is reported
as such rather than claimed as an MFC.

The scan is slow by nature: an address with nothing on it costs one timeout.
Narrow it with --addresses if you know roughly where to look, and raise
--timeout if a port answers intermittently.
"""

import argparse
import sys
import time

import pyvisa

#: Serial settings of the Aera digital interface, as the AE DMFC utility uses
#: them. Mirrors :mod:`nupylab.drivers.aera_mfc`.
BAUD_RATE = 9600
DATA_BITS = 8

#: VISA backends to try, in order, and what to call them. pyvisa-py first,
#: as the GUI does: an Aera is a plain serial device, and it finds ports the
#: system VISA may never have enumerated.
BACKENDS = (("@py", "pyvisa-py"), ("", "the system VISA"))

#: Status characters an Aera puts in front of a reply.
STATUSES = {
    "N": "normal",
    "A": "alarm",
    "E": "error",
    "X": "alarm and error",
    "Z": "zeroing",
}


def parse_args(argv):
    """Read the command line."""
    parser = argparse.ArgumentParser(
        description="Find the Aera MFCs on this station's serial ports."
    )
    parser.add_argument(
        "ports",
        nargs="*",
        help="VISA resources to try, e.g. ASRL6::INSTR. Default: every "
        "serial port either backend can see.",
    )
    parser.add_argument(
        "--addresses",
        default="1-16",
        help="addresses to try, e.g. 5-8 or 1-127. Default: 1-16.",
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=BAUD_RATE,
        help="baud rate. Default: {}, which is what the DMFC utility "
        "uses.".format(BAUD_RATE),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=400,
        help="milliseconds to wait for an answer. Default: 400.",
    )
    return parser.parse_args(argv)


def address_range(text):
    """Read `5-8` or `7` as a list of addresses.

    Args:
        text: the range as written.

    Returns:
        The addresses, in order.

    Raises:
        SystemExit: if it is not a range, or falls outside 1 to 127.
    """
    try:
        if "-" in text:
            first, last = (int(part) for part in text.split("-", 1))
        else:
            first = last = int(text)
    except ValueError:
        raise SystemExit("--addresses takes a number or a range, e.g. 5-8")
    if not 1 <= first <= last <= 127:
        raise SystemExit("--addresses must lie between 1 and 127")
    return list(range(first, last + 1))


def managers():
    """Open each VISA backend that loads.

    Returns:
        The backend name, what to call it, and its resource manager, for each
        one that could be opened.
    """
    opened = []
    for library, name in BACKENDS:
        try:
            opened.append((library, name, pyvisa.ResourceManager(library)))
        except Exception as e:
            print("{} could not be loaded: {}".format(name, e))
    return opened


def serial_ports(opened):
    """Get every serial resource any backend can see, and who saw it.

    Args:
        opened: the backends, as :func:`managers` returns them.

    Returns:
        A mapping of resource name to the names of the backends listing it,
        in the order first seen.
    """
    seen = {}
    for _, name, manager in opened:
        try:
            resources = manager.list_resources("?*")
        except Exception as e:
            print("{} could not list resources: {}".format(name, e))
            continue
        for resource in resources:
            if str(resource).upper().startswith("ASRL"):
                seen.setdefault(str(resource), []).append(name)
    return seen


def probe(instrument, address):
    """Ask one address what it is flowing.

    Args:
        instrument: the open serial session.
        address: the address to ask, 1 to 127.

    Returns:
        `None` if nothing answered, otherwise the status character and the
        flow, or the status `?` and the reply itself where it did not parse.
    """
    reply = instrument.query("\x02{:02x}RFX".format(address)).strip()
    if not reply:
        return None
    status, rest = reply[0], reply[1:]
    if status not in STATUSES:
        return "?", reply
    try:
        return status, float(rest)
    except ValueError:
        return "?", reply


def version(instrument, address):
    """Ask one address for its version and serial number, or say why not."""
    try:
        return instrument.query("\x02{:02x}RVN".format(address)).strip()[1:]
    except pyvisa.Error as e:
        return "could not be read: {}".format(e)


def open_port(opened, port, baud, timeout):
    """Open one port through whichever backend will have it.

    Args:
        opened: the backends, as :func:`managers` returns them.
        port: the resource to open.
        baud: baud rate.
        timeout: milliseconds to wait for an answer.

    Returns:
        The open session and the name of the backend that opened it, or
        `None` and a report of why each refused.
    """
    failures = []
    for _, name, manager in opened:
        try:
            instrument = manager.open_resource(
                port,
                baud_rate=baud,
                data_bits=DATA_BITS,
                parity=pyvisa.constants.Parity.none,
                stop_bits=pyvisa.constants.StopBits.one,
            )
        except Exception as e:
            failures.append("{}: {}".format(name, e))
            continue
        instrument.read_termination = "\r"
        instrument.write_termination = "\r"
        instrument.timeout = timeout
        return instrument, name
    return None, "; ".join(failures)


def scan_port(opened, port, addresses, baud, timeout):
    """Try every address on one port and report what answered.

    Args:
        opened: the backends, as :func:`managers` returns them.
        port: the resource to open.
        addresses: addresses to try.
        baud: baud rate.
        timeout: milliseconds to wait for an answer.

    Returns:
        The addresses that answered as an Aera would, and the backend that
        opened the port.
    """
    print("\n{} at {} baud, {}N1".format(port, baud, DATA_BITS))
    instrument, opener = open_port(opened, port, baud, timeout)
    if instrument is None:
        print("  could not be opened. {}".format(opener))
        return [], None
    print("  opened with {}".format(opener))

    found = []
    unparsed = 0
    try:
        for address in addresses:
            started = time.monotonic()
            try:
                answer = probe(instrument, address)
            except pyvisa.Error:
                continue  # Nothing there, which is most addresses
            elapsed = (time.monotonic() - started) * 1e3
            if answer is None:
                continue
            status, value = answer
            if status == "?":
                unparsed += 1
                print(
                    "  address {:3d}: answered {!r}, which is not an Aera "
                    "reply".format(address, value)
                )
                continue
            found.append(address)
            print(
                "  address {:3d}: flowing {:.1f} % of range, status {} "
                "({:.0f} ms)".format(address, value, STATUSES[status], elapsed)
            )
            print(
                "               version and serial {}".format(
                    version(instrument, address)
                )
            )
    finally:
        try:
            instrument.close()
        except Exception:
            pass

    if not found and not unparsed:
        print("  nothing answered")
    elif not found:
        print("  something is on this port, but it is not answering as an Aera")
    return found, opener


def main(argv=None):
    """Scan the ports and say what was found."""
    args = parse_args(sys.argv[1:] if argv is None else argv)
    addresses = address_range(args.addresses)
    opened = managers()
    if not opened:
        print("No VISA backend could be loaded at all.")
        return 1

    if args.ports:
        ports = list(args.ports)
    else:
        listed = serial_ports(opened)
        for port, backends in listed.items():
            print("{} listed by {}".format(port, " and ".join(backends)))
        ports = list(listed)
    if not ports:
        print(
            "Neither backend can see a serial port. Check that the RS-485 "
            "converter is plugged in and that its driver is installed. If "
            "Windows shows it in Device Manager, name its port on the "
            "command line, e.g. ASRL6::INSTR."
        )
        return 1
    print("\nTrying: {}".format(", ".join(ports)))
    print("Addresses {} to {}".format(addresses[0], addresses[-1]))

    answered = {}
    for port in ports:
        found, opener = scan_port(opened, port, addresses, args.baud, args.timeout)
        if found:
            answered[port] = (found, opener)

    print("\n" + "-" * 60)
    if not answered:
        print(
            "No Aera MFCs answered. If the converter's port was among those "
            "tried, check the baud rate against the DMFC utility and pass "
            "--baud, and check that the converter is not echoing what is "
            "sent to it."
        )
        return 1
    for port, (found, opener) in answered.items():
        print(
            "{}: Aera MFCs at {}, through {}".format(
                port,
                ", ".join(str(address) for address in found),
                opener,
            )
        )
    if len(answered) == 1:
        port = next(iter(answered))
        print(
            "\nSet MFC Port to {} in the inputs panel. The station remembers "
            "it, so this is a one-off.".format(port)
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
