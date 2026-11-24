"""Choosing an MFC's gas and flow calibration, from the GUI's inputs panel.

Two inputs panel entries live here, one per MFC each:

:class:`GasInput`
    A drop-down of the gases on offer, with `Add New Gas...` at the end of the
    list to name one that is not there yet. A gas added this way is kept, so
    it is offered from then on.

:class:`MFCCalibrationInput`
    Shows the coefficients and date of the calibration in use for that MFC in
    that gas, says in red when there is none, and opens
    :func:`choose_calibration` to change it. It follows the gas: selecting a
    different gas looks up the newest calibration recorded for the new pair,
    and says so when there is not one.

Kept apart from the instrument module so that importing the instrument does
not pull in Qt.
"""

from __future__ import annotations

import logging
import os
from datetime import date as date_type
from typing import List, Optional, Sequence

from pymeasure.display.inputs import Input
from pymeasure.display.Qt import QtCore, QtWidgets

from nupylab.instruments.mfc.mfc_calibration import (
    CALIBRATION_FILE,
    MFCCalibration,
    add_gas,
    append_calibration,
    calibrations_for,
    decode_selection,
    encode_selection,
    gas_choices,
    load_calibrations,
    load_selection,
    match_key,
)

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

#: Last entry of a gas drop-down, which asks for a gas that is not on it yet.
ADD_GAS_TEXT: str = "Add New Gas..."

#: How many characters wider than its longest entry to leave a gas
#: drop-down, so that the name in it is not up against the arrow.
GAS_BOX_PADDING: int = 4


def _combo_values(
    existing: Sequence[str], defaults: Sequence[str] = ()
) -> List[str]:
    """Build a drop-down list from what has been used before.

    Args:
        existing: values already recorded, newest first.
        defaults: values to offer even when nothing has used them.

    Returns:
        The values, in the order they were recorded, each once, with any
        unused defaults after them.
    """
    values: List[str] = []
    seen = set()
    for value in list(existing) + list(defaults):
        value = str(value or "").strip()
        key = match_key(value)
        if value and key not in seen:
            seen.add(key)
            values.append(value)
    return values


class AddMFCCalibrationDialog(QtWidgets.QDialog):
    """Form for recording one new MFC calibration."""

    def __init__(
        self,
        calibrations: Sequence[MFCCalibration],
        mfc: str = "",
        gas: str = "",
        parent=None,
    ):
        """Build the form.

        Args:
            calibrations: calibrations already recorded, whose calibrators
                and gases fill the drop-downs.
            mfc: MFC the form records against. Shown rather than chosen, see
                :meth:`_setup_ui`.
            gas: gas the form opens on, for both gas fields, since a
                calibration is usually taken in the gas it is used for.
            parent: parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle(
            "Add Calibration for {}".format(mfc) if mfc else "Add MFC Calibration"
        )
        self._calibration: Optional[MFCCalibration] = None
        self._setup_ui(calibrations, mfc, gas)

    def _setup_ui(
        self,
        calibrations: Sequence[MFCCalibration],
        mfc: str,
        gas: str,
    ) -> None:
        layout = QtWidgets.QFormLayout()

        self.date_edit = QtWidgets.QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setDate(QtCore.QDate.currentDate())
        layout.addRow("Calibration date:", self.date_edit)

        # Editable, so a calibrator, MFC or gas not used before can be typed in
        self.calibrator_box = QtWidgets.QComboBox()
        self.calibrator_box.setEditable(True)
        self.calibrator_box.addItems(
            _combo_values([c.calibrated_by for c in calibrations])
        )
        self.calibrator_box.setCurrentText("")
        layout.addRow("Calibrator:", self.calibrator_box)

        # Fixed, not chosen. This form is opened from one MFC's entry in the
        # inputs panel, and a calibration filed against another by a slip of
        # the hand would then be used for the wrong instrument.
        self.mfc = mfc
        mfc_label = QtWidgets.QLabel(mfc or "not known")
        mfc_label.setStyleSheet("font-weight: bold;")
        mfc_label.setToolTip(
            "The MFC this calibration was taken on. It is whichever one this "
            "was opened from, since a calibration belongs to its instrument."
        )
        layout.addRow("MFC:", mfc_label)

        known_gases = _combo_values(
            [c.gas for c in calibrations] + [c.calibration_gas for c in calibrations],
            gas_choices(),
        )

        self.calibration_gas_box = QtWidgets.QComboBox()
        self.calibration_gas_box.setEditable(True)
        self.calibration_gas_box.addItems(known_gases)
        self.calibration_gas_box.setCurrentText(gas)
        self.calibration_gas_box.setToolTip(
            "The gas that actually flowed through the MFC while it was "
            "calibrated."
        )
        layout.addRow("Calibration gas:", self.calibration_gas_box)

        self.gas_box = QtWidgets.QComboBox()
        self.gas_box.setEditable(True)
        self.gas_box.addItems(known_gases)
        self.gas_box.setCurrentText(gas)
        self.gas_box.setToolTip(
            "The gas this calibration is to be used for. Leave it the same as "
            "the calibration gas unless you are reusing a calibration taken in "
            "a similar gas."
        )
        layout.addRow("Used for gas:", self.gas_box)

        self.m_edit = QtWidgets.QLineEdit()
        self.m_edit.setPlaceholderText("slope of flow against setpoint, e.g. 5.0")
        layout.addRow("m (sccm/%):", self.m_edit)

        self.b_edit = QtWidgets.QLineEdit()
        self.b_edit.setText("0")
        self.b_edit.setPlaceholderText("flow at a setpoint of 0 %, e.g. 0.0")
        layout.addRow("b (sccm):", self.b_edit)

        self.note_label = QtWidgets.QLabel()
        self.note_label.setStyleSheet("color: #aa6600;")
        self.note_label.setWordWrap(True)
        layout.addRow(self.note_label)
        for box in (self.calibration_gas_box, self.gas_box):
            box.currentTextChanged.connect(self._show_substitution)
        self._show_substitution()

        self.comment_edit = QtWidgets.QLineEdit()
        self.comment_edit.setPlaceholderText(
            "anything the numbers do not say, e.g. checked against the "
            "bubble meter at 20 psi"
        )
        layout.addRow("Comment:", self.comment_edit)

        self.error_label = QtWidgets.QLabel()
        self.error_label.setStyleSheet("color: red;")
        self.error_label.setWordWrap(True)
        layout.addRow(self.error_label)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

        self.setLayout(layout)

    def _show_substitution(self, *_) -> None:
        """Say so where a calibration is being reused for a different gas."""
        taken = self.calibration_gas_box.currentText().strip()
        used = self.gas_box.currentText().strip()
        if taken and used and match_key(taken) != match_key(used):
            self.note_label.setText(
                "This records a {} calibration used for {}. That is allowed, "
                "and both gases are kept so the substitution can be seen "
                "later.".format(taken, used)
            )
        else:
            self.note_label.setText("")

    @property
    def calibration(self) -> Optional[MFCCalibration]:
        """Get the calibration the form holds, once it has been accepted."""
        return self._calibration

    def _on_accept(self) -> None:
        """Check the form over, and accept it only once it reads."""
        calibrator = self.calibrator_box.currentText().strip()
        mfc = self.mfc.strip()
        calibration_gas = self.calibration_gas_box.currentText().strip()
        gas = self.gas_box.currentText().strip()
        if not calibrator:
            self.error_label.setText("Who took this calibration?")
            return
        if not mfc:
            self.error_label.setText("Which MFC was calibrated?")
            return
        if not calibration_gas:
            self.error_label.setText("What gas was it taken in?")
            return
        if not gas:
            self.error_label.setText("What gas is it to be used for?")
            return
        coefficients = []
        for name, edit in (("m", self.m_edit), ("b", self.b_edit)):
            try:
                coefficients.append(float(edit.text().strip()))
            except ValueError:
                self.error_label.setText(
                    "{} is not a number: write it as 5.0, say.".format(name)
                )
                return
        if coefficients[0] == 0:
            self.error_label.setText(
                "m cannot be zero: no setpoint would give a flow."
            )
            return
        chosen = self.date_edit.date()
        self._calibration = MFCCalibration(
            m=coefficients[0],
            b=coefficients[1],
            mfc=mfc,
            calibration_gas=calibration_gas,
            gas=gas,
            calibrated_by=calibrator,
            # Read field by field, since the bindings differ over how a QDate
            # converts to a Python date
            date=date_type(chosen.year(), chosen.month(), chosen.day()),
            comment=self.comment_edit.text().strip(),
        )
        # A gas named here is worth offering from now on
        for name in (calibration_gas, gas):
            add_gas(name)
        self.accept()


class SelectMFCCalibrationDialog(QtWidgets.QDialog):
    """List of the calibrations on file, to take one into use or add one."""

    #: Columns of the table.
    COLUMNS = (
        "Date",
        "Calibrator",
        "MFC",
        "Calibration Gas",
        "Used For Gas",
        "m (sccm/%)",
        "b (sccm)",
        "Comment",
    )

    def __init__(
        self,
        path: str = CALIBRATION_FILE,
        mfc: str = "",
        gas: str = "",
        parent=None,
    ):
        """Build the list.

        Args:
            path: calibration file to list and to add to.
            mfc: MFC whose calibrations to list. Only that MFC's are listed,
                whatever else the file holds.
            gas: gas whose calibrations to show first.
            parent: parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle(
            "{} Calibrations".format(mfc) if mfc else "MFC Calibrations"
        )
        self.resize(820, 400)
        self._path = path
        self._mfc = mfc
        self._gas = gas
        self._all: List[MFCCalibration] = []
        self._shown: List[MFCCalibration] = []
        self._chosen: Optional[MFCCalibration] = None
        self._setup_ui()
        self.refresh()

    def _setup_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout()

        self.filter_box = QtWidgets.QCheckBox(
            "Only calibrations for {}".format(self._gas or "this gas")
        )
        self.filter_box.setChecked(bool(self._gas))
        self.filter_box.setEnabled(bool(self._gas))
        self.filter_box.setToolTip(
            "Unticking this shows what else {} has been calibrated for, which "
            "is worth seeing when a gas has none of its own and a similar one "
            "would do. Calibrations taken on the other MFCs are never listed: "
            "one belongs to the instrument it was taken on.".format(
                self._mfc or "this MFC"
            )
        )
        self.filter_box.toggled.connect(lambda *_: self.refresh())
        layout.addWidget(self.filter_box)

        self.table = QtWidgets.QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.doubleClicked.connect(self._on_use)
        layout.addWidget(self.table)

        self.status_label = QtWidgets.QLabel()
        self.status_label.setStyleSheet("color: gray;")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        button_layout = QtWidgets.QHBoxLayout()
        self.add_btn = QtWidgets.QPushButton("Add New Calibration...")
        self.add_btn.clicked.connect(self._on_add)
        self.open_btn = QtWidgets.QPushButton("Open Another File...")
        self.open_btn.clicked.connect(self._on_open)
        self.use_btn = QtWidgets.QPushButton("Use Selected")
        self.use_btn.setDefault(True)
        self.use_btn.clicked.connect(self._on_use)
        self.cancel_btn = QtWidgets.QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.add_btn)
        button_layout.addWidget(self.open_btn)
        button_layout.addStretch()
        button_layout.addWidget(self.use_btn)
        button_layout.addWidget(self.cancel_btn)
        layout.addLayout(button_layout)

        self.setLayout(layout)

    @property
    def chosen(self) -> Optional[MFCCalibration]:
        """Get the calibration the user took into use, if they did."""
        return self._chosen

    @property
    def path(self) -> str:
        """Get the calibration file the dialog ended on."""
        return self._path

    def _on_open(self) -> None:
        """Point the dialog at a different calibration file."""
        chosen, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select MFC Calibration File",
            os.path.dirname(self._path) or os.getcwd(),
            "Calibration files (*.csv);;All files (*)",
        )
        if not chosen:
            return
        self._path = chosen
        self.refresh()

    def refresh(self, select: Optional[MFCCalibration] = None) -> None:
        """Re-read the calibration file and fill the table, newest first.

        Args:
            select: calibration to leave selected, the newest by default.
        """
        try:
            self._all = load_calibrations(self._path)
        except Exception as e:
            self._all = []
            self.status_label.setText(str(e))
            self.status_label.setStyleSheet("color: red;")
        else:
            self.status_label.setStyleSheet("color: gray;")

        # Only ever this MFC's. A calibration describes the instrument it
        # was taken on, so another MFC's is not a worse answer but no answer,
        # and it should not be on offer at all.
        whose = self._mfc or "any MFC"
        mine = (
            [c for c in self._all if match_key(c.mfc) == match_key(self._mfc)]
            if self._mfc
            else list(self._all)
        )
        if self.filter_box.isChecked():
            self._shown = calibrations_for(mine, self._mfc, self._gas)
            summary = "{} calibration(s) for {} in {}, of {} for it in all".format(
                len(self._shown), whose, self._gas, len(mine)
            )
        else:
            self._shown = mine
            summary = "{} calibration(s) for {}".format(len(mine), whose)
        if mine:
            self.status_label.setText("{}, in {}".format(summary, self._path))
        elif self.status_label.styleSheet() != "color: red;":
            self.status_label.setText(
                "Nothing recorded for {} in {}, of {} calibration(s) "
                "there".format(whose, self._path, len(self._all))
            )

        self.table.setRowCount(len(self._shown))
        for row, calibration in enumerate(self._shown):
            cells = (
                calibration.date_text,
                calibration.calibrated_by,
                calibration.mfc,
                calibration.calibration_gas,
                calibration.gas,
                "{:g}".format(calibration.m),
                "{:g}".format(calibration.b),
                calibration.comment,
            )
            for column, text in enumerate(cells):
                item = QtWidgets.QTableWidgetItem(text)
                if calibration.substituted:
                    item.setToolTip(
                        "Taken in {}, used for {}".format(
                            calibration.calibration_gas, calibration.gas
                        )
                    )
                self.table.setItem(row, column, item)
        self.table.resizeColumnsToContents()
        if self._shown:
            row = 0
            if select is not None and select in self._shown:
                row = self._shown.index(select)
            self.table.selectRow(row)
        self.use_btn.setEnabled(bool(self._shown))

    def _selected(self) -> Optional[MFCCalibration]:
        """Get the calibration the table has selected, if any."""
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        row = rows[0].row()
        if 0 <= row < len(self._shown):
            return self._shown[row]
        return None

    def _record_reuse(self, source: MFCCalibration) -> Optional[MFCCalibration]:
        """Write a row using `source`'s coefficients for the gas in use.

        Reusing one gas's calibration for another is allowed and sometimes
        right, which is why the file keeps the gas a calibration was taken in
        apart from the gas it is used for. Recording it, rather than only
        pointing at the other gas's row, is what makes those two columns
        worth having: the new row says what was measured, what it is being
        used for, and that the two differ.

        The date is the day the measurement was taken, not today, so that the
        reused row is as old as the numbers in it and goes stale with them.

        Args:
            source: the calibration whose coefficients to reuse.

        Returns:
            The row to take into use, which is an existing one if this
            substitution has been recorded before, or `None` if it could not
            be written.
        """
        note = "Reused from the {} calibration of {}".format(
            source.calibration_gas or "unknown gas", source.short_date
        )
        reused = source._replace(
            gas=self._gas,
            comment="{}; {}".format(source.comment, note)
            if source.comment
            else note,
        )
        for existing in self._all:
            if (
                existing.is_for(self._mfc, self._gas)
                and existing.date == reused.date
                and existing.m == reused.m
                and existing.b == reused.b
                and match_key(existing.calibration_gas)
                == match_key(reused.calibration_gas)
            ):
                return existing  # Recorded already, no need for it twice
        try:
            append_calibration(reused, self._path)
        except Exception as e:
            self.status_label.setText(str(e))
            self.status_label.setStyleSheet("color: red;")
            return None
        log.info(
            "Recorded %s as a %s calibration for %s used for %s: %s",
            self._path, reused.calibration_gas, reused.mfc, reused.gas, note,
        )
        return reused

    def _on_use(self) -> None:
        """Take the selected calibration into use and close.

        One taken in a different gas from the one in use is recorded against
        the gas in use first, see :meth:`_record_reuse`.
        """
        calibration = self._selected()
        if calibration is None:
            self.status_label.setText("Select a calibration first.")
            return
        if self._gas and match_key(calibration.gas) != match_key(self._gas):
            calibration = self._record_reuse(calibration)
            if calibration is None:
                return  # Could not be written, and the error says so
        self._chosen = calibration
        self.accept()

    def _on_add(self) -> None:
        """Record a new calibration, then select it."""
        form = AddMFCCalibrationDialog(self._all, self._mfc, self._gas, self)
        if not form.exec():
            return
        calibration = form.calibration
        if calibration is None:
            return
        try:
            append_calibration(calibration, self._path)
        except Exception as e:
            self.status_label.setText(str(e))
            self.status_label.setStyleSheet("color: red;")
            return
        log.info(
            "MFC calibration added to %s: %s, m = %g sccm/%%, b = %g sccm",
            self._path,
            calibration.describe(),
            calibration.m,
            calibration.b,
        )
        # A calibration added for another gas would otherwise vanish behind
        # the filter the moment it is recorded
        if self.filter_box.isChecked() and not calibration.is_for(
            self._mfc, self._gas
        ):
            self.filter_box.setChecked(False)  # refreshes on its own
            self.refresh(select=calibration)
        else:
            self.refresh(select=calibration)


def choose_calibration(
    path: str = CALIBRATION_FILE,
    mfc: str = "",
    gas: str = "",
    parent=None,
) -> Optional[str]:
    """Open the calibration list, and return whichever one the user takes.

    Args:
        path: calibration file to list and to add to.
        mfc: MFC whose calibrations to list, and to record a new one against.
        gas: gas whose calibrations to show first.
        parent: parent widget.

    Returns:
        The selection, as :func:`encode_selection` writes it, naming the file,
        the MFC and gas it is for, and the calibration taken from it, or
        `None` if the user cancelled. A calibration the user adds is written
        to the file whether or not they then take it into use.
    """
    dialog = SelectMFCCalibrationDialog(path, mfc, gas, parent)
    if dialog.exec() and dialog.chosen is not None:
        chosen = dialog.chosen
        # The row is recorded against its own MFC and gas, which is what it
        # has to be looked up by next time
        return encode_selection(dialog.path, chosen.mfc, chosen.gas, chosen.date)
    return None


class GasInput(Input, QtWidgets.QWidget):
    """Inputs panel entry for choosing which gas an MFC is flowing.

    A drop-down of the gases on offer, with :data:`ADD_GAS_TEXT` at the end to
    name one that is not there yet. A gas added this way is kept in the gas
    file, so it is offered from then on, on this station.
    """

    #: Emitted with the gas when a different one is selected.
    gas_changed = QtCore.Signal(str)

    def __init__(self, parameter, parent=None, **kwargs):
        """Build the entry.

        Args:
            parameter: the parameter this entry sets, whose value is the name
                of a gas. PyMeasure passes this and nothing else.
            parent: parent widget.
        """
        self._value = ""
        self._built = False
        super().__init__(parameter=parameter, parent=parent, **kwargs)
        self._setup_ui()
        self._built = True
        self._refresh_choices()
        self._select(self._value)

    def _setup_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.combo = QtWidgets.QComboBox()
        # Sized by `_refresh_choices` from what is in it, rather than by
        # whatever the style would otherwise ask for
        policy = getattr(
            QtWidgets.QComboBox, "AdjustToMinimumContentsLengthWithIcon", None
        )
        if policy is not None:
            self.combo.setSizeAdjustPolicy(policy)
        # Never wider than it needs. Expanding here, in a row that has space
        # to give, came to about twice the width of the longest gas.
        self.combo.setSizePolicy(
            QtWidgets.QSizePolicy.Maximum,
            self.combo.sizePolicy().verticalPolicy(),
        )
        self.combo.activated.connect(self._on_activated)
        layout.addWidget(self.combo)
        self.setLayout(layout)

    def _refresh_choices(self) -> None:
        """Fill the drop-down from the gases on offer, keeping the selection."""
        current = self._value
        self.combo.blockSignals(True)
        self.combo.clear()
        choices = gas_choices()
        if current and match_key(current) not in {match_key(c) for c in choices}:
            choices.append(current)
        self.combo.addItems(choices)
        self.combo.insertSeparator(self.combo.count())
        self.combo.addItem(ADD_GAS_TEXT)
        self.combo.blockSignals(False)
        longest = max(
            (len(self.combo.itemText(i)) for i in range(self.combo.count())),
            default=0,
        )
        self.combo.setMinimumContentsLength(longest + GAS_BOX_PADDING)

    def _select(self, gas: str) -> None:
        """Show a gas in the drop-down without emitting anything."""
        index = self.combo.findText(gas)
        self.combo.blockSignals(True)
        if index >= 0:
            self.combo.setCurrentIndex(index)
        elif self.combo.count():
            self.combo.setCurrentIndex(0)
        self.combo.blockSignals(False)
        # The box is narrower than some of the names, so the one in use is
        # said in full here
        self.combo.setToolTip(
            "{} flows through this MFC. The calibration below follows "
            "it.".format(self.combo.currentText() or "No gas")
        )

    def _on_activated(self, index: int) -> None:
        """Take up the gas the user picked, or ask for a new one."""
        text = self.combo.itemText(index)
        if text == ADD_GAS_TEXT:
            self._add_gas()
            return
        if not text or match_key(text) == match_key(self._value):
            return
        self._value = text
        self.gas_changed.emit(self._value)

    def _add_gas(self) -> None:
        """Ask for a gas that is not on the list, and take it up."""
        name, accepted = QtWidgets.QInputDialog.getText(
            self, "Add New Gas", "Name of the gas, e.g. 5% H2 in Ar:"
        )
        name = str(name or "").strip()
        if not accepted or not name:
            self._select(self._value)  # Put the drop-down back where it was
            return
        add_gas(name)
        self._value = name
        self._refresh_choices()
        self._select(name)
        self.gas_changed.emit(name)

    # The interface PyMeasure's Input mixin expects #

    def setValue(self, value) -> None:
        """Set the gas this entry holds."""
        self._value = "" if value is None else str(value)
        if self._built:
            self._refresh_choices()
            self._select(self._value)

    def value(self) -> str:
        """Get the gas this entry holds."""
        return self._value

    def setSuffix(self, value) -> None:
        """Ignore units, which a gas name does not have."""


class MFCCalibrationInput(Input, QtWidgets.QWidget):
    """Inputs panel entry for choosing one MFC's flow calibration.

    Its value is the selection that
    :func:`~nupylab.instruments.mfc.mfc_calibration.encode_selection` writes,
    so the procedure a queued experiment runs reads the same calibration the
    panel shows. The selection carries the MFC and the gas it was looked up
    by, which is how a calibration follows the gas rather than the other way
    round.

    Says in red, rather than refusing anything, when the MFC and gas in use
    have no calibration on file: the GUI then asks for one rather than
    guessing a flow.
    """

    #: Emitted when the calibration in use changes, for any reason.
    calibration_changed = QtCore.Signal()

    def __init__(self, parameter, parent=None, **kwargs):
        """Build the entry.

        Args:
            parameter: the parameter this entry sets, whose value is a
                selection. PyMeasure passes this and nothing else.
            parent: parent widget.
        """
        self._value = ""
        self._built = False
        super().__init__(parameter=parameter, parent=parent, **kwargs)
        self._setup_ui()
        self._built = True
        self._refresh()

    def _setup_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        line = QtWidgets.QWidget()
        line_layout = QtWidgets.QHBoxLayout(line)
        line_layout.setContentsMargins(0, 0, 0, 0)
        line_layout.setSpacing(4)

        self.select_btn = QtWidgets.QPushButton("Calibration...")
        self.select_btn.setToolTip(
            "Choose, add, or point at another file of flow calibrations for "
            "this MFC and gas."
        )
        self.select_btn.clicked.connect(self.select_calibration)
        line_layout.addWidget(self.select_btn)

        # Marks the one MFC, since the warning itself is a single line for the
        # whole panel and does not sit beside any of them
        self.stale_mark = QtWidgets.QLabel("!")
        self.stale_mark.setStyleSheet("color: #cc0000; font-weight: bold;")
        self.stale_mark.hide()
        line_layout.addWidget(self.stale_mark)
        line_layout.addStretch(1)
        layout.addWidget(line)

        self.summary_label = QtWidgets.QLabel()
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.setLayout(layout)

    @property
    def caption_widget(self):
        """Get the label saying which calibration is in use.

        Offered so that whoever lays the panel out can put it on a line of
        its own under the whole row, rather than beside the button where it
        sets how wide the panel has to be. Left in this entry's own layout
        until something takes it.
        """
        return self.summary_label

    # What it is for #

    @property
    def mfc(self) -> str:
        """Get the MFC the calibration in use is for."""
        return decode_selection(self._value)[1]

    @property
    def gas(self) -> str:
        """Get the gas the calibration in use is for."""
        return decode_selection(self._value)[2]

    @property
    def path(self) -> str:
        """Get the calibration file in use."""
        return decode_selection(self._value)[0] or CALIBRATION_FILE

    def set_context(self, mfc: Optional[str] = None, gas: Optional[str] = None):
        """Look the calibration up for a different MFC or gas.

        Takes the newest calibration recorded for the new pair, since a
        calibration chosen for one gas says nothing about another.

        Args:
            mfc: MFC to look up, or None to leave it as it is.
            gas: gas to look up, or None to leave it as it is.
        """
        path, current_mfc, current_gas, _ = decode_selection(self._value)
        new_mfc = current_mfc if mfc is None else str(mfc)
        new_gas = current_gas if gas is None else str(gas)
        if new_mfc == current_mfc and new_gas == current_gas:
            return
        # No date: whichever is newest for the new pair
        self.setValue(
            encode_selection(path or CALIBRATION_FILE, new_mfc, new_gas, None)
        )
        self.calibration_changed.emit()

    # The interface PyMeasure's Input mixin expects #

    def setValue(self, value) -> None:
        """Set the selection this entry holds."""
        self._value = "" if value is None else str(value)
        if self._built:
            self._refresh()

    def value(self) -> str:
        """Get the selection this entry holds."""
        return self._value

    def setSuffix(self, value) -> None:
        """Ignore units, which a selection does not have."""

    # Showing what is in use #

    @property
    def calibration(self) -> Optional[MFCCalibration]:
        """Get the calibration in use, or `None` if there is none to be had."""
        try:
            return load_selection(self._value)
        except Exception as e:
            log.warning("Could not read the MFC calibrations: %s", e)
            return None

    @property
    def missing(self) -> bool:
        """Get whether the MFC and gas in use have no calibration on file."""
        return self.calibration is None

    @property
    def stale(self) -> bool:
        """Get whether the calibration in use is old enough to redo.

        See :data:`~nupylab.instruments.mfc.mfc_calibration.CALIBRATION_WARNING_DAYS`.
        An MFC with no calibration at all is not stale: it has a louder
        problem than that.
        """
        calibration = self.calibration
        return calibration is not None and calibration.older_than()

    def _refresh(self) -> None:
        """Show the calibration in use, or say why there is none."""
        path, mfc, gas, _ = decode_selection(self._value)
        try:
            calibration = load_selection(self._value)
            problem = ""
        except Exception as e:
            calibration = None
            problem = str(e)

        self.stale_mark.hide()
        if calibration is None:
            self.summary_label.setStyleSheet("color: #cc0000;")
            self.summary_label.setText(
                problem
                if problem
                else "No calibration for {} in {}".format(
                    mfc or "this MFC", gas or "this gas"
                )
            )
            self.setToolTip(
                "This MFC cannot be set in sccm until it has a calibration "
                "for the gas it is flowing. Looked in {}".format(
                    path or "nowhere"
                )
            )
            return

        # What was taken and when, which is what tells one calibration from
        # another. The coefficients are in the tooltip, where they do not set
        # how wide the panel has to be.
        self.summary_label.setStyleSheet(
            "color: #aa6600;" if calibration.substituted else ""
        )
        self.summary_label.setText(
            "Calibrated {} in {}".format(
                calibration.short_date,
                calibration.calibration_gas or "an unknown gas",
            )
        )
        if calibration.older_than():
            self.stale_mark.show()
            self.stale_mark.setToolTip(
                "{} was last calibrated {}, more than a year ago.".format(
                    mfc or "This MFC", calibration.date_text
                )
            )
        self.setToolTip("{}\nfrom {}".format(calibration.describe(), path))

    def select_calibration(self) -> None:
        """Open the calibrations, and take up whichever the user chooses."""
        path, mfc, gas, _ = decode_selection(self._value)
        selection = choose_calibration(path or CALIBRATION_FILE, mfc, gas, self)
        if selection is None:
            self._refresh()  # A calibration may have been added without use
            return
        self.setValue(selection)
        self.calibration_changed.emit()
