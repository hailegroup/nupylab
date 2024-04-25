"""Adapts Eurotherm2200 driver to NUPylab instrument class for use with NUPyLab GUIs."""

from nupylab.drivers import eurotherm2200
from nupylab.utilities import DataTuple, NupylabError
from ..nupylab_instrument import NupylabInstrument


class Eurotherm2200(NupylabInstrument):
    """Eurotherm 2200 instrument class. Abstracts driver for NUPyLab procedures.

    Attributes:
        data_label: label for DataTuple.
        name: name of instrument.
        eurotherm: Eurotherm driver class.
    """

    def __init__(
        self, port: str, address: int, data_label: str, name: str = "Eurotherm2200"
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
        if not isinstance(data_label, str):
            raise TypeError("Eurotherm 2200 data label must be string.")
        if "COM" not in port:
            port = port.replace("ASRL", "COM").replace("::INSTR", "")
        self._port = port
        self._address = address
        super().__init__(data_label, name)

    def connect(self) -> None:
        """Connect to Eurotherm."""
        self.eurotherm = eurotherm2200.Eurotherm2200(self._port, self._address)
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
        target_temperature, ramp_rate, dwell_time = self._parameters
        self.eurotherm.program_status = "reset"
        self.eurotherm.active_setpoint = 1
        self.eurotherm.end_type = "dwell"
        self.eurotherm.setpoint_rate_limit = ramp_rate
        self.eurotherm.setpoint2 = target_temperature
        # Dwell must be non-zero for program to work, add one second
        self.eurotherm.dwell_time = dwell_time * 60 + 1
        self.eurotherm.program_status = "run"
        self._parameters = None

    def get_data(self) -> DataTuple:
        """Read heater temperature.

        Returns:
            DataTuple with current temperature.
        """
        temperature: float = self.eurotherm.process_value
        return DataTuple(self.data_label, temperature)

    @property
    def finished(self) -> bool:
        """Get whether Eurotherm program is finished. Read-only."""
        status: str = self.eurotherm.program_status
        return status in ("off", "end")

    def stop_measurement(self):
        """Stop Eurotherm measurement. Not implemented."""

    def shutdown(self):
        """Reset Eurotherm program and close serial connection."""
        self.eurotherm.program_status = "reset"
        self.eurotherm.serial.close()
