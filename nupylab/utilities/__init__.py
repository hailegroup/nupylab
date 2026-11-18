import sys
from typing import NamedTuple, Sequence, Tuple, Union

import pyvisa


class DataTuple(NamedTuple):
    """Container for data reported by instruments."""

    label: str
    value: Union[float, Sequence[float]]


class NupylabError(Exception):
    """General exception class for errors in NUPyLab library."""


def list_resources(query: str = "?*::INSTR", backend: str = None) -> Tuple[str, ...]:
    """Get PyVISA resource manager list. Provided for compatibility with Sphinx.

    Args:
        query: VISA Resource Regular Expression syntax for finding devices.
        backend: PyVISA backend, e.g. `@ivi` or `@py`. Optional, defaults to PyVISA
            default.

    Returns:
        Tuple of PyVISA resources.
    """
    if "sphinx" not in sys.modules:
        if backend is not None:
            rm = pyvisa.ResourceManager(backend)
        else:
            rm = pyvisa.ResourceManager()
        return rm.list_resources(query)
    return ()


def detect_instrument_port(probe, resources=None, query: str = "ASRL?*::INSTR"):
    """Find the first serial resource that answers a probe.

    Args:
        probe: callable taking a `COM##` port name. It should open the
            instrument and read from it, raising an exception if the device on
            that port is not the expected one. It is responsible for closing
            whatever it opens.
        resources: resources to try, defaults to the serial VISA resources.
            Entries that are not serial resources are skipped.
        query: VISA resource query used when `resources` is not given.

    Returns:
        VISA resource name of the first port that answered, e.g.
        `ASRL1::INSTR`, or None if no port answered.
    """
    if resources is None:
        resources = list_resources(query)
    for resource in resources:
        if "ASRL" not in resource and "COM" not in resource:
            continue  # skip USB/GPIB resources and placeholder entries
        com_port = resource.replace("ASRL", "COM").replace("::INSTR", "")
        try:
            probe(com_port)
        except Exception:
            continue
        return resource
    return None
