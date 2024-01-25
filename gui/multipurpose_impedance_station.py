#
# This file is part of the PyMeasure package.
#
# Copyright (c) 2013-2023 PyMeasure Developers
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
#

"""
This example demonstrates how to make a graphical interface to preform
V characteristic measurements. There are a two items that need to be
changed for your system:

1) Correct the adapter in VProcedure.startup for your instrument
2) Correct the directory to save files in MainWindow.queue

Run the program by changing to the directory containing this file and calling:

python keithley2420_gui.py

"""

import logging
import sys
from time import sleep, time

from pymeasure.adapters import VISAAdapter
from pymeasure.instruments.keithley import Keithley2182
from pymeasure.instruments.proterial import ROD4
from pymeasure.display.Qt import QtWidgets
from pymeasure.display.windows import ManagedWindow
from pymeasure.experiment import (
    Procedure, Parameter, IntegerParameter, FloatParameter, BooleanParameter,
    unique_filename, Results
)

from ..instruments.eurotherm2000 import Eurotherm2000
from ..instruments.biologic import GeneralPotentiostat

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


class Experiment(Procedure):

    delay = FloatParameter('Record Time', units='s', default=1.0)

    keithley_port = Parameter('Keithley 2182 Port',
                              default='ASRL/dev/ttyUSB0::INSTR',
                              ui_class=None,
                              group_by='pO2_toggle')
    rod4_port = Parameter('ROD4 Port',
                          default='ASRL/dev/ttyUSB1::INSTR',
                          ui_class=None)
    eurotherm_port = Parameter('Eurotherm Port',
                               default='/dev/ttyUSB2',
                               ui_class=None)
    eurotherm_address = IntegerParameter('Eurotherm Address',
                                         minimum=1,
                                         maximum=254,
                                         step=1,
                                         default=1)
    biologic_port = Parameter('Biologic Port',
                              default='192.109.209.128',
                              ui_class=None,
                              group_by='eis_toggle')

    target_temperature = FloatParameter('Target Temperature', units='C')
    ramp_rate = FloatParameter('Ramp Rate', units = 'C/min', default=5.0)

    pO2_toggle = BooleanParameter('pO2 Sensor Connected', default=True)
    pO2_slope = FloatParameter('pO2 Sensor Cal Slope', group_by='pO2_toggle')
    pO2_intercept = FloatParameter('pO2 Sensor Cal Intercept',
                                   group_by='pO2_toggle')

    mfc_1_flow = FloatParameter('MFC 1 Flow', units='sccm')
    mfc_2_flow = FloatParameter('MFC 2 Flow', units='sccm')
    mfc_3_flow = FloatParameter('MFC 3 Flow', units='sccm')
    mfc_4_flow = FloatParameter('MFC 4 Flow', units='sccm')

    eis_toggle = BooleanParameter('Run EIS', default=True)
    maximum_frequency = FloatParameter('Maximum Frequency', units='Hz',
                                       default=1e6, maximum=7e6, minimum=1e-5,
                                       group_by='eis_toggle')
    minimum_frequency = FloatParameter('Minimum Frequency', units='Hz',
                                       default=1.0, maximum=7e6, minimum=1e-5,
                                       group_by='eis_toggle')
    amplitude_voltage = FloatParameter('Amplitude Voltage', units='V',
                                       default=0.02, minimum=5e-4, maximum=2.5,
                                       group_by='eis_toggle')

    DATA_COLUMNS = ['Time (s)',
                    'Furnace Temperature (C)',
                    'pO2 Sensor Temperature (C)',
                    'pO2 (atm)',
                    'MFC 1 Flow (sccm)',
                    'MFC 2 Flow (sccm)',
                    'MFC 3 Flow (sccm)',
                    'MFC 4 Flow (sccm)',
                    'Frequency (Hz)',
                    'Z\' (Ohm)',
                    '-Z\" (Ohm)']

    def startup(self):
        log.info("Connecting to instruments")
        if self.pO2_toggle:
            self.keithley_adapter = VISAAdapter(self.keithley_port,
                                                visa_library='@py')
            self.keithley = Keithley2182(self.keithley_adapter)
        self.rod4_adapter = VISAAdapter(self.rod4_port,
                                        visa_library='@py')
        self.rod4 = ROD4(self.rod4_adapter)
        self.eurotherm = Eurotherm2000(self.eurotherm_port,
                                       self.eurotherm_address)
        if self.eis_toggle:
            self.biologic = GeneralPotentiostat('SP200', self.biologic_port)

        sleep(2)

    def execute(self):
        log.info("Beginning measurement.")
        self.start_furnace()
                
        time0 = time()
        while not self.should_stop():

            sleep(self.delay)
            
            furnace_temp = self.eurotherm.process_value
            sensor_temp = 0
            sensor_pO2 = 0
            
            mfc1_flow = self.rod4.ch_1.actual_flow
            mfc2_flow = self.rod4.ch_2.actual_flow
            mfc3_flow = self.rod4.ch_3.actual_flow
            mfc4_flow = self.rod4.ch_4.actual_flow
            
            biologic_frequency = 0
            biologic_Zre = 0
            biologic_Zim = 0

            new_time = time()

            data = {
                'Time (s)' : new_time - time0,
                'Furnace Temperature (C)' : furnace_temp,
                'pO2 Sensor Temperature (C)' : sensor_temp,
                'pO2 (atm)' : sensor_pO2,
                'MFC 1 Flow (sccm)' : mfc1_flow,
                'MFC 2 Flow (sccm)' : mfc2_flow,
                'MFC 3 Flow (sccm)' : mfc3_flow,
                'MFC 4 Flow (sccm)' : mfc4_flow,
                'Frequency (Hz)' : biologic_frequency,
                'Z\' (Ohm)' : biologic_Zre,
                '-Z\" (Ohm)' : biologic_Zim
            }
            self.emit('results', data)
            self.emit('progress', )
            if self.should_stop():
                log.warning("Catch stop command in procedure")
                break

    def shutdown(self):
        """Shut off furnace and turn off gas flow."""
        self.eurotherm.program_status = 'reset'
        
        self.rod4.ch_1.actual_flow = 0
        self.rod4.ch_2.actual_flow = 0
        self.rod4.ch_3.actual_flow = 0
        self.rod4.ch_4.actual_flow = 0
        log.info("Finished")

    def start_furnace(self):
        self.eurotherm.program_status = 'reset'
        self.eurotherm.current_program = 1

        self.eurotherm.programs[1].segments[0]['Ramp Units'] = 'Mins'
        self.eurotherm.programs[1].segments[0]['Program Cycles'] = 0

        self.eurotherm.programs[1].segments[1]['Segment Type'] = 'Ramp Rate'
        self.eurotherm.programs[1].segments[1]['Ramp Rate'] = self.ramp_rate
        self.eurotherm.programs[1].segments[1]['Target Setpoint'] = \
            self.target_temperature

        self.eurotherm.programs[1].segments[2]['Segment Type'] = 'End'
        self.eurotherm.programs[1].segments[2]['End Type'] = 'Dwell'

        self.eurotherm.program_status = 'run'


class MainWindow(ManagedWindow):

    def __init__(self):
        inputs=['delay', 'keithley_port', 'rod4_port', 'eurotherm_port',
                'eurotherm_address', 'biologic_port', 'target_temperature',
                'ramp_rate', 'pO2_toggle', 'pO2_slope', 'pO2_intercept',
                'mfc_1_flow', 'mfc_2_flow', 'mfc_3_flow', 'mfc_4_flow',
                'eis_toggle', 'maximum_frequency', 'minimum_frequency',
                'amplitude_voltage']
        super().__init__(
            procedure_class=Experiment,
            inputs=inputs,
            x_axis=['Z\' (Ohm)', 'Time (s)'],
            y_axis=['-Z\" (Ohm)'
                    'Furnace Temperature (C)',
                    'pO2 Sensor Temperature (C)',
                    'pO2 (atm)',
                    'MFC 1 Flow (sccm)',
                    'MFC 2 Flow (sccm)',
                    'MFC 3 Flow (sccm)',
                    'MFC 4 Flow (sccm)'],
            inputs_in_scrollarea=True
        )
        self.setWindowTitle('Multipurpose Impedance Station')

    def queue(self):
        directory = "./"  # Change this to the desired directory
        filename = unique_filename(directory, prefix='demo')

        procedure = self.make_procedure()
        results = Results(procedure, filename)
        experiment = self.new_experiment(results)

        self.manager.queue(experiment)


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
