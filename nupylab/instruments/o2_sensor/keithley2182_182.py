"""pO2 sensor read by a Keithley 2182 nanovoltmeter and a Keithley 182 voltmeter.

Unlike :mod:`nupylab.instruments.o2_sensor.keithley2182`, where one Keithley 2182
measures both the sensor temperature and the sensor potential on its two
channels, station S2 splits the measurement across two instruments: the Keithley
2182A nanovoltmeter measures the sensor thermocouple on channel 1, and the
Keithley 182 sensitive digital voltmeter measures the potential across the
sensor.

The partial pressure of oxygen follows from the Nernst equation,

.. math::

    pO_2 = 0.2095 \\times 10^{20158 \\left( \\frac{E - a}{T} - b \\right)}

with `E` the sensor potential in volts, `T` the sensor temperature in kelvin,
and `a` and `b` the calibration coefficients for the sensor. The coefficients
are read from a text file with :func:`load_po2_calibration`, and default to zero
(an ideal, uncalibrated sensor) until one is selected.
"""

from __future__ import annotations

import logging
import math
import os
import re
import time
from typing import List, Optional, Sequence, Tuple

from pymeasure.instruments.keithley import keithley2182

from nupylab.drivers import keithley182
from nupylab.utilities import DataTuple, NupylabError
from nupylab.utilities.nupylab_instrument import NupylabInstrument

_control_log = logging.getLogger("nupylab.instrument_control")

# `a = 1.0e-4`, `a: 1.0e-4`, `a, 1.0e-4` or `a 1.0e-4`, one coefficient per line
_SEPARATORS_RE = re.compile(r"[=:,\s]+")
# Names accepted for each coefficient. `slope` and `intercept` match the names
# used by the single-Keithley pO2 sensor class.
_COEFFICIENT_NAMES = {
    "a": "a",
    "slope": "a",
    "po2_slope": "a",
    "b": "b",
    "intercept": "b",
    "po2_intercept": "b",
}


def load_po2_calibration(path: str) -> Tuple[float, float]:
    """Read pO2 calibration coefficients `a` and `b` from a text file.

    Blank lines are ignored, as is anything after a `#`, `!`, or `%` comment
    character. Coefficients may be named, one per line::

        # S2 pO2 sensor calibration
        a = 1.0e-4
        b = 2.5e-7

    where the name and value may be separated by `=`, `:`, `,`, or whitespace,
    and `slope` and `intercept` are accepted as aliases of `a` and `b`.
    Alternatively, the file may hold two bare numbers, `a` first.

    Args:
        path: path to the calibration file.

    Returns:
        Tuple of coefficients `a` and `b`.

    Raises:
        NupylabError: if the file cannot be read or does not define both
            coefficients.
    """
    named: dict = {}
    positional: List[float] = []
    try:
        with open(path, "r") as calibration_file:
            for number, line in enumerate(calibration_file, start=1):
                for comment in ("#", "!", "%"):
                    line = line.split(comment)[0]
                line = line.strip()
                if not line:
                    continue
                fields = _SEPARATORS_RE.split(line)
                try:
                    if len(fields) == 1:
                        positional.append(float(fields[0]))
                    elif len(fields) == 2 and fields[0].lower() in _COEFFICIENT_NAMES:
                        named[_COEFFICIENT_NAMES[fields[0].lower()]] = float(fields[1])
                    else:
                        raise ValueError(line)
                except ValueError:
                    raise NupylabError(
                        f"Could not read pO2 calibration file {path}, line {number}: "
                        f"`{line}` is not a coefficient."
                    )
    except OSError as e:
        raise NupylabError(f"Could not open pO2 calibration file {path}: {e}")

    if "a" in named and "b" in named:
        return named["a"], named["b"]
    if len(positional) >= 2:
        return positional[0], positional[1]
    raise NupylabError(
        f"pO2 calibration file {path} does not define both coefficients `a` and `b`."
    )


class Keithley2182And182(NupylabInstrument):
    """pO2 sensor read by a Keithley 2182 and a Keithley 182.

    The Keithley 2182 measures the sensor thermocouple on channel 1, and the
    Keithley 182 measures the sensor potential. Abstracts both drivers for
    NUPyLab procedures.

    Attributes:
        data_label: labels for DataTuples.
        name: name of instrument.
        lock: thread lock for preventing simultaneous calls to instrument.
        keithley2182: Keithley 2182 driver class, reading temperature.
        keithley182: Keithley 182 driver class, reading sensor potential.
    """

    #: Reference pO2 in atm, i.e. that of the air-side reference electrode.
    REFERENCE_PO2: float = 0.2095
    #: 4F / (2.303 R) in K/V, the Nernst prefactor for a 4-electron reaction.
    NERNST_CONSTANT: float = 20158.0

    def __init__(
        self,
        nanovoltmeter_port: str,
        voltmeter_port: str,
        data_label: Sequence[str],
        po2_a: float = 0.0,
        po2_b: float = 0.0,
        calibration_file: Optional[str] = None,
        invert_voltage: bool = True,
        thermocouple: str = "S",
        name: str = "pO2 Sensor",
    ) -> None:
        """Initialize data labels, name, calibration, and connection parameters.

        Args:
            nanovoltmeter_port: string name of the Keithley 2182 port, e.g.
                `GPIB0::7::INSTR`.
            voltmeter_port: string name of the Keithley 182 port, e.g.
                `GPIB0::8::INSTR`.
            data_label: labels for DataTuples. :meth:`get_data` returns sensor
                temperature, pO2, and sensor voltage, and corresponding labels
                should match entries in DATA_COLUMNS of the calling procedure.
            po2_a: calibration coefficient `a`, a sensor voltage offset in V.
                Overridden by `calibration_file`, if given.
            po2_b: calibration coefficient `b`, an offset on E / T in V/K.
                Overridden by `calibration_file`, if given.
            calibration_file: path to a text file holding `a` and `b`, see
                :func:`load_po2_calibration`. Optional.
            invert_voltage: whether to negate the voltage read by the Keithley
                182, which depends on how the sensor leads are wired. Set in
                code rather than from the control panel; the default matches
                the single-Keithley pO2 sensor class.
            thermocouple: thermocouple type of the pO2 sensor.
            name: name of instrument.

        Raises:
            ValueError: if `data_label` does not contain 3 entries.
            NupylabError: if `calibration_file` cannot be read.
        """
        if len(data_label) != 3:
            raise ValueError(
                f"{self.__class__.__name__} data_label must be sequence of length 3."
            )
        self.keithley2182: Optional[keithley2182.Keithley2182] = None
        self.keithley182: Optional[keithley182.Keithley182] = None
        self._panel = None
        # Set by the control panel so that a calibration loaded elsewhere, e.g.
        # from the inputs panel, reaches the panel labels. Emitting a Qt signal
        # is safe from the procedure thread; touching widgets is not.
        self._calibration_changed = None
        self._nanovoltmeter_port: str = nanovoltmeter_port
        self._voltmeter_port: str = voltmeter_port
        self._port = f"{nanovoltmeter_port}, {voltmeter_port}"  # For __repr__
        self._a: float = po2_a
        self._b: float = po2_b
        self._calibration_file: Optional[str] = None
        self._invert_voltage: bool = invert_voltage
        self._thermocouple: str = thermocouple
        if calibration_file:
            self.set_calibration_file(calibration_file)
        super().__init__(data_label, name)

    # Calibration #

    @property
    def a(self) -> float:
        """Get calibration coefficient `a`, a sensor voltage offset in V."""
        return self._a

    @property
    def b(self) -> float:
        """Get calibration coefficient `b`, an offset on E / T in V/K."""
        return self._b

    @property
    def calibration_file(self) -> Optional[str]:
        """Get the path of the loaded calibration file, if any."""
        return self._calibration_file

    def set_calibration(self, po2_a: float, po2_b: float) -> None:
        """Set the calibration coefficients directly.

        Args:
            po2_a: calibration coefficient `a`, a sensor voltage offset in V.
            po2_b: calibration coefficient `b`, an offset on E / T in V/K.
        """
        self._a = float(po2_a)
        self._b = float(po2_b)
        self._calibration_file = None
        self._notify_calibration_changed()

    def set_calibration_file(self, path: str) -> Tuple[float, float]:
        """Read the calibration coefficients from a text file.

        Args:
            path: path to the calibration file, see
                :func:`load_po2_calibration`.

        Returns:
            Tuple of coefficients `a` and `b`.

        Raises:
            NupylabError: if the file cannot be read or does not define both
                coefficients.
        """
        self._a, self._b = load_po2_calibration(path)
        self._calibration_file = path
        self._notify_calibration_changed()
        return self._a, self._b

    def _notify_calibration_changed(self) -> None:
        """Tell the control panel, if any, that the coefficients changed."""
        if self._calibration_changed is not None:
            try:
                self._calibration_changed()
            except Exception:
                pass

    @property
    def invert_voltage(self) -> bool:
        """Control whether the voltage read by the Keithley 182 is negated."""
        return self._invert_voltage

    @invert_voltage.setter
    def invert_voltage(self, invert: bool) -> None:
        self._invert_voltage = bool(invert)

    def calculate_po2(self, voltage: float, temperature: float) -> float:
        """Convert sensor potential and temperature to pO2.

        Args:
            voltage: sensor potential in V, sign corrected.
            temperature: sensor temperature in deg C.

        Returns:
            pO2 in atm, or `inf` if the exponent overflows.
        """
        kelvin: float = temperature + 273.15
        if kelvin <= 0:
            return float("nan")
        exponent: float = self.NERNST_CONSTANT * (
            (voltage - self._a) / kelvin - self._b
        )
        try:
            return self.REFERENCE_PO2 * 10**exponent
        except OverflowError:
            return float("inf")

    # Connection #

    def set_connection(self, nanovoltmeter_port: str, voltmeter_port: str) -> None:
        """Set ports used by the next call to :meth:`connect`.

        Args:
            nanovoltmeter_port: string name of the Keithley 2182 port.
            voltmeter_port: string name of the Keithley 182 port.
        """
        self._nanovoltmeter_port = nanovoltmeter_port
        self._voltmeter_port = voltmeter_port
        self._port = f"{nanovoltmeter_port}, {voltmeter_port}"

    def _close_meters(self) -> None:
        """Close whichever VISA sessions are open. Call while holding the lock."""
        for attribute in ("keithley2182", "keithley182"):
            instrument = getattr(self, attribute)
            if instrument is not None:
                try:
                    instrument.adapter.close()
                except Exception:
                    pass
                setattr(self, attribute, None)
        self._connected = False

    def connect(self) -> None:
        """Connect to the Keithley 2182 and the Keithley 182.

        Configures the 2182 to read the sensor thermocouple on channel 1 and the
        182 to free-run on DC volts.

        Raises:
            NupylabError: if either meter cannot be set up, naming which one.
                Whatever was opened is closed again first, since a second VISA
                session to a meter left open by a failed attempt is itself a
                cause of GPIB bus errors.
        """
        with self.lock:
            self._close_meters()
            try:
                # PyMeasure's class asks for a CR terminator, but the 2182
                # ends its replies with LF, so every read would warn that the
                # string does not end with the termination characters
                self.keithley2182 = keithley2182.Keithley2182(
                    self._nanovoltmeter_port, read_termination="\n"
                )
                self.keithley2182.clear()
                self.keithley2182.reset()
                self.keithley2182.thermocouple = self._thermocouple
                self.keithley2182.ch_1.setup_temperature()
            except Exception as e:
                self._close_meters()
                raise NupylabError(
                    f"Keithley 2182 on {self._nanovoltmeter_port} failed: {e}"
                ) from e
            try:
                self.keithley182 = keithley182.Keithley182(self._voltmeter_port)
                self.keithley182.reset()
                # The sensor potential is a slow DC signal read every few
                # seconds, so take the 100 ms integration period for the
                # quietest readings
                self.keithley182.setup_voltage(integration_period=0.1)
            except Exception as e:
                self._close_meters()
                raise NupylabError(
                    f"Keithley 182 on {self._voltmeter_port} failed: {e}"
                ) from e
            self._connected = True

    def disconnect(self) -> None:
        """Close both connections."""
        if self._panel is not None and self._connected:
            try:
                self._panel.timer.stop()
                if self._panel._worker and self._panel._worker.isRunning():
                    self._panel._worker.wait(2000)
            except Exception:
                pass
        with self.lock:
            self._close_meters()
        time.sleep(0.3)

    # Measurement #

    def set_parameters(self, *args, **kwargs) -> None:
        """Set measurement parameters. Not implemented."""

    def start(self) -> None:
        """Start pO2 measurement. Not implemented."""

    def get_data(self) -> List[DataTuple]:
        """Get pO2 sensor data.

        Returns:
            DataTuples in the order of sensor temperature in deg C, pO2 in atm,
            and sensor voltage in V.

        Raises:
            NupylabError: if either meter cannot be read, naming which one.
        """
        temperature: float
        voltage: float
        with self.lock:
            try:
                temperature = self.keithley2182.temperature
            except Exception as e:
                raise NupylabError(
                    f"Keithley 2182 on {self._nanovoltmeter_port}: {e}"
                ) from e
            try:
                voltage = self.keithley182.voltage
            except Exception as e:
                # A read that times out leaves the 182 holding the bus off, and
                # every later transfer to it then fails until it is cleared or
                # power cycled. Try to clear it rather than leave it stuck.
                recovery = ""
                try:
                    self.keithley182.recover()
                    recovery = ", cleared and reconfigured it"
                except Exception as recovery_error:
                    recovery = f", and could not clear it: {recovery_error}"
                _control_log.warning(
                    "Keithley 182 read failed%s", recovery
                )
                raise NupylabError(
                    f"Keithley 182 on {self._voltmeter_port}: {e}{recovery}"
                ) from e
        if self._invert_voltage:
            voltage = -voltage
        po2: float = self.calculate_po2(voltage, temperature)
        return [
            DataTuple(self.data_label[0], temperature),
            DataTuple(self.data_label[1], po2),
            DataTuple(self.data_label[2], voltage),
        ]

    def stop_measurement(self) -> None:
        """Stop pO2 measurement. Not implemented."""

    def shutdown(self) -> None:
        """Disconnect from both Keithleys."""
        self.disconnect()

    # Manual control #

    def control_widget(self, abort_callback=None):
        """Return a Qt control panel for this instrument."""
        from pymeasure.display.Qt import QtCore, QtWidgets

        from nupylab.utilities.instrument_control import LivePlotWidget

        instrument = self

        class Worker(QtCore.QThread):
            result = QtCore.Signal(list)
            error = QtCore.Signal(str)

            def run(self):
                try:
                    data = instrument.get_data()
                    self.result.emit([dt.value for dt in data])
                except Exception as e:
                    self.error.emit(str(e))

        class PO2SensorPanel(QtWidgets.QGroupBox):
            plot_title = "pO2"
            instrument_name = "pO2 Sensor"
            record_columns = [
                "pO2 Sensor Temperature (degC)",
                "pO2 (atm)",
                "pO2 Sensor Voltage (V)",
            ]
            data_recorded = QtCore.Signal(list)
            calibration_changed = QtCore.Signal()
            READ_INTERVAL_MS = 2000
            NO_READING = "—"

            def __init__(self):
                super().__init__("pO2 Sensor — Keithley 2182 + 182")
                self._worker = None
                self._abort_callback = abort_callback
                self._last_error = None
                # Last status shown, so a steady state is not rewritten
                self._status = None
                # One tab each, since pO2 spans decades while the sensor
                # temperature and potential do not share a scale with it
                self.live_plot = LivePlotWidget("pO2", "pO2 (atm)", n_traces=1)
                self.live_plot.set_log_y(True)
                self.temp_plot = LivePlotWidget(
                    "pO2 Sensor Temperature", "Temperature (°C)", n_traces=1
                )
                self.voltage_plot = LivePlotWidget(
                    "pO2 Sensor Voltage", "Sensor Voltage (mV)", n_traces=1
                )
                self.live_plots = {
                    "pO2": self.live_plot,
                    "pO2 Sensor T": self.temp_plot,
                    "pO2 Sensor V": self.voltage_plot,
                }
                self._setup_ui()
                self.timer = QtCore.QTimer()
                self.timer.timeout.connect(self.update_readings)
                # A calibration loaded from the inputs panel, possibly from the
                # procedure thread, refreshes these labels through the signal
                self.calibration_changed.connect(self.refresh_calibration)
                instrument._panel = self
                instrument._calibration_changed = self.calibration_changed.emit

            def _setup_ui(self):
                layout = QtWidgets.QFormLayout()

                cal_layout = QtWidgets.QHBoxLayout()
                self.cal_btn = QtWidgets.QPushButton("Calibration File...")
                self.cal_btn.clicked.connect(self.select_calibration_file)
                cal_layout.addWidget(self.cal_btn)
                self.cal_file_label = QtWidgets.QLabel("no file selected")
                self.cal_file_label.setStyleSheet("color: gray;")
                cal_layout.addWidget(self.cal_file_label)
                layout.addRow(cal_layout)

                self.cal_label = QtWidgets.QLabel()
                self.refresh_calibration()
                layout.addRow(self.cal_label)

                btn_layout = QtWidgets.QHBoxLayout()
                self.connect_btn = QtWidgets.QPushButton("Connect")
                self.disconnect_btn = QtWidgets.QPushButton("Disconnect")
                self.connect_btn.clicked.connect(self.connect_instrument)
                self.disconnect_btn.clicked.connect(self.disconnect_instrument)
                btn_layout.addWidget(self.connect_btn)
                btn_layout.addWidget(self.disconnect_btn)
                layout.addRow(btn_layout)

                # Temperature, pO2 and sensor voltage, the three live readings
                self.temp_label = QtWidgets.QLabel(self.NO_READING)
                self.po2_label = QtWidgets.QLabel(self.NO_READING)
                self.voltage_label = QtWidgets.QLabel(self.NO_READING)
                readouts = QtWidgets.QGridLayout()
                readouts.setHorizontalSpacing(12)
                captions = (
                    ("Temperature:", self.temp_label),
                    ("pO2:", self.po2_label),
                    ("Sensor Voltage:", self.voltage_label),
                )
                for row, (caption, value_label) in enumerate(captions):
                    value_label.setStyleSheet(
                        "font-size: 15px; font-weight: bold;"
                    )
                    value_label.setAlignment(
                        QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter
                    )
                    readouts.addWidget(QtWidgets.QLabel(caption), row, 0)
                    readouts.addWidget(value_label, row, 1)
                layout.addRow(readouts)

                self.status_label = QtWidgets.QLabel("Status: Not connected")
                layout.addRow(self.status_label)

                self.setLayout(layout)

            def _clear_readouts(self):
                self.temp_label.setText(self.NO_READING)
                self.po2_label.setText(self.NO_READING)
                self.voltage_label.setText(self.NO_READING)

            def _update_calibration_label(self):
                self.cal_label.setText(
                    "a = %.6g V, b = %.6g V/K" % (instrument.a, instrument.b)
                )

            def refresh_calibration(self):
                """Show the coefficients and file the instrument now holds."""
                path = instrument.calibration_file
                if path:
                    self.cal_file_label.setText(os.path.basename(path))
                    self.cal_file_label.setStyleSheet("color: black;")
                else:
                    self.cal_file_label.setText("no file selected")
                    self.cal_file_label.setStyleSheet("color: gray;")
                self._update_calibration_label()

            def _abort_if_needed(self):
                if self._abort_callback:
                    self._abort_callback()

            def select_calibration_file(self):
                """Pick the text file holding the `a` and `b` coefficients."""
                start_dir = (
                    os.path.dirname(instrument.calibration_file)
                    if instrument.calibration_file
                    else ""
                )
                path, _ = QtWidgets.QFileDialog.getOpenFileName(
                    self,
                    "Select pO2 Calibration File",
                    start_dir,
                    "Text files (*.txt *.csv *.dat);;All files (*)",
                )
                if not path:
                    return
                try:
                    a, b = instrument.set_calibration_file(path)
                    self.refresh_calibration()
                    _control_log.info(
                        "pO2 calibration loaded from %s: a = %.6g, b = %.6g",
                        path, a, b
                    )
                except Exception as e:
                    self.cal_file_label.setText("Error")
                    self.cal_file_label.setStyleSheet("color: red;")
                    self.status_label.setText(f"Status: Error — {e}")
                    _control_log.error("pO2 calibration file failed: %s", e)

            def connect_instrument(self):
                self._abort_if_needed()
                try:
                    instrument.connect()
                    self.status_label.setText("Status: Connected")
                    self._status = "Connected"
                    self._last_error = None
                    for plot in self.live_plots.values():
                        plot.clear()
                    self.timer.start(self.READ_INTERVAL_MS)
                    _control_log.info(
                        "pO2 sensor connected: Keithley 2182 on %s, "
                        "Keithley 182 on %s",
                        instrument._nanovoltmeter_port,
                        instrument._voltmeter_port,
                    )
                except Exception as e:
                    self.status_label.setText(f"Status: Error — {e}")
                    self._status = None
                    _control_log.error("pO2 sensor connect failed: %s", e)

            def disconnect_instrument(self):
                self._abort_if_needed()
                try:
                    self.timer.stop()
                    if self._worker and self._worker.isRunning():
                        self._worker.wait(2000)
                    instrument.disconnect()
                    self.status_label.setText("Status: Disconnected")
                    self._status = None
                    self._clear_readouts()
                    _control_log.info("pO2 sensor disconnected")
                except Exception as e:
                    self.status_label.setText(f"Status: Error — {e}")

            def update_readings(self):
                if not instrument.connected:
                    return
                if self._worker and self._worker.isRunning():
                    return
                self._worker = Worker()

                def on_result(values):
                    temperature, po2, voltage = values
                    self.temp_label.setText(f"{temperature:.2f} °C")
                    # The Keithley 182 reports an overflowed reading, e.g. from
                    # an open sensor lead, as a value the driver turns into nan
                    if math.isnan(voltage):
                        self.voltage_label.setText("overflow")
                        self.po2_label.setText(self.NO_READING)
                        status = "Voltage overflows range — check leads"
                    else:
                        self.voltage_label.setText(f"{voltage*1e3:.4f} mV")
                        self.po2_label.setText(f"{po2:.4g} atm")
                        status = "Connected"
                    # A log axis cannot show a non-positive or non-finite point
                    if math.isfinite(po2) and po2 > 0:
                        self.live_plot.add_point(po2)
                    if math.isfinite(temperature):
                        self.temp_plot.add_point(temperature)
                    if math.isfinite(voltage):
                        self.voltage_plot.add_point(voltage * 1e3)
                    self.data_recorded.emit([temperature, po2, voltage])
                    self._last_error = None
                    if status != self._status:
                        self._status = status
                        self.status_label.setText(f"Status: {status}")

                def on_error(message):
                    # Only on a change, so the log stays readable
                    if message != self._last_error:
                        self._last_error = message
                        self._status = None
                        self.status_label.setText(f"Status: Read error — {message}")
                        _control_log.error("pO2 sensor read failed: %s", message)

                self._worker.result.connect(on_result)
                self._worker.error.connect(on_error)
                self._worker.start()

        return PO2SensorPanel()
