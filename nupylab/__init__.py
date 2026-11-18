"""NUPyLab: instrument control and experiment automation for the Haile Group."""

import sys

if sys.version_info < (3, 8):
    # PyMeasure 0.13.0 and newer import ``importlib.metadata`` unconditionally,
    # but that module is stdlib only from Python 3.8. On the lab's 32-bit
    # Windows 7 machines (Python 3.7) the import raises ModuleNotFoundError
    # before any NUPyLab code runs, so point the name at the backport here.
    #
    # This runs before PyMeasure is imported: Python initialises a parent
    # package before any of its submodules, and every module that imports
    # PyMeasure lives under ``nupylab``.
    import importlib

    import importlib_metadata

    sys.modules.setdefault("importlib.metadata", importlib_metadata)
    importlib.metadata = importlib_metadata

__version__ = "0.1.0"
__author__ = "Connor Carr"
__credits__ = "Haile Group"
