"""Adapts the Aera MFC driver to a NUPyLab instrument for use with NUPyLab GUIs.

Several Aera mass flow controllers share one serial port, each answering to its
own address, so one instance of :class:`AeraMFC` stands for the whole bank.

An Aera MFC is set and read in percent of its own range. What that percent
flows depends on the gas, so each channel carries a gas and a flow
calibration, `Q = m p + b`, see
:mod:`nupylab.instruments.mfc.mfc_calibration`. A channel with no calibration
for the gas it is flowing can still be set and read in percent; its flow in
sccm reads NaN and a setpoint in sccm is refused rather than guessed. Closing
a channel needs no calibration, since zero flow is zero whatever the gas.

Each channel reports four values: the setpoint and the flow, each in percent
and in sccm. The setpoint is read back from the MFC rather than remembered, so
a setpoint applied from the instrument's own front panel, or by another
program sharing the port, is recorded as well.
"""

from __future__ import annotations

import logging
import math
import time
from typing import List, Optional, Sequence, TYPE_CHECKING, Tuple, Union

from nupylab.drivers import aera_mfc
from nupylab.instruments.mfc.mfc_calibration import MFCCalibration
from nupylab.utilities import DataTuple, NupylabError
from nupylab.utilities.nupylab_instrument import NupylabInstrument

if TYPE_CHECKING:
    from nupylab.drivers.aera_mfc import AeraChannel

# The control log writes straight into the Manual Control tab's log pane
_control_log = logging.getLogger("nupylab.instrument_control")

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


class AeraMFC(NupylabInstrument):
    """A bank of Aera mass flow controllers on one serial port.

    Attributes:
        data_label: labels for DataTuples, four per channel.
        name: name of instrument.
        lock: thread lock for preventing simultaneous calls to instrument.
        aera: AeraMFC driver class with channels for all connected MFCs.
    """

    #: What each channel reports, in the order :meth:`get_data` returns it.
    #: A procedure's DATA_COLUMNS entries are these endings after the
    #: channel's name, e.g. `MFC 1 Flow (cc/min)`.
    #:
    #: The flow columns are written `cc/min` rather than `sccm`, which is the
    #: same number: PyMeasure reads the units in a data column's parentheses
    #: with Pint, which knows `cc/min` and does not know `sccm`, and S4's
    #: ROD-4 records its flows under the same name.
    DATA_SUFFIXES: Tuple[str, ...] = (
        "Setpoint (%)",
        "Flow (%)",
        "Setpoint (cc/min)",
        "Flow (cc/min)",
    )

    #: Units a setpoint may be given in, see :meth:`apply_setpoint`.
    UNITS: Tuple[str, ...] = ("sccm", "%")

    def __init__(
        self,
        port: str,
        addresses: Union[int, Sequence[int]],
        mfc_classes: Union[type, Sequence[type]],
        data_label: Sequence,
        channel_names: Optional[Sequence[str]] = None,
        gases: Optional[Sequence[str]] = None,
        calibrations: Optional[Sequence[Optional[MFCCalibration]]] = None,
        name: str = "Aera MFC",
    ) -> None:
        """Initialize the bank, its labels, and its connection parameters.

        Args:
            port: string name of the port the bank is on, e.g. `ASRL6::INSTR`.
            addresses: MFC addresses, one per channel.
            mfc_classes: MFC channel class per address, or one class for all.
            data_label: labels for DataTuples. Either one sequence of four
                labels per channel, or a flat sequence of four labels per
                channel in channel order, in the order of
                :attr:`DATA_SUFFIXES`. They should match entries in
                DATA_COLUMNS of the calling procedure class.
            channel_names: what to call each channel, e.g. `MFC 1`. Defaults
                to `MFC 1` upwards, in the order of `addresses`.
            gases: gas each channel is flowing. Optional.
            calibrations: flow calibration of each channel, `None` where there
                is none. Optional.
            name: name of instrument.

        Raises:
            ValueError: if the addresses, classes, labels, names, gases and
                calibrations do not describe the same number of channels.
        """
        if isinstance(addresses, int):
            addresses = (addresses,)
        self._addresses: Tuple[int, ...] = tuple(int(a) for a in addresses)
        count = len(self._addresses)
        if not count:
            raise ValueError("An Aera MFC bank needs at least one address.")

        if isinstance(mfc_classes, type) or mfc_classes is None:
            mfc_classes = (mfc_classes,) * count
        self._mfc_classes: Tuple[type, ...] = tuple(mfc_classes)
        if len(self._mfc_classes) != count:
            raise ValueError(
                "Aera MFC addresses and mfc_classes must describe the same "
                "number of channels, got {} and {}.".format(
                    count, len(self._mfc_classes)
                )
            )

        self._label_groups: Tuple[Tuple[str, ...], ...] = self._group_labels(
            data_label, count
        )
        self._channel_names: List[str] = list(
            channel_names
            if channel_names is not None
            else ["MFC {}".format(i + 1) for i in range(count)]
        )
        if len(self._channel_names) != count:
            raise ValueError(
                "Aera MFC channel_names must name each of the {} "
                "channels.".format(count)
            )
        self._gases: List[str] = list(gases) if gases is not None else [""] * count
        if len(self._gases) != count:
            raise ValueError(
                "Aera MFC gases must give a gas for each of the {} "
                "channels.".format(count)
            )
        self._calibrations: List[Optional[MFCCalibration]] = (
            list(calibrations) if calibrations is not None else [None] * count
        )
        if len(self._calibrations) != count:
            raise ValueError(
                "Aera MFC calibrations must give one for each of the {} "
                "channels.".format(count)
            )

        self.aera: Optional[aera_mfc.AeraMFC] = None
        self._port = port
        self._ranges: List[float] = [math.nan] * count
        self._panel = None
        # Set by the control panel, so that a disconnection from elsewhere,
        # e.g. when an experiment is queued, reaches its button. Emitting a Qt
        # signal is safe from any thread; touching widgets is not.
        self._disconnected = None

        flat: List[str] = [label for group in self._label_groups for label in group]
        super().__init__(flat, name)

    @staticmethod
    def _group_labels(data_label: Sequence, count: int) -> Tuple[Tuple[str, ...], ...]:
        """Read `data_label` as four labels per channel, however it was given.

        Args:
            data_label: labels, either grouped per channel or flat.
            count: number of channels.

        Returns:
            One tuple of four labels per channel.

        Raises:
            ValueError: if the labels do not come to four per channel.
        """
        labels = list(data_label)
        width = len(AeraMFC.DATA_SUFFIXES)
        if labels and isinstance(labels[0], (list, tuple)):
            groups = tuple(tuple(str(x) for x in group) for group in labels)
        else:
            groups = tuple(
                tuple(str(x) for x in labels[i * width:(i + 1) * width])
                for i in range(count)
            )
        if len(groups) != count or any(len(group) != width for group in groups):
            raise ValueError(
                "Aera MFC data_label must give {} labels ({}) for each of the "
                "{} channels.".format(
                    width, ", ".join(AeraMFC.DATA_SUFFIXES), count
                )
            )
        return groups

    # What the bank is made of #

    @property
    def channel_count(self) -> int:
        """Get how many MFCs are in the bank."""
        return len(self._addresses)

    @property
    def addresses(self) -> Tuple[int, ...]:
        """Get the address of each channel, in channel order."""
        return self._addresses

    @property
    def channel_names(self) -> List[str]:
        """Get what each channel is called, in channel order."""
        return list(self._channel_names)

    def channel_name(self, index: int) -> str:
        """Get what one channel is called.

        Args:
            index: channel index, counting from zero.

        Returns:
            The channel's name, e.g. `MFC 1`.
        """
        return self._channel_names[index]

    def labels(self, index: int) -> Tuple[str, ...]:
        """Get the four data labels of one channel.

        Args:
            index: channel index, counting from zero.

        Returns:
            The labels, in the order of :attr:`DATA_SUFFIXES`.
        """
        return self._label_groups[index]

    def gas(self, index: int) -> str:
        """Get the gas one channel is flowing.

        Args:
            index: channel index, counting from zero.

        Returns:
            Name of the gas, empty if none has been chosen.
        """
        return self._gases[index]

    def set_gas(self, index: int, gas: str) -> None:
        """Set the gas one channel is flowing.

        The gas is what the calibration is chosen for; nothing is written to
        the MFC, whose own gas table is set on the instrument itself.

        Args:
            index: channel index, counting from zero.
            gas: name of the gas.
        """
        self._gases[index] = str(gas or "")
        self._tell_panel()

    def calibration(self, index: int) -> Optional[MFCCalibration]:
        """Get the flow calibration of one channel.

        Args:
            index: channel index, counting from zero.

        Returns:
            The calibration, or `None` if the channel has none.
        """
        return self._calibrations[index]

    def set_calibration(
        self, index: int, calibration: Optional[MFCCalibration]
    ) -> None:
        """Set the flow calibration of one channel.

        Args:
            index: channel index, counting from zero.
            calibration: the calibration to use, or `None` for none, which
                leaves the channel readable in percent only.
        """
        self._calibrations[index] = calibration
        self._tell_panel()

    def calibrated(self, index: int) -> bool:
        """Get whether one channel can be set and read in sccm.

        Args:
            index: channel index, counting from zero.

        Returns:
            Whether it has a calibration.
        """
        return self._calibrations[index] is not None

    def range(self, index: int) -> float:
        """Get the full scale flow the MFC itself reports, in sccm.

        Read once when the bank connects. This is the MFC's own figure for the
        gas table it is set to, which is not the station's calibration and is
        not used to convert anything; it is reported so that a setpoint can be
        sanity checked against it.

        Args:
            index: channel index, counting from zero.

        Returns:
            Full scale flow in sccm, NaN until the bank has connected.
        """
        return self._ranges[index]

    def _tell_panel(self) -> None:
        """Let the control panel redraw itself, if there is one."""
        panel = self._panel
        if panel is not None:
            try:
                panel.refresh_labels()
            except Exception:  # A panel being torn down must not stop a write
                pass

    # Converting between percent and sccm #

    def percent_to_flow(self, index: int, percent: float) -> float:
        """Convert a setpoint in percent to a flow in sccm.

        Args:
            index: channel index, counting from zero.
            percent: setpoint as a percentage of the MFC range.

        Returns:
            Flow in sccm, or NaN if the channel has no calibration.
        """
        calibration = self._calibrations[index]
        if calibration is None:
            return math.nan
        return calibration.flow(percent)

    def flow_to_percent(self, index: int, flow: float) -> float:
        """Convert a wanted flow in sccm to a setpoint in percent.

        Args:
            index: channel index, counting from zero.
            flow: wanted flow in sccm.

        Returns:
            Setpoint as a percentage of the MFC range.

        Raises:
            NupylabError: if the channel has no calibration, or if the flow
                needs a setpoint outside 0 to 100 percent.
        """
        calibration = self._calibrations[index]
        if calibration is None:
            raise NupylabError(
                "{} has no flow calibration for {}, so it cannot be set in "
                "sccm. Choose or add one, or set it in percent.".format(
                    self.channel_name(index), self._gases[index] or "this gas"
                )
            )
        percent = calibration.percent(flow)
        if not -0.05 <= percent <= 100.05:
            raise NupylabError(
                "{} cannot flow {:g} sccm of {}: that needs a setpoint of "
                "{:.1f} %, and the MFC takes 0 to 100 %.".format(
                    self.channel_name(index),
                    flow,
                    self._gases[index] or "this gas",
                    percent,
                )
            )
        return min(100.0, max(0.0, percent))

    # Connection #

    def set_connection(self, port: str) -> None:
        """Set the port used by the next call to :meth:`connect`.

        Args:
            port: string name of the port, e.g. `ASRL6::INSTR`.
        """
        self._port = port

    def connect(self) -> None:
        """Open the port and add a channel for each address.

        Each MFC's own full scale flow is read once, see :meth:`range`.

        Raises:
            NupylabError: if the port cannot be opened or an MFC does not
                answer, naming which.
        """
        with self.lock:
            self._close_port()
            try:
                self.aera = aera_mfc.AeraMFC(self._port)
            except Exception as e:
                self.aera = None
                raise NupylabError(
                    "Aera MFCs on {} could not be opened: {}".format(self._port, e)
                ) from e
            for address, mfc_class in zip(self._addresses, self._mfc_classes):
                self.aera.add_channel(address, mfc_class)
            for index, address in enumerate(self._addresses):
                try:
                    self._ranges[index] = float(self._channel(index).mfc_range)
                except Exception as e:
                    self._close_port()
                    raise NupylabError(
                        "{} at address {} did not answer on {}: {}".format(
                            self.channel_name(index), address, self._port, e
                        )
                    ) from e
            self._connected = True

    def _channel(self, index: int) -> "AeraChannel":
        """Get the driver channel of one MFC. Call while holding the lock."""
        if self.aera is None:
            raise NupylabError("Aera MFCs are not connected.")
        return self.aera.channels[self._addresses[index]]

    def _close_port(self) -> None:
        """Close the port if it is open. Call while holding the lock."""
        if self.aera is not None:
            try:
                self.aera.adapter.close()
            except Exception:
                pass
            self.aera = None
        self._connected = False

    def disconnect(self) -> None:
        """Close the port, leaving every MFC flowing as it is."""
        if self._panel is not None and self._connected:
            try:
                self._panel.stop_reading()
            except Exception:
                pass
        with self.lock:
            self._close_port()
        if self._disconnected is not None:
            try:
                self._disconnected()
            except Exception:
                pass
        time.sleep(0.2)

    # Measurement #

    def set_parameters(self, setpoints: Sequence[float]) -> None:
        """Set the flows, in sccm, to apply on the next call to :meth:`start`.

        Args:
            setpoints: wanted flow of each channel in sccm, in channel order.

        Raises:
            ValueError: if there is not one setpoint per channel.
        """
        setpoints = list(setpoints)
        if len(setpoints) != self.channel_count:
            raise ValueError(
                "Aera MFC setpoints must give a flow for each of the {} "
                "channels, got {}.".format(self.channel_count, len(setpoints))
            )
        self._parameters = setpoints

    def start(self) -> None:
        """Apply the setpoints given to :meth:`set_parameters`.

        A channel set to zero is closed rather than set to a flow of zero, and
        needs no calibration to be.

        Raises:
            NupylabError: if `set_parameters` was not called first, or if a
                channel set to a non-zero flow has no calibration.
        """
        if self._parameters is None:
            raise NupylabError(
                "`{}` method `set_parameters` must be called before calling "
                "its `start` method.".format(self.__class__.__name__)
            )
        setpoints = self._parameters
        # Worked out before anything is written, so that an uncalibrated
        # channel stops the step rather than leaving the bank half set
        percents = [
            0.0 if not flow else self.flow_to_percent(index, flow)
            for index, flow in enumerate(setpoints)
        ]
        with self.lock:
            for index, (flow, percent) in enumerate(zip(setpoints, percents)):
                self._write_setpoint(index, percent, close=not flow)
        self._parameters = None

    def _write_setpoint(self, index: int, percent: float, close: bool) -> None:
        """Set one channel's setpoint and valve. Call while holding the lock.

        Args:
            index: channel index, counting from zero.
            percent: setpoint as a percentage of the MFC range.
            close: whether to shut the valve rather than flow.
        """
        channel = self._channel(index)
        channel.setpoint = percent
        channel.valve_mode = "close" if close else "flow"

    def apply_setpoint(self, index: int, value: float, unit: str = "sccm") -> float:
        """Set one channel's flow, from the Manual Control tab.

        Args:
            index: channel index, counting from zero.
            value: the setpoint, in `unit`.
            unit: `sccm` or `%`, see :attr:`UNITS`.

        Returns:
            The setpoint written, as a percentage of the MFC range.

        Raises:
            NupylabError: if the unit is not one of :attr:`UNITS`, if the
                setpoint is out of range, or if sccm was asked for on a
                channel with no calibration.
        """
        value = float(value)
        if unit not in self.UNITS:
            raise NupylabError(
                "An Aera MFC setpoint is in {}, not {!r}.".format(
                    " or ".join(self.UNITS), unit
                )
            )
        if value < 0:
            raise NupylabError(
                "{} cannot be set to a negative flow.".format(
                    self.channel_name(index)
                )
            )
        if unit == "%":
            if value > 100:
                raise NupylabError(
                    "{} takes 0 to 100 %, not {:g} %.".format(
                        self.channel_name(index), value
                    )
                )
            percent = value
        elif value == 0:
            percent = 0.0  # Closing needs no calibration
        else:
            percent = self.flow_to_percent(index, value)
        with self.lock:
            self._write_setpoint(index, percent, close=not value)
        return percent

    def close_channel(self, index: int) -> None:
        """Shut one channel's valve and set it to zero.

        Args:
            index: channel index, counting from zero.

        Raises:
            NupylabError: if the bank is not connected.
        """
        with self.lock:
            self._write_setpoint(index, 0.0, close=True)

    def read_channel(self, index: int) -> Tuple[float, float, float, float]:
        """Read one channel's setpoint and flow.

        Args:
            index: channel index, counting from zero.

        Returns:
            Setpoint in percent, flow in percent, setpoint in sccm and flow in
            sccm, the last two NaN on a channel with no calibration. The order
            matches :attr:`DATA_SUFFIXES`.

        Raises:
            NupylabError: if the channel cannot be read.
        """
        with self.lock:
            channel = self._channel(index)
            try:
                setpoint = float(channel.setpoint)
                flow = float(channel.actual_flow)
            except Exception as e:
                raise NupylabError(
                    "{} at address {} could not be read: {}".format(
                        self.channel_name(index), self._addresses[index], e
                    )
                ) from e
        return (
            setpoint,
            flow,
            self.percent_to_flow(index, setpoint),
            self.percent_to_flow(index, flow),
        )

    def read_all(self) -> List[Tuple[float, float, float, float]]:
        """Read every channel's setpoint and flow.

        Returns:
            One tuple per channel, see :meth:`read_channel`.

        Raises:
            NupylabError: if a channel cannot be read, naming which.
        """
        return [self.read_channel(index) for index in range(self.channel_count)]

    def get_data(self) -> List[DataTuple]:
        """Read the setpoint and flow of every channel.

        Returns:
            Four DataTuples per channel, in the order of
            :attr:`DATA_SUFFIXES`.

        Raises:
            NupylabError: if a channel cannot be read, naming which.
        """
        data: List[DataTuple] = []
        for index, readings in enumerate(self.read_all()):
            for label, value in zip(self._label_groups[index], readings):
                data.append(DataTuple(label, value))
        return data

    def stop_measurement(self) -> None:
        """Stop the measurement. Nothing to do: the MFCs keep flowing."""

    def shutdown(self) -> None:
        """Shut every valve and close the port.

        Unlike the solenoid valve, an MFC left flowing is left flowing gas, so
        a shutdown closes them. A channel that cannot be closed is logged and
        the rest are still tried.
        """
        if self._connected:
            for index in range(self.channel_count):
                try:
                    self.close_channel(index)
                except Exception as e:
                    log.warning(
                        "Could not close %s on shutdown: %s",
                        self.channel_name(index),
                        e,
                    )
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
                    self.result.emit(instrument.read_all())
                except Exception as e:
                    self.error.emit(str(e))

        class AeraMFCPanel(QtWidgets.QGroupBox):
            plot_title = "MFC Flows"
            instrument_name = instrument.name
            record_columns = list(instrument.data_label)
            data_recorded = QtCore.Signal(list)
            #: Emitted when the bank is disconnected from anywhere, so that
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
                super().__init__("Mass Flow Controllers — Aera")
                self._worker = None
                self._abort_callback = abort_callback
                self._last_error = None
                self._monitoring = False
                self._last_toggle = 0.0
                self._status = None
                self.live_plot = LivePlotWidget(
                    "MFC Flows",
                    "Flow (%)",
                    n_traces=instrument.channel_count,
                    trace_labels=instrument.channel_names,
                )
                self._setup_ui()
                self.timer = QtCore.QTimer()
                self.timer.timeout.connect(self.update_readings)
                self.monitoring_stopped.connect(lambda: self._set_monitoring(False))
                instrument._panel = self
                instrument._disconnected = self.monitoring_stopped.emit

            def _setup_ui(self):
                layout = QtWidgets.QVBoxLayout()

                self.monitor_btn = QtWidgets.QPushButton(self.START_TEXT)
                self.monitor_btn.setToolTip(
                    "Reads every MFC in the bank. Applying a setpoint does "
                    "not need monitoring to be running."
                )
                self.monitor_btn.clicked.connect(self.toggle_monitoring)
                layout.addWidget(self.monitor_btn)

                # Two lines per MFC: what it is being set to on the first,
                # what it is flowing and the buttons on the second. On one
                # line the panel is wider than the tab has to spare.
                grid = QtWidgets.QGridLayout()
                grid.setHorizontalSpacing(8)
                grid.setVerticalSpacing(2)
                for column, heading in ((1, "Setpoint"), (2, "Units")):
                    label = QtWidgets.QLabel(heading)
                    label.setStyleSheet("font-weight: bold;")
                    grid.addWidget(label, 0, column)

                self.gas_labels = []
                self.value_edits = []
                self.unit_combos = []
                self.set_labels = []
                self.actual_labels = []
                for index in range(instrument.channel_count):
                    top = 1 + 2 * index
                    bottom = top + 1

                    name_label = QtWidgets.QLabel(instrument.channel_name(index))
                    name_label.setStyleSheet("font-weight: bold;")
                    if index:
                        name_label.setContentsMargins(0, 8, 0, 0)
                    grid.addWidget(name_label, top, 0)

                    edit = QtWidgets.QDoubleSpinBox()
                    edit.setDecimals(2)
                    edit.setRange(0.0, 1e6)
                    edit.setKeyboardTracking(False)
                    self.value_edits.append(edit)
                    grid.addWidget(edit, top, 1)

                    units = QtWidgets.QComboBox()
                    units.addItems(list(instrument.UNITS))
                    units.currentTextChanged.connect(
                        lambda _text, i=index: self._on_unit_changed(i)
                    )
                    self.unit_combos.append(units)
                    grid.addWidget(units, top, 2)

                    set_label = QtWidgets.QLabel()
                    set_label.setAlignment(
                        QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter
                    )
                    self.set_labels.append(set_label)
                    grid.addWidget(set_label, top, 3)

                    gas_label = QtWidgets.QLabel()
                    self.gas_labels.append(gas_label)
                    grid.addWidget(gas_label, bottom, 0)

                    buttons = QtWidgets.QWidget()
                    button_layout = QtWidgets.QHBoxLayout(buttons)
                    button_layout.setContentsMargins(0, 0, 0, 0)
                    button_layout.setSpacing(4)
                    apply_btn = QtWidgets.QPushButton("Apply")
                    apply_btn.clicked.connect(
                        lambda _checked=False, i=index: self.apply_setpoint(i)
                    )
                    button_layout.addWidget(apply_btn)
                    close_btn = QtWidgets.QPushButton("Close")
                    close_btn.setToolTip("Shut this MFC's valve at once.")
                    close_btn.clicked.connect(
                        lambda _checked=False, i=index: self.close_channel(i)
                    )
                    button_layout.addWidget(close_btn)
                    button_layout.addStretch(1)
                    grid.addWidget(buttons, bottom, 1, 1, 2)

                    actual_label = QtWidgets.QLabel()
                    actual_label.setStyleSheet("font-weight: bold;")
                    actual_label.setAlignment(
                        QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter
                    )
                    self.actual_labels.append(actual_label)
                    grid.addWidget(actual_label, bottom, 3)

                grid.setColumnStretch(4, 1)
                layout.addLayout(grid)
                self._clear_readouts()

                self.status_label = QtWidgets.QLabel("Status: Not connected")
                self.status_label.setWordWrap(True)
                layout.addWidget(self.status_label)

                self.setLayout(layout)
                self.refresh_labels()

            # What the panel says about itself #

            def refresh_labels(self):
                """Say which gas each MFC is flowing, and whether it is calibrated."""
                for index, label in enumerate(self.gas_labels):
                    gas = instrument.gas(index)
                    name = instrument.channel_name(index)
                    label.setText(gas or "no gas chosen")
                    calibration = instrument.calibration(index)
                    if calibration is None:
                        label.setStyleSheet("color: #cc0000;")
                        label.setToolTip(
                            "No flow calibration for {} in {}, so it can only "
                            "be set in percent.".format(
                                name, gas or "this gas"
                            )
                        )
                    else:
                        label.setStyleSheet("")
                        label.setToolTip(calibration.describe())
                    self._on_unit_changed(index)

            def _on_unit_changed(self, index):
                """Keep the setpoint box's suffix and limits with its unit."""
                unit = self.unit_combos[index].currentText()
                edit = self.value_edits[index]
                edit.setSuffix(" %" if unit == "%" else " sccm")
                if unit == "%":
                    edit.setRange(0.0, 100.0)
                    edit.setToolTip("Setpoint as a percentage of the MFC range.")
                    return
                calibration = instrument.calibration(index)
                if calibration is None:
                    edit.setRange(0.0, 1e6)
                    edit.setToolTip(
                        "{} has no calibration for {}: only 0 sccm, which "
                        "closes it, can be applied.".format(
                            instrument.channel_name(index),
                            instrument.gas(index) or "this gas",
                        )
                    )
                    return
                top = max(calibration.flow(0.0), calibration.flow(100.0))
                edit.setRange(0.0, max(top, 0.0))
                edit.setToolTip(
                    "0 to {:.4g} sccm on this calibration.".format(top)
                )

            def _format(self, index, percent, flow):
                """Write one reading as both a percentage and a flow."""
                if percent is None or not math.isfinite(percent):
                    return self.NO_READING
                if flow is None or not math.isfinite(flow):
                    return "{:.1f} %".format(percent)
                return "{:.1f} % / {:.2f} sccm".format(percent, flow)

            def _show_set(self, index, percent, flow):
                """Write one MFC's setpoint, on its first line."""
                self.set_labels[index].setText(
                    "Set: {}".format(self._format(index, percent, flow))
                )

            def _show_actual(self, index, percent, flow):
                """Write one MFC's flow, on its second line."""
                self.actual_labels[index].setText(
                    "Actual: {}".format(self._format(index, percent, flow))
                )

            def _clear_readouts(self):
                for label in self.set_labels:
                    label.setText("Set: {}".format(self.NO_READING))
                for label in self.actual_labels:
                    label.setText("Actual: {}".format(self.NO_READING))

            def _abort_if_needed(self):
                if self._abort_callback:
                    self._abort_callback()

            def _set_monitoring(self, monitoring):
                """Keep the single button in step with the instrument."""
                self._monitoring = monitoring
                self.monitor_btn.setText(
                    self.STOP_TEXT if monitoring else self.START_TEXT
                )

            def stop_reading(self):
                """Stop the read timer and let a reading in flight finish."""
                self.timer.stop()
                if self._worker and self._worker.isRunning():
                    self._worker.wait(2000)

            # Actions #

            def toggle_monitoring(self):
                """Start reading the bank, or stop if it already is.

                Ignores a repeat click that lands within
                :attr:`TOGGLE_GUARD_S` of the previous one finishing, so that
                a double click cannot start monitoring and stop it again.
                """
                now = time.monotonic()
                if now - self._last_toggle < self.TOGGLE_GUARD_S:
                    _control_log.info(
                        "Ignoring repeat click on the MFC monitoring button"
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
                    if not instrument.connected:
                        instrument.connect()
                    self._set_monitoring(True)
                    self._show_status("Monitoring")
                    self._last_error = None
                    self.live_plot.clear()
                    self.timer.start(self.READ_INTERVAL_MS)
                    _control_log.info(
                        "Aera MFCs connected on %s, addresses %s",
                        instrument._port,
                        ", ".join(str(a) for a in instrument.addresses),
                    )
                except Exception as e:
                    self._set_monitoring(False)
                    self._show_status("Error — {}".format(e), steady=False)
                    _control_log.error("Aera MFC connect failed: %s", e)

            def disconnect_instrument(self):
                self._abort_if_needed()
                try:
                    self.stop_reading()
                    instrument.disconnect()
                    self._set_monitoring(False)
                    self._show_status("Not monitoring")
                    self._clear_readouts()
                    _control_log.info("Aera MFCs disconnected")
                except Exception as e:
                    self._show_status("Error — {}".format(e), steady=False)

            def _ensure_connected(self):
                """Open the port for a write, without starting monitoring."""
                if not instrument.connected:
                    instrument.connect()

            def apply_setpoint(self, index):
                """Set one MFC to what its row says, and read it back once."""
                self._abort_if_needed()
                name = instrument.channel_name(index)
                try:
                    self._ensure_connected()
                    unit = self.unit_combos[index].currentText()
                    value = self.value_edits[index].value()
                    percent = instrument.apply_setpoint(index, value, unit)
                except Exception as e:
                    self._show_status("{} not set — {}".format(name, e), steady=False)
                    _control_log.error("%s setpoint failed: %s", name, e)
                    return
                flow = instrument.percent_to_flow(index, percent)
                self._show_set(index, percent, flow)
                self._show_status("{} set".format(name))
                _control_log.info(
                    "%s set to %s", name, self._format(index, percent, flow)
                )
                self.update_readings()

            def close_channel(self, index):
                """Shut one MFC's valve."""
                self._abort_if_needed()
                name = instrument.channel_name(index)
                try:
                    self._ensure_connected()
                    instrument.close_channel(index)
                except Exception as e:
                    self._show_status(
                        "{} not closed — {}".format(name, e), steady=False
                    )
                    _control_log.error("%s close failed: %s", name, e)
                    return
                self.value_edits[index].setValue(0.0)
                self._show_set(index, 0.0, 0.0)
                self._show_status("{} closed".format(name))
                _control_log.info("%s valve closed", name)
                self.update_readings()

            def _show_status(self, status, steady=True):
                """Write the status line, leaving a steady one alone."""
                if steady and status == self._status:
                    return
                self._status = status if steady else None
                self.status_label.setText("Status: {}".format(status))

            def update_readings(self):
                if not instrument.connected:
                    return
                if self._worker and self._worker.isRunning():
                    return
                self._worker = Worker()

                def on_result(readings):
                    flows = []
                    recorded = []
                    for index, values in enumerate(readings):
                        set_pct, flow_pct, set_sccm, flow_sccm = values
                        self._show_set(index, set_pct, set_sccm)
                        self._show_actual(index, flow_pct, flow_sccm)
                        flows.append(flow_pct)
                        recorded.extend(values)
                    self.live_plot.add_points(flows)
                    self.data_recorded.emit(recorded)
                    self._last_error = None
                    if self._monitoring:
                        self._show_status("Monitoring")

                def on_error(message):
                    # Only on a change, so the log stays readable
                    if message != self._last_error:
                        self._last_error = message
                        self._show_status(
                            "Read error — {}".format(message), steady=False
                        )
                        _control_log.error("Aera MFC read failed: %s", message)

                self._worker.result.connect(on_result)
                self._worker.error.connect(on_error)
                self._worker.start()

        return AeraMFCPanel()
