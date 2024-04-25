"""
GUI for high-impedance station.

This GUI connects to and displays data from
    * Eurotherm 2216e Furnace Controller
    * Biologic SP-200 Potentiostat (optional)

Run the program by changing to the directory containing this file and calling:

python s8_biologic_eurotherm2200_gui.py
"""

from __future__ import annotations
from datetime import datetime
import logging
import os
from queue import Empty, SimpleQueue
import sys
from threading import Thread
from time import sleep, monotonic, perf_counter
from typing import Callable, List, Optional, TYPE_CHECKING

import numpy as np
import pyvisa

from pymeasure.display.Qt import QtWidgets
from pymeasure.display.windows.managed_dock_window import ManagedDockWindow
from pymeasure.experiment import (
    BooleanParameter, FloatParameter, IntegerParameter, ListParameter, Parameter,
    Procedure, Results, unique_filename)

from nupylab.instruments import BiologicPotentiostat, Eurotherm2200
from nupylab.instruments.biologic import OCV, PEIS
from nupylab.utilities import ParameterTableWidget, DataTuple


if TYPE_CHECKING:
    import pandas as pd

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

class Experiment(Procedure):
    """Procedure for running high impedance station GUI.

    Running this procedure calls startup, execute, and shutdown methods sequentially.
    """

    def __init__(self, **kwargs):
        # Initialize values with NaN to avoid complaints
        self._data_defaults: dict = {
            'Furnace Temperature (degC)': np.nan,
            'Furnace Thread Time (s)': np.nan,
            'Ewe (V)': np.nan,
            'Frequency (Hz)': np.nan,
            'Z_re (ohm)': np.nan,
            '-Z_im (ohm)': np.nan,
            'Biologic Time (s)': np.nan,
            'Biologic Thread Time (s)': np.nan
        }
        self._data: dict = {
            'System Time': None,
            'Time (s)': np.nan
        }
        self._data.update(self._data_defaults)
        self._measuring_ocv: bool = False
        self._counter: int = 1
        self._finished: bool = False
        self._start_time: float = 0
        self._furnace_running: bool = False
        self._furnace_time: float = 0
        self._biologic_time: float = 0
        self._peis: Optional[PEIS] = None
        self._multivalue_results: List[DataTuple] = []
        self.eurotherm = None
        self.biologic = None
        self._previous_step = None

        super().__init__(**kwargs)

    delay = FloatParameter('Record Time', units='s', default=2.0)

    # rm = pyvisa.ResourceManager('@py')
    rm = pyvisa.ResourceManager()
    resources = rm.list_resources()

    eurotherm_port = ListParameter('Eurotherm Port', choices=resources, ui_class=None)
    eurotherm_address = IntegerParameter(
        'Eurotherm Address', minimum=1, maximum=254, step=1, default=1
    )
    biologic_port = Parameter(
        'Biologic Port', default='USB0', ui_class=None, group_by='eis_toggle'
    )

    start_time = FloatParameter('Start Time', maximum=1e12)
    num_steps = IntegerParameter('Number of Measurement Steps')
    current_step = IntegerParameter('Current Step')

    target_temperature = FloatParameter('Target Temperature', units='C')
    ramp_rate = FloatParameter('Ramp Rate', units='C/min')
    dwell_time = FloatParameter('Dwell Time', units='min')

    eis_toggle = BooleanParameter('Run EIS')
    maximum_frequency = FloatParameter('Maximum Frequency', units='Hz')
    minimum_frequency = FloatParameter('Minimum Frequency', units='Hz')
    amplitude_voltage = FloatParameter('Amplitude Voltage', units='V')
    points_per_decade = IntegerParameter('Points Per Decade')

    # Note: units in parentheses must be valid pint units
    DATA_COLUMNS: List[str] = ['System Time',
                               'Time (s)',
                               'Furnace Temperature (degC)',
                               'Furnace Thread Time (s)',
                               'Ewe (V)',
                               'Frequency (Hz)',
                               'Z_re (ohm)',
                               '-Z_im (ohm)',
                               'Biologic Time (s)',
                               'Biologic Thread Time (s)']

    def startup(self) -> None:
        """Connect to instruments and start furnace, gas flow, and OCV measurement."""
        log.info("Connecting to instruments...")
        self._set_instruments()
        self._initialize_furnace()
        if self.eis_toggle:
            self._initialize_biologic()
        self._start_furnace()
        sleep(1)  # Give Eurotherm time to get program running

    def execute(self) -> None:
        """Loop through thread for each instrument and emit results."""
        log.info("Running step %d / %d.", self.current_step, self.num_steps)
        furnace_queue: SimpleQueue = SimpleQueue()
        queues: List[SimpleQueue] = [furnace_queue,]

        furnace_thread = Thread(
            target=self._sub_loop, args=(self._update_furnace, furnace_queue)
        )
        threads: List[Thread] = [furnace_thread,]

        if self.eis_toggle:
            biologic_queue: SimpleQueue = SimpleQueue()
            biologic_thread = Thread(
                target=self._sub_loop, args=(self._update_biologic, biologic_queue)
            )
            queues.append(biologic_queue)
            threads.append(biologic_thread)
            self.biologic.start_channel(0)
            self._measuring_ocv = True

        self._start_time = monotonic()
        sleep_time: float
        for thread in threads:
            thread.start()

        while True:
            sleep_time = self.delay * self._counter - (monotonic() - self._start_time)
            sleep(max(0, sleep_time))
            self._emit_results(queues)  # Emit after other threads have run
            self._counter += 1

            if self.should_stop():
                log.warning("Catch stop command in procedure")
                break
            if self._finished:
                for thread in threads:
                    thread.join()
                self._counter -= 1  # Quick counter fix for flushing queues
                while self._emit_results(queues) != 0:  # Flush queues
                    continue
                log.info("Step %d / %d complete.", self.current_step, self.num_steps)
                break
        for thread in threads:
            thread.join()

    def shutdown(self) -> None:
        """Shut off furnace and turn off gas flow."""
        if self.eis_toggle:
            try:
                self.biologic.stop_channel(0)
            except Exception as e:
                log.warning("Error stopping Biologic: %s", e)
        if (self.status == (Procedure.FAILED or Procedure.ABORTED) or
                self.num_steps == self.current_step):
            try:
                self.eurotherm.program_status = 'reset'
                self.eurotherm.serial.close()
            except Exception as e:
                log.warning("Error shutting down Eurotherm: %s", e)
            if self.eis_toggle:
                try:
                    self.biologic.disconnect()
                except Exception as e:
                    log.warning("Error shutting down Biologic: %s", e)
        log.info("Shutdown complete.")

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

    def _parse_results(self, result: DataTuple) -> None:
        """Write value to class data if single-valued, otherwise postpone extraction."""
        if hasattr(result.value, '__len__'):
            if len(result.value) == 1:
                self._data[result.label] = result.value[0]
            else:
                self._multivalue_results.append(result)
        else:
            self._data[result.label] = result.value

    def _emit_results(self, queues: List[SimpleQueue]) -> int:
        """Emit most recent set of results from all queues.

        Args:
            queues: list of queues.

        Returns:
            the number of queues that contained results.
        """
        self._data['Time (s)'] = self.delay * (self._counter - 1)
        self._data['System Time'] = str(datetime.now())
        self._multivalue_results = []
        filled_queues: int = 0
        results: tuple
        for q in queues:
            try:
                results = q.get_nowait()
            except Empty:
                print(f"Empty queue {q}")
                continue
            else:
                filled_queues += 1
                if isinstance(results, DataTuple):
                    self._parse_results(results)
                else:
                    for result in results:
                        self._parse_results(result)

        if filled_queues == 0:
            return filled_queues

        if len(self._multivalue_results) == 0:
            self.emit('results', self._data)
            self.emit('progress', 0)
            self._data.update(self._data_defaults)  # reset data to defaults
            return filled_queues

        index: int = max(len(result.value) for result in self._multivalue_results)
        for i in range(index):
            for result in self._multivalue_results:
                if len(result.value) < i + 1:
                    continue
                self._data[result.label] = result.value[i]
            self.emit('results', self._data)
            self._data.update(self._data_defaults)  # reset data to defaults
        self.emit('progress', 0)
        return filled_queues

    def _update_furnace(self, furnace_queue: SimpleQueue) -> None:
        """Read furnace temperature and program status.

        Args:
            furnace_queue: queue for holding furnace temperature measurements.
        """
        time0 = perf_counter()
        temperature: float = self.eurotherm.process_value
        status: str = self.eurotherm.program_status
        furnace_queue.put(
            (DataTuple('Furnace Temperature (degC)', temperature),
             DataTuple('Furnace Thread Time (s)', perf_counter() - time0)))
        self._furnace_running = status not in ('off', 'end')
        if not self.eis_toggle:
            self._finished = not self._furnace_running

    def _update_biologic(self, biologic_queue: SimpleQueue) -> None:
        time0 = perf_counter()
        kbio_data = self.biologic.get_data(0)
        # Bug: sometimes Biologic does not report data when it should
        while len(kbio_data.Ewe) == 0:
            kbio_data = self.biologic.get_data(0)
        if self._measuring_ocv and not self._furnace_running:
            # Switch from OCV to PEIS upon completing furnace program
            self.biologic.stop_channel(0)
            self.biologic.load_technique(0, self._peis, first=True, last=True)
            self.biologic.start_channel(0)
            self._measuring_ocv = False

        channel_infos = self.biologic.get_channel_infos(0)

        if 'freq' in kbio_data.data_field_names:  # Measuring PEIS
            abs_z = kbio_data.abs_Ewe_numpy / kbio_data.abs_I_numpy
            z_phase = kbio_data.Phase_Zwe_numpy
            z_re = abs_z * np.cos(z_phase)
            z_im = abs_z * np.sin(z_phase)
            biologic_queue.put(
                (
                    DataTuple('Ewe (V)', kbio_data.Ewe),
                    DataTuple('Frequency (Hz)', kbio_data.freq),
                    DataTuple('Z_re (ohm)', z_re),
                    DataTuple('-Z_im (ohm)', -z_im),
                    DataTuple('Biologic Time (s)', kbio_data.t),
                    DataTuple('Biologic Thread Time (s)', perf_counter() - time0)
                )
            )
        else:
            biologic_queue.put((DataTuple('Ewe (V)', kbio_data.Ewe),
                               DataTuple('Biologic Time (s)', kbio_data.time),
                               DataTuple('Biologic Thread Time (s)', perf_counter() - time0)))
        self._finished = channel_infos['State'] == 0

    def _initialize_furnace(self) -> None:
        """Convert 'ASRL##::INSTR' to form 'COM##'."""
        if self.eurotherm is None:
            port: str = str(self.eurotherm_port)
            port = port.replace('ASRL', 'COM').replace('::INSTR', '')
            self.eurotherm = Eurotherm2200(port, self.eurotherm_address)
        self._furnace_time = 60 * (
            self.dwell_time +
            (self.target_temperature - self.eurotherm.process_value) / self.ramp_rate)
        log.info("Connection to Eurotherm successful.")

    def _initialize_biologic(self) -> None:
        if self.biologic is None:
            self.biologic = BiologicPotentiostat('SP200', self.biologic_port, None)
            self.biologic.connect()
            self.biologic.load_firmware((1,))

        ocv = OCV(duration=24*60*60,
                  record_every_dE=10.0,
                  record_every_dt=self.delay,
                  E_range='KBIO_ERANGE_AUTO')
        self.biologic.load_technique(0, ocv, first=True, last=True)

        freq_steps: int = (
            (max_log_f := np.log10(self.maximum_frequency)) -
            (min_log_f := np.log10(self.minimum_frequency))
        )
        freq_steps = round(freq_steps * self.points_per_decade) + 1
        self._biologic_time = np.sum(
            1 / np.logspace(max_log_f, min_log_f, freq_steps)
        )
        self._peis = PEIS(
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
        log.info("Furnace started successfully.")

    def _set_instruments(self):
        """Pass instrument connections from previous step to current step."""
        if self._previous_step is None:
            return
        self.biologic = self._previous_step.biologic
        self.eurotherm = self._previous_step.eurotherm


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
        "EIS? [True/False]",
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
                specified in :attr:`parameter_types`

        Raises:
            IndexError: if the number of columns in the parameter table does not match
                the number of parameters in :attr:`parameter_types`
            ValueError: if the parameters table cannot be converted to the types listed
                in :attr:`parameter_types`
        """
        if len(self.parameter_types) != table_df.shape[1]:
            raise IndexError(f"Expected {len(self.parameter_types)} parameters, but "
                             f"parameters table has {table_df.shape[1]} columns.")

        converted_df = table_df.copy()
        bool_map = {'true': True, 'yes': True, '1': True,
                    'false': False, 'no': False, '0': False}
        for parameter_type, column in zip(
            self.parameter_types.values(), converted_df.columns
        ):  # non-empty strings evaluate to True; apply map instead for boolean columns
            if parameter_type == bool:
                converted_df[column] = converted_df[column].str.casefold().map(bool_map)
        converted_df = converted_df.astype(
            dict(zip(converted_df.columns, self.parameter_types.values()))
        )
        return converted_df

    def set_table_parameters(
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

    def queue(self, procedure=None) -> None:
        """Queue all rows in parameters table. Overwrites parent method."""
        log.info("Reading experiment parameters.")
        table_widget = self.tabs.widget(0)
        table_df = table_widget.table.model().export_df()
        converted_df: pd.DataFrame = self.verify_parameters(table_df)

        start_time: float = monotonic()
        num_steps: int = converted_df.shape[0]
        current_step: int = 1
        previous_procedure = None

        for parameters in converted_df.itertuples(index=False):
            procedure = self.make_procedure()
            self.set_table_parameters(
                procedure,
                parameters,
                start_time,
                num_steps,
                current_step
            )
            procedure._previous_step = previous_procedure
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
            previous_procedure = procedure


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
