"""Test instruments to play with GUI."""

from nupylab.utilities import DataTuple, NupylabError
from nupylab.utilities.nupylab_instrument import NupylabInstrument
from typing import Union, Sequence, Dict, Tuple, List, Optional, Callable
import numpy as np

class Eurotherm3216(NupylabInstrument):
    """Eurotherm 3216 instrument class. Abstracts driver for NUPyLab procedures.

    Attributes:
        data_label: label for DataTuples.
        name: name of instrument.
        lock: thread lock for preventing simultaneous calls to instrument.
        eurotherm: Eurotherm driver class.
    """

    def __init__(
        self, port: str, address: int, data_label: str, name: str = "Eurotherm3216"
    ) -> None:
        """Initialize Eurotherm data label, name, and connection parameters.

        Converts port 'ASRL##::INSTR' to form 'COM##' if necessary.

        Args:
            port: string name of port, e.g. `COM1` or `ASRL1::INSTR`.
            address: integer address of Eurotherm.
            data_label: label for DataTuple. :meth:`get_data` returns temperature, and
                corresponding label should match entry in DATA_COLUMNS of calling
                procedure class.
            name: name of instrument.
        """
        self.eurotherm = None
        self._finished: bool = False
        if "COM" not in port:
            port = port.replace("ASRL", "COM").replace("::INSTR", "")
        self._port = port
        self._address = address
        super().__init__(data_label, name)

    def connect(self) -> None:
        """Connect to Eurotherm."""
        self._connected = True

    def set_parameters(
        self, target_temperature: float, ramp_rate: float, dwell_time: float
    ) -> None:
        """Set Eurotherm program parameters.

        Args:
            target_temperature: target temperature in deg C.
            ramp_rate: ramp rate in C/min.
            dwell_time: dwell time in minutes.
        """
        self._finished = False
        self._parameters = (target_temperature, ramp_rate, dwell_time)

    def start(self) -> None:
        """End any active program, ramp to setpoint and dwell.

        Raises:
            NupylabError if `start` method is called before `set_parameters`.
        """
        if self._parameters is None:
            raise NupylabError(
                f"`{self.__class__.__name__}` method `set_parameters` "
                "must be called before calling its `start` method."
            )
        self._parameters = None

    def get_data(self) -> DataTuple:
        """Read heater temperature.

        Returns:
            DataTuple with current temperature.
        """
        temperature: float = 10.0
        self._finished = True
        return DataTuple(self.data_label, temperature)

    @property
    def finished(self) -> bool:
        """Get whether Eurotherm program is finished. Read-only."""
        return self._finished

    def stop_measurement(self):
        """Stop Eurotherm measurement. Not implemented."""
        pass

    def shutdown(self):
        """Reset Eurotherm program and close serial connection."""
        pass

"""Adapts ROD-4 driver to NUPylab instrument class for use with NUPyLab GUIs."""


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
        self._parameters = None

    def get_data(self) -> List[DataTuple]:
        """Read flow for each MFC channel.

        Returns:
            tuple of four DataTuples with flow for each channel.
        """
        mfc: List[float] = [1.0, 2.0, 3.0, 4.0]
        return list(DataTuple(self.data_label[i], mfc[i]) for i in range(4))

    def stop_measurement(self) -> None:
        """Stop ROD-4 measurement. Not implemented."""
        pass

    def shutdown(self) -> None:
        """Shutdown ROD-4 gas flow and close serial connection."""
        pass

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
        self.keithley705 = None
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
            self.keithley705 = None
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
        # case 1: both are strings
        # if I were to do this again:
        # 1) string equlivelent to sequence of length 1
        # 2) need to throw if your NupylabInstrument gets something that is not a sequence

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
        data = [
            DataTuple("1: Temperature (degC)", 1),
            DataTuple("2: Temperature (degC)", 2),
            DataTuple("3: Temperature (degC)", 3),
        ]
        self._finished = True
        return data

    @property
    def finished(self) -> bool:
        """Get whether measurements on all channels are finished."""
        return self._finished

    def stop_measurement(self) -> None:
        """Stop measurement on Keithley 705. Clears channel dict."""
        self.channels.clear()

    def shutdown(self) -> None:
        """Close serial connection on Keithley 705."""
        pass


class Agilent4284A(NupylabInstrument):
    """Agilent 4284A instrument class. Abstracts driver for NUPyLab procedures.

    Attributes:
        data_label: labels for DataTuples.
        name: name of instrument.
        lock: thread lock for preventing simultaneous calls to instrument.
        agilent: Agilent 4284A driver class.
    """

    def __init__(
        self,
        port: str,
        data_label: Sequence[str],
        name: str = "Agilent 4284A",
    ) -> None:
        """Initialize Agilent data labels, name, and connection parameters.

        Args:
            port: string name of port, e.g. `GPIB::1::INSTR`.
            data_label: labels for DataTuples. :meth:`get_data` returns frequency,
                Z_re, and -Z_im, and corresponding labels should match entries in
                DATA_COLUMNS.
            name: name of instrument.

        Raises:
            ValueError: if `data_label` does not contain 3 entries.
        """
        if len(data_label) != 3:
            raise ValueError("Agilent 4284A data_label must be sequence of length 3.")
        self.agilent = None
        self._port = port
        self._finished: bool = False
        self._freq_list = None
        self._eis_condition = None
        super().__init__(data_label, name)

    def connect(self) -> None:
        """Connect to Agilent 4284A."""
        with self.lock:
            self._connected = True

    def set_parameters(
        self,
        maximum_frequency: float,
        minimum_frequency: float,
        amplitude: float,
        points_per_decade: int,
        technique: str,
        eis_condition: Callable[[], bool],
    ) -> None:
        """Set eis measurement parameters.

        Args:
            maximum_frequency: maximum eis frequency in Hz.
            minimum_frequency: minimum eis frequency in Hz.
            amplitude: eis amplitude in Volt or Amp, depending on whether technique is
                PEIS or GEIS.
            points_per_decade: eis frequency points per decade.
            technique: eis technique to run, must be `PEIS` or `GEIS`.
            eis_condition: function indicating whether to begin eis measurement.

        Raises:
            KeyError: if `technique` is not supported.
        """
        technique = technique.upper()
        if technique not in ("PEIS", "GEIS"):
            raise KeyError(f"Technique {technique} must be `PEIS` or `GEIS`.")     
        self._finished = False
        max_f_log = np.log10(maximum_frequency)
        min_f_log = np.log10(minimum_frequency)
        freq_steps: int = round((max_f_log - min_f_log) * points_per_decade) + 1
        self._freq_list = np.logspace(max_f_log, min_f_log, num=freq_steps)
        self._eis_condition = eis_condition
        self._parameters = True  # Placeholder just to indicate parameters are set.

    def start(self) -> None:
        """Prepare eis measurement. Verifies eis parameters were set.

        Raises:
            NupylabError: if `start` method is called before `set_parameters`.
        """
        if self._parameters is None:
            raise NupylabError(
                f"`{self.__class__.__name__}` method `set_parameters` "
                "must be called before calling its `start` method."
            )
        self._parameters = None

    def get_data(self) -> Optional[List[DataTuple]]:
        """Get eis data.

        Returns:
            DataTuples in the order of frequency, Z_re, and -Z_im if measuring eis,
            None otherwise
        """
        self._finished = True
        freq: list = [1, 2, 3]
        z_re: list = [1, 2, 3]
        z_im: list = [1, 2, 3]
        data = [
            DataTuple(self.data_label[0], freq),
            DataTuple(self.data_label[1], z_re),
            DataTuple(self.data_label[2], [-z for z in z_im]),
        ]
        return data

    @property
    def eis_condition(self) -> bool:
        """Get whether to begin eis measurement."""
        if self.finished:  # Prevents unnecessary function calls
            return False
        return self._eis_condition()

    @property
    def finished(self) -> bool:
        """Get whether eis measurement is finished."""
        return self._finished

    def stop_measurement(self) -> None:
        """Stop eis measurement. Not implemented."""

    def shutdown(self) -> None:
        """Disconnect from Agilent 4284A."""
        pass

class HP3478A(NupylabInstrument):
    """HP 3478A instrument class. Adapts driver to NUPyLab thermocouple sensor.

    Attributes:
        data_label: label for DataTuple.
        name: name of instrument.
        hp3478a: HP3478A driver class.
        cj_temp: cold junction temperature in Celsius.
        cj_flag: boolean indicating whether next voltage reading is cold junction
            voltage.
    """

    def __init__(
        self,
        port: str,
        data_label: str,
        name: str = "HP 3478A",
    ) -> None:
        """Initialize HP 3478A data labels, name, and connection parameters.

        Args:
            port: string name of port, e.g. `GPIB::1`
            data_label: label for DataTuple, should match entry in DATA_COLUMNS of
                calling procedure class.
            name: name of instrument.
        """
        self._port: str = port
        self.cj_temp: float = 23
        self.cj_flag: bool = False
        self.hp3478a = None
        self._tc_type: str = "K"
        super().__init__(data_label, name)

    def connect(self) -> None:
        """Connect to HP 3478A."""
        self._connected = True

    @property
    def tc_type(self) -> str:
        """Control thermocouple type.

        Valid options are `B`, `E`, `J`, `K`, `N`, `R`, `S`, or `T`.
        """
        return self._tc_type

    @tc_type.setter
    def tc_type(self, tc_type: str) -> None:
        tc_type = tc_type.upper()
        if tc_type not in ("B", "E", "J", "K", "N", "R", "S", "T"):
            raise ValueError(f"Invalid thermocouple type: `{tc_type}`.")
        self._tc_type = tc_type

    def start(self) -> None:
        """Start multimeter measurement. Not implemented."""

    def get_data(self) -> Optional[DataTuple]:
        """Read thermocouple temperature.

        Returns:
            DataTuple with thermocouple temperature in Celsius.
        """
        temp = 4
        return DataTuple(self.data_label, temp)

    def stop_measurement(self) -> None:
        """Stop measurement on HP 3478A. Not implemented."""

    def shutdown(self) -> None:
        """Close serial connection on HP 3478A."""
        pass

