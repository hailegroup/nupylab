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

        # Trace selector for multiple traces
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
                 parent=None):
        super().__init__(parent)
        self._panels = []
        self._abort_callback = abort_callback
        self._experiment_running = False
        self._setup_ui(instruments or [])

    def _setup_ui(self, instruments):
        layout = QtWidgets.QVBoxLayout()

        # Top: instrument control panels in horizontal scroll
        panels_scroll = QtWidgets.QScrollArea()
        panels_scroll.setWidgetResizable(True)
        panels_scroll.setMaximumHeight(350)
        panels_scroll.setMinimumHeight(250)
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

        panels_scroll.setWidget(container)
        layout.addWidget(panels_scroll)

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
        self.log_area.setReadOnly(True)
        self.log_area.setPlaceholderText("Control actions will appear here...")
        self.log_area.setMinimumWidth(150)
        log_layout.addWidget(self.log_area)
        log_group.setLayout(log_layout)
        bottom_layout.addWidget(log_group, stretch=1)

        layout.addLayout(bottom_layout)

        # Only capture instrument_control logger
        handler = QtLogHandler(self.log_area)
        handler.setFormatter(
            logging.Formatter('%(asctime)s : %(message)s', datefmt='%H:%M:%S')
        )
        _control_log.addHandler(handler)
        _control_log.setLevel(logging.DEBUG)
        # Prevent propagation to root logger (keeps experiment log clean)
        _control_log.propagate = False

        self.setLayout(layout)

    def _on_control_action(self):
        """Called when any control panel button is pressed."""
        if self._experiment_running and self._abort_callback:
            self._abort_callback()
            self._experiment_running = False
            _control_log.info("Experiment aborted by instrument control action")

    def set_enabled_for_experiment(self, running: bool):
        """Track experiment state — don't disable, abort instead."""
        self._experiment_running = running

    def set_abort_callback(self, callback: Callable):
        self._abort_callback = callback