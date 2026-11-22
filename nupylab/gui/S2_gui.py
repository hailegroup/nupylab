"""
GUI for S2 impedance station.

This GUI connects to and displays data from
    * Eurotherm 2416 Furnace Controller (via the Eurotherm 2400 driver)
    * Biologic SP-300 Potentiostat (optional)
    * pO2 sensor read by a Keithley 2182 and a Keithley 182 (optional)
    * Four Aera FC-PA78x mass flow controllers on one RS-485 port
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

The four mass flow controllers are Aera FC-PA78x units sharing one serial port
through an RS-485 converter, at the addresses :data:`MFC_ADDRESSES`. The GUI
calls them MFC 1 to MFC 4 in that order. An Aera is set and read in percent of
its own range, so each one carries the gas it is flowing and a flow
calibration, `Q = m p + b`, which turns that percent into sccm. Both are chosen
in the inputs panel, one row per MFC: the gas from a drop-down which also
offers `Add New Gas...`, and the calibration from the same kind of dialog the
pO2 sensor uses. Under each of those rows is one line saying when that MFC
was last calibrated and in what gas, or, in red, that it has no calibration
for the gas it is set to. Nothing is asked at launch: the red line is the
whole of it until a gas is changed, when the calibration for the new pair is
asked for if there is none. A calibration more than a year old is marked with a
red exclamation mark beside its button, and the MFCs that carries are named
in one red line under the last of them.

The gas last selected for each MFC is remembered in
`nupylab/gui/s2_settings.json`, and the newest calibration recorded for that
MFC in that gas is loaded at startup. Selecting a different gas looks the
calibration up again for the new pair, and asks for one when the pair has none.

Every MFC calibration the station has taken lives in one file,
`nupylab/instruments/mfc/mfc_calibrations.csv`, a row per calibration of its
date, who took it, which MFC, the gas it was taken in, the gas it is used for,
and the coefficients `m` and `b`. The two gas columns let a calibration taken
in one gas be used for a similar one, say a synthetic air calibration used for
10% O2 in Ar, while recording that that is what happened. An MFC with no
calibration for the gas it is flowing can still be set and read in percent; its
flow in sccm reads blank, and a setpoint in sccm is refused rather than
guessed. Closing an MFC never needs a calibration.

Each MFC records four columns, its setpoint and its flow, each in percent and
in sccm. The sccm columns are named `cc/min`, which is the same number under a
name PyMeasure's unit parser knows, and is what S4's ROD-4 records its flows
under as well. The Dock Tab plots all four in percent on one dock, a colour
per MFC, each flow solid and its setpoint dashed in the same colour, with a
legend naming the colours. That dock has no x and y selectors above it, since
what it plots never changes, and it keeps only the step that is running: every
step plots against time from zero, so the one before would otherwise be drawn
over in the same four colours. The other docks keep their whole history.

The solenoid valve selects between two gas mixes and is driven from the LabJack
U12's AO1 output. It appears in the Manual Control tab only, not in queued
experiments, and sits under the pO2 sensor panel rather than taking a column of
its own. Gas Mix 1 applies no power to the valve and Gas Mix 2 powers it, at
a drive voltage set in code rather than from the panel, so Apply writes AO1
either 0 V or that voltage and nothing else differs between the two positions.
There is nothing to read back from it, so the panel has no connect button:
Apply opens the LabJack if needed and writes, and the valve moves at no other
time. Each write is recorded to
`~/.nupylab/solenoid_valve.json` and shown in the panel as history, which is
what this GUI wrote rather than where the valve is, since LabVIEW drives the
same output.

Every pO2 calibration the station has taken lives in one file,
`nupylab/instruments/o2_sensor/po2_calibrations.csv`, a row per calibration of
its date, who took it, the gas, and the coefficients `a` and `b`. The inputs
panel loads the newest at startup, shows its coefficients and date, and its
`Add/Select Calibration` button lists them all to take an older one, record a
new one, or point at another file. That one choice is what the Manual Control
tab and a queued experiment both measure with. The panel says in red when the
station has no calibration, and when the one in use is over a year old;
neither stops an experiment running.

`Save Data`, on the pO2 heading, says whether a queued experiment reads the
sensor at all. Unchecked, the sensor is left out of the measurement
and its three columns stay empty, so nothing is read, recorded or plotted for
them; the Manual Control tab still reads it whenever its panel is connected.

The Keithley and MFC ports are chosen in the inputs panel too, and default to
the addresses S2 has them at. A station that picks others has them remembered
in `nupylab/gui/s2_settings.json`, which, like the calibrations, is the
station's own and not kept in version control.
"""

import json
import logging
import os
import sys
import time
from typing import Dict, List, Optional, Tuple, Type, Union

# Instrument Imports #
from nupylab.drivers.aeraFC_PA78x import AeraFCPA78x
from nupylab.instruments.ac_potentiostat.biologic import Biologic as Potentiostat
from nupylab.instruments.ac_potentiostat.biologic import eis_value_errors
from nupylab.instruments.heater.eurotherm2200 import Eurotherm2200
from nupylab.instruments.heater.eurotherm2400 import Eurotherm2400
from nupylab.instruments.heater.eurotherm3216 import Eurotherm3216
from nupylab.instruments.mfc.aera_mfc import AeraMFC as MFCBank
from nupylab.instruments.mfc.mfc_calibration import (
    CALIBRATION_FILE as MFC_CALIBRATION_FILE,
)
from nupylab.instruments.mfc.mfc_calibration import DEFAULT_GASES as MFC_DEFAULT_GASES
from nupylab.instruments.mfc.mfc_calibration import (
    encode_selection as encode_mfc_selection,
)
from nupylab.instruments.mfc.mfc_calibration import load_selection as load_mfc_selection
from nupylab.instruments.mfc.mfc_calibration_dialog import (
    GasInput,
    MFCCalibrationInput,
)
from nupylab.instruments.o2_sensor.keithley2182_182 import (
    Keithley2182And182 as PO2_Sensor,
)
from nupylab.instruments.o2_sensor.keithley2182_182 import (
    CALIBRATION_FILE,
    encode_selection,
    load_selection,
)
from nupylab.instruments.o2_sensor.po2_calibration_dialog import CalibrationInput
from nupylab.instruments.valve.labjack_u12 import LabJackSolenoid
from nupylab.utilities.instrument_control import InstrumentControlWidget
######################
from nupylab.utilities import list_resources, nupylab_procedure, nupylab_window
from nupylab.utilities.dock_widgets import MultiColumnPlotWidget
from pymeasure.display.Qt import QtCore, QtWidgets
from pymeasure.experiment import (
    BooleanParameter,
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

# S2's pO2 sensor is read by two GPIB meters at these addresses, which stand
# until the station picks others, which are then remembered in its settings
PO2_NANOVOLTMETER_PORT = "GPIB0::7::INSTR"  # Keithley 2182A, sensor thermocouple
PO2_VOLTMETER_PORT = "GPIB0::8::INSTR"  # Keithley 182, sensor potential

# S2's four Aera FC-PA78x mass flow controllers share one serial port through a
# BSU485-AE1 RS-485 converter. Their digital addresses are 5 to 8; the GUI
# calls them MFC 1 to MFC 4, in that order, and this is the one place the two
# namings meet.
MFC_PORT = "ASRL6::INSTR"
MFC_ADDRESSES: Tuple[int, ...] = (5, 6, 7, 8)
MFC_NAMES: Tuple[str, ...] = tuple(
    "MFC {}".format(i) for i in range(1, len(MFC_ADDRESSES) + 1)
)
#: The four columns each MFC records, in the order the instrument returns them.
MFC_DATA_LABELS: Tuple[Tuple[str, ...], ...] = tuple(
    tuple("{} {}".format(name, suffix) for suffix in MFCBank.DATA_SUFFIXES)
    for name in MFC_NAMES
)

#: Where this station keeps what it has been set to, beside this module. Like
#: the calibrations, it belongs to the station rather than to the source, so it
#: is not kept in version control.
SETTINGS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "s2_settings.json"
)


def load_settings(path=SETTINGS_FILE):
    """Read this station's settings.

    Args:
        path: path to the settings file.

    Returns:
        What the file holds, or an empty dict if there is none or it cannot
        be read. Settings are a convenience, so a broken file is worth a
        warning rather than a failure to start.
    """
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as settings_file:
            settings = json.load(settings_file)
    except (OSError, ValueError) as e:
        log.warning("Could not read %s, using defaults: %s", path, e)
        return {}
    return settings if isinstance(settings, dict) else {}


def save_settings(settings, path=SETTINGS_FILE):
    """Write this station's settings.

    Args:
        settings: what to remember.
        path: path to the settings file.
    """
    try:
        with open(path, "w") as settings_file:
            json.dump(settings, settings_file, indent=2, sort_keys=True)
    except OSError as e:
        log.warning("Could not write %s: %s", path, e)


#: What this station was last set to, read once as the GUI is imported.
SETTINGS = load_settings()


def remembered_gas(name: str) -> str:
    """Get the gas an MFC was last set to flow.

    Args:
        name: name of the MFC, e.g. `MFC 1`.

    Returns:
        The remembered gas, or the first of
        :data:`~nupylab.instruments.mfc.mfc_calibration.DEFAULT_GASES` if the
        station has not chosen one for this MFC yet.
    """
    remembered = SETTINGS.get("mfc_gases")
    gas = remembered.get(name) if isinstance(remembered, dict) else None
    return str(gas) if gas else MFC_DEFAULT_GASES[0]


def remember_gas(name: str, gas: str) -> None:
    """Remember the gas an MFC is flowing, for the next time the GUI starts.

    Args:
        name: name of the MFC, e.g. `MFC 1`.
        gas: name of the gas.
    """
    remembered = SETTINGS.get("mfc_gases")
    if not isinstance(remembered, dict):
        remembered = {}
    if remembered.get(name) == gas:
        return
    remembered[name] = gas
    SETTINGS["mfc_gases"] = remembered
    save_settings(SETTINGS)


def remembered_calibration(name: str) -> str:
    """Get the calibration selection an MFC should open on.

    No date is named, so the selection means whichever calibration is newest
    for that MFC in the gas it was last set to flow.

    Args:
        name: name of the MFC, e.g. `MFC 1`.

    Returns:
        The selection, see
        :func:`~nupylab.instruments.mfc.mfc_calibration.encode_selection`.
    """
    return encode_mfc_selection(
        MFC_CALIBRATION_FILE, name, remembered_gas(name), None
    )


#: Bold headings for the inputs panel, each above the first input of its
#: group, and the input to draw on the heading's own row where one belongs
#: there. The groups follow the order `INPUTS` lists them in. A name ending in
#: `_row` is a row built by :func:`pair_inputs`, which runs first.
INPUT_SECTIONS = (
    ("furnace_type_row", "Furnace Controls", None),
    ("po2_nanovoltmeter_port_row", "pO2 Sensor", "po2_record"),
    ("mfc_port", "Mass Flow Controllers", None),
)

#: Pairs of inputs drawn as one row, under one label: the first input, the
#: second input, the label to give the row, whether that label is the group's
#: bold heading rather than an ordinary field label, and whether it stands
#: over the inputs rather than beside them.
#:
#: A device named by what it is and then chosen from two drop-downs reads
#: better with the name over them, and leaves the panel as narrow as the
#: drop-downs themselves. An MFC row is named by nothing more than `MFC 1`,
#: which costs no width beside its inputs and would waste a line above them.
INPUT_PAIRS = (
    ("furnace_type", "furnace_port", "Furnace Controller", False, True),
    ("potentiostat", "potentiostat_model", "Potentiostat Model", True, True),
    (
        "po2_nanovoltmeter_port",
        "po2_voltmeter_port",
        "Keithley Ports: 2182, 182",
        False,
        True,
    ),
) + tuple(
    ("mfc{}_gas".format(i), "mfc{}_calibration".format(i), name, False, False)
    for i, name in enumerate(MFC_NAMES, start=1)
)

#: Width in pixels of the Manual Control column holding the pO2 sensor and
#: the solenoid valve, which are both narrower than the Biologic panel beside
#: them. Lines too long for it wrap.
MANUAL_COLUMN_WIDTH = 260

#: Least width in pixels to leave the inputs panel. The widest thing in it is
#: an MFC row, a name beside a gas and a button. What each calibration is, and
#: what each device is called, go on lines of their own, and the gas box is
#: sized by a character count rather than by its longest entry, so none of
#: those is what sets this.
INPUTS_PANEL_WIDTH = 320


def move_input_to_file_block(window, name="record_time"):
    """Move one input down to sit with the filename and directory fields.

    An input that is about the recording rather than about an instrument
    belongs with them, not among the instrument settings. Only where it is
    drawn changes: PyMeasure still builds the widget and still reads the
    value from it, wherever it sits.

    Args:
        window: the main window.
        name: the parameter to move.
    """
    inputs = window.inputs
    widget = getattr(inputs, name, None)
    if widget is None:
        log.warning("No input named %s to move", name)
        return
    label = getattr(inputs, "labels", {}).get(name)
    field_layout = widget.parentWidget().layout()
    dock = inputs.parentWidget()
    dock_layout = dock.layout() if dock is not None else None
    if field_layout is None or dock_layout is None:
        log.warning("Could not find the panel layouts to move %s", name)
        return
    anchor = getattr(window, "file_input", None)
    index = dock_layout.indexOf(anchor) if anchor is not None else -1
    if index < 0:
        # No filename field: settle for just below the other inputs
        index = dock_layout.indexOf(inputs) + 1
    for moving in (label, widget):
        if moving is None:
            continue
        field_layout.removeWidget(moving)
        dock_layout.insertWidget(index, moving)
        index += 1


def pair_inputs(inputs, pairs=INPUT_PAIRS):
    """Draw pairs of inputs on one row, under one label.

    PyMeasure gives every input a label of its own on a row of its own, which
    for a setting that is really one choice in two parts, a controller and the
    port it is on, say, costs a row and says less than one label would. This
    puts both widgets on the first one's row and labels the row once.

    A pair may ask for its label to stand over the inputs rather than beside
    them, which reads better for a device named by what it is, and leaves the
    panel no wider than the two drop-downs.

    An input that offers a `caption_widget` has it drawn on a line of its own
    under the row. What a calibration is runs to half a line of text, and
    beside the inputs it would set how wide the whole panel has to be.

    The row is left on the panel as `<first>_row`, so that
    :func:`add_input_sections` can head the group it starts.

    Args:
        inputs: the window's `InputsWidget`.
        pairs: see :data:`INPUT_PAIRS`.
    """
    labels = getattr(inputs, "labels", {})
    for first, second, title, heading, stacked in pairs:
        first_widget = getattr(inputs, first, None)
        second_widget = getattr(inputs, second, None)
        if first_widget is None or second_widget is None:
            log.warning("No inputs named %s and %s to pair", first, second)
            continue
        layout = first_widget.parentWidget().layout()
        if layout is None:
            log.warning("Could not find the panel layout to pair %s", first)
            continue

        caption = getattr(second_widget, "caption_widget", None)

        row = QtWidgets.QWidget()
        row_layout = QtWidgets.QVBoxLayout(row)
        # Space above a heading, to part it from the group before it
        row_layout.setContentsMargins(0, 10 if heading else 0, 0, 0)
        row_layout.setSpacing(0)

        line = QtWidgets.QWidget()
        line_layout = QtWidgets.QHBoxLayout(line)
        line_layout.setContentsMargins(0, 0, 0, 0)
        line_layout.setSpacing(6)

        label = QtWidgets.QLabel(title)
        if heading:
            label.setStyleSheet("font-weight: bold;")

        # Where the pair starts, measured before anything is taken out. Every
        # item removed below is at or after it, so the row goes back in the
        # same place.
        index = layout.indexOf(labels.get(first) or first_widget)
        if index < 0:
            index = layout.count()
        # The label each input came with is replaced by the row's own. Taken
        # out by hand rather than left to reparenting, which would leave the
        # panel's layout holding an item for a widget that has moved.
        for spare in (labels.get(first), labels.get(second)):
            if spare is not None:
                layout.removeWidget(spare)
                spare.setParent(None)
                spare.deleteLater()
        labels.pop(first, None)
        labels.pop(second, None)
        for widget in (first_widget, second_widget):
            layout.removeWidget(widget)

        line_layout.addWidget(first_widget, 1)
        # An input whose caption is on the line below keeps its own width,
        # rather than being stretched to fill a row it no longer fills
        line_layout.addWidget(second_widget, 0 if caption is not None else 2)

        if stacked:
            row_layout.addWidget(label)
        else:
            line_layout.insertWidget(0, label)
        row_layout.addWidget(line)

        if caption is not None:
            caption_parent = caption.parentWidget()
            caption_layout = (
                caption_parent.layout() if caption_parent is not None else None
            )
            if caption_layout is not None:
                caption_layout.removeWidget(caption)
            row_layout.addWidget(caption)

        layout.insertWidget(index, row)
        setattr(inputs, "{}_row".format(first), row)


def add_input_sections(inputs, sections=INPUT_SECTIONS):
    """Head each group of inputs with a bold title.

    PyMeasure lays the inputs panel out as one run of label and widget pairs,
    which says nothing about which instrument each belongs to. This puts a
    heading above the first input of each group, in the panel's own layout.

    Args:
        inputs: the window's `InputsWidget`.
        sections: the parameter a group starts at, its heading, and the
            parameter to draw on the heading's row rather than below it, or
            `None` where the heading stands alone.
    """
    labels = getattr(inputs, "labels", {})
    for name, title, on_heading in sections:
        # A parameter drawn without a label, as a checkbox is, is found by the
        # input widget itself, as is a row built by `pair_inputs`
        widget = labels.get(name) or getattr(inputs, name, None)
        parent = widget.parentWidget() if widget is not None else None
        layout = parent.layout() if parent is not None else None
        if layout is None:
            log.warning("No input named %s to head with %r", name, title)
            continue
        header = QtWidgets.QLabel(title)
        header.setStyleSheet("font-weight: bold;")

        # Whatever shares the heading's row leaves the run of inputs first, so
        # that the heading goes in where the group really starts
        companion = getattr(inputs, on_heading, None) if on_heading else None
        if companion is not None:
            layout.removeWidget(companion)
        index = layout.indexOf(widget)
        if index < 0:
            continue
        # Space above every heading but the first, to part the groups
        top = 0 if index == 0 else 10

        if companion is None:
            header.setContentsMargins(0, top, 0, 0)
            layout.insertWidget(index, header)
            continue
        row = QtWidgets.QWidget()
        row_layout = QtWidgets.QHBoxLayout(row)
        row_layout.setContentsMargins(0, top, 0, 0)
        row_layout.addWidget(header)
        row_layout.addStretch()
        row_layout.addWidget(companion)
        layout.insertWidget(index, row)


def add_mfc_warning(inputs, after="mfc{}_gas_row".format(len(MFC_NAMES))):
    """Put one line under the MFC rows for calibrations worth redoing.

    A stale calibration is already marked beside the MFC it belongs to, which
    says which one but not what is wrong with it. This is where that is said,
    once, rather than on every row.

    Args:
        inputs: the window's `InputsWidget`.
        after: the row the line goes under, the last MFC's by default. Built
            by :func:`pair_inputs`, which must have run.

    Returns:
        The label, hidden until something is stale, or `None` if the panel
        has no such row.
    """
    anchor = getattr(inputs, after, None)
    parent = anchor.parentWidget() if anchor is not None else None
    layout = parent.layout() if parent is not None else None
    if layout is None:
        log.warning("No row named %s to put the MFC warning under", after)
        return None
    label = QtWidgets.QLabel()
    label.setStyleSheet("color: red;")
    label.setWordWrap(True)
    label.hide()
    index = layout.indexOf(anchor)
    layout.insertWidget(index + 1 if index >= 0 else layout.count(), label)
    return label


def widen_inputs_panel(window, width=INPUTS_PANEL_WIDTH):
    """Leave the inputs panel wide enough for the rows it now carries.

    PyMeasure sizes the panel from the widest input it happens to hold, which
    is narrower than the MFC rows read well at. The panel is still in a
    splitter, so the user can widen or narrow it from here.

    Args:
        window: the main window.
        width: least width in pixels.
    """
    dock = window.inputs.parentWidget()
    if dock is None:
        log.warning("Could not find the inputs dock to widen it")
        return
    dock.setMinimumWidth(width)
    splitter = dock.parentWidget()
    while splitter is not None and not isinstance(splitter, QtWidgets.QSplitter):
        splitter = splitter.parentWidget()
    if splitter is None:
        return
    sizes = splitter.sizes()
    index = splitter.indexOf(dock)
    if 0 <= index < len(sizes) and sizes[index] < width:
        sizes[index] = width
        splitter.setSizes(sizes)


def fit_inputs_panel(window, floor=200):
    """Give the inputs panel the height its inputs need.

    PyMeasure puts the inputs in a scroll area with no minimum height, so a
    station with many of them has to scroll to reach the last. This asks for
    the height they need, or for whatever the dock has left once the fields
    and buttons below the panel have taken theirs, whichever is less: a panel
    taller than that would push the queue and abort buttons out of reach.

    Measures the dock as it stands, so call it once the window is up and
    laid out, and again if the window is resized to something smaller.

    Args:
        window: the main window.
        floor: least the panel is left with, in pixels, however little room
            the dock has.
    """
    scroll = window.inputs.findChild(QtWidgets.QScrollArea)
    inner = scroll.widget() if scroll is not None else None
    if inner is None:
        log.warning("Could not find the inputs panel to fit it to its inputs")
        return
    needed = inner.sizeHint().height() + 2 * scroll.frameWidth()

    dock = window.inputs.parentWidget()
    layout = dock.layout() if dock is not None else None
    if layout is None:
        log.warning("Could not find the inputs dock to measure its room")
        scroll.setMinimumHeight(needed)
        return
    # Everything else the dock holds: the filename fields, the record time,
    # the queue and abort buttons, and the spacing between them
    taken = 0
    for position in range(layout.count()):
        item = layout.itemAt(position)
        widget = item.widget()
        if widget is window.inputs:
            continue
        taken += item.sizeHint().height()
    room = dock.height() - taken
    scroll.setMinimumHeight(max(floor, min(needed, room)))


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
        if resource and resource not in choices:
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


def make_mfc_bank(port=MFC_PORT, gases=None, calibrations=None):
    """Build the bank of Aera mass flow controllers.

    Args:
        port: string name of the port they share, e.g. `ASRL6::INSTR`.
        gases: gas each MFC is flowing, in channel order. Optional.
        calibrations: flow calibration of each MFC, `None` where it has none.
            Optional.

    Returns:
        The bank, not yet connected.
    """
    return MFCBank(
        port,
        MFC_ADDRESSES,
        AeraFCPA78x,
        MFC_DATA_LABELS,
        channel_names=MFC_NAMES,
        gases=gases if gases is not None else [remembered_gas(n) for n in MFC_NAMES],
        calibrations=calibrations,
        name="Aera MFCs",
    )


def mfc_calibration_from(selection) -> Optional[object]:
    """Read the calibration a selection names, without letting it fail a step.

    Args:
        selection: the selection held by an MFC calibration input.

    Returns:
        The calibration, or `None` if there is none to be had or the file
        cannot be read.
    """
    try:
        return load_mfc_selection(selection)
    except Exception as e:
        log.warning("Could not read the MFC calibrations: %s", e)
        return None


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
    # The pO2 meters sit at fixed GPIB addresses on S2. A port this station
    # was set to is offered as well, or it could not be the default below when
    # the instrument happens to be switched off.
    po2_resources = resource_choices(
        resources,
        PO2_NANOVOLTMETER_PORT,
        PO2_VOLTMETER_PORT,
        SETTINGS.get("nanovoltmeter_port", ""),
        SETTINGS.get("voltmeter_port", ""),
    )
    # The MFCs share one serial port, offered the same way
    mfc_resources = resource_choices(
        resources, MFC_PORT, SETTINGS.get("mfc_port", "")
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
        default=SETTINGS.get("nanovoltmeter_port", PO2_NANOVOLTMETER_PORT),
        ui_class=None,
    )
    po2_voltmeter_port: ListParameter = ListParameter(
        "Keithley 182 Port",
        choices=po2_resources,
        default=SETTINGS.get("voltmeter_port", PO2_VOLTMETER_PORT),
        ui_class=None,
    )
    # Which calibration to measure pO2 with, chosen in the inputs panel and
    # used by the Manual Control tab and by queued experiments alike. The value
    # names the calibration file and, once one has been picked out of it, the
    # calibration itself; on its own the file means whichever is newest.
    # Whether a queued experiment reads the pO2 sensor at all. Drawn on the
    # pO2 heading, see `INPUT_SECTIONS`. The Manual Control tab is unaffected:
    # it reads the sensor whenever its panel is connected.
    po2_record: BooleanParameter = BooleanParameter("Save Data", default=True)
    po2_calibration: Parameter = Parameter(
        "pO2 Calibration",
        default=encode_selection(CALIBRATION_FILE),
        ui_class=CalibrationInput,
    )

    # Mass flow controller parameters. The gas each MFC is flowing decides
    # which calibration is in use, so the two are kept side by side, one row
    # per MFC. Both are remembered between sessions, see `remembered_gas`.
    mfc_port: ListParameter = ListParameter(
        "MFC Port",
        choices=mfc_resources,
        default=SETTINGS.get("mfc_port", MFC_PORT),
        ui_class=None,
    )
    mfc1_gas: Parameter = Parameter(
        "MFC 1 Gas", default=remembered_gas("MFC 1"), ui_class=GasInput
    )
    mfc1_calibration: Parameter = Parameter(
        "MFC 1 Calibration",
        default=remembered_calibration("MFC 1"),
        ui_class=MFCCalibrationInput,
    )
    mfc2_gas: Parameter = Parameter(
        "MFC 2 Gas", default=remembered_gas("MFC 2"), ui_class=GasInput
    )
    mfc2_calibration: Parameter = Parameter(
        "MFC 2 Calibration",
        default=remembered_calibration("MFC 2"),
        ui_class=MFCCalibrationInput,
    )
    mfc3_gas: Parameter = Parameter(
        "MFC 3 Gas", default=remembered_gas("MFC 3"), ui_class=GasInput
    )
    mfc3_calibration: Parameter = Parameter(
        "MFC 3 Calibration",
        default=remembered_calibration("MFC 3"),
        ui_class=MFCCalibrationInput,
    )
    mfc4_gas: Parameter = Parameter(
        "MFC 4 Gas", default=remembered_gas("MFC 4"), ui_class=GasInput
    )
    mfc4_calibration: Parameter = Parameter(
        "MFC 4 Calibration",
        default=remembered_calibration("MFC 4"),
        ui_class=MFCCalibrationInput,
    )

    # One flow per MFC per step, in sccm. A step that asks for zero closes
    # that MFC rather than setting it to flow nothing. The unit is written
    # `cc/min`, the same number under a name Pint knows, see
    # :attr:`~nupylab.instruments.mfc.aera_mfc.AeraMFC.DATA_SUFFIXES`.
    mfc1_setpoint: FloatParameter = FloatParameter("MFC 1 Flow", units="cc/min", default=0)
    mfc2_setpoint: FloatParameter = FloatParameter("MFC 2 Flow", units="cc/min", default=0)
    mfc3_setpoint: FloatParameter = FloatParameter("MFC 3 Flow", units="cc/min", default=0)
    mfc4_setpoint: FloatParameter = FloatParameter("MFC 4 Flow", units="cc/min", default=0)

    eis_toggle: ListParameter = ListParameter(
        "Run eis", choices=["True", "False"], default="False", ui_class=None
    )
    initial_step: FloatParameter = FloatParameter("Initial Step", units="V", default=0)
    duration_step: FloatParameter = FloatParameter("Duration Step", units="s")
    maximum_frequency: FloatParameter = FloatParameter("Maximum Frequency", units="Hz")
    minimum_frequency: FloatParameter = FloatParameter("Minimum Frequency", units="Hz")
    amplitude_voltage: FloatParameter = FloatParameter("Amplitude Voltage", units="V")
    points_per_decade: IntegerParameter = IntegerParameter("Points Per Decade")

    # Units in parentheses must be valid pint units, except that a percentage
    # is written `(%)`, which neither the plot nor the results formatter reads
    # as units, so those columns are written and labelled as they stand
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
    ] + [label for group in MFC_DATA_LABELS for label in group]

    TABLE_PARAMETERS: Dict[str, str] = {
        "Target Temperature [C]": "target_temperature",
        "Ramp Rate [C/min]": "ramp_rate",
        "Dwell Time [min]": "dwell_time",
        "MFC 1 (sccm)": "mfc1_setpoint",
        "MFC 2 (sccm)": "mfc2_setpoint",
        "MFC 3 (sccm)": "mfc3_setpoint",
        "MFC 4 (sccm)": "mfc4_setpoint",
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
        # Three or more columns: one dock, one axis, a colour each, and no
        # column selectors above it
        tuple("{} Flow (%)".format(name) for name in MFC_NAMES),
    ]
    # A measured column drawn beside what was asked for, dashed and in the
    # same colour. The MFC dock plots percent rather than sccm, so that an
    # uncalibrated MFC still shows.
    Y_AXIS_SETPOINTS: Dict[str, str] = {
        "{} Flow (%)".format(name): "{} Setpoint (%)".format(name)
        for name in MFC_NAMES
    }
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
        "po2_calibration",
        "po2_record",
        "mfc_port",
        "mfc1_gas",
        "mfc1_calibration",
        "mfc2_gas",
        "mfc2_calibration",
        "mfc3_gas",
        "mfc3_calibration",
        "mfc4_gas",
        "mfc4_calibration",
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

    @property
    def mfc_gases(self) -> List[str]:
        """Get the gas each MFC is flowing, in channel order."""
        return [
            str(getattr(self, "mfc{}_gas".format(i)))
            for i in range(1, len(MFC_NAMES) + 1)
        ]

    @property
    def mfc_calibrations(self) -> List[Optional[object]]:
        """Get the flow calibration of each MFC, in channel order."""
        return [
            mfc_calibration_from(getattr(self, "mfc{}_calibration".format(i)))
            for i in range(1, len(MFC_NAMES) + 1)
        ]

    @property
    def mfc_setpoints(self) -> List[float]:
        """Get this step's wanted flow for each MFC in sccm, in channel order."""
        return [
            float(getattr(self, "mfc{}_setpoint".format(i)))
            for i in range(1, len(MFC_NAMES) + 1)
        ]

    def set_instruments(self) -> None:
        """Set and configure instruments list.

        Pass in connections from previous step, if applicable, otherwise create new
        instances. Send current step parameters to appropriate instruments.

        It is required for this method to create non-empty `instruments` and
        `active_instruments` attributes.
        """
        if self.previous_procedure is not None:
            furnace, potentiostat, po2_sensor, mfcs = (
                self.previous_procedure.instruments
            )
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
            mfcs = make_mfc_bank(self.mfc_port)
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
        self.instruments = (furnace, potentiostat, po2_sensor, mfcs)
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
        self._add_mfcs(mfcs)
        if self.po2_record:
            self._add_po2_sensor(po2_sensor)
        else:
            # Left out of the measurement entirely: its three columns stay
            # empty, so nothing is read, recorded or plotted for them
            log.info("Not recording pO2 this step")

    def _add_mfcs(self, mfcs) -> None:
        """Set the gases and flows for this step, and add the MFCs.

        A step that asks for no gas at all can run without the MFC bank, which
        is how a station that is not wired to one, or has it switched off,
        still runs. A step that asks for a flow cannot: a step that silently
        did not flow the gas it was given would be worse than one that stops.

        Args:
            mfcs: the bank to flow with.

        Raises:
            NupylabError: if a flow was asked for and the bank cannot deliver
                it, because it does not answer or has no calibration for the
                gas in use.
        """
        setpoints = self.mfc_setpoints
        for index, (gas, calibration) in enumerate(
            zip(self.mfc_gases, self.mfc_calibrations)
        ):
            mfcs.set_gas(index, gas)
            mfcs.set_calibration(index, calibration)
        mfcs.set_parameters(setpoints)
        wanted = any(setpoints)
        try:
            if not mfcs.connected:
                mfcs.connect()
        except Exception as e:
            if wanted:
                raise
            log.warning(
                "MFCs unavailable and no gas was asked for, continuing "
                "without them: %s", e
            )
            return
        self.active_instruments.append(mfcs)

    def _add_po2_sensor(self, po2_sensor) -> None:
        """Calibrate the pO2 sensor and add it to the measurement.

        Connecting here rather than leaving it to `startup` keeps a station
        whose Keithleys are off from failing every queued step.

        Args:
            po2_sensor: the sensor to measure with.
        """
        try:
            calibration = load_selection(self.po2_calibration)
        except Exception as e:
            calibration = None
            log.warning("Could not read the pO2 calibrations: %s", e)
        if calibration is None:
            # Ordinary on a station that has not calibrated its sensor yet
            log.warning("pO2 sensor is uncalibrated: pO2 is the ideal "
                        "Nernst response")
        else:
            po2_sensor.set_calibration(calibration)
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

    # The MFC port is likewise only opened when the panel connects or a
    # setpoint is applied, so a bank that is switched off costs nothing here.
    mfcs = make_mfc_bank(inputs.mfc_port.value())

    # The LabJack driver is only loaded when the valve panel connects, so a
    # station without one, or without ljackuw.dll, still opens the GUI
    solenoid = LabJackSolenoid()

    def gas_input(index):
        """Get the gas drop-down of one MFC, counting from zero."""
        return getattr(inputs, "mfc{}_gas".format(index + 1))

    def calibration_input(index):
        """Get the calibration entry of one MFC, counting from zero."""
        return getattr(inputs, "mfc{}_calibration".format(index + 1))

    # Built once the panel has been laid out, which happens after the inputs
    # are wired up, so it is reached through a holder rather than by name
    mfc_warning = {"label": None}

    def refresh_mfc_warning():
        """Say in one line which MFCs were calibrated too long ago."""
        label = mfc_warning["label"]
        if label is None:
            return
        stale = [
            MFC_NAMES[index]
            for index in range(len(MFC_NAMES))
            if calibration_input(index).stale
        ]
        if not stale:
            label.setText("")
            label.hide()
            return
        if len(stale) == 1:
            which = "{} was".format(stale[0])
        else:
            which = "{} and {} were".format(", ".join(stale[:-1]), stale[-1])
        label.setText(
            "Warning! {} calibrated more than a year ago.".format(which)
        )
        label.show()

    def clear_mfc_dock(running=None):
        """Leave only the step that is running on the MFC dock.

        Every step plots against time from zero, so the four colours of one
        step are drawn over by the four of the next, and which is which stops
        being readable. The other docks keep their whole history, where
        comparing one step against another is the point.

        Args:
            running: the experiment now running, whose traces stay. Passed by
                the manager's `running` signal.
        """
        queued = getattr(window.manager.experiments, "queue", None) or ()
        for experiment in list(queued):
            if experiment is running:
                continue
            for curve in getattr(experiment, "curve_list", None) or ():
                widget = getattr(curve, "wdg", None)
                if isinstance(widget, MultiColumnPlotWidget):
                    try:
                        widget.remove(curve)
                    except Exception as e:
                        log.debug("Could not clear an MFC trace: %s", e)

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
        instruments.append(mfcs)
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
            # The pO2 and valve panels are both short of what the Biologic
            # panel beside them needs, and holding their column to that width
            # spent room the MFC panel wants. Their long lines wrap instead.
            column_width=MANUAL_COLUMN_WIDTH,
        )
        current["control"] = control
        window.tabs.addTab(control, "Manual Control")
        # The rebuilt panel starts from whatever the inputs panel has chosen
        sync_po2_calibration()
        sync_mfc_selections()

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
            nanovoltmeter = inputs.po2_nanovoltmeter_port.value()
            voltmeter = inputs.po2_voltmeter_port.value()
            po2_sensor.set_connection(nanovoltmeter, voltmeter)
            mfc_port = inputs.mfc_port.value()
            mfcs.set_connection(mfc_port)
            # Remembered, so the station opens on its own ports next time
            if (
                SETTINGS.get("nanovoltmeter_port") != nanovoltmeter
                or SETTINGS.get("voltmeter_port") != voltmeter
                or SETTINGS.get("mfc_port") != mfc_port
            ):
                SETTINGS["nanovoltmeter_port"] = nanovoltmeter
                SETTINGS["voltmeter_port"] = voltmeter
                SETTINGS["mfc_port"] = mfc_port
                save_settings(SETTINGS)
        except Exception as e:
            _control_log.warning("Could not read connection settings: %s", e)

    def sync_po2_calibration(*_):
        """Give the Manual Control sensor the calibration the panel shows.

        Runs at startup and whenever the user takes a different calibration
        into use, so the tab and a queued experiment measure pO2 the same way.
        """
        try:
            calibration = load_selection(inputs.po2_calibration.value())
        except Exception as e:
            _control_log.error("Could not read the pO2 calibrations: %s", e)
            return
        if calibration is None or calibration == po2_sensor.calibration:
            return
        po2_sensor.set_calibration(calibration)
        _control_log.info(
            "pO2 calibration in use: a = %.6g V/K, b = %.6g V, %s",
            calibration.a,
            calibration.b,
            calibration.describe(),
        )

    def sync_mfc_selections(*_):
        """Give the Manual Control bank the gases and calibrations shown.

        Runs at startup and whenever a gas or a calibration is changed, so the
        tab and a queued experiment flow the same way.
        """
        for index in range(len(MFC_NAMES)):
            mfcs.set_gas(index, gas_input(index).value())
            mfcs.set_calibration(index, calibration_input(index).calibration)

    def on_gas_changed(index, gas):
        """Follow a gas change: remember it, and look its calibration up.

        A pair with no calibration on file is worth asking about there and
        then, since the user has just said what the MFC is flowing and the
        calibration is what turns that into sccm.

        Args:
            index: MFC index, counting from zero.
            gas: the gas now selected.
        """
        name = MFC_NAMES[index]
        remember_gas(name, gas)
        entry = calibration_input(index)
        entry.set_context(mfc=name, gas=gas)
        mfcs.set_gas(index, gas)
        _control_log.info("%s set to flow %s", name, gas)
        if entry.missing:
            _control_log.warning(
                "No flow calibration for %s in %s", name, gas
            )
            entry.select_calibration()

    def on_mfc_calibration_changed(index):
        """Take a newly chosen MFC calibration into use.

        Args:
            index: MFC index, counting from zero.
        """
        name = MFC_NAMES[index]
        calibration = calibration_input(index).calibration
        mfcs.set_calibration(index, calibration)
        refresh_mfc_warning()
        if calibration is None:
            _control_log.warning(
                "%s has no flow calibration for %s: it can only be set in "
                "percent", name, gas_input(index).value()
            )
            return
        _control_log.info(
            "%s calibration in use: m = %.6g sccm/%%, b = %.6g sccm, %s",
            name,
            calibration.m,
            calibration.b,
            calibration.describe(),
        )

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
        "po2_nanovoltmeter_port", "po2_voltmeter_port", "mfc_port",
    ):
        connect_input(name, sync_connection_settings)
    connect_input("furnace_type", on_furnace_type_changed)
    inputs.po2_calibration.calibration_changed.connect(sync_po2_calibration)
    sync_po2_calibration()

    for index, mfc_name in enumerate(MFC_NAMES):
        entry = calibration_input(index)
        # The add form offers every MFC the station has, not just this one
        entry.mfc_names = MFC_NAMES
        # The gas the GUI opens on may differ from the one the remembered
        # selection was written for, e.g. after the settings file was edited
        entry.set_context(mfc=mfc_name, gas=gas_input(index).value())
        gas_input(index).gas_changed.connect(
            lambda gas, i=index: on_gas_changed(i, gas)
        )
        entry.calibration_changed.connect(
            lambda i=index: on_mfc_calibration_changed(i)
        )
    sync_mfc_selections()

    window.manager.queued.connect(disconnect_control_instruments)
    window.manager.running.connect(clear_mfc_dock)
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

    move_input_to_file_block(window)
    pair_inputs(window.inputs)
    mfc_warning["label"] = add_mfc_warning(window.inputs)
    add_input_sections(window.inputs)
    widen_inputs_panel(window)
    refresh_mfc_warning()

    def maximize():
        """Fill the screen, once the window is up.

        Maximizing a window that has never been shown leaves it holding the
        maximized state without the geometry to go with it, so the screen is
        filled by hand as well: whichever of the two the window manager
        honours, the window ends up the size of the screen.
        """
        try:
            window.setGeometry(app.primaryScreen().availableGeometry())
        except Exception as e:
            log.warning("Could not measure the screen: %s", e)
        window.showMaximized()
        # The panel is measured against the dock it ends up with, which is
        # only settled once the maximized layout has been through the event
        # loop, so that waits another turn
        QtCore.QTimer.singleShot(0, lambda: fit_inputs_panel(window))

    window.show()
    QtCore.QTimer.singleShot(0, maximize)
    sys.exit(app.exec())


if __name__ == "__main__":
    main(sys.argv)
