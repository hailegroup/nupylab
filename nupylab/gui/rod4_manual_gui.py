#!/usr/bin/env python3
"""
NUPyLab - Standalone ROD-4 MFC Controller GUI

Connects to a Proterial ROD-4(A) over RS-485, reads live flow values from
all four channels, sets per-channel setpoints in sccm, and optionally
logs everything to CSV.

Setpoints are written through the PyMeasure ROD-4 driver, which expects
percent of MFC range. Conversion from sccm to percent is handled here so
the user only ever works in sccm.

Dependencies: PyQt5 or PyQt6, pyqtgraph, numpy, pymeasure, nupylab utilities
"""

import sys
import csv
import time
import threading
from datetime import datetime
from collections import deque

from pymeasure.instruments.proterial import rod4

try:
    from nupylab.utilities import list_resources as _list_resources
    _SERIAL_PORTS = _list_resources()
except Exception:
    try:
        import serial.tools.list_ports as _lp
        _SERIAL_PORTS = [p.device for p in _lp.comports()]
    except Exception:
        _SERIAL_PORTS = []
if not _SERIAL_PORTS:
    _SERIAL_PORTS = ["COM1"]

import numpy as np

try:
    from PyQt6.QtWidgets import (
        QApplication, QMainWindow, QWidget,
        QHBoxLayout, QVBoxLayout, QGridLayout,
        QLabel, QLineEdit, QPushButton, QComboBox,
        QGroupBox, QFileDialog, QMessageBox, QFrame,
        QStatusBar,
    )
    from PyQt6.QtCore import pyqtSignal, QObject
    _PYQT6 = True
except ImportError:
    from PyQt5.QtWidgets import (
        QApplication, QMainWindow, QWidget,
        QHBoxLayout, QVBoxLayout, QGridLayout,
        QLabel, QLineEdit, QPushButton, QComboBox,
        QGroupBox, QFileDialog, QMessageBox, QFrame,
        QStatusBar,
    )
    from PyQt5.QtCore import pyqtSignal, QObject
    _PYQT6 = False

if _PYQT6:
    _HLINE = QFrame.Shape.HLine
else:
    _HLINE = QFrame.HLine

import pyqtgraph as pg


def _parse_rod4_value(raw) -> float:
    if isinstance(raw, str):
        return float(raw.lstrip('E'))
    return float(raw)


NUM_CHANNELS = 4

CHANNEL_COLORS = ["#ff5555", "#55aaff", "#44dd88", "#ffaa33"]

LIVE_BG    = "#0a0a0a"
LIVE_FG    = "#00e675"
EDIT_STYLE = "background-color: #dff0f7; color: #111111;"


class DataWorker(QObject):
    """Polls all 4 ROD-4 channels on a background thread every interval_s seconds."""

    data_ready     = pyqtSignal(list)
    error_occurred = pyqtSignal(str)

    def __init__(self, driver, ranges, interval_s: float = 1.0):
        super().__init__()
        self.driver     = driver
        self.ranges     = ranges
        self.interval_s = interval_s
        self._active    = False

    def start(self):
        self._active = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._active = False

    def _loop(self):
        while self._active:
            try:
                flows = []
                for channel, rng in zip(self.driver.channels.values(), self.ranges):
                    flow_pct = _parse_rod4_value(channel.actual_flow)
                    flows.append(flow_pct * rng / 100.0)
                self.data_ready.emit(flows)
            except Exception as exc:
                self.error_occurred.emit(str(exc))
            time.sleep(self.interval_s)


class ROD4GUI(QMainWindow):

    _reconnect_success = pyqtSignal()
    _reconnect_failed  = pyqtSignal(str)
    _reconnect_status  = pyqtSignal(str)

    def __init__(self):
        super().__init__()

        self.driver      = None
        self.worker      = None
        self._log_file   = None
        self._log_writer = None
        self._t0         = None
        self._log_t0     = None
        self._connected  = False
        self._logging    = False
        self._user_disconnected = False
        self._reconnecting      = False
        self._ranges = [1000.0] * NUM_CHANNELS

        N = 600
        self._times = deque(maxlen=N)
        self._flows = [deque(maxlen=N) for _ in range(NUM_CHANNELS)]

        self._last_flows = [0.0] * NUM_CHANNELS

        self._build_ui()
        self.setWindowTitle("NUPyLab - ROD-4 MFC Manual Control")
        self.setMinimumSize(1200, 700)

        self._reconnect_success.connect(self._on_reconnect_success)
        self._reconnect_failed.connect(self._on_reconnect_failed)
        self._reconnect_status.connect(lambda msg: self.statusBar().showMessage(msg))

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        h = QHBoxLayout(root)
        h.setContentsMargins(10, 10, 10, 10)
        h.setSpacing(12)
        h.addWidget(self._sidebar(), 0)
        h.addWidget(self._main_area(), 1)
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready - not connected.")

    def _sidebar(self) -> QWidget:
        box = QGroupBox("Connection && Logging")
        box.setFixedWidth(215)
        v = QVBoxLayout(box)
        v.setSpacing(6)

        v.addWidget(QLabel("Serial Port"))
        self.port_edit = QComboBox()
        self.port_edit.addItems(_SERIAL_PORTS)
        self.port_edit.setEditable(True)
        self.port_edit.setStyleSheet(EDIT_STYLE)
        v.addWidget(self.port_edit)

        v.addWidget(self._hline())

        v.addWidget(QLabel("Poll Interval (s)"))
        self.poll_interval_edit = QLineEdit("1")
        self.poll_interval_edit.setStyleSheet(EDIT_STYLE)
        v.addWidget(self.poll_interval_edit)

        v.addWidget(self._hline())

        v.addWidget(QLabel("Log File"))
        self.filepath_edit = QLineEdit()
        self.filepath_edit.setPlaceholderText("(not set)")
        self.filepath_edit.setReadOnly(True)
        v.addWidget(self.filepath_edit)

        self.browse_btn = QPushButton("Browse...")
        self.browse_btn.clicked.connect(self._browse_file)
        v.addWidget(self.browse_btn)

        self.log_btn = QPushButton("Start Logging")
        self.log_btn.setCheckable(True)
        self.log_btn.setEnabled(False)
        self.log_btn.clicked.connect(self._toggle_logging)
        v.addWidget(self.log_btn)

        v.addStretch()
        return box

    def _main_area(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(10)
        v.addWidget(self._controls_row())
        v.addWidget(self._graph_panel(), 1)
        return w

    def _controls_row(self) -> QWidget:
        box = QGroupBox("MFC Channels")
        g = QGridLayout(box)
        g.setSpacing(10)

        live_style = (
            f"background-color: {LIVE_BG}; color: {LIVE_FG};"
            "font-size: 14px; font-weight: bold; border: 1px solid #003300;"
        )

        g.addWidget(QLabel("Channel"),         0, 0)
        g.addWidget(QLabel("Range (sccm)"),    0, 1)
        g.addWidget(QLabel("Setpoint (sccm)"), 0, 2)
        g.addWidget(QLabel("Actual (sccm)"),   0, 3)
        g.addWidget(QLabel("Valve Mode"),      0, 4)

        self.setpoint_edits = []
        self.flow_displays  = []
        self.range_labels   = []
        self.valve_combos   = []
        self.apply_buttons  = []

        for i in range(NUM_CHANNELS):
            row = i + 1
            ch_label = QLabel(f"{i + 1}")
            ch_label.setStyleSheet(f"color: {CHANNEL_COLORS[i]}; font-weight: bold;")
            g.addWidget(ch_label, row, 0)

            range_lbl = QLabel("---")
            range_lbl.setMinimumWidth(80)
            g.addWidget(range_lbl, row, 1)
            self.range_labels.append(range_lbl)

            sp_edit = QLineEdit("0.0")
            sp_edit.setStyleSheet(EDIT_STYLE)
            sp_edit.setMinimumWidth(80)
            g.addWidget(sp_edit, row, 2)
            self.setpoint_edits.append(sp_edit)

            flow_disp = QLineEdit("---")
            flow_disp.setReadOnly(True)
            flow_disp.setStyleSheet(live_style)
            flow_disp.setMinimumWidth(90)
            g.addWidget(flow_disp, row, 3)
            self.flow_displays.append(flow_disp)

            valve_combo = QComboBox()
            valve_combo.addItems(["flow", "close", "open"])
            g.addWidget(valve_combo, row, 4)
            self.valve_combos.append(valve_combo)

            apply_btn = QPushButton("Apply")
            apply_btn.setEnabled(False)
            apply_btn.clicked.connect(lambda _, ch=i: self._apply_channel(ch))
            g.addWidget(apply_btn, row, 5)
            self.apply_buttons.append(apply_btn)

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setCheckable(True)
        self.connect_btn.clicked.connect(self._toggle_connect)
        g.addWidget(self.connect_btn, 0, 5)

        return box

    def _graph_panel(self) -> QWidget:
        box = QGroupBox("Live Flow Data")
        v = QVBoxLayout(box)

        pg.setConfigOptions(antialias=True)
        self.plot = pg.PlotWidget()
        self.plot.setLabel("left", "Flow (sccm)")
        self.plot.setLabel("bottom", "Elapsed Time (min)")
        self.plot.showGrid(x=True, y=True, alpha=0.25)

        self.curves = []
        legend = self.plot.addLegend(offset=(10, 10))
        for i in range(NUM_CHANNELS):
            curve = self.plot.plot(pen=pg.mkPen(color=CHANNEL_COLORS[i], width=2))
            self.curves.append(curve)
            legend.addItem(curve, f"Channel {i + 1}")

        v.addWidget(self.plot)
        return box

    def _hline(self) -> QFrame:
        f = QFrame()
        f.setFrameShape(_HLINE)
        return f

    def _browse_file(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Choose Log File", "", "CSV Files (*.csv);;All Files (*)"
        )
        if path:
            self.filepath_edit.setText(path)

    def _toggle_connect(self, checked: bool):
        if checked:
            self._connect()
        else:
            self._disconnect()

    def _connect(self):
        port = self.port_edit.currentText().strip()

        try:
            self.driver = rod4.ROD4(port)
            self._ranges = []
            for channel in self.driver.channels.values():
                rng = _parse_rod4_value(channel.mfc_range)
                self._ranges.append(rng)
        except Exception as exc:
            print(f"[ROD-4 connection error] {exc}")
            QMessageBox.critical(self, "Connection Failed",
                                 f"Could not connect to ROD-4 on {port}:\n{exc}")
            self.connect_btn.setChecked(False)
            self.driver = None
            return

        for i in range(NUM_CHANNELS):
            self.range_labels[i].setText(f"{self._ranges[i]:.1f}")

        self._t0                = time.time()
        self._connected         = True
        self._user_disconnected = False
        self._reconnecting      = False

        self._times.clear()
        for flow_deque in self._flows:
            flow_deque.clear()
        for curve in self.curves:
            curve.setData([], [])

        self.worker = DataWorker(self.driver, self._ranges, self._poll_interval())
        self.worker.data_ready.connect(self._on_data)
        self.worker.error_occurred.connect(self._on_worker_error)
        self.worker.start()

        for btn in self.apply_buttons:
            btn.setEnabled(True)
        self.connect_btn.setText("Disconnect")
        self.log_btn.setEnabled(True)
        self.port_edit.setEnabled(False)
        self.statusBar().showMessage(f"Connected - ROD-4 on {port}.")

    def _disconnect(self):
        self._user_disconnected = True
        self._reconnecting      = False

        if self._logging:
            self._stop_logging()

        if self.worker:
            self.worker.stop()
            self.worker = None

        if self.driver:
            try:
                for channel in self.driver.channels.values():
                    channel.valve_mode = "close"
            except Exception as exc:
                print(f"[ROD-4 disconnect error] {exc}")
            try:
                self.driver.adapter.close()
            except Exception:
                pass
            self.driver = None

        self._connected = False

        for btn in self.apply_buttons:
            btn.setEnabled(False)
        self.connect_btn.setChecked(False)
        self.connect_btn.setText("Connect")
        self.log_btn.setEnabled(False)
        self.log_btn.setChecked(False)
        self.log_btn.setText("Start Logging")
        self.port_edit.setEnabled(True)
        self.statusBar().showMessage("Disconnected.")

    def _apply_channel(self, ch_index: int):
        if not self._connected or self.driver is None:
            return

        try:
            sp_sccm = float(self.setpoint_edits[ch_index].text())
        except ValueError:
            QMessageBox.critical(self, "Input Error",
                                 f"Channel {ch_index + 1} setpoint must be a number.")
            return

        rng = self._ranges[ch_index]
        if sp_sccm < 0 or sp_sccm > rng:
            QMessageBox.warning(self, "Out of Range",
                                f"Channel {ch_index + 1} setpoint must be between "
                                f"0 and {rng:.1f} sccm.")
            return

        valve_mode = self.valve_combos[ch_index].currentText()
        sp_pct     = 100.0 * sp_sccm / rng

        if sp_sccm == 0 and valve_mode == "flow":
            valve_mode = "close"
            print(f"[ROD-4 ch {ch_index + 1}] setpoint is 0, auto-closing valve")

        channels = list(self.driver.channels.values())
        channel  = channels[ch_index]

        sp_error = None
        vm_error = None

        try:
            channel.setpoint = sp_pct
            print(f"[ROD-4 ch {ch_index + 1}] wrote setpoint {sp_pct:.2f}%")
        except Exception as exc:
            sp_error = exc
            print(f"[ROD-4 ch {ch_index + 1} setpoint write error] {exc}")

        try:
            channel.valve_mode = valve_mode
            print(f"[ROD-4 ch {ch_index + 1}] wrote valve_mode {valve_mode}")
        except Exception as exc:
            vm_error = exc
            print(f"[ROD-4 ch {ch_index + 1} valve_mode write error] {exc}")

        try:
            time.sleep(0.2)
            sp_readback = channel.setpoint
            vm_readback = channel.valve_mode
            print(f"[ROD-4 ch {ch_index + 1} readback] "
                  f"setpoint={sp_readback}, valve_mode={vm_readback}")
        except Exception as exc:
            print(f"[ROD-4 ch {ch_index + 1} readback error] {exc}")

        if sp_error or vm_error:
            parts = []
            if sp_error:
                parts.append(f"Setpoint: {sp_error}")
            if vm_error:
                parts.append(f"Valve mode: {vm_error}")
            QMessageBox.warning(self, "Write Error",
                                f"Channel {ch_index + 1}:\n" + "\n".join(parts))
            return

        self.statusBar().showMessage(
            f"Channel {ch_index + 1} -> {sp_sccm:.1f} sccm ({valve_mode})", 4000
        )

    def _toggle_logging(self, checked: bool):
        if checked:
            self._start_logging()
        else:
            self._stop_logging()

    def _start_logging(self):
        path = self.filepath_edit.text().strip()
        if not path:
            QMessageBox.warning(self, "No File Set",
                                "Use Browse... to choose a log file before starting.")
            self.log_btn.setChecked(False)
            return
        try:
            self._log_file   = open(path, "w", newline="")
            self._log_writer = csv.writer(self._log_file)
            header = ["Timestamp", "Elapsed_min"]
            for i in range(NUM_CHANNELS):
                header.append(f"Ch{i + 1}_sccm")
            self._log_writer.writerow(header)
            self._log_t0  = time.time()
            self._logging = True
            self.log_btn.setText("Stop Logging")
            self.browse_btn.setEnabled(False)
            self.statusBar().showMessage(f"Logging to: {path}")
        except Exception as exc:
            print(f"[ROD-4 logging error] {exc}")
            QMessageBox.warning(self, "Logging Error",
                                f"Could not open log file:\n{exc}")
            self.log_btn.setChecked(False)

    def _stop_logging(self):
        if self._log_file:
            try:
                self._log_file.close()
            except Exception:
                pass
        self._log_file   = None
        self._log_writer = None
        self._log_t0     = None
        self._logging    = False
        self.log_btn.setText("Start Logging")
        self.log_btn.setChecked(False)
        self.browse_btn.setEnabled(True)
        self.statusBar().showMessage("Logging stopped.")

    def _on_data(self, flows):
        for i, flow in enumerate(flows):
            self.flow_displays[i].setText(f"{flow:.2f}")
        self._last_flows = flows

        elapsed_min = (time.time() - self._t0) / 60.0
        self._times.append(elapsed_min)
        for i in range(NUM_CHANNELS):
            self._flows[i].append(flows[i])

        t_arr = np.array(self._times)
        for i in range(NUM_CHANNELS):
            self.curves[i].setData(t_arr, np.array(self._flows[i]))

        if self._log_writer and self._log_t0 is not None:
            log_elapsed = (time.time() - self._log_t0) / 60.0
            row = [datetime.now().isoformat(), f"{log_elapsed:.4f}"]
            for flow in flows:
                row.append(f"{flow:.3f}")
            self._log_writer.writerow(row)
            self._log_file.flush()

    def _on_worker_error(self, msg: str):
        for disp in self.flow_displays:
            disp.setText("ERR")
        print(f"[ROD-4 worker error] {msg}")
        if not self._user_disconnected and not self._reconnecting:
            self._start_reconnect(msg)
        else:
            self.statusBar().showMessage(f"Communication error: {msg}", 6000)

    def _start_reconnect(self, initial_error: str):
        self._reconnecting = True
        if self.worker:
            self.worker.stop()
            self.worker = None
        if self.driver:
            try:
                self.driver.adapter.close()
            except Exception:
                pass
        self._reconnect_status.emit(
            f"Connection lost ({initial_error}) - attempting to reconnect..."
        )
        threading.Thread(target=self._reconnect_loop, daemon=True).start()

    def _reconnect_loop(self):
        port = self.port_edit.currentText().strip()
        time.sleep(3.0)
        for attempt in range(1, 6):
            if self._user_disconnected:
                return
            self._reconnect_status.emit(f"Reconnect attempt {attempt}/5...")
            try:
                new_driver = rod4.ROD4(port)
                _ = new_driver.channels[1].actual_flow
                self.driver = new_driver
                self._reconnect_success.emit()
                return
            except Exception as exc:
                print(f"[ROD-4 reconnect attempt {attempt} failed] {exc}")
            time.sleep(2.0)

        self._reconnect_failed.emit(
            "Lost connection to ROD-4 - could not reconnect after 5 attempts."
        )

    def _on_reconnect_success(self):
        self._reconnecting = False
        self.worker = DataWorker(self.driver, self._ranges, self._poll_interval())
        self.worker.data_ready.connect(self._on_data)
        self.worker.error_occurred.connect(self._on_worker_error)
        self.worker.start()
        self.statusBar().showMessage("Reconnected successfully.")

    def _on_reconnect_failed(self, msg: str):
        self._reconnecting      = False
        self._user_disconnected = True
        print(f"[ROD-4 reconnect failed] {msg}")
        self.statusBar().showMessage(f"Error: {msg}")
        self._disconnect()

    def _poll_interval(self) -> float:
        try:
            return max(0.5, float(self.poll_interval_edit.text()))
        except ValueError:
            return 1.0

    def closeEvent(self, event):
        self._disconnect()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = ROD4GUI()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()