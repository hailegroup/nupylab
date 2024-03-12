"""Driver for Eurotherm 2000 series built on minimalmodbus.

Eurotherm 2000 series only supports RTU MODBUS mode. Transmission format:
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
from typing import Any, Optional

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


class Eurotherm2000(minimalmodbus.Instrument):
    """Instrument class for Eurotherm 2000 series process controller.

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
        self._initialize_setpoints()
        self._initialize_programs()

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
        self._num_setpoints = self.read_register(521) + 1  # Register val is 1 - n_SP
        self.setpoints = self.Setpoints(self)

    def _initialize_programs(self) -> None:
        self._num_programs = self.read_register(517)
        num_segments = self.read_register(211)
        self.programs = []
        for p in range(self._num_programs+1):
            self.programs.append(self.Program(p, num_segments, self))

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
            log.warning("Eurotherm 2000 received invalid program number")
        else:
            self.write_register(22, val)

    @property
    def program_status(self):
        """Program status."""
        program_status_dict = {1: "reset",
                               2: "run",
                               4: "hold",
                               8: "holdback",
                               16: "complete"}
        return program_status_dict[self.read_register(23)]

    @program_status.setter
    def program_status(self, val: str):
        program_status_dict = {"reset": 1,
                               "run": 2,
                               "hold": 4,
                               "holdback": 8,
                               "complete": 16}
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

    #################
    # Setpoint List #
    #################

    @property
    def active_setpoint(self):
        """1: SP1, 2: SP2, 3: SP3, etc."""
        return self.read_register(15)+1

    @active_setpoint.setter
    def active_setpoint(self, val: int):
        if val < 1 or val > self._num_setpoints:
            log.warning("Eurotherm2000 received invalid setpoint number")
        else:
            self.write_register(15, val-1)

    class Setpoints(dict):
        """Setpoints dictionary containing entries for valid setpoints in Eurotherm.

        Attributes:
            eurotherm: Eurotherm2000 instance, for reading and writing registers
        """

        def __init__(self, eurotherm: Eurotherm2000):
            """Create empty dictionary with access to eurotherm methods.

            Args:
                eurotherm: Eurotherm2000 instance
            """
            super().__init__()
            self.eurotherm = eurotherm
            self.update(
                {key: None for key in range(1, self.eurotherm._num_setpoints+1)}
            )

        def __getitem__(self, key: int) -> Optional[float]:
            """Read appropriate register."""
            if key < 1 or key > self.eurotherm._num_setpoints:
                log.warning("Eurotherm200 segment received invalid setpoint number")
                return None
            else:
                return self.eurotherm.read_float(SETPOINT_REGISTERS[key - 1])

        def __setitem__(self, key: int, val: float) -> None:
            """Write to appropriate register."""
            if key < 1 or key > self.eurotherm._num_setpoints:
                log.warning("Eurotherm200 segment received invalid setpoint number")
            else:
                self.eurotherm.write_float(SETPOINT_REGISTERS[key - 1], val)

    ##############
    # Programmer #
    ##############

    class Program:
        """Program class contains a list of Segment classes for each segment.

        Program 0 is the working program and is read-only. Segment 0 for each program is
        Program General Data. Program must have its `refresh` method called before
        segments can be accessed.

        Attributes:
            segments: list of segment dicts in program.
        """

        def __init__(self, program_num: int,
                     num_segments: int,
                     eurotherm: Eurotherm2000) -> None:
            """Create segment list and read current values.

            Args:
                program_num: program number, from 0 to maximum number of
                    programs supported by instrument.
                num_segments: number of maximum program segments supported by
                    instrument.
                eurotherm: Eurotherm2000 instance. Provides read/write access.
            """
            program_offset = 8192 + program_num*136
            self.segments = []

            for seg in range(num_segments+1):
                segment_offset = program_offset + seg*8
                self.segments.append(self.Segment(segment_offset, eurotherm))

        def refresh(self) -> None:
            """Create new dicts for all segments in program."""
            for segment in self.segments:
                segment.refresh()

        class Segment(dict):
            """A dictionary-like class for individual segments within a program.

            Segment values in key-value pairs are modified to behave similarly
            to other Eurotherm Python properties. Segment must have its `refresh` method
            called once before values can be read and written.
            """

            def __init__(self, offset: int, eurotherm: Eurotherm2000) -> None:
                """Read initial segment type and values.

                Args:
                    offset (int): segment register offset
                    eurotherm: Eurotherm2000 instance
                """
                self.offset = offset
                self.eurotherm = eurotherm

            def refresh(self, val: Optional[int] = None) -> None:
                """Create new dict from segment type."""
                self.clear()
                if ((self.offset % 8192) % 136) == 0:
                    self.registers = GENERAL_REGISTERS
                else:
                    if val is None:
                        val = self.eurotherm.read_register(self.offset)
                    self.registers = SEGMENTS_LIST[val]
                self.update({key: None for key in self.registers.keys()})

            def __setitem__(self, key: str, val) -> None:
                """Translate to register values if necessary, then write register."""
                key = key.casefold()
                if self.offset < 8328:
                    log.warning("Eurotherm program 0 is read-only.")
                    return

                if key not in self.registers:
                    log.warning("Parameter `%s` not in current segment type.", key)
                    return

                if key == "segment type":
                    if not isinstance(val, str):
                        log.warning("Invalid val type `%s` for `%s`.", type(val), key)
                        return
                    # Get new register offsets and update values
                    val_translated = reverse_dict(SEGMENT_TYPE)[val]
                    self.refresh(val_translated)

                if key in FLOAT_PARAMETERS:
                    if not isinstance(val, (float, int)):
                        log.warning("Invalid val type `%s` for `%s`.", type(val), key)
                        return
                    self.eurotherm.write_float(self.registers[key] + self.offset, val)

                elif key in WORD_PARAMETERS:
                    if not isinstance(val, str):
                        log.warning("Invalid val type `%s` for `%s`.", type(val), key)
                        return
                    word_list = key.upper().split()
                    key_dict = word_list[0] + "_" + word_list[1]
                    val_translated = reverse_dict(globals()[key_dict])[val]
                    self.eurotherm.write_register(
                        self.registers[key] + self.offset, val_translated
                    )

                elif key in INT_PARAMETERS:
                    if not isinstance(val, int):
                        log.warning("Invalid val type `%s` for `%s`.", type(val), key)
                        return
                    self.eurotherm.write_register(
                        self.registers[key] + self.offset, val
                    )

                else:
                    if not isinstance(val, (float, int)):
                        log.warning("Invalid val type `%s` for `%s`.", type(val), key)
                        return
                    self.eurotherm.write_time(self.registers[key] + self.offset, val)

                super().__setitem__(key, val)  # So items() method behaves as expected

            def __getitem__(self, key: str) -> Any:
                """Read appropriate register and translate value if necessary."""
                key = key.casefold()
                val: Any
                if key not in self.registers:
                    log.warning("Parameter not in current segment type.")
                    return None

                if key in FLOAT_PARAMETERS:
                    val = self.eurotherm.read_float(self.registers[key] + self.offset)

                elif key in WORD_PARAMETERS:
                    val = self.eurotherm.read_register(
                        self.registers[key] + self.offset
                    )
                    word_list = key.upper().split()
                    key_dict = word_list[0] + "_" + word_list[1]
                    val = globals()[key_dict][val]

                elif key in INT_PARAMETERS:
                    val = self.eurotherm.read_register(
                        self.registers[key] + self.offset
                    )

                else:
                    val = self.eurotherm.read_time(self.registers[key] + self.offset)

                super().__setitem__(key, val)  # So items() method behaves as expected
                return val


def reverse_dict(dict_):
    """Reverse the key/value status of a dict."""
    return {v: k for k, v in dict_.items()}


SETPOINT_REGISTERS = (24, 25, 164, 165, 166, 167, 168, 169, 170, 171, 172, 173, 174,
                      175, 176, 177)

GENERAL_REGISTERS = {"holdback type": 0,
                     "holdback value": 1,
                     "ramp units": 2,
                     "dwell units": 3,
                     "program cycles": 4}

END_REGISTERS = {"segment type": 0,
                 "end power": 1,
                 "end type": 3}

RAMP_RATE_REGISTERS = {"segment type": 0,
                       "target setpoint": 1,
                       "rate": 2}

RAMP_TIME_REGISTERS = {"segment type": 0,
                       "target setpoint": 1,
                       "duration": 2}

DWELL_REGISTERS = {"segment type": 0,
                   "duration": 2}

STEP_REGISTERS = {"segment type": 0,
                  "target setpoint": 1}

CALL_REGISTERS = {"segment type": 0,
                  "program number": 3}

HOLDBACK_TYPE = {0: 'none',
                 1: 'low',
                 2: 'high',
                 3: 'band'}

RAMP_UNITS = {0: 'secs',
              1: 'mins',
              2: 'hours'}

DWELL_UNITS = {0: 'secs',
               1: 'mins',
               2: 'hours'}

SEGMENT_TYPE = {0: 'end',
                1: "ramp rate",
                2: "ramp time",
                3: 'dwell',
                4: 'step',
                5: 'call'}

END_TYPE = {0: "dwell",
            1: "reset"}

FLOAT_PARAMETERS = ("holdback value", "target setpoint", "end power", "rate")

WORD_PARAMETERS = ("holdback type", "ramp units", "dwell units", "segment type",
                   "end type")

INT_PARAMETERS = ("program cycles", "program number")

SEGMENTS_LIST = (END_REGISTERS, RAMP_RATE_REGISTERS, RAMP_TIME_REGISTERS,
                 DWELL_REGISTERS, STEP_REGISTERS, CALL_REGISTERS)
