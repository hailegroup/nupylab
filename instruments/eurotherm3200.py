#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Jan 29 15:13:28 2024

@author: connor
"""

import logging
import minimalmodbus

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


class Eurotherm3200(minimalmodbus.Instrument):
    """Instrument class for Eurotherm 3200 series process controller.

    Args:
        * port (str or serial.Serial): port name or Serial connection
        * clientaddress (int): client address in the range 1 to 254

    Eurotherm 3200 series only support RTU MODBUS mode.
        * NONE (default), ODD, or EVEN parity bit
        * Baud rate 1200, 2400, 4800, 9600 (default), 19200
        * Default client address is 1
    """

    @property
    def process_value(self):
        """Process variable"""
        return self.read_float(1)

    @property
    def target_setpoint(self):
        """Target setpoint (if in manual mode)"""
        return self.read_float(2)

    @target_setpoint.setter
    def target_setpoint(self, val: int | float):
        self.write_float(2, val)

    @property
    def output_level(self):
        """Power output in percent"""
        return self.read_float(3)

    @property
    def working_output(self):
        """Read-only if in auto mode"""
        return self.read_float(4)

    @working_output.setter
    def working_output(self, val: int | float):
        self.write_float(4, val)

    @property
    def working_setpoint(self):
        """Working set point. Read only."""
        return self.read_float(5)

    @property
    def active_setpoint(self):
        """
        1: SP1
        2: SP2
        """
        return self.read_register(15)+1

    @active_setpoint.setter
    def active_setpoint(self, val: int):
        if val < 1 or val > 2:
            log.warning('Invalid setpoint number')
        else:
            self.write_register(15, val-1)

    @property
    def program_status(self):
        """Program Status"""
        program_status_dict = {0: 'Reset',
                               1: 'Run',
                               2: 'Hold',
                               3: 'End'}
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
        """Do not write continuously changing values to this variable. Use
        internal ramp rate function or remote comms setpoint instead."""
        self.read_float(24)

    @setpoint1.setter
    def setpoint1(self, val: int | float):
        self.write_float(24, val)

    @property
    def setpoint2(self):
        """Do not write continuously changing values to this variable. Use
        internal ramp rate function or remote comms setpoint instead."""
        self.read_float(24)

    @setpoint2.setter
    def setpoint2(self, val: int | float):
        self.write_float(25, val)

    @property
    def remote_setpoint(self):
        """Local/remote setpoint is selected with address 276"""
        self.read_float(26)

    @remote_setpoint.setter
    def remote_setpoint(self, val: int | float):
        self.write_float(26, val)

    @property
    def setpoint_rate_limit(self):
        """0 = no rate limit"""
        self.read_float(35)

    @setpoint_rate_limit.setter
    def setpoint_rate_limit(self, val: int | float):
        self.write_float(35, val)

    @property
    def calculated_error(self):
        """PV - SP"""
        return self.read_float(39)

    @property
    def local_remote_setpoint(self):
        """Select whether local or remote (comms) setpoint is selected. Remote
        setpoint is stored in address 26."""
        val = self.read_register(276)
        remote_dict = {0: 'Local', 1: 'Remote'}
        return remote_dict[val]

    @local_remote_setpoint.setter
    def local_remote_setpoint(self, val: str):
        remote_dict = {'local': 0, 'remote': 1}
        self.write_register(276, remote_dict[val.casefold()])

    @property
    def end_type(self):
        """Programmer end type"""
        end_type_dict = {0: 'Off', 1: 'Dwell', 2: 'SP2', 3: 'Reset'}
        val = self.read_register(328)
        return end_type_dict[val]

    @end_type.setter
    def end_type(self, val: str):
        end_type_dict = {'off': 0, 'dwell': 1, 'sp2': 2, 'reset': 3}
        self.write_register(328, end_type_dict[val.casefold()])

    @property
    def program_cycles(self):
        """Number of program cycles to run"""
        return self.read_register(332)

    @program_cycles.setter
    def program_cycles(self, val: int):
        self.write_register(332, val)

    @property
    def current_program_cycle(self):
        """Current program cycle number"""
        return self.read_register(333)

    @property
    def ramp_units(self):
        """Degrees per `Mins`, `Hours`, or `Secs`"""
        ramp_dict = {0: 'Mins', 1: 'Hours', 2: 'Secs'}
        val = self.read_register(531)
        return ramp_dict[val]

    @ramp_units.setter
    def ramp_units(self, val: str):
        ramp_dict = {'mins': 0, 'hours': 1, 'secs': 2}
        self.write_register(531, ramp_dict[val.casefold()])

    @property
    def dwell1(self):
        """Programmer dwell 1 duration"""
        return self.read_float(1280)

    @dwell1.setter
    def dwell1(self, val: int | float):
        self.write_float(1280, val)

    @property
    def target_setpoint1(self):
        """Programmer target setpoint 1"""
        return self.read_float(1281)

    @target_setpoint1.setter
    def target_setpoint1(self, val: int | float):
        self.write_float(1281, val)

    @property
    def ramp_rate1(self):
        """Programmer ramp rate 1"""
        return self.read_float(1282)

    @ramp_rate1.setter
    def ramp_rate1(self, val: int | float):
        self.write_float(1282, val)

    @property
    def dwell2(self):
        """Programmer dwell 2 duration"""
        return self.read_float(1283)

    @dwell2.setter
    def dwell2(self, val: int | float):
        self.write_float(1283, val)

    @property
    def target_setpoint2(self):
        """Programmer target setpoint 2"""
        return self.read_float(1284)

    @target_setpoint2.setter
    def target_setpoint2(self, val: int | float):
        self.write_float(1284, val)

    @property
    def ramp_rate2(self):
        """Programmer ramp rate 2"""
        return self.read_float(1285)

    @ramp_rate2.setter
    def ramp_rate2(self, val: int | float):
        self.write_float(1285, val)

    @property
    def dwell3(self):
        """Programmer dwell 3 duration"""
        return self.read_float(1286)

    @dwell3.setter
    def dwell3(self, val: int | float):
        self.write_float(1286, val)

    @property
    def target_setpoint3(self):
        """Programmer target setpoint 3"""
        return self.read_float(1287)

    @target_setpoint3.setter
    def target_setpoint3(self, val: int | float):
        self.write_float(1287, val)

    @property
    def ramp_rate3(self):
        """Programmer ramp rate 3"""
        return self.read_float(1288)

    @ramp_rate3.setter
    def ramp_rate3(self, val: int | float):
        self.write_float(1288, val)

    @property
    def dwell4(self):
        """Programmer dwell 4 duration"""
        return self.read_float(1289)

    @dwell4.setter
    def dwell4(self, val: int | float):
        self.write_float(1289, val)

    @property
    def target_setpoint4(self):
        """Programmer target setpoint 4"""
        return self.read_float(1290)

    @target_setpoint4.setter
    def target_setpoint4(self, val: int | float):
        self.write_float(1290, val)

    @property
    def ramp_rate4(self):
        """Programmer ramp rate 4"""
        return self.read_float(1291)

    @ramp_rate4.setter
    def ramp_rate4(self, val: int | float):
        self.write_float(1291, val)
