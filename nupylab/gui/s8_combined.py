"""
GUI for S8 impedance station.

This GUI connects to and displays data from
    * Eurotherm 2216e Furnace Controller
    * Biologic SP-300 Potentiostat (optional)

Run the program by changing to the directory containing this file and calling:

python s8_gui.py
"""

import sys
from typing import Dict, List

# Instrument Imports #
from nupylab.instruments.ac_potentiostat.biologic import Biologic as Potentiostat
from nupylab.instruments.heater.eurotherm2200 import Eurotherm2200 as Heater
######################
from nupylab.utilities import list_resources, nupylab_procedure, nupylab_window
from pymeasure.display.Qt import QtWidgets
from pymeasure.experiment import (
    BooleanParameter,
    FloatParameter,
    IntegerParameter,
    ListParameter,
    Parameter,
)


class S8Procedure(nupylab_procedure.NupylabProcedure):
    """Procedure for running high impedance station GUI.

    Running this procedure calls startup, execute, and shutdown methods sequentially.
    In addition to the parameters listed below, this procedure inherits `record_time`,
    `num_steps`, and `current_steps` from parent class.
    """

    resources = list_resources()
    biologic_models = ["SP300", "SP200"]
    AC_techniques = ["PEIS", "GEIS", "SPEIS", "SGEIS"]
    DC_techniques = ["CP","CA"]

    ### Furnace parameters ###
    furnace_port: ListParameter = ListParameter(
        "Eurotherm Port", choices=resources, ui_class=None
    )
    furnace_address: IntegerParameter = IntegerParameter(
        "Eurotherm Address", minimum=1, maximum=254, step=1, default=1
    )
    target_temperature: FloatParameter = FloatParameter("Target Temperature", units="C")
    ramp_rate: FloatParameter = FloatParameter("Ramp Rate", units="C/min")
    dwell_time: FloatParameter = FloatParameter("Dwell Time", units="min")

    ### General Potentiostat Parameters ###
    potentiostat_port: Parameter = Parameter(
        "Biologic Port", default="USB0", ui_class=None
    )
    potentiostat_model: ListParameter = ListParameter(
        "Biologic Model", choices=biologic_models, ui_class=None, default="SP200"
    )
    # The procedure class can not take a string as an input, so we can't make a drop down menu to select a method for
    # each step. We can only use booleans. Maybe a feature to add in the future? Would require re-working the
    # nupylab_window file as well as others.
    eis_techniques: ListParameter = ListParameter("EIS Technique", choices=AC_techniques, ui_class=None, default="PEIS")
    dc_techniques: ListParameter = ListParameter("DC Technique", choices=DC_techniques, ui_class=None, default="CA")
    eis_boolean: BooleanParameter = BooleanParameter("Run EIS?")
    dc_boolean: BooleanParameter = BooleanParameter("Run DC?")

    ### Potentiostat Parameters ###
    #applied step and duration can also work for applying bias and hold time before starting EIS in EIS condition
    applied_step: FloatParameter = FloatParameter("Applied Stimulus", units="V or A")
    duration_step: FloatParameter = FloatParameter("Hold Time", units="s")

    ### AC Potentiostat Parameters ###
    maximum_frequency: FloatParameter = FloatParameter("Maximum Frequency", units="Hz")
    minimum_frequency: FloatParameter = FloatParameter("Minimum Frequency", units="Hz")
    amplitude_voltage: FloatParameter = FloatParameter("Amplitude Voltage", units="V")
    points_per_decade: IntegerParameter = IntegerParameter("Points Per Decade")

    # Units in parentheses must be valid pint units
    # First two entries must be "System Time" and "Time (s)"
    DATA_COLUMNS: List[str] = [
        "System Time",
        "Time (s)",
        "Furnace Temperature (degC)",
        "Ewe (V)",
        "I (A)",
        "Frequency (Hz)",
        "Z_re (ohm)",
        "-Z_im (ohm)",
        "|Z| (ohm)",
        "Phase (deg)",
    ]

    TABLE_PARAMETERS: Dict[str, str] = {
        "Target Temperature [C]": "target_temperature",
        "Ramp Rate [C/min]": "ramp_rate",
        "Dwell Time [min]": "dwell_time",
        "Run EIS? [T/F]": "eis_boolean",
        "Run DC? [T/F]": "dc_boolean",
        "Applied Stimulus [V or A]": "applied_step",
        "Hold time [s]": "duration_step",
        "Maximum Frequency [Hz]": "maximum_frequency",
        "Minimum Frequency [Hz]": "minimum_frequency",
        "Amplitude Voltage [V]": "amplitude_voltage",
        "Points per Decade": "points_per_decade"
    }

    # Entries in axes must have matches in procedure DATA_COLUMNS.
    # Number of plots is determined by the longer of X_AXIS or Y_AXIS
    X_AXIS: List[str] = ["Z_re (ohm)", "Time (s)"]
    Y_AXIS: List[str] = [
        "-Z_im (ohm)",
        "|Z| (ohm)",
        "Phase (deg)",
        "Ewe (V)",
        "I (A)",
        "Furnace Temperature (degC)",
    ]
    # Inputs must match name of selected procedure parameters
    INPUTS: List[str] = [
        "record_time",
        "furnace_port",
        "furnace_address",
        "potentiostat_port",
        "potentiostat_model",
        "dc_techniques",
        "eis_techniques",
    ]

    def set_instruments(self) -> None:
        """Set and configure instruments list.

        Pass in connections from previous step, if applicable, otherwise create new
        instances. Send current step parameters to appropriate instruments.

        It is required for this method to create non-empty `instruments` and
        `active_instruments` attributes.
        """
        dc_methods = ["CP", "CA"]
        eis_methods = ["PEIS", "GEIS", "SPEIS", "SGEIS"]

        if self.previous_procedure is not None:
            furnace, potentiostat = self.previous_procedure.instruments
        else:
            furnace = Heater(
                self.furnace_port, self.furnace_address, "Furnace Temperature (degC)"
            )
            if self.eis_boolean and self.dc_boolean:
                raise ValueError("EIS and DC boolean can not both be true in the same step.")

            if self.eis_boolean or self.dc_boolean:
                potentiostat = Potentiostat(
                    self.potentiostat_port,
                    self.potentiostat_model,
                    0,
                    (
                        "Ewe (V)",
                        "I (A)",
                        "Frequency (Hz)",
                        "Z_re (ohm)",
                        "-Z_im (ohm)",
                        "|Z| (ohm)",
                        "Phase (deg)",
                    ),
                )
        self.instruments = (furnace, potentiostat)
        furnace.set_parameters(self.target_temperature, self.ramp_rate, self.dwell_time)
        if self.eis_boolean or self.dc_boolean:
            self.active_instruments = (furnace, potentiostat)
            if self.dc_boolean:
                technique = self.dc_techniques
            elif self.eis_boolean:
                technique = self.eis_techniques
            else:
                technique = "None"

            potentiostat.set_parameters(
                self.record_time,
                self.applied_step,
                self.duration_step,
                self.maximum_frequency,
                self.minimum_frequency,
                self.amplitude_voltage,
                self.points_per_decade,
                technique,
                lambda: furnace.finished,
            )
        else:
            self.active_instruments = (furnace,)


def main(*args):
    """Run S8 procedure."""
    app = QtWidgets.QApplication(*args)
    window = nupylab_window.NupylabWindow(S8Procedure)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main(sys.argv)
