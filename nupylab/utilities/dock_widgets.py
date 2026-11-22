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

A column may also be paired with the column that was *asked* for rather than
measured, its setpoint. A procedure says so with a ``Y_AXIS_SETPOINTS``
mapping of measured column to setpoint column::

    Y_AXIS_SETPOINTS = {"MFC 1 Flow (%)": "MFC 1 Setpoint (%)"}

Each such column is then drawn twice in its dock: the measurement and its
setpoint, in the same color. The setpoint curve is only made for the column a
dock opens on, so switching a dock to a different column shows the measurement
alone.

Every curve of one experiment is drawn in that experiment's color, so a dock
carrying both axes and both setpoints has four lines of one color on it, told
apart by their line style:

==================  ==============
Curve               Style
==================  ==============
Left measurement    solid
Left setpoint       dashed
Right measurement   dash-dotted
Right setpoint      dotted
==================  ==============

A dock with no setpoints keeps the two styles it has always had, solid on the
left and dashed on the right.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

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

#: Attribute tagged onto a setpoint curve, holding the axis it belongs to,
#: `left` or `right`. Absent on a curve that plots a measurement.
SETPOINT_ATTR = "nupylab_setpoint_axis"

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


class SetpointPlotWidget(PlotWidget):
    """Plot widget that draws a column's setpoint beside it, dashed.

    Behaves exactly as
    :class:`~pymeasure.display.widgets.plot_widget.PlotWidget` for a column
    that `setpoint_labels` does not name, which is every column of a procedure
    that asks for no setpoints at all.

    Args:
        name: name of the widget.
        columns: data columns selectable on the axis.
        x_axis: column initially plotted on the x axis.
        y_axis: column initially plotted on the y axis.
        refresh_time: seconds between plot refreshes.
        check_status: refresh curves only while their procedure is running.
        linewidth: width of plotted curves.
        parent: parent widget.
        setpoint_labels: measured column to the column holding its setpoint.
    """

    def __init__(
        self,
        name: str,
        columns: Sequence[str],
        x_axis: Optional[str] = None,
        y_axis: Optional[str] = None,
        refresh_time: float = 0.2,
        check_status: bool = True,
        linewidth: int = 1,
        parent=None,
        setpoint_labels: Optional[Dict[str, str]] = None,
    ) -> None:
        """Initialize the plot widget and note which columns have setpoints."""
        # Read by `new_curve`, and set before the base class initializer in
        # case anything it calls reaches for it.
        self.setpoint_labels: Dict[str, str] = dict(setpoint_labels or {})
        self._setpoint_curves: List[ResultsCurve] = []
        super().__init__(
            name, columns, x_axis, y_axis, refresh_time, check_status, linewidth, parent
        )

    # Building and keeping setpoint curves #

    def _make_setpoint_curve(
        self, results, color, column: Optional[str], side: str, **kwargs
    ) -> Optional[ResultsCurve]:
        """Build the dashed curve of a column's setpoint, if it has one.

        Args:
            results: results of the experiment being plotted.
            color: color of the measured curve this one pairs with.
            column: measured column the setpoint belongs to.
            side: `left` or `right`, the axis the curve is drawn against.
            **kwargs: as passed to :meth:`new_curve`, less the pen.

        Returns:
            The curve, or `None` if that column has no setpoint column.
        """
        setpoint = self.setpoint_labels.get(column) if column else None
        if not setpoint:
            return None
        curve_kwargs = dict(kwargs)
        curve_kwargs["antialias"] = False
        # Four lines of one color share a dock that carries both axes and
        # both setpoints, so each takes a style of its own
        curve_kwargs["pen"] = pg.mkPen(
            color=color,
            width=self.linewidth,
            style=(
                QtCore.Qt.PenStyle.DotLine
                if side == "right"
                else QtCore.Qt.PenStyle.DashLine
            ),
        )
        curve = ResultsCurve(
            results,
            wdg=self,
            x=self.plot_frame.x_axis,
            y=setpoint,
            **curve_kwargs,
        )
        curve.setSymbol(None)
        curve.setSymbolBrush(None)
        setattr(curve, SETPOINT_ATTR, side)
        if side == "right":
            setattr(curve, RIGHT_AXIS_ATTR, True)
        return curve

    def _point_setpoint_curves(self, curves, column: Optional[str]) -> None:
        """Point setpoint curves at the setpoint of the column now plotted.

        A dock the user has switched to a column with no setpoint has nothing
        to draw, so the curve is pointed back at the plotted column and
        hidden. Leaving it on a column the results do not hold would make
        every refresh fail.

        Args:
            curves: the setpoint curves of one axis.
            column: measured column now plotted on that axis.
        """
        setpoint = self.setpoint_labels.get(column) if column else None
        for curve in curves:
            curve.y = setpoint or column
            curve.setVisible(bool(setpoint))
            try:
                curve.update_data()
            except Exception as e:  # A column the results do not hold yet
                log.debug("Could not refresh a setpoint curve: %s", e)

    def update_y_column(self, index: int) -> None:
        """Plot the column at `index`, and follow it with its setpoint."""
        super().update_y_column(index)
        self._point_setpoint_curves(self._setpoint_curves, self.plot_frame.y_axis)

    # Curves #

    def new_curve(self, results, color=pg.intColor(0), **kwargs) -> List[ResultsCurve]:
        """Return the curve for one set of results, and its setpoint curve."""
        curves: List[ResultsCurve] = [
            PlotWidget.new_curve(self, results, color=color, **kwargs)
        ]
        setpoint_curve = self._make_setpoint_curve(
            results, color, self.plot_frame.y_axis, "left", **kwargs
        )
        if setpoint_curve is not None:
            curves.append(setpoint_curve)
        return curves

    def load(self, curve) -> None:
        """Add `curve` to the plot, as a measurement or as a setpoint."""
        if getattr(curve, SETPOINT_ATTR, None) != "left":
            super().load(curve)
            return
        curve.x = self.columns_x.currentText()
        if curve not in self._setpoint_curves:
            self._setpoint_curves.append(curve)
            self.plot_frame.plot.addItem(curve)
        self._point_setpoint_curves([curve], self.plot_frame.y_axis)

    def remove(self, curve) -> None:
        """Remove `curve` from the plot."""
        if getattr(curve, SETPOINT_ATTR, None) != "left":
            super().remove(curve)
            return
        self.plot_frame.plot.removeItem(curve)
        if curve in self._setpoint_curves:
            self._setpoint_curves.remove(curve)

    def clear_widget(self) -> None:
        """Remove every curve, setpoints included."""
        super().clear_widget()
        self._setpoint_curves.clear()

    def preview_widget(self, parent=None) -> "SetpointPlotWidget":
        """Return a widget suitable for preview during loading."""
        return SetpointPlotWidget(
            "Plot preview",
            self.columns,
            self.plot_frame.x_axis,
            self.plot_frame.y_axis,
            parent=parent,
            setpoint_labels=self.setpoint_labels,
        )


class DualAxisPlotWidget(SetpointPlotWidget):
    """Plot widget with an independent right-hand y axis.

    The left axis behaves exactly as in
    :class:`~pymeasure.display.widgets.plot_widget.PlotWidget`. The right axis gets
    its own view box, column selector and curves, sharing the x axis of the left.

    Either axis may also carry the dashed setpoint curve of the column it
    plots, see :class:`SetpointPlotWidget`.

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
        setpoint_labels: measured column to the column holding its setpoint.
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
        setpoint_labels: Optional[Dict[str, str]] = None,
    ) -> None:
        """Initialize the plot widget and attach its right-hand axis."""
        # Read by `_setup_ui`, which the base class calls from its initializer.
        self.y_axis_right = y_axis_right
        self._right_curves: List[ResultsCurve] = []
        self._right_setpoint_curves: List[ResultsCurve] = []
        super().__init__(
            name,
            columns,
            x_axis,
            y_axis,
            refresh_time,
            check_status,
            linewidth,
            parent,
            setpoint_labels=setpoint_labels,
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
        for curve in self._right_curves + self._right_setpoint_curves:
            if self.check_status:
                if curve.results.procedure.status == Procedure.RUNNING:
                    curve.update_data()
            else:
                curve.update_data()

    def _change_right_x_axis(self, axis: str) -> None:
        for curve in self._right_curves + self._right_setpoint_curves:
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
        self._point_setpoint_curves(self._right_setpoint_curves, axis)

    def _draws_setpoints(self) -> bool:
        """Get whether either axis of this dock carries a setpoint curve.

        `setpoint_labels` is handed to every dock, so it says what the
        procedure asked for rather than what this dock draws.
        """
        return bool(
            self.setpoint_labels.get(self.plot_frame.y_axis)
            or self.setpoint_labels.get(self.y_axis_right)
        )

    def new_curve(self, results, color=pg.intColor(0), **kwargs) -> List[ResultsCurve]:
        """Return the curves of both axes, with their setpoints."""
        curves: List[ResultsCurve] = list(
            SetpointPlotWidget.new_curve(self, results, color=color, **kwargs)
        )

        right_kwargs = dict(kwargs)
        right_kwargs.setdefault("antialias", False)
        # Dashed pairs the right axis with the left, except where this dock
        # also draws a dashed setpoint, which would then look the same
        right_kwargs["pen"] = pg.mkPen(
            color=color,
            width=self.linewidth,
            style=(
                QtCore.Qt.PenStyle.DashDotLine
                if self._draws_setpoints()
                else QtCore.Qt.PenStyle.DashLine
            ),
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
        curves.append(right_curve)

        right_setpoint = self._make_setpoint_curve(
            results, color, self.y_axis_right, "right", **kwargs
        )
        if right_setpoint is not None:
            curves.append(right_setpoint)
        return curves

    def load(self, curve) -> None:
        """Add `curve` to the axis it was created for."""
        if not getattr(curve, RIGHT_AXIS_ATTR, False):
            super().load(curve)
            return
        curve.x = self.columns_x.currentText()
        if getattr(curve, SETPOINT_ATTR, None) == "right":
            if curve not in self._right_setpoint_curves:
                self.right_viewbox.addItem(curve)
                self._right_setpoint_curves.append(curve)
            self._point_setpoint_curves([curve], self.y_axis_right)
            return
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
        for curves in (self._right_curves, self._right_setpoint_curves):
            if curve in curves:
                curves.remove(curve)

    def clear_widget(self) -> None:
        """Remove the curves of both axes."""
        super().clear_widget()
        for curves in (self._right_curves, self._right_setpoint_curves):
            for curve in list(curves):
                self.right_viewbox.removeItem(curve)
            curves.clear()

    def preview_widget(self, parent=None) -> "DualAxisPlotWidget":
        """Return a widget suitable for preview during loading."""
        return DualAxisPlotWidget(
            "Plot preview",
            self.columns,
            self.plot_frame.x_axis,
            self.plot_frame.y_axis,
            self.y_axis_right,
            parent=parent,
            setpoint_labels=self.setpoint_labels,
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
        setpoint_labels: measured column to the column holding its setpoint. A
            dock opening on such a column also draws the setpoint, dashed.
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
        setpoint_labels: Optional[Dict[str, str]] = None,
        linewidth: int = 1,
        layout_path: str = "./",
        layout_filename: str = "",
        parent=None,
    ) -> None:
        """Initialize the dock area, adding a right axis where one is requested."""
        # Read by `_setup_ui`, which the base class calls from its initializer.
        self.y_axis_right_labels: List[Optional[str]] = list(y_axis_right_labels or ())
        self.setpoint_labels: Dict[str, str] = dict(setpoint_labels or {})
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
            wants_setpoints = bool(
                self.setpoint_labels.get(y_label)
                or (y_right_label and self.setpoint_labels.get(y_right_label))
            )

            dock = Dock("Dock " + str(i + 1), closable=False, size=(200, 50))
            self.dock_area.addDock(dock)
            if y_right_label is not None:
                plot_widget: PlotWidget = DualAxisPlotWidget(
                    "Results Graph",
                    self.procedure_class.DATA_COLUMNS,
                    x_label,
                    y_label,
                    y_right_label,
                    linewidth=self.linewidth,
                    setpoint_labels=self.setpoint_labels,
                )
            elif wants_setpoints:
                plot_widget = SetpointPlotWidget(
                    "Results Graph",
                    self.procedure_class.DATA_COLUMNS,
                    x_label,
                    y_label,
                    linewidth=self.linewidth,
                    setpoint_labels=self.setpoint_labels,
                )
            else:
                # A dock that needs neither a right axis nor a setpoint is
                # left exactly as PyMeasure builds it
                plot_widget = PlotWidget(
                    "Results Graph",
                    self.procedure_class.DATA_COLUMNS,
                    x_label,
                    y_label,
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
            # Dual-axis and setpoint plots return one curve per drawn line.
            if isinstance(curve, (list, tuple)):
                curves.extend(curve)
            else:
                curves.append(curve)
        return curves

    def clear(self) -> None:
        """Remove the curves of every dock."""
        for plot_widget in self.plot_frames:
            plot_widget.plot.clear()
            if isinstance(plot_widget, SetpointPlotWidget):
                plot_widget._setpoint_curves.clear()
            if isinstance(plot_widget, DualAxisPlotWidget):
                for curves in (
                    plot_widget._right_curves,
                    plot_widget._right_setpoint_curves,
                ):
                    for curve in list(curves):
                        plot_widget.right_viewbox.removeItem(curve)
                    curves.clear()
