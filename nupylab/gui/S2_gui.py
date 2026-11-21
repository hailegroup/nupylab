"""
GUI for S2 impedance station.

This GUI connects to and displays data from
    * Eurotherm 2416 Furnace Controller (via the Eurotherm 2400 driver)
    * Biologic SP-300 Potentiostat (optional)
    * pO2 sensor read by a Keithley 2182 and a Keithley 182 (optional)
    * Solenoid valve driven by a LabJack U12 analog output (manual control only)

Run the program by changing to the directory containing this file and calling:

python S2_gui.py

The Furnace Controller input selects which Eurotherm the station is wired to,
and defaults to the 2400 family used by the 2416 on S2. The Manual Control tab
connects using the port, address, and model settings in the inputs panel, and
is rebuilt whenever the furnace type changes.

The pO2 sensor is split across two meters on S2: the Keithley 2182A at
GPIB0::7 measures the sensor thermocouple on its channel 1, and the Keithley 182
at GPIB0::8 measures the sensor potential. The Manual Control tab reads both and
shows the sensor temperature, the pO2 in atm, and the sensor voltage, whether or
not an experiment is queued. A queued experiment records all three as well,
unless neither meter answers, in which case it runs without them.

The solenoid valve is driven from the LabJack U12's AO1 output and appears in
the Manual Control tab only, not in queued experiments. Its selector follows
the station's LabVIEW front panel, where `True` applies no power to the valve
and `False` powers it, at a drive voltage set from the panel. Connecting and
disconnecting leave the valve exactly as it is, so it only moves when the user
applies a state.

The calibration coefficients `a` and `b` are read from a text file, selectable
both from the inputs panel and from the Manual Control tab, and default to zero
until one is chosen. See `po2_calibration_example.txt` in
`nupylab/instruments/o2_sensor` for the file format.
"""

import logging
import sys
import time
from typing import Dict, List, Tuple, Type, Union

# Instrument Imports #
from nupylab.instruments.ac_potentiostat.biologic import Biologic as Potentiostat
from nupylab.instruments.ac_potentiostat.biologic import eis_value_errors
from nupylab.instruments.heater.eurotherm2200 import Eurotherm2200
from nupylab.instruments.heater.eurotherm2400 import Eurotherm2400
from nupylab.instruments.heater.eurotherm3216 import Eurotherm3216
from nupylab.instruments.o2_sensor.keithley2182_182 import (
    Keithley2182And182 as PO2_Sensor,
)
from nupylab.instruments.valve.labjack_u12 import LabJackSolenoid
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
# The control log writes straight into a widget, so anything logged off the GUI
# thread, i.e. from a running procedure, goes to the standard log instead
log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

# S2 runs a Eurotherm 2416, which the Eurotherm 2400 driver covers.
FURNACE_TYPES: Dict[str, Type] = {
    "Eurotherm 2400": Eurotherm2400,
    "Eurotherm 2200": Eurotherm2200,
    "Eurotherm 3216": Eurotherm3216,
}

FURNACE_DATA_LABEL = "Furnace Temperature (degC)"

PO2_DATA_LABELS = (
    "pO2 Sensor Temperature (degC)",
    "pO2 (atm)",
    "pO2 Sensor Voltage (V)",
)

# S2's pO2 sensor is read by two GPIB meters at these addresses
PO2_NANOVOLTMETER_PORT = "GPIB0::7::INSTR"  # Keithley 2182A, sensor thermocouple
PO2_VOLTMETER_PORT = "GPIB0::8::INSTR"  # Keithley 182, sensor potential


def resource_choices(resources, *expected):
    """Offer the station's known addresses whether or not they answered.

    `list_resources` only reports instruments that are powered on and on the
    bus, so without this a meter switched on after the GUI started could not be
    selected at all.

    Args:
        resources: VISA resources found on this machine.
        *expected: resource names to offer even when they were not found.

    Returns:
        Tuple of resource names, found ones first.
    """
    choices = list(resources)
    for resource in expected:
        if resource not in choices:
            choices.append(resource)
    return tuple(choices)


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
    # The pO2 meters sit at fixed GPIB addresses on S2
    po2_resources = resource_choices(
        resources, PO2_NANOVOLTMETER_PORT, PO2_VOLTMETER_PORT
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

    # pO2 Sensor Parameters
    po2_nanovoltmeter_port: ListParameter = ListParameter(
        "Keithley 2182 Port",
        choices=po2_resources,
        default=PO2_NANOVOLTMETER_PORT,
        ui_class=None,
    )
    po2_voltmeter_port: ListParameter = ListParameter(
        "Keithley 182 Port",
        choices=po2_resources,
        default=PO2_VOLTMETER_PORT,
        ui_class=None,
    )
    po2_calibration_file: Parameter = Parameter(
        "pO2 Calibration File", default="", ui_class=None
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
        "Phase (degrees)",
        "pO2 Sensor Temperature (degC)",
        "pO2 (atm)",
        "pO2 Sensor Voltage (V)",
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
    # An entry naming two columns gives one plot with left and right y axes
    X_AXIS: List[str] = ["Z_re (ohm)","Frequency (Hz)", "Time (s)"]
    Y_AXIS: List[Union[str, Tuple[str, str]]] = [
        "-Z_im (ohm)",
        "|Z| (ohm)",
        "Phase (degrees)",
        ("Ewe (V)", "I (A)"),
        "Furnace Temperature (degC)",
        "pO2 (atm)",
        ("pO2 Sensor Temperature (degC)", "pO2 Sensor Voltage (V)"),
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
        "po2_nanovoltmeter_port",
        "po2_voltmeter_port",
        "po2_calibration_file",
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
            furnace, potentiostat, po2_sensor = self.previous_procedure.instruments
        else:
            furnace = make_heater(
                self.furnace_type, self.furnace_port, self.furnace_address
            )
            # The calibration file is applied below, and only when the pO2
            # sensor is in use, so a stale path cannot fail an unrelated step
            po2_sensor = PO2_Sensor(
                self.po2_nanovoltmeter_port,
                self.po2_voltmeter_port,
                PO2_DATA_LABELS,
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
        self.instruments = (furnace, potentiostat, po2_sensor)
        furnace.set_parameters(self.target_temperature, self.ramp_rate, self.dwell_time)
        if str(self.eis_toggle).lower() == "true":
            self.active_instruments = [furnace, potentiostat]
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
            self.active_instruments = [furnace]
        # There is no pO2 toggle in the inputs panel, so the sensor joins the
        # measurement whenever its two meters answer. Connecting here rather
        # than leaving it to `startup` keeps a station whose Keithleys are off
        # from failing every queued step.
        if self.po2_calibration_file:
            po2_sensor.set_calibration_file(self.po2_calibration_file)
        try:
            if not po2_sensor.connected:
                po2_sensor.connect()
            self.active_instruments.append(po2_sensor)
        except Exception as e:
            log.warning("pO2 sensor unavailable, continuing without it: %s", e)


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

    # The two Keithleys are only opened when the pO2 panel connects, so this
    # costs nothing if the station is not wired to a pO2 sensor.
    po2_sensor = PO2_Sensor(
        inputs.po2_nanovoltmeter_port.value(),
        inputs.po2_voltmeter_port.value(),
        PO2_DATA_LABELS,
    )

    # The LabJack driver is only loaded when the valve panel connects, so a
    # station without one, or without ljackuw.dll, still opens the GUI
    solenoid = LabJackSolenoid()

    # Each controller supplies its own panel, so the Manual Control tab is
    # rebuilt when the furnace type changes rather than patched in place.
    current = {"furnace": None, "control": None, "type": None}

    def control_instruments():
        """Instruments currently shown in the Manual Control tab."""
        instruments = []
        if current["furnace"] is not None:
            instruments.append(current["furnace"])
        if potentiostat is not None:
            instruments.append(potentiostat)
        instruments.append(po2_sensor)
        instruments.append(solenoid)
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

    def build_control_tab():
        """(Re)build the Manual Control tab for the selected furnace type."""
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
        window.tabs.addTab(control, "Manual Control")

        # A calibration file picked in the pO2 panel also belongs in the inputs
        # panel, since queued experiments build their own pO2 sensor from it.
        panel = getattr(po2_sensor, "_panel", None)
        if panel is not None:
            panel.calibration_changed.connect(mirror_po2_calibration)

    def mirror_po2_calibration():
        """Show the loaded calibration file path in the inputs panel."""
        path = po2_sensor.calibration_file
        if path and path != inputs.po2_calibration_file.value():
            inputs.po2_calibration_file.setValue(path)

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
            po2_sensor.set_connection(
                inputs.po2_nanovoltmeter_port.value(),
                inputs.po2_voltmeter_port.value(),
            )
        except Exception as e:
            _control_log.warning("Could not read connection settings: %s", e)

    def sync_po2_calibration(*_):
        """Load the calibration file named in the inputs panel, if any.

        The Manual Control panel has its own file picker; this only runs when
        the inputs panel entry is edited, so it does not override that choice.
        """
        path = inputs.po2_calibration_file.value().strip()
        if not path or path == po2_sensor.calibration_file:
            return
        try:
            a, b = po2_sensor.set_calibration_file(path)
            _control_log.info(
                "pO2 calibration loaded from %s: a = %.6g, b = %.6g", path, a, b
            )
        except Exception as e:
            _control_log.error("pO2 calibration file failed: %s", e)

    def on_furnace_type_changed(*_):
        """Swap the control tab over when a different controller is selected."""
        if inputs.furnace_type.value() == current["type"]:
            return
        _control_log.info(
            "Furnace controller changed to %s, rebuilding Manual Control tab",
            inputs.furnace_type.value(),
        )
        build_control_tab()

    build_control_tab()
    if potentiostat_error is not None:
        _control_log.error(
            "Biologic control panel unavailable: %s", potentiostat_error
        )

    def connect_input(name, callback, signals=None):
        element = getattr(inputs, name)
        for signal in signals or ("currentTextChanged", "valueChanged", "textChanged"):
            if hasattr(element, signal):
                getattr(element, signal).connect(callback)
                break

    for name in (
        "furnace_port", "furnace_address", "potentiostat_port", "potentiostat_model",
        "po2_nanovoltmeter_port", "po2_voltmeter_port",
    ):
        connect_input(name, sync_connection_settings)
    connect_input("furnace_type", on_furnace_type_changed)
    # Wait for the whole path rather than loading on every keystroke
    connect_input(
        "po2_calibration_file",
        sync_po2_calibration,
        ("editingFinished", "textChanged"),
    )
    sync_po2_calibration()

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
