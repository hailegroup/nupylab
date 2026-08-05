"""Generic individual instrument control widget for NUPyLab GUIs."""

from __future__ import annotations

import csv
import logging
import os
from datetime import datetime
from typing import List, Callable, Optional

from pymeasure.display.Qt import QtWidgets, QtCore

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

# Separate logger for instrument control only
_control_log = logging.getLogger('nupylab.instrument_control')


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
    """Generic tab widget that assembles control panels from instruments."""

    def __init__(self, instruments: List = None,
                 abort_callback: Optional[Callable] = None,
                 scanner=None,
                 directory = "",  # str or callable returning str
                 parent=None):
        super().__init__(parent)
        self._panels = []
        self._abort_callback = abort_callback
        self._experiment_running = False
        self._scanner = scanner
        self._directory = directory
        self._setup_ui(instruments or [])

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
                panels_layout.addWidget(panel)
                self._panels.append(panel)

        panels_scroll.setWidget(container)
        layout.addWidget(panels_scroll)

        # Recording controls — instrument selector + record/save/delete buttons
        rec_group = QtWidgets.QGroupBox("Data Recording")
        rec_layout = QtWidgets.QHBoxLayout()

        rec_layout.addWidget(QtWidgets.QLabel("Instrument:"))
        self._instrument_selector = QtWidgets.QComboBox()
        for panel in self._panels:
            name = getattr(panel, 'instrument_name', panel.__class__.__name__)
            self._instrument_selector.addItem(name)
        rec_layout.addWidget(self._instrument_selector)

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
        rec_layout.addWidget(self._rec_status)

        rec_group.setLayout(rec_layout)
        layout.addWidget(rec_group)

        self._recorder = None

        # Bottom: plots and log side by side
        bottom_layout = QtWidgets.QHBoxLayout()

        if any(hasattr(p, 'live_plot') for p in self._panels):
            plot_group = QtWidgets.QGroupBox("Live Data")
            plot_layout = QtWidgets.QVBoxLayout()
            self._plot_tabs = QtWidgets.QTabWidget()
            self._plot_tabs.setMinimumHeight(250)
            for panel in self._panels:
                if hasattr(panel, 'live_plot'):
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

    def _get_selected_panel(self):
        idx = self._instrument_selector.currentIndex()
        if 0 <= idx < len(self._panels):
            return self._panels[idx]
        return None

    def _on_record(self, checked):
        if checked:
            panel = self._get_selected_panel()
            if panel is None:
                self._record_btn.setChecked(False)
                return
            instrument_name = getattr(panel, 'instrument_name', panel.__class__.__name__)
            columns = getattr(panel, 'record_columns', ['Value'])
            self._recorder = DataRecorder(instrument_name, columns, self._directory)
            try:
                filepath = self._recorder.start()
                fname = os.path.basename(filepath)
                self._rec_status.setText(f"Recording: {fname}")
                self._rec_status.setStyleSheet("color: red;")
                self._record_btn.setText("Recording...")
                self._save_btn.setEnabled(True)
                self._delete_btn.setEnabled(True)
                self._instrument_selector.setEnabled(False)
                # Connect panel's data signal to recorder
                if hasattr(panel, 'data_recorded'):
                    panel.data_recorded.connect(self._recorder.write)
            except Exception as e:
                self._record_btn.setChecked(False)
                self._rec_status.setText(f"Error: {e}")
                _control_log.error("Recording failed: %s", e)
        else:
            self._on_save()

    def _on_save(self):
        panel = self._get_selected_panel()
        if panel and hasattr(panel, 'data_recorded'):
            try:
                panel.data_recorded.disconnect(self._recorder.write)
            except Exception:
                pass
        if self._recorder:
            self._recorder.save()
        self._record_btn.setChecked(False)
        self._record_btn.setText("Record")
        self._save_btn.setEnabled(False)
        self._delete_btn.setEnabled(False)
        self._rec_status.setText("Saved")
        self._rec_status.setStyleSheet("color: green;")
        self._instrument_selector.setEnabled(True)

    def _on_delete(self):
        panel = self._get_selected_panel()
        if panel and hasattr(panel, 'data_recorded'):
            try:
                panel.data_recorded.disconnect(self._recorder.write)
            except Exception:
                pass
        if self._recorder:
            self._recorder.delete()
        self._record_btn.setChecked(False)
        self._record_btn.setText("Record")
        self._save_btn.setEnabled(False)
        self._delete_btn.setEnabled(False)
        self._rec_status.setText("Deleted")
        self._rec_status.setStyleSheet("color: gray;")
        self._instrument_selector.setEnabled(True)

    def _on_control_action(self):
        if self._experiment_running and self._abort_callback:
            self._abort_callback()
            self._experiment_running = False
            _control_log.info("Experiment aborted by instrument control action")

    def set_enabled_for_experiment(self, running: bool):
        self._experiment_running = running

    def set_abort_callback(self, callback: Callable):
        self._abort_callback = callback