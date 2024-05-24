"""Classes for adding table of parameters as a tab in station GUIs."""

from typing import List, Optional, Sequence

import pandas as pd

from pymeasure.display.Qt import QtCore, QtGui, QtWidgets
from pymeasure.display.widgets import TabWidget


class FileLineEdit(QtWidgets.QLineEdit):
    """Widget for browsing and selecting file of experimental parameters."""

    def __init__(self, table_model, parent=None):
        """Create line edit widget with completer and file browser."""
        super().__init__(parent)

        self.table_model = table_model

        completer = QtWidgets.QCompleter(self)
        completer.setCompletionMode(
            QtWidgets.QCompleter.CompletionMode.PopupCompletion)

        model = QtGui.QFileSystemModel(completer)
        model.setRootPath(model.myComputer())
        model.setFilter(QtCore.QDir.Filter.Files |
                        QtCore.QDir.Filter.Dirs |
                        QtCore.QDir.Filter.Drives |
                        QtCore.QDir.Filter.NoDotAndDotDot |
                        QtCore.QDir.Filter.AllDirs)
        completer.setModel(model)

        self.setCompleter(completer)

        browse_action = QtGui.QAction(self)
        browse_action.setIcon(self.style().standardIcon(
            getattr(QtWidgets.QStyle.StandardPixmap, 'SP_DialogOpenButton'))
        )
        browse_action.triggered.connect(self.browse_triggered)

        self.addAction(
            browse_action, QtWidgets.QLineEdit.ActionPosition.TrailingPosition
        )

    def _get_starting_directory(self):
        """Get current directory if set, otherwise set to root folder."""
        current_text = self.text()
        if current_text != '' and QtCore.QDir(current_text).exists():
            return current_text
        else:
            return '/'

    def browse_triggered(self):
        """Open dialog for file selection."""
        filename, ext = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "/path/to/parameters.csv",
            self._get_starting_directory(),
            "csv File(*.csv)"
        )
        if ".csv" in filename:
            self.setText(filename)
            self.table_model().update_data(filename)


# TODO: Replace boolean column values with checkbox
class TableModel(QtCore.QAbstractTableModel):
    """Provides a model for displaying and editing table content.

    Required methods to overwrite from QAbstractTableModel are
    * rowCount
    * columnCount
    * data
    * headerData
    * setData
    * flags
    """

    def __init__(
            self, df: Optional[pd.DataFrame] = None, float_digits: int = 1, parent=None
    ) -> None:
        """Set initial view."""
        self.df: pd.DataFrame
        if df is None:
            self.df = pd.DataFrame(data=[0], columns=['None'])
        else:
            self.df = df
        self.float_digits: int = float_digits
        super().__init__(parent)

    def rowCount(self, parent=None) -> int:
        """Return number of rows in .csv file."""
        return self.df.shape[0]

    def columnCount(self, parent=None) -> int:
        """Return number of columns in .csv file."""
        return self.df.shape[1]

    def data(self, index, role=QtCore.Qt.ItemDataRole.DisplayRole) -> Optional[str]:
        """Display table content."""
        if index.isValid() and role == QtCore.Qt.ItemDataRole.DisplayRole:
            value = self.df.iloc[index.row(), index.column()]
            if isinstance(value, float):
                return f"{value:.{self.float_digits:d}g}"
            return str(value)
        return None

    def headerData(self, section, orientation, role) -> Optional[str]:
        """Set header content."""
        if (role == QtCore.Qt.ItemDataRole.DisplayRole
                and orientation == QtCore.Qt.Orientation.Horizontal):
            return str(self.df.columns[section])
        return None

    def setData(self, index, value, role) -> bool:
        """Update cell contents. Called each time a cell is edited."""
        if index.isValid() and role == QtCore.Qt.ItemDataRole.EditRole:
            self.df.iloc[index.row(), index.column()] = value
            return True
        return False

    def flags(self, index):
        """Allow user editing cell content."""
        if index.isValid():
            return QtCore.Qt.ItemFlag.ItemIsEditable | super().flags(index)

    def export_df(self) -> pd.DataFrame:
        """Return parameters table dataframe."""
        return self.df

    def update_data(self, path) -> None:
        """Update data upon selecting new parameters file."""
        self.beginResetModel()
        new_df = pd.read_csv(path, dtype=str)
        self.df = pd.DataFrame(new_df.values, columns=self.df.columns)
        self.endResetModel()

    def append_row(self):
        # row_position = self.rowCount()
        # self.insertRow(row_position)
        if self.rowCount() == 0:
            last_row = pd.DataFrame([("",)*self.columnCount()], columns=self.df.columns)
        else:
            last_row = pd.DataFrame(self.df.iloc[[-1]])
        self.beginResetModel()
        self.df = pd.concat((self.df, last_row), ignore_index=True)
        self.endResetModel()

    def remove_row(self):
        # row_position = self.rowCount()
        # self.removeRow(row_position)
        if self.rowCount() != 0:
            self.beginResetModel()
            self.df = self.df.drop([self.rowCount() - 1])
            self.endResetModel()


class ParameterTable(QtWidgets.QTableView):
    """Table format view of experiment parameters."""

    def __init__(
        self,
        table_columns: List[str],
        float_digits: int = 1,
        parent=None
    ) -> None:
        """Connect to view model and configure basic appearance.

        Args:
           float_digits (int): float-point resolution in table
           parent: parent class
        """
        super().__init__(parent)
        df = pd.DataFrame(columns=table_columns)
        model = TableModel(df=df, float_digits=float_digits)
        self.setModel(model)
        self.horizontalHeader().setStyleSheet("font: bold;")
        self.horizontalHeader().setMinimumHeight(50)
        self.horizontalHeader().setDefaultAlignment(
            QtCore.Qt.AlignCenter | QtCore.Qt.Alignment(QtCore.Qt.TextWordWrap)
        )
        self.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeMode.Interactive
        )

        self.setup_context_menu()

    def export_action(self):
        """Save table to .csv file."""
        df = self.model().export_df()
        if df is not None:
            filename_and_ext = QtWidgets.QFileDialog.getSaveFileName(
                self,
                "Save File",
                "",
                "CSV file (*.csv)",
            )
            filename = filename_and_ext[0]
            if filename:
                if '.csv' not in filename:
                    filename += '.csv'
                df.to_csv(filename, index=False)

    def copy_action(self):
        """Copy table to clipboard."""
        df = self.model().export_df()
        if df is not None:
            df.to_clipboard()

    def add_row_action(self):
        """Append row to table."""
        self.model().append_row()

    def del_row_action(self):
        """Remove last row from table."""
        self.model().remove_row()

    def setup_context_menu(self):
        """Set up context menu for copying and saving table."""
        self.setContextMenuPolicy(
            QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.context_menu)
        self.copy = QtGui.QAction("Copy parameters table", self)
        self.copy.triggered.connect(self.copy_action)
        self.export = QtGui.QAction("Save parameters", self)
        self.export.triggered.connect(self.export_action)
        self.add_row = QtGui.QAction("Add row", self)
        self.add_row.triggered.connect(self.add_row_action)
        self.del_row = QtGui.QAction("Delete row", self)
        self.del_row.triggered.connect(self.del_row_action)

    def context_menu(self, point):
        """Create context menu."""
        menu = QtWidgets.QMenu(self)
        menu.addAction(self.copy)
        menu.addAction(self.export)
        menu.addAction(self.add_row)
        menu.addAction(self.del_row)
        menu.exec(self.mapToGlobal(point))


class ParameterTableWidget(TabWidget, QtWidgets.QWidget):
    """Widget to display experiment parameters in a tabular format."""

    def __init__(
            self,
            name: str,
            table_columns: Sequence[str],
            float_digits: int = 1,
            parent=None
    ) -> None:
        """Initialize UI and layout.

        Args:
            name: name of tab widget
            table_columns: string labels of column headers
            float_digits: float-point resolution in table
            parent: parent class
        """
        super().__init__(name, parent)
        self.float_digits = float_digits
        self._setup_ui(table_columns)
        self._layout()

    def _setup_ui(self, table_columns):
        self.parameters_file_label = QtWidgets.QLabel(self)
        self.parameters_file_label.setText('Load Parameters:')

        self.table = ParameterTable(
            table_columns,
            float_digits=self.float_digits,
            parent=self,
        )

        self.parameters_file = FileLineEdit(self.table.model, self)

    def _layout(self):
        vbox = QtWidgets.QVBoxLayout(self)
        vbox.setSpacing(0)

        hbox = QtWidgets.QHBoxLayout()
        hbox.setSpacing(10)
        hbox.setContentsMargins(-1, 6, -1, 6)
        hbox.addWidget(self.parameters_file_label)
        hbox.addWidget(self.parameters_file)

        vbox.addLayout(hbox)
        vbox.addWidget(self.table)
        self.setLayout(vbox)

    def preview_widget(self, parent=None):
        """Return a widget suitable for preview during loading."""
        return ParameterTableWidget("Table preview",
                                    ["Preview", "Preview", "Preview"],
                                    float_digits=self.float_digits,
                                    parent=None,
                                    )
