"""Base window module for NUPyLab GUIs."""

from __future__ import annotations
import logging
import os
from typing import Dict, Sequence, TYPE_CHECKING

from pymeasure.display.windows.managed_dock_window import ManagedDockWindow
from pymeasure.experiment import (
    BooleanParameter,
    FloatParameter,
    IntegerParameter,
    Results,
    unique_filename,
)
from nupylab.utilities.parameter_table import ParameterTableWidget

if TYPE_CHECKING:
    import pandas as pd
    from nupylab.utilities.nupylab_procedure import NupylabProcedure

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


class NupylabWindow(ManagedDockWindow):
    """Docked GUI window class.

    NOTE: The parameter order in the procedure class attribute `TABLE_PARAMETERS`
    **MUST MATCH** the order of the corresponding table column labels.
    """

    parameter_types: dict = {
        BooleanParameter: bool,
        FloatParameter: float,
        IntegerParameter: int,
    }

    def __init__(
        self,
        procedure_class: NupylabProcedure,
        table_column_labels: Sequence[str],
        x_axis: Sequence[str],
        y_axis: Sequence[str],
        inputs: Sequence[str],
        *args,
        **kwargs
    ) -> None:
        """Initialize main window GUI.

        Args:
            procedure_class: NUPyLab procedure class to run.
            table_column_labels: labels to apply to parameters table columns.
            x_axis: items in DATA_COLUMNS to plot along x axes in docked plots.
            y_axis: items in DATA_COLUMNS to plot along y axes in docked plots.
            inputs: input parameters not belonging to the parameters table.
        """
        super().__init__(
            procedure_class=procedure_class,
            x_axis=x_axis,
            y_axis=y_axis,
            inputs=inputs,
            inputs_in_scrollarea=True,
            widget_list=(
                ParameterTableWidget("Experiment Parameters", table_column_labels),
            ),
            *args,
            **kwargs
        )
        self.setWindowTitle(f"{procedure_class.__name__}")

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
            "true": True,
            "yes": True,
            "1": True,
            "false": False,
            "no": False,
            "0": False,
        }
        cast_dict: dict = {}
        for parameter, column in zip(
            self.procedure_class.TABLE_PARAMETERS, converted_df.columns
        ):
            # non-empty strings evaluate to True; apply map instead for boolean columns
            param_cast = self.parameter_types[type(parameter)]
            if param_cast is bool:
                converted_df[column] = converted_df[column].str.casefold().map(bool_map)
            cast_dict.update(column, param_cast)
        converted_df = converted_df.astype(cast_dict)
        return converted_df

    def queue(self, procedure=None) -> None:
        """Queue all rows in parameters table. Overwrites parent method."""
        log.info("Reading experiment parameters.")
        table_widget = self.tabs.widget(0)
        table_df: pd.DataFrame = table_widget.table.model().export_df()
        converted_df: pd.DataFrame = self.verify_parameters(table_df)

        num_steps: int = converted_df.shape[0]
        current_step: int = 1
        previous_procedure = None

        for table_row in converted_df.itertuples(index=False):
            procedure: NupylabProcedure = self.make_procedure(previous_procedure)
            procedure.num_steps = num_steps
            procedure.current_step = current_step
            for i, parameter in enumerate(procedure.TABLE_PARAMETERS):
                parameter = table_row[i]
            procedure.previous_procedure = previous_procedure
            current_step += 1
            filename: str = unique_filename(
                self.directory,
                prefix=self.file_input.filename_base + "_",
                suffix="_{Current Step}",
                ext="csv",
                dated_folder=False,
                index=False,
                procedure=procedure,
            )
            index: int = 2
            basename: str = filename.split(".csv")[0]
            while os.path.exists(filename):
                filename = f"{basename}_{index}.csv"
                index += 1

            results = Results(procedure, filename)
            experiment = self.new_experiment(results)

            self.manager.queue(experiment)
            previous_procedure = procedure
