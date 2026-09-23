"""Adapts Eurotherm2200 driver to NUPylab instrument class for use with NUPyLab GUIs."""

import logging
import time

from nupylab.drivers import eurotherm2200
from nupylab.utilities import DataTuple, NupylabError
from nupylab.utilities.nupylab_instrument import NupylabInstrument

_control_log = logging.getLogger('nupylab.instrument_control')


class Eurotherm2200(NupylabInstrument):
    """Eurotherm 2200 instrument class. Abstracts driver for NUPyLab procedures.

    Attributes:
        data_label: label for DataTuple.
        name: name of instrument.
        lock: thread lock for preventing simultaneous calls to instrument.
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
        self._finished: bool = False
        self.eurotherm = None
        self._panel = None
        if not isinstance(data_label, str):
            raise TypeError("Eurotherm 2200 data label must be string.")
        if "COM" not in port:
            port = port.replace("ASRL", "COM").replace("::INSTR", "")
        self._port = port
        self._address = address
        super().__init__(data_label, name)

    def connect(self) -> None:
        """Connect to Eurotherm."""
        with self.lock:
            self.eurotherm = eurotherm2200.Eurotherm2200(self._port, self._address)
            self._connected = True

    def set_connection(self, port: str, address: int) -> None:
        """Set port and address used by the next call to :meth:`connect`.

        Args:
            port: string name of port, e.g. `COM1` or `ASRL1::INSTR`.
            address: integer address of Eurotherm.
        """
        if "COM" not in port:
            port = port.replace("ASRL", "COM").replace("::INSTR", "")
        self._port = port
        self._address = address

    def disconnect(self) -> None:
        """Disconnect from Eurotherm."""
        if self._panel is not None and self._connected:
            try:
                self._panel.timer.stop()
                if self._panel._worker and self._panel._worker.isRunning():
                    self._panel._worker.wait(100)
            except Exception:
                pass
        with self.lock:
            if self.eurotherm is not None:
                try:
                    self.eurotherm.serial.close()
                except Exception:
                    pass
                self.eurotherm = None
            self._connected = False
        time.sleep(0.3)

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
        target_temperature, ramp_rate, dwell_time = self._parameters
        with self.lock:
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
        with self.lock:
            temperature: float = self.eurotherm.process_value
            self._finished = self.eurotherm.program_status in ("off", "end")
        return DataTuple(self.data_label, temperature)

    @property
    def finished(self) -> bool:
        """Get whether Eurotherm program is finished. Read-only."""
        return self._finished

    def stop_measurement(self):
        """Stop Eurotherm measurement. Not implemented."""

    def shutdown(self):
        """Reset Eurotherm program and close serial connection."""
        with self.lock:
            self.eurotherm.program_status = "reset"
            self.eurotherm.serial.close()

    def control_widget(self, abort_callback=None):
        """Return a Qt control panel for this instrument."""
        from pymeasure.display.Qt import QtWidgets, QtCore
        from nupylab.utilities.instrument_control import LivePlotWidget

        instrument = self

        class Worker(QtCore.QThread):
            result = QtCore.Signal(float)

            def run(self):
                try:
                    data = instrument.get_data()
                    self.result.emit(data.value)
                except Exception:
                    pass

        class EurothermPanel(QtWidgets.QGroupBox):
            plot_title = "Furnace Temperature"
            instrument_name = "Eurotherm"
            record_columns = ["Furnace Temperature (degC)"]
            data_recorded = QtCore.Signal(list)

            def __init__(self):
                super().__init__("Eurotherm 2200 — Furnace")
                self._worker = None
                self._program_started = False
                self._abort_callback = abort_callback
                self.live_plot = LivePlotWidget(
                    "Furnace Temperature", "Temperature (°C)", n_traces=1
                )
                self._setup_ui()
                self.timer = QtCore.QTimer()
                self.timer.timeout.connect(self.update_temp)
                instrument._panel = self

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
                self.dwell_time.setValue(1)
                layout.addRow("Dwell Time:", self.dwell_time)

                btn_layout = QtWidgets.QHBoxLayout()
                self.connect_btn = QtWidgets.QPushButton("Connect")
                self.start_btn = QtWidgets.QPushButton("Start Program")
                self.stop_btn = QtWidgets.QPushButton("Stop Program")
                self.disconnect_btn = QtWidgets.QPushButton("Disconnect")
                self.connect_btn.clicked.connect(self.connect_instrument)
                self.start_btn.clicked.connect(self.start_program)
                self.stop_btn.clicked.connect(self.stop_program)
                self.disconnect_btn.clicked.connect(self.disconnect_instrument)
                btn_layout.addWidget(self.connect_btn)
                btn_layout.addWidget(self.start_btn)
                btn_layout.addWidget(self.stop_btn)
                btn_layout.addWidget(self.disconnect_btn)
                layout.addRow(btn_layout)

                self.temp_label = QtWidgets.QLabel("Current Temp: —")
                self.status_label = QtWidgets.QLabel("Status: Not connected")
                layout.addRow(self.temp_label)
                layout.addRow(self.status_label)

                self.setLayout(layout)

            def _abort_if_needed(self):
                if self._abort_callback:
                    self._abort_callback()

            def connect_instrument(self):
                self._abort_if_needed()
                try:
                    instrument.connect()
                    self.status_label.setText("Status: Connected")
                    self.timer.start(2000)
                    _control_log.info("Eurotherm connected on %s", instrument._port)
                except Exception as e:
                    self.status_label.setText(f"Status: Error — {e}")
                    _control_log.error("Eurotherm connect failed: %s", e)

            def disconnect_instrument(self):
                self._abort_if_needed()
                try:
                    self.timer.stop()
                    if self._worker and self._worker.isRunning():
                        self._worker.wait(2000)
                    instrument.disconnect()
                    self.status_label.setText("Status: Disconnected")
                    self.temp_label.setText("Current Temp: —")
                    _control_log.info("Eurotherm disconnected")
                except Exception as e:
                    self.status_label.setText(f"Status: Error — {e}")

            def start_program(self):
                self._abort_if_needed()
                try:
                    if not instrument.connected:
                        instrument.connect()
                    instrument.set_parameters(
                        self.target_temp.value(),
                        self.ramp_rate.value(),
                        self.dwell_time.value(),
                    )
                    instrument.start()
                    self._program_started = True
                    self.live_plot.clear()
                    if not self.timer.isActive():
                        self.timer.start(2000)
                    self.status_label.setText("Status: Program running")
                    _control_log.info(
                        "Eurotherm program started: target=%.1f°C, "
                        "ramp=%.1f°C/min, dwell=%.1fmin",
                        self.target_temp.value(), self.ramp_rate.value(),
                        self.dwell_time.value()
                    )
                except Exception as e:
                    self.status_label.setText(f"Status: Error — {e}")
                    _control_log.error("Eurotherm start failed: %s", e)

            def stop_program(self):
                self._abort_if_needed()
                try:
                    with instrument.lock:
                        instrument.eurotherm.program_status = "reset"
                    self._program_started = False
                    self.status_label.setText("Status: Stopped")
                    _control_log.info("Eurotherm program stopped")
                except Exception as e:
                    self.status_label.setText(f"Status: Error — {e}")

            def update_temp(self):
                if not instrument.connected:
                    return
                if self._worker and self._worker.isRunning():
                    return
                self._worker = Worker()

                def on_result(v):
                    self.temp_label.setText(f"Current Temp: {v:.2f} °C")
                    self.live_plot.add_point(v)
                    self.data_recorded.emit([v])
                    if instrument.finished and self._program_started:
                        self.status_label.setText("Status: Program complete")
                        self._program_started = False

                self._worker.result.connect(on_result)
                self._worker.start()

        return EurothermPanel()
