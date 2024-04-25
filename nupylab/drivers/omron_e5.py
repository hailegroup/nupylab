"""Driver for Omron E5AR-T and E5ER-T.

Omron supports CompoWay/F and RTU Modbus protocols. However, Omron does not support
accessing programmer data with Modbus, so this driver is written for CompoWay/F.

CompoWay/F communication:
    * 1 start bit
    * 7 (default) or 8 data bits
    * 2 (default) or 1 stop bits
    * EVEN (default), ODD, or NONE parity bit
    * baud rate 9600 (default), 19200, 38400
    * BCC calculated by this script

Written and read values are expressed in hexadecimal and disregard the decimal point.
The number of decimal places must be known beforehand. Negative values are expressed as
a two's complement.
"""

from __future__ import annotations
import logging
import serial
from typing import Any, Optional

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

_OMRON_END_CODES = {
    "0F": "Could not execute the specified FINS command.",
    "10": "Parity error.",
    "11": "Framing error.",
    "12": "Attempted to transfer new data when reception data buffer is already full.",
    "13": "BCC error.",
    "14": "Format error.",
    "16": "Sub-address error.",
    "18": "Frame length error."
}

_OMRON_RESPONSE_CODES = {
    "1001": "Command length too long.",
    "1002": "Command length too short.",
    "1003": "The specified number of elements does not agree with the actual number of "
            "data elements.",
    "1101": "Incorrect variable type.",
    "110B": "Number of elements is greater than 25.",
    "1100": "Specified bit position is not `00`, or write data is outside setting "
            "range.",
    "2203": "Operation error."
}

_OMRON_ADDRESS = {
    "process_value": ("C0", "0000", "ch", "ip"),
    "status": ("C0", "0001", "ch", "")
}


class Omron:
    """Instrument class for Omron E5A(E)R-T based on CompoWay/F communication.

    Attributes:
        serial: pySerial serial port object, for setting data transfer parameters.
        setpoints: dict of available setpoints.
        programs: list of available programs, each program containing a list of segment
            dictionaries.
    """

    def __init__(
        self,
        port: str,
        clientaddress: int,
        channels: int = 2,
        decimals: int = 1,
        baudrate: int = 9600,
        parity: str = "even",
        bytesize: int = 7,
        stopbits: int = 2,
        timeout: float = 0.05,
        write_timeout: float = 2.0,
        **kwargs,
    ) -> None:
        """Connect to Omron and initialize communication settings.

        Serial connection settings including baudrate, parity, bytesize, stopbits,
        timeout, and write_timeout can be changed after initialization.

        Args:
            port: port name to connect to, e.g. `COM1`.
            clientaddress: integer address of Omron in the range of 1 to 99.
            channels: number of input channels. E5ER-T comes with 2 inputs, E5AR-T
                comes with 2 or 4 inputs.
            decimals: number of decimal places when reading and writing to Omron.
            baudrate: baud rate, one of 9600 (default), 19200, or 38400.
            parity: `even`, `odd`, or `none`.
            bytesize: number of data bits.
            stopbits: number of stopbits.
            timeout: read timeout in seconds.
            write_timeout: write timeout in seconds.

        Raises:
            ValueError: if Omron or serial parameters are out of range.
            SerialException: if device cannot be found or configured.
        """
        if clientaddress < 0 or clientaddress > 99:
            raise ValueError(
                f"Omron client address must be between 1 and 99, not {clientaddress}."
            )
        if channels not in (2, 4):
            raise ValueError(
                f"Number of Omron channels must be 2 or 4, not {channels}."
            )
        parity_dict = {
            "even": serial.PARITY_EVEN,
            "odd": serial.PARITY_ODD,
            "none": serial.PARITY_NONE,
        }
        self.serial = serial.Serial(
            port,
            baudrate,
            bytesize=bytesize,
            parity=parity_dict[parity],
            stopbits=stopbits,
            timeout=timeout,
        )

        self.decimals = decimals
        self.ch_1 = self.OmronChannel(self, 1)
        self.ch_2 = self.OmronChannel(self, 2)
        if channels == 4:
            self.ch_3 = self.OmronChannel(self, 3)
            self.ch_4 = self.OmronChannel(self, 4)

    def _bcc_calc(self, message: bytes) -> bytes:
        """Calculate block check character for an arbitrary message."""
        bcc = 0
        for byte in message:
            bcc = bcc ^ byte
        return bcc.to_bytes(1, byteorder="big")

    def _check_end_code(self, code: str) -> None:
        if code != "00":
            raise OmronException(f"End code {code}: {_OMRON_END_CODES[code]}")

    def _check_response_code(self, code: str) -> None:
        if code != "0000":
            raise OmronException(f"Response code {code}: {_OMRON_RESPONSE_CODES[code]}")

    def read_decimal(self, command: str, decimals: int) -> float:
        """Execute read command and convert to signed decimal."""
        data: int = int.from_bytes(self.read(command), byteorder="big", signed=True)
        return data / 10**decimals

    def write_decimal(self, command: str, val: float, decimals: int) -> None:
        """Convert val to unsigned int and execute write command."""
        if val < 0:
            pass
        int_val : int = abs(val * 10**decimals)

    def read(self, command: str) -> bytes:
        """Execute read command."""
        message: bytes = ("00000" + command + "\x03").encode()
        bcc: bytes = self._bcc_calc(message)
        message = b"".join(["b\x02", message, bcc])
        self.serial.write(message)
        response: bytes = self.serial.read_until(expected=b'\x03')
        bcc = self._bcc_calc(response[1:-1])
        if bcc != response[-1]:
            raise OmronException(
                f"Omron BCC error: expected {bcc} but received {response[-1]}."
            )
        end_code: str = response[5:7].decode()
        self._check_end_code(end_code)
        data = response[8:-1]
        return data

    def write(self, command: str) -> bytes:
        """Execute write command."""
        message: bytes = ("00000" + command + "\x03").encode()
        bcc: bytes = self._bcc_calc(message)
        message = b"".join(["b\x02", message, bcc])
        self.serial.write(message)
        response: bytes = self.serial.read_until(expected=b'\x03')
        bcc = self._bcc_calc(response[1:-1])
        if bcc != response[-1]:
            raise OmronException(
                f"Omron BCC error: expected {bcc} but received {response[-1]}."
            )
        end_code: str = response[5:7].decode()
        self._check_end_code(end_code)
        data = response[8:-1]

    class OmronChannel:
        """Individual Omron input channel."""

        def __init__(self, parent: Omron, channel_num: int) -> None:
            self.omron = parent
            self.offset = (channel_num - 1) * 16384

    #############
    # Home List #
    #############

    @property
    def process_value(self):
        """Process value."""
        response: bytes = self.read("0101C00000000001")
        response_code: bytes = response[4:8]

    @property
    def status(self):
        """Omron status."""
        response: bytes = self.read("0101C00001000001")
        response_code: bytes = response[4:8]

    @property
    def target_setpoint(self):
        """Target setpoint."""
        response: bytes = self.read("0101C00002000001")
        response_code: bytes = response[4:8]

    @property
    def output_level(self):
        """Power output in percent."""
        response: bytes = self.read("0101C00004000001")
        response_code: bytes = response[4:8]

    @property
    def operating_mode(self):
        """Auto/manual mode select."""
        auto_man_dict = {0: "auto", 1: "manual"}
        return auto_man_dict[self.read_register(273)]

    @operating_mode.setter
    def operating_mode(self, val: str):
        auto_man_dict = {"auto": 0, "manual": 1}
        self.write_register(273, auto_man_dict[val.casefold()])

    @property
    def working_setpoint(self):
        """Working set point. Read only."""
        return self.read_float(5)

    ############
    # Run List #
    ############

    @property
    def current_program(self):
        """Current program running (active program number)."""
        return self.read_register(22)

    @current_program.setter
    def current_program(self, val: int):
        if val < 0 or val > self._num_programs:
            log.warning("Eurotherm 2400 received invalid program number")
        else:
            self.write_register(22, val)

    @property
    def program_status(self):
        """Program status."""
        program_status_dict = {
            1: "reset",
            2: "run",
            4: "hold",
            8: "holdback",
            16: "complete",
        }
        return program_status_dict[self.read_register(23)]

    @program_status.setter
    def program_status(self, val: str):
        program_status_dict = {
            "reset": 1,
            "run": 2,
            "hold": 4,
            "holdback": 8,
            "complete": 16,
        }
        self.write_register(23, program_status_dict[val.casefold()])

    @property
    def programmer_setpoint(self):
        """Read only."""
        return self.read_float(163)

    @property
    def programmer_cycles(self):
        """Programmer cycles remaining. Read only."""
        return self.read_register(59)

    @property
    def current_segment_number(self):
        """Read only."""
        return self.read_register(56)

    @property
    def current_segment_type(self):
        """Read only."""
        return SEGMENT_TYPE[self.read_register(29)]

    @property
    def segment_time_remaining(self):
        """Read only. Segment time remaining in seconds."""
        return self.read_time(36)

    @property
    def segment_setpoint(self):
        """Read only."""
        return self.read_float(160)

    @property
    def ramp_rate(self):
        """Read only."""
        return self.read_float(161)

    @property
    def program_time_remaining(self):
        """Read only. Program time remaining in seconds."""
        return self.read_time(58)


class OmronException(Exception):
    """Base class for Omron-related errors."""
