#########################################
Northwestern University Python Laboratory
#########################################

``NUPyLab`` is a suite of tools for instrument communication and experiment
control via Python, heavily built on `PyMeasure`_.
NUPyLab was conceived primarily as a LabVIEW replacement for experiments in the
`Haile Group`_ lab, but others may find useful components as well.

Included are high-level GUIs for specific lab stations, low-level instrument
drivers, and mid-level instrument classes adapting instrument drivers to the
station GUIs. Check out `the documentation`_ for information on how to get
started with NUPyLab.

.. image:: https://readthedocs.org/projects/nupylab/badge/?version=latest
    :target: https://nupylab.readthedocs.io/en/latest/?badge=latest
    :alt: Documentation Status

.. _Haile Group: https://addis.ms.northwestern.edu/
.. _PyMeasure: https://pymeasure.readthedocs.io/en/latest/
.. _the documentation: https://nupylab.readthedocs.io/en/latest/

************
Installation
************

NUPyLab runs on Python 3.8 and newer. Python 3.7 is also supported, for 32-bit
Windows 7 machines where it is the newest usable interpreter -- CPython 3.9
dropped Windows 7. ``pyproject.toml`` carries both dependency sets and pip picks
between them from the running interpreter, so the commands below are identical
on every machine.

From a clone of the repository, on Windows::

    install.bat

That upgrades ``pip``, ``setuptools`` and ``wheel``, then installs NUPyLab in
editable mode with ``--only-binary :all:`` so pip never attempts a source build.
By hand, it is::

    python -m pip install --upgrade pip setuptools wheel
    python -m pip install --only-binary :all: -e .

Upgrading pip first is required rather than tidiness: the pip bundled with
Python 3.7 predates the packaging standard this project's ``pyproject.toml``
uses and cannot read it.

The station GUIs additionally need a Qt binding, installed as an extra::

    pip install --only-binary :all: -e ".[qt5]"   # PySide2, or PyQt5 on Python 3.7
    pip install --only-binary :all: -e ".[qt6]"   # PySide6; 64-bit, Python 3.8+

On 32-bit Windows, use ``qt5``. PySide6 has never published a 32-bit Windows
wheel, so the ``qt6`` extra resolves to nothing on Python 3.7.

.. note::

   ``nupylab/instruments/scanner/keithley705.py`` uses Python 3.8 syntax and
   will not import under 3.7. Only ``safc1_gui.py`` depends on it; the other
   station GUIs are unaffected.

.. note::

   On Python 3.7 the PyMeasure version matters and ``pip install -e .`` alone is
   not enough, which is why ``install.bat`` exists. PyMeasure 0.14.0 is required
   -- it is the first release with the ``proterial`` (ROD-4) and
   ``agilent4284A`` drivers -- and it declares ``Requires-Python >=3.8``, so
   ``install.bat`` installs it with ``--ignore-requires-python``. Its source has
   no 3.8-only syntax; the one stdlib module it assumes, ``importlib.metadata``,
   is aliased to the ``importlib_metadata`` backport in ``nupylab/__init__.py``.
   Installing PyMeasure 0.13.0 instead produces
   ``ModuleNotFoundError: No module named 'importlib.metadata'`` on import.

*****************
Development Setup
*****************

1. Install `Git <https://git-scm.com/downloads/win>`_ and `Python <https://python.org>`_.
2. Open ``Git Bash``.
3. Clone this repo: ``git clone https://github.com/hailegroup/nupylab.git``.
4. Change to the repository: ``cd nupylab``.
5. Create a virtualenv: ``python -m venv .venv``
6. Activate it: ``source .venv/Scripts/activate`` in Git Bash, or
   ``.venv\Scripts\Activate.ps1`` in PowerShell.
7. Update the installer tools: ``python -m pip install -U pip setuptools wheel``
8. Install the packages: ``pip install --only-binary :all: -e ".[develop]"``
9. Start a GUI, e.g. ``python nupylab/gui/s8_gui.py``

The available station GUIs are ``s4_gui.py``, ``s8_gui.py``, ``safc1_gui.py``,
``safc2_gui.py`` and ``rod4_manual_gui.py``; ``safc_gui_fake.py`` runs against
simulated instruments.
