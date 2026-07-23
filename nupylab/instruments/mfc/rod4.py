"""Adapts ROD-4 driver to NUPylab instrument class for use with NUPyLab GUIs."""

from typing import List, Sequence

from nupylab.utilities import DataTuple, NupylabError
from nupylab.utilities.nupylab_instrument import NupylabInstrument
import time
import serial


class ROD4(NupylabInstrument):
    """ROD-4(A) instrument class. Abstracts ROD-4 driver for NUPyLab procedures.

    Attributes:
        data_label: labels for DataTuples.
        name: name of instrument.
        lock: thread lock for preventing simultaneous calls to instrument.
        rod4: ROD4 driver class.
    """

    def __init__(
        self,
        port: str,
        data_label: Sequence[str],
        name: str = "ROD-4",
    ) -> None:
        """Initialize ROD-4 data labels, name, and connection parameters.

        Args:
            port: string name of port, e.g. `ASRL1::INSTR`.
            data_label: labels for DataTuples. :meth:`get_data` returns flow rate for
                each channel, and corresponding labels should match entries in
                DATA_COLUMNS of calling procedure class.
            name: name of instrument.

        Raises:
            ValueError if length of data_label is not 4.
        """
        if len(data_label) != 4:
            raise ValueError("ROD-4 data_label must be sequence of length 4.")
        if "COM" not in port:
            port = port.replace("ASRL", "COM").replace("::INSTR", "")
        self._port = port
        self._serial = None
        self._ranges = [1000.0] * 4
        super().__init__(data_label, name)

    def connect(self) -> None:
        """Connect to ROD-4."""
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

    def _send(self, cmd: bytes) -> str:
        self._serial.read_all()
        self._serial.write(cmd)
        time.sleep(0.2)
        return self._serial.read_all().decode(errors="ignore").strip()
        

    def set_parameters(self, setpoints: Sequence[float]) -> None:
        """Set ROD-4 flow setpoints.

        Args:
            setpoints: tuple or list of 4 channel setpoints.
        Raises:
            ValueError if lengths of setpoints is not 4.
        """
        if len(setpoints) != 4:
            raise ValueError("ROD-4 setpoints must be sequence of length 4.")
        self._parameters = setpoints

    def start(self) -> None:
        """Convert setpoints from sccm to % and set flow.

        Raises:
            NupylabError if `start` method is called before `set_parameters`.
        """
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
                # resp1 = self._send(f"\x02{ch}SVM0\r".encode())
                # resp2 = self._send(f"\x02{ch}SFD{pct:.1f}\r".encode())
                # print(f"ch{i} SVM: {repr(resp1)}, SFD {pct:.1f}%: {repr(resp2)}")
                if setpoint == 0:
                    self._send(f"\x02{ch}SVM1\r".encode())  # CLOSE
                else:
                    self._send(f"\x02{ch}SVM0\r".encode())  # FLOW CTRL
                self._send(f"\x02{ch}SFD{pct:.1f}\r".encode())
        self._parameters = None

    def get_data(self) -> List[DataTuple]:
        """Read flow for each MFC channel.

        Returns:
            tuple of four DataTuples with flow for each channel.
        """
        with self.lock:
            flows = []
            for i, range_ in enumerate(self._ranges):
                ch = f"{i+1:02d}"
                #print(f"ch{i+1} RFX response: {repr(resp)}")
                try:
                    resp = self._send(f"\x02{ch}RFX\r".encode())
                    flows.append(float(resp) * range_ / 100.0)
                except Exception as e:
                    #print(f"ch{i+1} parse error: {e}")
                    flows.append(0.0)
        return [DataTuple(self.data_label[i], flows[i]) for i in range(4)]
    
    def stop_measurement(self) -> None:
        """Stop ROD-4 measurement. Not implemented."""
        pass

    def shutdown(self) -> None:
        """Shutdown ROD-4 gas flow and close serial connection."""
        with self.lock:
            for i in range(1, 5):
                ch = f"{i:02d}"
                self._send(f"\x02{ch}SVM1\r".encode())  # CLOSE all
            self._serial.close()

    def control_widget(self):
        """Return a Qt control panel for this instrument."""
        from pymeasure.display.Qt import QtWidgets, QtCore

        instrument = self

        class ROD4Panel(QtWidgets.QGroupBox):
            def __init__(self):
                super().__init__("ROD-4A — Mass Flow Controllers")
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
                self.apply_btn = QtWidgets.QPushButton("Apply Setpoints")
                self.close_all_btn = QtWidgets.QPushButton("Close All Valves")
                self.apply_btn.clicked.connect(self.apply_setpoints)
                self.close_all_btn.clicked.connect(self.close_all)
                btn_layout.addWidget(self.apply_btn)
                btn_layout.addWidget(self.close_all_btn)
                layout.addLayout(btn_layout)

                self.status_label = QtWidgets.QLabel("Status: —")
                layout.addWidget(self.status_label)
                self.setLayout(layout)

            def apply_setpoints(self):
                try:
                    if not instrument.connected:
                        instrument.connect()
                    setpoints = [sp.value() for sp in self.setpoint_spins]
                    instrument.set_parameters(setpoints)
                    instrument.start()
                    self.status_label.setText("Status: Setpoints applied")
                except Exception as e:
                    self.status_label.setText(f"Status: Error — {e}")

            def close_all(self):
                try:
                    instrument.set_parameters([0.0, 0.0, 0.0, 0.0])
                    instrument.start()
                    self.status_label.setText("Status: All valves closed")
                except Exception as e:
                    self.status_label.setText(f"Status: Error — {e}")

            def update_flows(self):
                if not instrument.connected:
                    return
                try:
                    data = instrument.get_data()
                    for i, dt in enumerate(data):
                        if i < 4:
                            self.flow_labels[i].setText(f"{dt.value:.2f} sccm")
                except Exception:
                    pass

        return ROD4Panel()