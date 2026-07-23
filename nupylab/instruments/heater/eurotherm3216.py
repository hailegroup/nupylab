"""Adapts Eurotherm3216 driver to NUPylab instrument class for use with NUPyLab GUIs."""

from nupylab.drivers import eurotherm3216
from nupylab.utilities import DataTuple, NupylabError
from nupylab.utilities.nupylab_instrument import NupylabInstrument


class Eurotherm3216(NupylabInstrument):
    """Eurotherm 3216 instrument class. Abstracts driver for NUPyLab procedures.

    Attributes:
        data_label: label for DataTuples.
        name: name of instrument.
        lock: thread lock for preventing simultaneous calls to instrument.
        eurotherm: Eurotherm driver class.
    """

    def __init__(
        self, port: str, data_label: str, name: str = "Eurotherm3216"
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
        self._address = 1
        super().__init__(data_label, name)

    def connect(self) -> None:
        """Connect to Eurotherm."""
        with self.lock:
            self.eurotherm = eurotherm3216.Eurotherm3216(self._port, self._address)
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
            self.eurotherm.end_type = "dwell"
            for segment in self.eurotherm.segments:
                segment.clear()
            # Eurotherm 3216 runs all 8 segments, so only the final segment matters
            self.eurotherm.segments[-1].target_setpoint = target_temperature
            self.eurotherm.segments[-1].ramp_rate = ramp_rate
            self.eurotherm.segments[-1].dwell = dwell_time * 60
            self.eurotherm.program_status = "run"
            self._parameters = None

    def get_data(self) -> DataTuple:
        """Read heater temperature.

        Returns:
            DataTuple with current temperature.
        """
        with self.lock:
            temperature: float = self.eurotherm.process_value
            self._finished = self.eurotherm.program_status in ("reset", "end")
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

    def control_widget(self):
        """Return a Qt control panel for this instrument."""
        from pymeasure.display.Qt import QtWidgets, QtCore

        instrument = self

        class EurothermPanel(QtWidgets.QGroupBox):
            def __init__(self):
                super().__init__("Eurotherm 3216 — Furnace")
                self._setup_ui()
                self.timer = QtCore.QTimer()
                self.timer.timeout.connect(self.update_temp)
                self.timer.start(2000)

            def _setup_ui(self):
                layout = QtWidgets.QFormLayout()

                self.target_temp = QtWidgets.QDoubleSpinBox()
                self.target_temp.setRange(0, 1200)
                self.target_temp.setSuffix(" °C")
                self.target_temp.setValue(25)
                layout.addRow("Target Temperature:", self.target_temp)

                self.ramp_rate = QtWidgets.QDoubleSpinBox()
                self.ramp_rate.setRange(0.1, 100)
                self.ramp_rate.setSuffix(" °C/min")
                self.ramp_rate.setValue(5)
                layout.addRow("Ramp Rate:", self.ramp_rate)

                self.dwell_time = QtWidgets.QDoubleSpinBox()
                self.dwell_time.setRange(0, 9999)
                self.dwell_time.setSuffix(" min")
                self.dwell_time.setValue(10)
                layout.addRow("Dwell Time:", self.dwell_time)

                btn_layout = QtWidgets.QHBoxLayout()
                self.start_btn = QtWidgets.QPushButton("Start Program")
                self.stop_btn = QtWidgets.QPushButton("Stop Program")
                self.start_btn.clicked.connect(self.start_program)
                self.stop_btn.clicked.connect(self.stop_program)
                btn_layout.addWidget(self.start_btn)
                btn_layout.addWidget(self.stop_btn)
                layout.addRow(btn_layout)

                self.temp_label = QtWidgets.QLabel("Current Temp: —")
                self.status_label = QtWidgets.QLabel("Status: —")
                layout.addRow(self.temp_label)
                layout.addRow(self.status_label)
                self.setLayout(layout)

            def start_program(self):
                try:
                    if not instrument.connected:
                        instrument.connect()
                    instrument.set_parameters(
                        self.target_temp.value(),
                        self.ramp_rate.value(),
                        self.dwell_time.value()
                    )
                    instrument.start()
                    self.status_label.setText("Status: Program running")
                except Exception as e:
                    self.status_label.setText(f"Status: Error — {e}")

            def stop_program(self):
                try:
                    instrument.eurotherm.program_status = "reset"
                    self.status_label.setText("Status: Stopped")
                except Exception as e:
                    self.status_label.setText(f"Status: Error — {e}")

            def update_temp(self):
                if not instrument.connected:
                    return
                try:
                    data = instrument.get_data()
                    self.temp_label.setText(f"Current Temp: {data.value:.1f} °C")
                except Exception:
                    pass

        return EurothermPanel()

        