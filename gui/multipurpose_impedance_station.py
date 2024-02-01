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
import numpy as np

from pymeasure.adapters import VISAAdapter
from pymeasure.instruments.keithley import Keithley2182
from pymeasure.instruments.proterial import ROD4
from pymeasure.display.Qt import QtWidgets
from pymeasure.display.windows.managed_dock_window import ManagedDockWindow
from pymeasure.experiment import (
    Procedure, Parameter, IntegerParameter, FloatParameter, BooleanParameter,
    unique_filename, Results
)

from eurotherm2000 import Eurotherm2000
from biologic import GeneralPotentiostat, OCV, PEIS

sys.path.append('/home/connor/Documents/NUPyLab/nupylab/instruments/')
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
    ramp_rate = FloatParameter('Ramp Rate', units='C/min', default=5.0)
    dwell_time = FloatParameter('Dwell Time', units='min', default=30.0)

    pO2_toggle = BooleanParameter('pO2 Sensor Connected', default=True)
    pO2_slope = FloatParameter('pO2 Sensor Cal Slope', group_by='pO2_toggle')
    pO2_intercept = FloatParameter('pO2 Sensor Cal Intercept',
                                   group_by='pO2_toggle')

    mfc_1_setpoint = FloatParameter('MFC 1 Flow', units='cc/s')
    mfc_2_setpoint = FloatParameter('MFC 2 Flow', units='cc/s')
    mfc_3_setpoint = FloatParameter('MFC 3 Flow', units='cc/s')
    mfc_4_setpoint = FloatParameter('MFC 4 Flow', units='cc/s')

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
    points_per_decade = IntegerParameter('Points Per Decade', minimum=1,
                                         step=1, default=10,
                                         group_by='eis_toggle')

    DATA_COLUMNS = ['Time (s)',
                    'Furnace Temperature (C)',
                    'pO2 Sensor Temperature (C)',
                    'pO2 (atm)',
                    'MFC 1 Flow (cc/min)',
                    'MFC 2 Flow (cc/min)',
                    'MFC 3 Flow (cc/min)',
                    'MFC 4 Flow (cc/min)',
                    'Frequency (Hz)',
                    'Z\' (ohm)',
                    '-Z\" (ohm)']

    def startup(self):
        log.info("Connecting to instruments")
        if self.pO2_toggle:
            self.keithley_adapter = VISAAdapter(self.keithley_port,
                                                visa_library='@py')
            self.keithley = Keithley2182(self.keithley_adapter)
            self.initialize_keithley()

        self.rod4_adapter = VISAAdapter(self.rod4_port,
                                        visa_library='@py')
        self.rod4 = ROD4(self.rod4_adapter)
        self.rod4_range = (self.rod4.ch_1.mfc_range,
                           self.rod4.ch_2.mfc_range,
                           self.rod4.ch_3.mfc_range,
                           self.rod4.ch_4.mfc_range)

        self.eurotherm = Eurotherm2000(self.eurotherm_port,
                                       self.eurotherm_address)

        if self.eis_toggle:
            self.biologic = GeneralPotentiostat('SP200', self.biologic_port)
            self.biologic.connect()
            self.biologic.load_firmware((1,))
            ocv = OCV(duration=12*60*60,
                      record_every_dE=0.01,
                      record_every_dt=1.0,
                      E_range='KBIO_ERANGE_AUTO')
            freq_steps = round(10 * (np.log10(self.maximum_frequency) -
                                     np.log10(self.minimum_frequency)) + 1)
            self.peis = PEIS(initial_voltage_step=0,
                             duration_step=5.0,
                             vs_initial=False,
                             initial_frequency=self.maximum_frequency,
                             final_frequency=self.minimum_frequency,
                             logarithmic_spacing=True,
                             amplitude_voltage=self.amplitude_voltage,
                             frequency_number=freq_steps,
                             average_n_times=1,
                             wait_for_steady=1.0,
                             drift_correction=False,
                             record_every_dt=0.1,
                             record_every_dI=0.1,
                             I_range='KBIO_IRANGE_AUTO',
                             E_range='KBIO_ERANGE_2_5',
                             bandwidth='KBIO_BW_5')
            self.biologic.load_technique(0, ocv, first=True, last=True)

        sleep(1)

    def execute(self):
        log.info("Beginning measurement.")
        self.start_furnace()
        self.start_rod4()
        self.biologic.start_channel(0)
        (sensor_temp, sensor_pO2) = (None, None)
        (Ewe, frequency, Zre, Zim) = (None,)*4

        start_time = time()
        while not self.should_stop():
            loop_time = time()
            furnace_temp = self.eurotherm.process_value

            mfc_1_flow = self.rod4.ch_1.actual_flow * self.rod4_range[0]
            mfc_2_flow = self.rod4.ch_2.actual_flow * self.rod4_range[1]
            mfc_3_flow = self.rod4.ch_3.actual_flow * self.rod4_range[2]
            mfc_4_flow = self.rod4.ch_4.actual_flow * self.rod4_range[3]

            if self.pO2_toggle:
                (sensor_temp, sensor_pO2) = self.read_pO2_sensor()

            if self.eis_toggle:
                biologic_data = self.biologic.get_data(0)
            if self.eurotherm.program_status == 'run':
                pass

                data = {
                    'Time (s)': loop_time - start_time,
                    'Furnace Temperature (C)': furnace_temp,
                    'pO2 Sensor Temperature (C)': sensor_temp,
                    'pO2 (atm)': sensor_pO2,
                    'MFC 1 Flow (sccm)': mfc_1_flow,
                    'MFC 2 Flow (sccm)': mfc_2_flow,
                    'MFC 3 Flow (sccm)': mfc_3_flow,
                    'MFC 4 Flow (sccm)': mfc_4_flow,
                    'Ewe (V)': Ewe,
                    'Frequency (Hz)': frequency,
                    'Z\' (ohm)': Zre,
                    '-Z\" (ohm)': Zim
                }
                self.emit('results', data)
                self.emit('progress', )

            sleep(max(0, self.delay - (time() - loop_time)))
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

        if self.eis_toggle:
            self.biologic.disconnect()
        log.info("Finished")

    def initialize_keithley(self):
        self.keithley.reset()
        self.keithley.thermocouple = 'S'

    def start_furnace(self):
        """End any active program, ramp to setpoint and dwell."""
        self.eurotherm.program_status = 'reset'
        self.eurotherm.current_program = 1

        self.eurotherm.programs[1].segments[0]['Ramp Units'] = 'Mins'
        self.eurotherm.programs[1].segments[0]['Program Cycles'] = 0

        self.eurotherm.programs[1].segments[1]['Segment Type'] = 'Ramp Rate'
        self.eurotherm.programs[1].segments[1]['Ramp Rate'] = self.ramp_rate
        self.eurotherm.programs[1].segments[1]['Target Setpoint'] = \
            self.target_temperature

        self.eurotherm.programs[1].segments[3]['Segment Type'] = 'End'
        self.eurotherm.programs[1].segments[3]['End Type'] = 'Dwell'

        self.eurotherm.program_status = 'run'

    def start_rod4(self):
        """Convert setpoints from sccm to % and set flow"""

        self.rod4.ch_1.setpoint = self.mfc_1_setpoint / self.rod4_range[0]
        self.rod4.ch_2.setpoint = self.mfc_2_setpoint / self.rod4_range[1]
        self.rod4.ch_3.setpoint = self.mfc_3_setpoint / self.rod4_range[2]
        self.rod4.ch_4.setpoint = self.mfc_4_setpoint / self.rod4_range[3]

    def read_pO2_sensor(self):
        a = self.pO2_intercept
        b = self.pO2_slope
        self.keithley.ch_1.setup_voltage()
        voltage = -1 * self.keithley.voltage
        self.keithley.ch_2.setup_temperature()
        temp = self.keithley.temperature
        pO2 = 0.2095*10**(20158 * ((voltage - b) / (temp + 273.15) - a))
        return (temp, pO2)


class MainWindow(ManagedDockWindow):

    def __init__(self):
        inputs = ['delay', 'keithley_port', 'rod4_port', 'eurotherm_port',
                  'eurotherm_address', 'biologic_port', 'target_temperature',
                  'ramp_rate', 'dwell_time', 'pO2_toggle', 'pO2_slope',
                  'pO2_intercept', 'mfc_1_setpoint', 'mfc_2_setpoint',
                  'mfc_3_setpoint', 'mfc_4_setpoint', 'eis_toggle',
                  'maximum_frequency', 'minimum_frequency',
                  'amplitude_voltage', 'points_per_decade']
        super().__init__(
            procedure_class=Experiment,
            inputs=inputs,
            x_axis=['Z\' (Ohm)', 'Time (s)'],
            y_axis=['-Z\" (Ohm)',
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
