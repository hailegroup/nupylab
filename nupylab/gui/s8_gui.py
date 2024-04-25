"""
GUI for high-impedance station.

This GUI connects to and displays data from
    * Eurotherm 2216e Furnace Controller
    * Biologic SP-200 Potentiostat (optional)

Run the program by changing to the directory containing this file and calling:

python s8_gui.py
"""

import logging
import sys
from typing import List, Dict

import pyvisa

from pymeasure.display.Qt import QtWidgets
from pymeasure.experiment import (
    BooleanParameter,
    FloatParameter,
    IntegerParameter,
    ListParameter,
    Parameter,
)

# Instrument Imports #
from nupylab.instruments.ac_potentiostat.biologic import Biologic as Potentiostat
from nupylab.instruments.heater.eurotherm2200 import Eurotherm2200 as Heater
######################

from nupylab.utilities import nupylab_procedure, nupylab_window


log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


class S8Procedure(nupylab_procedure.NupylabProcedure):
    """Procedure for running high impedance station GUI.

    Running this procedure calls startup, execute, and shutdown methods sequentially.
    In addition to the parameters listed below, this procedure inherits `record_time`,
    `start_time`, `num_steps`, and `current_steps` from parent class.
    """

    # Units in parentheses must be valid pint units
    DATA_COLUMNS: List[str] = [
        "System Time",
        "Time (s)",
        "Furnace Temperature (degC)",
        "Ewe (V)",
        "Frequency (Hz)",
        "Z_re (ohm)",
        "-Z_im (ohm)",
    ]

    rm = pyvisa.ResourceManager()
    resources = rm.list_resources()

    furnace_port = ListParameter("Eurotherm Port", choices=resources, ui_class=None)
    furnace_address = IntegerParameter(
        "Eurotherm Address", minimum=1, maximum=254, step=1, default=1
    )
    target_temperature = FloatParameter("Target Temperature", units="C")
    ramp_rate = FloatParameter("Ramp Rate", units="C/min")
    dwell_time = FloatParameter("Dwell Time", units="min")

    potentiostat_port = Parameter(
        "Biologic Port", default="USB0", ui_class=None, group_by="eis_toggle"
    )
    eis_toggle = BooleanParameter("Run EIS")
    maximum_frequency = FloatParameter("Maximum Frequency", units="Hz")
    minimum_frequency = FloatParameter("Minimum Frequency", units="Hz")
    amplitude_voltage = FloatParameter("Amplitude Voltage", units="V")
    points_per_decade = IntegerParameter("Points Per Decade")

    TABLE_PARAMETERS: Dict[str, Parameter] = {
        "Target Temperature [C]": target_temperature,
        "Ramp Rate [C/min]": ramp_rate,
        "Dwell Time [min]": dwell_time,
        "EIS? [True/False]": eis_toggle,
        "Maximum Frequency [Hz]": maximum_frequency,
        "Minimum Frequency [Hz]": amplitude_voltage,
        "Amplitude Voltage [V]": minimum_frequency,
        "Points per Decade": points_per_decade
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
    INPUTS: List[str] = [
        "record_time",
        "furnace_port",
        "furnace_address",
        "potentiostat_port",
    ]

    def set_instruments(self) -> None:
        """Set and configure instruments list.

        Pass in connections from previous step, if applicable, otherwise create new
        instances. Send current step parameters to appropriate instruments.
        """
        if self.previous_procedure:
            furnace, potentiostat = self.previous_procedure.instruments
        else:
            furnace = Heater(
                self.furnace_port, self.furnace_address, "Furnace Temperature (degC)"
            )
            potentiostat = Potentiostat(
                self.potentiostat_port,
                "SP200",
                0,
                (
                    "Ewe (V)",
                    "Frequency (Hz)",
                    "Z_re (ohm)",
                    "-Z_im (ohm)",
                ),
            )
        self.instruments = (furnace, potentiostat)
        furnace.set_parameters(self.target_temperature, self.ramp_rate, self.dwell_time)
        if self.eis_toggle:
            self.active_instruments = (furnace, potentiostat)
            potentiostat.set_parameters(
                self.record_time,
                self.maximum_frequency,
                self.minimum_frequency,
                self.amplitude_voltage,
                self.points_per_decade,
                "PEIS",
                lambda: furnace.finished,
            )
        else:
            self.active_instruments = (furnace,)


class MainWindow(nupylab_window.NupylabWindow):
    """Main GUI window. Procedure must be specified."""

    def __init__(self) -> None:
        procedure = S8Procedure
        super().__init__(
            procedure_class=procedure,
            table_column_labels=list(procedure.TABLE_PARAMETERS),
            x_axis=procedure.X_AXIS,
            y_axis=procedure.Y_AXIS,
            inputs=procedure.INPUTS,
        )


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
