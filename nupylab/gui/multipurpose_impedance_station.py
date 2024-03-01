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

from __future__ import annotations
from collections import namedtuple
from datetime import datetime
import logging
import os
from pint import UnitRegistry
from queue import Empty, SimpleQueue
import sys
from threading import Thread
from time import sleep, time
from typing import Callable, List, TYPE_CHECKING

import numpy as np
import pyvisa

from pymeasure.display.Qt import QtWidgets
from pymeasure.display.windows.managed_dock_window import ManagedDockWindow
from pymeasure.experiment import (
    BooleanParameter, FloatParameter, IntegerParameter, ListParameter, Parameter,
    Procedure, Results, unique_filename)
from pymeasure.instruments.keithley import Keithley2182
from pymeasure.instruments.proterial import ROD4


# TODO: make nupylab installable module, remove path append
# sys.path.append('/home/connor/Documents/NUPyLab/')
sys.path.append(r"C:\Users\PROB-E\Desktop\NUPyLab")
from nupylab.gui.parameter_table import ParameterTableWidget
from nupylab.instruments.biologic import GeneralPotentiostat, OCV, PEIS
from nupylab.instruments.eurotherm2000 import Eurotherm2000

if TYPE_CHECKING:
    import pandas as pd

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

ResultTuple = namedtuple('ResultTuple', ['label', 'value'])


class Experiment(Procedure):
    """Procedure for running multipurpose impedance station GUI.

    Running this procedure calls startup, execute, and shutdown methods sequentially.
    """

    delay = FloatParameter('Record Time', units='s', default=2.0)

    # rm = pyvisa.ResourceManager('@py')
    rm = pyvisa.ResourceManager()
    resources = rm.list_resources()

    keithley_port = ListParameter(
        'Keithley 2182 Port', choices=resources, ui_class=None, group_by='pO2_toggle'
    )
    rod4_port = ListParameter('ROD4 Port', choices=resources, ui_class=None)
    eurotherm_port = ListParameter('Eurotherm Port', choices=resources, ui_class=None)
    eurotherm_address = IntegerParameter(
        'Eurotherm Address', minimum=1, maximum=254, step=1, default=1
    )
    biologic_port = Parameter(
        'Biologic Port', default='192.109.209.128', ui_class=None, group_by='eis_toggle'
    )

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
    minimum_frequency = FloatParameter('Minimum Frequency', units='Hz', default=1.0)
    amplitude_voltage = FloatParameter('Amplitude Voltage', units='V', default=0.02)
    points_per_decade = IntegerParameter('Points Per Decade', default=10)

    # Note: units in parentheses must be valid pint units
    DATA_COLUMNS: List[str] = ['System Time',
                               'Time (s)',
                               'Furnace Temperature (degC)',
                               'pO2 Sensor Temperature (degC)',
                               'pO2 (atm)',
                               'MFC 1 Flow (cc/min)',
                               'MFC 2 Flow (cc/min)',
                               'MFC 3 Flow (cc/min)',
                               'MFC 4 Flow (cc/min)',
                               'Ewe (V)',
                               'Frequency (Hz)',
                               'Z_re (ohm)',
                               '-Z_im (ohm)']

    def startup(self) -> None:
        """Connect to instruments and start furnace, gas flow, and OCV measurement."""
        log.info("Connecting to instruments...")

        self._initialize_rod4()
        self._initialize_eurotherm()
        if self.pO2_toggle:
            self._initialize_keithley()
        if self.eis_toggle:
            self._initialize_biologic()
            self.biologic.start_channel(0)
            self._measuring_ocv = True
        # Initialize values with NaN and appropriate pint dimension to avoid complaints
        ureg = UnitRegistry()
        self.data = {
            'System Time': None,
            'Time (s)': np.nan * ureg.second,
            'Furnace Temperature (degC)': np.nan * ureg.K,
            'pO2 Sensor Temperature (degC)': np.nan * ureg.K,
            'pO2 (atm)': np.nan * ureg.atm,
            'MFC 1 Flow (cc/min)': np.nan * ureg.cc / ureg.min,
            'MFC 2 Flow (cc/min)': np.nan * ureg.cc / ureg.min,
            'MFC 3 Flow (cc/min)': np.nan * ureg.cc / ureg.min,
            'MFC 4 Flow (cc/min)': np.nan * ureg.cc / ureg.min,
            'Ewe (V)': np.nan * ureg.V,
            'Frequency (Hz)': np.nan * ureg.Hz,
            'Z_re (ohm)': np.nan * ureg.ohm,
            '-Z_im (ohm)': np.nan * ureg.ohm
        }
        furnace_thread = Thread(target=self._start_furnace)
        rod4_thread = Thread(target=self._start_rod4)
        furnace_thread.start()
        rod4_thread.start()
        furnace_thread.join()
        rod4_thread.join()
        sleep(1)  # Give Eurotherm time to get program running

    def execute(self) -> None:
        """Loop through thread for each instrument and emit results."""
        log.info(f"Running step {self.current_step} / {self.num_steps}.")
        furnace_queue: SimpleQueue = SimpleQueue()
        rod4_queue: SimpleQueue = SimpleQueue()
        queues: List[SimpleQueue] = [furnace_queue, rod4_queue,]

        furnace_thread = Thread(
            target=self._sub_loop, args=(self._update_furnace, furnace_queue)
        )
        rod4_thread = Thread(
            target=self._sub_loop, args=(self._update_rod4, rod4_queue)
        )
        threads: List[Thread] = [furnace_thread, rod4_thread,]

        if self.pO2_toggle:
            self._ch_1_first: bool = True
            pO2_queue: SimpleQueue = SimpleQueue()
            pO2_thread = Thread(
                target=self._sub_loop, args=(self._update_pO2, pO2_queue)
            )
            queues.append(pO2_queue)
            threads.append(pO2_thread)

        if self.eis_toggle:
            biologic_queue: SimpleQueue = SimpleQueue()
            biologic_thread = Thread(
                target=self._sub_loop, args=(self._update_biologic, biologic_queue)
            )
            queues.append(biologic_queue)
            threads.append(biologic_thread)

        self._counter: int = 0
        self._finished: bool = False
        self._start_time: float = time()
        for thread in threads:
            thread.start()

        while True:
            self._counter += 1
            sleep_time: float = self.delay * self._counter - (time() - self._start_time)
            sleep(max(0, sleep_time))
            _ = self._emit_results(queues)  # Emit after other threads have run

            if self.should_stop():
                log.warning("Catch stop command in procedure")
                break
            elif self._finished:
                while self._emit_results(queues) != 0:  # Flush queues
                    continue
                log.info(f"Step {self.current_step} / {self.num_steps} complete.")
                break
        for thread in threads:
            thread.join()

    def shutdown(self) -> None:
        """Shut off furnace and turn off gas flow."""
        # TODO: create threads for closing instruments simultaneously, not sequentially
        try:
            if self.eis_toggle:
                self.biologic.stop_channel(0)
                self.biologic.disconnect()
            if self.pO2_toggle:
                self.keithley.adapter.close()
            if (self.status == (Procedure.FAILED or Procedure.ABORTED) or
                    self.num_steps == self.current_step):
                self.eurotherm.program_status = 'reset'
                for channel in self.rod4.channels.values():
                    channel.valve_mode = 'close'
                log.info("Shutdown complete.")
            self.eurotherm.serial.close()
            self.rod4.adapter.close()
        except AttributeError:
            log.warning('Error shutting down instruments.')

    def get_estimates(self, sequence_length=None, sequence=None) -> float:
        """Get estimate for measurement duration in seconds."""
        if not hasattr(self, 'eurotherm'):  # Unable to read starting temperature
            return 0
        if hasattr(self, 'biologic') and self.eis_toggle:
            return self._furnace_time + self._biologic_time
        return self._furnace_time

    def _sub_loop(self, process: Callable[..., None], *args) -> None:
        """Implement generic sub-loop for concurrent instrument communication.

        All sub-loops are synchronized with the main loop.

        Args:
            process: function to loop, typically an instrument read.
            *args: additional args to pass to `process`
        """
        while not self.should_stop() and not self._finished:
            process(*args)
            sleep_time = self.delay * self._counter - (time() - self._start_time)
            sleep(max(0, sleep_time))

    def _parse_results(self, result: ResultTuple) -> None:
        """Write value to class data if single-valued, otherwise postpone extraction."""
        if not hasattr(result.value, '__len__'):
            self.data[result.label] = result.value
        elif len(result.value) == 1:
            self.data[result.label] = result.value[0]
        else:
            self._multivalue_results.append(result)

    def _emit_results(self, queues: List[SimpleQueue]) -> int:
        """Emit most recent set of results from all queues.

        Args:
            queues: list of queues.

        Returns:
            the number of queues that contained results.
        """
        filled_queues: int = 0
        self.data['Time (s)'] = self.delay * (self._counter - 1)
        self.data['System Time'] = str(datetime.now())
        self._multivalue_results: List[ResultTuple] = []
        for q in queues:
            try:
                results: tuple = q.get_nowait()
            except Empty:
                continue
            filled_queues += 1
            if isinstance(results, ResultTuple):
                self._parse_results(results)
            else:
                for result in results:
                    self._parse_results(result)

        if len(self._multivalue_results) == 0:
            self.emit('results', self.data)
            self.emit('progress', 0)
            return filled_queues

        index: int = max([len(result.value) for result in self._multivalue_results])
        for i in range(index):
            for result in self._multivalue_results:
                if len(result.value) < i + 1:
                    continue
                self.data[result.label] = result.value[i]
            self.emit('results', self.data)
        self.emit('progress', 0)
        return filled_queues

    def _update_furnace(self, furnace_queue: SimpleQueue) -> None:
        """Read furnace temperature and program status.

        Args:
            furnace_queue: queue for holding furnace temperature measurements.
        """
        temperature: float = self.eurotherm.process_value
        status: str = self.eurotherm.program_status
        self._furnace_running = (status == 'run')
        if not self.eis_toggle:
            self._finished = not self._furnace_running
        furnace_queue.put(ResultTuple('Furnace Temperature (degC)', temperature))

    def _update_rod4(self, rod4_queue: SimpleQueue) -> None:
        """Read flow for each MFC channel.

        Args:
            rod4_queue: queue for holding MFC flow measurements.
        """
        mfc: List[float] = []
        for channel, range_ in zip(self.rod4.channels.values(), self._rod4_range):
            mfc.append(channel.actual_flow * range_ / 100)
        rod4_queue.put(
            (ResultTuple('MFC 1 Flow (cc/min)', mfc[0]),
             ResultTuple('MFC 2 Flow (cc/min)', mfc[1]),
             ResultTuple('MFC 3 Flow (cc/min)', mfc[2]),
             ResultTuple('MFC 4 Flow (cc/min)', mfc[3]))
        )

    def _update_pO2(self, pO2_queue: SimpleQueue) -> None:
        """Convert measured sensor voltage to pO2.

        Requires calibrated slope and intercept of sensor voltage as a function of
        temperature in Celsius under dry air.

        Args:
            pO2_queue: queue for holding sensor temperature and pO2 measurements.
        """
        a: float = self.pO2_intercept
        b: float = self.pO2_slope
        voltage: float
        temperature: float
        pO2: float
        # Toggle between which channel is measured first to speed up measurement cycle
        if self._ch_1_first:
            voltage = -1 * self.keithley.voltage
            self.keithley.ch_2.setup_temperature()
            temperature = self.keithley.temperature
            self._ch_1_first = False
        else:
            temperature = self.keithley.temperature
            self.keithley.ch_1.setup_voltage()
            voltage = -1 * self.keithley.voltage
            self._ch_1_first = True
        pO2 = 0.2095 * 10**(20158 * ((voltage - b) / (temperature + 273.15) - a))
        pO2_queue.put(
            (ResultTuple('pO2 Sensor Temperature (degC)', temperature),
             ResultTuple('pO2 (atm)', pO2))
        )

    def _update_biologic(self, biologic_queue: SimpleQueue) -> None:
        kbio_data = self.biologic.get_data(0)
        if self._measuring_ocv and not self._furnace_running:
            # Switch from OCV to PEIS upon completing furnace program
            self.biologic.stop_channel(0)
            self.biologic.load_technique(0, self.peis, first=True, last=True)
            self.biologic.start_channel(0)
            self._measuring_ocv = False

        if kbio_data is None:
            return

        if 'freq' in kbio_data.data_field_names:  # Measuring PEIS
            abs_Z = kbio_data.abs_Ewe_numpy / kbio_data.abs_I_numpy
            Z_phase = kbio_data.Phase_Zwe_numpy
            Zre = abs_Z * np.cos(Z_phase)
            Zim = abs_Z * np.sin(Z_phase)
            biologic_queue.put(
                (ResultTuple('Ewe (V)', kbio_data.Ewe),
                 ResultTuple('Frequency (Hz)', kbio_data.freq),
                 ResultTuple('Z_re (ohm)', Zre),
                 ResultTuple('-Z_im (ohm)', Zim))
            )
        else:
            biologic_queue.put(ResultTuple('Ewe (V)', kbio_data.Ewe))
        channel_infos = self.biologic.get_channel_infos(0)
        self._finished = (channel_infos['State'] == 0)

    def _initialize_rod4(self) -> None:
        self.rod4 = ROD4(self.rod4_port)
        self._rod4_range = tuple(
            channel.mfc_range for channel in self.rod4.channels.values()
        )
        self._mfc_setpoints: tuple = (
            self.mfc_1_setpoint,
            self.mfc_2_setpoint,
            self.mfc_3_setpoint,
            self.mfc_4_setpoint
        )
        log.info("Connection to ROD-4 successful.")

    def _initialize_eurotherm(self) -> None:
        """Convert 'ASRL##::INSTR' to form 'COM##'."""
        port: str = self.eurotherm_port.replace('ASRL', 'COM').replace('::INSTR', '')
        self.eurotherm = Eurotherm2000(port, self.eurotherm_address)
        self._furnace_time: float = 60 * (
            self.dwell_time +
            (self.target_temperature - self.eurotherm.process_value) / self.ramp_rate)
        log.info("Connection to Eurotherm successful.")

    def _initialize_keithley(self) -> None:
        """Reset Keithley 2182 to default measurement conditions and set TC type."""
        self.keithley = Keithley2182(self.keithley_port)
        self.keithley.reset()
        self.keithley.thermocouple = 'S'
        self.keithley.ch_1.setup_voltage()
        log.info("Connection to Keithley-2182 successful.")

    def _initialize_biologic(self) -> None:
        self.biologic = GeneralPotentiostat('SP200', self.biologic_port, None)
        self.biologic.connect()
        self.biologic.load_firmware((1,))

        ocv = OCV(duration=24*60*60,
                  record_every_dE=0.1,
                  record_every_dt=self.delay,
                  E_range='KBIO_ERANGE_AUTO')
        self.biologic.load_technique(0, ocv, first=True, last=True)

        freq_steps: int = (
            (max_log_f := np.log10(self.maximum_frequency)) -
            (min_log_f := np.log10(self.minimum_frequency))
        )
        freq_steps = round(freq_steps * self.points_per_decade) + 1
        self._biologic_time: float = np.sum(
            1 / np.logspace(max_log_f, min_log_f, freq_steps)
        )
        self.peis = PEIS(
            initial_voltage_step=0,
            duration_step=1,
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
            bandwidth='KBIO_BW_5'
        )
        log.info("Connection to Biologic successful.")

    def _start_furnace(self) -> None:
        """End any active program, ramp to setpoint and dwell."""
        self.eurotherm.program_status = 'reset'
        self.eurotherm.current_program = 1
        self.eurotherm.programs[1].refresh()

        self.eurotherm.programs[1].segments[1]['segment type'] = 'ramp rate'
        self.eurotherm.programs[1].segments[1]['rate'] = self.ramp_rate
        self.eurotherm.programs[1].segments[1]['target setpoint'] = \
            self.target_temperature

        self.eurotherm.programs[1].segments[2]['segment type'] = 'dwell'
        self.eurotherm.programs[1].segments[2]['duration'] = 30.

        self.eurotherm.programs[1].segments[3]['segment type'] = 'end'
        self.eurotherm.programs[1].segments[3]['end type'] = 'dwell'

        self.eurotherm.program_status = 'run'
        self._furnace_running = True

    def _start_rod4(self) -> None:
        """Convert setpoints from sccm to % and set flow."""
        for channel, setpoint, range_ in zip(self.rod4.channels.values(),
                                             self._mfc_setpoints,
                                             self._rod4_range):
            channel.setpoint = 100 * setpoint / range_
            if setpoint == 0:
                channel.valve_mode = 'close'
            else:
                channel.valve_mode = 'flow'


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

    def __init__(self) -> None:
        """Initialize main window GUI."""
        inputs = [
            'delay',
            'rod4_port',
            'eurotherm_port',
            'eurotherm_address',
            'biologic_port',
            'pO2_toggle',
            'keithley_port',
            'pO2_slope',
            'pO2_intercept',
        ]
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

    def verify_parameters(self, table_df: pd.DataFrame) -> pd.DataFrame:
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

    def set_parameters(
            self, procedure, parameters, start_time, num_steps, current_step
    ) -> None:
        """Unpack table parameters and set corresponding values in procedure."""
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

    def queue(self) -> None:
        """Queue all rows in parameters table. Overwrites parent method."""
        log.info("Reading experiment parameters.")
        table_widget = self.tabs.widget(0)
        table_df = table_widget.table.model().export_df()
        table_df.replace({'True': True, 'TRUE': True, 'true': True,
                          'False': False, 'FALSE': False, 'false': False}, inplace=True)
        converted_df: pd.DataFrame = self.verify_parameters(table_df)

        start_time: float = time()
        num_steps: int = converted_df.shape[0]
        current_step: int = 1

        for parameters in converted_df.itertuples(index=False):
            procedure = self.make_procedure()
            self.set_parameters(procedure,
                                parameters,
                                start_time,
                                num_steps,
                                current_step)
            current_step += 1
            filename: str = unique_filename(
                self.directory,
                prefix=self.file_input.filename_base + "_",
                suffix="_{Current Step}" + "_{Target Temperature:.0f}",
                ext='csv',
                dated_folder=False,
                index=False,
                procedure=procedure
            )
            index: int = 2
            basename: str = filename.split('.csv')[0]
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
