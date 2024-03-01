import os
with open(os.path.join(os.path.abspath('../'), 'VERSION.txt')) as version_file:
    version = version_file.read().strip()

__version__ = version
__author__ = "Connor Carr"
__credits__ = "Haile Group"
