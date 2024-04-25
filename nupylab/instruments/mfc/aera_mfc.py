"""Adapts AeraMFC driver to NUPylab instrument class for use with NUPyLab GUIs."""

from typing import List, Sequence, Tuple, TYPE_CHECKING, Union

from pymeasure.instruments.proterial import aera_mfc
from nupylab.utilities import DataTuple, NupylabError
from ..nupylab_instrument import NupylabInstrument

if TYPE_CHECKING:
    from pymeasure.instruments.proterial.aera_mfc import AeraChannel


class AeraMFC(NupylabInstrument):
    """AeraMFC instrument class. Abstracts Aera MFC driver for NUPyLab procedures.

    Attributes:
        data_label: labels for DataTuples.
        name: name of instrument.
        aera: AeraMFC driver class with channels for all connected MFCs.
    """

    def __init__(
        self,
        port: str,
        addresses: Union[int, Sequence[int]],
        mfc_classes: Union[AeraChannel, Sequence[AeraChannel]],
        data_label: Union[str, Sequence[str]],
        name: str = "Aera MFC",
    ) -> None:
        """Initialize Aera data labels, name, and connection parameters.

        Args:
            port: string name of port, e.g. `ASRL1::INSTR`.
            addresses: MFC addresses.
            mfc_classes: MFC channel classes to add to AeraMFC.
            data_label: labels for DataTuples. :meth:`get_data` returns flow rate for
                each channel, and corresponding labels should match entries in
                DATA_COLUMNS of calling procedure class.
            name: name of instrument.

        Raises:
            ValueError if lengths of addresses, mfc_classes, and data_label
            do not match.
        """
        err_msg = "Aera MFC addresses, mfc_classes, and data_label must \
            be single-valued or sequences of same length."

        if isinstance(data_label, (tuple, list)):
            if len(addresses) == len(mfc_classes) == len(data_label):
                self._port = port
                self._addresses = addresses
                self._mfc_classes = mfc_classes
            else:
                raise ValueError(err_msg)
        elif (isinstance(addresses, (tuple, list)) and len(addresses) != 1) or (
            isinstance(mfc_classes, (tuple, list) and len(mfc_classes) != 1)
        ):
            raise ValueError(err_msg)
        else:
            self._port = port
            self._addresses = (addresses,)
            self._mfc_classes = (mfc_classes,)

        super().__init__(data_label, name)

    def connect(self) -> None:
        """Connect to Aera MFCs."""
        self.aera = aera_mfc.AeraMFC(self._port)
        for address, mfc_class in zip(self._addresses, self._mfc_classes):
            self.aera.add_channel(address, mfc_class)
        self._ranges = tuple(
            channel.mfc_range for channel in self.aera.channels.values()
        )
        self._connected = True

    def set_parameters(self, setpoints: Sequence[float]) -> None:
        """Set Aera flow setpoints.

        Args:
            setpoints: MFC channel setpoints.

        Raises:
            ValueError if lengths of setpoints and data_label do not match.
        """
        err_msg = "Setpoints length must match length of data_label."
        if isinstance(setpoints, (float, int)) and not isinstance(
            self.data_label, str
        ):
            raise ValueError(err_msg)
        elif len(setpoints) != len(self.data_label):
            raise ValueError(err_msg)
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
        for channel, setpoint, range_ in zip(
            self.aera.channels.values(), setpoints, self._ranges
        ):
            channel.setpoint = 100 * setpoint / range_
            if setpoint == 0:
                channel.valve_mode = "close"
            else:
                channel.valve_mode = "flow"
        self._parameters = None

    def get_data(self) -> Tuple[DataTuple]:
        """Read flow for each MFC channel.

        Returns:
            DataTuples with flow for each channel.
        """
        flow_rates: List[float] = []
        for channel, range_ in zip(self.aera.channels.values(), self._ranges):
            flow_rates.append(channel.actual_flow * range_ / 100)
        return (
            DataTuple(label, flow_rate)
            for label, flow_rate in zip(self.data_label, flow_rates)
        )

    @property
    def finished(self) -> bool:
        """Get whether Aera MFCs are finished. Always False."""
        return False

    def stop_measurement(self) -> None:
        """Stop Aera MFC measurement. Not implemented."""
        pass

    def shutdown(self) -> None:
        """Shutdown Aera MFC gas flow and close serial connection."""
        for channel in self.aera.channels.values():
            channel.valve_mode = "close"
        self.aera.adapter.close()
