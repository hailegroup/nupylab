Introduction
============

``NUPyLab`` (Northwestern University Python Laboratory) is a suite of tools for
instrument communication and experiment control via Python in the Haile Group.
Included are high-level GUIs for specific lab stations, low-level instrument
drivers, and mid-level instrument classes adapting instrument drivers to the
station GUIs.

High-level components are largely built on PyMeasure and Qt. If not already
available, instrument drivers are contributed to `PyMeasure`_ when compatible
and hosted in NUPyLab when not.

.. _PyMeasure: https://pymeasure.readthedocs.io/en/latest/

Motivation
----------

Python is quickly becoming the favorite programming language of scientists for
its popular data analysis and visualization libraries. As a full-fledged
programming language, it also has powerful capabilities beyond those uses, as
well as a large community that values open-source programming. In contrast,
many instrument control systems in research settings are built on tools that:

* are closed source
* have diminishing community expertise
* have high licensing fees

Diminishing expertise in, e.g., LabVIEW, makes diagnosing and fixing issues
slow and new systems unreliable. This repository is an effort to take advantage
of the growing expertise in Python to convert our lab's instrument control to a
free, open-source, well-supported alternative.
