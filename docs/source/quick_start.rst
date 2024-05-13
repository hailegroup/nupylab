###########
Quick Start
###########

This section provides instructions for getting up and running quickly with NUPyLab.

Setting up Python
=================

It is best to first create a new Python environment for NUPyLab so it doesn't interfere with the system Python installation or other environments.
The easiest way to create and manage a Python environment for NUPyLab is with **conda**. `Miniconda`_ provides a light-weight alternative to the heavier (but fancier) `Anaconda`_, which may be better for older lab computers.

Download and install the `appropriate Python version of Anaconda or Miniconda for your operating system <Python OS_>`_. For example, Windows 7 is not compatible with Python 3.9 or greater.

.. _Miniconda: https://docs.anaconda.com/free/miniconda/index.html
.. _Anaconda: https://docs.anaconda.com/free/anaconda/
.. _Python OS: https://www.python.org/downloads/operating-systems/

Installing NUPyLab
==================

If you have `Anaconda`_ or `Miniconda`_, you can use conda to create a new Python environment. It is recommended to first install PyMeasure with :code:`conda`, then install NUPyLab with :code:`pip`.
The reason is that NUPyLab and PyMeasure (which NUPyLab depends on) use Qt graphics, and installing PyMeasure with :code:`conda` will make sure Qt is installed, whereas :code:`pip` only installs Python packages.

Open a terminal and type the following commands (on Windows look for the **Anaconda Prompt** in the Start Menu):

.. code-block:: bash

    conda create -n nupylab
    conda activate nupylab
    conda install pymeasure
    pip install nupylab

This will install NUPyLab and all the required dependencies. 


Installing VISA
---------------
Most instruments communicate with a VISA adapter, which requires either the VISA library provided by a vendor like `National Instruments`_, or the pure Python backend `PyVISA-Py`_.
See the `PyVISA documentation`_ for more information about configuring the backend.
.. _National Instruments: https://www.ni.com/en/support/downloads/drivers/download.ni-visa.html
.. _PyVISA-Py: https://pyvisa.readthedocs.io/projects/pyvisa-py/en/latest/
.. _PyVISA: https://pyvisa.readthedocs.io/en/latest/introduction/configuring.html


Contributing
------------
If you want to edit the NUPyLab code, add an instrument driver, or create a new station GUI, you can contribute to the `GitHub repository`_:
    1. Install :code:`git` on your computer
    2. Fork the :code:`hailegroup/nupylab` repository on GitHub
    3. Remove any previous NUPyLab installation with :code:`pip uninstall nupylab`
    4. Download your forked repository with :code:`git clone https://github.com/<your-name>/nupylab.git
    5. Install 

