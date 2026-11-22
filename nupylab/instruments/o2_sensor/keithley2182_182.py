"""pO2 sensor read by a Keithley 2182 nanovoltmeter and a Keithley 182 voltmeter.

Unlike :mod:`nupylab.instruments.o2_sensor.keithley2182`, where one Keithley 2182
measures both the sensor temperature and the sensor potential on its two
channels, station S2 splits the measurement across two instruments: the Keithley
2182A nanovoltmeter measures the sensor thermocouple on channel 1, and the
Keithley 182 sensitive digital voltmeter measures the potential across the
sensor.

The partial pressure of oxygen follows from the Nernst equation,

.. math::

    pO_2 = 0.2095 \\times 10^{20158 \\left( \\frac{E - b}{T} - a \\right)}

with `E` the sensor potential in volts and `T` the sensor temperature in
kelvin. The calibration coefficients are those of the sensor's measured
potential against its temperature, :math:`E = aT + b`, so `a` is a slope in
V/K and `b` an intercept in V. That is the form and the lettering S2's LabVIEW
program uses, so coefficients recorded for this station carry over unchanged.
They are read from a text file with :func:`load_po2_calibration`, and default
to zero (an ideal, uncalibrated sensor) until one is selected.

S2's calibrations live in the `S2 Calibrations` folder beside this module, each
named for the day it was taken and the gas it was taken in, as
`YYYYMMDD_gas_po2_calibration.txt`. :func:`find_latest_calibration` picks the
newest of them, which is what the GUI loads when it starts.
"""

from __future__ import annotations

import csv
import logging
import math
import os
import re
import time
from datetime import date, datetime
from typing import List, NamedTuple, Optional, Sequence, Tuple

from pymeasure.instruments.keithley import keithley2182

from nupylab.drivers import keithley182
from nupylab.utilities import DataTuple, NupylabError
from nupylab.utilities.nupylab_instrument import NupylabInstrument

_control_log = logging.getLogger("nupylab.instrument_control")
# The control log writes straight into a widget, so anything logged off the GUI
# thread, i.e. from a reading, goes to the standard log instead
log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

#: Where S2 keeps every pO2 sensor calibration it has taken, beside this
#: module: one row per calibration, in the order they were added.
CALIBRATION_FILE: str = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "po2_calibrations.csv"
)

#: How old a calibration may be before the GUI says so, in days. Saying so is
#: all it does: an old calibration still measures, just less well.
CALIBRATION_WARNING_DAYS: int = 365

#: Columns of the calibration file, in order.
CALIBRATION_FIELDS: Tuple[str, ...] = ("date", "calibrator", "gas", "a", "b")


class PO2Calibration(NamedTuple):
    """One pO2 sensor calibration: its coefficients and where it came from.

    The coefficients are those of the sensor potential against its
    temperature, `E = aT + b`: `a` is a slope in V/K and `b` an intercept in V,
    the lettering S2's LabVIEW program uses.
    """

    a: float
    b: float
    gas: str = ""
    calibrated_by: str = ""
    date: Optional[date] = None

    @property
    def date_text(self) -> str:
        """Get the calibration date as `November 21, 2026`."""
        return self.date.strftime("%B %d, %Y") if self.date else "unknown date"

    def describe(self) -> str:
        """Get a one-line summary of when, by whom and in what gas."""
        return "{}, by {}, in {}".format(
            self.date_text,
            self.calibrated_by or "unknown",
            self.gas or "unknown gas",
        )

    def older_than(self, days: int = CALIBRATION_WARNING_DAYS) -> bool:
        """Get whether the calibration was taken more than `days` ago.

        Args:
            days: how long a calibration is trusted for.

        Returns:
            Whether it is older than that. A calibration with no date is not
            taken to be stale, since nothing is known about when it was taken.
        """
        if self.date is None:
            return False
        return (date.today() - self.date).days > days

    def as_row(self) -> List[str]:
        """Get this calibration as a row of the calibration file."""
        return [
            self.date.strftime("%Y%m%d") if self.date else "",
            self.calibrated_by,
            self.gas,
            repr(self.a),
            repr(self.b),
        ]


def _parse_date(text: str) -> date:
    """Read a calibration date written as YYYYMMDD.

    Args:
        text: the date as written, where dashes and slashes are ignored.

    Returns:
        The date.

    Raises:
        ValueError: if it is not a date in that form.
    """
    return datetime.strptime(re.sub(r"[-/]", "", text.strip()), "%Y%m%d").date()


def _parse_row(row: Sequence[str], path: str, number: int) -> PO2Calibration:
    """Read one row of a calibration file.

    Args:
        row: the row's cells, as :data:`CALIBRATION_FIELDS` orders them.
        path: path of the file, for the error message.
        number: row number in the file, for the error message.

    Returns:
        The calibration the row holds.

    Raises:
        NupylabError: if the row is short, or its date or coefficients cannot
            be read.
    """
    if len(row) < len(CALIBRATION_FIELDS):
        raise NupylabError(
            f"pO2 calibration file {path}, row {number}: expected "
            f"{len(CALIBRATION_FIELDS)} columns "
            f"({', '.join(CALIBRATION_FIELDS)}), found {len(row)}."
        )
    taken, calibrator, gas, a, b = (cell.strip() for cell in row[:5])
    try:
        when = _parse_date(taken)
    except ValueError:
        raise NupylabError(
            f"pO2 calibration file {path}, row {number}: `{taken}` is not a "
            "date as YYYYMMDD."
        )
    try:
        coefficients = (float(a), float(b))
    except ValueError:
        raise NupylabError(
            f"pO2 calibration file {path}, row {number}: `{a}` and `{b}` are "
            "not both numbers."
        )
    return PO2Calibration(
        a=coefficients[0],
        b=coefficients[1],
        gas=gas,
        calibrated_by=calibrator,
        date=when,
    )


def load_calibrations(path: str = CALIBRATION_FILE) -> List[PO2Calibration]:
    """Read every calibration in a calibration file, newest first.

    A first row whose first cell is not a date is taken for a header and
    skipped, as are blank rows.

    Args:
        path: path to the calibration file, S2's by default.

    Returns:
        The calibrations, newest first. Empty if the file does not exist,
        which is how a station that has not taken one yet reads.

    Raises:
        NupylabError: if the file cannot be read, or holds a row that cannot.
            One bad row stops the whole file: a calibration quietly dropped is
            worse than one that has to be fixed by hand.
    """
    if not os.path.exists(path):
        return []
    calibrations: List[PO2Calibration] = []
    try:
        with open(path, "r", newline="") as calibration_file:
            for number, row in enumerate(csv.reader(calibration_file), start=1):
                if not row or not any(cell.strip() for cell in row):
                    continue
                if number == 1:
                    try:
                        _parse_date(row[0])
                    except ValueError:
                        continue  # A header, not a calibration
                calibrations.append(_parse_row(row, path, number))
    except OSError as e:
        raise NupylabError(f"Could not open pO2 calibration file {path}: {e}")
    # Newest first, and among calibrations taken the same day, last added first
    return sorted(
        calibrations,
        key=lambda calibration: (calibration.date, len(calibrations)),
        reverse=True,
    )


def latest_calibration(path: str = CALIBRATION_FILE) -> Optional[PO2Calibration]:
    """Read the newest calibration in a calibration file.

    Args:
        path: path to the calibration file, S2's by default.

    Returns:
        The newest calibration, or `None` if the file holds none.

    Raises:
        NupylabError: if the file cannot be read.
    """
    calibrations = load_calibrations(path)
    return calibrations[0] if calibrations else None


#: Separates a calibration file from the date of the calibration chosen in it
SELECTION_SEPARATOR: str = "|"


def encode_selection(path: str, when: Optional[date] = None) -> str:
    """Write down which calibration is in use, as one string.

    A GUI parameter holds a single value, so the file and the calibration
    chosen within it travel together.

    Args:
        path: path of the calibration file.
        when: date of the chosen calibration. `None` means whichever is
            newest, which is what a station wants unless it says otherwise.

    Returns:
        The selection, e.g. `C:/.../po2_calibrations.csv|20261121`.
    """
    if when is None:
        return path
    return "{}{}{}".format(path, SELECTION_SEPARATOR, when.strftime("%Y%m%d"))


def decode_selection(value: str) -> Tuple[str, Optional[date]]:
    """Read back a selection written by :func:`encode_selection`.

    Args:
        value: the selection, or a bare path.

    Returns:
        The calibration file, and the date chosen in it if one was.
    """
    value = (value or "").strip()
    if SELECTION_SEPARATOR not in value:
        return value, None
    path, _, chosen = value.rpartition(SELECTION_SEPARATOR)
    try:
        return path, _parse_date(chosen)
    except ValueError:
        return value, None  # A path that happens to hold the separator


def load_selection(value: str) -> Optional[PO2Calibration]:
    """Read the calibration a selection points at.

    Args:
        value: the selection, see :func:`encode_selection`.

    Returns:
        The chosen calibration, the newest if none was chosen, or `None` if
        the file holds none. A chosen calibration that is no longer in the
        file, because it was edited out, also gives the newest.

    Raises:
        NupylabError: if the file cannot be read.
    """
    path, chosen = decode_selection(value)
    if not path:
        return None
    calibrations = load_calibrations(path)
    if not calibrations:
        return None
    if chosen is not None:
        for calibration in calibrations:
            if calibration.date == chosen:
                return calibration
        log.warning(
            "No pO2 calibration dated %s in %s, using the newest instead",
            chosen.strftime("%Y%m%d"),
            path,
        )
    return calibrations[0]


def create_calibration_file(path: str = CALIBRATION_FILE) -> str:
    """Start an empty calibration file, with its header and no calibrations.

    For a station that has not recorded one yet. An existing file is left
    exactly as it is, since it may hold calibrations.

    Args:
        path: path to the calibration file, S2's by default.

    Returns:
        The path, whether it was created here or was already there.

    Raises:
        NupylabError: if the file cannot be written.
    """
    if os.path.exists(path):
        return path
    try:
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(path, "w", newline="") as calibration_file:
            csv.writer(calibration_file).writerow(CALIBRATION_FIELDS)
    except OSError as e:
        raise NupylabError(f"Could not create pO2 calibration file {path}: {e}")
    log.info("Started a pO2 calibration file at %s", path)
    return path


def append_calibration(
    calibration: PO2Calibration, path: str = CALIBRATION_FILE
) -> None:
    """Add one calibration to a calibration file.

    Creates the file, with its header, if it is not there yet.

    Args:
        calibration: the calibration to record. It needs a date.
        path: path to the calibration file, S2's by default.

    Raises:
        NupylabError: if the calibration has no date, or the file cannot be
            written.
    """
    if calibration.date is None:
        raise NupylabError("A pO2 calibration needs the date it was taken.")
    exists = os.path.exists(path) and os.path.getsize(path) > 0
    try:
        with open(path, "a", newline="") as calibration_file:
            writer = csv.writer(calibration_file)
            if not exists:
                writer.writerow(CALIBRATION_FIELDS)
            writer.writerow(calibration.as_row())
    except OSError as e:
        raise NupylabError(f"Could not write pO2 calibration file {path}: {e}")


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

    #: How long to wait for the Keithley 182 to have a reading waiting, in
    #: seconds, when connecting and when reading.
    CONNECT_TIMEOUT: float = 5.0
    READ_TIMEOUT: float = 5.0

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
        invert_voltage: bool = False,
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
            po2_a: calibration coefficient `a`, the slope of the sensor
                potential against its temperature, in V/K.
                Overridden by `calibration_file`, if given.
            po2_b: calibration coefficient `b`, the intercept of the sensor
                potential against its temperature, in V.
                Overridden by `calibration_file`, if given.
            calibration_file: path to a calibration file whose newest
                calibration to use, see :func:`load_calibrations`. Optional.
            invert_voltage: whether to negate the voltage read by the
                Keithley 182, which depends on how the sensor leads are wired.
                Off by default, i.e. the reading is used as it comes, which is
                what S2's LabVIEW program does with it. Set in code rather
                than from the control panel.
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
        # Set by the control panel, so that a disconnection from elsewhere,
        # e.g. when an experiment is queued, reaches its button. Emitting a Qt
        # signal is safe from any thread; touching widgets is not.
        self._disconnected = None
        self._nanovoltmeter_port: str = nanovoltmeter_port
        self._voltmeter_port: str = voltmeter_port
        self._port = f"{nanovoltmeter_port}, {voltmeter_port}"  # For __repr__
        self._calibration = PO2Calibration(a=po2_a, b=po2_b)
        self._calibration_source: Optional[str] = None
        self._invert_voltage: bool = invert_voltage
        self._thermocouple: str = thermocouple
        if calibration_file:
            self.set_calibration_file(calibration_file)
        super().__init__(data_label, name)

    # Calibration #

    @property
    def calibration(self) -> PO2Calibration:
        """Get the calibration in use, coefficients and provenance."""
        return self._calibration

    @property
    def a(self) -> float:
        """Get coefficient `a`, the slope of E against T, in V/K."""
        return self._calibration.a

    @property
    def b(self) -> float:
        """Get coefficient `b`, the intercept of E against T, in V."""
        return self._calibration.b

    @property
    def calibration_file(self) -> Optional[str]:
        """Get the calibration file the calibration in use came from."""
        return self._calibration_source

    def set_calibration(
        self, calibration: PO2Calibration, source: Optional[str] = None
    ) -> None:
        """Use one calibration, e.g. one picked out of the calibration file.

        Args:
            calibration: the calibration to use.
            source: path of the calibration file it came from, if it came
                from one.
        """
        self._calibration = calibration
        self._calibration_source = source

    def set_coefficients(self, po2_a: float, po2_b: float) -> None:
        """Set the calibration coefficients directly, without provenance.

        Args:
            po2_a: calibration coefficient `a`, the slope of the sensor
                potential against its temperature, in V/K.
            po2_b: calibration coefficient `b`, the intercept of the sensor
                potential against its temperature, in V.
        """
        self.set_calibration(PO2Calibration(a=float(po2_a), b=float(po2_b)))

    def set_calibration_file(self, path: str) -> PO2Calibration:
        """Use the newest calibration in a calibration file.

        Args:
            path: path to the calibration file, see
                :func:`load_calibrations`.

        Returns:
            The calibration now in use.

        Raises:
            NupylabError: if the file cannot be read, or holds no calibration.
        """
        calibration = latest_calibration(path)
        if calibration is None:
            raise NupylabError(
                f"pO2 calibration file {path} holds no calibration yet."
            )
        self.set_calibration(calibration, path)
        return calibration

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
            (voltage - self._calibration.b) / kelvin - self._calibration.a
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

        Configures the 2182 to read the sensor thermocouple on channel 1. The
        182 is only checked for a reading, never configured, see
        :meth:`nupylab.drivers.keithley182.Keithley182.read_voltage`.

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
                # Nothing is written to the 182: it is left exactly as its
                # front panel and saved setup have it, which is how the
                # station's LabVIEW program has read it for years. Recalling
                # the factory setup here would select the rear panel trigger
                # input, which nothing drives, and the instrument would stop
                # producing readings. A serial poll is a bus command, so it
                # checks the instrument is answering without changing it.
                if not self.keithley182.wait_for_reading(self.CONNECT_TIMEOUT):
                    raise NupylabError(
                        "no reading waiting; check that its front panel is "
                        "taking readings continuously"
                    )
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
        if self._disconnected is not None:
            try:
                self._disconnected()
            except Exception:
                pass
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
                # Waits for the instrument to report a reading ready, rather
                # than reading into its bus hold-off
                voltage = self.keithley182.read_voltage(self.READ_TIMEOUT)
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
                # Not the control log: its handler writes into a widget, and
                # this runs on a worker or procedure thread
                log.warning("Keithley 182 read failed%s", recovery)
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
            #: Emitted when the sensor is disconnected from anywhere, so that
            #: the button says so even when the tab did not do it
            monitoring_stopped = QtCore.Signal()
            READ_INTERVAL_MS = 2000
            NO_READING = "—"
            START_TEXT = "Start Monitoring"
            STOP_TEXT = "Stop Monitoring"
            # A double click would otherwise start monitoring and stop it
            # again; a second toggle this soon after one lands is ignored.
            # The window is measured from the end of the previous action,
            # since connecting blocks the event loop while it runs.
            TOGGLE_GUARD_S = 1.0

            def __init__(self):
                super().__init__("pO2 Sensor — Keithley 2182 + 182")
                self._worker = None
                self._abort_callback = abort_callback
                self._last_error = None
                self._monitoring = False
                self._last_toggle = 0.0
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
                self.monitoring_stopped.connect(
                    lambda: self._set_monitoring(False)
                )
                instrument._panel = self
                instrument._disconnected = self.monitoring_stopped.emit

            def _setup_ui(self):
                layout = QtWidgets.QFormLayout()

                self.monitor_btn = QtWidgets.QPushButton(self.START_TEXT)
                self.monitor_btn.clicked.connect(self.toggle_monitoring)
                layout.addRow(self.monitor_btn)

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

            def _abort_if_needed(self):
                if self._abort_callback:
                    self._abort_callback()

            def _set_monitoring(self, monitoring):
                """Keep the single button in step with the instrument."""
                self._monitoring = monitoring
                self.monitor_btn.setText(
                    self.STOP_TEXT if monitoring else self.START_TEXT
                )

            def toggle_monitoring(self):
                """Start reading the sensor, or stop if it already is.

                Ignores a repeat click that lands within
                :attr:`TOGGLE_GUARD_S` of the previous one finishing, so that
                a double click cannot start monitoring and stop it again.
                """
                now = time.monotonic()
                if now - self._last_toggle < self.TOGGLE_GUARD_S:
                    _control_log.info(
                        "Ignoring repeat click on the pO2 monitoring button"
                    )
                    return
                try:
                    if self._monitoring:
                        self.disconnect_instrument()
                    else:
                        self.connect_instrument()
                finally:
                    self._last_toggle = time.monotonic()

            def connect_instrument(self):
                self._abort_if_needed()
                try:
                    instrument.connect()
                    self._set_monitoring(True)
                    self.status_label.setText("Status: Monitoring")
                    self._status = "Monitoring"
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
                    self._set_monitoring(False)
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
                    self._set_monitoring(False)
                    self.status_label.setText("Status: Not monitoring")
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
                        status = "Monitoring"
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
