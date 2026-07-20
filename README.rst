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

*****************
Development Setup
*****************

1. Install `Git <https://git-scm.com/downloads/win>`_ and `Python <https://python.org>`_.
2. Open ``Git Bash``.
3. Clone this repo: ``git clone https://github.com/hailegroup/nupylab.git``.
4. Change to the repository: ``cd nupylab``.
5. Create a virtualenv: ``python -m venv .venv``
6. Activate the virtualenv: ``source .venv/Scripts/activate``
7. Update pip: ``python -m pip install -U pip``
8. Install the packages: ``pip install -e .[develop]``
9. Start a GUI: ``python nupylab/gui/safc_gui.py``
