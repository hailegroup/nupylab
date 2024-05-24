"""Adapts Biologic driver to NUPylab instrument class for use with NUPyLab GUIs."""
from __future__ import annotations
import importlib
from typing import Sequence, Union, TYPE_CHECKING, Optional, List, Type, Callable

import numpy as np
from nupylab.drivers.biologic import BiologicPotentiostat, OCV
from nupylab.utilities import DataTuple, NupylabError
from nupylab.utilities.nupylab_instrument import NupylabInstrument

if TYPE_CHECKING:
    from nupylab.drivers.biologic import Technique


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
            data_label: labels for DataTuples. :meth:`get_data` returns four results
                for each channel (E_we, frequency, Z_re, and -Z_im), and corresponding
                labels should match entries in DATA_COLUMNS.
            name: name of instrument.
            eclib_path: path to the directory containing the EClib DLL. If None, default
                is used.

        Raises:
            ValueError: if `data_label` does not contain 4 entries per channel.
        """
        if not hasattr(channels, "__len__"):
            channels = (channels,)
        if len(channels) * 4 != len(data_label):
            raise ValueError("data_label must contain 4 entries per channel.")
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
        super().__init__(data_label, name)

    def connect(self) -> None:
        """Connect to Biologic."""
        with self.lock:
            self.biologic.connect()
            self.biologic.load_firmware(self._chan_bool)
            self._connected = True

    def _initialize_eis(
        self,
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
                "initial_frequency": max_freq,
                "final_frequency": min_freq,
                "frequency_number": freq_steps,
                "record_every_dt": record_time
            }
        )
        if technique in ("PEIS" or "SPEIS"):
            technique_dict.update({"amplitude_voltage": amp})
        else:
            technique_dict.update({"amplitude_current": amp})
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
                data.append((
                    DataTuple(self.data_label[0], kbio_data.Ewe),
                    DataTuple(self.data_label[1], kbio_data.freq),
                    DataTuple(self.data_label[2], z_re),
                    DataTuple(self.data_label[3], -z_im),)
                )
            else:
                data.append(DataTuple(self.data_label[0], kbio_data.Ewe))
        return data

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
