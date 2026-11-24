"""Generic individual instrument control widget for NUPyLab GUIs."""

from __future__ import annotations

import csv
import logging
import math
import os
from datetime import datetime
from typing import List, Callable, Optional

from pymeasure.display.Qt import QtWidgets, QtCore

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

# Separate logger for instrument control only
_control_log = logging.getLogger('nupylab.instrument_control')


#: How many of a base unit one of each offered unit is, e.g. one kHz is a
#: thousand Hz. An entry holds what it is given in the base unit, whichever
#: of these it is read in.
FREQUENCY_UNITS = (("MHz", 1e6), ("kHz", 1e3), ("Hz", 1.0), ("mHz", 1e-3))
POTENTIAL_UNITS = (("V", 1.0), ("mV", 1e-3))
CURRENT_UNITS = (("A", 1.0), ("mA", 1e-3))


class UnitEntry(QtWidgets.QWidget):
    """A number and the unit it is read in, drawn side by side.

    The unit sits to the right of the box rather than inside it. Where a
    quantity is worth giving in more than one unit it is a drop-down, and
    where there is only one it is a plain label, so that every row of a panel
    reads the same way whether or not there is a choice to make.

    What the entry holds is the quantity itself, in the base unit, and that is
    what :meth:`value` gives back: choosing a different unit redraws the same
    quantity in it rather than changing what was asked for. The range is kept
    in the base unit too, so it means the same under every unit, and the
    number of decimal places follows the unit, since a frequency read in MHz
    needs places that the same frequency read in mHz does not.
    """

    #: Fewest and most decimal places to draw a value with, whatever unit it
    #: is read in: enough to be worth typing, few enough to read at a glance.
    MIN_DECIMALS = 2
    MAX_DECIMALS = 6

    #: Emitted when the number or the unit changes, with the value in the
    #: base unit.
    value_changed = QtCore.Signal(float)

    def __init__(
        self,
        units,
        minimum: float,
        maximum: float,
        value: float = 0.0,
        decimals: int = 4,
        default: Optional[str] = None,
        parent=None,
    ) -> None:
        """Build the entry.

        Args:
            units: the units on offer, as `(name, size in base units)` pairs.
                One pair gives a label rather than a drop-down.
            minimum: least value the entry takes, in the base unit.
            maximum: most it takes, in the base unit.
            value: what it opens holding, in the base unit.
            decimals: decimal places to draw the base unit with. Another unit
                is given the places that match it, within `MIN_DECIMALS` and
                `MAX_DECIMALS`.
            default: name of the unit to open on. Defaults to the first.
            parent: parent widget.
        """
        super().__init__(parent)
        self._units = tuple(units)
        self._minimum = float(minimum)
        self._maximum = float(maximum)
        self._decimals = int(decimals)
        self._value = float(value)
        self._factor = 1.0
        # Set while the entry is redrawing itself, so that the boxes changing
        # under it are not taken for the user changing what was asked for
        self._redrawing = False

        # Keyboard tracking is left as a plain spin box has it, so that a
        # value typed and not entered is still the value when Run is pressed
        self.spinbox = QtWidgets.QDoubleSpinBox()
        self.spinbox.valueChanged.connect(self._on_number_changed)

        if len(self._units) > 1:
            self.unit = QtWidgets.QComboBox()
            self.unit.setSizeAdjustPolicy(
                QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToContents
            )
            self.unit.currentIndexChanged.connect(self._on_unit_changed)
        else:
            self.unit = QtWidgets.QLabel()

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.spinbox, 1)
        layout.addWidget(self.unit, 0)

        self.set_units(self._units, default)

    # What unit the number is read in #

    def set_units(self, units, default: Optional[str] = None) -> None:
        """Offer a different set of units, keeping the quantity as it stands.

        A panel whose technique decides whether an entry is a potential or a
        current swaps one set for the other this way. Where the new set is as
        long as the old one and no default is named, the entry stays on the
        unit at the same place in it, so a user reading in mV goes on reading
        in mA rather than being moved back to A.

        Args:
            units: the units on offer, as `(name, size in base units)` pairs.
            default: name of the unit to show. Defaults to the one at the
                place the current unit holds, or the first.
        """
        previous = self.unit.currentIndex() if self._is_choice else 0
        self._units = tuple(units)
        index = previous if previous < len(self._units) else 0
        if default is not None:
            for i, (name, _) in enumerate(self._units):
                if name == default:
                    index = i
                    break
        if self._is_choice:
            self._redrawing = True
            try:
                self.unit.clear()
                self.unit.addItems([name for name, _ in self._units])
                self.unit.setCurrentIndex(index)
            finally:
                self._redrawing = False
        else:
            self.unit.setText(self._units[index][0])
        self._redraw()

    @property
    def _is_choice(self) -> bool:
        """Whether the unit is chosen from a drop-down rather than fixed."""
        return isinstance(self.unit, QtWidgets.QComboBox)

    @property
    def unit_name(self) -> str:
        """Name of the unit the entry is being read in."""
        return self._units[self._index][0]

    @property
    def _index(self) -> int:
        """Which unit of the set is in use."""
        if not self._is_choice:
            return 0
        return max(self.unit.currentIndex(), 0)

    def _redraw(self) -> None:
        """Draw the quantity in the unit now chosen."""
        _, factor = self._units[self._index]
        # A unit larger than the base needs the decimal places the base does
        # not, and a smaller one needs fewer
        decimals = self._decimals + int(round(math.log10(factor)))
        decimals = max(self.MIN_DECIMALS, min(self.MAX_DECIMALS, decimals))
        smallest = 10.0 ** -decimals
        low = self._minimum / factor
        high = self._maximum / factor
        # A range that starts above zero must go on doing so once it is drawn
        # in a unit too large to show how little it starts at
        if low > 0:
            low = max(low, smallest)
        self._redrawing = True
        try:
            self.spinbox.setDecimals(decimals)
            self.spinbox.setRange(low, high)
            self.spinbox.setValue(self._value / factor)
        finally:
            self._redrawing = False
        self._factor = factor
        # What the box can show is what the entry holds, so that the quantity
        # and the number drawn never part company
        self._value = self.spinbox.value() * factor

    def _on_unit_changed(self, *_) -> None:
        if self._redrawing:
            return
        self._redraw()
        self.value_changed.emit(self._value)

    def _on_number_changed(self, *_) -> None:
        if self._redrawing:
            return
        self._value = self.spinbox.value() * self._factor
        self.value_changed.emit(self._value)

    # The interface a plain spin box offers #

    def value(self) -> float:
        """Get what the entry holds, in the base unit."""
        return self._value

    def setValue(self, value) -> None:
        """Set what the entry holds, in the base unit."""
        self._value = float(value)
        self._redraw()


class QtLogHandler(logging.Handler):
    """Logging handler that writes to a QTextEdit widget."""

    def __init__(self, text_widget):
        super().__init__()
        self.widget = text_widget

    def emit(self, record):
        msg = self.format(record)
        self.widget.append(msg)


class DataRecorder:
    """Records instrument data to a CSV file."""

    def __init__(self, instrument_name: str, columns: List[str], directory: str):
        """Initialize recorder.

        Args:
            instrument_name: name used for folder and filename prefix
            columns: list of column header strings
            directory: base data directory (same as experiment data directory)
        """
        self._instrument_name = instrument_name
        self._columns = columns
        self._directory = directory
        self._file = None
        self._writer = None
        self._filepath = None
        self._recording = False
        self._start_time = None

    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def filepath(self) -> Optional[str]:
        return self._filepath

    def start(self) -> str:
        """Start recording — create file and write header. Returns filepath."""
        import time
        # Resolve directory (may be callable to get current window directory)
        base_dir = self._directory() if callable(self._directory) else self._directory
        # Create instrument subfolder
        folder = os.path.join(base_dir, self._instrument_name)
        os.makedirs(folder, exist_ok=True)

        # Generate filename: InstrumentName_YYYY-MM-DD_HHMMSS.csv
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        filename = f"{self._instrument_name}_{timestamp}.csv"
        self._filepath = os.path.join(folder, filename)

        self._file = open(self._filepath, 'w', newline='')
        self._writer = csv.writer(self._file)
        self._writer.writerow(["System Time", "Time (s)"] + self._columns)
        self._file.flush()
        self._recording = True
        self._start_time = time.monotonic()
        _control_log.info("Recording started: %s", self._filepath)
        return self._filepath

    def write(self, values: List):
        """Write a row of data."""
        if not self._recording or self._writer is None:
            return
        import time
        elapsed = time.monotonic() - self._start_time
        row = [datetime.now().isoformat(), f"{elapsed:.3f}"] + [str(v) for v in values]
        self._writer.writerow(row)
        self._file.flush()

    def save(self):
        """Stop recording and keep the file."""
        if self._file:
            self._file.close()
            self._file = None
            self._writer = None
        self._recording = False
        _control_log.info("Recording saved: %s", self._filepath)

    def delete(self):
        """Stop recording and delete the file."""
        filepath = self._filepath
        if self._file:
            self._file.close()
            self._file = None
            self._writer = None
        self._recording = False
        if filepath and os.path.exists(filepath):
            os.remove(filepath)
            _control_log.info("Recording deleted: %s", filepath)
        self._filepath = None


class RecordingControlWidget(QtWidgets.QGroupBox):
    """Widget with Record/Save/Delete buttons for data recording."""

    def __init__(self, recorder: DataRecorder, parent=None):
        super().__init__("Data Recording", parent)
        self._recorder = recorder
        self._setup_ui()

    def _setup_ui(self):
        layout = QtWidgets.QHBoxLayout()

        self.record_btn = QtWidgets.QPushButton("Record")
        self.record_btn.setCheckable(True)
        self.record_btn.setStyleSheet(
            "QPushButton:checked { background-color: #cc0000; color: white; }"
        )
        self.record_btn.clicked.connect(self._on_record)

        self.save_btn = QtWidgets.QPushButton("Save")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._on_save)

        self.delete_btn = QtWidgets.QPushButton("Delete")
        self.delete_btn.setEnabled(False)
        self.delete_btn.clicked.connect(self._on_delete)

        self.status_label = QtWidgets.QLabel("Not recording")
        self.status_label.setStyleSheet("color: gray;")

        layout.addWidget(self.record_btn)
        layout.addWidget(self.save_btn)
        layout.addWidget(self.delete_btn)
        layout.addWidget(self.status_label)
        self.setLayout(layout)

    def _on_record(self, checked):
        if checked:
            try:
                filepath = self._recorder.start()
                fname = os.path.basename(filepath)
                self.status_label.setText(f"Recording: {fname}")
                self.status_label.setStyleSheet("color: red;")
                self.save_btn.setEnabled(True)
                self.delete_btn.setEnabled(True)
                self.record_btn.setText("Recording...")
            except Exception as e:
                self.record_btn.setChecked(False)
                self.status_label.setText(f"Error: {e}")
                _control_log.error("Recording start failed: %s", e)
        else:
            # User unchecked — treat as save
            self._on_save()

    def _on_save(self):
        self._recorder.save()
        self.record_btn.setChecked(False)
        self.record_btn.setText("Record")
        self.save_btn.setEnabled(False)
        self.delete_btn.setEnabled(False)
        self.status_label.setText("Saved")
        self.status_label.setStyleSheet("color: green;")

    def _on_delete(self):
        self._recorder.delete()
        self.record_btn.setChecked(False)
        self.record_btn.setText("Record")
        self.save_btn.setEnabled(False)
        self.delete_btn.setEnabled(False)
        self.status_label.setText("Deleted")
        self.status_label.setStyleSheet("color: gray;")


class LivePlotWidget(QtWidgets.QWidget):
    """Live plot for instrument data using pyqtgraph, with fallback to QLabel."""

    def __init__(self, title: str, y_label: str, n_traces: int = 1,
                 trace_labels: List[str] = None, parent=None):
        super().__init__(parent)
        self.title = title
        self.y_label = y_label
        self.n_traces = n_traces
        self.trace_labels = trace_labels or [f"Trace {i+1}" for i in range(n_traces)]
        self._data = [[] for _ in range(n_traces)]
        self._times = []
        self._current_trace = 0
        self._start_time = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        try:
            import pyqtgraph as pg
            self._use_pg = True
            self._plot_widget = pg.PlotWidget()
            self._plot_widget.setTitle(self.title)
            self._plot_widget.setLabel('left', self.y_label)
            self._plot_widget.setLabel('bottom', 'Time (s)')
            self._plot_widget.setMinimumHeight(200)
            if self.n_traces > 1:
                self._plot_widget.addLegend()
            colors = ['r', 'g', 'b', 'y', 'c', 'm']
            self._curves = []
            for i in range(self.n_traces):
                curve = self._plot_widget.plot(
                    pen=colors[i % len(colors)],
                    name=self.trace_labels[i] if self.n_traces > 1 else None
                )
                self._curves.append(curve)
            layout.addWidget(self._plot_widget)
        except ImportError:
            self._use_pg = False
            self._value_label = QtWidgets.QLabel("—")
            self._value_label.setStyleSheet(
                "font-size: 28px; font-weight: bold; padding: 20px;"
            )
            self._value_label.setAlignment(QtCore.Qt.AlignCenter)
            self._value_label.setMinimumHeight(100)
            title_label = QtWidgets.QLabel(f"{self.title}")
            title_label.setStyleSheet("font-weight: bold;")
            title_label.setAlignment(QtCore.Qt.AlignCenter)
            layout.addWidget(title_label)
            layout.addWidget(self._value_label)

        if self.n_traces > 1 and not self._use_pg:
            selector_layout = QtWidgets.QHBoxLayout()
            selector_layout.addWidget(QtWidgets.QLabel("Showing:"))
            self._trace_selector = QtWidgets.QComboBox()
            self._trace_selector.addItems(self.trace_labels)
            self._trace_selector.currentIndexChanged.connect(self._on_trace_changed)
            selector_layout.addWidget(self._trace_selector)
            layout.addLayout(selector_layout)

        self.setLayout(layout)

    def _on_trace_changed(self, index):
        self._current_trace = index
        if not self._use_pg and self._data[index]:
            self._value_label.setText(
                f"{self._data[index][-1]:.2f} {self.y_label}"
            )

    def add_point(self, value, trace_index: int = 0):
        """Add a data point to the specified trace."""
        import time
        if self._start_time is None:
            self._start_time = time.monotonic()
        t = time.monotonic() - self._start_time
        if len(self._times) <= len(self._data[trace_index]):
            self._times.append(t)
        self._data[trace_index].append(value)

        if self._use_pg:
            n = min(len(self._times), len(self._data[trace_index]))
            self._curves[trace_index].setData(
                self._times[-200:n], self._data[trace_index][-200:]
            )
        else:
            if trace_index == self._current_trace:
                self._value_label.setText(
                    f"{value:.2f} {self.y_label}"
                )

    def add_points(self, values: List):
        """Add one point per trace simultaneously."""
        import time
        if self._start_time is None:
            self._start_time = time.monotonic()
        t = time.monotonic() - self._start_time
        self._times.append(t)

        for i, v in enumerate(values):
            if i < self.n_traces:
                self._data[i].append(v)
                if self._use_pg:
                    n = min(len(self._times), len(self._data[i]))
                    self._curves[i].setData(
                        self._times[-200:n], self._data[i][-200:]
                    )
        if not self._use_pg and values:
            idx = self._current_trace if hasattr(self, '_current_trace') else 0
            if idx < len(values):
                self._value_label.setText(
                    f"{values[idx]:.2f} {self.y_label}"
                )

    def set_xy(self, x_data: List, y_data: List):
        """Set X/Y data directly (for Nyquist plots)."""
        if self._use_pg:
            self._plot_widget.clear()
            self._plot_widget.plot(
                x_data, y_data, pen='r', symbol='o', symbolSize=5
            )
        else:
            if y_data:
                self._value_label.setText(
                    f"max: {max(y_data):.2e}"
                )

    def set_labels(self, x_label: str, y_label: str):
        """Update axis labels."""
        if self._use_pg:
            self._plot_widget.setLabel('bottom', x_label)
            self._plot_widget.setLabel('left', y_label)

    def set_log_y(self, enabled: bool = True):
        """Plot the y axis on a log scale, for data spanning several decades.

        Values are still added in linear units; pyqtgraph transforms them.
        Non-positive values cannot be shown on a log axis.
        """
        if self._use_pg:
            self._plot_widget.setLogMode(False, enabled)

    def clear(self):
        """Clear all data."""
        self._data = [[] for _ in range(self.n_traces)]
        self._times = []
        self._start_time = None
        if self._use_pg:
            for curve in self._curves:
                curve.setData([], [])
        else:
            self._value_label.setText("—")


class InstrumentControlWidget(QtWidgets.QWidget):
    """Generic tab widget that assembles control panels from instruments.

    Panels are laid out left to right in the order their instruments are
    given. A panel that sets `stack_with_previous = True` is placed beneath
    the panel before it instead of starting a column of its own, which suits a
    short panel that would otherwise waste a column's width.
    """

    def __init__(self, instruments: List = None,
                 abort_callback: Optional[Callable] = None,
                 scanner=None,
                 directory = "",  # str or callable returning str
                 recording_beside_panels: bool = False,
                 fit_panels_height: bool = False,
                 button_feedback: bool = False,
                 column_width_from: Optional[str] = None,
                 column_width: Optional[int] = None,
                 parent=None):
        """
        Args:
            recording_beside_panels: place Data Recording controls as a pane next
                to the instrument panels instead of in a row below them.
            fit_panels_height: size the top pane to show the tallest instrument
                panel in full instead of the default fixed height.
            button_feedback: darken buttons from press until their action
                finishes, so slow actions like connecting show the click registered.
            column_width_from: `instrument_name` of the panel whose width a
                stacked column should take, so that columns line up and the
                room left over is predictable. Columns are never narrowed
                below what they need. Ignored for panels in a column of their
                own, and when no panel answers to the name.
            column_width: width in pixels for a stacked column, used instead
                of `column_width_from` when both are given. For a column of
                short panels that would otherwise be held to the width of a
                much larger one beside it. A panel with a label set to wrap
                takes the extra lines it needs.
        """
        super().__init__(parent)
        self._panels = []
        self._abort_callback = abort_callback
        self._experiment_running = False
        self._scanner = scanner
        self._directory = directory
        self._recording_beside_panels = recording_beside_panels
        self._fit_panels_height = fit_panels_height
        self._column_width_from = column_width_from
        self._column_width = column_width
        self._setup_ui(instruments or [])
        if button_feedback:
            self._add_button_feedback()

    _BUSY_MIN_MS = 300  # keep busy shading visible for fast actions

    #: Width in pixels the Data Recording pane is held to where it stands as a
    #: column beside the instrument panels. What is recording is named in it,
    #: which with several instruments ticked is longer than the pane wants to
    #: be, so it is capped and the line wraps inside it.
    RECORDING_PANE_WIDTH = 240

    def _add_button_feedback(self):
        self.setStyleSheet(
            self.styleSheet()
            + 'QPushButton[busy="true"] { background-color: #505050; color: white; }'
        )
        for button in self.findChildren(QtWidgets.QPushButton):
            button.pressed.connect(lambda b=button: self._set_button_busy(b, True))
            # Timer cannot fire until the clicked handler returns control to Qt
            button.released.connect(
                lambda b=button: QtCore.QTimer.singleShot(
                    self._BUSY_MIN_MS, lambda: self._set_button_busy(b, False)
                )
            )

    @staticmethod
    def _set_button_busy(button, busy: bool):
        button.setProperty("busy", busy)
        button.style().unpolish(button)
        button.style().polish(button)
        # Paint now; slow handlers block the event loop until they return
        button.repaint()

    def _setup_ui(self, instruments):
        layout = QtWidgets.QVBoxLayout()

        # Top: instrument control panels in horizontal scroll
        panels_scroll = QtWidgets.QScrollArea()
        panels_scroll.setWidgetResizable(True)
        panels_scroll.setMaximumHeight(350)
        panels_scroll.setMinimumHeight(210)
        container = QtWidgets.QWidget()
        panels_layout = QtWidgets.QHBoxLayout(container)
        panels_layout.setAlignment(QtCore.Qt.AlignLeft)

        for instrument in instruments:
            if hasattr(instrument, 'control_widget'):
                import inspect
                cw_params = inspect.signature(instrument.control_widget).parameters
                kwargs = {'abort_callback': self._on_control_action}
                if 'scanner' in cw_params:
                    kwargs['scanner'] = self._scanner
                panel = instrument.control_widget(**kwargs)
                self._panels.append(panel)

        # Panels sit side by side, except that a short one may ask to go under
        # the panel before it rather than take a column of its own. Grouping
        # them first keeps the single-panel case laid out exactly as before.
        columns: List[List] = []
        for panel in self._panels:
            if columns and getattr(panel, 'stack_with_previous', False):
                columns[-1].append(panel)
            else:
                columns.append([panel])

        reference_width = self._column_width or self._reference_panel_width()

        for column in columns:
            if len(column) == 1:
                panels_layout.addWidget(column[0])
                continue
            holder = QtWidgets.QWidget()
            column_layout = QtWidgets.QVBoxLayout(holder)
            column_layout.setContentsMargins(0, 0, 0, 0)
            column_layout.setAlignment(QtCore.Qt.AlignTop)
            for panel in column:
                column_layout.addWidget(panel)
            if reference_width:
                # Never below what the column needs, since a fixed width
                # overrides the minimum Qt would otherwise respect and the
                # panels would be clipped rather than merely crowded
                holder.setFixedWidth(
                    max(reference_width, holder.minimumSizeHint().width())
                )
            panels_layout.addWidget(holder)

        # Recording controls — a box per instrument, and record/save/delete
        rec_group = QtWidgets.QGroupBox("Data Recording")
        if self._recording_beside_panels:
            rec_layout = QtWidgets.QVBoxLayout()
            rec_layout.setAlignment(QtCore.Qt.AlignTop)
        else:
            rec_layout = QtWidgets.QHBoxLayout()

        rec_layout.addWidget(QtWidgets.QLabel("Instruments:"))
        # One box each rather than one choice between them: an experiment run
        # by hand is usually several instruments at once, and each writes its
        # own file anyway, since they have their own columns and their own
        # idea of how often there is something to write
        self._instrument_checks = []
        for panel in self._panels:
            name = self._panel_name(panel)
            box = QtWidgets.QCheckBox(name)
            box.setToolTip(
                "Record {} to a file of its own, in a {} folder beside the "
                "experiment data.".format(name, name)
            )
            self._instrument_checks.append(box)
            rec_layout.addWidget(box)

        self._record_btn = QtWidgets.QPushButton("Record")
        self._record_btn.setCheckable(True)
        self._record_btn.setStyleSheet(
            "QPushButton:checked { background-color: #cc0000; color: white; }"
        )
        self._record_btn.clicked.connect(self._on_record)
        rec_layout.addWidget(self._record_btn)

        self._save_btn = QtWidgets.QPushButton("Save")
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._on_save)
        rec_layout.addWidget(self._save_btn)

        self._delete_btn = QtWidgets.QPushButton("Delete")
        self._delete_btn.setEnabled(False)
        self._delete_btn.clicked.connect(self._on_delete)
        rec_layout.addWidget(self._delete_btn)

        self._rec_status = QtWidgets.QLabel("Not recording")
        self._rec_status.setStyleSheet("color: gray;")
        # Names what is recording, or what failed to start, either of which
        # runs past the width of the pane. It wraps rather than stretching it,
        # and keeps room for the lines it wraps onto, since a layout sizing a
        # wrapped label from its one-line hint would leave them clipped.
        self._rec_status.setWordWrap(True)
        self._rec_status.setMinimumHeight(
            (3 if self._recording_beside_panels else 2)
            * self._rec_status.fontMetrics().height()
        )
        rec_layout.addWidget(self._rec_status)

        rec_group.setLayout(rec_layout)
        if self._recording_beside_panels:
            # A column of its own, so it is held to a width the status line
            # can wrap inside rather than one it can stretch
            rec_group.setMaximumWidth(self.RECORDING_PANE_WIDTH)

        if self._recording_beside_panels:
            panels_layout.addWidget(rec_group)
        panels_scroll.setWidget(container)
        if self._fit_panels_height:
            # Tallest pane plus room for the frame and horizontal scrollbar
            fit_height = (
                container.sizeHint().height()
                + 2 * panels_scroll.frameWidth()
                + panels_scroll.horizontalScrollBar().sizeHint().height()
            )
            panels_scroll.setMinimumHeight(fit_height)
            panels_scroll.setMaximumHeight(fit_height)
        layout.addWidget(panels_scroll)
        if not self._recording_beside_panels:
            layout.addWidget(rec_group)

        # One recorder per instrument being recorded, by panel index
        self._recorders = {}

        # Bottom: plots and log side by side
        bottom_layout = QtWidgets.QHBoxLayout()

        # A panel offers one plot as `live_plot`, titled by `plot_title`, or
        # several as `live_plots`, a mapping of tab title to plot widget.
        if any(
            hasattr(p, 'live_plot') or hasattr(p, 'live_plots')
            for p in self._panels
        ):
            plot_group = QtWidgets.QGroupBox("Live Data")
            plot_layout = QtWidgets.QVBoxLayout()
            self._plot_tabs = QtWidgets.QTabWidget()
            self._plot_tabs.setMinimumHeight(250)
            for panel in self._panels:
                if hasattr(panel, 'live_plots'):
                    for title, plot in panel.live_plots.items():
                        self._plot_tabs.addTab(plot, title)
                elif hasattr(panel, 'live_plot'):
                    self._plot_tabs.addTab(
                        panel.live_plot,
                        getattr(panel, 'plot_title', 'Plot')
                    )
            plot_layout.addWidget(self._plot_tabs)
            plot_group.setLayout(plot_layout)
            bottom_layout.addWidget(plot_group, stretch=2)

        log_group = QtWidgets.QGroupBox("Instrument Control Log")
        log_layout = QtWidgets.QVBoxLayout()
        self.log_area = QtWidgets.QTextEdit()
        self.log_area.setMinimumWidth(270)
        self.log_area.setReadOnly(True)
        self.log_area.setPlaceholderText("Control actions will appear here...")
        log_layout.addWidget(self.log_area)
        log_group.setLayout(log_layout)
        bottom_layout.addWidget(log_group, stretch=1)

        layout.addLayout(bottom_layout)

        handler = QtLogHandler(self.log_area)
        handler.setFormatter(
            logging.Formatter('%(asctime)s : %(message)s', datefmt='%H:%M:%S')
        )
        _control_log.addHandler(handler)
        _control_log.setLevel(logging.DEBUG)
        _control_log.propagate = False

        self.setLayout(layout)

    def _reference_panel_width(self) -> int:
        """Get the width stacked columns should take, or 0 to leave them be.

        Returns:
            Width in pixels of the panel named by `column_width_from`, or 0 if
            no name was given or no panel answers to it, which happens when an
            instrument could not be built.
        """
        if not self._column_width_from:
            return 0
        for panel in self._panels:
            name = getattr(panel, 'instrument_name', panel.__class__.__name__)
            if name == self._column_width_from:
                return panel.sizeHint().width()
        log.debug(
            "No panel named %s to take a column width from", self._column_width_from
        )
        return 0

    @staticmethod
    def _panel_name(panel):
        """Get what to call one panel's instrument."""
        return getattr(panel, 'instrument_name', panel.__class__.__name__)

    def _checked_panels(self):
        """Get the panels whose box is ticked, with the index of each."""
        return [
            (index, panel)
            for index, (panel, box) in enumerate(
                zip(self._panels, self._instrument_checks)
            )
            if box.isChecked()
        ]

    def _on_record(self, checked):
        if not checked:
            self._on_save()
            return
        selected = self._checked_panels()
        if not selected:
            self._record_btn.setChecked(False)
            self._rec_status.setText("Tick an instrument to record")
            self._rec_status.setStyleSheet("color: #cc0000;")
            return

        started, failed = [], []
        for index, panel in selected:
            name = self._panel_name(panel)
            recorder = DataRecorder(
                name, getattr(panel, 'record_columns', ['Value']),
                self._directory,
            )
            try:
                recorder.start()
            except Exception as e:
                failed.append(f"{name}: {e}")
                _control_log.error("Recording %s failed: %s", name, e)
                continue
            self._recorders[index] = recorder
            if hasattr(panel, 'data_recorded'):
                panel.data_recorded.connect(recorder.write)
            started.append(name)

        if not started:
            # Nothing is recording, so the button should not say it is
            self._record_btn.setChecked(False)
            self._rec_status.setText("Error: " + "; ".join(failed))
            self._rec_status.setStyleSheet("color: #cc0000;")
            return
        self._record_btn.setText("Recording...")
        self._save_btn.setEnabled(True)
        self._delete_btn.setEnabled(True)
        for box in self._instrument_checks:
            box.setEnabled(False)
        self._rec_status.setText("Recording: " + ", ".join(started))
        self._rec_status.setStyleSheet("color: red;")
        if failed:
            # Said in the log, since the status line is saying what is
            # recording and that is the more useful of the two
            _control_log.warning(
                "Recording started for %s but not for %s",
                ", ".join(started), "; ".join(failed),
            )

    def _stop_recording(self, keep):
        """Stop every recorder, and keep or discard what each has written.

        Args:
            keep: whether to keep the files.

        Returns:
            The instruments that were recording.
        """
        stopped = []
        for index, recorder in self._recorders.items():
            panel = self._panels[index]
            if hasattr(panel, 'data_recorded'):
                try:
                    panel.data_recorded.disconnect(recorder.write)
                except Exception:
                    pass
            try:
                if keep:
                    recorder.save()
                else:
                    recorder.delete()
            except Exception as e:
                _control_log.error(
                    "Could not %s the %s recording: %s",
                    "save" if keep else "delete", self._panel_name(panel), e
                )
            stopped.append(self._panel_name(panel))
        self._recorders.clear()
        self._record_btn.setChecked(False)
        self._record_btn.setText("Record")
        self._save_btn.setEnabled(False)
        self._delete_btn.setEnabled(False)
        for box in self._instrument_checks:
            box.setEnabled(True)
        return stopped

    def _on_save(self):
        stopped = self._stop_recording(keep=True)
        self._rec_status.setText(
            "Saved: {}".format(", ".join(stopped)) if stopped else "Saved"
        )
        self._rec_status.setStyleSheet("color: green;")

    def _on_delete(self):
        stopped = self._stop_recording(keep=False)
        self._rec_status.setText(
            "Deleted: {}".format(", ".join(stopped)) if stopped else "Deleted"
        )
        self._rec_status.setStyleSheet("color: gray;")

    def _on_control_action(self):
        if self._experiment_running and self._abort_callback:
            self._abort_callback()
            self._experiment_running = False
            _control_log.info("Experiment aborted by instrument control action")

    def set_enabled_for_experiment(self, running: bool):
        self._experiment_running = running

    def set_abort_callback(self, callback: Callable):
        self._abort_callback = callback