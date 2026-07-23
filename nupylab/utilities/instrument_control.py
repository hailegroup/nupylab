"""Generic individual instrument control widget for NUPyLab GUIs."""

from __future__ import annotations

import logging
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


class LivePlotWidget(QtWidgets.QWidget):
    """Simple live plot for instrument data using pyqtgraph if available, else QLabel."""

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
        self._setup_ui()

    def _setup_ui(self):
        layout = QtWidgets.QVBoxLayout()

        # Try to use pyqtgraph, fall back to simple label display
        try:
            import pyqtgraph as pg
            self._use_pg = True
            self._plot_widget = pg.PlotWidget(title=self.title)
            self._plot_widget.setLabel('left', self.y_label)
            self._plot_widget.setLabel('bottom', 'Time (s)')
            self._plot_widget.addLegend()
            colors = ['r', 'g', 'b', 'y']
            self._curves = []
            for i in range(self.n_traces):
                curve = self._plot_widget.plot(
                    pen=colors[i % len(colors)],
                    name=self.trace_labels[i]
                )
                self._curves.append(curve)
            layout.addWidget(self._plot_widget)
        except ImportError:
            self._use_pg = False
            self._value_label = QtWidgets.QLabel("—")
            self._value_label.setStyleSheet("font-size: 18px; font-weight: bold;")
            self._value_label.setAlignment(QtCore.Qt.AlignCenter)
            layout.addWidget(QtWidgets.QLabel(f"{self.title}:"))
            layout.addWidget(self._value_label)

        # Trace selector if multiple traces
        if self.n_traces > 1:
            selector_layout = QtWidgets.QHBoxLayout()
            selector_layout.addWidget(QtWidgets.QLabel("Showing:"))
            self._trace_selector = QtWidgets.QComboBox()
            self._trace_selector.addItems(self.trace_labels)
            self._trace_selector.currentIndexChanged.connect(self._on_trace_changed)
            selector_layout.addWidget(self._trace_selector)
            layout.addLayout(selector_layout)

        self.setLayout(layout)
        self._start_time = None

    def _on_trace_changed(self, index):
        self._current_trace = index
        if not self._use_pg and self._data[index]:
            self._value_label.setText(f"{self._data[index][-1]:.2f}")

    def add_point(self, value, trace_index: int = 0):
        """Add a data point to the specified trace."""
        import time
        if self._start_time is None:
            self._start_time = time.monotonic()
        t = time.monotonic() - self._start_time
        self._times.append(t)
        self._data[trace_index].append(value)

        if self._use_pg:
            # Update the curve for this trace
            times = self._times[-200:]  # keep last 200 points
            data = self._data[trace_index][-200:]
            self._curves[trace_index].setData(times, data)
        else:
            if trace_index == self._current_trace:
                self._value_label.setText(f"{value:.2f}")

    def add_points(self, values: List):
        """Add one point per trace simultaneously."""
        for i, v in enumerate(values):
            if i < self.n_traces:
                self.add_point(v, i)

    def clear(self):
        """Clear all data."""
        self._data = [[] for _ in range(self.n_traces)]
        self._times = []
        self._start_time = None
        if self._use_pg:
            for curve in self._curves:
                curve.setData([], [])


class InstrumentControlWidget(QtWidgets.QWidget):
    """Generic tab widget that assembles control panels from instruments.

    Each instrument passed in should have a `control_widget()` method
    that returns a QWidget control panel for that instrument.

    Pass `abort_callback` to abort a running experiment when controls are used.
    """

    # Signal emitted when any control action is taken
    control_action = QtCore.Signal()

    def __init__(self, instruments: List = None,
                 abort_callback: Optional[Callable] = None,
                 parent=None):
        super().__init__(parent)
        self._panels = []
        self._abort_callback = abort_callback
        self._experiment_running = False
        self._setup_ui(instruments or [])

    def _setup_ui(self, instruments):
        layout = QtWidgets.QVBoxLayout()

        # Top: panels in a scroll area
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        container = QtWidgets.QWidget()
        panels_layout = QtWidgets.QHBoxLayout(container)
        panels_layout.setAlignment(QtCore.Qt.AlignLeft)

        for instrument in instruments:
            if hasattr(instrument, 'control_widget'):
                panel = instrument.control_widget(
                    abort_callback=self._on_control_action
                )
                panels_layout.addWidget(panel)
                self._panels.append(panel)

        scroll.setWidget(container)

        # Middle: live plots in a tab widget
        self._plot_tabs = QtWidgets.QTabWidget()
        self._plot_tabs.setMaximumHeight(250)
        for panel in self._panels:
            if hasattr(panel, 'live_plot'):
                self._plot_tabs.addTab(panel.live_plot, panel.plot_title)

        # Bottom: control-only log
        log_label = QtWidgets.QLabel("Instrument Control Log:")
        self.log_area = QtWidgets.QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setMaximumHeight(120)
        self.log_area.setPlaceholderText("Control actions will appear here...")

        # Only capture instrument_control logger (not all of nupylab)
        handler = QtLogHandler(self.log_area)
        handler.setFormatter(
            logging.Formatter('%(asctime)s : %(message)s', datefmt='%H:%M:%S')
        )
        _control_log.addHandler(handler)
        _control_log.setLevel(logging.DEBUG)

        layout.addWidget(scroll, stretch=2)
        if self._plot_tabs.count() > 0:
            layout.addWidget(self._plot_tabs, stretch=1)
        layout.addWidget(log_label)
        layout.addWidget(self.log_area)
        self.setLayout(layout)

    def _on_control_action(self):
        """Called when any control panel button is pressed."""
        if self._experiment_running and self._abort_callback:
            self._abort_callback()
            self._experiment_running = False
            _control_log.info("Experiment aborted by instrument control action")

    def set_enabled_for_experiment(self, running: bool):
        """Track experiment state but don't disable — abort instead."""
        self._experiment_running = running

    def set_abort_callback(self, callback: Callable):
        """Set the callback to abort a running experiment."""
        self._abort_callback = callback