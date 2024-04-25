"""
Python adapter for the Metrohm Autolab SDK, version 2.1.

Autolab control requires pythonnet (.NET framework support) and Autolab SDK. Adapted
from pyMetrohmAUTOLAB https://github.com/shuayliu/pyMetrohmAUTOLAB
"""


import time
from math import log10, floor
import logging
import clr
import numpy as np
import os
from typing import Optional

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

def appendSuffixToFilename(filename, suffix):
    if not len(filename) == 0:
        dotAt = filename.rfind('.')
        baseName = filename[0:dotAt]
        extName = filename[dotAt:]
        return baseName+suffix+extName
    else:
        return filename


class Autolab:
    """Driver for Metrohm Autolab potentiostats.

    The Autolab SDK is not functional without an instrument connection, which requires a
    connected instrument and the appropriate hardware setup file. For DC measurements,
    providing the generic setup file for the instrument may be provided. For AC
    measurements, the hardware setup file must contain FRA calibration data. In this
    case, the setup file will contain the serial number of the instrument, e.g.
    `HardwareSetup.AUT81234.xml`.

    Raises:
        AutolabError: All regular methods in this class use the Autolab DLL
            communications library to talk with the equipment and they will
            raise this exception if this library reports an error. It will not
            be explicitly mentioned in every single method.
    """

    def __init__(
            self,
            hardware_file: str,
            sdk_path: str = r"C:\Program Files\Metrohm Autolab\Autolab SDK 2.1"
    ) -> None:
        r"""Initialize the potentiostat driver.

        Args:
            hardware_file: the hardware configuration file for the instrument. See class
                note.
            sdk_path: the path to the Autolab SDK.

        Raises:
            WindowsError: If the Autolab SDK DLL cannot be found.
        """
        sdk = os.path.join(sdk_path, "EcoChemie.Autolab.Sdk")
        self._adx = os.path.join(sdk_path, "Hardware Setup Files/Adk.x")
        self.pcd = None
        if clr.FindAssembly(sdk):
            clr.AddReference(sdk)
            from EcoChemie.Autolab.Sdk import Instrument
            self._instrument = Instrument()
        else:
            message = f"Cannot find {sdk}.dll"
            raise AutolabException(message, -1000)
        connected = self.connect(hardware_file)

    def disconnect(self) -> None:
        """Disconnect from Autolab."""
        self._instrument.Disconnect()

    @property
    def is_measuring(self) -> bool:
        """Get whether measurement is in progress.

        Returns: boolean indicating whether Autolab is currently measuring.
        """
        if self.pcd is not None:
            return self.pcd.IsMeasuring
        return False

    def connect(self, hardware_file: str) -> bool:
        """Connect to Autolab.

        Hardware configuration file is set by instrument model and FRA option.

        Args:
            fra_module: FRA module to connect to, if installed, e.g. `FRA2`.

        Returns:
            bool indicating whether Autolab is connected.
        """
        self._instrument.AutolabConnection.EmbeddedExeFileToStart = self._adx
        self._instrument.set_HardwareSetupFile(hardware_file)
        self._instrument.Connect()
        return self._instrument.AutolabConnection.IsConnected

    def measure(self, procedure):
        """Load and run measurement procedure.

        Args:
            procedure: Nova procedure file of .nox type
        """
        self.pcd = self._instrument.LoadProcedure(procedure)

        if self._instrument.AutolabConnection.IsConnected:
            self.pcd.Measure()
        else:
            raise AutolabException("Autolab is not connected", -2000)

    def save(self) -> None:
        """Save procedure as current filename with date and time appended."""
        self.save_as(self.pcd.get_FileName())

    def save_as(self, filename: str) -> None:
        """Save procedure to filename with with date and time appended."""
        saveto = appendSuffixToFilename(filename, time.strftime("_%Y%m%d-%H%M%S"))
        self.pcd.SaveAs(saveto)

    def setCellOn(self,On=True):
        self._instrument.Ei.set_CellOnOff(On)
        while self._instrument.Ei.get_CurrentOverload() :
            self._instrument.Ei.set_CurrentRange(self._instrument.Ei.CurrentRange + 1)

    def set_mode(self, mode: str = 'potentiostatic') -> None:
        if mode.casefold() == 'galvanostatic':
            self._instrument.Ei.set_Mode(1)  # Ei.EIMode.Galvanostatic = 0
        elif mode.casefold() == 'potentiostatic':
            self._instrument.Ei.set_Mode(0)  # Ei.EIMode.Potentiostatic = 1
        else:
            message = f"Mode {mode} must be 'potentiostatic' or 'galvanostatic'"
            raise AutolabException(message, -3000)

    def set_potential(self, potential: float) -> float:
        self.set_mode('potentiostatic')
        self._instrument.Ei.set_Setpoint(potential)
        if self._instrument.Ei.get_CurrentOverload() :
            self._instrument.Ei.set_CurrentRange(self._instrument.Ei.Current + 1)

        return self._instrument.Ei.PotentialApplied

    def set_current_range(self, current: float) -> float:
        """Set Autolab current range.

        Args:
            current: expected approximate current value.

        Returns:
            Autolab current range.
        """
        self._instrument.Ei.set_CurrentRange(floor(log10(current)))
        return self._instrument.Ei.CurrentRange

    def loadData(self, filename):
        Data = None
        try:
            pcd = self._instrument.LoadProcedure(filename)

            if pcd.Commands.ContainsId('FHCyclicVoltammetry2'):
                # CMDLOG(self.CMD,"It is a CV procedure DATA!\n");
                cmd = pcd.Commands['FHCyclicVoltammetry2']
                sig = cmd.Signals
#                sigTime      = np.array(sig.get_Item('CalcTime').ValueAsObject)
#                sigCurrent   = np.array(sig.get_Item('EI_0.CalcCurrent').ValueAsObject)
#                sigPotential = np.array(sig.get_Item('EI_0.CalcPotential').ValueAsObject)
#                sigPotAppl   = np.array(sig.get_Item('SetpointApplied').ValueAsObject)
#
                Data = np.array([
                    sig.get_Item('SetpointApplied').ValueAsObject,
                    sig.get_Item('EI_0.CalcCurrent').ValueAsObject,
                    sig.get_Item('CalcTime').ValueAsObject,
                    sig.get_Item('ScanNumber').ValueAsObject
                    ]).T

                # CMDLOG(self.CMD,"The File Format is %s \n"%
                #             ' '.join(['SetpointApplied',
                #             'EI_0.CalcCurrent',
                #             'CalcTime',
                #             'ScanNumber'])
                #             )

            elif pcd.Commands.ContainsId('PlotsNyquist') and pcd.Commands.ContainsId('PlotsBodeModulus'):
                # CMDLOG(self.CMD,"It is a CV procedure DATA!\n");

                cmd1 = pcd.Commands['PlotsNyquist']
                cmd2 = pcd.Commands['PlotsBodeModulus']

                Data = np.array([
                    cmd1.CommandParameters['Z'].ValueAsObject,  # Freq
                    cmd1.CommandParameters['X'].ValueAsObject,  # Zr
                    cmd1.CommandParameters['Y'].ValueAsObject,  # Zi
                    cmd2.CommandParameters['Y'].ValueAsObject,  # ZMod
                    cmd2.CommandParameters['Z'].ValueAsObject  # -Phase
                    ]).T

                # CMDLOG(self.CMD,"The File Format is %s \n"%
                #             ' '.join(['Frequency',
                #             'Zreal',
                #             'Zimaginary',
                #             'Zmodulus',
                #             '-Phase'])
                #             )

        except Exception as e:
            print(repr(e))

        finally:
            if Data is None:
                raise AutolabException("Failed to read data.", -4000)
            else:
                return Data


# Exceptions
class AutolabException(Exception):
    """Base exception for all Autolab SDK exceptions."""

    def __init__(self, message, error_code) -> None:
        """Initialize base exception."""
        super().__init__(message)
        self.error_code = error_code
        self.message = message

    def __str__(self):
        """__str__ representation of the AutolabException."""
        string = (
            f"{self.__class__.__name__} code: {self.error_code}. Message "
            f"\'{self.message.decode('utf-8')}\'"
        )
        return string

    def __repr__(self):
        """__repr__ representation of the AutolabException."""
        return self.__str__()


INSTRUMENTS = (
    "AutolabIMP",
    "M101",
    "PGSTAT10",
    "PGSTAT12",
    "PGSTAT20",
    "PGSTAT30",
    "PGSTAT100",
    "PGSTAT100N",
    "PGSTAT101",
    "PGSTAT128N",
    "PGSTAT204",
    "PGSTAT302",
    "PGSTAT302F",
    "PGSTAT302N",
    "uAutolabII",
    "uAutolabIII"
)
