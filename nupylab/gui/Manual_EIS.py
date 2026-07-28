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
from nupylab.instruments.ac_potentiostat.manual_biologic import Biologic as Potentiostat
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


class EIS_BIO_Procedure(nupylab_procedure.NupylabProcedure):
    """Procedure for running high impedance station GUI.

    Running this procedure calls startup, execute, and shutdown methods sequentially.
    In addition to the parameters listed below, this procedure inherits `record_time`,
    `num_steps`, and `current_steps` from parent class.
    """

    Potentiostat_options = ["Biologic",]
    Biologic_models = ["SP200", "SP300"]

    potentiostat: ListParameter = ListParameter("Brand Potentiostat", default="Biologic", choices=Potentiostat_options)
    potentiostat_model = ListParameter("Model Potentiostat", choices=Biologic_models, default="SP200")
    potentiostat_port: Parameter = Parameter(
        "Biologic Port", default="USB0", ui_class=None
    )
    initial_step: FloatParameter = FloatParameter("Initial Step", units="V", default=0)
    duration_step: FloatParameter = FloatParameter("Duration Step", units="s")
    maximum_frequency: FloatParameter = FloatParameter("Maximum Frequency", units="Hz")
    minimum_frequency: FloatParameter = FloatParameter("Minimum Frequency", units="Hz")
    amplitude_voltage: FloatParameter = FloatParameter("Amplitude Voltage", units="V")
    points_per_decade: IntegerParameter = IntegerParameter("Points Per Decade")

    # Units in parentheses must be valid pint units
    # First two entries must be "System Time" and "Time (s)"
    DATA_COLUMNS: List[str] = [
        "System Time",
        "Time (s)",
        "Ewe (V)",
        "Frequency (Hz)",
        "Z_re (ohm)",
        "-Z_im (ohm)",
        "|Z| (ohm)",
        "Phase (degrees)"
    ]

    TABLE_PARAMETERS: Dict[str, str] = {
        "Initial Ewe or I [V or A]": "initial_step",
        "Hold before EIS [s]": "duration_step",
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
    ]
    # Inputs must match name of selected procedure parameters
    INPUTS: List[str] = [
        "record_time",
        "potentiostat",
        "potentiostat_model",
        "potentiostat_port",
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
                    "Phase (degrees)",
                ),
            )
        self.instruments = (potentiostat,)
        self.active_instruments = (potentiostat,)
        potentiostat.set_parameters(
            self.record_time,
            self.initial_step,
            self.duration_step,
            self.maximum_frequency,
            self.minimum_frequency,
            self.amplitude_voltage,
            self.points_per_decade,
            "PEIS",
        )


def main(*args):
    """Run S8 procedure."""
    app = QtWidgets.QApplication(*args)
    window = nupylab_window.NupylabWindow(EIS_BIO_Procedure)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main(sys.argv)
