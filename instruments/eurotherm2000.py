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
        self.setpoints = self.Setpoints(self.num_setpoints,
                                        self.read_float,
                                        self.write_float)

        self.programs = []
        for p in range(self.num_programs+1):
            self.programs.append(self.Program(p,
                                              num_segments,
                                              self.read_register,
                                              self.write_register,
                                              self.read_float,
                                              self.write_float)
                                 )

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
        return SEGMENT_TYPE[self.read_register(29)]

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
    def active_setpoint(self):
        """
        1: SP1
        2: SP2
        3: SP3
        etc."""
        return self.read_register(15)+1

    @active_setpoint.setter
    def active_setpoint(self, val: int):
        if val < 1 or val > self.num_setpoints:
            log.warning('Invalid setpoint number')
        else:
            self.write_register(15, val-1)

    class Setpoints(dict):
        """Setpoints dictionary contains entries for valid setpoints in
        Eurotherm and read and write methods for accessing appropriate
        registers."""

        def __init__(self, num_setpoints, read_float, write_float):
            """Create empty dictionary with access to read_float and
            write_float methods.

            Args:
                num_setpoints (int)  number of setpoints supported by
                    Eurotherm
                read_float: Eurotherm read_float method
                write_float: Eurotherm write_float method
            """

            self.read_float = read_float
            self.write_float = write_float
            self.num_setpoints = num_setpoints
            super().__init__()
            self.update({key: None for key in range(1, num_setpoints+1)})

        def __getitem__(self, key):
            if key < 1 or key > self.num_setpoints:
                log.warning('Invalid setpoint number')
            return self.read(SETPOINT_REGISTERS[key-1])

        def __setitem__(self, key, val):
            if key < 1 or key > self.num_setpoints:
                log.warning('Invalid setpoint number')
            self.write_float(SETPOINT_REGISTERS[key-1], val)

    ##############
    # Programmer #
    ##############

    class Program:
        """Program class contains a list of Segment classes for each segment,
        including segment 0 (Program General Data)."""

        def __init__(self, program_num, num_segments, read_register,
                     write_register, read_float, write_float):
            """Create segment list and read current values.

            Args:
                program_num (int): program number, from 0 to maximum number of
                    programs supported by instrument
                num_segments (int): number of maximum program segments
                    supported by instrument
                read_register: Eurotherm read_register method
                write_register: Eurotherm write_register method
                read_float: Eurotherm read_float method
                write_float: Eurotherm write_float method"""

            program_offset = 8192 + program_num*136
            self.segments = []

            for seg in range(num_segments+1):
                segment_offset = program_offset + seg*8
                self.segments.append(self.Segment(segment_offset,
                                                  read_register,
                                                  write_register,
                                                  read_float,
                                                  write_float)
                                     )

        class Segment(dict):
            """A dictionary-like class for individual segments within a
            program.

            Segment values in key-value pairs are modified to behave similarly
            to other Eurotherm Python properties."""

            def __init__(self, offset, read_register, write_register,
                         read_float, write_float):
                """Read initial segment type and values.

                Args:
                    offset (int): segment register offset
                    read_register: Eurotherm read_register method
                    write_register: Eurotherm write_register method
                    read_float: Eurotherm read_float method
                    write_float: Eurotherm write_float method
                """

                self.offset = offset
                self.read_register = read_register
                self.write_register = write_register
                self.read_float = read_float
                self.write_float = write_float
                super().__init__()
                if (self.offset - 8192) % 136 == 0:
                    self.registers = GENERAL_REGISTERS
                else:
                    self.registers = None

            def __setitem__(self, key, val):
                """Translate to register values if necessary, then write
                register."""

                if self.offset == 8192:
                    log.warning("Program 0 is read-only.")
                    return

                if self.registers is None:
                    seg_type_val = self.read_register(self.offset)
                    self.registers = SEGMENTS_LIST[seg_type_val]
                    self.update({k: None for k in self.registers.keys()})

                if key not in self.registers:
                    log.warning("Parameter not in current segment type")
                    return

                if val == self[key]:  # ignore if value is unchanged
                    return

                if key == 'Segment Type':
                    val = reverse_dict(SEGMENT_TYPE)[val]
                    # Get new register offsets on type change and update values
                    self.registers = SEGMENTS_LIST[val]
                    self.clear()
                    self.update({k: None for k in self.registers.keys()})
                    return

                self.update({key, val})

                if key in FLOAT_PARAMETERS:
                    self.write_float(self.registers[key] + self.offset, val)

                elif key in WORD_PARAMETERS:
                    word_list = key.upper().split()
                    key_dict = word_list[0] + '_' + word_list[1]
                    val = reverse_dict(globals()[key_dict])[val]
                    self.write_register(self.registers[key] + self.offset, val)

                else:
                    self.write_register(self.registers[key] + self.offset, val)

            def __getitem__(self, key):
                """Read appropriate register and translate value if
                necessary."""

                if self.registers is None:
                    seg_type_val = self.read_register(self.offset)
                    self.registers = SEGMENTS_LIST[seg_type_val]
                    self.update({k: None for k in self.registers.keys()})

                if key not in self.registers:
                    log.warning("Parameter not in current segment type")
                    return

                if key in FLOAT_PARAMETERS:
                    val = self.read_float(self.registers[key] + self.offset)
                else:
                    val = self.read_register(self.registers[key] + self.offset)
                    if key in WORD_PARAMETERS:
                        word_list = key.upper().split()
                        key_dict = word_list[0] + '_' + word_list[1]
                        val = globals()[key_dict][val]

                # So items() method behaves as expected
                self.update({key, val})
                return val


def reverse_dict(dict_):
    """Reverse the key/value status of a dict"""
    return {v: k for k, v in dict_.items()}


SETPOINT_REGISTERS = (24, 25, 164, 165, 166, 167, 168, 169, 170,
                      171, 172, 173, 174, 175, 176, 177)

GENERAL_REGISTERS = {"Holdback Type": 0,
                     "Holdback Value": 1,
                     "Ramp Units": 2,
                     "Dwell Units": 3,
                     "Program Cycles": 4}

END_REGISTERS = {"Segment Type": 0,
                 "End Power": 1,
                 "End Type": 3}

RAMP_RATE_REGISTERS = {"Segment Type": 0,
                       "Target Setpoint": 1,
                       "Rate": 2}

RAMP_TIME_REGISTERS = {"Segment Type": 0,
                       "Target Setpoint": 1,
                       "Duration": 2}

DWELL_REGISTERS = {"Segment Type": 0,
                   "Duration": 1}

STEP_REGISTERS = {"Segment Type": 0,
                  "Target Setpoint": 1}

CALL_REGISTERS = {"Segment Type": 0,
                  "Program Number": 3}

HOLDBACK_TYPE = {0: 'None',
                 1: 'Low',
                 2: 'High',
                 3: 'Band'}

RAMP_UNITS = {0: 'Secs',
              1: 'Mins',
              2: 'Hours'}

DWELL_UNITS = {0: 'Secs',
               1: 'Mins',
               2: 'Hours'}

SEGMENT_TYPE = {0: "End",
                1: "Ramp Rate",
                2: "Ramp Time",
                3: "Dwell",
                4: "Step",
                5: "Call"}

END_TYPE = {0: 'Dwell',
            1: 'Reset'}

FLOAT_PARAMETERS = ['Holdback Value', 'Target Setpoint', 'End Power', 'Rate']

WORD_PARAMETERS = ["Holdback Type", "Ramp Units", "Dwell Units",
                   "Segment Type", "End Type"]

SEGMENTS_LIST = [END_REGISTERS, RAMP_RATE_REGISTERS, RAMP_TIME_REGISTERS,
                 DWELL_REGISTERS, STEP_REGISTERS, CALL_REGISTERS]
