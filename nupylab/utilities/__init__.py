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
