"""Adapts Eurotherm3216 driver to NUPylab instrument class for use with NUPyLab GUIs."""

import logging
import time

from nupylab.drivers import eurotherm3216
from nupylab.utilities import DataTuple, NupylabError
from nupylab.utilities.nupylab_instrument import NupylabInstrument

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

_control_log = logging.getLogger('nupylab.instrument_control')


class Eurotherm3216(NupylabInstrument):
    """Eurotherm 3216 instrument class. Abstracts driver for NUPyLab procedures."""

    def __init__(
        self, port: str, data_label: str, name: str = "Eurotherm3216"
    ) -> None:
        self.eurotherm = None
        self._finished: bool = False
        self._panel = None
        self._target_temperature: float = 0.0
        if "COM" not in port:
            port = port.replace("ASRL", "COM").replace("::INSTR", "")
        self._port = port
        self._address = 1
        super().__init__(data_label, name)

    def connect(self) -> None:
        """Connect to Eurotherm and verify it responds."""
        with self.lock:
            self.eurotherm = eurotherm3216.Eurotherm3216(self._port, self._address)
            time.sleep(0.5)
            # Verify instrument responds before declaring connected
            _ = self.eurotherm.process_value
            self._connected = True

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
        self,
        target_temperature: float,
        ramp_rate: float,
        dwell_time: float,
    ) -> None:
        self._finished = False
        self._target_temperature = target_temperature
        self._parameters = (target_temperature, ramp_rate, dwell_time)

    def start(self) -> None:
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
            self.eurotherm.segments[-1].target_setpoint = target_temperature
            self.eurotherm.segments[-1].ramp_rate = ramp_rate
            self.eurotherm.segments[-1].dwell = dwell_time * 60
            self.eurotherm.program_status = "run"
            self._parameters = None

    def get_data(self) -> DataTuple:
        with self.lock:
            temperature: float = self.eurotherm.process_value
            self._finished = self.eurotherm.program_status in ("reset", "end")
        return DataTuple(self.data_label, temperature)

    @property
    def finished(self) -> bool:
        return self._finished

    def stop_measurement(self):
        pass

    def shutdown(self):
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

            def __init__(self):
                super().__init__("Eurotherm 3216 — Furnace")
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
                        "Eurotherm program started: target=%.1f°C, ramp=%.1f°C/min, dwell=%.1fmin",
                        self.target_temp.value(), self.ramp_rate.value(),
                        self.dwell_time.value()
                    )
                except Exception as e:
                    self.status_label.setText(f"Status: Error — {e}")
                    _control_log.error("Eurotherm start failed: %s", e)

            def stop_program(self):
                self._abort_if_needed()
                try:
                    instrument.eurotherm.program_status = "reset"
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
                self.temp_label.setText(f"Current Temp: {v:.1f} °C")
                self.live_plot.add_point(v)
                if instrument.finished and self._program_started:
                    self.status_label.setText("Status: Program complete")
                    self._program_started = False
                    # Don't stop timer — keep showing temperature

        return EurothermPanel()