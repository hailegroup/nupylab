"""
GUI for SAFC station.

This GUI connects to and displays data from
    * ROD-4 MFC Controller
    * Eurotherm 3216 Furnace Controller
    * Keithley 705 Scanner
    * Agilent 4284A LCR Meter
    * HP 3478A Multimeter

Run the program by changing to the directory containing this file and calling:

python safc_gui.py
"""

import sys
from typing import Dict, List

# Instrument Imports #
from nupylab.instruments.ac_potentiostat.agilent4284A import (
    Agilent4284A as Potentiostat,
)
from nupylab.instruments.heater.eurotherm3216 import Eurotherm3216 as Heater
from nupylab.instruments.mfc.rod4 import ROD4 as MFC
from nupylab.instruments.scanner.keithley705 import Keithley705 as Scanner
from nupylab.instruments.thermocouple_sensor.hp3478A import HP3478A as TC_Sensor
######################
from nupylab.utilities import list_resources, nupylab_procedure, nupylab_window
from pymeasure.display.Qt import QtWidgets
from pymeasure.experiment import (
    BooleanParameter,
    FloatParameter,
    IntegerParameter,
    ListParameter,
)


class SAFCProcedure(nupylab_procedure.NupylabProcedure):
    """Procedure for running high impedance station GUI.

    Running this procedure calls startup, execute, and shutdown methods sequentially.
    In addition to the parameters listed below, this procedure inherits `record_time`,
    `num_steps`, and `current_steps` from parent class.
    """

    # Units in parentheses must be valid pint units
    DATA_COLUMNS: List[str] = [
        "System Time",
        "Time (s)",
        "Furnace Temperature (degC)",
        "1: Temperature (degC)",
        "2: Temperature (degC)",
        "3: Temperature (degC)",
        "MFC 1 Flow (cc/min)",
        "MFC 2 Flow (cc/min)",
        "MFC 3 Flow (cc/min)",
        "MFC 4 Flow (cc/min)",
        "Frequency (Hz)",
        "Z_re (ohm)",
        "-Z_im (ohm)",
    ]

    resources = list_resources()

    furnace_port = ListParameter("Eurotherm Port", choices=resources, ui_class=None)
    furnace_address = IntegerParameter(
        "Eurotherm Address", minimum=1, maximum=254, step=1, default=1
    )
    mfc_port = ListParameter("ROD-4 Port", choices=resources, ui_class=None)
    potentiostat_port = ListParameter(
        "Potentiostat Port", choices=resources, ui_class=None
    )
    tc_sensor_port = ListParameter("TC Sensor Port", choices=resources, ui_class=None)
    scanner_port = ListParameter("Scanner Port", choices=resources, ui_class=None)

    target_temperature = FloatParameter("Target Temperature", units="C")
    ramp_rate = FloatParameter("Ramp Rate", units="C/min")
    dwell_time = FloatParameter("Dwell Time", units="min")

    mfc_1_setpoint = FloatParameter("MFC 1 Setpoint", units="sccm")
    mfc_2_setpoint = FloatParameter("MFC 2 Setpoint", units="sccm")
    mfc_3_setpoint = FloatParameter("MFC 3 Setpoint", units="sccm")
    mfc_4_setpoint = FloatParameter("MFC 4 Setpoint", units="sccm")

    eis_toggle = BooleanParameter("Run EIS")
    eis_sample = IntegerParameter("EIS Sample Number")
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
        "EIS? [True/False]": "eis_toggle",
        "EIS Sample Number": "eis_sample",
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
        "1: Temperature (degC)",
        "2: Temperature (degC)",
        "3: Temperature (degC)",
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
        "tc_sensor_port",
        "scanner_port",
    ]

    def set_instruments(self) -> None:
        """Set and configure instruments list.

        Pass in connections from previous step, if applicable, otherwise create new
        instances. Send current step parameters to appropriate instruments.

        It is required for this method to create non-empty `instruments` and
        `active_instruments` attributes.
        """
        if self.previous_procedure:
            furnace, mfc, potentiostat, tc_sensor, scanner = (
                self.previous_procedure.instruments
            )
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
                ("1: Frequency (Hz)", "1: Z_re (ohm)", "1: -Z_im (ohm)"),
            )
            tc_sensor = TC_Sensor(self.tc_sensor_port, "1: Temperature (degC)")
            scanner = Scanner(self.scanner_port)
        self.instruments = (furnace, mfc, potentiostat, tc_sensor, scanner)
        furnace.set_parameters(self.target_temperature, self.ramp_rate, self.dwell_time)
        mfc.set_parameters(
            (
                self.mfc_1_setpoint,
                self.mfc_2_setpoint,
                self.mfc_3_setpoint,
                self.mfc_4_setpoint,
            )
        )
        scanner.set_parameters(
            1, tc_sensor, "cj_volt", lambda: setattr(tc_sensor, "cj_flag", True)
        )
        scanner.set_parameters(2, tc_sensor, "1: Temperature (degC)")
        scanner.set_parameters(3, tc_sensor, "2: Temperature (degC)")
        scanner.set_parameters(4, tc_sensor, "3: Temperature (degC)")
        if self.eis_toggle:
            potentiostat.set_parameters(
                self.maximum_frequency,
                self.minimum_frequency,
                self.amplitude_voltage,
                self.points_per_decade,
                "PEIS",
                lambda: furnace.finished,
            )
            # EIS channels are 10 + TC channels, and channel 1 is internal cj voltage
            scanner.set_parameters(
                self.eis_sample + 11,
                potentiostat,
                ("Frequency(Hz)", "Z_re (ohm)", "-Z_im (ohm)"),
            )
        self.active_instruments = (furnace, mfc, scanner)


def main(*args):
    """Run SAFC procedure."""
    app = QtWidgets.QApplication(*args)
    window = nupylab_window.NupylabWindow(SAFCProcedure)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main(sys.argv)
