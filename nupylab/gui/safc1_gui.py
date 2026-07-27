"""
GUI for SAFC2 station.

This GUI connects to and displays data from
    * ROD-4 MFC Controller
    * Eurotherm 3216 Furnace Controller
    * Keithley 705 Scanner
    * Agilent 4284A LCR Meter
    * HP 3478A Multimeter

Run the program by changing to the directory containing this file and calling:

python safc2_gui.py

Configs:
Furnace: COM9
ROD4: COM3
Potentiostat: 20
TC: 15
Scanner: 17
"""

import sys
import time
from typing import Dict, List

# Instrument Imports #
from nupylab.instruments.ac_potentiostat.agilent4284A import (
    Agilent4284A as Potentiostat,
)
from nupylab.utilities.instrument_control import InstrumentControlWidget
from nupylab.instruments.heater.eurotherm3216 import Eurotherm3216 as Heater
from nupylab.instruments.mfc.rod4 import ROD4 as MFC
from nupylab.instruments.scanner.keithley705 import Keithley705 as Scanner
from nupylab.instruments.thermocouple_sensor.hp3478A import HP3478A as TC_Sensor
######################
from nupylab.utilities import list_resources, nupylab_procedure, nupylab_window
from pymeasure.display.Qt import QtWidgets
from pymeasure.experiment import (
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
    record_time = FloatParameter("Record Time", units="s", default=3.0)

    _default_furnace = "ASRL9::INSTR" if "ASRL9::INSTR" in resources else (resources[0] if resources else "")
    _default_mfc = "ASRL3::INSTR" if "ASRL3::INSTR" in resources else (resources[0] if resources else "")
    _default_potentiostat = "GPIB0::20::INSTR" if "GPIB0::20::INSTR" in resources else (resources[0] if resources else "")
    _default_tc = "GPIB0::15::INSTR" if "GPIB0::15::INSTR" in resources else (resources[0] if resources else "")
    _default_scanner = "GPIB0::17::INSTR" if "GPIB0::17::INSTR" in resources else (resources[0] if resources else "")

    furnace_port = ListParameter("Eurotherm Port", choices=resources, default=_default_furnace, ui_class=None)
    mfc_port = ListParameter("ROD-4 Port", choices=resources, default=_default_mfc, ui_class=None)
    potentiostat_port = ListParameter("Potentiostat Port", choices=resources, default=_default_potentiostat, ui_class=None)
    tc_sensor_port = ListParameter("TC Sensor Port", choices=resources, default=_default_tc, ui_class=None)
    room_temp = FloatParameter("Cold Junction Temperature", units="C°", step=1, default=31.0)
    scanner_port = ListParameter("Scanner Port", choices=resources, default=_default_scanner, ui_class=None)

    target_temperature = FloatParameter("Target Temperature", units="C")
    ramp_rate = FloatParameter("Ramp Rate", units="C/min")
    dwell_time = FloatParameter("Dwell Time", units="min")

    mfc_1_setpoint = FloatParameter("MFC 1 Setpoint", units="sccm")
    mfc_2_setpoint = FloatParameter("MFC 2 Setpoint", units="sccm")
    mfc_3_setpoint = FloatParameter("MFC 3 Setpoint", units="sccm")
    mfc_4_setpoint = FloatParameter("MFC 4 Setpoint", units="sccm")

    eis_toggle = ListParameter("Run EIS", choices=["True", "False"], ui_class=None)
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
        "MFC 1 Flow (cc/min)",
    ]
    # Inputs must match name of selected procedure parameters
    INPUTS: List[str] = [
        "record_time",
        "furnace_port",
        "mfc_port",
        "potentiostat_port",
        "tc_sensor_port",
        "room_temp",
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
                self.furnace_port, "Furnace Temperature (degC)"
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
            digits = 5
            if self.record_time < 3:
                digits = 4
            elif self.record_time < 2:
                digits = 3
            tc_sensor = TC_Sensor(self.tc_sensor_port, "1: Temperature (degC)", digits, self.room_temp)
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
        scanner.set_parameters(2, tc_sensor, "1: Temperature (degC)")
        scanner.set_parameters(3, tc_sensor, "2: Temperature (degC)")
        scanner.set_parameters(4, tc_sensor, "3: Temperature (degC)")
        tc_sensor.connect()
        potentiostat.connect()
        if str(self.eis_toggle).lower() == "true":
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

    # Create instrument instances for control panel.
    # These are NOT connected at startup — user clicks Connect in the panel.
    # They use separate instances from the experiment so ports aren't shared.
    furnace = Heater("ASRL9::INSTR", "Furnace Temperature (degC)")
    mfc = MFC(
        "ASRL3::INSTR",
        (
            "MFC 1 Flow (cc/min)",
            "MFC 2 Flow (cc/min)",
            "MFC 3 Flow (cc/min)",
            "MFC 4 Flow (cc/min)",
        )
    )
    potentiostat = Potentiostat(
        "GPIB0::20::INSTR",
        ("Frequency(Hz)", "Z_re (ohm)", "-Z_im (ohm)")
    )

    # Window must be created before control so abort_callback can reference manager
    window = nupylab_window.NupylabWindow(
        SAFCProcedure,
        directory="C:/Users/HaileResearch/nupylab/data",
        parameters_dir="C:/Users/HaileResearch/nupylab/parameters",
    )

    def abort_experiment():
        """Abort any running experiment and disconnect control instruments."""
        try:
            window.manager.abort()
        except Exception:
            pass
        # Also disconnect control instruments to free ports for experiment
        for inst in [furnace, mfc, potentiostat]:
            if inst.connected:
                try:
                    inst.disconnect()
                except Exception:
                    pass

    control = InstrumentControlWidget(
        [furnace, mfc, potentiostat],
        abort_callback=abort_experiment
    )

    #Add control tab after window is created
    window.tabs.addTab(control, "Instrument Control")

    def disconnect_control_instruments():
        """Disconnect control panel instruments before experiment starts."""
        for inst in [furnace, mfc, potentiostat]:
            if inst.connected:
                try:
                    inst.disconnect()
                except Exception:
                    pass
        time.sleep(0.5)

    #Disconnect control instruments when experiment is queued/started
    window.manager.queued.connect(disconnect_control_instruments)

    #Track experiment state for abort logic
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