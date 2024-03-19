"""
GUI for high-impedance station.

This GUI connects to and displays data from
    * Eurotherm 2216e Furnace Controller
    * Biologic SP-200 Potentiostat (optional)

Run the program by changing to the directory containing this file and calling:

python S8_biologic_eurotherm2200.py
"""

from __future__ import annotations
from collections import namedtuple
from datetime import datetime
import logging
import os
from pint import UnitRegistry
from queue import Empty
import sys
from threading import Thread
from time import sleep, monotonic
from typing import Callable, List, TYPE_CHECKING

import numpy as np
import pyvisa

from pymeasure.display.Qt import QtWidgets
from pymeasure.display.windows.managed_dock_window import ManagedDockWindow
from pymeasure.experiment import (
    BooleanParameter, FloatParameter, IntegerParameter, ListParameter, Parameter,
    Procedure, Results, unique_filename)

from nupylab.instruments import BiologicPotentiostat, Eurotherm2200
from nupylab.instruments.biologic import OCV, PEIS
from nupylab.utilities import DefaultQueue, ParameterTableWidget


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

    rm = pyvisa.ResourceManager('@py')
    # rm = pyvisa.ResourceManager()
    resources = rm.list_resources()

    eurotherm_port = ListParameter('Eurotherm Port', choices=resources, ui_class=None)
    eurotherm_address = IntegerParameter(
        'Eurotherm Address', minimum=1, maximum=254, step=1, default=1
    )
    biologic_port = Parameter(
        'Biologic Port', default='192.109.209.128', ui_class=None, group_by='eis_toggle'
    )

    start_time = FloatParameter('Start Time', maximum=1e12)
    num_steps = IntegerParameter('Number of Measurement Steps', default=1)
    current_step = IntegerParameter('Current Step', default=1)

    target_temperature = FloatParameter('Target Temperature', units='C', default=20)
    ramp_rate = FloatParameter('Ramp Rate', units='C/min', default=5)
    dwell_time = FloatParameter('Dwell Time', units='min', default=0)

    eis_toggle = BooleanParameter('Run EIS', default=True)
    maximum_frequency = FloatParameter('Maximum Frequency', units='Hz', default=100.0e3)
    minimum_frequency = FloatParameter('Minimum Frequency', units='Hz', default=1.0)
    amplitude_voltage = FloatParameter('Amplitude Voltage', units='V', default=0.02)
    points_per_decade = IntegerParameter('Points Per Decade', default=10)

    # Note: units in parentheses must be valid pint units
    DATA_COLUMNS: List[str] = ['System Time',
                               'Time (s)',
                               'Furnace Temperature (degC)',
                               'Ewe (V)',
                               'Frequency (Hz)',
                               'Z_re (ohm)',
                               '-Z_im (ohm)']

    def startup(self) -> None:
        """Connect to instruments and start furnace, gas flow, and OCV measurement."""
        log.info("Connecting to instruments...")

        self._initialize_furnace()
        if self.eis_toggle:
            self._initialize_biologic()
        # Initialize values with NaN and appropriate pint dimension to avoid complaints
        self.ureg = UnitRegistry()
        ureg = self.ureg
        self.data = {
            'System Time': None,
            'Time (s)': np.nan * ureg.second,
            'Furnace Temperature (degC)': np.nan * ureg.K,
            'Ewe (V)': np.nan * ureg.V,
            'Frequency (Hz)': np.nan * ureg.Hz,
            'Z_re (ohm)': np.nan * ureg.ohm,
            '-Z_im (ohm)': np.nan * ureg.ohm
        }
        self._start_furnace()
        sleep(1)  # Give Eurotherm time to get program running
        if self.eis_toggle:
            self.biologic.start_channel(0)
            self._measuring_ocv = True

    def execute(self) -> None:
        """Loop through thread for each instrument and emit results."""
        log.info(f"Running step {self.current_step} / {self.num_steps}.")
        ureg = self.ureg
        furnace_queue: DefaultQueue = DefaultQueue(
            ResultTuple('Furnace Temperature (degC)', np.nan * ureg.K)
        )
        queues: List[DefaultQueue] = [furnace_queue,]

        furnace_thread = Thread(
            target=self._sub_loop, args=(self._update_furnace, furnace_queue)
        )
        threads: List[Thread] = [furnace_thread,]

        if self.eis_toggle:
            biologic_queue: DefaultQueue = DefaultQueue(
                (
                    ResultTuple('Ewe (V)', np.nan * ureg.V),
                    ResultTuple('Frequency (Hz)', np.nan * ureg.Hz),
                    ResultTuple('Z_re (ohm)', np.nan * ureg.ohm),
                    ResultTuple('-Z_im (ohm)', np.nan * ureg.ohm)
                 )
            )
            biologic_thread = Thread(
                target=self._sub_loop, args=(self._update_biologic, biologic_queue)
            )
            queues.append(biologic_queue)
            threads.append(biologic_thread)

        self._counter: int = 0
        self._finished: bool = False
        self._start_time: float = monotonic()
        for thread in threads:
            thread.start()

        while True:
            self._counter += 1
            sleep_time: float = self.delay * self._counter - (monotonic() - self._start_time)
            sleep(max(0, sleep_time))
            self._emit_results(queues)  # Emit after other threads have run

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
            if (self.status == (Procedure.FAILED or Procedure.ABORTED) or
                    self.num_steps == self.current_step):
                self.eurotherm.program_status = 'reset'
                log.info("Shutdown complete.")
            self.eurotherm.serial.close()
        except AttributeError:
            log.warning('Error shutting down instruments.')

    def _sub_loop(self, process: Callable[..., None], *args) -> None:
        """Implement generic sub-loop for concurrent instrument communication.

        All sub-loops are synchronized with the main loop.

        Args:
            process: function to loop, typically an instrument read.
            *args: additional args to pass to `process`
        """
        while not self.should_stop() and not self._finished:
            process(*args)
            sleep_time = self.delay * self._counter - (monotonic() - self._start_time)
            sleep(max(0, sleep_time))

    def _parse_results(self, result: ResultTuple) -> None:
        """Write value to class data if single-valued, otherwise postpone extraction."""
        if not hasattr(result.value, '__len__'):
            self.data[result.label] = result.value
        elif len(result.value) == 1:
            self.data[result.label] = result.value[0]
        else:
            self._multivalue_results.append(result)

    def _emit_results(self, queues: List[DefaultQueue]) -> int:
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
                filled_queues += 1
            except Empty:
                results = q.default
            if isinstance(results, ResultTuple):
                self._parse_results(results)
            else:
                for result in results:
                    self._parse_results(result)

        if filled_queues == 0:
            return filled_queues

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

    def _update_furnace(self, furnace_queue: DefaultQueue) -> None:
        """Read furnace temperature and program status.

        Args:
            furnace_queue: queue for holding furnace temperature measurements.
        """
        temperature: float = self.eurotherm.process_value
        status: str = self.eurotherm.program_status
        self._furnace_running = (status not in ('off', 'end'))
        if not self.eis_toggle:
            self._finished = not self._furnace_running
        furnace_queue.put(ResultTuple('Furnace Temperature (degC)', temperature))

    def _update_biologic(self, biologic_queue: DefaultQueue) -> None:
        kbio_data = self.biologic.get_data(0)
        ureg = self.ureg
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
                (
                    ResultTuple('Ewe (V)', kbio_data.Ewe),
                    ResultTuple('Frequency (Hz)', kbio_data.freq),
                    ResultTuple('Z_re (ohm)', Zre),
                    ResultTuple('-Z_im (ohm)', -Zim)
                )
            )
        else:
            biologic_queue.put(
                (
                    ResultTuple('Ewe (V)', kbio_data.Ewe),
                    ResultTuple('Frequency (Hz)', np.nan * ureg.Hz),
                    ResultTuple('Z_re (ohm)', np.nan * ureg.ohm),
                    ResultTuple('-Z_im (ohm)', np.nan * ureg.ohm)
                )
            )
        channel_infos = self.biologic.get_channel_infos(0)
        self._finished = (channel_infos['State'] == 0)

    def _initialize_furnace(self) -> None:
        """Convert 'ASRL##::INSTR' to form 'COM##'."""
        port: str = self.eurotherm_port.replace('ASRL', 'COM').replace('::INSTR', '')
        self.eurotherm = Eurotherm2200(port, self.eurotherm_address)
        self._furnace_time: float = 60 * (
            self.dwell_time +
            (self.target_temperature - self.eurotherm.process_value) / self.ramp_rate)
        log.info("Connection to Eurotherm successful.")

    def _initialize_biologic(self) -> None:
        self.biologic = BiologicPotentiostat('SP200', self.biologic_port, None)
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
        self.eurotherm.active_setpoint = 1
        self.eurotherm.end_type = 'dwell'
        self.eurotherm.setpoint_rate_limit = self.ramp_rate
        self.eurotherm.setpoint2 = self.target_temperature
        # Dwell must be non-zero for program to work
        self.eurotherm.dwell_time = self.dwell_time * 60 + 1
        self.eurotherm.program_status = 'run'
        self._furnace_running = True


class MainWindow(ManagedDockWindow):
    """Main GUI window.

    NOTE: The column order in the loaded experiment parameters table **must match**
    the order in which they are listed in table_parameters attribute below.
    """

    parameter_types = {'target_temperature': float,
                       'ramp_rate': float,
                       'dwell_time': float,
                       'eis_toggle': bool,
                       'maximum_frequency': float,
                       'minimum_frequency': float,
                       'amplitude_voltage': float,
                       'points_per_decade': int}

    table_columns = (
        "Target Temperature [C]",
        "Ramp Rate [C/min]",
        "Dwell Time [min]",
        "EIS?",
        "Maximum Frequency [Hz]",
        "Minimum Frequency [Hz]",
        "Amplitude Voltage [V]",
        "Points per Decade",
    )

    def __init__(self) -> None:
        """Initialize main window GUI."""
        inputs = [
            'delay',
            'eurotherm_port',
            'eurotherm_address',
            'biologic_port',
        ]
        super().__init__(
            procedure_class=Experiment,
            x_axis=['Z_re (ohm)', 'Time (s)'],
            y_axis=['-Z_im (ohm)',
                    'Ewe (V)',
                    'Furnace Temperature (degC)',],
            inputs=inputs,
            inputs_in_scrollarea=True,
            widget_list=(
                ParameterTableWidget("Experiment Parameters", self.table_columns),
            )
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
         procedure.eis_toggle,
         procedure.maximum_frequency,
         procedure.minimum_frequency,
         procedure.amplitude_voltage,
         procedure.points_per_decade) = parameters
        procedure.start_time = start_time
        procedure.num_steps = num_steps
        procedure.current_step = current_step

    def queue(self) -> None:
        """Queue all rows in parameters table. Overwrites parent method."""
        log.info("Reading experiment parameters.")
        table_widget = self.tabs.widget(0)
        table_df = table_widget.table.model().export_df()
        table_df.replace({'True': True, 'TRUE': True, 'true': True,
                          'False': False, 'FALSE': False, 'false': False}, inplace=True)
        converted_df: pd.DataFrame = self.verify_parameters(table_df)

        start_time: float = monotonic()
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
