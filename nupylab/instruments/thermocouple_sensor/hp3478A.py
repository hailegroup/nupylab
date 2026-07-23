"""Adapts HP 3478A driver to NUPylab instrument class for use with NUPyLab GUIs."""

from typing import Optional

from pymeasure.instruments.hp import hp3478A
from nupylab.utilities import DataTuple, thermocouples
from nupylab.utilities.nupylab_instrument import NupylabInstrument


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
        resolution: int,
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
        self.cj_temp: float = 40
        self.cj_flag: bool = False
        self.hp3478a: Optional[hp3478A.HP3478A] = None
        self._tc_type: str = "T"
        self._last_temp: float = 40
        self._resolution: int = resolution
        super().__init__(data_label, name)

    def connect(self) -> None:
        """Connect to HP 3478A."""
        try:    
            self.hp3478a = hp3478A.HP3478A(self._port)
            self.hp3478a.reset()
            self.hp3478a.mode = "DCV"
            self.hp3478a.range = 0.03
            self.hp3478a.resolution = self._resolution
            self._connected = True
        except Exception as e:
            pass

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
        self.cj_flag = True

    def get_data(self) -> Optional[DataTuple]:
        """Read thermocouple temperature.

        Returns:
            DataTuple with thermocouple temperature in Celsius.
        """
        try:
            with self.lock:
                voltage: float = self.hp3478a.measure_DCV
        except Exception as e:
            return DataTuple(self.data_label, self._last_temp)
        try:
            temp: float = thermocouples.calculate_temperature(
                (voltage) * 1000, self.tc_type, self.cj_temp
            )
            self._last_temp = temp
            return DataTuple(self.data_label, temp)
        except ValueError as e:
            return DataTuple(self.data_label, self._last_temp)

    def stop_measurement(self) -> None:
        """Stop measurement on HP 3478A. Not implemented."""

    def shutdown(self) -> None:
        """Close serial connection on HP 3478A."""
        self.hp3478a.shutdown()
