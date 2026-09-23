"""Adapts Biologic driver to NUPylab instrument class for use with NUPyLab GUIs."""
from __future__ import annotations
import importlib
import logging
from typing import Sequence, Union, TYPE_CHECKING, Optional, List, Type, Callable

import numpy as np
from nupylab.drivers.biologic import BiologicPotentiostat, OCV
from nupylab.utilities import DataTuple, NupylabError
from nupylab.utilities.nupylab_instrument import NupylabInstrument

if TYPE_CHECKING:
    from nupylab.drivers.biologic import Technique

_control_log = logging.getLogger('nupylab.instrument_control')


class Biologic(NupylabInstrument):
    """Biologic instrument class. Abstracts driver for NUPyLab procedures.

    Attributes:
        data_label: labels for DataTuples.
        name: name of instrument.
        lock: thread lock for preventing simultaneous calls to instrument.
        biologic: Biologic driver class.
        channels: active measurement channels.
    """

    def __init__(
        self,
        port: str,
        model: str,
        channels: Union[int, Sequence[int]],
        data_label: Sequence[str],
        name: str = "Biologic",
        eclib_path: Optional[str] = None,
    ) -> None:
        """Initialize Biologic data labels, name, and connection parameters.

        Args:
            port: string name of port, e.g. `USB0` or IP address.
            model: Biologic model, e.g. `SP200` or `SP300`.
            channels: Biologic channels to measure, zero-based.
            data_label: labels for DataTuples. :meth:`get_data` returns seven results
                for each channel (E_we, I, frequency, Z_re, -Z_im, |Z|, and Phase), and corresponding
                labels should match entries in DATA_COLUMNS.
            name: name of instrument.
            eclib_path: path to the directory containing the EClib DLL. If None, default
                is used.

        Raises:
            ValueError: if `data_label` does not contain 7 entries per channel.
        """
        if not hasattr(channels, "__len__"):
            channels = (channels,)
        if len(channels) * 7 != len(data_label):
            raise ValueError("data_label must contain 7 entries per channel.")
        model = model.replace("-", "").replace(" ", "").upper()
        self.biologic: BiologicPotentiostat = BiologicPotentiostat(
            model, port, eclib_path
        )
        self.ocv = None
        self.channels = channels
        self._chan_bool: List[int] = [
            0,
        ] * 16  # for multi-channel operations
        for c in self.channels:
            self._chan_bool[c] = 1
        self._measuring_ocv: bool = False
        self._finished: bool = False
        self._eis_condition = None
        self._port = port
        self._model = model
        self._eclib_path = eclib_path
        self._panel = None
        super().__init__(data_label, name)

    def set_connection(self, port: str, model: str) -> None:
        """Set port and model used by the next call to :meth:`connect`.

        Args:
            port: string name of port, e.g. `USB0` or IP address.
            model: Biologic model, e.g. `SP200` or `SP300`.
        """
        self._port = port
        self._model = model.replace("-", "").replace(" ", "").upper()

    def connect(self) -> None:
        """Connect to Biologic."""
        with self.lock:
            # Rebuild driver if port or model changed since it was created
            if (
                self.biologic.address != self._port
                or self.biologic.model != "KBIO_DEV_" + self._model
            ):
                self.biologic = BiologicPotentiostat(
                    self._model, self._port, self._eclib_path
                )
            self.biologic.connect()
            self.biologic.load_firmware(self._chan_bool)
            self._connected = True

    def disconnect(self) -> None:
        """Stop any running measurement and disconnect from Biologic."""
        if self._panel is not None and self._connected:
            try:
                self._panel.timer.stop()
                self._panel.monitor_timer.stop()
                for worker in (self._panel._worker, self._panel._monitor_worker):
                    if worker and worker.isRunning():
                        worker.wait(100)
            except Exception:
                pass
        with self.lock:
            if self._connected:
                try:
                    if len(self.channels) == 1:
                        self.biologic.stop_channel(self.channels[0])
                    else:
                        self.biologic.stop_channels(self._chan_bool)
                except Exception:
                    pass
                try:
                    self.biologic.disconnect()
                except Exception:
                    pass
            self._measuring_ocv = False
            self._connected = False

    def _initialize_eis(
        self,
        step_0: float, #the initial voltage or current step (bias)
        dur_0: float, #how long to hold step_0 before starting EIS
        max_freq: float,
        min_freq: float,
        amp: float,
        ppd: int,
        record_time: float,
        technique: str,
        eis: Type[Technique],
        **kwargs,
    ) -> None:
        freq_steps: int = round((np.log10(max_freq) - np.log10(min_freq)) * ppd) + 1
        technique_dict: dict = globals()[technique + "_DICT"].copy()
        technique_dict.update(
            {
                "duration_step": dur_0*60,
                "initial_frequency": max_freq,
                "final_frequency": min_freq,
                "frequency_number": freq_steps,
                "record_every_dt": record_time
            }
        )
        if technique in ("PEIS" or "SPEIS"):
            technique_dict.update({"amplitude_voltage": amp})
            technique_dict.update({"initial_voltage_step": step_0})
        else:
            technique_dict.update({"amplitude_current": amp})
            technique_dict.update({"initial_current_step": step_0})
        for key in kwargs.keys():
            if key not in technique_dict:
                raise KeyError(
                    f"Biologic technique {technique} does not contain "
                    f"keyword argument {key}"
                )
        self._eis = eis(**technique_dict)

    def set_parameters(
        self,
        record_time: float,
        initial_step: float,
        duration_step: float,
        maximum_frequency: float,
        minimum_frequency: float,
        amplitude: float,
        points_per_decade: int,
        technique: str,
        eis_condition: Callable[[], bool],
        **kwargs,
    ) -> None:
        """Set measurement parameters and prepare eis technique.

        Args:
            record_time: time between recording events.
            maximum_frequency: maximum eis frequency in Hz.
            minimum_frequency: minimum eis frequency in Hz.
            amplitude: eis amplitude in Volt or Amp, depending on whether technique is
                PEIS or GEIS.
            points_per_decade: eis frequency points per decade.
            technique: eis technique to run, must be `PEIS`, `GEIS`, `SPEIS`, or
                `SGEIS`. Defaults to `PEIS`.
            eis_condition: function indicating whether to begin eis measurement.
            **kwargs: additional kwargs to pass to `technique`.

        Raises:
            KeyError: if `technique` is not supported.
        """
        technique = technique.upper()
        if technique not in ("PEIS", "GEIS", "SPEIS", "SGEIS"):
            raise KeyError(
                f"Technique {technique} must be `PEIS`, `GEIS`, `SPEIS`, or `SGEIS`."
            )
        eis: Type[Technique] = getattr(
            importlib.import_module("nupylab.drivers.biologic"), technique
        )
        self.ocv: OCV = OCV(
            duration=24 * 60 * 60,
            record_every_de=0.1,
            record_every_dt=record_time,
            e_range="KBIO_ERANGE_AUTO",
        )
        self._eis_condition = eis_condition
        self._initialize_eis(
            initial_step,
            duration_step,
            maximum_frequency,
            minimum_frequency,
            amplitude,
            points_per_decade,
            record_time,
            technique,
            eis,
            **kwargs,
        )
        self._finished = False
        self._parameters = True  # Placeholder just to indicate parameters are set.

    def start(self) -> None:
        """Start OCV measurement on Biologic channel(s).

        Raises:
            NupylabError: if `start` method is called before `set_parameters`.
        """
        if self._parameters is None:
            raise NupylabError(
                f"`{self.__class__.__name__}` method `set_parameters` "
                "must be called before calling its `start` method."
            )
        with self.lock:
            for c in self.channels:
                self.biologic.load_technique(c, self.ocv, first=True, last=True)
            if len(self.channels) == 1:
                self.biologic.start_channel(self.channels[0])
            else:
                self.biologic.start_channels(self._chan_bool)
        self._measuring_ocv = True
        self._parameters = None

    def get_data(self) -> List[DataTuple]:
        """Get OCV or eis data for each channel.

        Returns:
            DataTuples in the order E_we, frequency, Z_re, and -Z_im for each
            channel if measuring eis, E_we only if measuring OCV.
        """
        with self.lock:
            all_data = [self.biologic.get_data(c) for c in self.channels]
            if not self._measuring_ocv:
                self._finished = all(
                    self.biologic.get_channel_infos(c)["State"] == 0 for c in self.channels
                )
            # Switch from OCV to eis upon external condition, like furnace program complete
            if self.eis_condition:
                if len(self.channels) == 1:
                    channel = self.channels[0]
                    self.biologic.stop_channel(channel)
                    self.biologic.load_technique(channel, self._eis, first=True, last=True)
                    self.biologic.start_channel(channel)
                else:
                    self.biologic.stop_channels(self._chan_bool)
                    for c in self.channels:
                        self.biologic.load_technique(c, self._eis, first=True, last=True)
                    self.biologic.start_channels(self._chan_bool)
                self._measuring_ocv = False

        data = []
        for kbio_data, c in zip(all_data, self.channels):
            if kbio_data is None:
                continue

            if "freq" in kbio_data.data_field_names:  # Measuring PEIS
                abs_z = kbio_data.abs_Ewe_numpy / kbio_data.abs_I_numpy
                z_phase = kbio_data.Phase_Zwe_numpy
                z_re = abs_z * np.cos(z_phase)
                z_im = abs_z * np.sin(z_phase)
                theta = np.arctan(z_im/z_re)*180/np.pi
                data.append((
                    DataTuple(self.data_label[0], kbio_data.Ewe),
                    DataTuple(self.data_label[1], kbio_data.I),
                    DataTuple(self.data_label[2], kbio_data.freq),
                    DataTuple(self.data_label[3], z_re),
                    DataTuple(self.data_label[4], -z_im),
                    DataTuple(self.data_label[5], np.abs(abs_z)),
                    DataTuple(self.data_label[6], theta),)
                )
            else:
                data.append(DataTuple(self.data_label[0], kbio_data.Ewe))
        return data

    def get_current_values(self) -> tuple:
        """Read present Ewe and I on the first channel without loading a technique.

        Returns:
            Tuple of Ewe in V and I in A.
        """
        with self.lock:
            values = self.biologic.get_current_values(self.channels[0])
        return values["Ewe"], values["I"]

    @property
    def eis_condition(self) -> bool:
        """Get whether to begin eis measurement."""
        if not self._measuring_ocv:  # Prevents unnecessary function calls
            return False
        return self._eis_condition()

    @property
    def finished(self) -> bool:
        """Get whether Biologic channels are finished."""
        if self._measuring_ocv:  # Never finished if measuring OCV
            return False
        return self._finished

    def stop_measurement(self) -> None:
        """Stop measurement on all Biologic channels."""
        with self.lock:
            if len(self.channels) == 1:
                self.biologic.stop_channel(self.channels[0])
            else:
                self.biologic.stop_channels(self._chan_bool)

    def shutdown(self) -> None:
        """Disconnect from Biologic."""
        with self.lock:
            self.biologic.disconnect()

    def control_widget(self, abort_callback=None):
        """Return a Qt control panel for this instrument."""
        from pymeasure.display.Qt import QtWidgets, QtCore
        from nupylab.utilities.instrument_control import LivePlotWidget

        instrument = self

        class Worker(QtCore.QThread):
            # rows of [freq, Z_re, -Z_im, |Z|, phase, Ewe, I], latest Ewe
            result = QtCore.Signal(list, float)
            error = QtCore.Signal(str)

            def run(self):
                try:
                    data = instrument.get_data()
                    rows = []
                    ewe = float("nan")
                    for channel_data in data:
                        if isinstance(channel_data, DataTuple):  # Ewe only
                            values = np.atleast_1d(channel_data.value)
                            if values.size:
                                ewe = float(values[-1])
                            continue
                        columns = [np.atleast_1d(d.value) for d in channel_data]
                        e_we, i, freq, z_re, z_im, abs_z, phase = columns
                        for n in range(len(freq)):
                            rows.append([
                                float(freq[n]), float(z_re[n]), float(z_im[n]),
                                float(abs_z[n]), float(phase[n]),
                                float(e_we[n]), float(i[n]),
                            ])
                        if e_we.size:
                            ewe = float(e_we[-1])
                    self.result.emit(rows, ewe)
                except Exception as e:
                    self.error.emit(str(e))

        class MonitorWorker(QtCore.QThread):
            result = QtCore.Signal(float, float)

            def run(self):
                try:
                    ewe, i = instrument.get_current_values()
                    self.result.emit(ewe, i)
                except Exception:
                    pass

        class BiologicPanel(QtWidgets.QGroupBox):
            plot_title = "EIS — Nyquist"
            instrument_name = "Biologic"
            record_columns = [
                "Frequency (Hz)",
                "Z_re (ohm)",
                "-Z_im (ohm)",
                "|Z| (ohm)",
                "Phase (degrees)",
                "Ewe (V)",
                "I (A)",
            ]
            data_recorded = QtCore.Signal(list)

            def __init__(self):
                super().__init__("Biologic — Potentiostat")
                self._worker = None
                self._monitor_worker = None
                self._running = False
                self._final_poll = False
                self._abort_callback = abort_callback
                self._z_re: List[float] = []
                self._z_im: List[float] = []
                self.live_plot = LivePlotWidget(
                    "Nyquist Plot", "-Z_im (Ω)", n_traces=1
                )
                self._setup_ui()
                self.timer = QtCore.QTimer()
                self.timer.timeout.connect(self.update_data)
                # Ewe/I readout while connected, independent of EIS runs
                self.monitor_timer = QtCore.QTimer()
                self.monitor_timer.timeout.connect(self.update_monitor)
                instrument._panel = self

            def _setup_ui(self):
                layout = QtWidgets.QFormLayout()

                self.technique = QtWidgets.QComboBox()
                self.technique.addItems(["PEIS", "GEIS", "SPEIS", "SGEIS"])
                self.technique.currentTextChanged.connect(self._on_technique_changed)
                layout.addRow("Technique:", self.technique)

                self.initial_step = QtWidgets.QDoubleSpinBox()
                self.initial_step.setRange(-10, 10)
                self.initial_step.setDecimals(4)
                self.initial_step.setSuffix(" V")
                self.initial_step.setValue(0)
                layout.addRow("Initial Ewe or I:", self.initial_step)

                self.duration_step = QtWidgets.QDoubleSpinBox()
                self.duration_step.setRange(0, 9999)
                self.duration_step.setSuffix(" min")
                self.duration_step.setValue(0)
                layout.addRow("Hold before EIS:", self.duration_step)

                self.max_freq = QtWidgets.QDoubleSpinBox()
                self.max_freq.setRange(1e-5, 7e6)
                self.max_freq.setDecimals(5)
                self.max_freq.setSuffix(" Hz")
                self.max_freq.setValue(1e6)
                layout.addRow("Max Frequency:", self.max_freq)

                self.min_freq = QtWidgets.QDoubleSpinBox()
                self.min_freq.setRange(1e-5, 7e6)
                self.min_freq.setDecimals(5)
                self.min_freq.setSuffix(" Hz")
                self.min_freq.setValue(1)
                layout.addRow("Min Frequency:", self.min_freq)

                self.amplitude = QtWidgets.QDoubleSpinBox()
                self.amplitude.setRange(0.0001, 1.0)
                self.amplitude.setDecimals(4)
                self.amplitude.setSuffix(" V")
                self.amplitude.setValue(0.01)
                layout.addRow("Amplitude:", self.amplitude)

                self.ppd = QtWidgets.QSpinBox()
                self.ppd.setRange(1, 100)
                self.ppd.setValue(10)
                layout.addRow("Points per Decade:", self.ppd)

                self.record_time = QtWidgets.QDoubleSpinBox()
                self.record_time.setRange(0.01, 3600)
                self.record_time.setSuffix(" s")
                self.record_time.setValue(1)
                layout.addRow("Record Time:", self.record_time)

                btn_layout = QtWidgets.QHBoxLayout()
                self.connect_btn = QtWidgets.QPushButton("Connect")
                self.run_btn = QtWidgets.QPushButton("Run EIS")
                self.stop_btn = QtWidgets.QPushButton("Stop")
                self.disconnect_btn = QtWidgets.QPushButton("Disconnect")
                self.connect_btn.clicked.connect(self.connect_instrument)
                self.run_btn.clicked.connect(self.run_eis)
                self.stop_btn.clicked.connect(self.stop_eis)
                self.disconnect_btn.clicked.connect(self.disconnect_instrument)
                btn_layout.addWidget(self.connect_btn)
                btn_layout.addWidget(self.run_btn)
                btn_layout.addWidget(self.stop_btn)
                btn_layout.addWidget(self.disconnect_btn)
                layout.addRow(btn_layout)

                self.ewe_label = QtWidgets.QLabel("Ewe: —    I: —")
                self.status_label = QtWidgets.QLabel("Status: Not connected")
                layout.addRow(self.ewe_label)
                layout.addRow(self.status_label)
                self.setLayout(layout)

            def _on_technique_changed(self, technique):
                # PEIS/SPEIS are potential controlled, GEIS/SGEIS current controlled
                suffix = " V" if technique in ("PEIS", "SPEIS") else " A"
                self.initial_step.setSuffix(suffix)
                self.amplitude.setSuffix(suffix)

            def _abort_if_needed(self):
                if self._abort_callback:
                    self._abort_callback()

            def connect_instrument(self):
                self._abort_if_needed()
                try:
                    instrument.connect()
                    self.monitor_timer.start(2000)
                    self.status_label.setText("Status: Connected")
                    _control_log.info(
                        "Biologic %s connected on %s",
                        instrument._model, instrument._port
                    )
                except Exception as e:
                    self.status_label.setText(f"Status: Error — {e}")
                    _control_log.error("Biologic connect failed: %s", e)

            def disconnect_instrument(self):
                self._abort_if_needed()
                try:
                    self.timer.stop()
                    self.monitor_timer.stop()
                    for worker in (self._worker, self._monitor_worker):
                        if worker and worker.isRunning():
                            worker.wait(2000)
                    instrument.disconnect()
                    self._set_running(False)
                    self.status_label.setText("Status: Disconnected")
                    self.ewe_label.setText("Ewe: —    I: —")
                    _control_log.info("Biologic disconnected")
                except Exception as e:
                    self.status_label.setText(f"Status: Error — {e}")

            def _set_running(self, running):
                self._running = running
                self._final_poll = False
                self.run_btn.setEnabled(not running)
                self.technique.setEnabled(not running)

            def run_eis(self):
                self._abort_if_needed()
                try:
                    if self.min_freq.value() >= self.max_freq.value():
                        raise ValueError(
                            "Min frequency must be less than max frequency"
                        )
                    if not instrument.connected:
                        instrument.connect()
                    if not self.monitor_timer.isActive():
                        self.monitor_timer.start(2000)
                    instrument.set_parameters(
                        self.record_time.value(),
                        self.initial_step.value(),
                        self.duration_step.value(),
                        self.max_freq.value(),
                        self.min_freq.value(),
                        self.amplitude.value(),
                        self.ppd.value(),
                        self.technique.currentText(),
                        lambda: True,  # Skip OCV wait, begin EIS immediately
                    )
                    instrument.start()
                    self._z_re, self._z_im = [], []
                    self.live_plot.clear()
                    self._set_running(True)
                    self.timer.start(1000)
                    self.status_label.setText("Status: EIS running...")
                    _control_log.info(
                        "Biologic %s started: %.4g–%.4g Hz, amplitude %.4g, "
                        "hold %.1f min",
                        self.technique.currentText(), self.max_freq.value(),
                        self.min_freq.value(), self.amplitude.value(),
                        self.duration_step.value()
                    )
                except Exception as e:
                    self.status_label.setText(f"Status: Error — {e}")
                    _control_log.error("Biologic EIS start failed: %s", e)

            def stop_eis(self):
                self._abort_if_needed()
                try:
                    self.timer.stop()
                    if self._worker and self._worker.isRunning():
                        self._worker.wait(2000)
                    if instrument.connected:
                        instrument.stop_measurement()
                    self._set_running(False)
                    self.status_label.setText("Status: Stopped")
                    _control_log.info("Biologic measurement stopped")
                except Exception as e:
                    self.status_label.setText(f"Status: Error — {e}")

            def update_data(self):
                if not instrument.connected or not self._running:
                    return
                if self._worker and self._worker.isRunning():
                    return
                self._worker = Worker()
                self._worker.result.connect(self._on_result)
                self._worker.error.connect(self._on_error)
                self._worker.start()

            def update_monitor(self):
                if not instrument.connected:
                    return
                if self._monitor_worker and self._monitor_worker.isRunning():
                    return
                self._monitor_worker = MonitorWorker()
                self._monitor_worker.result.connect(self._on_monitor)
                self._monitor_worker.start()

            def _on_monitor(self, ewe, i):
                self.ewe_label.setText(f"Ewe: {ewe:.4f} V    I: {i:.4e} A")

            def _on_result(self, rows, ewe):
                if rows:
                    for row in rows:
                        self._z_re.append(row[1])
                        self._z_im.append(row[2])
                        self.data_recorded.emit(row)
                    try:
                        self.live_plot.set_xy(self._z_re, self._z_im)
                        self.live_plot.set_labels("Z_re (Ω)", "-Z_im (Ω)")
                    except Exception:
                        pass
                    self.status_label.setText(
                        f"Status: EIS running... {len(self._z_re)} points"
                    )
                if instrument.finished:
                    # Poll once more after channel stops to collect buffered data
                    if self._final_poll:
                        self.timer.stop()
                        self._set_running(False)
                        self.status_label.setText(
                            f"Status: EIS complete ({len(self._z_re)} points)"
                        )
                        _control_log.info("Biologic EIS complete")
                    else:
                        self._final_poll = True

            def _on_error(self, e):
                self.timer.stop()
                self._set_running(False)
                self.status_label.setText(f"Status: Error — {e}")
                _control_log.error("Biologic data error: %s", e)

        return BiologicPanel()


PEIS_DICT = {
    "initial_voltage_step": 0,
    "duration_step": 5.0,
    "vs_initial": False,
    "initial_frequency": 100.0e3,
    "final_frequency": 1.0,
    "logarithmic_spacing": True,
    "amplitude_voltage": 0.01,
    "frequency_number": 51,
    "average_n_times": 1,
    "wait_for_steady": 1.0,
    "drift_correction": False,
    "record_every_dt": 0.1,
    "record_every_di": 0.1,
    "i_range": "KBIO_IRANGE_AUTO",
    "e_range": "KBIO_ERANGE_2_5",
    "bandwidth": "KBIO_BW_5",
}

SPEIS_DICT = {
    "initial_voltage_step": 0.0,
    "duration_step": 10.0,
    "final_voltage_step": 0.1,
    "vs_initial": False,
    "step_number": 10,
    "initial_frequency": 100.0e3,
    "final_frequency": 1.0,
    "logarithmic_spacing": True,
    "amplitude_voltage": 0.01,
    "frequency_number": 51,
    "average_n_times": 1,
    "wait_for_steady": 1.0,
    "drift_correction": False,
    "record_every_dt": 0.1,
    "record_every_di": 0.1,
    "i_range": "KBIO_IRANGE_AUTO",
    "e_range": "KBIO_ERANGE_2_5",
    "bandwidth": "KBIO_BW_5",
}

GEIS_DICT = {
    "initial_current_step": 0.0,
    "duration_step": 5.0,
    "vs_initial": False,
    "initial_frequency": 100.0e3,
    "final_frequency": 1.0,
    "logarithmic_spacing": True,
    "amplitude_current": 50.0e-3,
    "frequency_number": 51,
    "average_n_times": 1,
    "wait_for_steady": 1.0,
    "drift_correction": False,
    "record_every_dt": 0.1,
    "record_every_de": 0.1,
    "i_range": "KBIO_IRANGE_1mA",
    "e_range": "KBIO_ERANGE_AUTO",
    "bandwidth": "KBIO_BW_5",
}

SGEIS_DICT = {
    "initial_current_step": 0.0,
    "duration_step": 10.0,
    "final_current_step": 0.1,
    "vs_initial": False,
    "step_number": 10,
    "initial_frequency": 100.0e3,
    "final_frequency": 1.0,
    "logarithmic_spacing": True,
    "amplitude_current": 0.01,
    "frequency_number": 51,
    "average_n_times": 1,
    "wait_for_steady": 1.0,
    "drift_correction": False,
    "record_every_dt": 0.1,
    "record_every_de": 0.1,
    "i_range": "KBIO_IRANGE_1mA",
    "e_range": "KBIO_ERANGE_AUTO",
    "bandwidth": "KBIO_BW_5",
}
