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
            data_label: labels for DataTuples. :meth:`get_data` returns two results
                for each channel (Ewe, I), and corresponding
                labels should match entries in DATA_COLUMNS.
            name: name of instrument.
            eclib_path: path to the directory containing the EClib DLL. If None, default
                is used.

        Raises:
            ValueError: if `data_label` does not contain 2 entries per channel.
        """
        if not hasattr(channels, "__len__"):
            channels = (channels,)
        if len(channels) * 2 != len(data_label):
            raise ValueError("data_label must contain 2 entries per channel.")
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
        self._dc_condition = None
        super().__init__(data_label, name)

    def connect(self) -> None:
        """Connect to Biologic."""
        with self.lock:
            self.biologic.connect()
            self.biologic.load_firmware(self._chan_bool)
            self._connected = True

    def _initialize_DC(
        self,
        app_step: list,
        dur_step: list,
        record_time: float,
        technique: str,
        dc: Type[Technique],
        **kwargs,
    )-> None:
        technique_dict: dict = globals()[technique + "_DICT"].copy()
        technique_dict.update(
            {
                "duration_step": dur_step,
                "record_every_dt": record_time
            }
        )
        if technique in "CP":
            technique_dict.update({"current_step": app_step})
        else:
            technique_dict.update({"voltage_step": app_step})
        for key in kwargs.keys():
            if key not in technique_dict:
                raise KeyError(
                    f"Biologic technique {technique} does not contain "
                    f"keyword argument {key}"
                )
        self._dc = dc(**technique_dict)

    def set_parameters(
        self,
        record_time: float,
        applied_step: list,
        duration_step: list,
        technique: str,
        dc_condition: Callable[[], bool],
        **kwargs,
    ) -> None:
        """Set measurement parameters and prepare DC technique.

        Args:
            record_time: time between recording events.
            applied_step: list of currents in Amps or voltages in Amps to apply
            duration_step: list of durations in seconds.
            technique: DC technique to run, must be `CP` or 'CA'. Defaults to 'CA'.
            dc_condition: function indicating whether to begin dc measurement.
            **kwargs: additional kwargs to pass to `technique`.

        Raises:
            KeyError: if `technique` is not supported.
        """
        technique = technique.upper()
        if technique not in ("CA", "CP"):
            raise KeyError(
                f"Technique {technique} must be `CA` or `CP`."
            )
        dc: Type[Technique] = getattr(
            importlib.import_module("nupylab.drivers.biologic"), technique
        )
        self.ocv: OCV = OCV(
            duration=24 * 60 * 60,
            record_every_de=0.1,
            record_every_dt=record_time,
            e_range="KBIO_ERANGE_AUTO",
        )
        self._dc_condition = dc_condition
        self._initialize_DC(
            applied_step,
            duration_step,
            record_time,
            technique,
            dc,
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
        """Get OCV or DC data for each channel.

        Returns:
            DataTuples in the order E_we, I for each
            channel if measuring eis, E_we only if measuring OCV.
        """
        with self.lock:
            all_data = [self.biologic.get_data(c) for c in self.channels]
            if not self._measuring_ocv:
                self._finished = all(
                    self.biologic.get_channel_infos(c)["State"] == 0 for c in self.channels
                )
            # Switch from OCV to DC upon external condition, like furnace program complete
            if self.dc_condition:
                if len(self.channels) == 1:
                    channel = self.channels[0]
                    self.biologic.stop_channel(channel)
                    self.biologic.load_technique(channel, self._dc, first=True, last=True)
                    self.biologic.start_channel(channel)
                else:
                    self.biologic.stop_channels(self._chan_bool)
                    for c in self.channels:
                        self.biologic.load_technique(c, self._dc, first=True, last=True)
                    self.biologic.start_channels(self._chan_bool)
                self._measuring_ocv = False

        data = []
        for kbio_data, c in zip(all_data, self.channels):
            if kbio_data is None:
                continue

            if "I" in kbio_data.data_field_names:  # Measuring CP or CA
                data.append((
                    DataTuple(self.data_label[0], kbio_data.Ewe),
                    DataTuple(self.data_label[1], kbio_data.I),)
                )
            else:
                data.append(DataTuple(self.data_label[0], kbio_data.Ewe))
        return data

    @property
    def dc_condition(self) -> bool:
        """Get whether to begin eis measurement."""
        if not self._measuring_ocv:  # Prevents unnecessary function calls
            return False
        return self._dc_condition()

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

CP_DICT = {
    "current_step": [1e-6,],
    "duration_step": [5.0,],
    "vs_initial": False,
    "n_cycles": 0,
    "record_every_dt": 0.1,
    "record_every_de": 0.1,
    "i_range": "KBIO_IRANGE_AUTO",
    "e_range": "KBIO_ERANGE_2_5",
    "bandwidth": "KBIO_BW_5",
}

CA_DICT = {
    "voltage_step": [1e-3,],
    "duration_step": [5.0,],
    "vs_initial": False,
    "n_cycles": 0,
    "record_every_dt": 0.1,
    "record_every_di": 0.1,
    "i_range": "KBIO_IRANGE_AUTO",
    "e_range": "KBIO_ERANGE_2_5",
    "bandwidth": "KBIO_BW_5",
}