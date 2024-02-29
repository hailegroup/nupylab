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

import logging
import minimalmodbus

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


class Eurotherm3200(minimalmodbus.Instrument):
    """Instrument class for Eurotherm 2000 series process controller.

    Attributes:
        serial: pySerial serial port object, for setting data transfer parameters.
        setpoints: dict of available setpoints.
        programs: list of available programs, each program containing a list of segment
            dictionaries.
    """

    def read_float(self, register: int):  # Float registers are double sized and offset
        """Convert to higher register to properly read floats."""
        return super().read_float(2 * register + 32768)

    def write_float(self, register: int, val: float):
        """Convert to higher register to properly write floats."""
        super().write_float(2 * register + 32768, val)

    def read_time(self, register: int):
        """Read time parameters in seconds."""
        return super().read_long(2 * register + 32768) / 1000

    def write_time(self, register: int, val: float):
        """Write time parameters in seconds."""
        super().write_long(2 * register + 32768, round(val * 1000))

    @property
    def process_value(self):
        """Process variable."""
        return self.read_float(1)

    @property
    def target_setpoint(self):
        """Target setpoint (if in manual mode)."""
        return self.read_float(2)

    @target_setpoint.setter
    def target_setpoint(self, val: float):
        self.write_float(2, val)

    @property
    def output_level(self):
        """Power output in percent."""
        return self.read_float(3)

    @property
    def working_output(self):
        """Read-only if in auto mode."""
        return self.read_float(4)

    @working_output.setter
    def working_output(self, val: float):
        self.write_float(4, val)

    @property
    def working_setpoint(self):
        """Working set point. Read only."""
        return self.read_float(5)

    @property
    def active_setpoint(self):
        """1: SP1, 2: SP2."""
        return self.read_register(15)+1

    @active_setpoint.setter
    def active_setpoint(self, val: int):
        if val not in (1, 2):
            log.warning(f"Setpoint must be 1 or 2, not {val}.")
        else:
            self.write_register(15, val-1)

    @property
    def program_status(self):
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
    def setpoint1(self):
        """Do not write continuously changing values to this variable."""
        self.read_float(24)

    @setpoint1.setter
    def setpoint1(self, val: float):
        self.write_float(24, val)

    @property
    def setpoint2(self):
        """Do not write continuously changing values to this variable."""
        self.read_float(24)

    @setpoint2.setter
    def setpoint2(self, val: float):
        self.write_float(25, val)

    @property
    def remote_setpoint(self):
        """Local/remote setpoint is selected with address 276."""
        self.read_float(26)

    @remote_setpoint.setter
    def remote_setpoint(self, val: float):
        self.write_float(26, val)

    @property
    def setpoint_rate_limit(self):
        """0 = no rate limit."""
        self.read_float(35)

    @setpoint_rate_limit.setter
    def setpoint_rate_limit(self, val: float):
        self.write_float(35, val)

    @property
    def calculated_error(self):
        """Error = PV - SP."""
        return self.read_float(39)

    @property
    def remote_setpoint_enabled(self):
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
    def end_type(self):
        """Programmer end type."""
        end_type_dict = {0: 'off', 1: 'dwell', 2: 'sp2', 3: 'reset'}
        val = self.read_register(328)
        return end_type_dict[val]

    @end_type.setter
    def end_type(self, val: str):
        end_type_dict = {'off': 0, 'dwell': 1, 'sp2': 2, 'reset': 3}
        self.write_register(328, end_type_dict[val.casefold()])

    @property
    def program_cycles(self):
        """Number of program cycles to run."""
        return self.read_register(332)

    @program_cycles.setter
    def program_cycles(self, val: int):
        self.write_register(332, val)

    @property
    def current_program_cycle(self):
        """Current program cycle number."""
        return self.read_register(333)

    @property
    def ramp_units(self):
        """Degrees per `mins`, `hours`, or `secs`."""
        ramp_dict = {0: 'mins', 1: 'hours', 2: 'secs'}
        val = self.read_register(531)
        return ramp_dict[val]

    @ramp_units.setter
    def ramp_units(self, val: str):
        ramp_dict = {'mins': 0, 'hours': 1, 'secs': 2}
        self.write_register(531, ramp_dict[val.casefold()])

    @property
    def dwell1(self):
        """Programmer dwell 1 duration in seconds."""
        return self.read_time(1280)

    @dwell1.setter
    def dwell1(self, val: float):
        self.write_time(1280, val)

    @property
    def target_setpoint1(self):
        """Programmer target setpoint 1."""
        return self.read_float(1281)

    @target_setpoint1.setter
    def target_setpoint1(self, val: float):
        self.write_float(1281, val)

    @property
    def ramp_rate1(self):
        """Programmer ramp rate 1."""
        return self.read_float(1282)

    @ramp_rate1.setter
    def ramp_rate1(self, val: float):
        self.write_float(1282, val)

    @property
    def dwell2(self):
        """Programmer dwell 2 duration in seconds."""
        return self.read_time(1283)

    @dwell2.setter
    def dwell2(self, val: float):
        self.write_time(1283, val)

    @property
    def target_setpoint2(self):
        """Programmer target setpoint 2."""
        return self.read_float(1284)

    @target_setpoint2.setter
    def target_setpoint2(self, val: float):
        self.write_float(1284, val)

    @property
    def ramp_rate2(self):
        """Programmer ramp rate 2."""
        return self.read_float(1285)

    @ramp_rate2.setter
    def ramp_rate2(self, val: float):
        self.write_float(1285, val)

    @property
    def dwell3(self):
        """Programmer dwell 3 duration in seconds."""
        return self.read_time(1286)

    @dwell3.setter
    def dwell3(self, val: float):
        self.write_time(1286, val)

    @property
    def target_setpoint3(self):
        """Programmer target setpoint 3."""
        return self.read_float(1287)

    @target_setpoint3.setter
    def target_setpoint3(self, val: float):
        self.write_float(1287, val)

    @property
    def ramp_rate3(self):
        """Programmer ramp rate 3."""
        return self.read_float(1288)

    @ramp_rate3.setter
    def ramp_rate3(self, val: float):
        self.write_float(1288, val)

    @property
    def dwell4(self):
        """Programmer dwell 4 duration in seconds."""
        return self.read_time(1289)

    @dwell4.setter
    def dwell4(self, val: float):
        self.write_time(1289, val)

    @property
    def target_setpoint4(self):
        """Programmer target setpoint 4."""
        return self.read_float(1290)

    @target_setpoint4.setter
    def target_setpoint4(self, val: float):
        self.write_float(1290, val)

    @property
    def ramp_rate4(self):
        """Programmer ramp rate 4."""
        return self.read_float(1291)

    @ramp_rate4.setter
    def ramp_rate4(self, val: float):
        self.write_float(1291, val)
