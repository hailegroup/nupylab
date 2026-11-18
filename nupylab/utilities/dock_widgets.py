"""Dock widgets that give a plot a second, right-hand y axis.

PyMeasure's :class:`~pymeasure.display.widgets.dock_widget.DockWidget` builds one
dock per entry in a procedure's ``Y_AXIS``, and each dock plots a single y column.
The classes here extend that so one dock can show two columns on independent
y scales, e.g. voltage on the left axis and current on the right.

A procedure requests this by making the ``Y_AXIS`` entry a two-item tuple of
``(left, right)`` column names::

    X_AXIS = ["Time (s)"]
    Y_AXIS = ["Furnace Temperature (degC)", ("Ewe (V)", "I (A)")]

The right-axis curve of an experiment is drawn with a dashed line in the same
color as its left-axis curve, so the two stay visually paired in the browser.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional, Sequence, Tuple, Union

import pyqtgraph as pg
from pyqtgraph.dockarea import Dock
from pymeasure.display.curves import ResultsCurve
from pymeasure.display.Qt import QtCore, QtWidgets
from pymeasure.display.widgets.dock_widget import DockWidget
from pymeasure.display.widgets.plot_frame import PlotFrame
from pymeasure.display.widgets.plot_widget import PlotWidget
from pymeasure.experiment import Procedure

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

#: Attribute tagged onto each curve to record which y axis it belongs to.
RIGHT_AXIS_ATTR = "nupylab_right_axis"

YAxisEntry = Union[str, Sequence[str]]


def split_y_axis(
    y_axis: Sequence[YAxisEntry],
) -> Tuple[List[str], List[Optional[str]]]:
    """Split procedure `Y_AXIS` entries into left- and right-axis labels.

    Args:
        y_axis: entries are either a single column name, plotted on the left axis,
            or a two-item sequence of ``(left, right)`` column names.

    Returns:
        Two equal-length lists: the left-axis label of every dock, and the
        right-axis label of every dock, `None` where a dock has no right axis.

    Raises:
        ValueError: if an entry is a sequence of more than two column names.
    """
    left_labels: List[str] = []
    right_labels: List[Optional[str]] = []
    for entry in y_axis:
        if isinstance(entry, str):
            left_labels.append(entry)
            right_labels.append(None)
            continue
        labels = list(entry)
        if len(labels) == 1:
            left_labels.append(labels[0])
            right_labels.append(None)
        elif len(labels) == 2:
            left_labels.append(labels[0])
            right_labels.append(labels[1])
        else:
            raise ValueError(
                "`Y_AXIS` entries may name at most two columns, one per y axis, "
                f"but got {entry!r}."
            )
    return left_labels, right_labels


class DualAxisPlotWidget(PlotWidget):
    """Plot widget with an independent right-hand y axis.

    The left axis behaves exactly as in
    :class:`~pymeasure.display.widgets.plot_widget.PlotWidget`. The right axis gets
    its own view box, column selector and curves, sharing the x axis of the left.

    Args:
        name: name of the widget.
        columns: data columns selectable on either axis.
        x_axis: column initially plotted on the x axis.
        y_axis: column initially plotted on the left y axis.
        y_axis_right: column initially plotted on the right y axis.
        refresh_time: seconds between plot refreshes.
        check_status: refresh curves only while their procedure is running.
        linewidth: width of plotted curves.
        parent: parent widget.
    """

    def __init__(
        self,
        name: str,
        columns: Sequence[str],
        x_axis: Optional[str] = None,
        y_axis: Optional[str] = None,
        y_axis_right: Optional[str] = None,
        refresh_time: float = 0.2,
        check_status: bool = True,
        linewidth: int = 1,
        parent=None,
    ) -> None:
        """Initialize the plot widget and attach its right-hand axis."""
        # Read by `_setup_ui`, which the base class calls from its initializer.
        self.y_axis_right = y_axis_right
        self._right_curves: List[ResultsCurve] = []
        super().__init__(
            name, columns, x_axis, y_axis, refresh_time, check_status, linewidth, parent
        )
        self._setup_right_axis()

    def _setup_ui(self) -> None:
        super()._setup_ui()
        self.columns_y_right_label = QtWidgets.QLabel(self)
        self.columns_y_right_label.setMaximumSize(QtCore.QSize(60, 16777215))
        self.columns_y_right_label.setText("Y Right:")

        self.columns_y_right = QtWidgets.QComboBox(self)
        for column in self.columns:
            self.columns_y_right.addItem(column)
        self.columns_y_right.activated.connect(self.update_y_right_column)
        index = self.columns_y_right.findText(self.y_axis_right)
        self.columns_y_right.setCurrentIndex(max(index, 0))
        self.y_axis_right = self.columns_y_right.currentText()

    def _layout(self) -> None:
        vbox = QtWidgets.QVBoxLayout(self)
        vbox.setSpacing(0)

        hbox = QtWidgets.QHBoxLayout()
        hbox.setSpacing(10)
        hbox.setContentsMargins(-1, 6, -1, 6)
        hbox.addWidget(self.columns_x_label)
        hbox.addWidget(self.columns_x)
        hbox.addWidget(self.columns_y_label)
        hbox.addWidget(self.columns_y)
        hbox.addWidget(self.columns_y_right_label)
        hbox.addWidget(self.columns_y_right)

        vbox.addLayout(hbox)
        vbox.addWidget(self.plot_frame)
        self.setLayout(vbox)

    def _setup_right_axis(self) -> None:
        """Overlay a second view box on the plot and drive it off the left one."""
        plot_item = self.plot_frame.plot
        self.right_viewbox = pg.ViewBox()
        plot_item.showAxis("right")
        plot_item.scene().addItem(self.right_viewbox)
        plot_item.getAxis("right").linkToView(self.right_viewbox)
        self.right_viewbox.setXLink(plot_item)
        # The overlay is only a canvas for the right-axis curves; panning and
        # zooming stay with the left view box, which this one follows.
        self.right_viewbox.setMouseEnabled(x=False, y=False)
        self.right_viewbox.setZValue(plot_item.vb.zValue() - 1)
        self.right_viewbox.setMenuEnabled(False)

        plot_item.vb.sigResized.connect(self._resize_right_viewbox)
        self._resize_right_viewbox()

        # Right-axis curves live outside `plot_item.items`, so PlotFrame's own
        # refresh and axis-change handling does not reach them.
        self.plot_frame.updated.connect(self._update_right_curves)
        self.plot_frame.x_axis_changed.connect(self._change_right_x_axis)
        self._label_right_axis(self.y_axis_right)

    def _resize_right_viewbox(self) -> None:
        view = self.plot_frame.plot.vb
        self.right_viewbox.setGeometry(view.sceneBoundingRect())
        self.right_viewbox.linkedViewChanged(view, self.right_viewbox.XAxis)

    def _label_right_axis(self, axis: str) -> None:
        label, units = self.plot_frame.parse_axis(axis)
        self.plot_frame.plot.setLabel(
            "right", label, units=units, **PlotFrame.LABEL_STYLE
        )

    def _update_right_curves(self) -> None:
        for curve in self._right_curves:
            if self.check_status:
                if curve.results.procedure.status == Procedure.RUNNING:
                    curve.update_data()
            else:
                curve.update_data()

    def _change_right_x_axis(self, axis: str) -> None:
        for curve in self._right_curves:
            curve.x = axis
            curve.update_data()

    def update_y_right_column(self, index: int) -> None:
        """Plot the column at `index` of the right-axis selector."""
        self.change_y_right_axis(self.columns_y_right.itemText(index))

    def change_y_right_axis(self, axis: str) -> None:
        """Replot every right-axis curve against data column `axis`."""
        for curve in self._right_curves:
            curve.y = axis
            curve.update_data()
        self._label_right_axis(axis)
        self.y_axis_right = axis

    def new_curve(self, results, color=pg.intColor(0), **kwargs) -> List[ResultsCurve]:
        """Return the left- and right-axis curves for one set of results."""
        left_curve = super().new_curve(results, color=color, **kwargs)
        setattr(left_curve, RIGHT_AXIS_ATTR, False)

        right_kwargs = dict(kwargs)
        right_kwargs.setdefault("antialias", False)
        right_kwargs["pen"] = pg.mkPen(
            color=color, width=self.linewidth, style=QtCore.Qt.PenStyle.DashLine
        )
        right_curve = ResultsCurve(
            results,
            wdg=self,
            x=self.plot_frame.x_axis,
            y=self.y_axis_right,
            **right_kwargs,
        )
        right_curve.setSymbol(None)
        right_curve.setSymbolBrush(None)
        setattr(right_curve, RIGHT_AXIS_ATTR, True)
        return [left_curve, right_curve]

    def load(self, curve) -> None:
        """Add `curve` to the axis it was created for."""
        if not getattr(curve, RIGHT_AXIS_ATTR, False):
            super().load(curve)
            return
        curve.x = self.columns_x.currentText()
        curve.y = self.columns_y_right.currentText()
        curve.update_data()
        if curve in self._right_curves:
            # Already shown; `PlotItem.addItem` ignores repeat adds in the same way
            return
        self.right_viewbox.addItem(curve)
        self._right_curves.append(curve)

    def remove(self, curve) -> None:
        """Remove `curve` from the axis it was added to."""
        if not getattr(curve, RIGHT_AXIS_ATTR, False):
            super().remove(curve)
            return
        self.right_viewbox.removeItem(curve)
        if curve in self._right_curves:
            self._right_curves.remove(curve)

    def clear_widget(self) -> None:
        """Remove the curves of both axes."""
        super().clear_widget()
        for curve in list(self._right_curves):
            self.right_viewbox.removeItem(curve)
        self._right_curves.clear()

    def preview_widget(self, parent=None) -> "DualAxisPlotWidget":
        """Return a widget suitable for preview during loading."""
        return DualAxisPlotWidget(
            "Plot preview",
            self.columns,
            self.plot_frame.x_axis,
            self.plot_frame.y_axis,
            self.y_axis_right,
            parent=parent,
        )


class NupylabDockWidget(DockWidget):
    """Dock widget whose docks may carry a second y axis.

    Args:
        name: name of the tab.
        procedure_class: procedure class describing the experiment.
        x_axis_labels: x-axis column of each dock. A list shorter than the number
            of docks has its last entry reused.
        y_axis_labels: left y-axis column of each dock.
        y_axis_right_labels: right y-axis column of each dock, `None` entries
            giving a dock with a single y axis. Defaults to no right axes.
        linewidth: width of plotted curves.
        layout_path: directory in which the dock layout file is saved.
        layout_filename: name of the dock layout file.
        parent: parent widget.
    """

    def __init__(
        self,
        name: str,
        procedure_class,
        x_axis_labels: Optional[Sequence[str]] = None,
        y_axis_labels: Optional[Sequence[str]] = None,
        y_axis_right_labels: Optional[Sequence[Optional[str]]] = None,
        linewidth: int = 1,
        layout_path: str = "./",
        layout_filename: str = "",
        parent=None,
    ) -> None:
        """Initialize the dock area, adding a right axis where one is requested."""
        # Read by `_setup_ui`, which the base class calls from its initializer.
        self.y_axis_right_labels: List[Optional[str]] = list(y_axis_right_labels or ())
        super().__init__(
            name,
            procedure_class,
            x_axis_labels,
            y_axis_labels,
            linewidth=linewidth,
            layout_path=layout_path,
            layout_filename=layout_filename,
            parent=parent,
        )

    def _right_label(self, index: int) -> Optional[str]:
        if index < len(self.y_axis_right_labels):
            return self.y_axis_right_labels[index]
        return None

    def _setup_ui(self) -> None:
        for i in range(self.num_plots):
            # Repeat the last entry of a list shorter than the number of docks.
            x_label = self.x_axis_labels[min(i, len(self.x_axis_labels) - 1)]
            y_label = self.y_axis_labels[min(i, len(self.y_axis_labels) - 1)]
            y_right_label = self._right_label(i)

            dock = Dock("Dock " + str(i + 1), closable=False, size=(200, 50))
            self.dock_area.addDock(dock)
            if y_right_label is None:
                plot_widget: PlotWidget = PlotWidget(
                    "Results Graph",
                    self.procedure_class.DATA_COLUMNS,
                    x_label,
                    y_label,
                    linewidth=self.linewidth,
                )
            else:
                plot_widget = DualAxisPlotWidget(
                    "Results Graph",
                    self.procedure_class.DATA_COLUMNS,
                    x_label,
                    y_label,
                    y_right_label,
                    linewidth=self.linewidth,
                )
            self.plot_frames.append(plot_widget)
            plot_widget.plot_frame.plot_widget.scene().contextMenu.append(
                self.save_dock_action()
            )
            dock.addWidget(plot_widget)
            self.docks.append(dock)

    def new_curve(self, results, color=pg.intColor(0), **kwargs) -> List[Any]:
        """Return a flat list of the curves of every dock."""
        if "pen" not in kwargs:
            kwargs["pen"] = pg.mkPen(color=color, width=self.linewidth)
        if "antialias" not in kwargs:
            kwargs["antialias"] = False
        curves: List[Any] = []
        for plot_widget in self.plot_frames:
            curve = plot_widget.new_curve(results, color=color, **kwargs)
            # Dual-axis plots return one curve per axis.
            if isinstance(curve, (list, tuple)):
                curves.extend(curve)
            else:
                curves.append(curve)
        return curves

    def clear(self) -> None:
        """Remove the curves of every dock."""
        for plot_widget in self.plot_frames:
            plot_widget.plot.clear()
            if isinstance(plot_widget, DualAxisPlotWidget):
                for curve in list(plot_widget._right_curves):
                    plot_widget.right_viewbox.removeItem(curve)
                plot_widget._right_curves.clear()
