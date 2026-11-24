"""Adapts the LabJack U12 driver to a NUPyLab instrument for solenoid control.

The solenoid valve selects which of two gas mixes reaches the station. It is
driven from the U12's AO1 analog output, which is a screw terminal on the
device, and AO0 is held at 0 V. Gas Mix 1 is the unpowered position and Gas
Mix 2 the powered one.

This mirrors the LabVIEW Call Library Function Node the station has used:
`AOUpdate` in `ljackuw.dll`, called with `updateDigital` and `resetCounter`
both zero so that the digital lines and the counter are left alone. Note that
one call always writes *both* analog outputs, exactly as the LabVIEW node does,
which is why AO0 is written 0 V on every call rather than left alone.

Nothing here moves the valve on its own. Connecting, disconnecting and shutting
down all leave the analog output exactly where it is, so the valve only changes
when the user applies a state. An analog output holds its voltage until it is
written again or the U12 loses power, so a valve left powered stays powered
across a restart of this GUI.

There is nothing to read from this instrument, so there is no connection worth
holding open between writes and the control panel offers no connect or
disconnect button. Applying a state opens the LabJack if it is not already
open, writes, and leaves it at that.

Each write is appended to a record file so that the last state this GUI applied
survives a restart. That record is a log of what this GUI did, not a reading of
the valve: the U12 cannot read an analog output back, and the station's LabVIEW
program writes the same output without knowing about this file. Nothing here
acts on the record, and it is shown only as history.
"""

from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

from nupylab.drivers import labjack_u12
from nupylab.utilities import DataTuple, NupylabError
from nupylab.utilities.nupylab_instrument import NupylabInstrument

# The control log writes straight into the Manual Control tab's log pane
_control_log = logging.getLogger("nupylab.instrument_control")

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


class LabJackSolenoid(NupylabInstrument):
    """Solenoid valve driven by a LabJack U12 analog output.

    The valve selects which of two gas mixes reaches the station, and its two
    positions are the unpowered and powered states of the same output:

        * Gas Mix 1 -- no power, the analog output is written 0 V
        * Gas Mix 2 -- powered, the analog output is written `drive_voltage`

    Internally a state is the boolean the LabVIEW front panel uses, where
    `True` is Gas Mix 1 and so applies no power. :attr:`STATE_LABELS` maps the
    two onto each other, and is the only place the names appear.

    The U12 analog outputs span 0 to 5 V and the valve needs more than 2 V, so
    the default drive voltage is 3 V.

    S2 wires the valve to AO1, which is the default and is not selectable from
    the control panel. The `channel` argument is kept so that a station wired
    to AO0 only needs a different value here.

    A U12 cannot report what an analog output is doing, so the only way to read
    the drive voltage is to jumper that output to an analog input and read the
    input instead. S2 has such a jumper, and `sense_channel` says which input
    it lands on. Note that this reads what the U12 is putting out, not that the
    valve moved: a fault between the output and the coil reads the same as a
    valve that is working.

    Attributes:
        u12: LabJack U12 driver instance, or None until connected.
        name: name of instrument.
        lock: thread lock for preventing simultaneous calls to instrument.
    """

    #: Full scale of a U12 analog output, in volts.
    MAX_VOLTAGE: float = 5.0
    #: Drive voltage used when the selector is `False`, in volts.
    DEFAULT_DRIVE_VOLTAGE: float = 3.0
    #: Analog output S2's solenoid valve is wired to, 1 for AO1.
    DEFAULT_CHANNEL: int = 1
    #: Names of the two valve positions, keyed by their LabVIEW boolean.
    STATE_LABELS: dict = {True: "Gas Mix 1", False: "Gas Mix 2"}
    #: Where :meth:`_record_write` keeps the last state this GUI applied.
    DEFAULT_STATE_FILE: Path = Path.home() / ".nupylab" / "solenoid_valve.json"
    #: Analog input the jumper from the valve's output lands on.
    DEFAULT_SENSE_CHANNEL: int = 0
    #: Single-ended analog inputs a U12 offers, AI0 to AI7.
    SENSE_CHANNELS: tuple = (0, 1, 2, 3, 4, 5, 6, 7)

    def __init__(
        self,
        channel: int = DEFAULT_CHANNEL,
        drive_voltage: float = DEFAULT_DRIVE_VOLTAGE,
        id_num: int = -1,
        data_label: str = "Solenoid Valve Voltage (V)",
        name: str = "Solenoid Valve",
        state_file: Optional[Union[str, os.PathLike]] = None,
        sense_channel: Optional[int] = DEFAULT_SENSE_CHANNEL,
    ) -> None:
        """Initialize solenoid valve connection parameters.

        Args:
            channel: index of the analog output driving the valve, 0 for AO0 or
                1 for AO1. S2 uses AO1.
            drive_voltage: voltage written to that output to power the valve.
            id_num: LabJack local ID. -1 selects the first U12 found, which is
                what the station's LabVIEW program uses.
            data_label: label for DataTuples, should match an entry in
                DATA_COLUMNS of the calling procedure.
            name: name of instrument.
            state_file: where to record each write, or None for
                :attr:`DEFAULT_STATE_FILE`. Pass an empty string to record
                nothing.
            sense_channel: analog input, 0 to 7, that the valve's output is
                jumpered to, or None if there is no jumper and the voltage
                therefore cannot be read.

        Raises:
            ValueError: if `channel`, `drive_voltage` or `sense_channel` is out
                of range.
        """
        self.u12: Optional[labjack_u12.U12] = None
        self._panel = None
        self._channel: int = self._check_channel(channel)
        self._sense_channel: Optional[int] = self._check_sense_channel(sense_channel)
        self._drive_voltage: float = self._check_voltage(drive_voltage)
        self._id_num: int = int(id_num)
        self._state: bool = True
        # A U12 cannot read an analog output back, so until this class writes
        # one there is no way to know what the valve is doing. Reporting 0 V
        # would be a guess, and a wrong one for a valve left powered.
        self._applied_voltage: Optional[float] = None
        if state_file is None:
            self._state_file: Optional[Path] = self.DEFAULT_STATE_FILE
        else:
            self._state_file = Path(state_file) if state_file else None
        self._last_record: Optional[dict] = self._read_record()
        self._port = f"LabJack U12 id {self._id_num} AO{self._channel}"
        super().__init__(data_label, name)

    # Settings #

    @staticmethod
    def _check_channel(channel: int) -> int:
        channel = int(channel)
        if channel not in (0, 1):
            raise ValueError(
                f"Analog output channel must be 0 (AO0) or 1 (AO1), not {channel}."
            )
        return channel

    @classmethod
    def _check_sense_channel(cls, channel: Optional[int]) -> Optional[int]:
        if channel is None:
            return None
        channel = int(channel)
        if channel not in cls.SENSE_CHANNELS:
            raise ValueError(
                f"Sense channel must be one of {cls.SENSE_CHANNELS} or None, "
                f"not {channel}."
            )
        return channel

    @classmethod
    def _check_voltage(cls, voltage: float) -> float:
        voltage = float(voltage)
        if not 0.0 <= voltage <= cls.MAX_VOLTAGE:
            raise ValueError(
                f"Drive voltage must be between 0 and {cls.MAX_VOLTAGE} V, "
                f"not {voltage}."
            )
        return voltage

    @property
    def channel(self) -> int:
        """Get index of the analog output driving the valve, 0 or 1."""
        return self._channel

    @property
    def channel_name(self) -> str:
        """Get the name of the analog output driving the valve, `AO0` or `AO1`."""
        return f"AO{self._channel}"

    @property
    def drive_voltage(self) -> float:
        """Get voltage written to power the valve, in volts."""
        return self._drive_voltage

    @property
    def sense_channel(self) -> Optional[int]:
        """Get the analog input the valve's output is jumpered to, or None."""
        return self._sense_channel

    @property
    def sense_channel_name(self) -> Optional[str]:
        """Get the name of that analog input, `AI0` to `AI7`, or None."""
        if self._sense_channel is None:
            return None
        return f"AI{self._sense_channel}"

    @property
    def can_sense(self) -> bool:
        """Get whether the valve's drive voltage can be read back."""
        return self._sense_channel is not None

    @property
    def state(self) -> bool:
        """Get the selector state. `True` is Gas Mix 1, `False` is Gas Mix 2."""
        return self._state

    @property
    def applied_voltage(self) -> Optional[float]:
        """Get the voltage this session last wrote, or None if it wrote none."""
        return self._applied_voltage

    # Record of past writes #

    @property
    def state_file(self) -> Optional[Path]:
        """Get the path writes are recorded to, or None if they are not."""
        return self._state_file

    @property
    def last_record(self) -> Optional[dict]:
        """Get the last write recorded, from this session or an earlier one.

        This is a log of what this GUI wrote, not a reading. The U12 cannot
        report an analog output, and the station's LabVIEW program drives the
        same output without touching this record, so the valve may well not be
        where the record says. Nothing in this class acts on it.

        Returns:
            Mapping with `timestamp`, `state`, `voltage` and `channel` keys, or
            None if nothing has ever been recorded.
        """
        return self._last_record

    def _read_record(self) -> Optional[dict]:
        """Read the recorded write, or None if there is not a readable one."""
        if self._state_file is None:
            return None
        try:
            with open(self._state_file, encoding="utf-8") as f:
                record = json.load(f)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as e:
            log.warning("Solenoid valve record could not be read: %s", e)
            return None
        if not isinstance(record, dict) or "voltage" not in record:
            log.warning("Solenoid valve record is not in the expected form.")
            return None
        return record

    def _record_write(self, state: bool, voltage: float) -> None:
        """Record a write that has just succeeded.

        A record that cannot be written is logged and otherwise ignored, since
        nothing reads it back and losing it must not cost control of the valve.

        Args:
            state: selector state written, `True` for Gas Mix 1.
            voltage: voltage written, in volts.
        """
        record = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "state": bool(state),
            "label": self.label_for(bool(state)),
            "voltage": float(voltage),
            "channel": self._channel,
            "channel_name": self.channel_name,
        }
        self._last_record = record
        if self._state_file is None:
            return
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._state_file, "w", encoding="utf-8") as f:
                json.dump(record, f, indent=2)
        except OSError as e:
            log.warning("Solenoid valve record could not be written: %s", e)

    def last_record_summary(self) -> str:
        """Describe the last recorded write in one line, for display.

        Returns:
            Human-readable summary, or a note that nothing has been recorded.
        """
        record = self._last_record
        if record is None:
            return "Nothing written by this GUI yet"
        try:
            when = datetime.fromisoformat(record["timestamp"])
            stamp = when.strftime("%Y-%m-%d %H:%M")
        except (KeyError, TypeError, ValueError):
            stamp = "an unknown time"
        # Kept short: this sits in a narrow column, and what it means is in
        # the panel's tooltip rather than in the line itself
        return (
            f"Last wrote {self.label_for(record.get('state'))}, "
            f"{record['voltage']:.2f} V, {stamp}"
        )

    def voltage_for(self, state: bool) -> float:
        """Get the drive voltage a selector state corresponds to.

        Args:
            state: `True` for Gas Mix 1 and no power, `False` for Gas Mix 2.

        Returns:
            Voltage in volts to write to the valve's analog output.
        """
        return 0.0 if state else self._drive_voltage

    @classmethod
    def label_for(cls, state: Optional[bool]) -> str:
        """Get the name of a valve position.

        Args:
            state: `True` for Gas Mix 1 and no power, `False` for Gas Mix 2.

        Returns:
            Name of the position, or a note if `state` is not one of the two.
        """
        return cls.STATE_LABELS.get(state, "an unknown position")

    @classmethod
    def state_for(cls, label: str) -> bool:
        """Get the selector state a valve position's name corresponds to.

        Args:
            label: name of a position, as :attr:`STATE_LABELS` gives it.

        Returns:
            `True` for Gas Mix 1 and no power, `False` for Gas Mix 2.

        Raises:
            ValueError: if `label` names neither position.
        """
        for state, name in cls.STATE_LABELS.items():
            if name == label:
                return state
        raise ValueError(f"{label!r} is not a valve position.")

    def set_connection(self, channel: int, id_num: Optional[int] = None) -> None:
        """Set the analog output and LabJack used by the next write.

        Not reachable from the control panel, which is fixed to the station's
        wiring, see :attr:`DEFAULT_CHANNEL`.

        Args:
            channel: index of the analog output driving the valve, 0 or 1.
            id_num: LabJack local ID, or None to leave it unchanged.

        Raises:
            ValueError: if `channel` is neither 0 nor 1.
        """
        self._channel = self._check_channel(channel)
        if id_num is not None:
            self._id_num = int(id_num)
        self._port = f"LabJack U12 id {self._id_num} AO{self._channel}"

    def set_drive_voltage(self, drive_voltage: float) -> None:
        """Set the voltage written to power the valve.

        Takes effect on the next write, so a change made while the valve is
        powered does not reach it until the state is applied again.

        Args:
            drive_voltage: voltage in volts, from 0 to 5.

        Raises:
            ValueError: if `drive_voltage` is out of range.
        """
        self._drive_voltage = self._check_voltage(drive_voltage)

    # Connection #

    def connect(self) -> None:
        """Load the LabJack driver and check that a U12 answers.

        On Windows the UW driver opens the device on every call rather than
        returning a handle, so creating the driver object only loads
        `ljackuw.dll` and cannot tell whether a U12 is attached. Analog input 0
        is therefore read once as a liveness check. Reading an input leaves
        both analog outputs untouched, so connecting cannot move the valve.

        Raises:
            NupylabError: if the driver cannot be loaded or the U12 does not
                answer.
        """
        try:
            self.u12 = labjack_u12.U12(id=self._id_num)
        except Exception as e:
            self.u12 = None
            raise NupylabError(f"LabJack U12 driver could not be loaded: {e}") from e
        try:
            with self.lock:
                self.u12.eAnalogIn(0, idNum=self._id_num)
        except Exception as e:
            self.u12 = None
            raise NupylabError(f"LabJack U12 did not answer: {e}") from e
        self._connected = True

    def disconnect(self) -> None:
        """Drop the driver reference, leaving the valve as it is.

        Stops the panel monitoring the sense channel first, if it is, so that
        its timer cannot fire against a LabJack that has gone away. Queueing
        an experiment disconnects every control panel, so this is the usual
        way monitoring ends.
        """
        if self._panel is not None:
            try:
                self._panel.stop_monitor()
            except Exception as e:
                log.debug("Solenoid valve monitor could not be stopped: %s", e)
        self.u12 = None
        self._connected = False

    # Control #

    def _write(self, voltage: float) -> None:
        """Write the valve's analog output, holding the other at 0 V.

        Args:
            voltage: voltage in volts for the valve's analog output.

        Raises:
            NupylabError: if the LabJack is not connected.
        """
        if self.u12 is None:
            raise NupylabError("LabJack U12 is not connected.")
        analog_out = [0.0, 0.0]
        analog_out[self._channel] = voltage
        with self.lock:
            self.u12.aoUpdate(
                idNum=self._id_num,
                demo=0,
                trisD=0,
                trisIO=0,
                stateD=0,
                stateIO=0,
                updateDigital=0,  # Leave the 20 digital lines alone
                resetCounter=0,
                analogOut0=analog_out[0],
                analogOut1=analog_out[1],
            )
        self._applied_voltage = voltage
        self._record_write(self._state, voltage)

    def read_input(self, channel: int) -> float:
        """Read one single-ended analog input.

        Reading an input leaves both analog outputs alone, so this can be
        called while the valve is powered without disturbing it.

        Args:
            channel: analog input to read, 0 to 7.

        Returns:
            Voltage in volts. Single-ended inputs span -10 to 10 V in 12 bits,
            so the reading is good to about 5 mV.

        Raises:
            NupylabError: if the LabJack is not connected or does not answer.
            ValueError: if `channel` is not an analog input.
        """
        channel = self._check_sense_channel(channel)
        if channel is None:
            raise ValueError("A channel is needed to read an analog input.")
        if self.u12 is None:
            raise NupylabError("LabJack U12 is not connected.")
        try:
            with self.lock:
                reading = self.u12.eAnalogIn(channel, idNum=self._id_num)
        except Exception as e:
            raise NupylabError(f"LabJack U12 AI{channel} could not be read: {e}") from e
        return float(reading["voltage"])

    def read_sense_voltage(self) -> float:
        """Read the voltage on the valve's drive line through the jumper.

        Returns:
            Voltage in volts on :attr:`sense_channel`.

        Raises:
            NupylabError: if the LabJack is not connected or does not answer.
            ValueError: if there is no sense channel to read.
        """
        if self._sense_channel is None:
            raise ValueError(
                f"{self.name} has no sense channel, so its drive voltage "
                "cannot be read. Jumper the output to an analog input and "
                "pass that input as `sense_channel`."
            )
        return self.read_input(self._sense_channel)

    def survey_inputs(self) -> dict:
        """Read every single-ended analog input once.

        Useful for finding which input a jumper actually lands on, since that
        is a question about the wiring that the U12 cannot answer directly.
        An input that cannot be read is left out rather than failing the lot.

        Returns:
            Mapping of input name to voltage, for those that answered.
        """
        readings = {}
        for channel in self.SENSE_CHANNELS:
            try:
                readings[f"AI{channel}"] = self.read_input(channel)
            except Exception as e:
                log.debug("AI%d could not be read: %s", channel, e)
        return readings

    def set_parameters(self, state: bool) -> None:
        """Set the selector state to apply on the next call to :meth:`start`.

        Args:
            state: `True` for Gas Mix 1 and no power, `False` for Gas Mix 2.
        """
        self._state = bool(state)

    def start(self) -> None:
        """Write the selector state to the valve's analog output.

        Raises:
            NupylabError: if the LabJack is not connected.
        """
        self._write(self.voltage_for(self._state))

    def set_state(self, state: bool) -> None:
        """Set the selector state and write it in one step.

        Args:
            state: `True` for Gas Mix 1 and no power, `False` for Gas Mix 2.

        Raises:
            NupylabError: if the LabJack is not connected.
        """
        self.set_parameters(state)
        self.start()

    def get_data(self) -> DataTuple:
        """Get the voltage last written to the valve's analog output.

        The U12 cannot read an analog output back, so this reports what was
        written rather than a measurement, and is NaN until something is.

        Returns:
            DataTuple of the applied voltage in volts.
        """
        voltage = self._applied_voltage
        return DataTuple(
            self.data_label, math.nan if voltage is None else voltage
        )

    def stop_measurement(self) -> None:
        """Stop measurement. Not implemented, the valve holds its state."""

    def shutdown(self) -> None:
        """Disconnect, leaving the valve powered or not as the user left it.

        Removing power here would mean a shutdown could close a valve the user
        deliberately opened, so the valve is only ever moved by an explicit
        request.
        """
        self.disconnect()

    # Control panel #

    def control_widget(self, abort_callback=None):
        """Return a Qt control panel for this instrument."""
        from pymeasure.display.Qt import QtCore, QtWidgets

        instrument = self

        class SolenoidPanel(QtWidgets.QGroupBox):
            instrument_name = "Solenoid Valve"
            record_columns = [
                "Written Voltage (V)",
                "Sense Voltage (V)",
            ]
            data_recorded = QtCore.Signal(list)
            #: How often monitoring reads the sense channel, in milliseconds.
            MONITOR_INTERVAL_MS = 1000
            # Short enough that a column of its own would be mostly empty, so
            # it goes under the panel before it, the pO2 sensor on S2
            stack_with_previous = True

            def __init__(self):
                super().__init__("Solenoid Valve - LabJack U12")
                self._abort_callback = abort_callback
                self._last_sense = None
                self._surveyed = False
                instrument._panel = self
                # The output and the drive voltage are set in code rather than
                # from here, so they are stated where they can still be seen
                self.setToolTip(
                    f"Writes {instrument.channel_name}, "
                    f"{instrument.label_for(False)} at "
                    f"{instrument.drive_voltage:.2f} V and "
                    f"{instrument.label_for(True)} at 0 V"
                )
                self._setup_ui()

            def _setup_ui(self):
                layout = QtWidgets.QVBoxLayout()
                grid = QtWidgets.QGridLayout()

                grid.addWidget(QtWidgets.QLabel("Gas Mix"), 0, 0)
                self.state_combo = QtWidgets.QComboBox()
                self.state_combo.addItems(
                    [
                        instrument.label_for(True),
                        instrument.label_for(False),
                    ]
                )
                self.state_combo.setToolTip(
                    f"{instrument.label_for(True)} applies no power to the "
                    f"valve, {instrument.label_for(False)} powers it"
                )
                grid.addWidget(self.state_combo, 0, 1)
                grid.setColumnStretch(2, 1)

                layout.addLayout(grid)

                # There is nothing to read from this instrument, so there is no
                # connection to hold open between writes. Apply opens the
                # LabJack if it is not already open, writes, and that is all.
                btn_layout = QtWidgets.QHBoxLayout()
                self.apply_btn = QtWidgets.QPushButton("Apply")
                self.apply_btn.clicked.connect(self.apply_state)
                btn_layout.addWidget(self.apply_btn)

                # The sense channel is a jumper from the valve's output to an
                # analog input, since a U12 will not report an output itself
                self.monitor_btn = QtWidgets.QPushButton("Monitor")
                self.monitor_btn.setCheckable(True)
                self.monitor_btn.setEnabled(instrument.can_sense)
                self.monitor_btn.setStyleSheet(
                    "QPushButton:checked { background-color: #cc0000; "
                    "color: white; }"
                )
                self.monitor_btn.setToolTip(
                    f"Read {instrument.sense_channel_name} every "
                    f"{self.MONITOR_INTERVAL_MS / 1000:.0f} s"
                    if instrument.can_sense
                    else "No analog input is jumpered to the valve's output"
                )
                self.monitor_btn.clicked.connect(self.toggle_monitor)
                btn_layout.addWidget(self.monitor_btn)
                btn_layout.addStretch()
                layout.addLayout(btn_layout)

                self.sense_label = QtWidgets.QLabel(
                    f"{instrument.sense_channel_name}: not monitoring"
                    if instrument.can_sense
                    else "No sense channel"
                )
                # Wraps like the lines below it, since a failure puts a whole
                # error message here and the column is narrow
                self.sense_label.setWordWrap(True)
                self.sense_label.setMinimumHeight(
                    2 * self.sense_label.fontMetrics().height()
                )
                layout.addWidget(self.sense_label)

                self.monitor_timer = QtCore.QTimer(self)
                # Read on the GUI thread rather than a worker, since the UW
                # driver is the one the station's LabVIEW calls in its UI
                # thread, and a read takes milliseconds
                self.monitor_timer.timeout.connect(self.read_sense)

                # One status line rather than separate applied and status
                # lines, which only ever said the same thing, and the panel is
                # stacked under another so its height is worth saving
                self.applied_label = QtWidgets.QLabel(
                    "Nothing written this session"
                )
                self.applied_label.setWordWrap(True)
                # Room for the second line it wraps onto at this column's
                # width, which a layout sizing it from its one-line hint
                # would otherwise leave clipped
                self.applied_label.setMinimumHeight(
                    2 * self.applied_label.fontMetrics().height()
                )
                layout.addWidget(self.applied_label)

                self.record_label = QtWidgets.QLabel(instrument.last_record_summary())
                self.record_label.setWordWrap(True)
                self.record_label.setMinimumHeight(
                    2 * self.record_label.fontMetrics().height()
                )
                self.record_label.setStyleSheet("color: gray;")
                self.record_label.setToolTip(
                    "History of what this GUI wrote. The valve cannot be read "
                    "back, and LabVIEW drives the same output, so this is not "
                    "necessarily where the valve is now."
                )
                layout.addWidget(self.record_label)
                self.setLayout(layout)

            # Helpers #

            def _abort_if_needed(self):
                """Abort a queued experiment, if one is running.

                Only a queued experiment is affected. Anything started from the
                Manual Control tab, recording included, keeps running.
                """
                if self._abort_callback:
                    self._abort_callback()

            def _selected_state(self) -> bool:
                return instrument.state_for(self.state_combo.currentText())

            def _emit_row(self):
                """Send one row to the recorder, blank where there is no value."""
                written = instrument.applied_voltage
                self.data_recorded.emit(
                    [
                        "" if written is None else f"{written:.3f}",
                        "" if self._last_sense is None else f"{self._last_sense:.3f}",
                    ]
                )

            def _show_applied(self):
                voltage = instrument.applied_voltage
                self.record_label.setText(instrument.last_record_summary())
                self.applied_label.setStyleSheet("")
                if voltage is None:
                    self.applied_label.setText("Nothing written this session")
                    return
                self.applied_label.setText(
                    f"{instrument.label_for(instrument.state)}, "
                    f"{instrument.channel_name} at {voltage:.2f} V"
                )

            # Actions #

            def toggle_monitor(self, checked):
                if not checked:
                    self.stop_monitor()
                    return
                try:
                    if not instrument.connected:
                        instrument.connect()
                except Exception as e:
                    self.stop_monitor()
                    self.sense_label.setText(f"Cannot monitor: {e}")
                    self.sense_label.setStyleSheet("color: #cc0000;")
                    _control_log.error("Solenoid valve monitor failed: %s", e)
                    return
                # Read every input once, so that a jumper on an input other
                # than the configured one can be found without a multimeter
                if not self._surveyed:
                    self._surveyed = True
                    readings = instrument.survey_inputs()
                    if readings:
                        _control_log.info(
                            "LabJack analog inputs: %s",
                            ", ".join(
                                f"{name} {volts:.2f} V"
                                for name, volts in readings.items()
                            ),
                        )
                self.monitor_btn.setText("Monitoring...")
                self.monitor_timer.start(self.MONITOR_INTERVAL_MS)
                _control_log.info(
                    "Solenoid valve monitoring %s", instrument.sense_channel_name
                )
                self.read_sense()

            def stop_monitor(self):
                """Stop monitoring, whether the user asked or the LabJack went."""
                self.monitor_timer.stop()
                self.monitor_btn.setChecked(False)
                self.monitor_btn.setText("Monitor")

            def read_sense(self):
                try:
                    voltage = instrument.read_sense_voltage()
                except Exception as e:
                    self.stop_monitor()
                    self.sense_label.setText(f"Cannot monitor: {e}")
                    self.sense_label.setStyleSheet("color: #cc0000;")
                    _control_log.error("Solenoid valve sense read failed: %s", e)
                    return
                self._last_sense = voltage
                self.sense_label.setStyleSheet("")
                self.sense_label.setText(
                    f"{instrument.sense_channel_name}: {voltage:.2f} V"
                )
                self._emit_row()

            def apply_state(self):
                self._abort_if_needed()
                state = self._selected_state()
                try:
                    if not instrument.connected:
                        instrument.connect()
                    instrument.set_state(state)
                    self._show_applied()
                    # Reading straight back confirms the write landed, and
                    # keeps one row per apply rather than two
                    if self.monitor_timer.isActive():
                        self.read_sense()
                    else:
                        self._emit_row()
                    _control_log.info(
                        "Solenoid valve set to %s, %s written %.2f V",
                        instrument.label_for(state),
                        instrument.channel_name,
                        instrument.applied_voltage,
                    )
                except Exception as e:
                    # The valve has not moved, so say so where the applied
                    # state would otherwise be read as current
                    self.applied_label.setText(f"Not applied: {e}")
                    self.applied_label.setStyleSheet("color: #cc0000;")
                    _control_log.error("Solenoid valve write failed: %s", e)

        return SolenoidPanel()
