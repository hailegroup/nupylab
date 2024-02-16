"""
Master GUI for multipurpose impedance station.

This GUI connects to and displays data from
    * ROD-4 MFC Controller
    * Eurotherm 2416 Furnace Controller
    * Keithley 2182 Nanovoltmeter (pO2 sensor, optional)
    * Biologic SP-200 Potentiostat (optional)

Run the program by changing to the directory containing this file and calling:

python multipurpose_impedance_station.py
"""

from datetime import datetime
import logging
import os
import sys
from threading import Thread
from time import sleep, time

import numpy as np
import pyvisa

from pymeasure.adapters import VISAAdapter
from pymeasure.display.Qt import QtWidgets
from pymeasure.display.windows.managed_dock_window import ManagedDockWindow
from pymeasure.experiment import (
    BooleanParameter, FloatParameter, IntegerParameter, ListParameter, Parameter,
    Procedure, Results, unique_filename)
from pymeasure.instruments.keithley import Keithley2182
from pymeasure.instruments.proterial import ROD4


# TODO: make nupylab installable module, remove path append
sys.path.append('/home/connor/Documents/NUPyLab/')
from nupylab.gui.parameter_table import ParameterTableWidget
from nupylab.instruments.biologic import GeneralPotentiostat, OCV, PEIS
from nupylab.instruments.eurotherm2000 import Eurotherm2000

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


class Experiment(Procedure):

    delay = FloatParameter('Record Time', units='s', default=1.0)

    rm = pyvisa.ResourceManager('@py')
    resources = rm.list_resources()

    keithley_port = ListParameter('Keithley 2182 Port',
                                  choices=resources,
                                  ui_class=None,
                                  group_by='pO2_toggle')
    rod4_port = ListParameter('ROD4 Port',
                              choices=resources,
                              ui_class=None)
    eurotherm_port = ListParameter('Eurotherm Port',
                                   choices=resources,
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

    pO2_toggle = BooleanParameter('pO2 Sensor Connected', default=True)
    pO2_slope = FloatParameter('pO2 Sensor Cal Slope', group_by='pO2_toggle')
    pO2_intercept = FloatParameter('pO2 Sensor Cal Intercept', group_by='pO2_toggle')

    start_time = FloatParameter('Start Time', maximum=1e12)
    num_steps = IntegerParameter('Number of Measurement Steps', default=1)
    current_step = IntegerParameter('Current Step', default=1)

    target_temperature = FloatParameter('Target Temperature', units='C', default=20)
    ramp_rate = FloatParameter('Ramp Rate', units='C/min', default=5)
    dwell_time = FloatParameter('Dwell Time', units='min', default=0)

    mfc_1_setpoint = FloatParameter('MFC 1 Setpoint', units='sccm', default=0)
    mfc_2_setpoint = FloatParameter('MFC 2 Setpoint', units='sccm', default=0)
    mfc_3_setpoint = FloatParameter('MFC 3 Setpoint', units='sccm', default=0)
    mfc_4_setpoint = FloatParameter('MFC 4 Setpoint', units='sccm', default=0)

    eis_toggle = BooleanParameter('Run EIS', default=True)
    maximum_frequency = FloatParameter('Maximum Frequency', units='Hz', default=100.0e3)
    minimum_frequency = FloatParameter('Minimum Frequency', units='Hz',
                                       default=1.0)
    amplitude_voltage = FloatParameter('Amplitude Voltage', units='V', default=0.02)
    points_per_decade = IntegerParameter('Points Per Decade', default=10)

    DATA_COLUMNS = ['Time (s)',
                    'Furnace Temperature (degC)',
                    'pO2 Sensor Temperature (degC)',
                    'pO2 (atm)',
                    'MFC 1 Flow (cc/min)',
                    'MFC 2 Flow (cc/min)',
                    'MFC 3 Flow (cc/min)',
                    'MFC 4 Flow (cc/min)',
                    'Frequency (Hz)',
                    'Z_re (ohm)',
                    '-Z_im (ohm)']

    def startup(self):
        """Connect to instruments and create OCV and EIS techniques."""
        log.info("Connecting to instruments...")

        self._initialize_rod4()
        # TODO: edit Eurotherm port name to be compatible with minimalmodbus
        self._initialize_eurotherm()
        if self.pO2_toggle:
            self._initialize_keithley()
        if self.eis_toggle:
            self._initialize_biologic()

        self.data = {
            'System Time': None,
            'Time (s)': None,
            'Furnace Temperature (degC)': None,
            'pO2 Sensor Temperature (degC)': None,
            'pO2 (atm)': None,
            'MFC 1 Flow (cc/min)': None,
            'MFC 2 Flow (cc/min)': None,
            'MFC 3 Flow (cc/min)': None,
            'MFC 4 Flow (cc/min)': None,
            'Ewe (V)': None,
            'Frequency (Hz)': None,
            'Z_re (ohm)': None,
            '-Z_im (ohm)': None
        }
        sleep(1)

    def execute(self):
        log.info(f"Running step {self.current_step} / {self.num_steps}.")
        # (Ewe, freq, Zre, Zim) = ((None,),)*4  # Important: defaults have length 1
        furnace_thread = Thread(target=self._start_furnace)
        rod4_thread = Thread(target=self._start_rod4)
        furnace_thread.start()
        rod4_thread.start()
        if self.eis_toggle:
            self.biologic.start_channel(0)
            self.measuring_ocv = True
        furnace_thread.join()
        rod4_thread.join()

        furnace_thread = Thread(target=self.sub_loop, args=(self._update_furnace))
        rod4_thread = Thread(target=self.sub_loop, args=(self._update_rod4))
        threads = [furnace_thread, rod4_thread,]
        if self.pO2_toggle:
            pO2_thread = Thread(target=self.sub_loop, args=(self._update_pO2))
            threads.append(pO2_thread)
        if self.eis_toggle:
            biologic_thread = Thread(target=self.sub_loop, args=(self._update_biologic))
            threads.append(biologic_thread)

        start_time = time()
        counter = 0
        for thread in threads:
            thread.start()

        while not self.should_stop():
            counter += 1
            self.data['Time (s)'] = self.delay * (counter - 1)
            self.data['System Time'] = str(datetime.now())

            if self.measuring_ocv:
                for i in range(len(Ewe)):
                    self.data['Ewe (V)'] = Ewe[i]
                self.data['Frequency (Hz)'] = freq[i]
                self.data['Z_re (ohm)'] = Zre[i]
                self.data['-Z_im (ohm)'] = -Zim[i]

                self.emit('results', self.data)
                self.emit('progress', )

            sleep_time = self.delay * counter - (time() - start_time)
            sleep(max(0, sleep_time))

            if self.should_stop():
                log.warning("Catch stop command in procedure")
                break

    def shutdown(self):
        """Shut off furnace and turn off gas flow."""
        if (self.status == (Procedure.FAILED or Procedure.ABORTED) or
                self.num_steps == self.current_step):
            try:
                self.eurotherm.program_status = 'reset'
                self.rod4.ch_1.valve_mode = 'close'
                self.rod4.ch_2.valve_mode = 'close'
                self.rod4.ch_3.valve_mode = 'close'
                self.rod4.ch_4.valve_mode = 'close'

                if self.eis_toggle:
                    self.biologic.stop_channel((1,))
                    self.biologic.disconnect()
            except AttributeError:
                log.warning('Unable to shut down instrument.')
            log.info("Shutdown complete.")

    def sub_loop(self, process, *args):
        """Implement generic sub-loop for concurrent instrument communication."""
        start_time = time()
        counter = 0
        while not self.should_stop():
            counter += 1
            process(*args)
            sleep_time = self.delay * counter - (time() - start_time)
            sleep(max(0, sleep_time))

    def _update_furnace(self):
        self.data['Furnace Temperature (degC)'] = self.eurotherm.process_value
        self.program_status = self.eurotherm.program_status

    def _update_rod4(self):
        self.data['MFC 1 Flow (cc/min)'] = (self.rod4.ch_1.actual_flow *
                                            self.rod4_range[0])
        self.data['MFC 2 Flow (cc/min)'] = (self.rod4.ch_2.actual_flow *
                                            self.rod4_range[1])
        self.data['MFC 3 Flow (cc/min)'] = (self.rod4.ch_3.actual_flow *
                                            self.rod4_range[2])
        self.data['MFC 4 Flow (cc/min)'] = (self.rod4.ch_4.actual_flow *
                                            self.rod4_range[3])

    def _update_pO2(self):
        """Convert measured sensor voltage to pO2.

        Requires calibrated slope and intercept of sensor voltage as a function of
        temperature in Celsius under dry air.
        """
        a = self.pO2_intercept
        b = self.pO2_slope
        self.keithley.ch_1.setup_voltage()
        voltage = -1 * self.keithley.voltage
        self.keithley.ch_2.setup_temperature()
        temp = self.keithley.temperature
        pO2 = 0.2095*10**(20158 * ((voltage - b) / (temp + 273.15) - a))
        self.data['pO2 Sensor Temperature (degC)'] = temp
        self.data['pO2 (atm)'] = pO2

    def _update_biologic(self):
        if measuring_ocv and self.program_status == "Complete":
            # Switch from OCV to PEIS upon completing program
            self.biologic.stop_channel((1,))
            self.biologic.load_technique(0, self.peis, first=True, last=True)
            self.biologic.start_channel((1,))
        biologic_data = self.biologic.get_data(0)
        if biologic_data is None:  # Wait until the Biologic has data
            continue
        Ewe = biologic_data['Ewe_numpy']
        if 'freq' in biologic_data:  # Measuring PEIS
            freq = biologic_data['freq_numpy']
            abs_Z = (biologic_data['abs_Ewe_numpy'] /
                     biologic_data['abs_I_numpy'])
            Z_phase = biologic_data['Phase_Zwe_numpy']
            Zre = abs_Z * np.cos(Z_phase)
            Zim = abs_Z * np.sin(Z_phase)

        for i in range(len(Ewe)):
            self.data['Ewe (V)'] = Ewe[i]
            self.data['Frequency (Hz)'] = freq[i]
            self.data['Z_re (ohm)'] = Zre[i]
            self.data['-Z_im (ohm)'] = -Zim[i]

    def _initialize_rod4(self):
        self.rod4_adapter = VISAAdapter(self.rod4_port, visa_library='@py')
        self.rod4 = ROD4(self.rod4_adapter)
        self.rod4_range = (self.rod4.ch_1.mfc_range,
                           self.rod4.ch_2.mfc_range,
                           self.rod4.ch_3.mfc_range,
                           self.rod4.ch_4.mfc_range)
        log.info("Connection to ROD-4 successful.")

    def _initialize_eurotherm(self):
        self.eurotherm = Eurotherm2000(self.eurotherm_port, self.eurotherm_address)
        log.info("Connection to Eurotherm successful.")

    def _initialize_keithley(self):
        """Reset Keithley 2182 to default measurement conditions and set TC type."""
        self.keithley_adapter = VISAAdapter(self.keithley_port, visa_library='@py')
        self.keithley = Keithley2182(self.keithley_adapter)
        self.keithley.reset()
        self.keithley.thermocouple = 'S'
        log.info("Connection to Keithley-2182 successful.")

    def _initialize_biologic(self):
        self.biologic = GeneralPotentiostat('SP200', self.biologic_port)
        self.biologic.connect()
        self.biologic.load_firmware((1,))

        ocv = OCV(duration=24*60*60,
                  record_every_dE=0,
                  record_every_dt=self.delay,
                  E_range='KBIO_ERANGE_AUTO')
        self.biologic.load_technique(0, ocv, first=True, last=True)

        freq_steps = (np.log10(self.maximum_frequency) -
                      np.log10(self.minimum_frequency))
        freq_steps = round(freq_steps * self.points_per_decade) + 1
        self.peis = PEIS(initial_voltage_step=0,
                         duration_step=0,
                         vs_initial=False,
                         initial_frequency=self.maximum_frequency,
                         final_frequency=self.minimum_frequency,
                         logarithmic_spacing=True,
                         amplitude_voltage=self.amplitude_voltage,
                         frequency_number=freq_steps,
                         average_n_times=1,
                         wait_for_steady=1.0,
                         drift_correction=False,
                         record_every_dt=self.delay,
                         record_every_dI=0.1,
                         I_range='KBIO_IRANGE_AUTO',
                         E_range='KBIO_ERANGE_2_5',
                         bandwidth='KBIO_BW_5')
        log.info("Connection to Biologic successful.")

    def _start_furnace(self):
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

    def _start_rod4(self):
        """Convert setpoints from sccm to % and set flow."""
        self.rod4.ch_1.setpoint = self.mfc_setpoints[0] / self.rod4_range[0]
        self.rod4.ch_2.setpoint = self.mfc_setpoints[1] / self.rod4_range[1]
        self.rod4.ch_3.setpoint = self.mfc_setpoints[2] / self.rod4_range[2]
        self.rod4.ch_4.setpoint = self.mfc_setpoints[3] / self.rod4_range[3]


class MainWindow(ManagedDockWindow):
    """Main GUI window.

    NOTE: The column order in the loaded experiment parameters table **must match**
    the order in which they are listed in table_parameters attribute below.
    """

    parameter_types = {'target_temperature': float,
                       'ramp_rate': float,
                       'dwell_time': float,
                       'mfc_1_setpoint': float,
                       'mfc_2_setpoint': float,
                       'mfc_3_setpoint': float,
                       'mfc_4_setpoint': float,
                       'eis_toggle': bool,
                       'maximum_frequency': float,
                       'minimum_frequency': float,
                       'amplitude_voltage': float,
                       'points_per_decade': int}

    def __init__(self):
        inputs = ['delay',
                  'rod4_port',
                  'eurotherm_port',
                  'eurotherm_address',
                  'biologic_port',
                  'pO2_toggle',
                  'keithley_port',
                  'pO2_slope',
                  'pO2_intercept',]
        super().__init__(
            procedure_class=Experiment,
            x_axis=['Z_re (ohm)', 'Time (s)'],
            y_axis=['-Z_im (ohm)',
                    'Furnace Temperature (degC)',
                    'pO2 Sensor Temperature (degC)',
                    'pO2 (atm)',
                    'MFC 1 Flow (cc/min)',
                    'MFC 2 Flow (cc/min)',
                    'MFC 3 Flow (cc/min)',
                    'MFC 4 Flow (cc/min)'],
            inputs=inputs,
            inputs_in_scrollarea=True,
            widget_list=(ParameterTableWidget("Experiment Parameters"),)
        )
        self.setWindowTitle('Multipurpose Impedance Station')

    def verify_parameters(self, table_df):
        """Verify shape of dataframe and attempt to convert datatype.

        Args:
            table_df: Pandas dataframe representing parameters table in string format

        Returns:
            converted_df: parameters table with each column converted to the dtype
                specified in self.parameter_types

        Raises:
            IndexError: if the number of columns in the parameter table does not match
                the number of parameters in self.parameter_types
            ValueError: if the parameters table cannot be converted to the types listed
                in self.parameter_types
        """
        if len(self.parameter_types) != table_df.shape[1]:
            raise IndexError(f"Expected {len(self.parameter_types)} parameters, but "
                             f"parameters table has {table_df.shape[1]} columns.")

        converted_df = table_df.astype(
            {label: dtype for label, dtype in
             zip(table_df.columns, self.parameter_types.values())}
        )
        return converted_df

    def set_parameters(self, procedure, parameters, start_time, num_steps, current_step):
        # TODO: Clean this up
        (procedure.target_temperature,
         procedure.ramp_rate,
         procedure.dwell_time,
         procedure.mfc_1_setpoint,
         procedure.mfc_2_setpoint,
         procedure.mfc_3_setpoint,
         procedure.mfc_4_setpoint,
         procedure.eis_toggle,
         procedure.maximum_frequency,
         procedure.minimum_frequency,
         procedure.amplitude_voltage,
         procedure.points_per_decade) = parameters
        procedure.start_time = start_time
        procedure.num_steps = num_steps
        procedure.current_step = current_step
        pass

    def queue(self):
        """Queue all rows in parameters table. Overwrites parent method."""
        log.info("Reading experiment parameters.")
        table_widget = self.tabs.widget(0)
        table_df = table_widget.table.model().export_df()
        converted_df = self.verify_parameters(table_df)

        start_time = time()
        num_steps = converted_df.shape[0]
        current_step = 0

        for parameters in converted_df.itertuples(index=False):
            procedure = self.make_procedure()
            self.set_parameters(procedure,
                                parameters,
                                start_time,
                                num_steps,
                                current_step)
            current_step += 1
            filename = unique_filename(self.directory,
                                       prefix=self.file_input.filename_base + "_",
                                       suffix=("_{Current Step+1}" +
                                               "_{Target Temperature:.0f}"),
                                       ext='csv',
                                       dated_folder=False,
                                       index=False,
                                       procedure=procedure)
            index = 2
            basename = filename.split('.csv')[0]
            while os.path.exists(filename):
                filename = f"{basename}_{index}.csv"
                index += 1

            results = Results(procedure, filename)
            experiment = self.new_experiment(results)

            self.manager.queue(experiment)


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
