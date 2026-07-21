"""Adapts ROD-4 driver to NUPylab instrument class for use with NUPyLab GUIs."""

from typing import List, Sequence

from nupylab.utilities import DataTuple, NupylabError
from pymeasure.instruments.proterial import rod4
from nupylab.utilities.nupylab_instrument import NupylabInstrument


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
        data_label: Sequence[str],
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
            channels = [self.rod4.ch_1, self.rod4.ch_2, self.rod4.ch_3, self.rod4.ch_4]
            self._ranges = tuple(
                float(str(channel.mfc_range).lstrip('EN')) for channel in channels
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
        channels = [self.rod4.ch_1, self.rod4.ch_2, self.rod4.ch_3, self.rod4.ch_4]
        with self.lock:
            for channel, setpoint, range_ in zip(channels, setpoints, self._ranges):
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
        channels = [self.rod4.ch_1, self.rod4.ch_2, self.rod4.ch_3, self.rod4.ch_4]
        with self.lock:
            for channel, range_ in zip(channels, self._ranges):
                mfc.append(float(str(channel.actual_flow).lstrip('EN')) * range_ / 100)
        return list(DataTuple(self.data_label[i], mfc[i]) for i in range(4))

    def stop_measurement(self) -> None:
        """Stop ROD-4 measurement. Not implemented."""
        pass

    def shutdown(self) -> None:
        """Shutdown ROD-4 gas flow and close serial connection."""
        channels = [self.rod4.ch_1, self.rod4.ch_2, self.rod4.ch_3, self.rod4.ch_4]
        with self.lock:
            for channel in channels:
                channel.valve_mode = "close"
            self.rod4.adapter.close()