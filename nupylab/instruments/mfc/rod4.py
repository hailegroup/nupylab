"""Adapts ROD-4 driver to NUPylab instrument class for use with NUPyLab GUIs."""

import logging
import time
from typing import List, Sequence

import serial

from nupylab.utilities import DataTuple, NupylabError
from nupylab.utilities.nupylab_instrument import NupylabInstrument

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

_control_log = logging.getLogger('nupylab.instrument_control')


class ROD4(NupylabInstrument):
    """ROD-4(A) instrument class. Abstracts ROD-4 driver for NUPyLab procedures."""

    def __init__(
        self,
        port: str,
        data_label: Sequence[str],
        name: str = "ROD-4",
    ) -> None:
        if len(data_label) != 4:
            raise ValueError("ROD-4 data_label must be sequence of length 4.")
        if "COM" not in port:
            port = port.replace("ASRL", "COM").replace("::INSTR", "")
        self._port = port
        self._serial = None
        self._ranges = [1000.0] * 4
        super().__init__(data_label, name)

    def connect(self) -> None:
        with self.lock:
            self._serial = serial.Serial(
                self._port, 9600, bytesize=8, stopbits=1,
                parity='N', timeout=1, xonxoff=False, rtscts=False
            )
            time.sleep(0.5)
            for i in range(1, 5):
                ch = f"{i:02d}"
                resp = self._send(f"\x02{ch}RFK\r".encode())
                try:
                    self._ranges[i-1] = float(resp)
                except Exception:
                    self._ranges[i-1] = 1000.0
            self._connected = True

    def disconnect(self) -> None:
        with self.lock:
            if self._serial is not None:
                try:
                    self._serial.close()
                except Exception:
                    pass
                self._serial = None
            self._connected = False

    def _send(self, cmd: bytes) -> str:
        self._serial.read_all()
        self._serial.write(cmd)
        time.sleep(0.2)
        return self._serial.read_all().decode(errors="ignore").strip()

    def set_parameters(self, setpoints: Sequence[float]) -> None:
        if len(setpoints) != 4:
            raise ValueError("ROD-4 setpoints must be sequence of length 4.")
        self._parameters = setpoints

    def start(self) -> None:
        if self._parameters is None:
            raise NupylabError(
                f"`{self.__class__.__name__}` method `set_parameters` "
                "must be called before calling its `start` method."
            )
        setpoints = self._parameters
        with self.lock:
            for i, (setpoint, range_) in enumerate(zip(setpoints, self._ranges), 1):
                ch = f"{i:02d}"
                pct = 100.0 * setpoint / range_ if range_ > 0 else 0.0
                if setpoint == 0:
                    self._send(f"\x02{ch}SVM1\r".encode())
                else:
                    self._send(f"\x02{ch}SVM0\r".encode())
                self._send(f"\x02{ch}SFD{pct:.1f}\r".encode())
        self._parameters = None

    def get_data(self) -> List[DataTuple]:
        with self.lock:
            flows = []
            for i, range_ in enumerate(self._ranges):
                ch = f"{i+1:02d}"
                try:
                    resp = self._send(f"\x02{ch}RFX\r".encode())
                    flows.append(float(resp) * range_ / 100.0)
                except Exception:
                    print(f"ch{i+1} error: {e}")
                    flows.append(0.0)
        return [DataTuple(self.data_label[i], flows[i]) for i in range(4)]

    def stop_measurement(self) -> None:
        pass

    def shutdown(self) -> None:
        with self.lock:
            for i in range(1, 5):
                ch = f"{i:02d}"
                self._send(f"\x02{ch}SVM1\r".encode())
            self._serial.close()

    def control_widget(self, abort_callback=None):
        """Return a Qt control panel for this instrument."""
        from pymeasure.display.Qt import QtWidgets, QtCore
        from nupylab.utilities.instrument_control import LivePlotWidget

        instrument = self

        class Worker(QtCore.QThread):
            result = QtCore.Signal(list)

            def run(self):
                try:
                    data = instrument.get_data()
                    self.result.emit([dt.value for dt in data])
                except Exception:
                    pass

        class ROD4Panel(QtWidgets.QGroupBox):
            plot_title = "MFC Flows"

            def __init__(self):
                super().__init__("ROD-4A — Mass Flow Controllers")
                self._worker = None
                self._abort_callback = abort_callback
                self.live_plot = LivePlotWidget(
                    "MFC Flows", "Flow (sccm)",
                    n_traces=4,
                    trace_labels=["MFC 1", "MFC 2", "MFC 3", "MFC 4"]
                )
                self._setup_ui()
                self.timer = QtCore.QTimer()
                self.timer.timeout.connect(self.update_flows)
                self.timer.start(2000)

            def _setup_ui(self):
                layout = QtWidgets.QVBoxLayout()
                grid = QtWidgets.QGridLayout()

                grid.addWidget(QtWidgets.QLabel("Channel"), 0, 0)
                grid.addWidget(QtWidgets.QLabel("Setpoint (sccm)"), 0, 1)
                grid.addWidget(QtWidgets.QLabel("Actual Flow"), 0, 2)

                self.setpoint_spins = []
                self.flow_labels = []

                for i in range(4):
                    grid.addWidget(QtWidgets.QLabel(f"MFC {i+1}"), i+1, 0)
                    sp = QtWidgets.QDoubleSpinBox()
                    sp.setRange(0, 2000)
                    sp.setSuffix(" sccm")
                    self.setpoint_spins.append(sp)
                    grid.addWidget(sp, i+1, 1)
                    flow_lbl = QtWidgets.QLabel("— sccm")
                    self.flow_labels.append(flow_lbl)
                    grid.addWidget(flow_lbl, i+1, 2)

                layout.addLayout(grid)

                btn_layout = QtWidgets.QHBoxLayout()
                self.connect_btn = QtWidgets.QPushButton("Connect")
                self.apply_btn = QtWidgets.QPushButton("Apply Setpoints")
                self.close_all_btn = QtWidgets.QPushButton("Close All Valves")
                self.disconnect_btn = QtWidgets.QPushButton("Disconnect")
                self.connect_btn.clicked.connect(self.connect_instrument)
                self.apply_btn.clicked.connect(self.apply_setpoints)
                self.close_all_btn.clicked.connect(self.close_all)
                self.disconnect_btn.clicked.connect(self.disconnect_instrument)
                btn_layout.addWidget(self.connect_btn)
                btn_layout.addWidget(self.apply_btn)
                btn_layout.addWidget(self.close_all_btn)
                btn_layout.addWidget(self.disconnect_btn)
                layout.addLayout(btn_layout)

                self.status_label = QtWidgets.QLabel("Status: Not connected")
                layout.addWidget(self.status_label)
                self.setLayout(layout)

            def _abort_if_needed(self):
                if self._abort_callback:
                    self._abort_callback()

            def connect_instrument(self):
                self._abort_if_needed()
                try:
                    instrument.connect()
                    self.status_label.setText("Status: Connected")
                    _control_log.info("ROD-4 connected on %s", instrument._port)
                except Exception as e:
                    self.status_label.setText(f"Status: Error — {e}")
                    _control_log.error("ROD-4 connect failed: %s", e)

            def disconnect_instrument(self):
                self._abort_if_needed()
                try:
                    self.timer.stop()
                    instrument.disconnect()
                    self.status_label.setText("Status: Disconnected")
                    for lbl in self.flow_labels:
                        lbl.setText("— sccm")
                    _control_log.info("ROD-4 disconnected")
                except Exception as e:
                    self.status_label.setText(f"Status: Error — {e}")

            def apply_setpoints(self):
                self._abort_if_needed()
                try:
                    if not instrument.connected:
                        instrument.connect()
                    setpoints = [sp.value() for sp in self.setpoint_spins]
                    instrument.set_parameters(setpoints)
                    instrument.start()
                    self.live_plot.clear()
                    if not self.timer.isActive():
                        self.timer.start(2000)
                    self.status_label.setText("Status: Setpoints applied")
                    _control_log.info("ROD-4 setpoints applied: %s sccm", setpoints)
                except Exception as e:
                    self.status_label.setText(f"Status: Error — {e}")
                    _control_log.error("ROD-4 setpoint error: %s", e)

            def close_all(self):
                self._abort_if_needed()
                try:
                    instrument.set_parameters([0.0, 0.0, 0.0, 0.0])
                    instrument.start()
                    self.status_label.setText("Status: All valves closed")
                    _control_log.info("ROD-4 all valves closed")
                except Exception as e:
                    self.status_label.setText(f"Status: Error — {e}")

            def update_flows(self):
                if not instrument.connected:
                    return
                if self._worker and self._worker.isRunning():
                    return
                self._worker = Worker()
                def on_flows(flows):
                    for i, v in enumerate(flows):
                        if i < 4:
                            self.flow_labels[i].setText(f"{v:.2f} sccm")
                    self.live_plot.add_points(flows)
                self._worker.result.connect(on_flows)
                self._worker.start()

        return ROD4Panel()