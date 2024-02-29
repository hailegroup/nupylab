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
from pymeasure.instruments.keithley import Keithley2400
from pymeasure.display.Qt import QtWidgets
from pymeasure.display.windows import ManagedWindow
from pymeasure.experiment import (
    Procedure, FloatParameter, unique_filename, Results
)

log = logging.getLogger('')
log.addHandler(logging.NullHandler())


class VProcedure(Procedure):

    delay = FloatParameter('Delay Time', units='s', default=0.5)
    voltage_range = FloatParameter('Voltage Range', units='V', default=10)

    DATA_COLUMNS = ['Time (s)', 'Voltage (V)']

    def startup(self):
        log.info("Setting up instrument")
        self.adapter = VISAAdapter('ASRL/dev/ttyUSB0::INSTR',
                                   visa_library='@py')
        self.sourcemeter = Keithley2400(self.adapter)
        self.sourcemeter.apply_current()
        self.sourcemeter.enable_source()
        self.sourcemeter.measure_voltage()
        sleep(2)

    def execute(self):
        log.info("Measuring voltage")
        time0 = time()
        while not self.should_stop():

            sleep(self.delay)

            voltage = self.sourcemeter.voltage
            time1 = time()

            data = {
                'Time (s)': time1 - time0,
                'Voltage (V)': voltage,
            }
            self.emit('results', data)
            self.emit('progress', (time1 - time0) % 60)
            if self.should_stop():
                log.warning("Catch stop command in procedure")
                break

    def shutdown(self):
        self.sourcemeter.shutdown()
        log.info("Finished")


class MainWindow(ManagedWindow):

    def __init__(self):
        super().__init__(
            procedure_class=VProcedure,
            inputs=['delay'],
            displays=['delay'],
            x_axis='Time (s)',
            y_axis='Voltage (V)'
        )
        self.setWindowTitle('V Measurement')

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
