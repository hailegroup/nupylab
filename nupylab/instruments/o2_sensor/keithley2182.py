"""Adapts Keithley 2182 driver to NUPylab instrument class for use with NUPyLab GUIs."""

from typing import Sequence, List

from pymeasure.instruments.keithley import keithley2182
from nupylab.utilities import DataTuple
from nupylab.utilities.nupylab_instrument import NupylabInstrument


class Keithley2182(NupylabInstrument):
    """Keithley 2182 pO2 sensor instrument class. Abstracts driver for NUPyLab procedures.

    Attributes:
        data_label: labels for DataTuples.
        name: name of instrument.
        lock: thread lock for preventing simultaneous calls to instrument.
        keithley: Keithley 2182 driver class.
    """

    def __init__(
        self,
        port: str,
        po2_intercept: float,
        po2_slope: float,
        data_label: Sequence[str],
        name: str = "Keithley 2182",
    ) -> None:
        """Initialize Keithley data labels, name, and connection parameters.

        Args:
            port: string name of port, e.g. `ASRL::1::INSTR`.
            po2_intercept: calibrated pO2 sensor voltage vs temperature intercept.
            po2_slope: calibrated pO2 sensor voltage vs temperature slope.
            data_label: labels for DataTuples. :meth:`get_data` returns temperature,
                pO2, and pO2 sensor voltage, and corresponding labels should match
                entries in DATA_COLUMNS.
            name: name of instrument.

        Raises:
            ValueError if `data_label` does not contain 3 entries.

        """
        self._ch_1_first = None
        self.keithley = None
        if len(data_label) != 3:
            raise ValueError("Keithley 2182 data_label must be sequence of length 3.")
        self._intercept: float = po2_intercept
        self._slope: float = po2_slope
        self._port = port
        super().__init__(data_label, name)

    def connect(self) -> None:
        """Connect to Keithley 2182 nanovoltmeter."""
        with self.lock:
            self.keithley: keithley2182.Keithley2182 = keithley2182.Keithley2182(self._port)
            self.keithley.clear()
            self.keithley.reset()
            self.keithley.thermocouple = "S"
            self.keithley.ch_1.setup_voltage()
        self._ch_1_first: bool = True
        self._connected = True

    def start(self) -> None:
        """Start pO2 measurement. Not implemented."""

    def get_data(self) -> List[DataTuple]:
        """Get po2 sensor data.

        Returns:
            DataTuples in the order of sensor temperature in deg C, po2 in atm, and
            sensor voltage in Volts.
        """
        voltage: float
        temperature: float
        po2: float
        # Toggle between which channel is measured first to speed up measurement cycle
        with self.lock:
            if self._ch_1_first:
                voltage = -1 * self.keithley.voltage
                self.keithley.ch_2.setup_temperature()
                temperature = self.keithley.temperature
                self._ch_1_first = False
            else:
                temperature = self.keithley.temperature
                self.keithley.ch_1.setup_voltage()
                voltage = -1 * self.keithley.voltage
                self._ch_1_first = True
        po2 = 0.2095 * 10 ** (
            20158 * ((voltage - self._slope) / (temperature + 273.15) - self._intercept)
        )
        data = [
            DataTuple(self.data_label[0], temperature),
            DataTuple(self.data_label[1], po2),
            DataTuple(self.data_label[2], voltage),
        ]
        return data

    def stop_measurement(self) -> None:
        """Stop pO2 measurement. Not implemented."""

    def shutdown(self) -> None:
        """Disconnect from Keithley 2182."""
        with self.lock:
            self.keithley.adapter.close()
