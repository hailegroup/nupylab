"""
Python adapter for the Metrohm Autolab SDK, version 2.1.

Autolab control requires pythonnet (.NET framework support) and Autolab SDK. Adapted
from pyMetrohmAUTOLAB https://github.com/shuayliu/pyMetrohmAUTOLAB
"""


import time
from math import log10, floor
import clr
import numpy as np
import os
from typing import Optional


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

    Raises:
        AutolabError: All regular methods in this class use the Autolab DLL
            communications library to talk with the equipment and they will
            raise this exception if this library reports an error. It will not
            be explicitly mentioned in every single method.
    """

    def __init__(
            self, model: str, sdk_path: Optional[str] = None) -> None:
        r"""Initialize the potentiostat driver.

        Args:
            model: the device model e.g. 'PGSTAT302N'
            address: the address of the instrument, either IP address or 'USB0', 'USB1',
                etc.
            sdk_path: the path to the Autolab SDK. The default directory of the DLL is
                C:\Program Files\Metrohm Autolab\Autolab SDK 2.1\

        Raises:
            WindowsError: If the Autolab SDK DLL cannot be found.
        """
        if model not in INSTRUMENTS:
            message = f"Model {model} not in instrument list: {INSTRUMENTS}"
            raise AutolabException(message, -1000)
        if sdk_path is None:
            sdk_path = r"C:\Program Files\Metrohm Autolab\Autolab SDK 2.1"
        sdk = os.path.join(sdk_path, "EcoChemie.Autolab.Sdk")
        adx = os.path.join(sdk_path, "Hardware Setup Files/Adk.x")
        self.model = model
        self._adx = adx
        self._autolab = None
        self.pcd = None
        if clr.FindAssembly(sdk):
            clr.AddReference(sdk)
            from EcoChemie.Autolab.Sdk import Instrument
            self._autolab = Instrument()
        else:
            message = f"Cannot find {sdk}.dll"
            raise AutolabException(message, -1001)

    def disconnect(self) -> None:
        """Disconnect from Autolab."""
        self._autolab.Disconnect()

    def is_measuring(self) -> bool:
        """Get whether measurement is in progress.

        Returns: boolean indicating whether Autolab is currently measuring.
        """
        if self.pcd is not None:
            return self.pcd.IsMeasuring
        return False

    def connect(self, fra_module: Optional[str] = None) -> bool:
        """Connect to Autolab.

        Hardware configuration file is set by instrument model and FRA option.

        Args:
            fra_module: FRA module to connect to, if installed.

        Returns:
            bool indicating whether Autolab is connected.
        """
        self._autolab.AutolabConnection.EmbeddedExeFileToStart = self._adx
        hdw_file: str = os.path.join(self._sdk_path, "Hardware Setup Files", self.model)
        if fra_module is not None:
            hdw_file = os.path.join(hdw_file, f"HardwareSetup.{fra_module}.xml")
        else:
            hdw_file = os.path.join(hdw_file, "HardwareSetup.xml")
        self._autolab.set_HardwareSetupFile(hdw_file)
        self._autolab.Connect()
        return self._autolab.AutolabConnection.IsConnected

    def measure(self, procedure):
        """Load and run measurement procedure.

        Args:
            procedure: Nova procedure file of .nox type
        """
        self.pcd = self._autolab.LoadProcedure(procedure)

        if self._autolab.AutolabConnection.IsConnected:
            self.pcd.Measure()
        else:
            raise AutolabException("Autolab is not connected", -2000)

    def save(self):
        self.saveAs(self.pcd.get_FileName())

    def saveAs(self,saveName):
        if not len(saveName) == 0:
            saveto = appendSuffixToFilename(saveName,time.strftime("_%Y%m%d-%H%M%S"))
            # CMDLOG(self.CMD,"[INFO] Save File to %s\n\n"%saveto)
            self.pcd.SaveAs(saveto)
        else:
            print("[WARNING]You should give me a NAME to save this file.\n otherwise, please use save() instead of saveAs()")

    def setCellOn(self,On=True):
        self._autolab.Ei.set_CellOnOff(On)
        while self._autolab.Ei.get_CurrentOverload() :
            self._autolab.Ei.set_CurrentRange(self._autolab.Ei.CurrentRange + 1)

    def set_mode(self, mode: str = 'potentiostatic') -> None:
        if mode.casefold() == 'galvanostatic':
            self._autolab.Ei.set_Mode(1)  # Ei.EIMode.Galvanostatic = 0
        elif mode.casefold() == 'potentiostatic':
            self._autolab.Ei.set_Mode(0)  # Ei.EIMode.Potentiostatic = 1
        else:
            message = f"Mode {mode} must be 'potentiostatic' or 'galvanostatic'"
            raise AutolabException(message, -3000)

    def set_potential(self, potential: float) -> float:
        self.set_mode('potentiostatic')
        self._autolab.Ei.set_Setpoint(potential)
        if self._autolab.Ei.get_CurrentOverload() :
            self._autolab.Ei.set_CurrentRange(self._autolab.Ei.Current + 1)

        return self._autolab.Ei.PotentialApplied

    def set_current_range(self, current: float) -> float:
        """Set Autolab current range.

        Args:
            current: expected approximate current value.

        Returns:
            Autolab current range.
        """
        self._autolab.Ei.set_CurrentRange(floor(log10(current)))
        return self._autolab.Ei.CurrentRange

    def loadData(self, filename):
        Data = None
        try:
            pcd = self._autolab.LoadProcedure(filename)

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
                raise Exception('LoadFailedException', Data)
            else:
                return Data

    # TODO: def EIS
    # def EIS(self,EISProc=R"E:\LSh\PicoView 1.14\scripts\STEP0-FRA.nox"):
    #     self.measure(EISProc)


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
