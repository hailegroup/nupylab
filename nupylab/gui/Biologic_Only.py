"""
GUI for S8 impedance station.

This GUI connects to and displays data from
    * Biologic SP-200 or SP300 Potentiostats

Run the program by changing to the directory containing this file and calling:

python Biologic_only.py
"""

import sys
from typing import Dict, List

# Instrument Imports #
from nupylab.instruments.ac_potentiostat.manual_biologic import Biologic as Potentiostat
from nupylab.instruments.dc_potentiostat.biologic import DCBiologic as DCPotentiostat
from nupylab.drivers.biologic import OCV
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


class Manual_Biologic_Procedure(nupylab_procedure.NupylabProcedure):
    """Procedure for running high impedance station GUI.

    Running this procedure calls startup, execute, and shutdown methods sequentially.
    In addition to the parameters listed below, this procedure inherits `record_time`,
    `num_steps`, and `current_steps` from parent class.
    """
    # List options
    Potentiostat_options = ["Biologic",]
    Biologic_models = ["SP200", "SP300"]
    EIS_options = ["PEIS", "GEIS", "SPEIS", "SGEIS"]
    DC_options = ["CA","CP"]

    # General Potentiostat Options - These will be on the left bar
    potentiostat: ListParameter = ListParameter("Brand Potentiostat", default="Biologic", choices=Potentiostat_options)
    potentiostat_model: ListParameter = ListParameter("Model Potentiostat", choices=Biologic_models, default="SP200")
    potentiostat_port: Parameter = Parameter(
        "Biologic Port", default="USB0", ui_class=None, group_by="eis_toggle"
    )
    EIS_method: ListParameter = ListParameter("EIS Method", default="PEIS", choices=EIS_options)
    DC_method: ListParameter = ListParameter("DC Method", default="CA", choices=DC_options)

    # Experiment Parameters for the inputs
    eis_toggle: BooleanParameter = BooleanParameter("Run EIS?", default=True )
    dc_toggle: BooleanParameter = BooleanParameter("Run DC?", default=False)
    initial_step: FloatParameter = FloatParameter("Initial Step", units="V", default=0)
    duration_step: FloatParameter = FloatParameter("Duration Step", units="s")
    maximum_frequency: FloatParameter = FloatParameter(
        "Maximum Frequency",
        units="Hz",
        group_by="eis_toggle",
        group_condition= True
    )
    minimum_frequency: FloatParameter = FloatParameter(
        "Minimum Frequency",
        units="Hz",
        group_by="eis_toggle",
        group_condition= True
    )
    amplitude_voltage: FloatParameter = FloatParameter(
        "Amplitude Voltage",
        units="V",
        group_by="eis_toggle",
        group_condition= True
    )
    points_per_decade: IntegerParameter = IntegerParameter(
        "Points Per Decade",
        group_by="eis_toggle",
        group_condition=True
    )

    # Units in parentheses must be valid pint units
    # First two entries must be "System Time" and "Time (s)"
    DATA_COLUMNS: List[str] = [
        "System Time",
        "Time (s)",
        "Ewe (V)",
        "I (A)",
        "Frequency (Hz)",
        "Z_re (ohm)",
        "-Z_im (ohm)",
        "|Z| (ohm)",
        "Phase (degrees)"
    ]

    TABLE_PARAMETERS: Dict[str, str] = {
        "Run EIS? [T/F]": "eis_toggle",
        "Run DC? [T/F]": "dc_toggle",
        "Initial Ewe or I [V or A]": "initial_step",
        "Hold time (before EIS) [s]": "duration_step",
        "Maximum Frequency [Hz]": "maximum_frequency",
        "Minimum Frequency [Hz]": "minimum_frequency",
        "Amplitude Voltage [V]": "amplitude_voltage",
        "Points per Decade": "points_per_decade"
    }

    # Entries in axes must have matches in procedure DATA_COLUMNS.
    # Number of plots is determined by the longer of X_AXIS or Y_AXIS
    X_AXIS: List[str] = ["Z_re (ohm)","Frequency (Hz)", "Time (s)"]
    Y_AXIS: List[str] = [
        "-Z_im (ohm)",
        "|Z| (ohm)",
        "Phase (degrees)",
        "Ewe (V)",
        "I (A)",
    ]
    # Inputs must match name of selected procedure parameters
    INPUTS: List[str] = [
        "record_time",
        "potentiostat",
        "potentiostat_model",
        "potentiostat_port",
        "EIS_method",
        "DC_method"
    ]

    def set_instruments(self) -> None:
        """Set and configure instruments list.

        Pass in connections from previous step, if applicable, otherwise create new
        instances. Send current step parameters to appropriate instruments.

        It is required for this method to create non-empty `instruments` and
        `active_instruments` attributes.
        """

        if self.previous_procedure is not None:
            potentiostat = self.previous_procedure.instruments
        else:
            if self.dc_toggle:
                potentiostat = DCPotentiostat(
                    self.potentiostat_port,
                    self.potentiostat_model,
                    0,
                    (
                        "Ewe (V)",
                        "I (A)",
                    ),
                )
            elif self.eis_toggle:
                potentiostat = Potentiostat(
                    self.potentiostat_port,
                    self.potentiostat_model,
                    0,
                    (
                        "Ewe (V)",
                        "Frequency (Hz)",
                        "Z_re (ohm)",
                        "-Z_im (ohm)",
                        "|Z| (ohm)",
                        "Phase (degrees)"
                    ),
                )
        self.instruments = (potentiostat,)
        self.active_instruments = (potentiostat,)

        if self.dc_toggle:
            potentiostat.set_parameters(
                self.record_time,
                self.initial_step,
                self.duration_step,
                self.DC_method,
                lambda: potentiostat._measuring_ocv==False,
            )
        elif self.eis_toggle:
            potentiostat.set_parameters(
                self.record_time,
                self.initial_step,
                self.duration_step,
                self.maximum_frequency,
                self.minimum_frequency,
                self.amplitude_voltage,
                self.points_per_decade,
                self.EIS_method,
                lambda: potentiostat._measuring_ocv==False,
            )



def main(*args):
    """Run S8 procedure."""
    app = QtWidgets.QApplication(*args)
    window = nupylab_window.NupylabWindow(Manual_Biologic_Procedure)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main(sys.argv)
