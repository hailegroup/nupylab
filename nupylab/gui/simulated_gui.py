"""
GUI for a simulated impedance station.

This GUI runs the same procedure and export path as the real stations, but with
instruments that generate data instead of reading it from hardware. It is meant
for testing changes to the procedure and export code when no station is free.

    * Simulated furnace controller, ramps and dwells
    * Simulated potentiostat, returns a frequency sweep

Run the program by changing to the directory containing this file and calling:

python simulated_gui.py
"""

import csv
import sys
from math import log10, pi, sin
from pathlib import Path
from random import gauss
from time import monotonic
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from nupylab.utilities import DataTuple, NupylabError
from nupylab.utilities.nupylab_instrument import NupylabInstrument
from nupylab.utilities import nupylab_procedure
from pymeasure.experiment import (
    BooleanParameter,
    FloatParameter,
    IntegerParameter,
)

# Equivalent circuit the simulated potentiostat responds with: a series
# resistance and inductance from the leads, in series with a parallel
# resistor and capacitor for the sample.
SERIES_RESISTANCE: float = 720.0  # ohm
SERIES_INDUCTANCE: float = 2.0e-5  # H
POLARIZATION_RESISTANCE: float = 1500.0  # ohm
DOUBLE_LAYER_CAPACITANCE: float = 2.0e-9  # F

NOISE_FRACTION: float = 0.002
TEMPERATURE_NOISE: float = 0.3  # degC


class SimulatedFurnace(NupylabInstrument):
    """Furnace that ramps to a target temperature and dwells there.

    Reports finished once the ramp and dwell have both elapsed, which is what
    ends the measurement step.

    Attributes:
        data_label: label for the temperature DataTuple.
        name: name of instrument.
        lock: thread lock for preventing simultaneous calls to instrument.
    """

    def __init__(
        self, data_label: str, name: str = "Simulated Furnace"
    ) -> None:
        """Initialize simulated furnace.

        Args:
            data_label: label for the temperature DataTuple, should match an entry
                in DATA_COLUMNS of the calling procedure class.
            name: name of instrument.
        """
        self._temperature: float = 25.0
        self._start_temperature: float = 25.0
        self._target: float = 25.0
        self._ramp_duration: float = 0.0
        self._dwell_duration: float = 0.0
        self._start_time: Optional[float] = None
        super().__init__(data_label, name)

    def connect(self) -> None:
        """Connect to simulated furnace."""
        with self.lock:
            self._connected = True

    def set_parameters(
        self, target_temperature: float, ramp_rate: float, dwell_time: float
    ) -> None:
        """Set furnace ramp parameters.

        Args:
            target_temperature: temperature to ramp to, in degrees C.
            ramp_rate: ramp rate in degrees C per minute.
            dwell_time: time to hold at the target, in minutes.
        """
        self._parameters = (target_temperature, ramp_rate, dwell_time)

    def start(self) -> None:
        """Begin the ramp.

        Raises:
            NupylabError if `start` is called before `set_parameters`.
        """
        if self._parameters is None:
            raise NupylabError(
                f"`{self.__class__.__name__}` method `set_parameters` "
                "must be called before calling its `start` method."
            )
        target, ramp_rate, dwell_time = self._parameters
        self._start_temperature = self._temperature
        self._target = target
        self._ramp_duration = (
            abs(target - self._temperature) / ramp_rate * 60 if ramp_rate > 0 else 0.0
        )
        self._dwell_duration = dwell_time * 60
        self._start_time = monotonic()
        self._parameters = None

    @property
    def finished(self) -> bool:
        """Get whether the ramp and dwell have both elapsed."""
        if self._start_time is None:
            return False
        elapsed = monotonic() - self._start_time
        return elapsed >= self._ramp_duration + self._dwell_duration

    def get_data(self) -> DataTuple:
        """Read the current furnace temperature.

        Returns:
            DataTuple holding the temperature in degrees C.
        """
        with self.lock:
            elapsed = monotonic() - self._start_time
            if self._ramp_duration > 0 and elapsed < self._ramp_duration:
                fraction = elapsed / self._ramp_duration
                self._temperature = self._start_temperature + fraction * (
                    self._target - self._start_temperature
                )
            else:
                self._temperature = self._target
            return DataTuple(
                self.data_label, self._temperature + gauss(0, TEMPERATURE_NOISE)
            )

    def stop_measurement(self) -> None:
        """Stop furnace measurement. Not implemented."""
        pass

    def shutdown(self) -> None:
        """Disconnect from simulated furnace."""
        with self.lock:
            self._connected = False


class SimulatedPotentiostat(NupylabInstrument):
    """Potentiostat that returns an impedance sweep of a simple circuit.

    Points are returned a few at a time so that some records carry impedance data
    and others do not, matching how a real sweep fills in over a measurement.

    Attributes:
        data_label: labels for the DataTuples.
        name: name of instrument.
        lock: thread lock for preventing simultaneous calls to instrument.
    """

    #: Frequency points returned per read, and reads skipped between them.
    POINTS_PER_READ: int = 8
    READS_BETWEEN_SWEEPS: int = 2

    def __init__(
        self, data_label: Sequence[str], name: str = "Simulated Potentiostat"
    ) -> None:
        """Initialize simulated potentiostat.

        Args:
            data_label: labels for the DataTuples, in the order voltage, frequency,
                real impedance, negative imaginary impedance. Entries should match
                DATA_COLUMNS of the calling procedure class.
            name: name of instrument.

        Raises:
            ValueError if length of data_label is not 4.
        """
        if len(data_label) != 4:
            raise ValueError(
                "Simulated potentiostat data_label must be sequence of length 4."
            )
        self._frequencies: List[float] = []
        self._index: int = 0
        self._reads: int = 0
        self._amplitude: float = 0.0
        self._finished: Optional[Callable[[], bool]] = None
        super().__init__(data_label, name)

    def connect(self) -> None:
        """Connect to simulated potentiostat."""
        with self.lock:
            self._connected = True

    def set_parameters(
        self,
        maximum_frequency: float,
        minimum_frequency: float,
        amplitude_voltage: float,
        points_per_decade: int,
        finished: Callable[[], bool],
    ) -> None:
        """Set sweep parameters.

        Args:
            maximum_frequency: frequency to start the sweep at, in Hz.
            minimum_frequency: frequency to end the sweep at, in Hz.
            amplitude_voltage: amplitude of the applied signal, in V.
            points_per_decade: number of frequency points per decade.
            finished: callable returning whether the sweep should stop repeating.
        """
        self._parameters = (
            maximum_frequency,
            minimum_frequency,
            amplitude_voltage,
            points_per_decade,
            finished,
        )

    def start(self) -> None:
        """Build the frequency sweep.

        Raises:
            NupylabError if `start` is called before `set_parameters`.
        """
        if self._parameters is None:
            raise NupylabError(
                f"`{self.__class__.__name__}` method `set_parameters` "
                "must be called before calling its `start` method."
            )
        maximum, minimum, amplitude, points_per_decade, finished = self._parameters
        decades = log10(maximum / minimum)
        count = max(2, int(round(decades * points_per_decade)) + 1)
        step = decades / (count - 1)
        self._frequencies = [
            10 ** (log10(maximum) - step * i) for i in range(count)
        ]
        self._amplitude = amplitude
        self._finished = finished
        self._index = 0
        self._reads = 0
        self._parameters = None

    def _impedance(self, frequency: float) -> Tuple[float, float]:
        """Get the impedance of the simulated circuit at one frequency.

        Args:
            frequency: frequency in Hz.

        Returns:
            tuple of real impedance and negative imaginary impedance, in ohms.
        """
        omega = 2 * pi * frequency
        # Parallel resistor and capacitor
        denominator = 1 + (omega * POLARIZATION_RESISTANCE
                           * DOUBLE_LAYER_CAPACITANCE) ** 2
        parallel_real = POLARIZATION_RESISTANCE / denominator
        parallel_imaginary = -(omega * POLARIZATION_RESISTANCE ** 2
                               * DOUBLE_LAYER_CAPACITANCE) / denominator
        z_re = SERIES_RESISTANCE + parallel_real
        z_im = omega * SERIES_INDUCTANCE + parallel_imaginary
        z_re *= 1 + gauss(0, NOISE_FRACTION)
        z_im *= 1 + gauss(0, NOISE_FRACTION)
        return z_re, -z_im

    def get_data(self) -> List[DataTuple]:
        """Read the next group of frequency points.

        Returns empty values on the reads between groups so that not every record
        carries impedance data.

        Returns:
            list of four DataTuples holding voltage, frequency, real impedance, and
            negative imaginary impedance.
        """
        with self.lock:
            self._reads += 1
            empty: List[DataTuple] = [
                DataTuple(label, []) for label in self.data_label
            ]
            if self._reads % (self.READS_BETWEEN_SWEEPS + 1) != 0:
                return empty
            if self._index >= len(self._frequencies):
                if self._finished is not None and self._finished():
                    return empty
                self._index = 0  # repeat the sweep until the furnace is done
            group = self._frequencies[
                self._index:self._index + self.POINTS_PER_READ
            ]
            self._index += self.POINTS_PER_READ

            voltages: List[float] = []
            z_res: List[float] = []
            z_ims: List[float] = []
            for i, frequency in enumerate(group):
                z_re, z_im = self._impedance(frequency)
                voltages.append(self._amplitude * sin(i) + gauss(0, 1e-4))
                z_res.append(z_re)
                z_ims.append(z_im)
            return [
                DataTuple(self.data_label[0], voltages),
                DataTuple(self.data_label[1], list(group)),
                DataTuple(self.data_label[2], z_res),
                DataTuple(self.data_label[3], z_ims),
            ]

    def stop_measurement(self) -> None:
        """Stop the sweep."""
        with self.lock:
            self._index = len(self._frequencies)

    def shutdown(self) -> None:
        """Disconnect from simulated potentiostat."""
        with self.lock:
            self._connected = False


class SimulatedProcedure(nupylab_procedure.NupylabProcedure):
    """Procedure for running the simulated station GUI.

    Running this procedure calls startup, execute, and shutdown methods sequentially.
    In addition to the parameters listed below, this procedure inherits `record_time`,
    `num_steps`, and `current_steps` from parent class.
    """

    target_temperature: FloatParameter = FloatParameter(
        "Target Temperature", units="C", default=100.0
    )
    ramp_rate: FloatParameter = FloatParameter(
        "Ramp Rate", units="C/min", default=200.0
    )
    dwell_time: FloatParameter = FloatParameter(
        "Dwell Time", units="min", default=0.2
    )

    eis_toggle: BooleanParameter = BooleanParameter("Run eis", default=True)
    maximum_frequency: FloatParameter = FloatParameter(
        "Maximum Frequency", units="Hz", default=1.0e6
    )
    minimum_frequency: FloatParameter = FloatParameter(
        "Minimum Frequency", units="Hz", default=1.0
    )
    amplitude_voltage: FloatParameter = FloatParameter(
        "Amplitude Voltage", units="V", default=0.01
    )
    points_per_decade: IntegerParameter = IntegerParameter(
        "Points Per Decade", default=10
    )

    # Units in parentheses must be valid pint units
    # First two entries must be "System Time" and "Time (s)"
    DATA_COLUMNS: List[str] = [
        "System Time",
        "Time (s)",
        "Furnace Temperature (degC)",
        "Ewe (V)",
        "Frequency (Hz)",
        "Z_re (ohm)",
        "-Z_im (ohm)",
    ]

    TABLE_PARAMETERS: Dict[str, str] = {
        "Target Temperature [C]": "target_temperature",
        "Ramp Rate [C/min]": "ramp_rate",
        "Dwell Time [min]": "dwell_time",
        "eis? [True/False]": "eis_toggle",
        "Maximum Frequency [Hz]": "maximum_frequency",
        "Minimum Frequency [Hz]": "minimum_frequency",
        "Amplitude Voltage [V]": "amplitude_voltage",
        "Points per Decade": "points_per_decade",
    }

    # Entries in axes must have matches in procedure DATA_COLUMNS.
    # Number of plots is determined by the longer of X_AXIS or Y_AXIS
    X_AXIS: List[str] = ["Z_re (ohm)", "Time (s)"]
    Y_AXIS: List[str] = [
        "-Z_im (ohm)",
        "Ewe (V)",
        "Furnace Temperature (degC)",
    ]
    # Inputs must match name of selected procedure parameters
    INPUTS: List[str] = ["record_time"]

    def set_instruments(self) -> None:
        """Set and configure instruments list.

        Pass in connections from previous step, if applicable, otherwise create new
        instances. Send current step parameters to appropriate instruments.

        It is required for this method to create non-empty `instruments` and
        `active_instruments` attributes.
        """
        if self.previous_procedure is not None:
            furnace, potentiostat = self.previous_procedure.instruments
        else:
            furnace = SimulatedFurnace("Furnace Temperature (degC)")
            potentiostat = SimulatedPotentiostat(
                (
                    "Ewe (V)",
                    "Frequency (Hz)",
                    "Z_re (ohm)",
                    "-Z_im (ohm)",
                )
            )
        self.instruments = (furnace, potentiostat)
        furnace.set_parameters(self.target_temperature, self.ramp_rate,
                              self.dwell_time)
        if self.eis_toggle:
            self.active_instruments = (furnace, potentiostat)
            potentiostat.set_parameters(
                self.maximum_frequency,
                self.minimum_frequency,
                self.amplitude_voltage,
                self.points_per_decade,
                lambda: furnace.finished,
            )
        else:
            self.active_instruments = (furnace,)


def run_headless(filename: str = "simulated_run_1.csv") -> None:
    """Run one step without a GUI and report on the two files written.

    Useful for checking the export path on a machine with no working Qt install,
    and for confirming that the ZView file holds exactly the impedance records of
    the main file.

    Args:
        filename: path to write the main data file to.
    """
    procedure = SimulatedProcedure()
    procedure.num_steps = 1
    procedure.current_step = 1
    procedure.record_time = 0.5
    procedure.target_temperature = 100.0
    procedure.ramp_rate = 900.0
    procedure.dwell_time = 0.05
    procedure.eis_toggle = True
    procedure.maximum_frequency = 1.0e6
    procedure.minimum_frequency = 1.0
    procedure.amplitude_voltage = 0.01
    procedure.points_per_decade = 10
    procedure.data_filename = filename

    records: List[dict] = []
    # A Worker normally supplies these; stand in for it so the procedure can run
    # outside the GUI.
    procedure.emit = lambda topic, record: (
        records.append(dict(record)) if topic == "results" else None
    )
    procedure.should_stop = lambda: False

    procedure.startup()
    procedure.execute()
    procedure.shutdown()

    with open(filename, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=procedure.DATA_COLUMNS)
        writer.writeheader()
        writer.writerows(records)

    zview = Path(filename)
    zview = zview.with_name(zview.stem + "_ZView" + zview.suffix)
    impedance = [
        r for r in records
        if r["Frequency (Hz)"] == r["Frequency (Hz)"]  # False for NaN
    ]
    with open(zview) as f:
        exported = sum(1 for _ in f) - 1

    print()
    print(f"main file    {filename}")
    print(f"  records            {len(records)}")
    print(f"  with impedance     {len(impedance)}")
    print(f"  without impedance  {len(records) - len(impedance)}")
    print(f"ZView file   {zview}")
    print(f"  rows               {exported}")
    print()
    if exported == len(impedance):
        print("The ZView file holds every impedance record and nothing else.")
    else:
        print("Row counts do not agree, check the log above.")


def main(*args):
    """Run simulated procedure."""
    from nupylab.utilities import nupylab_window
    from pymeasure.display.Qt import QtWidgets

    app = QtWidgets.QApplication(*args)
    window = nupylab_window.NupylabWindow(SimulatedProcedure)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    if "--headless" in sys.argv:
        import logging
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
        run_headless()
    else:
        main(sys.argv)
