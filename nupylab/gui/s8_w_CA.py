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
from nupylab.instruments.dc_potentiostat.biologic import DCBiologic as DCPotentiostat
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
    """Procedure for running chronopotentiometry or chronoamperometry station GUI.

    Running this procedure calls startup, execute, and shutdown methods sequentially.
    In addition to the parameters listed below, this procedure inherits `record_time`,
    `num_steps`, and `current_steps` from parent class.
    """
    #Lists for references
    resources = list_resources()
    biologic_models = ["SP300", "SP200"]
    method_list = ["CA", "CP"]

    #Furnace parameters for left bar
    furnace_port: ListParameter = ListParameter(
        "Eurotherm Port", choices=resources, ui_class=None
    )
    furnace_address: IntegerParameter = IntegerParameter(
        "Eurotherm Address", minimum=1, maximum=254, step=1, default=1
    )

    #Furnace parameters for steps
    target_temperature: FloatParameter = FloatParameter("Target Temperature", units="C")
    ramp_rate: FloatParameter = FloatParameter("Ramp Rate", units="C/min")
    dwell_time: FloatParameter = FloatParameter("Dwell Time", units="min")

    #Potentiostat parameters for left bar
    potentiostat_port: Parameter = Parameter(
        "Biologic Port", default="USB0", ui_class=None, group_by="dc_toggle"
    )
    potentiostat_model: ListParameter = ListParameter("Biologic Model", choices=biologic_models, default="SP200", ui_class=None)

    # The procedure class can not take a string as an input, so we can't make a drop down menu to select a method for
    # each step. We can only use booleans. Maybe a feature to add in the future? Would require re-working the
    # nupylab_window file as well as others.
    dc_technique: ListParameter = ListParameter("Technique", choices=method_list, default="CA")

    # Potentiostat parameters for steps
    dc_toggle: BooleanParameter = BooleanParameter("Run DC")
    applied_step: FloatParameter = FloatParameter("Applied Stimulus",units="V or A")
    duration_step: FloatParameter = FloatParameter("Hold Time", units="s")

    # Units in parentheses must be valid pint units
    # First two entries must be "System Time" and "Time (s)"
    DATA_COLUMNS: List[str] = [
        "System Time",
        "Time (s)",
        "Furnace Temperature (degC)",
        "Ewe (V)",
        "I (A)",
    ]

    TABLE_PARAMETERS: Dict[str, str] = {
        "Target Temperature [C]": "target_temperature",
        "Ramp Rate [C/min]": "ramp_rate",
        "Dwell Time [min]": "dwell_time",
        "DC? [True/False]": "dc_toggle",
        "Applied Stimulus [V or A]": "applied_step",
        "Hold time [s]": "duration_step",
    }

    # Entries in axes must have matches in procedure DATA_COLUMNS.
    # Number of plots is determined by the longer of X_AXIS or Y_AXIS
    X_AXIS: List[str] = ["Time (s)"]
    Y_AXIS: List[str] = [
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
        "dc_technique",
    ]

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
            furnace = Heater(
                self.furnace_port, self.furnace_address, "Furnace Temperature (degC)"
            )
            potentiostat = DCPotentiostat(
                self.potentiostat_port,
                self.potentiostat_model,
                0,
                (
                    "Ewe (V)",
                    "I (A)",
                ),
            )
        self.instruments = (furnace, potentiostat)
        furnace.set_parameters(self.target_temperature, self.ramp_rate, self.dwell_time)
        if self.dc_toggle:
            self.active_instruments = (furnace, potentiostat)
            potentiostat.set_parameters(
                self.record_time,
                self.applied_step,
                self.duration_step,
                self.dc_technique,
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
