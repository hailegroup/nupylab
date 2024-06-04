"""Abstract instrument class module for NUPyLab procedures."""

from threading import Lock
from typing import Any, Optional, Sequence, Union

from nupylab.utilities import DataTuple


class NupylabInstrument:
    """Generic NUPyLab instrument for instruments to subclass.

    Ensures that subclasses have implemented all necessary methods and attributes.

    Attributes:
        data_label: labels for DataTuples.
        name: name of instrument.
        lock: thread lock for preventing simultaneous calls to instrument.
    """

    _port = None

    def __init__(
        self, data_label: Union[str, Sequence[str]], name: str, *args, **kwargs
    ) -> None:
        """Instantiate generic NUPyLab instrument.

        Args:
            data_label: one or more labels for DataTuples, should match entries in
                DATA_COLUMNS of calling procedure class.
            name: name of instrument.
        """
        self.data_label: Union[str, Sequence[str]] = data_label
        self.name: str = name
        self.lock: Lock = Lock()
        self._connected: bool = False
        self._parameters: Optional[Any] = None
        super().__init__(*args, **kwargs)

    def connect(self) -> None:
        """Connect to instrument."""
        raise NotImplementedError(
            "Method `connect` missing from instrument class "
            f"`{self.__class__.__name__}`."
        )

    def set_parameters(self, *args, **kwargs) -> None:
        """Set instrument measurement parameters."""
        raise NotImplementedError(
            "Method `set_parameters` missing from instrument class "
            f"`{self.__class__.__name__}`."
        )

    def start(self) -> None:
        """Start instrument."""
        raise NotImplementedError(
            "Method `start` missing from instrument class "
            f"`{self.__class__.__name__}`."
        )

    def get_data(self) -> Union[DataTuple, Sequence[DataTuple]]:
        """Get data from instrument.

        Returns:
            one or more DataTuples.
        """
        raise NotImplementedError(
            "Method `get_data` missing from instrument class "
            f"`{self.__class__.__name__}`."
        )

    @property
    def connected(self) -> bool:
        """Get whether instrument is connected."""
        return self._connected

    @property
    def finished(self) -> bool:
        """Get whether measurement is finished. Default True unless overwritten."""
        return True

    def stop_measurement(self) -> None:
        """Stop instrument measurement."""
        raise NotImplementedError(
            "Method `stop_measurement` missing from instrument class "
            f"`{self.__class__.__name__}`."
        )

    def shutdown(self) -> None:
        """Shutdown instrument and close connection."""
        raise NotImplementedError(
            "Method `shutdown` missing from instrument class "
            f"`{self.__class__.__name__}`."
        )

    def __repr__(self):
        """Get instrument class connection status and data label."""
        return (f"{self.__class__} (name={self.name}, port={self._port}, "
                f"connected={self._connected}, data_label={self.data_label})")
