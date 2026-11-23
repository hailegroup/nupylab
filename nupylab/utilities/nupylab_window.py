"""Base window module for NUPyLab GUIs."""

from __future__ import annotations

import contextlib
import functools
import inspect
import logging
import os
import tempfile
from typing import Dict, Iterator, List, Optional, Sequence, TYPE_CHECKING, Type

import pymeasure.display.windows.managed_dock_window as managed_dock_window
from nupylab.utilities.dock_widgets import NupylabDockWidget, split_y_axis
from nupylab.utilities.parameter_table import ParameterTableWidget
from pymeasure.display.Qt import QtGui, QtWidgets
from pymeasure.display.windows.managed_dock_window import ManagedDockWindow
from pymeasure.experiment import (
    BooleanParameter,
    FloatParameter,
    IntegerParameter,
    ListParameter,
    Results,
    unique_filename,
)

if TYPE_CHECKING:
    import pandas as pd
    from nupylab.utilities.nupylab_procedure import NupylabProcedure

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


@contextlib.contextmanager
def _dock_widget_class(
    y_axis_right_labels: Sequence[Optional[str]],
    y_axis_extra_labels: Optional[Sequence[Sequence[str]]] = None,
    setpoint_labels: Optional[Dict[str, str]] = None,
    log_columns: Optional[Sequence[str]] = None,
) -> Iterator[None]:
    """Build the Dock Tab with right-hand y axes for the duration of the context.

    :class:`~pymeasure.display.windows.managed_dock_window.ManagedDockWindow`
    builds its Dock Tab from the `DockWidget` name in its own module and offers no
    hook for substituting a different class, so the name is swapped for the
    duration of its initializer and restored afterwards.

    Args:
        y_axis_right_labels: right y-axis column of each dock, `None` entries
            giving a dock with a single y axis.
        y_axis_extra_labels: further columns each dock plots beside its
            left-axis one, which gives that dock a color per column.
        setpoint_labels: measured column to the column holding its setpoint, so
            that a dock opening on such a column draws the setpoint dashed
            beside it.
        log_columns: columns whose axis is drawn on a log scale while a dock
            is showing them.
    """
    original = managed_dock_window.DockWidget
    managed_dock_window.DockWidget = functools.partial(
        NupylabDockWidget,
        y_axis_right_labels=y_axis_right_labels,
        y_axis_extra_labels=[list(entry) for entry in (y_axis_extra_labels or ())],
        setpoint_labels=dict(setpoint_labels or {}),
        log_columns=list(log_columns or ()),
    )
    try:
        yield
    finally:
        managed_dock_window.DockWidget = original


class NupylabWindow(ManagedDockWindow):
    """Docked GUI window class.

    Each entry of the procedure class attribute `Y_AXIS` gets its own dock. An
    entry naming two columns, e.g. ``("Ewe (V)", "I (A)")``, gives one dock whose
    plot carries the first column on its left y axis and the second on its right.
    An entry naming three or more gives one dock carrying all of them on one
    axis, a color each, with no column selectors above it.

    A procedure may also define `Y_AXIS_SETPOINTS`, a mapping of a measured
    column to the column holding what was asked for. A dock that opens on such
    a column draws the setpoint beside the measurement as a dashed line of the
    same color.

    NOTE: The parameter order in the procedure class attribute `TABLE_PARAMETERS`
    **MUST MATCH** the order of the corresponding table column labels.
    """

    parameter_types: dict = {
        BooleanParameter: bool,
        FloatParameter: float,
        IntegerParameter: int,
        ListParameter: str,
    }

    def __init__(
        self,
        procedure_class: Type[NupylabProcedure],
        directory: str = "",
        parameters_dir: str = "",
        extra_tabs = None,
        **kwargs,
    ) -> None:
        """Initialize main window GUI.

        Args:
            procedure_class: NUPyLab procedure class to run.
            directory: base data directory. Defaults to the user's Documents folder.
            parameters_dir: directory for saved parameter tables.
            **kwargs: optional keyword arguments that will be passed to
                :class:`pymeasure.display.windows.managed_window.ManagedDockWindow`
        """
        kwargs.setdefault("linewidth", 2)
        if hasattr(procedure_class, "X_AXIS"):
            kwargs.setdefault("x_axis", procedure_class.X_AXIS)
        if hasattr(procedure_class, "Y_AXIS"):
            kwargs.setdefault("y_axis", procedure_class.Y_AXIS)
        # A two-item `Y_AXIS` entry asks for one dock with left and right y axes
        y_axis_right_labels: List[Optional[str]] = []
        y_axis_extra_labels: List[List[str]] = []
        if isinstance(kwargs.get("y_axis"), (list, tuple)):
            y_axis_labels, right_labels, extra_labels = split_y_axis(
                kwargs["y_axis"]
            )
            if any(right_labels) or any(extra_labels):
                kwargs["y_axis"] = y_axis_labels
                y_axis_right_labels = right_labels
                y_axis_extra_labels = extra_labels
        # Columns a dock should draw the setpoint of, dashed, see
        # :mod:`nupylab.utilities.dock_widgets`
        setpoint_labels: Dict[str, str] = dict(
            getattr(procedure_class, "Y_AXIS_SETPOINTS", {}) or {}
        )
        # Columns measured over decades rather than over a range, whose axis
        # is drawn on a log scale, see :mod:`nupylab.utilities.dock_widgets`
        log_columns: List[str] = list(
            getattr(procedure_class, "LOG_COLUMNS", ()) or ()
        )
        if hasattr(procedure_class, "INPUTS"):
            kwargs.setdefault("inputs", procedure_class.INPUTS)
        table_column_labels = list(procedure_class.TABLE_PARAMETERS)
        combo_col_dict = {}
        for i, param_name in enumerate(procedure_class.TABLE_PARAMETERS.values()):
            for name, value in inspect.getmembers(procedure_class):
                if name == param_name and isinstance(value, ListParameter):
                    combo_col_dict[i] = list(value.choices)
        # Procedures may define `table_row_errors` to flag invalid table cells
        row_validator = None
        if hasattr(procedure_class, "table_row_errors"):
            label_to_name = dict(procedure_class.TABLE_PARAMETERS)
            name_to_label = {v: k for k, v in label_to_name.items()}

            def row_validator(row: Dict[str, str]) -> Dict[str, str]:
                errors = procedure_class.table_row_errors(
                    {label_to_name[label]: text for label, text in row.items()
                     if label in label_to_name}
                )
                return {name_to_label[name]: message for name, message in errors.items()
                        if name in name_to_label}

        with contextlib.ExitStack() as stack:
            if (
                y_axis_right_labels
                or y_axis_extra_labels
                or setpoint_labels
                or log_columns
            ):
                stack.enter_context(
                    _dock_widget_class(
                        y_axis_right_labels,
                        y_axis_extra_labels,
                        setpoint_labels,
                        log_columns,
                    )
                )
            super().__init__(
                procedure_class,
                inputs_in_scrollarea=True,
                widget_list=(
                    ParameterTableWidget("Experiment Parameters", table_column_labels, combo_columns=combo_col_dict, parameters_dir=parameters_dir, row_validator=row_validator,),
                ),
                **kwargs,
            )
        # Right-axis and setpoint columns are plotted too, so the browser must
        # expect them
        self.browser_widget.browser.measured_quantities.update(
            label for label in y_axis_right_labels if label
        )
        self.browser_widget.browser.measured_quantities.update(
            label for entry in y_axis_extra_labels for label in entry
        )
        self.browser_widget.browser.measured_quantities.update(
            label for label in setpoint_labels.values() if label
        )
        self.setWindowTitle(f"{procedure_class.__name__}")
        self.directory = directory or os.path.join(
            os.path.expanduser("~"), "Documents"
        )
        if hasattr(self, 'file_input'):
            self.file_input.filename = "EXPRDATA"
            
        # Filter instrument_control logs out of the experiment log widget
        class ExcludeInstrumentControlFilter(logging.Filter):
            def filter(self, record):
                return not record.name.startswith('nupylab.instrument_control')

        if hasattr(self, 'log_widget') and hasattr(self.log_widget, 'handler'):
            self.log_widget.handler.addFilter(ExcludeInstrumentControlFilter())

        if extra_tabs:
            for tab_name, tab_widget in extra_tabs:
                self.tabs.addTab(tab_widget, tab_name)

        self.setWindowTitle(f"{procedure_class.__name__}")
        self._keep_log_tab_text_themed()
        self.manager.failed.connect(self._on_failed)

    def _keep_log_tab_text_themed(self) -> None:
        """Leave the Experiment Log tab's name whatever colour the theme gives.

        PyMeasure blinks that tab's name when something is logged as a warning
        or an error, and paints it back to a hard-coded black between blinks
        and once the tab has been read. On a dark theme that is a name the
        same colour as the tab behind it, so the Experiment Log tab reads as
        blank while every other tab name is white.

        Painting it back to no colour at all instead leaves the tab bar to
        draw the name as it draws the others, under either theme. Only the
        colour it returns to changes: the blink itself, and the icon that
        comes with it, are PyMeasure's and still happen.
        """
        log_widget = getattr(self, "log_widget", None)
        if log_widget is None:
            return

        def blink() -> None:
            tabs = getattr(log_widget, "tab_widget", None)
            index = getattr(log_widget, "tab_index", None)
            if tabs is None or index is None:
                return
            # An invalid color is what a tab bar takes as "no colour of its
            # own", which is the theme's and so readable either way round
            color = QtGui.QColor()
            if not log_widget._blink_state and log_widget._blink_color:
                color = QtGui.QColor(log_widget._blink_color)
            tabs.tabBar().setTabTextColor(index, color)
            log_widget._blink_state = not log_widget._blink_state

        # The timer holds the method it was given when the widget was built,
        # so replacing the attribute alone would leave the blink itself black
        try:
            log_widget._blink_qtimer.timeout.disconnect()
        except (RuntimeError, TypeError):
            pass
        log_widget._blink_qtimer.timeout.connect(blink)
        log_widget._blink = blink

    def abort_returned(self, experiment):
        self.browser_widget.clear_button.setEnabled(True)
        if self.manager.experiments.has_next():
            self.abort_button.setText("Resume")
            self.abort_button.setEnabled(True)
            try:
                self.abort_button.clicked.disconnect()
            except Exception:
                pass
            self.abort_button.clicked.connect(self.resume)
        else:
            self.abort_button.setText("Abort")
            self.abort_button.setEnabled(False)
            try:
                self.abort_button.clicked.disconnect()
            except Exception:
                pass
            self.abort_button.clicked.connect(self.abort)

    def finished(self, experiment):
        """Show Resume if more steps queued, else re-enable clear button."""
        self.browser_widget.clear_button.setEnabled(True)
        if self.manager.experiments.has_next():
            self.abort_button.setText("Resume")
            self.abort_button.setEnabled(True)
            try:
                self.abort_button.clicked.disconnect()
            except Exception:
                pass
            self.abort_button.clicked.connect(self.resume)
        else:
            self.abort_button.setEnabled(False)
            try:
                self.abort_button.clicked.disconnect()
            except Exception:
                pass
            self.abort_button.clicked.connect(self.abort)

    def _on_failed(self, experiment):
        """Handle failed experiment — re-enable controls and show Resume if more steps queued."""
        self.browser_widget.clear_button.setEnabled(True)
        if self.manager.experiments.has_next():
            self.abort_button.setText("Resume")
            self.abort_button.setEnabled(True)
            try:
                self.abort_button.clicked.disconnect()
            except Exception:
                pass
            self.abort_button.clicked.connect(self.resume)
        else:
            self.abort_button.setEnabled(False)
            try:
                self.abort_button.clicked.disconnect()
            except Exception:
                pass
            self.abort_button.clicked.connect(self.abort)

    def new_curve(self, wdg, results, color=None, **kwargs):
        kwargs.setdefault("connect", "finite")
        return super().new_curve(wdg, results, color=None, **kwargs)

    def verify_parameters(self, table_df: pd.DataFrame) -> pd.DataFrame:
        """Verify shape of dataframe and attempt to convert datatype.

        Args:
            table_df: Pandas dataframe representing parameters table in string format

        Returns:
            converted_df: parameters table with each column converted to the dtype
                specified in :attr:`parameter_types`

        Raises:
            IndexError: if the number of columns in the parameter table does not match
                the number of expected columns.
            ValueError: if the parameters table cannot be converted to the types listed
                in :attr:`parameter_types`
        """
        if len(self.procedure_class.TABLE_PARAMETERS) != table_df.shape[1]:
            raise IndexError(
                f"Expected {len(self.parameter_types)} parameters, but "
                f"parameters table has {table_df.shape[1]} columns."
            )

        converted_df: pd.DataFrame = table_df.copy()
        bool_map: Dict[str, bool] = {
            "t": True,
            "true": True,
            "yes": True,
            "1": True,
            "f": False,
            "false": False,
            "no": False,
            "0": False,
        }
        cast_dict: dict = {}
        for param_name, column in zip(
            self.procedure_class.TABLE_PARAMETERS.values(), converted_df.columns
        ):
            for name, value in inspect.getmembers(self.procedure_class):
                if name == param_name:
                    # non-empty strings evaluate to True
                    # apply map instead for boolean columns
                    param_cast = self.parameter_types[type(value)]
                    bool_str_map = {
                        "true": "True", "false": "False",
                        "t": "True", "f": "False",
                        "1": "True", "0": "False",
                        "yes": "True", "no": "False",
                    }
                    if param_cast is bool:
                        converted_df[column] = (
                            converted_df[column].str.casefold().map(bool_map)
                        )
                    elif param_cast is str:
                        converted_df[column] = (
                            converted_df[column].fillna("").astype(str).str.strip()
                            .str.casefold().map(lambda x: bool_str_map.get(x, x))
                        )
                        if isinstance(value, ListParameter):
                            # Restore choice capitalization, e.g. "peis" -> "PEIS"
                            choice_map = {
                                str(c).casefold(): str(c) for c in value.choices
                            }
                            converted_df[column] = converted_df[column].map(
                                lambda x: choice_map.get(x, x)
                            )
                            # Blank cells, e.g. from older parameter files, use default
                            if value.default is not None:
                                converted_df[column] = converted_df[column].replace(
                                    "", str(value.default)
                                )
                    cast_dict.update({column: param_cast})
        converted_df = converted_df.astype(cast_dict)
        return converted_df

    def queue(self, procedure=None) -> None:
        """Queue all rows in parameters table. Overwrites parent method."""
        # A GUI that will not run on what its inputs panel is holding, a
        # Sample ID left blank say, sets `input_errors` to something callable
        # giving one line per problem. Asked first, since nothing has been
        # read yet and the panel is where the user has to go back to.
        check = getattr(self, "input_errors", None)
        problems: List[str] = list(check() or ()) if callable(check) else []
        if problems:
            details = "\n".join(problems)
            log.error("Experiment not queued:\n%s", details)
            QtWidgets.QMessageBox.warning(
                self,
                "Nothing Queued",
                "Nothing was queued. Fill the inputs panel in:\n\n" + details,
            )
            return
        log.info("Reading experiment parameters.")
        table_widget = self.tabs.widget(0)
        table_model = table_widget.table.model()
        table_model.validate()
        row_errors = table_model.row_errors()
        if row_errors:
            details = "\n".join(
                f"Step {row + 1}: " + "; ".join(messages)
                for row, messages in row_errors.items()
            )
            log.error("Experiment not queued, invalid parameters:\n%s", details)
            QtWidgets.QMessageBox.warning(
                self,
                "Invalid Parameters",
                "Nothing was queued. Fix the cells marked in red with '!':\n\n"
                + details,
            )
            return
        table_df: pd.DataFrame = table_model.export_df()
        converted_df: pd.DataFrame = self.verify_parameters(table_df)

        num_steps: int = converted_df.shape[0]
        current_step: int = 1
        previous_procedure = None

        # A GUI that names its own files sets `filename_stem` to something
        # callable giving the whole name a file is saved under, before its
        # step number: what the user typed and what the panel named, in
        # whichever order that GUI puts them. Read once here, so that every
        # step of one queue is filed the same way.
        stem_source = getattr(self, "filename_stem", None)
        stem: Optional[str] = stem_source() if callable(stem_source) else None

        for table_row in converted_df.itertuples(index=False):
            procedure: NupylabProcedure = self.make_procedure()
            procedure.num_steps = num_steps
            procedure.current_step = current_step
            for i, parameter in enumerate(procedure.TABLE_PARAMETERS.values()):
                setattr(procedure, parameter, table_row[i])
            procedure.refresh_parameters()
            procedure.previous_procedure = previous_procedure
            current_step += 1
            if self.store_measurement:
                _experiments_dir = os.path.join(self.directory, "Experiments")
                os.makedirs(_experiments_dir, exist_ok=True)
                ext = self.file_input.filename_extension
                if stem is None:
                    # Filename input sets the prefix,
                    # e.g. "EXPRDATA" -> EXPRDATA_<date>_1.csv
                    filename_base = self.file_input.filename_base.strip()
                    prefix = f"{filename_base or 'EXPRDATA'}_"
                    datetimeformat = "%Y-%m-%d"
                else:
                    # The GUI has named the file in full, date included
                    prefix = stem
                    datetimeformat = ""
                try:
                    filename: str = unique_filename(
                        _experiments_dir,
                        prefix=prefix,
                        suffix="_{Current Step}",
                        ext=ext,
                        dated_folder=False,
                        index=False,
                        datetimeformat=datetimeformat,
                        procedure=procedure,
                    )
                except KeyError as e:
                    if not str(e.args[0]).startswith(
                        "The following placeholder-keys are not valid:"
                    ):
                        raise
                    log.error("Invalid filename provided: %s", e.args[0])
                    return
                index: int = 2
                basename: str = filename.rsplit(f".{ext}", 1)[0]
                while os.path.exists(filename):
                    filename = f"{basename}_{index}.{ext}"
                    index += 1
            else:
                # "Save data" unchecked: write to a temporary file, as pymeasure does
                filename = tempfile.mktemp(prefix="TempFile_", suffix=".csv")

            results = Results(procedure, filename)
            experiment = self.new_experiment(results)

            self.manager.queue(experiment)
            previous_procedure = procedure

            # If manager stopped (after abort/fail), show Resume instead of auto-running
        if not self.manager.is_running():
            self.abort_button.setText("Resume")
            self.abort_button.setEnabled(True)
            try:
                self.abort_button.clicked.disconnect()
            except Exception:
                pass
            self.abort_button.clicked.connect(self.resume)