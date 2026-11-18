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

    SHUTDOWN_TEMPERATURE = 20.0

    def __init__(
        self, port: str, data_label: str, name: str = "Eurotherm3216"
    ) -> None:
        self.eurotherm = None
        self._finished: bool = False
        self._panel = None
        self._target_temperature: float = 0.0
        self._autotuning: bool = False
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

    def set_connection(self, port: str, address: int = 1) -> None:
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
        self,
        target_temperature: float,
        ramp_rate: float,
        dwell_time: float,
    ) -> None:
        self._finished = False
        self._target_temperature = target_temperature
        self._parameters = (target_temperature, ramp_rate, dwell_time)

    def start(self) -> None:
        """End any active program, ramp to setpoint and dwell.

        Raises:
            NupylabError if `start` is called before `set_parameters`, or
            if autotune is currently running.
        """
        if self._autotuning:
            raise NupylabError(
                "Cannot start furnace program while autotune is running. "
                "Wait for autotune to complete before queuing experiments."
            )
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
            status = self.eurotherm.program_status
            if status in ("reset", "end"):
                if abs(temperature - self._target_temperature) < 2.0:
                    self._finished = True
        return DataTuple(self.data_label, temperature)

    @property
    def finished(self) -> bool:
        return self._finished

    def stop_measurement(self):
        pass

    def shutdown(self):
        """Close serial connection, leaving a completed program dwelling at setpoint.

        The program is only reset if it did not finish (e.g. experiment aborted or
        failed mid-ramp).
        """
        with self.lock:
            if not self._finished:
                self.eurotherm.program_status = "reset"
            self.eurotherm.serial.close()

    def get_status(self):
        """Read the values shown by the manual control panel.

        Returns:
            Tuple of process value in deg C, working setpoint in deg C, output
            power in percent, and program status.
        """
        with self.lock:
            return (
                self.eurotherm.process_value,
                self.eurotherm.working_setpoint,
                self.eurotherm.output_level,
                self.eurotherm.program_status,
            )

    def start_ramp_and_hold(
        self, target_temperature: float, ramp_rate: float
    ) -> None:
        """Ramp to a target temperature and hold there until stopped.

        Unlike :meth:`start`, which dwells for a set time and then ends, this
        holds the target indefinitely. It is what the manual control panel
        uses, and it does not require :meth:`set_parameters` first.

        Args:
            target_temperature: target temperature in deg C.
            ramp_rate: ramp rate in C/min.

        Raises:
            NupylabError if autotune is currently running.
        """
        if self._autotuning:
            raise NupylabError(
                "Cannot start furnace program while autotune is running. "
                "Wait for autotune to complete before starting a program."
            )
        self._finished = False
        with self.lock:
            self.eurotherm.program_status = "reset"
            # End type `dwell` holds the final setpoint indefinitely
            self.eurotherm.end_type = "dwell"
            for segment in self.eurotherm.segments:
                segment.clear()
            self.eurotherm.segments[-1].target_setpoint = target_temperature
            self.eurotherm.segments[-1].ramp_rate = ramp_rate
            self.eurotherm.segments[-1].dwell = 0
            self.eurotherm.program_status = "run"
        self._target_temperature = target_temperature

    def shutdown_to(
        self, temperature: float = None, ramp_rate: float = 5.0
    ) -> None:
        """Ramp the furnace to a safe temperature, then close the connection.

        The controller keeps running the ramp after the connection closes.

        Args:
            temperature: temperature to ramp to, defaults to
                :attr:`SHUTDOWN_TEMPERATURE`.
            ramp_rate: ramp rate in C/min.
        """
        if temperature is None:
            temperature = self.SHUTDOWN_TEMPERATURE
        self.start_ramp_and_hold(temperature, ramp_rate)
        self.disconnect()

    def control_widget(self, abort_callback=None):
        """Return a Qt control panel for this instrument."""
        from pymeasure.display.Qt import QtWidgets, QtCore
        from nupylab.utilities.instrument_control import LivePlotWidget

        instrument = self

        class Worker(QtCore.QThread):
            result = QtCore.Signal(object)

            def run(self):
                try:
                    self.result.emit(instrument.get_status())
                except Exception:
                    pass

        class EurothermPanel(QtWidgets.QGroupBox):
            plot_title = "Furnace Temperature"
            instrument_name = "Eurotherm"
            record_columns = ["Furnace Temperature (degC)"]
            data_recorded = QtCore.Signal(list)
            START_TEXT = "Start Program"
            STOP_TEXT = "Stop Program"
            # Program status words differ between controllers: the 2200 idles
            # at "off", the 2400 and 3216 at "reset", and the end-of-program
            # hold is "end", "dwell" or "complete" depending on the model.
            # A double click would otherwise start a program and stop it
            # again; a second toggle this soon after one lands is ignored.
            TOGGLE_GUARD_S = 1.0
            IDLE_STATES = ("reset", "off")
            HOLDING_STATES = ("end", "dwell", "complete")

            def __init__(self):
                super().__init__("Eurotherm 3216 — Furnace")
                self._worker = None
                self._program_started = False
                self._phase = None
                self._last_toggle = 0.0
                self._abort_callback = abort_callback
                self._autotune_setpoint = 0.0
                self.live_plot = LivePlotWidget(
                    "Furnace Temperature", "Temperature (°C)", n_traces=1
                )
                self._setup_ui()
                self.timer = QtCore.QTimer()
                self.timer.timeout.connect(self.update_temp)
                self._autotune_timer = QtCore.QTimer()
                self._autotune_timer.timeout.connect(self._check_autotune)
                instrument._panel = self

            def _setup_ui(self):
                layout = QtWidgets.QFormLayout()

                self.target_temp = QtWidgets.QDoubleSpinBox()
                self.target_temp.setRange(0, 1200)
                self.target_temp.setSuffix(" \u00b0C")
                self.target_temp.setValue(25)
                layout.addRow("Target Temperature:", self.target_temp)

                self.ramp_rate = QtWidgets.QDoubleSpinBox()
                self.ramp_rate.setRange(0.1, 100)
                self.ramp_rate.setSuffix(" \u00b0C/min")
                self.ramp_rate.setValue(5)
                layout.addRow("Ramp Rate:", self.ramp_rate)

                btn_layout = QtWidgets.QHBoxLayout()
                self.connect_btn = QtWidgets.QPushButton("Connect")
                self.program_btn = QtWidgets.QPushButton(self.START_TEXT)
                self.shutdown_btn = QtWidgets.QPushButton("Shut Down")
                self.connect_btn.clicked.connect(self.connect_instrument)
                self.program_btn.clicked.connect(self.toggle_program)
                self.shutdown_btn.clicked.connect(self.shutdown_instrument)
                btn_layout.addWidget(self.connect_btn)
                btn_layout.addWidget(self.program_btn)
                btn_layout.addWidget(self.shutdown_btn)
                layout.addRow(btn_layout)

                self.temp_label = QtWidgets.QLabel("Current Temp: \u2014")
                self.setpoint_label = QtWidgets.QLabel("Set Temp: \u2014")
                self.power_label = QtWidgets.QLabel("Power: \u2014")
                self.status_label = QtWidgets.QLabel("Status: Not connected")
                layout.addRow(self.temp_label)
                layout.addRow(self.setpoint_label)
                layout.addRow(self.power_label)
                layout.addRow(self.status_label)

                # Autotune section
                autotune_layout = QtWidgets.QHBoxLayout()
                self.autotune_temp = QtWidgets.QDoubleSpinBox()
                self.autotune_temp.setRange(0, 1200)
                self.autotune_temp.setSuffix(" \u00b0C")
                self.autotune_temp.setValue(200)
                self.autotune_temp.setToolTip(
                    "Temperature at which to run autotune.\n"
                    "Set close to your normal operating temperature.\n"
                    "Autotune takes 20-30 minutes and locks all experiments."
                )
                self.autotune_btn = QtWidgets.QPushButton("Start Autotune")
                self.autotune_btn.setStyleSheet(
                    "QPushButton { background-color: #cc6600; color: white; }"
                    "QPushButton:disabled { background-color: #888888; }"
                )
                self.autotune_btn.clicked.connect(self.start_autotune)
                self.autotune_status = QtWidgets.QLabel("Autotune: Off")
                autotune_layout.addWidget(QtWidgets.QLabel("Autotune Temp:"))
                autotune_layout.addWidget(self.autotune_temp)
                autotune_layout.addWidget(self.autotune_btn)
                autotune_layout.addWidget(self.autotune_status)
                layout.addRow(autotune_layout)

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
                    self.status_label.setText(f"Status: Error \u2014 {e}")
                    _control_log.error("Eurotherm connect failed: %s", e)

            def _clear_readouts(self):
                self.temp_label.setText("Current Temp: \u2014")
                self.setpoint_label.setText("Set Temp: \u2014")
                self.power_label.setText("Power: \u2014")

            def _set_program_running(self, running):
                """Keep the single program button in step with the controller."""
                self._program_started = running
                self.program_btn.setText(
                    self.STOP_TEXT if running else self.START_TEXT
                )

            def disconnect_instrument(self):
                self._abort_if_needed()
                try:
                    self.timer.stop()
                    if self._worker and self._worker.isRunning():
                        self._worker.wait(2000)
                    instrument.disconnect()
                    self.status_label.setText("Status: Disconnected")
                    self._clear_readouts()
                    _control_log.info("Eurotherm disconnected")
                except Exception as e:
                    self.status_label.setText(f"Status: Error \u2014 {e}")

            def shutdown_instrument(self):
                """Ramp the furnace to a safe temperature, then disconnect."""
                self._abort_if_needed()
                try:
                    self.timer.stop()
                    if self._worker and self._worker.isRunning():
                        self._worker.wait(2000)
                    if not instrument.connected:
                        instrument.connect()
                    instrument.shutdown_to(ramp_rate=self.ramp_rate.value())
                    self._set_program_running(False)
                    self._clear_readouts()
                    self.status_label.setText(
                        "Status: Ramping to %.0f\u00b0C, disconnected"
                        % instrument.SHUTDOWN_TEMPERATURE
                    )
                    _control_log.info(
                        "Eurotherm shutting down: ramping to %.0f\u00b0C at "
                        "%.1f\u00b0C/min, then disconnecting",
                        instrument.SHUTDOWN_TEMPERATURE, self.ramp_rate.value()
                    )
                except Exception as e:
                    self.status_label.setText(f"Status: Error \u2014 {e}")
                    _control_log.error("Eurotherm shutdown failed: %s", e)

            def toggle_program(self):
                """Start the ramp, or stop it if one is already running.

                Ignores a repeat click that lands within
                :attr:`TOGGLE_GUARD_S` of the previous one finishing, so that
                a double click cannot start a program and stop it again. The
                window is measured from the end of the previous action, since
                the serial write blocks the event loop while it runs.
                """
                now = time.monotonic()
                if now - self._last_toggle < self.TOGGLE_GUARD_S:
                    _control_log.info(
                        "Ignoring repeat click on the program button"
                    )
                    return
                try:
                    if self._program_started:
                        self.stop_program()
                    else:
                        self.start_program()
                finally:
                    self._last_toggle = time.monotonic()

            def start_program(self):
                self._abort_if_needed()
                try:
                    if not instrument.connected:
                        instrument.connect()
                    instrument.start_ramp_and_hold(
                        self.target_temp.value(), self.ramp_rate.value()
                    )
                    self._set_program_running(True)
                    self.live_plot.clear()
                    if not self.timer.isActive():
                        self.timer.start(2000)
                    self.status_label.setText("Status: Ramping to setpoint")
                    _control_log.info(
                        "Eurotherm ramping to %.1f\u00b0C at %.1f\u00b0C/min, "
                        "then holding",
                        self.target_temp.value(), self.ramp_rate.value()
                    )
                except Exception as e:
                    self.status_label.setText(f"Status: Error \u2014 {e}")
                    _control_log.error("Eurotherm start failed: %s", e)

            def stop_program(self):
                self._abort_if_needed()
                try:
                    with instrument.lock:
                        instrument.eurotherm.program_status = "reset"
                    self._set_program_running(False)
                    self.status_label.setText("Status: Stopped")
                    _control_log.info("Eurotherm program stopped")
                except Exception as e:
                    self.status_label.setText(f"Status: Error \u2014 {e}")

            def start_autotune(self):
                """Start autotune at the specified temperature."""
                if not instrument.connected:
                    self.autotune_status.setText("Autotune: Connect first")
                    return
                try:
                    at_temp = self.autotune_temp.value()
                    self._autotune_setpoint = at_temp
                    with instrument.lock:
                        # Set SP1 to autotune temperature
                        instrument.eurotherm.setpoint1 = at_temp
                        # Trigger autotune — ATUNE at float parameter address 270
                        instrument.eurotherm.write_register(270, 1)  # 1 = on
                    instrument._autotuning = True
                    # Lock all controls during autotune
                    self.connect_btn.setEnabled(False)
                    self.program_btn.setEnabled(False)
                    self.shutdown_btn.setEnabled(False)
                    self.autotune_btn.setEnabled(False)
                    self.autotune_temp.setEnabled(False)
                    self.autotune_status.setText(
                        f"Autotuning at {at_temp:.0f}\u00b0C... DO NOT interrupt"
                    )
                    self.autotune_status.setStyleSheet(
                        "color: red; font-weight: bold;"
                    )
                    # Poll every 10 seconds to check if autotune completed
                    self._autotune_timer.start(10000)
                    _control_log.info(
                        "Autotune started at %.0f\u00b0C — experiments locked",
                        at_temp
                    )
                except Exception as e:
                    instrument._autotuning = False
                    self.autotune_status.setText(f"Autotune failed: {e}")
                    _control_log.error("Autotune start failed: %s", e)

            def _check_autotune(self):
                """Poll autotune status every 10s and unlock when done."""
                if not instrument.connected:
                    self._autotune_timer.stop()
                    instrument._autotuning = False
                    self._unlock_after_autotune()
                    return
                try:
                    with instrument.lock:
                        # ATUNE: 1.0 = running, 0.0 = done
                        atune_val = instrument.eurotherm.read_register(270)
                        temp = instrument.eurotherm.process_value
                    if atune_val == 0:
                        self.autotune_status.setText(
                            f"Autotune complete! Temp: {temp:.2f}\u00b0C. "
                            "New PID values saved."
                        )
                        self.autotune_status.setStyleSheet(
                            "color: green; font-weight: bold;"
                        )
                        self._autotune_timer.stop()
                        instrument._autotuning = False
                        self._unlock_after_autotune()
                        _control_log.info(
                            "Autotune complete at %.1f\u00b0C. "
                            "New PID values saved to Eurotherm.", temp
                        )
                    else:
                        self.autotune_status.setText(
                            f"Autotuning... {temp:.1f}\u00b0C / "
                            f"{self._autotune_setpoint:.0f}\u00b0C target"
                        )
                except Exception as e:
                    _control_log.warning("Autotune status check failed: %s", e)

            def _unlock_after_autotune(self):
                """Re-enable all controls after autotune completes."""
                self.connect_btn.setEnabled(True)
                self.program_btn.setEnabled(True)
                self.shutdown_btn.setEnabled(True)
                self.autotune_btn.setEnabled(True)
                self.autotune_temp.setEnabled(True)

            def update_temp(self):
                if not instrument.connected:
                    return
                if self._worker and self._worker.isRunning():
                    return
                self._worker = Worker()

                def on_result(status):
                    temperature, setpoint, power, program_status = status
                    self.temp_label.setText(
                        f"Current Temp: {temperature:.2f} \u00b0C"
                    )
                    self.setpoint_label.setText(
                        f"Set Temp: {setpoint:.2f} \u00b0C"
                    )
                    self.power_label.setText(f"Power: {power:.1f} %")
                    self.live_plot.add_point(temperature)
                    self.data_recorded.emit([temperature])
                    # Follow the controller rather than only the programs
                    # this panel started: a queued experiment, the Eurotherm's
                    # own front panel, or a previous session can each leave one
                    # running, and Stop has to be reachable in all those cases.
                    running = program_status not in self.IDLE_STATES
                    if running != self._program_started:
                        self._set_program_running(running)
                    if not running:
                        phase = "Stopped"
                    elif program_status in self.HOLDING_STATES:
                        phase = "Holding at setpoint"
                    else:
                        phase = "Ramping to setpoint"
                    # Only on a change, so an error message stays readable
                    if phase != self._phase:
                        self._phase = phase
                        self.status_label.setText("Status: %s" % phase)

                self._worker.result.connect(on_result)
                self._worker.start()

        return EurothermPanel()