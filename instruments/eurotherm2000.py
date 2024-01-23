#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Jan  4 12:24:42 2024

@author: Connor Carr
"""

import logging
import minimalmodbus

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

class Eurotherm2000(minimalmodbus.Instrument):
    """Instrument class for Eurotherm 2000 series process controller.

    Args:
        * port (str or serial.Serial): port name or Serial connection
        * clientaddress (int): client address in the range 1 to 254

    Eurotherm 2000 series only support RTU MODBUS mode.
        * 1 start bit
        * 8 data bits
        * NONE, ODD, or EVEN parity bit
        * 1 stop bit
    """

    def __init__(self, port, clientaddress):
        """Read specific program and setpoint configuration and create
        corresponding attributes."""

        super().__init__(self, port, clientaddress)
        self.num_programs = self.read_register(517)
        num_segments = self.read_register(211)
        self.num_setpoints = self.read_register(521)

        self.programs = []
        for p in range(self.num_programs+1):
            self.programs.append(self.Program(p, num_segments, self))

    #############
    # Home List #
    #############

    @property
    def process_value(self):
        """Process variable"""
        return self.read_float(1)

    @property
    def output_level(self):
        """Power output in percent"""
        return self.read_float(3)

    @property
    def target_setpoint(self):
        """Target setpoint (if in manual mode)"""
        return self.read_float(2)

    @target_setpoint.setter
    def target_setpoint(self, val: int | float):
        self.write_float(2, val)

    @property
    def operating_mode(self):
        """Auto/manual mode select"""
        auto_man_dict = {0: 'Auto',
                         1: 'Manual'}
        return auto_man_dict[self.read_register(273)]

    @operating_mode.setter
    def operating_mode(self, val: str):
        auto_man_dict = {'auto': 0,
                         'manual': 1}
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
        """Current program running (active program number)"""
        return self.read_register(22)

    @current_program.setter
    def current_program(self, val: int):
        if val < 0 or val > self.num_programs:
            log.warning('Invalid program number')
        else:
            self.write_register(22, val)

    @property
    def program_status(self):
        """Program Status"""
        program_status_dict = {1: 'Reset',
                               2: 'Run',
                               4: 'Hold',
                               8: 'Holdback',
                               16: 'Complete'}
        return program_status_dict[self.read_register(23)]

    @program_status.setter
    def program_status(self, val: str):
        program_status_dict = {'reset': 1,
                               'run': 2,
                               'hold': 4,
                               'holdback': 8,
                               'complete': 16}
        self.write_register(23, program_status_dict[val.casefold()])

    @property
    def programmer_setpoint(self):
        """Read only"""
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
        """Read only.
        End, Ramp (rate), Ramp (time to target), Dwell, Step, or Call"""
        return SEGMENT_TYPE_VALUES[self.read_register(29)]

    @property
    def segment_time_remaining(self):
        """Read only. Segment time remaining in milliseconds."""
        return self.read_register(36)

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
        """Read only. Program time remaining in milliseconds."""
        return self.read_register(58)

    #################
    # Setpoint List #
    #################

    @property
    def select_setpoint(self):
        """
        1: SP1
        2: SP2
        3: SP3
        etc."""
        return self.read_register(15)+1

    @select_setpoint.setter
    def select_setpoint(self, val: int):
        if val < 1 or val > self.num_setpoints:
            log.warning('Invalid setpoint number')
        else:
            self.write_register(15, val-1)

    ##############
    # Programmer #
    ##############

    class Program:
        """Program class contains a list of Segment classes for each segment,
        including segment 0 (Program General Data)."""

        def __init__(self, program_num, num_segments, modbus):
            """Create segment list and read current values.

            Args:
                program_num (int): program number, from 0 to maximum number of
                    programs supported by instrument
                num_segments (int): number of maximum program segments
                    supported by instrument
                modbus: Eurotherm modbus instance"""

            program_offset = 8192 + program_num*136
            self.segments = []

            for seg in range(num_segments+1):
                segment_offset = program_offset + seg*8
                self.segments.append(self.Segment(segment_offset, modbus))


        class Segment(dict):
            """A dictionary-like class for individual segments within a program.

            Segment values in key-value pairs are modified to behave similarly
            to other Eurotherm Python properties."""

            def __init__(self, offset, modbus):
                """Read initial segment type and values.

                Args:
                    offset (int): segment register offset
                    modbus: Eurotherm modbus instance
                """

                self.modbus = modbus
                self.offset = offset
                super().__init__()
                if (self.offset - 8192) % 136 == 0:
                    self.registers = GENERAL_REGISTERS
                else:
                    #Read initial segment type
                    seg_type_val = self.modbus.read_register(self.offset)
                    self.registers = SEGMENTS_LIST[seg_type_val]
                self.update_values()

            def update_values(self):
                """Get current segment registry values"""
                for key, val in self.registers.items():
                    if key in FLOAT_PARAMETERS:
                        register_val = self.modbus.read_float(val+self.offset)
                    else:
                        register_val = self.modbus.read_register(val+self.offset)
                    if key=='Segment Type':
                        register_val = SEGMENT_TYPE_VALUES[register_val]
                    elif key=='Holdback Type':
                        register_val = HOLDBACK_TYPE_VALUES[register_val]
                    elif key=='Ramp Units':
                        register_val = RAMP_UNITS[register_val]
                    elif key=='Dwell Units':
                        register_val = DWELL_UNITS[register_val]
                    self.update({key, register_val})

            def __setitem__(self, key, val):
                """Translate to register values if necessary, then write
                register."""

                if self.offset==8192:
                    log.warning("Program 0 is read-only.")
                    return

                if val==self[key]: #ignore if value is unchanged
                    return

                if key=='Segment Type':
                    val = reverse_dict(SEGMENT_TYPE_VALUES)[val]
                    #Get new register offsets on type change and update values
                    self.registers = SEGMENTS_LIST[val]
                    self.clear()
                    self.modbus.write_register(self.registers[key] + self.offset,
                                               val)
                    self.update_values()
                    return

                if key=='Holdback Type':
                    val = reverse_dict(HOLDBACK_TYPE_VALUES)[val]
                elif key=='Ramp Units':
                    val = reverse_dict(RAMP_UNITS)[val]
                elif key=='Dwell Units':
                    val = reverse_dict(DWELL_UNITS)[val]

                if key in FLOAT_PARAMETERS:
                    self.modbus.write_float(self.registers[key] + self.offset,
                                            val)
                else:
                    self.modbus.write_register(self.registers[key] + self.offset,
                                               val)


def reverse_dict(dict_):
    """Reverse the key/value status of a dict"""
    return {v: k for k, v in dict_.items()}

GENERAL_REGISTERS = {"Holdback Type" : 0,
                      "Holdback Value" : 1,
                      "Ramp Units" : 2,
                      "Dwell Units" : 3,
                      "Program Cycles": 4}

HOLDBACK_TYPE_VALUES = {0: 'None',
                        1: 'Low',
                        2: 'High',
                        3: 'Band'}

RAMP_UNITS = {0: 'Secs',
              1: 'Mins',
              2: 'Hours'}

DWELL_UNITS = {0: 'Secs',
              1: 'Mins',
              2: 'Hours'}

SEGMENT_TYPE_VALUES = {0: "End",
                       1: "Ramp Rate",
                       2: "Ramp Time",
                       3: "Dwell",
                       4: "Step",
                       5: "Call"}

END_REGISTERS = {"Segment Type": 0,
                 "End Power" : 1,
                 "End Type" : 3}

RAMP_RATE_REGISTERS = {"Segment Type": 0,
                       "Target Setpoint" : 1,
                       "Rate" : 2}

RAMP_TIME_REGISTERS = {"Segment Type": 0,
                       "Target Setpoint" : 1,
                       "Duration" : 2}

DWELL_REGISTERS = {"Segment Type": 0,
                   "Duration" : 1}

STEP_REGISTERS = {"Segment Type": 0,
                  "Target Setpoint" : 1}

CALL_REGISTERS = {"Segment Type": 0,
                  "Program Number" : 3}

FLOAT_PARAMETERS = ['Holdback Value', 'Target Setpoint', 'End Power', 'Rate']

SEGMENTS_LIST = [END_REGISTERS, RAMP_RATE_REGISTERS, RAMP_TIME_REGISTERS,
                 DWELL_REGISTERS, STEP_REGISTERS, CALL_REGISTERS]
