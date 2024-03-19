"""Driver for Eurotherm 3200 series built on minimalmodbus.

Eurotherm 3200 series only supports RTU MODBUS mode. Transmission format:
    * 1 start bit
    * 8 data bits
    * NONE (default), ODD, or EVEN parity bit
    * 1 stop bit

CRC is automatically calculated by minimalmodbus.

Values can be accessed in two ways:
    * From the 'lower' registers as documented in the 2000 series communication
        manual. These registers transfer 16-bit integer representations of the data.
    * From the 'upper' registers, which are double the size of lower registers and
        transfer 32-bit values. This allows for full resolution floats and timer
        parameters to be accessed.

In this driver, upper registers are used for float and time parameters, lower registers
for word and int parameters. Time parameters are written and read in seconds regardless
of display settings such as dwell units.
"""

from __future__ import annotations
import logging
import minimalmodbus  # type: ignore

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


class Eurotherm3200(minimalmodbus.Instrument):
    """Instrument class for Eurotherm 3200 series process controller.

    The 3200 series has a program with 4 segments, each consisting of a target setpoint,
    ramp rate, and dwell time. Segment parameters are accessible by referencing the
    segment sub-class they are attached to, e.g. `segment1.ramp_rate`.

    Attributes:
        serial: pySerial serial port object, for setting data transfer parameters.
        segments: program segments, accessible as segment1, segment2, etc.
    """

    def __init__(
            self, port: str, clientaddress: int, baudrate: int = 9600,
            timeout: float = 1, **kwargs
    ) -> None:
        """Connect to Eurotherm.

        Args:
            port: port name to connect to, e.g. `COM1`.
            clientaddress: integer address of Eurotherm in the range of 1 to 254.
            baudrate: baud rate, one of 9600 (default), 19200, 4800, 2400, or 1200.
            timeout: timeout for communication in seconds.
        """
        super().__init__(port, clientaddress, **kwargs)
        self.serial.baudrate = baudrate
        self.serial.timeout = timeout
        self.segment1 = self.Segment(1, self)
        self.segment2 = self.Segment(2, self)
        self.segment3 = self.Segment(3, self)
        self.segment4 = self.Segment(4, self)

    # Float and long integer registers are double sized and offset
    def read_float(
            self,
            registeraddress: int,
            functioncode: int = 3,
            number_of_registers: int = 2,
            byteorder: int = 0
    ) -> float:
        """Convert to higher register to properly read floats."""
        return super().read_float(
            2 * registeraddress + 32768,
            functioncode,
            number_of_registers,
            byteorder
        )

    def write_float(
            self,
            registeraddress: int,
            val: float,
            functioncode: int = 3,
            number_of_registers: int = 2,
            byteorder: int = 0
    ) -> float:
        """Convert to higher register to properly write floats."""
        super().write_float(
            2 * registeraddress + 32768,
            val,
            functioncode,
            number_of_registers,
            byteorder
        )

    def read_time(self, register: int) -> float:
        """Read time parameters in seconds."""
        return super().read_long(2 * register + 32768) / 1000

    def write_time(self, register: int, val: float):
        """Write time parameters in seconds."""
        super().write_long(2 * register + 32768, round(val * 1000))

    @property
    def process_value(self) -> float:
        """Process variable."""
        return self.read_float(1)

    @property
    def target_setpoint(self) -> float:
        """Target setpoint (if in manual mode)."""
        return self.read_float(2)

    @target_setpoint.setter
    def target_setpoint(self, val: float):
        self.write_float(2, val)

    @property
    def output_level(self) -> float:
        """Power output in percent."""
        return self.read_float(3)

    @property
    def working_output(self) -> float:
        """Read-only if in auto mode."""
        return self.read_float(4)

    @working_output.setter
    def working_output(self, val: float):
        self.write_float(4, val)

    @property
    def working_setpoint(self) -> float:
        """Working set point. Read only."""
        return self.read_float(5)

    @property
    def active_setpoint(self) -> int:
        """1: SP1, 2: SP2."""
        return self.read_register(15)+1

    @active_setpoint.setter
    def active_setpoint(self, val: int):
        if val not in (1, 2):
            log.warning("Eurotherm3200 received invalid setpoint number")
        else:
            self.write_register(15, val-1)

    @property
    def program_status(self) -> str:
        """Program status."""
        program_status_dict = {0: 'reset',
                               1: 'run',
                               2: 'hold',
                               3: 'end'}
        return program_status_dict[self.read_register(23)]

    @program_status.setter
    def program_status(self, val: str):
        program_status_dict = {'reset': 0,
                               'run': 1,
                               'hold': 2,
                               'end': 3}
        self.write_register(23, program_status_dict[val.casefold()])

    @property
    def setpoint1(self) -> float:
        """Do not write continuously changing values to this variable."""
        return self.read_float(24)

    @setpoint1.setter
    def setpoint1(self, val: float):
        self.write_float(24, val)

    @property
    def setpoint2(self) -> float:
        """Do not write continuously changing values to this variable."""
        return self.read_float(24)

    @setpoint2.setter
    def setpoint2(self, val: float):
        self.write_float(25, val)

    @property
    def remote_setpoint(self) -> float:
        """Local/remote setpoint is selected with address 276."""
        return self.read_float(26)

    @remote_setpoint.setter
    def remote_setpoint(self, val: float):
        self.write_float(26, val)

    @property
    def setpoint_rate_limit(self) -> float:
        """0 = no rate limit."""
        return self.read_float(35)

    @setpoint_rate_limit.setter
    def setpoint_rate_limit(self, val: float):
        self.write_float(35, val)

    @property
    def calculated_error(self) -> float:
        """Error = PV - SP."""
        return self.read_float(39)

    @property
    def remote_setpoint_enabled(self) -> bool:
        """Select whether local or remote (comms) setpoint is selected.

        Remote setpoint is stored in address 26.
        """
        val = self.read_register(276)
        remote_dict = {0: False, 1: True}
        return remote_dict[val]

    @remote_setpoint_enabled.setter
    def remote_setpoint_enabled(self, val: bool):
        remote_dict = {False: 0, True: 1}
        self.write_register(276, remote_dict[val])

    @property
    def end_type(self) -> str:
        """Programmer end type."""
        end_type_dict = {0: 'off', 1: 'dwell', 2: 'sp2', 3: 'reset'}
        val = self.read_register(328)
        return end_type_dict[val]

    @end_type.setter
    def end_type(self, val: str):
        end_type_dict = {'off': 0, 'dwell': 1, 'sp2': 2, 'reset': 3}
        self.write_register(328, end_type_dict[val.casefold()])

    @property
    def program_cycles(self) -> int:
        """Number of program cycles to run."""
        return self.read_register(332)

    @program_cycles.setter
    def program_cycles(self, val: int):
        self.write_register(332, val)

    @property
    def current_program_cycle(self) -> int:
        """Current program cycle number."""
        return self.read_register(333)

    @property
    def ramp_units(self) -> str:
        """Degrees per `mins`, `hours`, or `secs`."""
        ramp_dict = {0: 'mins', 1: 'hours', 2: 'secs'}
        val = self.read_register(531)
        return ramp_dict[val]

    @ramp_units.setter
    def ramp_units(self, val: str):
        ramp_dict = {'mins': 0, 'hours': 1, 'secs': 2}
        self.write_register(531, ramp_dict[val.casefold()])

    class Segment:
        """A class for each (target, ramp rate, dwell time) segment."""

        def __init__(self, segment_num: int, eurotherm: Eurotherm3200) -> None:
            """Create segment list and read current values.

            Args:
                segment_num: segment number, from 1 to 4.
                eurotherm: Eurotherm3200 instance. Provides read/write access.
            """
            self.offset: int = (segment_num - 1) * 3
            self.eurotherm: Eurotherm3200 = eurotherm

        @property
        def dwell(self) -> float:
            """Segment dwell duration in seconds."""
            return self.eurotherm.read_time(1280 + self.offset)

        @dwell.setter
        def dwell(self, val: float):
            self.eurotherm.write_time(1280 + self.offset, val)

        @property
        def target_setpoint(self) -> float:
            """Segment target setpoint."""
            return self.eurotherm.read_float(1281 + self.offset)

        @target_setpoint.setter
        def target_setpoint(self, val: float):
            self.eurotherm.write_float(1281 + self.offset, val)

        @property
        def ramp_rate(self) -> float:
            """Segment ramp rate."""
            return self.eurotherm.read_float(1282 + self.offset)

        @ramp_rate.setter
        def ramp_rate(self, val: float):
            self.eurotherm.write_float(1282 + self.offset, val)
