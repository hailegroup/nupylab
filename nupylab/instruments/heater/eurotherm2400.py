"""Adapts Eurotherm2400 driver to NUPylab instrument class for use with NUPyLab GUIs."""

from nupylab.drivers import eurotherm2400
from nupylab.utilities import DataTuple, NupylabError
from ..nupylab_instrument import NupylabInstrument


class Eurotherm2400(NupylabInstrument):
    """Eurotherm 2400 instrument class. Abstracts driver for NUPyLab procedures.

    Attributes:
        data_label: label for DataTuple.
        name: name of instrument.
        lock: thread lock for preventing simultaneous calls to instrument.
        eurotherm: Eurotherm driver class.
    """

    def __init__(
        self, port: str, address: int, data_label: str, name: str = "Eurotherm2400"
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
        with self.lock:
            self.eurotherm = eurotherm2400.Eurotherm2400(self._port, self._address)
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
        with self.lock:
            target_temperature, ramp_rate, dwell_time = self._parameters
            self.eurotherm.program_status = "reset"
            self.eurotherm.current_program = 1
            self.eurotherm.programs[1].refresh()

            self.eurotherm.programs[1].segments[1]["segment type"] = "ramp rate"
            self.eurotherm.programs[1].segments[1]["rate"] = ramp_rate
            self.eurotherm.programs[1].segments[1]["target setpoint"] = target_temperature

            self.eurotherm.programs[1].segments[2]["segment type"] = "dwell"
            self.eurotherm.programs[1].segments[2]["duration"] = dwell_time * 60

            self.eurotherm.programs[1].segments[3]["segment type"] = "end"
            self.eurotherm.programs[1].segments[3]["end type"] = "dwell"

            self.eurotherm.program_status = "run"
            self._parameters = None

    def get_data(self) -> DataTuple:
        """Read heater temperature.

        Returns:
            DataTuple with current temperature.
        """
        with self.lock:
            temperature: float = self.eurotherm.process_value
            self._finished = self.eurotherm.program_status != "run"
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
        with self.lock:
            self.eurotherm.program_status = "reset"
            self.eurotherm.serial.close()
