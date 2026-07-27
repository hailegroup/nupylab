"""Adapts Agilent 4284A driver to NUPylab instrument class for use with NUPyLab GUIs."""

import logging
from typing import Sequence, List, Optional, Callable

import numpy as np
from pymeasure.instruments.agilent import agilent4284A
from nupylab.utilities import DataTuple, NupylabError
from nupylab.utilities.nupylab_instrument import NupylabInstrument

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

_control_log = logging.getLogger('nupylab.instrument_control')


class Agilent4284A(NupylabInstrument):
    """Agilent 4284A instrument class. Abstracts driver for NUPyLab procedures."""

    def __init__(
        self,
        port: str,
        data_label: Sequence[str],
        name: str = "Agilent 4284A",
    ) -> None:
        if len(data_label) != 3:
            raise ValueError("Agilent 4284A data_label must be sequence of length 3.")
        self.agilent = None
        self._port = port
        self._finished: bool = False
        self._freq_list = None
        self._eis_condition = None
        super().__init__(data_label, name)

    def connect(self) -> None:
        with self.lock:
            self.agilent = agilent4284A.Agilent4284A(self._port)
            self._connected = True

    def disconnect(self) -> None:
        with self.lock:
            if self.agilent is not None:
                try:
                    self.agilent.adapter.close()
                except Exception:
                    pass
                self.agilent = None
            self._connected = False

    def set_parameters(
        self,
        maximum_frequency: float,
        minimum_frequency: float,
        amplitude: float,
        points_per_decade: int,
        technique: str,
        eis_condition: Callable[[], bool],
    ) -> None:
        technique = technique.upper()
        if technique not in ("PEIS", "GEIS"):
            raise KeyError(f"Technique {technique} must be `PEIS` or `GEIS`.")
        with self.lock:
            self.agilent.clear()
            self.agilent.reset()
            if technique == "PEIS":
                self.agilent.ac_voltage = amplitude
            else:
                self.agilent.ac_current = amplitude
            self.agilent.mode = "ZTR"
        self._finished = False
        max_f_log = np.log10(maximum_frequency)
        min_f_log = np.log10(minimum_frequency)
        freq_steps: int = round((max_f_log - min_f_log) * points_per_decade) + 1
        self._freq_list = np.logspace(max_f_log, min_f_log, num=freq_steps)
        self._eis_condition = eis_condition
        self._parameters = True

    def start(self) -> None:
        if self._parameters is None:
            raise NupylabError(
                f"`{self.__class__.__name__}` method `set_parameters` "
                "must be called before calling its `start` method."
            )
        self._parameters = None

    def get_data(self) -> Optional[List[DataTuple]]:
        if not self.eis_condition:
            return DataTuple(self.data_label[0], [])
        with self.lock:
            results = self.agilent.sweep_measurement("frequency", self._freq_list)
        abs_z, z_phase, freq = results
        z_re = abs_z * np.cos(z_phase)
        z_im = abs_z * np.sin(z_phase)
        data = [
            DataTuple(self.data_label[0], freq),
            DataTuple(self.data_label[1], z_re),
            DataTuple(self.data_label[2], -z_im),
        ]
        self._finished = True
        return data

    @property
    def eis_condition(self) -> bool:
        if self.finished:
            return False
        return self._eis_condition()

    @property
    def finished(self) -> bool:
        if self._eis_condition is None:
            return True
        return self._finished

    def stop_measurement(self) -> None:
        pass

    def shutdown(self) -> None:
        with self.lock:
            self.agilent.adapter.close()

    def control_widget(self, abort_callback=None):
        """Return a Qt control panel for this instrument."""
        from pymeasure.display.Qt import QtWidgets, QtCore
        from nupylab.utilities.instrument_control import LivePlotWidget

        instrument = self

        class SweepWorker(QtCore.QThread):
            finished_sig = QtCore.Signal(list, list, list)  # freq, z_re, z_im
            error = QtCore.Signal(str)

            def run(self):
                try:
                    with instrument.lock:
                        results = instrument.agilent.sweep_measurement(
                            "frequency", instrument._freq_list
                        )
                    abs_z, z_phase, freq = results
                    abs_z = np.array(abs_z)      # ADD
                    z_phase = np.array(z_phase)  # ADD
                    freq = np.array(freq)        # ADD
                    z_re = (abs_z * np.cos(z_phase)).tolist()
                    z_im = (-abs_z * np.sin(z_phase)).tolist()
                    instrument._finished = True
                    self.finished_sig.emit(freq.tolist(), z_re, z_im)
                except Exception as e:
                    self.error.emit(str(e))

        class AgilentPanel(QtWidgets.QGroupBox):
            plot_title = "EIS — Nyquist"

            def __init__(self):
                super().__init__("Agilent 4284A — LCR Meter")
                self._worker = None
                self._abort_callback = abort_callback
                self.live_plot = LivePlotWidget(
                    "Nyquist Plot", "-Z_im (Ω)", n_traces=1
                )
                self._setup_ui()

            def _setup_ui(self):
                layout = QtWidgets.QFormLayout()

                self.max_freq = QtWidgets.QDoubleSpinBox()
                self.max_freq.setRange(20, 1e6)
                self.max_freq.setSuffix(" Hz")
                self.max_freq.setValue(1000)
                layout.addRow("Max Frequency:", self.max_freq)

                self.min_freq = QtWidgets.QDoubleSpinBox()
                self.min_freq.setRange(20, 1e6)
                self.min_freq.setSuffix(" Hz")
                self.min_freq.setValue(100)
                layout.addRow("Min Frequency:", self.min_freq)

                self.amplitude = QtWidgets.QDoubleSpinBox()
                self.amplitude.setRange(0.001, 1.0)
                self.amplitude.setSuffix(" V")
                self.amplitude.setValue(0.01)
                self.amplitude.setDecimals(3)
                layout.addRow("Amplitude:", self.amplitude)

                self.ppd = QtWidgets.QSpinBox()
                self.ppd.setRange(1, 20)
                self.ppd.setValue(10)
                layout.addRow("Points per Decade:", self.ppd)

                self.technique = QtWidgets.QComboBox()
                self.technique.addItems(["PEIS", "GEIS"])
                layout.addRow("Technique:", self.technique)

                btn_layout = QtWidgets.QHBoxLayout()
                self.connect_btn = QtWidgets.QPushButton("Connect")
                self.run_btn = QtWidgets.QPushButton("Run EIS Sweep")
                self.disconnect_btn = QtWidgets.QPushButton("Disconnect")
                self.connect_btn.clicked.connect(self.connect_instrument)
                self.run_btn.clicked.connect(self.run_sweep)
                self.disconnect_btn.clicked.connect(self.disconnect_instrument)
                btn_layout.addWidget(self.connect_btn)
                btn_layout.addWidget(self.run_btn)
                btn_layout.addWidget(self.disconnect_btn)
                layout.addRow(btn_layout)

                self.status_label = QtWidgets.QLabel("Status: Not connected")
                layout.addRow(self.status_label)
                self.setLayout(layout)

            def _abort_if_needed(self):
                if self._abort_callback:
                    self._abort_callback()

            def connect_instrument(self):
                self._abort_if_needed()
                try:
                    instrument.connect()
                    self.status_label.setText("Status: Connected")
                    _control_log.info("Agilent 4284A connected on %s", instrument._port)
                except Exception as e:
                    self.status_label.setText(f"Status: Error — {e}")
                    _control_log.error("Agilent connect failed: %s", e)

            def disconnect_instrument(self):
                self._abort_if_needed()
                try:
                    instrument.disconnect()
                    self.status_label.setText("Status: Disconnected")
                    _control_log.info("Agilent 4284A disconnected")
                except Exception as e:
                    self.status_label.setText(f"Status: Error — {e}")

            def run_sweep(self):
                self._abort_if_needed()
                try:
                    if not instrument.connected:
                        instrument.connect()
                    instrument.set_parameters(
                        self.max_freq.value(),
                        self.min_freq.value(),
                        self.amplitude.value(),
                        self.ppd.value(),
                        self.technique.currentText(),
                        lambda: True,
                    )
                    instrument.start()
                    self.status_label.setText("Status: Sweep running...")
                    self.run_btn.setEnabled(False)
                    self.live_plot.clear()
                    _control_log.info(
                        "Agilent EIS sweep started: %.1f–%.1f Hz, %.3fV",
                        self.max_freq.value(), self.min_freq.value(),
                        self.amplitude.value()
                    )
                    self._worker = SweepWorker()
                    self._worker.finished_sig.connect(self._on_sweep_done)
                    self._worker.error.connect(self._on_sweep_error)
                    self._worker.start()
                except Exception as e:
                    self.status_label.setText(f"Status: Error — {e}")
                    _control_log.error("Agilent sweep error: %s", e)

            def _on_sweep_done(self, freq, z_re, z_im):
                self.status_label.setText("Status: Sweep complete")
                self.run_btn.setEnabled(True)
                # Plot -Z_im vs Z_re (Nyquist) — add each point
                try:
                    import pyqtgraph as pg
                    self.live_plot._plot_widget.clear()
                    self.live_plot._plot_widget.plot(z_re, z_im, pen='r',
                                                     symbol='o', symbolSize=5)
                    self.live_plot._plot_widget.setLabel('bottom', 'Z_re (Ω)')
                except Exception:
                    if z_im:
                        self.live_plot._value_label.setText(
                            f"max -Z_im: {max(z_im):.2e} Ω"
                        )
                _control_log.info("Agilent EIS sweep complete")

            def _on_sweep_error(self, e):
                self.status_label.setText(f"Status: Error — {e}")
                self.run_btn.setEnabled(True)
                _control_log.error("Agilent sweep error: %s", e)

        return AgilentPanel()