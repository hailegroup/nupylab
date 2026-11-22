"""Dialogs for choosing a pO2 sensor calibration, or adding a new one.

The pO2 control panel opens :func:`choose_calibration`, which lists every
calibration in the station's calibration file, newest first, and lets the user
take one into use or record a new one. A calibration added here is appended to
that file, so it is there the next time the GUI starts.

Kept apart from the instrument module so that importing the instrument does not
pull in Qt.
"""

from __future__ import annotations

import logging
from datetime import date as date_type
from typing import List, Optional, Sequence

from pymeasure.display.Qt import QtCore, QtWidgets

import os
import shutil

from nupylab.instruments.o2_sensor.keithley2182_182 import (
    CALIBRATION_FILE,
    PO2Calibration,
    append_calibration,
    create_calibration_file,
    load_calibrations,
)

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

#: Offered in the gas list before anything has been calibrated in them. The
#: list also holds every gas already in the calibration file, and a user may
#: type one that is in neither.
DEFAULT_GASES = (
    "air",
    "O2",
    "N2",
    "Ar",
    "5% H2 in Ar",
    "CO/CO2",
)


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
    for value in list(existing) + list(defaults):
        value = value.strip()
        if value and value not in values:
            values.append(value)
    return values


class AddCalibrationDialog(QtWidgets.QDialog):
    """Form for recording one new calibration."""

    def __init__(self, calibrations: Sequence[PO2Calibration], parent=None):
        """Build the form.

        Args:
            calibrations: calibrations already recorded, whose calibrators and
                gases fill the drop-downs.
            parent: parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle("Add pO2 Calibration")
        self._calibration: Optional[PO2Calibration] = None
        self._setup_ui(calibrations)

    def _setup_ui(self, calibrations: Sequence[PO2Calibration]) -> None:
        layout = QtWidgets.QFormLayout()

        self.date_edit = QtWidgets.QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setDate(QtCore.QDate.currentDate())
        layout.addRow("Calibration date:", self.date_edit)

        # Editable, so a calibrator or a gas not used before can be typed in
        self.calibrator_box = QtWidgets.QComboBox()
        self.calibrator_box.setEditable(True)
        self.calibrator_box.addItems(
            _combo_values([c.calibrated_by for c in calibrations])
        )
        self.calibrator_box.setCurrentText("")
        layout.addRow("Calibrator:", self.calibrator_box)

        self.gas_box = QtWidgets.QComboBox()
        self.gas_box.setEditable(True)
        self.gas_box.addItems(
            _combo_values([c.gas for c in calibrations], DEFAULT_GASES)
        )
        self.gas_box.setCurrentText("")
        layout.addRow("Calibration gas:", self.gas_box)

        self.a_edit = QtWidgets.QLineEdit()
        self.a_edit.setPlaceholderText("slope of E against T, e.g. 2.5e-7")
        layout.addRow("a (V/K):", self.a_edit)

        self.b_edit = QtWidgets.QLineEdit()
        self.b_edit.setPlaceholderText("intercept of E against T, e.g. 1.0e-4")
        layout.addRow("b (V):", self.b_edit)

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

    @property
    def calibration(self) -> Optional[PO2Calibration]:
        """Get the calibration the form holds, once it has been accepted."""
        return self._calibration

    def _on_accept(self) -> None:
        """Check the form over, and accept it only once it reads."""
        calibrator = self.calibrator_box.currentText().strip()
        gas = self.gas_box.currentText().strip()
        if not calibrator:
            self.error_label.setText("Who took this calibration?")
            return
        if not gas:
            self.error_label.setText("What gas was it taken in?")
            return
        coefficients = []
        for name, edit in (("a", self.a_edit), ("b", self.b_edit)):
            try:
                coefficients.append(float(edit.text().strip()))
            except ValueError:
                self.error_label.setText(
                    f"{name} is not a number: write it as 2.5e-7, say."
                )
                return
        chosen = self.date_edit.date()
        self._calibration = PO2Calibration(
            a=coefficients[0],
            b=coefficients[1],
            gas=gas,
            calibrated_by=calibrator,
            # Read field by field, since the bindings differ over how a QDate
            # converts to a Python date
            date=date_type(chosen.year(), chosen.month(), chosen.day()),
        )
        self.accept()


class SelectCalibrationDialog(QtWidgets.QDialog):
    """List of the calibrations on file, to take one into use or add one."""

    #: Columns of the table, and how wide to leave them.
    COLUMNS = ("Date", "Calibrator", "Gas", "a (V/K)", "b (V)")

    def __init__(self, path: str = CALIBRATION_FILE, parent=None):
        """Build the list.

        Args:
            path: calibration file to list and to add to.
            parent: parent widget.
        """
        super().__init__(parent)
        self.setWindowTitle("pO2 Calibrations")
        self.resize(640, 360)
        self._path = path
        self._calibrations: List[PO2Calibration] = []
        self._chosen: Optional[PO2Calibration] = None
        self._setup_ui()
        self.refresh()

    def _setup_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout()

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
        self.use_btn = QtWidgets.QPushButton("Use Selected")
        self.use_btn.setDefault(True)
        self.use_btn.clicked.connect(self._on_use)
        self.cancel_btn = QtWidgets.QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.add_btn)
        button_layout.addStretch()
        button_layout.addWidget(self.use_btn)
        button_layout.addWidget(self.cancel_btn)
        layout.addLayout(button_layout)

        self.setLayout(layout)

    @property
    def chosen(self) -> Optional[PO2Calibration]:
        """Get the calibration the user took into use, if they did."""
        return self._chosen

    def refresh(self, select: Optional[PO2Calibration] = None) -> None:
        """Re-read the calibration file and fill the table, newest first.

        Args:
            select: calibration to leave selected, the newest by default.
        """
        try:
            self._calibrations = load_calibrations(self._path)
        except Exception as e:
            self._calibrations = []
            self.status_label.setText(str(e))
            self.status_label.setStyleSheet("color: red;")
        else:
            self.status_label.setStyleSheet("color: gray;")
            self.status_label.setText(
                "{} calibration(s) in {}".format(
                    len(self._calibrations), self._path
                )
                if self._calibrations
                else "No calibrations recorded yet in {}".format(self._path)
            )
        self.table.setRowCount(len(self._calibrations))
        for row, calibration in enumerate(self._calibrations):
            cells = (
                calibration.date_text,
                calibration.calibrated_by,
                calibration.gas,
                "{:g}".format(calibration.a),
                "{:g}".format(calibration.b),
            )
            for column, text in enumerate(cells):
                self.table.setItem(
                    row, column, QtWidgets.QTableWidgetItem(text)
                )
        self.table.resizeColumnsToContents()
        if self._calibrations:
            row = 0
            if select is not None and select in self._calibrations:
                row = self._calibrations.index(select)
            self.table.selectRow(row)
        self.use_btn.setEnabled(bool(self._calibrations))

    def _selected(self) -> Optional[PO2Calibration]:
        """Get the calibration the table has selected, if any."""
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        row = rows[0].row()
        if 0 <= row < len(self._calibrations):
            return self._calibrations[row]
        return None

    def _on_use(self) -> None:
        """Take the selected calibration into use and close."""
        calibration = self._selected()
        if calibration is None:
            self.status_label.setText("Select a calibration first.")
            return
        self._chosen = calibration
        self.accept()

    def _on_add(self) -> None:
        """Record a new calibration, then select it."""
        form = AddCalibrationDialog(self._calibrations, self)
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
            "pO2 calibration added to %s: %s, a = %g, b = %g",
            self._path,
            calibration.describe(),
            calibration.a,
            calibration.b,
        )
        self.refresh(select=calibration)


def ensure_calibration_file(
    path: str = CALIBRATION_FILE, parent=None
) -> Optional[str]:
    """Make sure the station has a calibration file, asking for one if not.

    Called when the GUI starts. A station that already has its calibrations
    is not asked anything. One that does not, because it has just been set up
    or because the file is not kept in version control, is offered the chance
    to point at calibrations taken elsewhere, which are then copied into
    place; closing that dialog starts an empty file instead.

    A file that is there but cannot be read is never written over, since it
    may hold calibrations that only need a correction by hand.

    Args:
        path: where the station's calibration file belongs.
        parent: parent widget.

    Returns:
        The path, or `None` if there is no usable file there and the user
        did not supply one, which leaves the sensor uncalibrated.
    """
    problem = ""
    if os.path.exists(path):
        try:
            load_calibrations(path)
            return path
        except Exception as e:
            problem = str(e)

    if problem:
        message = (
            "{}\n\nSelect a calibration file to use instead, or close this to "
            "carry on without one. The file above is left as it is.".format(
                problem
            )
        )
    else:
        message = (
            "This station has no pO2 calibration file yet.\n\nSelect "
            "calibrations taken elsewhere to copy into place, or close this "
            "to start an empty file at\n{}".format(path)
        )
    QtWidgets.QMessageBox.information(parent, "pO2 Calibrations", message)
    chosen, _ = QtWidgets.QFileDialog.getOpenFileName(
        parent,
        "Select pO2 Calibration File",
        os.path.dirname(path),
        "Calibration files (*.csv);;All files (*)",
    )

    if chosen:
        try:
            load_calibrations(chosen)
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                parent,
                "pO2 Calibrations",
                "That file cannot be read as calibrations:\n\n{}".format(e),
            )
            return None
        if os.path.abspath(chosen) != os.path.abspath(path):
            try:
                shutil.copyfile(chosen, path)
            except OSError as e:
                QtWidgets.QMessageBox.warning(
                    parent,
                    "pO2 Calibrations",
                    "Could not copy that file to {}:\n\n{}".format(path, e),
                )
                return chosen  # Usable where it is, just not copied
            log.info("Copied pO2 calibrations from %s to %s", chosen, path)
        return path

    if problem:
        return None  # Leave an unreadable file alone rather than replace it
    try:
        return create_calibration_file(path)
    except Exception as e:
        QtWidgets.QMessageBox.warning(
            parent,
            "pO2 Calibrations",
            "Could not start a calibration file:\n\n{}".format(e),
        )
        return None


def choose_calibration(
    path: str = CALIBRATION_FILE, parent=None
) -> Optional[PO2Calibration]:
    """Open the calibration list, and return whichever one the user takes.

    Args:
        path: calibration file to list and to add to.
        parent: parent widget.

    Returns:
        The calibration to use, or `None` if the user cancelled. A calibration
        the user adds is written to the file whether or not they then take it
        into use.
    """
    dialog = SelectCalibrationDialog(path, parent)
    if dialog.exec():
        return dialog.chosen
    return None
