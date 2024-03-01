from setuptools import setup

with open('VERSION.txt') as version_file:
    version = version_file.read().strip()

setup(
    name='nupylab',
    version=version,
    description='Python-based lab instrument control for the Haile Group',
    author='Haile Group',
    author_email='haile.research.lab@gmail.com',
    url='https://github.com/hailegroup/nupylab',
    install_requires=[
        'gpib-ctypes',
        'minimalmodbus',
        'numpy',
        'pandas',
        'pint',
        'pymeasure >= 0.13.1',
        'pytest',
        'pyvisa',
        'pyvisa-py',
    ],
    python_requires="==3.8.*",
    classifiers=[
        'Development Status :: 1 - Planning',
        'Intended Audience :: Science/Research',
        'License :: OSI Approved :: GNU General Public License v3 (GPLv3)',
        'Operating System :: Microsoft :: Windows',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.8',
        'Topic :: Scientific/Engineering',
    ],
)
