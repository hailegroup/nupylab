"""Adapts ROD-4 driver to NUPylab instrument class for use with NUPyLab GUIs."""

from typing import List, Tuple, Sequence

from pymeasure.instruments.proterial import rod4
from nupylab.utilities import DataTuple, NupylabError
from ..nupylab_instrument import NupylabInstrument


class ROD4(NupylabInstrument):
    """ROD-4(A) instrument class. Abstracts ROD-4 driver for NUPyLab procedures.

    Attributes:
        data_label: labels for DataTuples.
        name: name of instrument.
        lock: thread lock for preventing simultaneous calls to instrument.
        rod4: ROD4 driver class.
    """

    def __init__(
        self,
        port: str,
        data_label: Tuple[str],
        name: str = "ROD-4",
    ) -> None:
        """Initialize ROD-4 data labels, name, and connection parameters.

        Args:
            port: string name of port, e.g. `ASRL1::INSTR`.
            data_label: labels for DataTuples. :meth:`get_data` returns flow rate for
                each channel, and corresponding labels should match entries in
                DATA_COLUMNS of calling procedure class.
            name: name of instrument.

        Raises:
            ValueError if length of data_label is not 4.
        """
        if len(data_label) != 4:
            raise ValueError("ROD-4 data_label must be sequence of length 4.")
        self._port = port
        self.rod4 = None
        super().__init__(data_label, name)

    def connect(self) -> None:
        """Connect to ROD-4."""
        with self.lock:
            self.rod4 = rod4.ROD4(self._port)
            self._ranges = tuple(
                channel.mfc_range for channel in self.rod4.channels.values()
            )
            self._connected = True

    def set_parameters(self, setpoints: Sequence[float]) -> None:
        """Set ROD-4 flow setpoints.

        Args:
            setpoints: tuple or list of 4 channel setpoints.
        Raises:
            ValueError if lengths of setpoints is not 4.
        """
        if len(setpoints) != 4:
            raise ValueError("ROD-4 setpoints must be sequence of length 4.")
        self._parameters = setpoints

    def start(self) -> None:
        """Convert setpoints from sccm to % and set flow.

        Raises:
            NupylabError if `start` method is called before `set_parameters`.
        """
        if self._parameters is None:
            raise NupylabError(
                f"`{self.__class__.__name__}` method `set_parameters` "
                "must be called before calling its `start` method."
            )
        setpoints = self._parameters
        with self.lock:
            for channel, setpoint, range_ in zip(
                self.rod4.channels.values(), setpoints, self._ranges
            ):
                channel.setpoint = 100 * setpoint / range_
                if setpoint == 0:
                    channel.valve_mode = "close"
                else:
                    channel.valve_mode = "flow"
        self._parameters = None

    def get_data(self) -> List[DataTuple]:
        """Read flow for each MFC channel.

        Returns:
            tuple of four DataTuples with flow for each channel.
        """
        mfc: List[float] = []
        with self.lock:
            for channel, range_ in zip(self.rod4.channels.values(), self._ranges):
                mfc.append(channel.actual_flow * range_ / 100)
        return list(DataTuple(self.data_label[i], mfc[i]) for i in range(4))

    def stop_measurement(self) -> None:
        """Stop ROD-4 measurement. Not implemented."""
        pass

    def shutdown(self) -> None:
        """Shutdown ROD-4 gas flow and close serial connection."""
        with self.lock:
            for channel in self.rod4.channels.values():
                channel.valve_mode = "close"
            self.rod4.adapter.close()
