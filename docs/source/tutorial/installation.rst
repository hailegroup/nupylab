############
Installation
############


Setting up Python
=================

It is best to first create a new Python environment for NUPyLab so it doesn't
interfere with the system Python installation or other environments. The
easiest way to create and manage a Python environment for NUPyLab is with
**conda**. `Miniconda`_ provides a light-weight alternative to the heavier (but
fancier) `Anaconda`_, which may be better for older lab computers. Conda
environments can also manage non-Python dependencies, which is helpful for
installing NUPyLab.

Download and install the `appropriate Python version of Anaconda or Miniconda
for your operating system <Python OS_>`_. The latest version of Python that can
run on Windows 7 is Python 3.8.

.. _Miniconda: https://docs.anaconda.com/free/miniconda/index.html
.. _Anaconda: https://docs.anaconda.com/free/anaconda/
.. _Python OS: https://www.python.org/downloads/operating-systems/


Installing NUPyLab
==================

If you have `Anaconda`_ or `Miniconda`_, you can use conda to create a new
Python environment. NUPyLab requires either PyQt or PySide, and it is
recommended that either be installed with :code:`conda` first before installing
NUPyLab with :code:`pip`. NUPyLab uses Qt graphics, and installing PyQt or
PySide with :code:`conda` will make sure Qt is also installed, whereas
:code:`pip` only installs Python packages. For Qt5, install PyQt5 or PySide5;
for Qt6 install PyQt6 or PySide6.

Open a terminal and type the following commands (on Windows look for the
**Anaconda Prompt** in the Start Menu):

.. code-block:: bash

    conda create -n nupylab
    conda activate nupylab
    conda install PySide6
    pip install nupylab

This will install NUPyLab and all the required dependencies. You can verify the
installation with :code:`pip show nupylab`.

Installing VISA
---------------
Most instruments communicate with a VISA adapter, which requires either the
VISA library provided by a vendor like `National Instruments`_, or the pure
Python backend `PyVISA-Py`_. See the `PyVISA documentation`_ for more
information about configuring the backend.

.. _National Instruments: https://www.ni.com/en/support/downloads/drivers/download.ni-visa.html
.. _PyVISA-Py: https://pyvisa.readthedocs.io/projects/pyvisa-py/en/latest/
.. _PyVISA documentation: https://pyvisa.readthedocs.io/en/latest/introduction/configuring.html

Contributing
------------
If you want to add an instrument driver, create a new station GUI, or otherwise
edit the NUPyLab code, you can contribute to the `GitHub repository`_:

    1. Install :code:`git` on your computer, e.g. :code:`conda install git`
    2. Fork the :code:`hailegroup/nupylab` repository on GitHub
    3. Remove any previous NUPyLab installation with :code:`pip uninstall nupylab`
    4. Download your forked repository with :code:`git clone https://github.com/<your-name>/nupylab.git`
    5. Change the current working directory to the location of the clone
    6. Install NUPyLab in editable mode: :code:`pip install -e .`
    7. Create a new git branch for your feature and submit a pull request with your edits

.. _GitHub repository: https://github.com/hailegroup/nupylab

