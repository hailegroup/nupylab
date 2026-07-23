"""Adapts Agilent 4284A driver to NUPylab instrument class for use with NUPyLab GUIs."""

from typing import Sequence, List, Optional, Callable

import numpy as np
from pymeasure.instruments.agilent import agilent4284A
from nupylab.utilities import DataTuple, NupylabError
from nupylab.utilities.nupylab_instrument import NupylabInstrument


class Agilent4284A(NupylabInstrument):
    """Agilent 4284A instrument class. Abstracts driver for NUPyLab procedures.

    Attributes:
        data_label: labels for DataTuples.
        name: name of instrument.
        lock: thread lock for preventing simultaneous calls to instrument.
        agilent: Agilent 4284A driver class.
    """

    def __init__(
        self,
        port: str,
        data_label: Sequence[str],
        name: str = "Agilent 4284A",
    ) -> None:
        """Initialize Agilent data labels, name, and connection parameters.

        Args:
            port: string name of port, e.g. `GPIB::1::INSTR`.
            data_label: labels for DataTuples. :meth:`get_data` returns frequency,
                Z_re, and -Z_im, and corresponding labels should match entries in
                DATA_COLUMNS.
            name: name of instrument.

        Raises:
            ValueError: if `data_label` does not contain 3 entries.
        """
        if len(data_label) != 3:
            raise ValueError("Agilent 4284A data_label must be sequence of length 3.")
        self.agilent = None
        self._port = port
        self._finished: bool = False
        self._freq_list = None
        self._eis_condition = None
        super().__init__(data_label, name)

    def connect(self) -> None:
        """Connect to Agilent 4284A."""
        with self.lock:
            self.agilent = agilent4284A.Agilent4284A(self._port)
            self._connected = True

    def set_parameters(
        self,
        maximum_frequency: float,
        minimum_frequency: float,
        amplitude: float,
        points_per_decade: int,
        technique: str,
        eis_condition: Callable[[], bool],
    ) -> None:
        """Set eis measurement parameters.

        Args:
            maximum_frequency: maximum eis frequency in Hz.
            minimum_frequency: minimum eis frequency in Hz.
            amplitude: eis amplitude in Volt or Amp, depending on whether technique is
                PEIS or GEIS.
            points_per_decade: eis frequency points per decade.
            technique: eis technique to run, must be `PEIS` or `GEIS`.
            eis_condition: function indicating whether to begin eis measurement.

        Raises:
            KeyError: if `technique` is not supported.
        """
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
        self._parameters = True  # Placeholder just to indicate parameters are set.

    def start(self) -> None:
        """Prepare eis measurement. Verifies eis parameters were set.

        Raises:
            NupylabError: if `start` method is called before `set_parameters`.
        """
        if self._parameters is None:
            raise NupylabError(
                f"`{self.__class__.__name__}` method `set_parameters` "
                "must be called before calling its `start` method."
            )
        self._parameters = None

    def get_data(self) -> Optional[List[DataTuple]]:
        """Get eis data.

        Returns:
            DataTuples in the order of frequency, Z_re, and -Z_im if measuring eis,
            None otherwise
        """
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
        """Get whether to begin eis measurement."""
        if self.finished:  # Prevents unnecessary function calls
            return False
        return self._eis_condition()

    @property
    def finished(self) -> bool:
        """Get whether eis measurement is finished."""
        if self._eis_condition is None:
            return True
        return self._finished

    def stop_measurement(self) -> None:
        """Stop eis measurement. Not implemented."""

    def shutdown(self) -> None:
        """Disconnect from Agilent 4284A."""
        with self.lock:
            self.agilent.adapter.close()

    def control_widget(self):
        """Return a Qt control panel for this instrument."""
        from pymeasure.display.Qt import QtWidgets, QtCore

        instrument = self

        class AgilentPanel(QtWidgets.QGroupBox):
            def __init__(self):
                super().__init__("Agilent 4284A — LCR Meter")
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

                self.run_btn = QtWidgets.QPushButton("Run Single EIS Sweep")
                self.run_btn.clicked.connect(self.run_sweep)
                layout.addRow(self.run_btn)

                self.status_label = QtWidgets.QLabel("Status: —")
                layout.addRow(self.status_label)
                self.setLayout(layout)

            def run_sweep(self):
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
                    self._poll_timer = QtCore.QTimer()
                    self._poll_timer.timeout.connect(self._check_done)
                    self._poll_timer.start(500)
                except Exception as e:
                    self.status_label.setText(f"Status: Error — {e}")

            def _check_done(self):
                try:
                    instrument.get_data()
                    if instrument.finished:
                        self._poll_timer.stop()
                        self.status_label.setText("Status: Sweep complete")
                except Exception as e:
                    self._poll_timer.stop()
                    self.status_label.setText(f"Status: Error — {e}")

        return AgilentPanel()
