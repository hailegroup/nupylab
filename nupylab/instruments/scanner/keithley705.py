"""Adapts Keithley 705 driver to NUPylab instrument class for use with NUPyLab GUIs."""

from typing import Union, Sequence, Dict, Tuple, List, Optional, Callable
from nupylab.drivers import keithley705
from nupylab.utilities import DataTuple, NupylabError
from nupylab.utilities.nupylab_instrument import NupylabInstrument


class Keithley705(NupylabInstrument):
    """Keithley 705 instrument class. Adapts driver to NUPyLab scanner.

    Attributes:
        channels: dictionary of instrument measurement channels.
        name: name of instrument.
        lock: thread lock for preventing simultaneous calls to instrument.
        keithley705: Keithley 705 driver class.
    """

    def __init__(
        self,
        port: str,
        name: str = "Keithley 705",
    ) -> None:
        """Initialize Keithley 705 name and connection parameters.

        Args:
            port: string name of port, e.g. `GPIB::1`
            name: name of instrument.
        """
        self.keithley705 = None
        self._finished: bool = False
        self._port: str = port
        self.keithley705: keithley705.Keithley705
        self.channels: Dict[
            int,
            Tuple[
                NupylabInstrument,
                Union[str, Sequence[str]],
                Optional[Callable[[], None]],
            ],
        ] = {}
        self._closed_channel = 1
        super().__init__("", name)

    def connect(self) -> None:
        """Connect to Keithley 705."""
        with self.lock:
            self.keithley705 = keithley705.Keithley705(self._port)
            self._connected = True

    def set_parameters(
        self,
        channel: int,
        instrument: NupylabInstrument,
        data_label: Union[str, Sequence[str]],
        pre_process: Optional[Callable[[], None]] = None,
    ) -> None:
        """Append channel configuration to scanner channel dict.

        Args:
            channel: integer channel number.
            instrument: NupylabInstrument instance that will measure the channel.
            data_label: DataTuple labels for instrument `get_data` method.
            pre_process: optional function to call before measuring channel.
        """
        # Check that new labels are compatible with instrument class requirements
        if isinstance(instrument.data_label, str) and not isinstance(data_label, str):
            raise TypeError(f"{instrument.name} `data_label` must be of "
                            f"type string but received {data_label}")
        elif (length := len(instrument.data_label)) != len(data_label):
            raise ValueError(f"{instrument.name} `data_label` must be sequence of "
                             f"{length} but received {data_label}")
        self._finished = False
        self.channels[channel] = (instrument, data_label, pre_process)

    def start(self) -> None:
        """Prepare channel scan. Verifies channels are set."""
        if not self.channels:
            raise NupylabError(
                f"`{self.name}` method `set_parameters` "
                "must be called before its `start` method."
            )

    def get_data(self) -> Optional[List[DataTuple]]:
        """Read scanner channels.

        Steps through channel list, sets corresponding instrument `data_label`
        attribute, and calls that instrument's `get_data` method. An optional
        pre_process callable is specified, which provides a basic two-way communication
        path between the scanner and instrument classes.

        Returns:
            DataTuples from instruments reading corresponding channels.
        """
        data: List[DataTuple] = []
        f: bool = True
        with self.lock:
            for channel, (instrument, labels, pre_process) in self.channels:
                if pre_process is not None:
                    pre_process()
                instrument.data_label = labels
                if channel != self._closed_channel:
                    self.keithley705.open_channel(channel)
                self.keithley705.close_channel(channel)
                self._closed_channel = channel
                d = instrument.get_data()
                if d is not None:
                    data.append(d)
                f = f and instrument.finished
            self._finished = f
        return data if data else None

    @property
    def finished(self) -> bool:
        """Get whether measurements on all channels are finished."""
        return self._finished

    def stop_measurement(self) -> None:
        """Stop measurement on Keithley 705. Clears channel dict."""
        self.channels.clear()

    def shutdown(self) -> None:
        """Close serial connection on Keithley 705."""
        with self.lock:
            self.keithley705.reset()
            self.keithley705.adapter.close()
