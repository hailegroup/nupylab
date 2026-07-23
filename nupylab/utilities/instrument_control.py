"""Generic individual instrument control widget for NUPyLab GUIs."""

from __future__ import annotations

import logging
from typing import List

from pymeasure.display.Qt import QtWidgets, QtCore

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


class InstrumentControlWidget(QtWidgets.QWidget):
    """Generic tab widget that assembles control panels from instruments.
    
    Each instrument passed in should have a `control_widget()` method
    that returns a QWidget control panel for that instrument.
    """

    def __init__(self, instruments: List = None, parent=None):
        super().__init__(parent)
        self._panels = []
        self._setup_ui(instruments or [])

    def _setup_ui(self, instruments):
        layout = QtWidgets.QVBoxLayout()

        self.warning_label = QtWidgets.QLabel(
            "⚠  Controls are disabled while an experiment is running."
        )
        self.warning_label.setStyleSheet("color: orange; font-weight: bold;")
        self.warning_label.hide()
        layout.addWidget(self.warning_label)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        container = QtWidgets.QWidget()
        panels_layout = QtWidgets.QHBoxLayout(container)
        panels_layout.setAlignment(QtCore.Qt.AlignLeft)

        for instrument in instruments:
            if hasattr(instrument, 'control_widget'):
                panel = instrument.control_widget()
                panels_layout.addWidget(panel)
                self._panels.append(panel)

        scroll.setWidget(container)
        layout.addWidget(scroll)
        self.setLayout(layout)

    def set_enabled_for_experiment(self, running: bool):
        """Disable all panels when experiment is running."""
        self.warning_label.setVisible(running)
        for panel in self._panels:
            panel.setEnabled(not running)