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

    SHUTDOWN_TEMPERATURE = 20.0

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
        """
        self._finished = False
        with self.lock:
            self.eurotherm.program_status = "reset"
            self.eurotherm.active_setpoint = 1
            # End type `dwell` holds the final setpoint indefinitely
            self.eurotherm.end_type = "dwell"
            self.eurotherm.setpoint_rate_limit = ramp_rate
            self.eurotherm.setpoint2 = target_temperature
            # Dwell must be non-zero for the program to run, use one second
            self.eurotherm.dwell_time = 1
            self.eurotherm.program_status = "run"

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
                super().__init__("Eurotherm 2200 — Furnace")
                self._worker = None
                self._program_started = False
                self._phase = None
                self._last_toggle = 0.0
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

                self.temp_label = QtWidgets.QLabel("Current Temp: —")
                self.setpoint_label = QtWidgets.QLabel("Set Temp: —")
                self.power_label = QtWidgets.QLabel("Power: —")
                self.status_label = QtWidgets.QLabel("Status: Not connected")
                layout.addRow(self.temp_label)
                layout.addRow(self.setpoint_label)
                layout.addRow(self.power_label)
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

            def _clear_readouts(self):
                self.temp_label.setText("Current Temp: —")
                self.setpoint_label.setText("Set Temp: —")
                self.power_label.setText("Power: —")

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
                    self.status_label.setText(f"Status: Error — {e}")

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
                        "Status: Ramping to %.0f°C, disconnected"
                        % instrument.SHUTDOWN_TEMPERATURE
                    )
                    _control_log.info(
                        "Eurotherm shutting down: ramping to %.0f°C at "
                        "%.1f°C/min, then disconnecting",
                        instrument.SHUTDOWN_TEMPERATURE, self.ramp_rate.value()
                    )
                except Exception as e:
                    self.status_label.setText(f"Status: Error — {e}")
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
                        "Eurotherm ramping to %.1f°C at %.1f°C/min, "
                        "then holding",
                        self.target_temp.value(), self.ramp_rate.value()
                    )
                except Exception as e:
                    self.status_label.setText(f"Status: Error — {e}")
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
                    self.status_label.setText(f"Status: Error — {e}")

            def update_temp(self):
                if not instrument.connected:
                    return
                if self._worker and self._worker.isRunning():
                    return
                self._worker = Worker()

                def on_result(status):
                    temperature, setpoint, power, program_status = status
                    self.temp_label.setText(
                        f"Current Temp: {temperature:.2f} °C"
                    )
                    self.setpoint_label.setText(
                        f"Set Temp: {setpoint:.2f} °C"
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
