"""Driver for Omron E5AR, E5ER, E5AR-T, and E5ER-T based on CompoWay/F protocol.

CompoWay/F communication:
    * 1 start bit
    * 7 (default) or 8 data bits
    * 2 (default) or 1 stop bits
    * EVEN (default), ODD, or NONE parity bit
    * baud rate 9600 (default), 19200, 38400
    * BCC calculated by this script

Written and read values are expressed in string hexadecimal format, and the decimal
point is disregarded. The number of decimal places must be known beforehand. Negative
values are expressed as a two's complement.

Example:
    * to write 100.5 -> 1005 -> "3ed"
    * to write -100.5 -> -1005 -> "fffffc13"
"""

from __future__ import annotations
from typing import Any, Dict, Union, Tuple, List

import serial  # type: ignore

_OMRON_END_CODES = {
    "0F": "Could not execute the specified FINS command.",
    "10": "Parity error.",
    "11": "Framing error.",
    "12": "Attempted to transfer new data when reception data buffer is already full.",
    "13": "BCC error.",
    "14": "Format error.",
    "16": "Sub-address error.",
    "18": "Frame length error.",
}

_OMRON_RESPONSE_CODES = {
    "1001": "Command length too long.",
    "1002": "Command length too short.",
    "1003": "The specified number of elements does not agree with the actual number of "
    "data elements.",
    "1101": "Incorrect variable type.",
    "110B": "Number of elements is greater than 25.",
    "1100": "Parameter error.",
    "2203": "Operation error.",
}

_OMRON_STATUS: Dict[int, Union[str, Tuple[str, str]]] = {
    2**3: "Remote setpoint input error",
    2**4: "Potentiometer error",
    2**5: "Display range exceeded",
    2**6: "Input error",
    2**8: "Control output (heating)",
    2**9: "Control output (cooling)",
    2**12: "Alarm 1",
    2**13: "Alarm 2",
    2**14: "Alarm 3",
    2**15: "Alarm 4",
    2**20: ("Write mode: backup", "Write mode: RAM write"),
    2**21: ("RAM = EEPROM", "RAM != EEPROM"),
    2**22: ("Setting area 0", "Setting area 1"),
    2**23: "Autotune in progress",
    2**24: ("Run", "Reset"),
    2**25: ("Communications writing OFF", "Communications writing ON"),
    2**26: ("Auto", "Manual"),
    2**27: ("Local setpoint", "Remote setpoint"),
    2**28: "MV tracking",
    2**29: "Fixed setpoint",
    2**30: ("Heating output: voltage", "Heating output: linear current"),
    2**31: ("Cooling output: voltage", "Cooling output: linear current"),
}

_OMRON_PROGRAM_STATUS: Dict[int, Union[str, Tuple[str, str]]] = {
    2**0: "Segment output 1 / Time signal 1",
    2**1: "Segment output 2 / Time signal 2",
    2**2: "Segment output 3 / Time signal 3",
    2**3: "Segment output 4 / Time signal 4",
    2**4: "Segment output 5 / Time signal 5",
    2**5: "Segment output 6 / Time signal 6",
    2**6: "Segment output 7",
    2**7: "Segment output 8",
    2**8: "Segment output 9",
    2**9: "Segment output 10",
    2**16: "hold",
    2**17: "wait",
    2**21: ("soak", "ramp"),
    2**22: "end",
    2**23: "standby",
}


class OmronChannel:
    """Individual input channel for Omron E5AR and E5ER.

    Attributes:
        omron: Omron parent class.
    """

    def __init__(self, parent: OmronE5, channel_num: int) -> None:
        """Create Omron input channel.

        Args:
            parent: Omron class instance channel belongs to.
            channel_num: channel number, 1 to 4.
        """
        self.omron: OmronE5 = parent
        self._offset: str = str(channel_num - 1)
        input_val: int = self.input_type[0]
        if input_val == 0 or input_val in range(2, 15):
            self._decimals = 1
        elif input_val == 1:
            self._decimals = 2
        else:
            self._decimals = self.analog_decimals

    def _parse_status(self, val: int, status_dict: dict) -> List[str]:
        status: List[str] = []
        for k, v in status_dict.items():
            if isinstance(v, tuple):
                if k & val == k:
                    status.append(v[1])
                else:
                    status.append(v[0])
            else:
                if k & val == k:
                    status.append(v)
        return status

    # Command format:
    #   * MRC                   2 bytes
    #   * SRC                   2 bytes
    #   * Variable type         2 bytes
    #   * Read start address    4 bytes
    #   * Bit position          2 bytes = "00"
    #   * Number of elements    4 bytes
    #   * Write data

    @property
    def present_value(self) -> float:
        """Read present value."""
        return self.omron.read_decimal(f"0101C00{self._offset}00000001", self._decimals)

    @property
    def status(self) -> List[str]:
        """Read Omron status."""
        response: int = self.omron.read_int(f"0101C00{self._offset}01000001")
        return self._parse_status(response, _OMRON_STATUS)

    @property
    def internal_setpoint(self) -> float:
        """Read internal setpoint."""
        return self.omron.read_decimal(f"0101C00{self._offset}02000001", self._decimals)

    @property
    def output_power(self) -> float:
        """Control power output in percent. Negative values indicate cooling.

        Valid range is
            -5.0 to 105.0 for standard ouput
            -105.0 to 105.0 for heat/cool output
            -10.0 to 110.0 for position proportional output
        Settable only if :attr:`operating_mode` is set to `manual`.
        """
        return self.omron.read_decimal(f"0101C60{self._offset}00000001")

    @output_power.setter
    def output_power(self, val: float) -> None:
        self.omron.write_decimal(f"0102C60{self._offset}00000001", val)

    @property
    def present_setpoint(self) -> float:
        """Read present setpoint."""
        return self.omron.read_decimal(f"0101C10{self._offset}03000001", self._decimals)

    @property
    def alarm_1_setpoint_1(self) -> float:
        """Control alarm set 1, value 1."""
        return self.omron.read_decimal(f"0101C10{self._offset}04000001", self._decimals)

    @alarm_1_setpoint_1.setter
    def alarm_1_setpoint_1(self, val: float) -> None:
        self.omron.write_decimal(f"0102C90{self._offset}02000001", val, self._decimals)

    @property
    def alarm_1_limits_1(self) -> Tuple[float, float]:
        """Control alarm set 1, lower and upper limits 1.

        Returns and sets as a tuple of (lower_limit, upper_limit).
        """
        upper_limit = self.omron.read_decimal(
            f"0101C90{self._offset}03000001", self._decimals
        )
        lower_limit = self.omron.read_decimal(
            f"0101C90{self._offset}04000001", self._decimals
        )
        return (lower_limit, upper_limit)

    @alarm_1_limits_1.setter
    def alarm_1_limits_1(self, limits: Tuple[float, float]) -> None:
        self.omron.write_decimal(
            f"0102C90{self._offset}04000001", limits[0], self._decimals
        )
        self.omron.write_decimal(
            f"0102C90{self._offset}03000001", limits[1], self._decimals
        )

    @property
    def alarm_1_setpoint_2(self) -> float:
        """Control alarm set 1, value 2."""
        return self.omron.read_decimal(f"0101C10{self._offset}07000001", self._decimals)

    @alarm_1_setpoint_2.setter
    def alarm_1_setpoint_2(self, val: float) -> None:
        self.omron.write_decimal(f"0102C90{self._offset}05000001", val, self._decimals)

    @property
    def alarm_1_limits_2(self) -> Tuple[float, float]:
        """Control alarm set 1, lower and upper limits 2.

        Returns and sets as a tuple of (lower_limit, upper_limit).
        """
        upper_limit = self.omron.read_decimal(
            f"0101C90{self._offset}06000001", self._decimals
        )
        lower_limit = self.omron.read_decimal(
            f"0101C90{self._offset}07000001", self._decimals
        )
        return (lower_limit, upper_limit)

    @alarm_1_limits_2.setter
    def alarm_1_limits_2(self, limits: Tuple[float, float]) -> None:
        self.omron.write_decimal(
            f"0102C90{self._offset}07000001", limits[0], self._decimals
        )
        self.omron.write_decimal(
            f"0102C90{self._offset}06000001", limits[1], self._decimals
        )

    @property
    def pid_number(self) -> int:
        """Control PID set number. Valid range is 1 to 8, or 0 (automatic)."""
        return self.omron.read_int(f"0101C40{self._offset}05000001")

    @pid_number.setter
    def pid_number(self, val: int):
        self.omron.write_int(f"0102C90{self._offset}01000001", val)

    @property
    def autotune_status(self) -> str:
        """Control PID autotune status.

        Valid options are `run` or `stop`. Applies to current PID number only.
        """
        status: int = self.omron.read_int(f"0101C00{self._offset}01000001")
        if status & 8388608 == 0:
            return "stop"
        return "run"

    @autotune_status.setter
    def autotune_status(self, status: str) -> None:
        if status.casefold() == "run":
            self.omron.write(f"300503{self._offset}0")
        if status.casefold() == "stop":
            self.omron.write(f"30050A{self._offset}0")

    @property
    def setpoint_mode(self) -> str:
        """Control setpoint mode. Must be `local` or `remote`."""
        status: int = self.omron.read_int(f"0101C00{self._offset}01000001")
        if status & 134217728 == 134217728:
            return "remote"
        return "local"

    @setpoint_mode.setter
    def setpoint_mode(self, mode: str) -> None:
        mode = mode.casefold()
        if mode not in ("local", "remote", "fixed"):
            raise OmronException(
                f"Setpoint mode must be `local` or `remote`, not `{mode}`."
            )
        mode_map = {"local": "0", "remote": "1"}
        self.omron.write(f"30050D{self._offset}{mode_map[mode]}")

    @property
    def operating_mode(self) -> str:
        """Control auto/manual mode. Must be `auto` or `manual`."""
        status: int = self.omron.read_int(f"0101C00{self._offset}01000001")
        if status & 67108864 == 67108864:
            return "manual"
        return "auto"

    @operating_mode.setter
    def operating_mode(self, mode: str) -> None:
        mode_map = {"auto": "0", "manual": "1"}
        self.omron.write(f"300509{self._offset}{mode_map[mode.casefold()]}")

    @property
    def input_type(self) -> Tuple[int, str]:
        """Control input type. Can only set from Setting Area 1.

        Getting this property returns tuple with integer value and string input type.
        Temperature input type can be set with either integer or string.
        Analog imput type must be set with integer.
        Input type (temperature or analog) must agree with input type switch.
        """
        offset: str = str(int(self._offset) * 2)
        val: int = int(self.omron.read(f"0101CC000{offset}000001").decode(), base=16)
        input_map = {
            0: "Pt100",
            1: "Pt100",
            2: "K",
            3: "K",
            4: "J",
            5: "J",
            6: "T",
            7: "E",
            8: "L",
            9: "U",
            10: "N",
            11: "R",
            12: "S",
            13: "B",
            14: "W",
            15: "4 to 20 mA",
            16: "0 to 20 mA",
            17: "1 to 5 V",
            18: "0 to 5 V",
            19: "0 to 10 V",
        }
        return (val, input_map[val])

    @input_type.setter
    def input_type(self, val: Union[int, str]) -> None:
        offset: str = str(int(self._offset) * 2)
        if isinstance(val, str):
            input_map = {
                "pt100": 0,
                "k": 2,
                "j": 4,
                "t": 6,
                "e": 7,
                "l": 8,
                "u": 9,
                "n": 10,
                "r": 11,
                "s": 12,
                "b": 13,
                "w": 14,
            }
            val = input_map[val.casefold()]
        self.omron.write_int(f"0102CC000{offset}000001", val)

    @property
    def analog_decimals(self) -> int:
        """Control number of decimal places displayed, if input type is analog.

        Valid range is 0 to 4.
        """
        return self.omron.read_int(f"0101CC0{self._offset}0C000001")

    @analog_decimals.setter
    def analog_decimals(self, val: int) -> None:
        self.omron.write_int(f"0102CC0{self._offset}0C000001", val)


class OmronChannelE5T(OmronChannel):
    """Individual input channel for Omron E5AR-T and E5ER-T.

    Attributes:
        omron: Omron parent class.
    """

    def __init__(self, parent: OmronE5T, channel_num: int) -> None:
        """Create Omron input channel.

        Args:
            parent: Omron class instance channel belongs to.
            channel_num: channel number, 1 to 4.
        """
        super().__init__(parent, channel_num)
        self.omron: OmronE5T = parent
        self.program = self.Program(self, channel_num)

    @property
    def setpoint_mode(self) -> str:
        """Control setpoint mode. Must be `program`, `remote`, or `fixed`."""
        status: int = self.omron.read_int(f"0101C00{self._offset}01000001")
        if status & 134217728 == 134217728:
            return "remote"
        if status & 268435456 == 268435456:
            return "fixed"
        return "program"

    @setpoint_mode.setter
    def setpoint_mode(self, mode: str) -> None:
        mode = mode.casefold()
        if mode not in ("program", "remote", "fixed"):
            raise OmronException(
                f"Setpoint mode must be `program`, `remote`, or `fixed`, not `{mode}`."
            )
        mode_map = {"program": "0", "remote": "1", "fixed": "2"}
        self.omron.write(f"30050D{self._offset}{mode_map[mode]}")

    @property
    def program_status(self) -> List[str]:
        """Control program status.

        Getting this property returns list of program status conditions.
        Valid set values are `run` or `reset`.
        """
        response: int = self.omron.read_int(f"0101C40{self._offset}07000001")
        return self._parse_status(response, _OMRON_PROGRAM_STATUS)

    @program_status.setter
    def program_status(self, val: str) -> None:
        status_dict = {"run": "0", "reset": "1"}
        self.omron.write(f"300501{self._offset}{status_dict[val.casefold()]}")

    @property
    def fixed_setpoint(self) -> float:
        """Control fixed setpoint."""
        return self.omron.read_decimal(f"0101C70{self._offset}23000001", self._decimals)

    @fixed_setpoint.setter
    def fixed_setpoint(self, val: float) -> None:
        self.omron.write_decimal(f"0102C70{self._offset}23000001", val, self._decimals)

    @property
    def program_num(self) -> int:
        """Control active program number. Valid range is 1 to 32."""
        self._program_num = self.omron.read_int(f"0101C60{self._offset}08000001")
        return self._program_num

    @program_num.setter
    def program_num(self, val: int) -> None:
        self.omron.write_int(f"0102C60{self._offset}08000001", val)
        self._program_num = val

    @property
    def pid_number(self) -> int:
        """Control PID set number. Valid range is 1 to 8, or 0 (automatic)."""
        return self.omron.read_int(f"0101C40{self._offset}05000001")

    @pid_number.setter
    def pid_number(self, val: int):
        self.omron.write_int(f"0102D80{self._offset}03000001", val)

    @property
    def num_segments(self) -> int:
        """Control number of segments in programs.

        Valid options are 8, 12, 16, 20, and 32. Default is 16.
        The maximum number of programs than can be set depends on the number of
        segments.
        8 segments: 32 programs
        12 segments: 20 programs
        16 segments: 16 programs
        20 segments: 12 programs
        32 segments: 8 programs
        """
        return self.omron.read_int(f"0101CD0{self._offset}15000001")

    @num_segments.setter
    def num_segments(self, val: int) -> None:
        self.omron.write_int(f"0102CD0{self._offset}15000001", val)

    class Program:
        """Program class for each Omron input channel.

        Only supports 8 segments regardless of channel setting.

        Attributes:
            omron: Omron parent class of channel.
            segments_used: number of segments used.
        """

        def __init__(self, channel: OmronChannelE5T, channel_num: int):
            """Create program class.

            Args:
                channel: Omron Program Channel class instance.
                channel_num: Omron channel number.
            """
            self.channel = channel
            self.channel_num: int = channel_num
            self.segments = []
            for s in range(1024, 9 * 1024, 1024):
                address: str = hex(s + (channel_num - 1) * 256)[2:]
                address = address.zfill(4)[:2].upper()
                self.segments.append(
                    self.Segment(self, address)
                )

        @property
        def var(self) -> str:
            """Get variable area for current program number. Read only."""
            return hex(217 + self.channel._program_num)[2:].upper()

        @property
        def segments_used(self) -> int:
            """Control number of segments used. Valid range is 1 to 8."""
            return self.channel.omron.read_int(f"0101{self.var}0000000001")

        @segments_used.setter
        def segments_used(self, val: int) -> None:
            if val > 8:
                raise OmronException("Setting more than 8 segments is not supported "
                                     "by Omron driver.")
            self.channel.omron.write_int(f"0102{self.var}0000000001", val)

        class Segment:
            """Segment class for program."""

            def __init__(
                self,
                program: OmronChannelE5T.Program,
                address: str,
            ) -> None:
                """Create program segment.

                Args:
                    program: OmronChannelE5T program instance.
                    address: first two characters of segment address.
                """
                self.program: OmronChannelE5T.Program = program
                self.address = address
                self.omron: OmronE5T = program.channel.omron
                self.decimals: int = program.channel._decimals

            @property
            def setpoint(self) -> float:
                """Control segment setpoint."""
                var = self.program.var
                return self.omron.read_decimal(
                    f"0101{var}{self.address}00000001", self.decimals
                )

            @setpoint.setter
            def setpoint(self, val: float) -> None:
                var = self.program.var
                self.omron.write_decimal(
                    f"0102{var}{self.address}00000001", val, self.decimals
                )

            @property
            def ramp_rate(self) -> float:
                """Control segment ramp rate."""
                var = self.program.var
                return self.omron.read_decimal(
                    f"0101{var}{self.address}01000001", self.decimals
                )

            @ramp_rate.setter
            def ramp_rate(self, val: float) -> None:
                var = self.program.var
                self.omron.write_decimal(
                    f"0102{var}{self.address}01000001", val, self.decimals
                )

            @property
            def time(self) -> float:
                """Control segment time. Formatted as decimal, e.g. `99.59`."""
                var = self.program.var
                return self.omron.read_decimal(
                    f"0101{var}{self.address}02000001", self.decimals
                )

            @time.setter
            def time(self, val: float) -> None:
                var = self.program.var
                self.omron.write_decimal(
                    f"0102{var}{self.address}02000001", val, self.decimals
                )


class OmronE5:
    """Instrument class for Omron E5A(E)R-T based on CompoWay/F communication.

    Attributes:
        serial: pySerial serial port object, for setting data transfer parameters.
        setpoints: dict of available setpoints.
    """

    def __init__(
        self,
        port: str,
        clientaddress: int,
        channels: int = 2,
        baudrate: int = 9600,
        parity: str = "even",
        bytesize: int = 7,
        stopbits: int = 2,
        timeout: float = 0.05,
        write_timeout: float = 2.0
    ) -> None:
        """Initialize communication settings and connect to Omron.

        Serial connection settings including baudrate, parity, bytesize, stopbits,
        timeout, and write_timeout can be changed after initialization.

        Args:
            port: port name to connect to, e.g. `COM1`.
            clientaddress: integer address of Omron in the range of 1 to 99.
            channels: number of input channels. E5ER-T comes with 2 inputs, E5AR-T
                comes with 2 or 4 inputs.
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
            write_timeout=write_timeout
        )
        self._address: str = str(clientaddress).zfill(2)
        self._set_channels(channels)
        self.default_channel: int = 1
        self.write("30050001")  # Enable writing to instrument
        self.write("0102CD001700000100000000")  # Set programming mode to time

    def _set_channels(self, channels: int) -> None:
        self.ch_1: OmronChannel = OmronChannel(self, 1)
        self.ch_2: OmronChannel = OmronChannel(self, 2)
        if channels == 4:
            self.ch_3: OmronChannel = OmronChannel(self, 3)
            self.ch_4: OmronChannel = OmronChannel(self, 4)

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

    def read_int(self, command: str, signed: bool = False) -> int:
        """Read command and convert to signed integer.

        Args:
            command: string command to send to Omron.
            signed: whether integer response should be interpreted as signed.

        Returns:
            Omron response converted to integer.
        """
        return int.from_bytes(self.read(command), byteorder="big", signed=signed)

    def write_int(self, command: str, val: int) -> None:
        """Convert val to two's complement int, then 8-wide hex, and write command.

        Args:
            command: string command to send to Omron.
            val: integer value to write.
        """
        hex_str: str = hex((val + (1 << 32)) % (1 << 32))[2:].zfill(8)  # 8 chars
        self.write("".join((command, hex_str)))

    def read_decimal(self, command: str, decimals: int = 1) -> float:
        """Write command and convert response to signed decimal.

        Args:
            command: string command to send to Omron.
            decimals: number of decimal places in response.

        Returns:
            Response from Omron converted to a float.
        """
        data: int = int.from_bytes(self.read(command), byteorder="big", signed=True)
        return data / 10**decimals

    def write_decimal(self, command: str, val: float, decimals: int = 1) -> None:
        """Write command with decimal value.

        Convert value to two's complement int, then 8-wide hex and write command.

        Args:
            command: string command to send to Omron.
            val: float value to send to Omron.
            decimals: number of decimal places in response.
        """
        int_val: int = round(val * 10**decimals)
        hex_str: str = hex((int_val + (1 << 32)) % (1 << 32))[2:].zfill(8)  # 8 chars
        self.write("".join((command, hex_str)))

    def read(self, command: str) -> bytes:
        """Execute read command.

        Args:
            command: string command to send to Omron.

        Returns:
            Reponse from Omron as bytes object.
        """
        self._write(command)
        return self._read()

    def write(self, command: str) -> None:
        """Execute write command.

        Args:
            command: string command to send to Omron.
        """
        self._write(command)
        self._read()

    def _write(self, command: str) -> None:
        message: bytes = (self._address + "000" + command + "\x03").encode("utf-8")
        bcc: bytes = self._bcc_calc(message)
        message = b"".join((b"\x02", message, bcc))
        self.serial.write(message)

    def _read(self) -> bytes:
        """Read Omron response to written command."""
        # Minimum response length is 16 bytes
        #   * STX                   1 byte = "\x02"
        #   * Node number           2 bytes
        #   * Sub-address           2 bytes = "00"
        #   * End code              2 bytes
        #   * MRC                   2 bytes
        #   * SRC                   2 bytes
        #   * Response code         4 bytes
        #   * Read data
        #   * ETX                   1 byte = "\x03"

        response: bytes = self.serial.read_until(expected=b"\x03")
        bcc: bytes = self.serial.read(size=1)
        bcc_calc: bytes = self._bcc_calc(response[1:])
        if bcc != bcc_calc:
            raise OmronException(
                f"Omron BCC error: expected {bcc_calc!r} but received {bcc!r}."
            )
        end_code: str = response[5:7].decode()
        self._check_end_code(end_code)
        # Bytes 7 - 10 are just MRC and SRC and can be ignored
        response_code: str = response[11:15].decode()
        self._check_response_code(response_code)
        return response[15:-1]  # Returns empty bytes if no data

    @property
    def version(self) -> str:
        """Get Omron software version string."""
        version: str = self.read("0101C40000000001").decode()
        index = 0
        while version[index] == "0":
            index += 1
        return "".join((version[index], ".", version[(index + 1) :]))

    @property
    def writing_enabled(self) -> bool:
        """Control whether communications writing is enabled. True or False."""
        status: int = self.read_int("0101C00001000001")
        if status & 33554432 == 33554432:
            return True
        return False

    @writing_enabled.setter
    def writing_enabled(self, condition: bool) -> None:
        bool_map = {False: "30050000", True: "30050001"}
        self.write(bool_map[condition])

    def reset_software(self) -> None:
        """Reset software, equivalent to turning power OFF and ON."""
        self.write("30050600")

    def save_ram(self) -> None:
        """Write set values to EEPROM.

        Written set values include:
            * Operation Level
            * Program Setting Level
            * Adjustment Level
            * Adjustment 2 Level
            * Alarm Set Setting Level
            * PID Setting Level
            * Time Signal Setting Level
            * Approximation Setting Level.
        """
        self.write("30050500")

    def configure(self) -> None:
        """Move to Setting Area 1 and stops operation.

        Allows writing to Omron configurational settings. To return to Setting Area 0,
        use :meth:`reset_software`, or turn power OFF and ON.
        """
        self.write("30050700")

    def echoback(self, data: str) -> str:
        """Perform an echoback test.

        Args:
            data: string to send to Omron, maximum length 200.
        Returns:
            string of echoed data.
        """
        return self.read(f"0801{data}").decode()

    def __getattr__(self, name: str) -> Any:
        """Get attributes from default channel if not explicitly identified."""
        channel = self.__dict__[f"ch_{self.default_channel}"]
        if not hasattr(channel, name):
            raise AttributeError(f"Omron has no attribute `{name}`.")
        return getattr(channel, name)


class OmronE5T(OmronE5):
    """Instrument class for Omron E5A(E)R-T based on CompoWay/F communication.

    Attributes:
        serial: pySerial serial port object, for setting data transfer parameters.
        setpoints: dict of available setpoints.
        programs: list of available programs, each program containing a list of segment
            dictionaries.
    """

    def _set_channels(self, channels: int) -> None:
        self.ch_1: OmronChannelE5T = OmronChannelE5T(self, 1)
        self.ch_2: OmronChannelE5T = OmronChannelE5T(self, 2)
        if channels == 4:
            self.ch_3: OmronChannelE5T = OmronChannelE5T(self, 3)
            self.ch_4: OmronChannelE5T = OmronChannelE5T(self, 4)

    @property
    def time_units(self) -> str:
        """Control progammer time units. Can only set from Setting Area 1.

        Applies to soak time and ramp time for step time programming, applies only to
        soak time for ramp rate programming.

        Valid options are `hhmm` (hour, minute), `mmss` (minute, second), or `mmssd`
        (minute, second, desisecond). Default is `hhmm`.
        """
        time_map = {0: "hhmm", 1: "mmss", 2: "mmssd"}
        return time_map[self.read_int("0101CD0016000001")]

    @time_units.setter
    def time_units(self, val: str) -> None:
        time_map = {"hhmm": 0, "mmss": 1, "mmssd": 2}
        self.write_int("0102CD0016000001", time_map[val.casefold()])

    @property
    def ramp_mode(self) -> str:
        """Control program ramp mode. Can only set from Setting Area 1.

        Valid options are `time` or `rate`. Default is `time`.
        If set to `time`, :attr:`time_units` applies to both soak time and ramp time.
        If set to `rate`, :attr:`time_units` applies only to soak time, and
        :attr:`ramp_units` applies to ramp rate.
        """
        mode_map = {0: "time", 1: "rate"}
        return mode_map[self.read_int("0101CD0017000001")]

    @ramp_mode.setter
    def ramp_mode(self, val: str) -> None:
        mode_map = {"time": 0, "rate": 1}
        self.write_int("0102CD0017000001", mode_map[val.casefold()])

    @property
    def ramp_units(self) -> str:
        """Control ramp rate units. Only applies if :attr:`ramp_mode` is set to `rate`.

        Valid options are `10h` (10 hours), `hours`, `mins`, and `secs`.
        Default is `mins`. Can only be set from Setting Area 1.
        """
        units_map = {0: "10h", 1: "hours", 2: "mins", 3: "secs"}
        return units_map[self.read_int("0101CD0018000001")]

    @ramp_units.setter
    def ramp_units(self, val: str) -> None:
        units_map = {"10h": 0, "hours": 1, "mins": 2, "secs": 3}
        self.write_int("0102CD0018000001", units_map[val.casefold()])


class OmronException(Exception):
    """Base class for Omron-related errors."""
