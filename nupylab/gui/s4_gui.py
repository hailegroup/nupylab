"""
GUI for S4 impedance station.

This GUI connects to and displays data from
    * ROD-4 MFC Controller
    * Eurotherm 2416 Furnace Controller
    * Keithley 2182 Nanovoltmeter (pO2 sensor, optional)
    * Biologic SP-200 Potentiostat (optional)

Run the program by changing to the directory containing this file and calling:

python s4_gui.py
"""

import sys
from typing import Dict, List

from nupylab.instruments.ac_potentiostat.biologic import (
    Biologic as Potentiostat,
)
# Instrument Imports #
from nupylab.instruments.heater.eurotherm2400 import Eurotherm2400 as Heater
from nupylab.instruments.mfc.rod4 import ROD4 as MFC
from nupylab.instruments.o2_sensor.keithley2182 import Keithley2182 as PO2_Sensor
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


class S4Procedure(nupylab_procedure.NupylabProcedure):
    """Procedure for running high impedance station GUI.

    Running this procedure calls startup, execute, and shutdown methods sequentially.
    In addition to the parameters listed below, this procedure inherits `record_time`,
    `num_steps`, and `current_steps` from parent class.
    """

    # Units in parentheses must be valid pint units
    # First two entries must be "System Time" and "Time (s)"
    DATA_COLUMNS: List[str] = [
        "System Time",
        "Time (s)",
        "Furnace Temperature (degC)",
        "MFC 1 Flow (cc/min)",
        "MFC 2 Flow (cc/min)",
        "MFC 3 Flow (cc/min)",
        "MFC 4 Flow (cc/min)",
        "pO2 Sensor Temeprature (degC)",
        "pO2 (atm)",
        "pO2 Sensor Voltage (V)",
        "Ewe (V)",
        "Frequency (Hz)",
        "Z_re (ohm)",
        "-Z_im (ohm)",
    ]

    resources = list_resources()

    furnace_port = ListParameter("Eurotherm Port", choices=resources)
    furnace_address = IntegerParameter(
        "Eurotherm Address", minimum=1, maximum=254, step=1, default=1
    )
    mfc_port = ListParameter("ROD-4 Port", choices=resources)
    potentiostat_port = Parameter("Biologic Port", default="192.109.209.128")
    po2_sensor_port = ListParameter("Keithley Port", choices=resources)

    target_temperature = FloatParameter("Target Temperature", units="C")
    ramp_rate = FloatParameter("Ramp Rate", units="C/min")
    dwell_time = FloatParameter("Dwell Time", units="min")

    mfc_1_setpoint = FloatParameter("MFC 1 Setpoint", units="sccm")
    mfc_2_setpoint = FloatParameter("MFC 2 Setpoint", units="sccm")
    mfc_3_setpoint = FloatParameter("MFC 3 Setpoint", units="sccm")
    mfc_4_setpoint = FloatParameter("MFC 4 Setpoint", units="sccm")

    po2_toggle = BooleanParameter("pO2 Sensor Connected", default=True)
    po2_slope = FloatParameter("pO2 Sensor Cal Slope", group_by="pO2_toggle")
    po2_intercept = FloatParameter("pO2 Sensor Cal Intercept", group_by="pO2_toggle")

    eis_toggle = BooleanParameter("Run eis")
    maximum_frequency = FloatParameter("Maximum Frequency", units="Hz")
    minimum_frequency = FloatParameter("Minimum Frequency", units="Hz")
    amplitude_voltage = FloatParameter("Amplitude Voltage", units="V")
    points_per_decade = IntegerParameter("Points Per Decade")

    TABLE_PARAMETERS: Dict[str, str] = {
        "Target Temperature [C]": "target_temperature",
        "Ramp Rate [C/min]": "ramp_rate",
        "Dwell Time [min]": "dwell_time",
        "MFC 1 [sccm]": "mfc_1_setpoint",
        "MFC 2 [sccm]": "mfc_2_setpoint",
        "MFC 3 [sccm]": "mfc_3_setpoint",
        "MFC 4 [sccm]": "mfc_4_setpoint",
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
        "Furnace Temperature (degC)",
        "pO2 Sensor Temperature (degC)",
        "pO2 (atm)",
        "pO2 Sensor Voltage (V)",
        "MFC 1 Flow (cc/min)",
        "MFC 2 Flow (cc/min)",
        "MFC 3 Flow (cc/min)",
        "MFC 4 Flow (cc/min)",
    ]
    # Inputs must match name of selected procedure parameters
    INPUTS: List[str] = [
        "record_time",
        "furnace_port",
        "furnace_address",
        "mfc_port",
        "potentiostat_port",
        "po2_sensor_port",
    ]

    def set_instruments(self) -> None:
        """Set and configure instruments list.

        Pass in connections from previous step, if applicable, otherwise create new
        instances. Send current step parameters to appropriate instruments.

        It is required for this method to create non-empty `instruments` and
        `active_instruments` attributes.
        """
        if self.previous_procedure is not None:
            furnace, mfc, potentiostat, po2_sensor = self.previous_procedure.instruments
        else:
            furnace = Heater(
                self.furnace_port, self.furnace_address, "Furnace Temperature (degC)"
            )
            mfc = MFC(
                self.mfc_port,
                (
                    "MFC 1 Flow (cc/min)",
                    "MFC 2 Flow (cc/min)",
                    "MFC 3 Flow (cc/min)",
                    "MFC 4 Flow (cc/min)",
                ),
            )
            potentiostat = Potentiostat(
                self.potentiostat_port,
                "SP200",
                0,
                ("Ewe (V)", "Frequency (Hz)", "Z_re (ohm)", "-Z_im (ohm)"),
            )
            po2_sensor = PO2_Sensor(
                self.po2_sensor_port,
                self.po2_intercept,
                self.po2_slope,
                (
                    "pO2 Sensor Temeprature (degC)",
                    "pO2 (atm)",
                    "pO2 Sensor Voltage (V)",
                ),
            )
        self.instruments = (furnace, mfc, potentiostat, po2_sensor)
        self.active_instruments = [furnace, mfc]
        furnace.set_parameters(self.target_temperature, self.ramp_rate, self.dwell_time)
        mfc.set_parameters(
            (
                self.mfc_1_setpoint,
                self.mfc_2_setpoint,
                self.mfc_3_setpoint,
                self.mfc_4_setpoint,
            )
        )
        if self.eis_toggle:
            potentiostat.set_parameters(
                self.record_time,
                self.maximum_frequency,
                self.minimum_frequency,
                self.amplitude_voltage,
                self.points_per_decade,
                "PEIS",
                lambda: furnace.finished,
            )
            self.active_instruments.append(potentiostat)
        if self.po2_toggle:
            self.active_instruments.append(po2_sensor)


def main():
    """Run S4 procedure."""
    app = QtWidgets.QApplication(sys.argv)
    window = nupylab_window.NupylabWindow(S4Procedure)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
