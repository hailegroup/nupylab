"""
GUI for S2 impedance station.

This GUI connects to and displays data from
    * Eurotherm 2416 Furnace Controller (via the Eurotherm 2400 driver)
    * Biologic SP-300 Potentiostat (optional)

Run the program by changing to the directory containing this file and calling:

python S2_gui.py

The Furnace Controller input selects which Eurotherm the station is wired to,
and defaults to the 2400 family used by the 2416 on S2. The Instrument Control
tab connects using the port, address, and model settings in the inputs panel,
and is rebuilt whenever the furnace type changes. Its Detect Port button scans
the serial ports for a controller that answers and selects it here.
"""

import logging
import sys
import time
from typing import Dict, List, Type

# Instrument Imports #
from nupylab.instruments.ac_potentiostat.biologic import Biologic as Potentiostat
from nupylab.instruments.ac_potentiostat.biologic import eis_value_errors
from nupylab.instruments.heater.eurotherm2200 import Eurotherm2200
from nupylab.instruments.heater.eurotherm2400 import Eurotherm2400
from nupylab.instruments.heater.eurotherm3216 import Eurotherm3216
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

# S2 runs a Eurotherm 2416, which the Eurotherm 2400 driver covers.
FURNACE_TYPES: Dict[str, Type] = {
    "Eurotherm 2400": Eurotherm2400,
    "Eurotherm 2200": Eurotherm2200,
    "Eurotherm 3216": Eurotherm3216,
}

FURNACE_DATA_LABEL = "Furnace Temperature (degC)"


def make_heater(furnace_type, port, address, data_label=FURNACE_DATA_LABEL):
    """Build the selected furnace controller.

    Args:
        furnace_type: key of :data:`FURNACE_TYPES`, e.g. `Eurotherm 2400`.
        port: string name of port, e.g. `COM1` or `ASRL1::INSTR`.
        address: integer address of the Eurotherm. Ignored by the 3216, whose
            class takes no address and always uses 1.
        data_label: label for DataTuple, must match an entry in DATA_COLUMNS.

    Returns:
        NupylabInstrument subclass instance for the selected controller.
    """
    heater_class = FURNACE_TYPES[str(furnace_type)]
    if heater_class is Eurotherm3216:
        return heater_class(port, data_label)
    return heater_class(port, address, data_label)


class S2Procedure(nupylab_procedure.NupylabProcedure):
    """Procedure for running high impedance station GUI.

    Running this procedure calls startup, execute, and shutdown methods sequentially.
    In addition to the parameters listed below, this procedure inherits `record_time`,
    `num_steps`, and `current_steps` from parent class.
    """

    resources = list_resources() or ("No instruments found",)
    Potentiostat_options = ["Biologic",]
    Biologic_models = ["SP200", "SP300"]
    EIS_techniques = ["PEIS", "SPEIS", "GEIS", "SGEIS"]
    Furnace_options = list(FURNACE_TYPES)

    # S2's furnace controller sits on COM1
    _default_furnace = (
        "ASRL1::INSTR" if "ASRL1::INSTR" in resources else resources[0]
    )

    #Furnace parameters
    furnace_type: ListParameter = ListParameter(
        "Furnace Controller",
        choices=Furnace_options,
        default="Eurotherm 2400",
        ui_class=None,
    )
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
    potentiostat_technique = ListParameter(
        "EIS Technique", default="PEIS", choices=EIS_techniques, ui_class=None
    )

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
        "EIS Technique": "potentiostat_technique",
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
        "furnace_type",
        "furnace_port",
        "furnace_address",
        "potentiostat",
        "potentiostat_model",
        "potentiostat_port",
    ]

    @classmethod
    def table_row_errors(cls, row: Dict[str, str]) -> Dict[str, str]:
        """Check one parameters table row before queueing.

        Args:
            row: parameter name to cell text for one table row.

        Returns:
            Parameter name to error message for each invalid cell.
        """
        if str(row.get("eis_toggle", "")).strip().casefold() not in (
            "true", "t", "yes", "1"
        ):
            return {}
        technique = str(row.get("potentiostat_technique", "")).strip().upper()
        if technique not in cls.EIS_techniques:
            technique = cls.potentiostat_technique.default
        try:
            initial_step = float(row["initial_step"])
            amplitude = float(row["amplitude_voltage"])
        except (KeyError, TypeError, ValueError):
            return {}  # Blank or non-numeric cells are reported when queueing
        errors = eis_value_errors(technique, initial_step, amplitude)
        return {
            {"initial_step": "initial_step", "amplitude": "amplitude_voltage"}[k]: v
            for k, v in errors.items()
        }

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
            furnace = make_heater(
                self.furnace_type, self.furnace_port, self.furnace_address
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
    """Run S2 procedure."""
    app = QtWidgets.QApplication(*args)
    window = nupylab_window.NupylabWindow(S2Procedure)
    inputs = window.inputs

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
    except Exception as e:
        potentiostat_error = e

    # Each controller supplies its own panel, so the Instrument Control tab is
    # rebuilt when the furnace type changes rather than patched in place.
    current = {"furnace": None, "control": None, "type": None}

    def control_instruments():
        """Instruments currently shown in the Instrument Control tab."""
        instruments = []
        if current["furnace"] is not None:
            instruments.append(current["furnace"])
        if potentiostat is not None:
            instruments.append(potentiostat)
        return instruments

    def disconnect_control_instruments():
        for inst in control_instruments():
            if inst.connected:
                try:
                    inst.disconnect()
                except Exception:
                    pass
        time.sleep(0.5)

    def abort_experiment():
        try:
            window.manager.abort()
        except Exception:
            pass
        for inst in control_instruments():
            if inst.connected:
                try:
                    inst.disconnect()
                except Exception:
                    pass

    def on_port_detected(port):
        """Select the port the control tab just found in the inputs panel."""
        combo = inputs.furnace_port
        index = combo.findText(port) if hasattr(combo, "findText") else -1
        if index >= 0:
            combo.setCurrentIndex(index)
        else:
            _control_log.info(
                "Detected %s, which is not in the Eurotherm Port list", port
            )

    def build_control_tab():
        """(Re)build the Instrument Control tab for the selected furnace type."""
        furnace_type = inputs.furnace_type.value()
        previous = current["control"]
        if previous is not None:
            disconnect_control_instruments()
            index = window.tabs.indexOf(previous)
            if index >= 0:
                window.tabs.removeTab(index)
            previous.deleteLater()

        current["furnace"] = make_heater(
            furnace_type,
            inputs.furnace_port.value(),
            inputs.furnace_address.value(),
        )
        current["type"] = furnace_type

        control = InstrumentControlWidget(
            control_instruments(),
            abort_callback=abort_experiment,
            directory=lambda: window.directory,
            recording_beside_panels=True,
            fit_panels_height=True,
            button_feedback=True,
        )
        current["control"] = control
        window.tabs.addTab(control, "Instrument Control")

        panel = getattr(current["furnace"], "_panel", None)
        if panel is not None and hasattr(panel, "port_detected"):
            panel.port_detected.connect(on_port_detected)

    def sync_connection_settings(*_):
        """Apply inputs panel connection settings on next control tab connect."""
        try:
            furnace = current["furnace"]
            if furnace is not None:
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

    def on_furnace_type_changed(*_):
        """Swap the control tab over when a different controller is selected."""
        if inputs.furnace_type.value() == current["type"]:
            return
        _control_log.info(
            "Furnace controller changed to %s, rebuilding Instrument Control tab",
            inputs.furnace_type.value(),
        )
        build_control_tab()

    build_control_tab()
    if potentiostat_error is not None:
        _control_log.error(
            "Biologic control panel unavailable: %s", potentiostat_error
        )

    def connect_input(name, callback):
        element = getattr(inputs, name)
        for signal in ("currentTextChanged", "valueChanged", "textChanged"):
            if hasattr(element, signal):
                getattr(element, signal).connect(callback)
                break

    for name in (
        "furnace_port", "furnace_address", "potentiostat_port", "potentiostat_model"
    ):
        connect_input(name, sync_connection_settings)
    connect_input("furnace_type", on_furnace_type_changed)

    window.manager.queued.connect(disconnect_control_instruments)
    window.manager.running.connect(
        lambda: current["control"].set_enabled_for_experiment(True)
    )
    window.manager.finished.connect(
        lambda: current["control"].set_enabled_for_experiment(False)
    )
    window.manager.aborted.connect(
        lambda: current["control"].set_enabled_for_experiment(False)
    )
    window.manager.failed.connect(
        lambda: current["control"].set_enabled_for_experiment(False)
    )

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main(sys.argv)
