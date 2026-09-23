"""
GUI for S8 impedance station.

This GUI connects to and displays data from
    * Eurotherm 2216e Furnace Controller
    * Biologic SP-300 Potentiostat (optional)

Run the program by changing to the directory containing this file and calling:

python s8_gui.py

The Instrument Control tab connects using the port, address, and model settings
in the inputs panel.
"""

import logging
import sys
import time
from typing import Dict, List

# Instrument Imports #
from nupylab.instruments.ac_potentiostat.biologic import Biologic as Potentiostat
from nupylab.instruments.heater.eurotherm2200 import Eurotherm2200 as Heater
from nupylab.utilities.instrument_control import InstrumentControlWidget
######################
from nupylab.utilities import list_resources, nupylab_procedure, nupylab_window
from pymeasure.display.Qt import QtWidgets
from pymeasure.experiment import (
    FloatParameter,
    IntegerParameter,
    ListParameter,
    Parameter,
)

_control_log = logging.getLogger('nupylab.instrument_control')


class S8Procedure(nupylab_procedure.NupylabProcedure):
    """Procedure for running high impedance station GUI.

    Running this procedure calls startup, execute, and shutdown methods sequentially.
    In addition to the parameters listed below, this procedure inherits `record_time`,
    `num_steps`, and `current_steps` from parent class.
    """

    resources = list_resources()
    Potentiostat_options = ["Biologic",]
    Biologic_models = ["SP200", "SP300"]
    EIS_techniques = ["PEIS", "SPEIS", "GEIS", "SGEIS"]

    _default_furnace = "ASRL4::INSTR" if "ASRL4::INSTR" in resources else (resources[0] if resources else "")

    #Furnace parameters
    furnace_port: ListParameter = ListParameter(
        "Eurotherm Port", choices=resources, default=_default_furnace, ui_class=None
    )
    furnace_address: IntegerParameter = IntegerParameter(
        "Eurotherm Address", minimum=1, maximum=254, step=1, default=1
    )
    target_temperature: FloatParameter = FloatParameter("Target Temperature", units="C")
    ramp_rate: FloatParameter = FloatParameter("Ramp Rate", units="C/min")
    dwell_time: FloatParameter = FloatParameter("Dwell Time", units="min")

    #Potentiostat Parameters
    potentiostat: ListParameter = ListParameter("Brand Potentiostat", default="Biologic", choices=Potentiostat_options)
    potentiostat_model = ListParameter("Model Potentiostat", choices=Biologic_models, default="SP200")
    potentiostat_port: Parameter = Parameter(
        "Biologic Port", default="USB0", ui_class=None, group_by="eis_toggle"
    )
    potentiostat_technique = ListParameter("EIS Technique", default="PEIS", choices=EIS_techniques)

    eis_toggle: ListParameter = ListParameter(
        "Run eis", choices=["True", "False"], default="False", ui_class=None
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
        "Furnace Temperature (degC)",
        "Ewe (V)",
        "I (A)",
        "Frequency (Hz)",
        "Z_re (ohm)",
        "-Z_im (ohm)",
        "|Z| (ohm)",
        "Phase (degrees)"
    ]

    TABLE_PARAMETERS: Dict[str, str] = {
        "Target Temperature [C]": "target_temperature",
        "Ramp Rate [C/min]": "ramp_rate",
        "Dwell Time [min]": "dwell_time",
        "eis? [True/False]": "eis_toggle",
        "Initial Ewe or I [V or A]": "initial_step",
        "Hold before EIS [min]": "duration_step",
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
        "Furnace Temperature (degC)",
    ]
    # Inputs must match name of selected procedure parameters
    INPUTS: List[str] = [
        "record_time",
        "furnace_port",
        "furnace_address",
        "potentiostat",
        "potentiostat_model",
        "potentiostat_port",
        "potentiostat_technique",
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
                    "Phase (degrees)",
                ),
            )
        self.instruments = (furnace, potentiostat)
        furnace.set_parameters(self.target_temperature, self.ramp_rate, self.dwell_time)
        if str(self.eis_toggle).lower() == "true":
            self.active_instruments = (furnace, potentiostat)
            potentiostat.set_parameters(
                self.record_time,
                self.initial_step,
                self.duration_step,
                self.maximum_frequency,
                self.minimum_frequency,
                self.amplitude_voltage,
                self.points_per_decade,
                self.potentiostat_technique,
                lambda: furnace.finished,
            )
        else:
            self.active_instruments = (furnace,)


def main(*args):
    """Run S8 procedure."""
    app = QtWidgets.QApplication(*args)
    window = nupylab_window.NupylabWindow(S8Procedure)
    inputs = window.inputs

    # Instrument Control tab uses the connection settings from the inputs panel
    furnace = Heater(
        inputs.furnace_port.value(),
        inputs.furnace_address.value(),
        "Furnace Temperature (degC)",
    )
    control_instruments = [furnace]
    # Biologic loads the EClib DLL on creation; keep GUI usable if that fails
    potentiostat = None
    potentiostat_error = None
    try:
        potentiostat = Potentiostat(
            inputs.potentiostat_port.value(),
            inputs.potentiostat_model.value(),
            0,
            (
                "Ewe (V)",
                "I (A)",
                "Frequency (Hz)",
                "Z_re (ohm)",
                "-Z_im (ohm)",
                "|Z| (ohm)",
                "Phase (degrees)",
            ),
        )
        control_instruments.append(potentiostat)
    except Exception as e:
        potentiostat_error = e

    def sync_connection_settings(*_):
        """Apply inputs panel connection settings on next control tab connect."""
        try:
            furnace.set_connection(
                inputs.furnace_port.value(), inputs.furnace_address.value()
            )
            if potentiostat is not None:
                potentiostat.set_connection(
                    inputs.potentiostat_port.value(),
                    inputs.potentiostat_model.value(),
                )
        except Exception as e:
            _control_log.warning("Could not read connection settings: %s", e)

    for name in (
        "furnace_port", "furnace_address", "potentiostat_port", "potentiostat_model"
    ):
        element = getattr(inputs, name)
        for signal in ("currentTextChanged", "valueChanged", "textChanged"):
            if hasattr(element, signal):
                getattr(element, signal).connect(sync_connection_settings)
                break

    def abort_experiment():
        try:
            window.manager.abort()
        except Exception:
            pass
        for inst in control_instruments:
            if inst.connected:
                try:
                    inst.disconnect()
                except Exception:
                    pass

    control = InstrumentControlWidget(
        control_instruments,
        abort_callback=abort_experiment,
        directory=lambda: window.directory,
        recording_beside_panels=True,
        fit_panels_height=True,
        button_feedback=True,
    )

    window.tabs.addTab(control, "Instrument Control")
    if potentiostat_error is not None:
        _control_log.error(
            "Biologic control panel unavailable: %s", potentiostat_error
        )

    def disconnect_control_instruments():
        for inst in control_instruments:
            if inst.connected:
                try:
                    inst.disconnect()
                except Exception:
                    pass
        time.sleep(0.5)

    window.manager.queued.connect(disconnect_control_instruments)
    window.manager.running.connect(
        lambda: control.set_enabled_for_experiment(True)
    )
    window.manager.finished.connect(
        lambda: control.set_enabled_for_experiment(False)
    )
    window.manager.aborted.connect(
        lambda: control.set_enabled_for_experiment(False)
    )
    window.manager.failed.connect(
        lambda: control.set_enabled_for_experiment(False)
    )

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main(sys.argv)
