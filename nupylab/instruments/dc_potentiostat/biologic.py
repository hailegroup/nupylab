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
        applied_step: list,
        duration_step: list,
        record_time: float,
        technique: str,
        dc: Type[Technique],
        **kwargs,
    )-> None:
        technique_dict: dict = globals()[technique + "_DICT"].copy()
        technique_dict.update(
            {
                "duration_step": duration_step,
                "record_every_dt": record_time
            }
        )
        if technique in "CP":
            technique_dict.update({"current_step": applied_step})
        else:
            technique_dict.update({"voltage_step": applied_step})
        for key in kwargs.keys():
            if key not in technique_dict:
                raise KeyError(
                    f"Biologic technique {technique} does not contain "
                    f"keyword argument {key}"
                )
        self._dc = dc(**technique_dict)