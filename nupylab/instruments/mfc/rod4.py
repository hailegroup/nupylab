"""Adapts ROD-4 driver to NUPylab instrument class for use with NUPyLab GUIs."""

from typing import List, Sequence

from nupylab.utilities import DataTuple, NupylabError
from pymeasure.instruments.proterial import rod4
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
                self._port, 9600, bytesize=8, stopbits=2,
                parity='N', timeout=1, xonxoff=False, rtscts=False
            )
            time.sleep(0.5)
            for i in range(1, 5):
                resp = self._send(f"FF {i}")
                try:
                    parts = resp.split()
                    slpm = float(parts[2])
                    self._ranges[i-1] = slpm * 1000
                except Exception:
                    self._ranges[i-1] = 1000.0
            self._connected = True

    def _send(self, cmd: str) -> str:
        """Send ASCII command and return response."""
        self._serial.write((cmd + "\r").encode())
        time.sleep(0.15)
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
                pct = 100.0 * setpoint / range_ if range_ > 0 else 0.0
                self._send(f"RF {i} 0")
                if setpoint == 0:
                    self._send(f"VM {i} 0")
                else:
                    self._send(f"VM {i} 1")
                self._send(f"SP {i} {pct:.2f}")
        self._parameters = None

    def get_data(self) -> List[DataTuple]:
        """Read flow for each MFC channel.

        Returns:
            tuple of four DataTuples with flow for each channel.
        """
        with self.lock:
            resp = self._send("SD")
        flows = []
        for i, range_ in enumerate(self._ranges):
            try:
                tag = f"#{i+1}:"
                idx = resp.index(tag) + len(tag)
                end = resp.index("%", idx)
                pct = float(resp[idx:end].strip())
                flows.append(pct * range_ / 100.0)
            except Exception:
                flows.append(0.0)
        return [DataTuple(self.data_label[i], flows[i]) for i in range(4)]
    
    def stop_measurement(self) -> None:
        """Stop ROD-4 measurement. Not implemented."""
        pass

    def shutdown(self) -> None:
        """Shutdown ROD-4 gas flow and close serial connection."""
        with self.lock:
            for i in range(1, 5):
                self._send(f"VM {i} 0")
            self._serial.close()