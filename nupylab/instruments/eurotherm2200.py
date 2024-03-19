"""Driver for Eurotherm 2200 series built on minimalmodbus.

Eurotherm 2200 series only supports RTU MODBUS mode. Transmission format:
    * 1 start bit
    * 8 data bits
    * NONE (default), ODD, or EVEN parity bit
    * 1 stop bit

CRC is automatically calculated by minimalmodbus.

Values can be accessed in two ways:
    * From the 'lower' registers as documented in the 2200 series communication
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


class Eurotherm2200(minimalmodbus.Instrument):
    """Instrument class for Eurotherm 2200 series process controller.

    Attributes:
        serial: pySerial serial port object, for setting data transfer parameters.
        setpoints: dict of available setpoints.
        programs: list of available programs, each program containing a list of segment
            dictionaries.
    """

    def __init__(self,
                 port: str,
                 clientaddress: int,
                 baudrate: int = 9600,
                 timeout: float = 1,
                 **kwargs) -> None:
        """Connect to Eurotherm and initialize program and setpoint list.

        Args:
            port: port name to connect to, e.g. `COM1`.
            clientaddress: integer address of Eurotherm in the range of 1 to 254.
            baudrate: baud rate, one of 9600 (default), 19200, 4800, 2400, or 1200.
            timeout: timeout for communication in seconds.
        """
        super().__init__(port, clientaddress)
        self.serial.baudrate = baudrate
        self.serial.timeout = timeout

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
        return float(super().read_long(2 * register + 32768) / 1000)

    def write_time(self, register: int, val: float):
        """Write time parameters in seconds."""
        super().write_long(2 * register + 32768, round(val * 1000))

    def _initialize_setpoints(self) -> None:
        self._num_setpoints = 2
        self.setpoints = self.Setpoints(self)

    #############
    # Home List #
    #############

    @property
    def process_value(self):
        """Process variable."""
        return self.read_float(1)

    @property
    def output_level(self):
        """Power output in percent."""
        return self.read_float(3)

    @property
    def target_setpoint(self):
        """Target setpoint (if in manual mode)."""
        return self.read_float(2)

    @target_setpoint.setter
    def target_setpoint(self, val: float):
        self.write_float(2, val)

    @property
    def operating_mode(self):
        """Auto/manual mode select."""
        auto_man_dict = {0: "auto",
                         1: "manual"}
        return auto_man_dict[self.read_register(273)]

    @operating_mode.setter
    def operating_mode(self, val: str):
        auto_man_dict = {"auto": 0,
                         "manual": 1}
        self.write_register(273, auto_man_dict[val.casefold()])

    @property
    def working_setpoint(self):
        """Working set point. Read only."""
        return self.read_float(5)

    #################
    # Setpoint List #
    #################

    @property
    def active_setpoint(self):
        """1: SP1, 2: SP2"""
        return self.read_register(15)+1

    @active_setpoint.setter
    def active_setpoint(self, val: int):
        if val < 1 or val > self._num_setpoints:
            log.warning("Eurotherm2200 received invalid setpoint number")
        else:
            self.write_register(15, val-1)

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
    def setpoint_rate_limit(self):
        """Ramp rate limit."""
        return self.read_float(35)

    @setpoint_rate_limit.setter
    def setpoint_rate_limit(self, val):
        """Ramp rate limit."""
        self.write_float(35, val)

    @property
    def dwell_time(self):
        """Dwell time after ramping from SP1 to SP2."""
        return self.read_time(62)

    @dwell_time.setter
    def dwell_time(self, val: float):
        self.write_time(62, val)

    @property
    def end_type(self):
        """Go to state at end of program."""
        end_dict = {0: 'dwell',
                    1: 'reset',
                    2: 'hold',
                    3: 'standby'}
        return end_dict[self.read_register(517)]

    @end_type.setter
    def end_type(self, val: str):
        end_dict = {'dwell': 0,
                    'reset': 1,
                    'hold': 2,
                    'standby': 3}
        self.write_register(517, end_dict[val.casefold()])

    @property
    def program_status(self):
        """Program status. Writable values are`reset` or `run`."""
        program_status_dict = {1: "off",
                               2: "run",
                               4: "hold",
                               16: "end",
                               32: "dwell",
                               64: "ramp"}
        return program_status_dict[self.read_register(23)]

    @program_status.setter
    def program_status(self, val: str):
        program_status_dict = {"reset": 1,
                               "run": 2}
        self.write_register(57, program_status_dict[val.casefold()])
