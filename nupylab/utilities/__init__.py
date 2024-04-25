from typing import NamedTuple, Union, Sequence


class DataTuple(NamedTuple):
    """Container for data reported by instruments."""

    label: str
    value: Union[float, Sequence[float]]


class NupylabError(Exception):
    """General exception class for errors in NUPyLab library."""
