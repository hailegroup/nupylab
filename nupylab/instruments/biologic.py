"""Python adapter for the EC-Lab Development Package, Version 6.06.

.. toctree::
   :maxdepth: 2

.. code-block:: python

    from nupylab.instruments.biologic import GeneralPotentiostat, OCV
    address = '192.109.209.128'
    sp200 = GeneralPotentiostat('SP200', address)
    sp200.connect()
    sp200.load_firmware([0,],)
    ocv = OCV(duration=60,
              record_every_dE=0.01,
              record_every_dt=1.0,
              E_range='KBIO_ERANGE_AUTO')
    sp200.load_technique(0, ocv)
    sp200.start_channel(0)
    data = sp200.get_data(0)
    sp200.stop_channel(0)
    sp200.disconnect()

.. note :: When using the different techniques with the EC-lib DLL, different
 technique files must be passed to the library, depending on **which series
 the instrument is in (VMP3 series or SP-300 series)**.

.. note :: On **64-bit Windows systems**, you should use the ``EClib64.dll``
 instead of the ``EClib.dll``. If the EC-lab development package is installed
 in the default location, this driver will try and load the correct DLL
 automatically, if not, the DLL path will need to passed explicitely and the
 user will need to take 32 vs. 64 bit into account. **NOTE:** The relevant 32
 vs. 64 bit status is that of Windows, not of Python.

.. note :: If it is desired to run this driver and the EC-lab development DLL
 on **Linux**, this can be **achieved with Wine**. This will require
 installing both the EC-lab development package AND Python inside
 Wine. Getting Python installed is easiest, if it is a 32 bit Wine
 environment, so before starting, it is recommended to set such an environment
 up.

.. note:: All methods mentioned in the documentation are implemented unless
 mentioned in the list below:

 * (General) BL_GetVolumeSerialNumber (Not implemented)
 * (Communications) BL_TestCommSpeed (Not implemented)
 * (Communications) BL_GetUSBdeviceinfos (Not implemented)
 * (Channel information) BL_GetHardConf (N/A, only available w. SP300 series)
 * (Channel information) BL_SetHardConf (N/A, only available w. SP300 series)
 * (Technique) BL_UpdateParameters (Not implemented)
 * (Data) BL_GetFCTData (Not implemented)
 * (Misc) BL_SetExperimentInfos (Not implemented)
 * (Misc) BL_GetExperimentInfos (Not implemented)
 * (Misc) BL_SendMsg (Not implemented)
 * (Misc) BL_LoadFlash (Not implemented)

"""


from __future__ import annotations
from collections import namedtuple
from ctypes import c_uint8, c_uint32, c_int32, c_float, c_double, c_char
from ctypes import Structure, create_string_buffer, byref, POINTER, cast
import inspect
import os
import sys
from typing import Any, Dict, List, Optional, Sequence, Type, TYPE_CHECKING, Union

try:
    from ctypes import WinDLL
except ImportError:
    RUNNING_SPHINX = False
    for module in sys.modules:
        if 'sphinx' in module:
            RUNNING_SPHINX = True
    # Let the module continue after this fatal import error, if we are running
    # on read the docs or we can detect that sphinx is imported
    if not (os.environ.get('READTHEDOCS', None) == 'True' or RUNNING_SPHINX):
        raise

# Numpy is only required if it is desired to get the data as numpy arrays
try:
    import numpy as np
    GOT_NUMPY = True
except ImportError:
    GOT_NUMPY = False

if TYPE_CHECKING:
    from ctypes import Array

# Conversion of data types:
# In doc    | ctypes
# ====================
# int8      | c_int8
# int16     | c_int16
# int32     | c_int32
# uint8     | c_uint8
# unit16    | c_uint16
# uint32    | c_uint32
# boolean   | c_uint8 (FALSE=0, TRUE=1)
# single    | c_float
# double    | c_double


# Named tuples

# A named tuple used to defined a return data field for a technique
DataField = namedtuple('DataField', ['name', 'type'])

# The TechniqueArgument instance, that are used as args arguments, are named
# tuples with the following fields (in order):
#
# * label (str): the argument label mentioned in the :ref:`specification
#   <specification>`
# * type (str): the type used in the :ref:`specification <specification>`
#   ('bool', 'single' and 'integer') and possibly wrap ``[]`` around to
#   indicate an array e.g. ``[bool]```
# * value: The value to be passed, will usually be forwarded from ``__init__``
#   args
# * check (str): The bounds check to perform (if any), possible values are
#   '>=', 'in' and 'in_float_range'
# * check_argument: The argument(s) for the bounds check. For 'in' should be a
#   float or int, for 'in' should be a sequence and for 'in_float_range'
#   should be a tuple of two floats
TechniqueArgument = namedtuple(
    'TechniqueArgument', ['label', 'type', 'value', 'check', 'check_argument']
)


class GeneralPotentiostat:
    """Driver for the potentiostats that can be controlled by the EC-lib DLL.

    A driver for a specific potentiostat type will inherit from this class.

    Raises:
        ECLibError: All regular methods in this class use the EC-lib DLL
            communications library to talk with the equipment and they will
            raise this exception if this library reports an error. It will not
            be explicitly mentioned in every single method.
    """

    def __init__(
            self, type_: str, address: str, eclib_dll_path: Optional[str] = None
    ) -> None:
        r"""Initialize the potentiostat driver.

        Args:
            type_: The device type e.g. 'SP200'
            address: The address of the instrument, either IP address or 'USB0', 'USB1',
                etc.
            eclib_dll_path: The path to the EClib DLL. The default
                directory of the DLL is
                C:\EC-Lab Development Package\EC-Lab Development Package\ and the
                filename is either EClib64.dll or EClib.dll depending on whether the
                operating system is 64 of 32 Windows respectively. If no value is given
                the default location will be used and the 32/64 bit status inferred.

        Raises:
            WindowsError: If the EClib DLL cannot be found
        """
        type_ = 'KBIO_DEV_' + type_.upper()
        self._type = type_
        if type_ in SP300SERIES:
            self.series = 'sp300'
        elif type_ in VMP3SERIES:
            self.series = 'vmp3'
        else:
            message = ('Unrecognized device type: must be in SP300 or VMP3 series.')
            raise ECLibCustomException(-8000, message)

        self.address = address
        self._id: Optional[c_int32] = None
        self._device_info: Optional[DeviceInfos] = None

        # Load the EClib dll
        if eclib_dll_path is None:
            eclib_dll_path = (
                'C:\\EC-Lab Development Package\\EC-Lab Development Package\\'
            )

            # Check whether this is 64 bit Windows (not whether Python is 64 bit)
            if 'PROGRAMFILES(X86)' in os.environ:
                eclib_dll_path += 'EClib64.dll'
            else:
                eclib_dll_path += 'EClib.dll'

        self._eclib = WinDLL(eclib_dll_path)

    @property
    def id_number(self) -> Optional[int]:
        """Return the device id as an integer."""
        if self._id is None:
            return None
        return self._id.value

    @property
    def device_info(self) -> Optional[dict]:
        """Return the device information.

        Returns:
            The device information as a dict or None if the device is not
            connected.
        """
        if self._device_info is not None:
            out = structure_to_dict(self._device_info)
            out['DeviceCode(translated)'] = DEVICE_CODES[out['DeviceCode']]
            return out
        return None

    #####################
    # General functions #
    #####################

    def get_lib_version(self) -> bytes:
        """Return the version of the EClib communications library.

        Returns:
            The version string for the library.
        """
        size = c_uint32(255)
        version = create_string_buffer(255)
        ret = self._eclib.BL_GetLibVersion(byref(version), byref(size))
        self.check_eclib_return_code(ret)
        return version.value

    def get_error_message(self, error_code: int) -> bytes:
        """Return the error message corresponding to error_code.

        Args:
            error_code: The error number to translate.

        Returns:
            The error message corresponding to error_code.

        Raises:
            ECLibError if error message could not be retrieved.
        """
        message = create_string_buffer(255)
        number_of_chars = c_uint32(255)
        ret = self._eclib.BL_GetErrorMsg(
            error_code, byref(message), byref(number_of_chars)
        )
        # IMPORTANT: we cannot use self.check_eclib_return_code here, since that
        # internally use this method, thus we have the potential for an infinite loop
        if ret < 0:
            err_msg = (
                'The error message is unknown, because it is the '
                'method to retrieve the error message with that fails. '
                'See the error codes sections (5.4) of the EC-Lab '
                'development package documentation to get the meaning '
                'of the error code.'
            )
            raise ECLibError(err_msg, ret)
        return message.value

    ############################
    # Communications functions #
    ############################

    def connect(self, timeout: int = 5) -> Optional[dict]:
        """Connect to the instrument and return the device info.

        Args:
            timeout: The connect timeout

        Returns:
            The device information as a dict or None if the device is not connected.

        Raises:
            ECLibCustomException if this class does not match the device type
        """
        address: bytes = self.address.encode('utf-8')
        self._id = c_int32()
        device_info: DeviceInfos = DeviceInfos()
        ret: int = self._eclib.BL_Connect(
            address, timeout, byref(self._id), byref(device_info)
        )
        self.check_eclib_return_code(ret)
        if DEVICE_CODES[device_info.DeviceCode] != self._type:
            message = (f"The device type "
                       f"({DEVICE_CODES[device_info.DeviceCode]}) "
                       f"returned from the device on connect does not match "
                       f"the device type of the class ({self._type})."
                       )
            raise ECLibCustomException(-9000, message)
        self._device_info = device_info
        return self.device_info

    def disconnect(self) -> None:
        """Disconnect from the device."""
        ret = self._eclib.BL_Disconnect(self._id)
        self.check_eclib_return_code(ret)
        self._id = None
        self._device_info = None

    def test_connection(self) -> None:
        """Test the connection."""
        ret = self._eclib.BL_TestConnection(self._id)
        self.check_eclib_return_code(ret)

    ######################
    # Firmware functions #
    ######################

    def load_firmware(
            self, channels: Sequence[int], force_reload: bool = False
    ) -> List[int]:
        """Load the library firmware on the specified channels, if not already loaded.

        Args:
            channels: List with 1 integer per channel (usually 16) that indicates which
                channels the firmware should be loaded on (0=False and 1=True).
                NOTE: The length of the list corresponds to the number of channels
                supported by the equipment, not the number of channels installed.
            force_reload: If True the firmware is forcefully reloaded, even if it was
                already loaded

        Returns:
            List of integers indicating the success of loading the firmware on the
            specified channel. 0 is success and negative values are errors, whose error
            message can be retrieved with the get_error_message method.
        """
        c_results = (c_int32 * len(channels))()
        p_results = cast(c_results, POINTER(c_int32))

        c_channels = (c_uint8 * len(channels))()
        for i, channel in enumerate(channels):
            c_channels[i] = channel
        p_channels = cast(c_channels, POINTER(c_uint8))

        ret = self._eclib.BL_LoadFirmware(
            self._id,
            p_channels,
            p_results,
            len(channels),
            False,
            force_reload,
            None,
            None,
        )
        self.check_eclib_return_code(ret)
        return list(c_results)

    #################################
    # Channel information functions #
    #################################

    def is_channel_plugged(self, channel: int) -> bool:
        """Test if the selected channel is plugged.

        Args:
            channel: Selected channel (0-15 on most devices).

        Returns:
            Whether the channel is plugged.
        """
        result: int = self._eclib.BL_IsChannelPlugged(self._id, channel)
        return result == 1

    def get_channels_plugged(self) -> List[bool]:
        """Get information about which channels are plugged.

        Returns:
            A list of channel plugged statuses as booleans.
        """
        status = (c_uint8 * 16)()
        pstatus = cast(status, POINTER(c_uint8))
        ret = self._eclib.BL_GetChannelsPlugged(self._id, pstatus, 16)
        self.check_eclib_return_code(ret)
        return [result == 1 for result in status]

    def get_channel_infos(self, channel: int) -> dict:
        """Get information about the specified channel.

        Args:
            channel: Selected channel, zero based (0-15 on most devices).

        Returns:
            Channel infos dict. The dict is created by conversion from
            :class:`.ChannelInfos` class (type :py:class:`ctypes.Structure`). See the
            documentation for that class for a list of available dict items. Besides the
            items listed, there are extra items for all the original items whose value
            can be converted from an integer code to a string. The keys for those values
            are suffixed by (translated).
        """
        channel_info = ChannelInfos()
        self._eclib.BL_GetChannelInfos(self._id, channel, byref(channel_info))
        out = structure_to_dict(channel_info)

        # Translate code to strings
        out['FirmwareCode(translated)'] = FIRMWARE_CODES[out['FirmwareCode']]
        out['AmpCode(translated)'] = AMP_CODES.get(out['AmpCode'])
        out['State(translated)'] = STATES.get(out['State'])
        out['MaxIRange(translated)'] = I_RANGES.get(out['MaxIRange'])
        out['MinIRange(translated)'] = I_RANGES.get(out['MinIRange'])
        out['MaxBandwidth'] = BANDWIDTHS.get(out['MaxBandwidth'])
        return out

    def get_message(self, channel: int) -> bytes:
        """Return a message from the firmware of a channel."""
        size = c_uint32(255)
        message = create_string_buffer(255)
        ret = self._eclib.BL_GetMessage(self._id, channel, byref(message), byref(size))
        self.check_eclib_return_code(ret)
        return message.value

    #######################
    # Technique functions #
    #######################

    def load_technique(self, channel: int, technique: Technique, first: bool = True,
                       last: bool = True, display: bool = False) -> None:
        """Load a technique on the specified channel.

        Args:
            channel: The number of the channel to load the technique onto, 0-15.
            technique: The technique to load.
            first: Whether this technique is the first technique.
            last: Whether this technique is the last technique.

        Raises:
            ECLibError: On errors from the EClib communications library.
        """
        c_technique_file: bytes
        if self.series == 'sp300':
            filename, ext = os.path.splitext(technique.technique_filename)
            c_technique_file = (filename + '4' + ext).encode('utf-8')
        else:
            c_technique_file = technique.technique_filename.encode('utf-8')

        # Init TECCParams
        c_tecc_params = TECCParams()
        # Get the array of parameter structs
        c_params = technique.c_args(self)
        # Set the len
        c_tecc_params.len = len(c_params)
        p_params = cast(c_params, POINTER(TECCParam))
        c_tecc_params.pParams = p_params

        ret = self._eclib.BL_LoadTechnique(
            self._id,
            channel,
            c_technique_file,
            c_tecc_params,
            first,
            last,
            display,
        )
        self.check_eclib_return_code(ret)

    def define_bool_parameter(self, label: str, value: bool, index: int,
                              tecc_param: TECCParam) -> None:
        """Define a boolean TECCParam for a technique.

        This is a library convenience function to fill out the TECCParam struct in the
        correct way for a boolean value.

        Args:
            label: The label of the parameter.
            value: The boolean value for the parameter.
            index: The index of the parameter.
            tecc_param: A TECCParam struct.
        """
        c_label = label.encode('utf-8')
        ret = self._eclib.BL_DefineBoolParameter(
            c_label, value, index, byref(tecc_param)
        )
        self.check_eclib_return_code(ret)

    def define_single_parameter(self, label: str, value: float, index: int,
                                tecc_param: TECCParam) -> None:
        """Define a single (float) TECCParam for a technique.

        This is a library convenience function to fill out the TECCParam struct in the
        correct way for a single (float) value.

        Args:
            label: The label of the parameter.
            value: The float value for the parameter.
            index: The index of the parameter.
            tecc_param: A TECCParam struct.
        """
        c_label = label.encode('utf-8')
        ret = self._eclib.BL_DefineSglParameter(
            c_label, c_float(value), index, byref(tecc_param)
        )
        self.check_eclib_return_code(ret)

    def define_integer_parameter(self, label: str, value: int, index: int,
                                 tecc_param: TECCParam) -> None:
        """Define an integer TECCParam for a technique.

        This is a library convenience function to fill out the TECCParam struct in the
        correct way for a integer value.

        Args:
            label: The label of the parameter.
            value: The integer value for the parameter.
            index: The index of the parameter.
            tecc_param: A TECCParam struct.
        """
        c_label = label.encode('utf-8')
        ret = self._eclib.BL_DefineIntParameter(
            c_label, value, index, byref(tecc_param)
        )
        self.check_eclib_return_code(ret)

    ########################
    # Start/stop functions #
    ########################

    def start_channel(self, channel: int) -> None:
        """Start the channel.

        Args:
            channel: The channel number, 0-15.
        """
        ret = self._eclib.BL_StartChannel(self._id, channel)
        self.check_eclib_return_code(ret)

    def start_channels(self, channels: Sequence[int]) -> List[int]:
        """Start the selected channels.

        Args:
            channels: Sequence with 1 integer per channel (usually 16) that
                indicates whether the channels will be started (0=False and 1=True).
                NOTE: The length of the list corresponds to the number of channels
                supported by the equipment, not the number of channels installed.

        Returns:
            List of integers indicating the success of starting the specified channel.
            0 is success and negative values are errors, whose error message can be
            retrieved with the get_error_message method.
        """
        c_results = (c_int32 * len(channels))()
        p_results = cast(c_results, POINTER(c_int32))

        c_channels = (c_uint8 * len(channels))()
        for i, channel in enumerate(channels):
            c_channels[i] = channel
        p_channels = cast(c_channels, POINTER(c_uint8))
        ret = self._eclib.BL_StartChannels(
            self._id, p_channels, p_results, len(channels)
        )
        self.check_eclib_return_code(ret)
        return list(c_results)

    def stop_channel(self, channel: int) -> None:
        """Stop the channel.

        Args:
            channel: The channel number, 0-15.
        """
        ret = self._eclib.BL_StopChannel(self._id, channel)
        self.check_eclib_return_code(ret)

    def stop_channels(self, channels: Sequence[int]) -> List[int]:
        """Stop the selected channels.

        Args:
            channels: Sequence with 1 integer per channel (usually 16) that
                indicates whether the channels will be stopped (0=False and 1=True).
                NOTE: The length of the list corresponds to the number of channels
                supported by the equipment, not the number of channels installed.

        Returns:
            List of integers indicating the success of stopping the specified channel.
            0 is success and negative values are errors, whose error message can be
            retrieved with the get_error_message method.
        """
        c_results = (c_int32 * len(channels))()
        p_results = cast(c_results, POINTER(c_int32))

        c_channels = (c_uint8 * len(channels))()
        for i, channel in enumerate(channels):
            c_channels[i] = channel
        p_channels = cast(c_channels, POINTER(c_uint8))
        ret = self._eclib.BL_StopChannels(
            self._id, p_channels, p_results, len(channels)
        )
        self.check_eclib_return_code(ret)
        return list(c_results)

    ##################
    # Data functions #
    ##################

    def get_current_values(self, channel: int) -> dict:
        """Get the current values for the specified channel.

        Args:
            channel: The number of the channel (zero-based).

        Returns:
            A dict of current values information.
        """
        current_values = CurrentValues()
        ret = self._eclib.BL_GetCurrentValues(self._id, channel, byref(current_values))
        self.check_eclib_return_code(ret)

        # Convert the struct to a dict and translate a few values
        out = structure_to_dict(current_values)
        out['State(translated)'] = STATES[out['State']]
        out['IRange(translated)'] = I_RANGES[out['IRange']]
        return out

    def get_data(self, channel: int) -> Optional[KBIOData]:
        """Get data for the specified channel.

        Args:
            channel: The number of the channel (zero based).

        Returns:
            A :class:`.KBIOData` object or None if no data was available.
        """
        # Raw data is retrieved in an array of integers
        c_databuffer = (c_uint32 * 1000)()
        p_data_buffer = cast(c_databuffer, POINTER(c_uint32))
        c_data_infos = DataInfos()
        c_current_values = CurrentValues()

        ret = self._eclib.BL_GetData(
            self._id,
            channel,
            p_data_buffer,
            byref(c_data_infos),
            byref(c_current_values),
        )
        self.check_eclib_return_code(ret)

        # The KBIOData will ask the appropriate techniques for which data
        # fields they return data in
        data: KBIOData = KBIOData(c_databuffer, c_data_infos, c_current_values, self)
        if data.technique == 'KBIO_TECHID_NONE':
            return None
        return data

    def convert_numeric_into_single(self, numeric: int) -> float:
        """Convert a numeric (integer) into a float.

        The buffer used to get data out of the device consist only of uint32s. The EClib
        library stores floats as an uint32 with integer values whose bit-representation
        corresponds to the float that it should describe. This function is used to
        convert the integer back to the corresponding float.

        NOTE: This trick can also be performed with ctypes along the lines of:
        ``c_float.from_buffer(c_uint32(numeric))``, but in this driver the library
        version is used.

        Args:
            numeric: The integer that represents a float.

        Returns:
            The float value.
        """
        c_out_float = c_float()
        ret = self._eclib.BL_ConvertNumericIntoSingle(numeric, byref(c_out_float))
        self.check_eclib_return_code(ret)
        return c_out_float.value

    def check_eclib_return_code(self, error_code: int) -> None:
        """Check a ECLib return code and raise the appropriate exception."""
        if error_code < 0:
            message = self.get_error_message(error_code)
            raise ECLibError(message, error_code)


# Auxillary classes
class KBIOData:
    """Class used to represent data obtained with a get_data call.

    The data can be obtained as lists of floats through attributes on this class.
    The time is always available through the 'time' attribute. The attribute names for
    the rest of the data are the same as their names as listed in the field_names
    attribute. E.g:

    * kbio_data.Ewe
    * kbio_data.I

    Provided that numpy is installed, the data can also be obtained as numpy
    arrays by appending '_numpy' to the attribute name. E.g:

    * kbio_data.Ewe_numpy
    * kbio_data.I_numpy
    """

    def __init__(
            self, c_databuffer: Array[c_uint32], c_data_infos: DataInfos,
            c_current_values: CurrentValues, instrument: GeneralPotentiostat
    ) -> None:
        """Initialize the KBIOData object.

        Args:
            c_databuffer: ctypes array of :py:class:`ctypes.c_uint32` used as the data
                buffer.
            c_data_infos: :class:`.DataInfos` structure.
            c_current_values: :class:`.CurrentValues` structure.
            instrument: Instrument instance of :class:`.GeneralPotentiostat`.

        Raises:
            ECLibCustomException: Where the error codes indicate the following:

            * -20000 means that the technique has no entry in
              :data:`TECHNIQUE_IDENTIFIERS_TO_CLASS`.
            * -20001 means that the technique class has no ``data_fields`` class
              variable.
            * -20002 means that the ``data_fields`` class variables of the technique
              does not contain the right information.
        """
        technique_id = c_data_infos.TechniqueID
        self.technique: str = TECHNIQUE_IDENTIFIERS[technique_id]

        # Technique 0 means no data, get_data checks for this, so just return
        if technique_id == 0:
            return

        # Extract the process index, used to seperate data field classes for
        # techniques that support that
        self.process = c_data_infos.ProcessIndex
        # Init the data_fields
        self.data_fields: List[DataField] = self._init_data_fields(instrument)

        # Extract the number of points and columns
        self.number_of_points = c_data_infos.NbRows
        self.number_of_columns = c_data_infos.NbCols
        self.starttime = c_data_infos.StartTime

        # Make lists for the data in properties named after the field_names
        for data_field in self.data_fields:
            setattr(self, data_field.name, [])

        # Process data fields either have `t`  or `t_high` and `t_low`
        if not hasattr(self, 't'):
            self.time: List[float] = []  # TODO: add time to data fields

        # Parse the data
        self._parse_data(c_databuffer, c_current_values.TimeBase, instrument)

    def _init_data_fields(self, instrument: GeneralPotentiostat) -> List[DataField]:
        """Initialize the data fields property."""
        # Get the data_fields class variable from the corresponding technique class
        if self.technique not in TECHNIQUE_IDENTIFIERS_TO_CLASS:
            message = (
                f"The technique \'{self.technique}\' has no entry in "
                f"TECHNIQUE_IDENTIFIERS_TO_CLASS. The is required to be able to "
                f"interpret the data."
            )

            raise ECLibCustomException(message, -20000)
        technique_class: Type[Technique]
        technique_class = TECHNIQUE_IDENTIFIERS_TO_CLASS[self.technique]

        if 'data_fields' not in technique_class.__dict__:
            message = (
                f"The technique class {technique_class.__name__} does not "
                f"define a \'data_fields\' class variable, which is required "
                f"for data interpretation."
            )
            raise ECLibCustomException(message, -20001)

        data_fields_complete: Dict[str, List[DataField]]
        data_fields_complete = technique_class.data_fields[self.process]
        data_fields_out: List[DataField]

        try:
            data_fields_out = data_fields_complete['common']
        except KeyError:
            try:
                data_fields_out = data_fields_complete[instrument.series]
            except KeyError as exc:
                message = (
                    f"Unable to get data_fields from technique class. The "
                    f"data_fields class variable in the technique class must "
                    f"have either a \'common\' or a \'{instrument.series}\' "
                    f"key."
                )
                raise ECLibCustomException(message, -20002) from exc

        return data_fields_out

    def _parse_data(
            self, c_databuffer: Array[c_uint32], timebase: int,
            instrument: GeneralPotentiostat) -> None:
        """Parse the data and write to c_databuffer.

        Args:
            c_databuffer: ctypes array of :py:class:`ctypes.c_uint32` used as the data
                buffer.
            timebase: The timebase for the time calculation in microseconds.
            instrument: Instrument instance of :class:`.GeneralPotentiostat`.
        """
        # The data is written as one long array of points with a certain
        # amount of colums. Get the index of the first item of each point by
        # getting the range from 0 til n_point * n_columns in jumps of
        # n_columns
        for index in range(0, self.number_of_points * self.number_of_columns,
                           self.number_of_columns):
            # If there is a special time variable
            if hasattr(self, 'time'):
                # Calculate the time
                t_high = c_databuffer[index]
                t_low = c_databuffer[index + 1]
                # NOTE: The documentation uses a bitshift operation for the:
                # ((t_high * 2 ** 32) + tlow) operation as
                # ((thigh << 32) + tlow), but I could not be bothered to
                # figure out exactly how a bitshift operation is defined for
                # an int class that can change internal representation, so I
                # just do the explicit multiplication
                self.time.append(
                    self.starttime + timebase * ((t_high * 2 ** 32) + t_low)
                )
                # Only offset reading the rest of the variables if there is a
                # special conversion time variable
                time_variable_offset = 2
            else:
                time_variable_offset = 0

            # Get remaining fields as defined in data fields
            value: Union[int, float]
            for field_number, data_field in enumerate(self.data_fields):
                value = c_databuffer[index + time_variable_offset + field_number]
                # If the type is supposed to be float, convert the numeric to
                # float using the convinience function
                if data_field.type is c_float:
                    value = instrument.convert_numeric_into_single(value)

                # Append the field value to the appropriate list in a property
                getattr(self, data_field.name).append(value)

        # Check that the rest of the buffer is blank
        for index in range(self.number_of_points * self.number_of_columns,
                           1000):
            assert c_databuffer[index] == 0

    def __getattr__(self, key: str) -> np.ndarray:
        """Return generated numpy arrays for the data instead of lists, if requested.

        Requested property must be in the form field_name + '_numpy'.

        Args:
            key: data field to return as numpy array.

        Returns:
            numpy array of requested data field.

        Raises:
            RuntimeError: Unable to import numpy.
            ValueError: Unable to infer appropriate numpy dtype for data.
            AttributeError: Key is not in data_fields.
        """
        # __getattr__ is only called after the check of whether the key is in the
        # instance dict, therefore it is ok to raise attribute error at this points if
        # the key does not have the special form we expect
        if key.endswith('_numpy'):
            if not GOT_NUMPY:
                message = (
                    'The numpy module is required to get the data '
                    'as numpy arrays.'
                )
                raise RuntimeError(message)

            # Get the requested field name e.g. Ewe
            requested_field = key.split('_numpy')[0]

            dtype: Any = None
            if requested_field == 'time':
                dtype = float
            if requested_field in self.data_field_names:
                # Determine the numpy type to convert to
                for field in self.data_fields:
                    if field.name == requested_field:
                        correct_field = field
                        break
                if correct_field.type is c_float:
                    dtype = float
                elif correct_field.type is c_uint32:
                    dtype = int

                if dtype is None:
                    message = (
                        f"Unable to infer the numpy data type for "
                        f"requested field: {requested_field}"
                    )
                    raise ValueError(message)

                # Convert the data and return the numpy array
                return np.array(
                    getattr(self, requested_field), dtype=dtype
                )

        message = f"{self.__class__} object has no attribute {key}"
        raise AttributeError(message)

    @property
    def data_field_names(self) -> List[str]:
        """Return a list of data fields names (besides time)."""
        return [data_field.name for data_field in self.data_fields]


class Technique:
    """Base class for techniques.

    All specific technique classes inherits from this class.

    A specific technique that inherits from this class **must** define a **data_fields**
    class variable. It describes what the form of the data is that the technique can
    receive. The variable should be a dict or list of dicts:

    * Some techniques, like :class:`.OCV`, have different data fields depending
      on the series of the instrument. In these cases the dict must contain
      both a 'vmp3' and a 'sp300' key.
    * For cases where the instrument class distinction mentioned above does not
      exist, like e.g. for :class:`.CV`, one can simply define a 'common' key.
    * In most cases the first field of the returned data is a specially formatted
      ``time`` field, which must not be listed directly.
    * Some techniques, like e.g. :class:`.PEIS` returns data for two different
      processes. In this case **data_fields** is a list of dicts for each process. See
      the implementation of :class:`.PEIS` for details.

    All of the entries in the dict must point to an list of :class:`.DataField` named
    tuples, where the two arguments are the name and the C type of the field, usually
    :py:class:`c_float <ctypes.c_float>` or :py:class:`c_uint32 <ctypes.c_uint32>`. The
    list of fields must be in the order the data fields is specified in the
    :ref:`specification <specification>`.

    Attributes:
        technique_filename: A string of the technique filename.
        args: Tuple containing the Python version of the parameters, see
            :meth:`.__init__` for details.
        c_args: c-types array of :class:`.TECCParam`.
    """

    data_fields: List[Dict[str, List[DataField]]]

    def __init__(self, args: tuple, technique_filename: str) -> None:
        """Initialize a technique.

        Args:
            args: Tuple of technique arguments as TechniqueArgument instances.
            technique_filename: The name of the technique filename.
                .. note:: This must be the vmp3 series version, i.e. name.ecc
                  NOT name4.ecc, the replacement of technique file names is taken care
                  of in load technique
        """
        self.args = args
        self.technique_filename = technique_filename
        # The arguments must be converted to an array of TECCParam
        self._c_args: Array[TECCParam]

    def c_args(self, instrument: GeneralPotentiostat) -> Array[TECCParam]:
        """Return the arguments struct.

        Args:
            instrument: Instrument instance of :class:`.GeneralPotentiostat`.

        Returns:
            A ctypes array of :class:`.TECCParam`

        Raises:
            ECLibCustomException: Where the error codes indicate the following:

                * -10000 means that a :class:`.TechniqueArgument` failed the 'in' test
                * -10001 means that a :class:`.TechniqueArgument` failed the '>=' test
                * -10002 means that a :class:`.TechniqueArgument` failed the
                  'in_float_range' test
                * -10003 means that a :class:`.TechniqueArgument` had an unrecognized
                  argument check
                * -10010 means that it was not possible to find a conversion function
                  for the defined type
                * -10011 means that the value cannot be converted with the conversion
                  function
        """
        if not hasattr(self, '_c_args'):
            self._init_c_args(instrument)
        return self._c_args

    def _init_c_args(self, instrument: GeneralPotentiostat) -> None:
        """Initialize the arguments structure.

        Args:
            instrument: Instrument instance of :class:`.GeneralPotentiostat`.
        """
        # If it is a technique that has multistep arguments, get the number of steps
        step_number = 1
        for arg in self.args:
            if arg.label == 'Step_number':
                step_number = arg.value + 1

        param: TECCParam
        constructed_args = []
        for arg in self.args:
            # Bounds check the argument
            self._check_arg(arg)

            # When type is dict, it means that type is a int_code -> value_str
            # dict, that should be used to translate the str to an int by
            # reversing it be able to look up codes from strs and replace
            # value
            if isinstance(arg.type, dict):
                value = reverse_dict(arg.type)[arg.value]
                param = TECCParam()
                instrument.define_integer_parameter(arg.label, value, 0, param)
                constructed_args.append(param)
                continue

            # Get the appropriate conversion function, to populate the EccParam
            stripped_type = arg.type.strip('[]')
            try:
                # Get the conversion method from the instrument instance, this
                # is named something like defined_bool_parameter
                conversion_function = getattr(
                    instrument, f"define_{stripped_type}_parameter"
                )
            except AttributeError as exc:
                message = (
                    f"Unable to find parameter definitions function for "
                    f"type: {stripped_type}"
                )
                raise ECLibCustomException(message, -10010) from exc

            # If the parameter is not a multistep paramter, put the value in a
            # list so we can iterate over it
            if arg.type.startswith('[') and arg.type.endswith(']'):
                values = arg.value
            else:
                values = [arg.value]

            # Iterate over all the steps for the parameter (for most will just
            # be 1)
            for index in range(min(step_number, len(values))):
                param = TECCParam()
                try:
                    conversion_function(arg.label, values[index], index, param)
                except ECLibError as exc:
                    message = (
                        f"{values[index]} is not a valid value for conversion "
                        f"to type {stripped_type} for argument \'{arg.label}\'"
                    )
                    raise ECLibCustomException(message, -10011) from exc
                constructed_args.append(param)

        self._c_args = (TECCParam * len(constructed_args))()
        for index, param in enumerate(constructed_args):
            self._c_args[index] = param

    @staticmethod
    def _check_arg(arg: TechniqueArgument) -> None:
        """Perform bounds check on a single argument."""
        if arg.check is None:
            return

        # If the type is not a dict (used for constants) and indicates an array
        if (
            not isinstance(arg.type, dict)
            and arg.type.startswith('[')
            and arg.type.endswith(']')
        ):
            values = arg.value
        else:
            values = [arg.value]

        # Check arguments with a list of accepted values
        if arg.check == 'in':
            for value in values:
                if value not in arg.check_argument:
                    message = (
                        f"{value} is not among the valid values for "
                        f"\'{arg.label}\'. Valid values are: "
                        f"{arg.check_argument}"
                    )
                    raise ECLibCustomException(message, -10000)
            return

        # Perform bounds check, if any
        if arg.check == '>=':
            for value in values:
                if not value >= arg.check_argument:
                    message = (
                        f"Value {value} for parameter \'{arg.label}\' failed "
                        f"check >={arg.check_argument}"
                    )
                    raise ECLibCustomException(message, -10001)
            return

        # Perform in two parameter range check: A < value < B
        if arg.check == 'in_float_range':
            for value in values:
                if not arg.check_argument[0] <= value <= arg.check_argument[1]:
                    message = (
                        f"Value {value} for parameter \'{arg.label}\' failed "
                        f"check between {arg.check_argument[0]} and "
                        f"{arg.check_argument[1]}"
                    )
                    raise ECLibCustomException(message, -10002)
            return

        message = f"Unknown technique parameter check: {arg.check}"
        raise ECLibCustomException(message, -10003)


# Section 7.2 in the specification
class OCV(Technique):
    """Open Circuit Voltage (OCV) technique class.

    The OCV technique returns data on fields (in order):

    * time (float)
    * Ewe (float)
    * Ece (float) (only VMP3 series hardware)
    """

    #: Data fields definition
    data_fields = [{
        'vmp3': [DataField('Ewe', c_float), DataField('Ece', c_float)],
        'sp300': [DataField('Ewe', c_float)],
    }]

    def __init__(
        self,
        duration: float = 10.0,
        record_every_dE: float = 0.01,
        record_every_dt: float = 0.1,
        E_range: str = 'KBIO_ERANGE_AUTO'
    ) -> None:
        """Initialize the OCV technique.

        Args:
            rest_time_t: The amount of time to rest (s).
            record_every_dE: Record every dE (V).
            record_every_dt: Record evergy dt (s).
            E_range: A string describing the E range to use, see the :data:`.E_RANGES`
                variable for possible values.
        """
        args = (
            TechniqueArgument('Rest_time_T', 'single', duration, '>=', 0),
            TechniqueArgument('Record_every_dE', 'single', record_every_dE, '>=', 0),
            TechniqueArgument('Record_every_dT', 'single', record_every_dt, '>=', 0),
            TechniqueArgument('E_Range', E_RANGES, E_range, 'in',
                              list(E_RANGES.values())),
        )
        super().__init__(args, 'ocv.ecc')


# Section 7.3 in the specification
class CV(Technique):
    """Cyclic Voltammetry (CV) technique class.

    The CV technique returns data on fields (in order):

    * time (float)
    * Ec (float)
    * I (float)
    * Ewe (float)
    * cycle (int)
    """

    # :Data fields definition
    data_fields = [{
        'common': [
            DataField('Ec', c_float),
            DataField('I', c_float),
            DataField('Ewe', c_float),
            DataField('cycle', c_uint32),
        ]
    }]

    def __init__(
        self,
        voltage: Sequence = (0.0, 1.0, -1.0, 0.0),
        scan_rate: float = 10.0,  # mV/s
        vs_initial: bool = True,
        n_cycles: int = 0,
        record_every_dE: float = 0.01,
        average_I_over_dE: bool = True,
        begin_measuring_I: float = 0.5,
        end_measuring_I: float = 1.0,
        I_range: str = 'KBIO_IRANGE_AUTO',
        E_range: str = 'KBIO_ERANGE_2_5',
        bandwidth: str = 'KBIO_BW_5'
    ) -> None:
        r"""Initialize the CV technique::

         E_we
         ^
         |       E_1
         |       /\
         |      /  \
         |     /    \      E_f
         | E_i/      \    /
         |            \  /
         |             \/
         |             E_2
         +----------------------> t

        Args:
            voltage: Sequence of 4 floats (Ei, E1, E2, Ef) indicating the
                voltage steps (V), see diagram above.
            scan_rate: Scan rate in mV/s.
            vs_initial: Whether the current step is vs. the initial one.
            n_cycles: The number of cycles.
            record_every_dE: Record every dE (V).
            average_I_over_dE: Whether averaging should be performed over dE.
            begin_measuring_I: Begin step accumulation. 1 is 100% of step.
            end_measuring_I: End step accumulation. 1 is 100% of step.
            I_Range: A string describing the I range, see the :data:`.I_RANGES` variable
                for possible values.
            E_range: A string describing the E range to use, see the :data:`.E_RANGES`
                variable for possible values.
            bandwidth: A string describing the bandwidth setting, see the
                :data:`.BANDWIDTHS` variable for possible values.

        Raises:
            ValueError: If voltage_step is not of length 4.
        """

        if len(voltage) != 4:
            message = (
                f"Input \'voltage\' must be of length 4, not {len(voltage)}"
            )
            raise ValueError(message)

        voltage_list = list(voltage)
        voltage_list.insert(3, voltage[0])

        args = (
            TechniqueArgument(
                'vs_initial', '[bool]', (vs_initial,)*5, 'in', [True, False]),
            TechniqueArgument(
                'Voltage_step', '[single]', voltage_list, None, None),
            TechniqueArgument(
                'Scan_Rate', '[single]', (scan_rate,)*5, '>=', 0.0),
            TechniqueArgument('Scan_number', 'integer', 2, None, None),
            TechniqueArgument('Record_every_dE', 'single',
                              record_every_dE, '>=', 0.0),
            TechniqueArgument(
                'Average_over_dE', 'bool', average_I_over_dE, 'in', [
                    True, False]
            ),
            TechniqueArgument('N_Cycles', 'integer', n_cycles, '>=', 0),
            TechniqueArgument(
                'Begin_measuring_I',
                'single',
                begin_measuring_I,
                'in_float_range',
                (0.0, 1.0),
            ),
            TechniqueArgument(
                'End_measuring_I',
                'single',
                end_measuring_I,
                'in_float_range',
                (0.0, 1.0),
            ),
            TechniqueArgument('I_Range', I_RANGES, I_range,
                              'in', list(I_RANGES.values())),
            TechniqueArgument('E_Range', E_RANGES, E_range,
                              'in', list(E_RANGES.values())),
            TechniqueArgument(
                'Bandwidth', BANDWIDTHS, bandwidth, 'in', list(
                    BANDWIDTHS.values())
            ),
        )
        super().__init__(args, 'cv.ecc')


# Section 7.4 in the specification
class CVA(Technique):
    """Cyclic Voltammetry Advanced (CVA) technique class.

    The CVA technique returns data on fields (in order):

    * time (float)
    * Ec (float)
    * I (float)
    * Ewe (float)
    * cycle (int)
    """

    # :Data fields definition
    data_fields = [{
        'common': [
            DataField('Ec', c_float),
            DataField('I', c_float),
            DataField('Ewe', c_float),
            DataField('cycle', c_uint32),
        ]
    }]

    def __init__(
        self,
        voltage: Sequence = (0.0, 1.0, -1.0, 0.0),
        scan_rate: float = 10.0,  # mV/s,
        vs_initial: bool = True,
        n_cycles: int = 0,
        duration_step1: float = 10.0,
        duration_step2: float = 10.0,
        record_every_dE: float = 0.01,
        record_every_dt: float = 0.1,
        record_every_dI: float = 0.001,
        average_over_dE: float = True,
        begin_measuring_I: float = 0.5,
        end_measuring_I: float = 1.0,
        trigger_on_off: bool = False,
        I_range: str = 'KBIO_IRANGE_AUTO',
        E_range: str = 'KBIO_ERANGE_2_5',
        bandwidth: str = 'KBIO_BW_5'
    ) -> None:
        r"""Initialize the CVA technique::

         E_we
         ^
         |    E_1 _______
         |       /  t_1  \
         |      /         \
         |     /           \          E_f______________
         | E_i/             \           /             |_______E_i
         |                   \         /              |
         |                    \__t_2__/               |
         |                   E_2                      |
         +--------------------------------------------+---------> t
                                                      |
                                                  trigger

        Args:
            voltage : Sequence of 4 floats (Ei, E1, E2, Ef) indicating the voltage
                steps (V), see diagram above.
            scan_rate: Scan rate in mV/s.
            vs_initial: Whether the current step is vs. the initial one.
            n_cycles: The number of cycles.
            duration_step1: Duration to hold voltage at step 1 (s).
            duration_step2: Duration to hold voltage at step 2 (s).
            record_every_dE: Record every dE (V).
            record_every_dt: Record every dt (s).
            record_every_dI: Record every dI (A).
            average_over_dE: Whether averaging should be performed over dE.
            begin_measuring_I: Begin step accumulation, 1 is 100% of step.
            end_measuring_I: Begin step accumulation, 1 is 100% of step.
            trig_on_off: A boolean indicating whether to use the trigger.
            I_Range: A string describing the I range, see the :data:`.I_RANGES` variable
                for possible values.
            E_range: A string describing the E range to use, see the :data:`.E_RANGES`
                variable for possible values.
            bandwidth: A string describing the bandwidth setting, see the
                :data:`.BANDWIDTHS` variable for possible values.

        Raises:
            ValueError: If voltage_step is not of length 4.
        """

        if len(voltage) != 4:
            message = (
                f"Input \'voltage\' must be of length 4, not "
                f"{len(voltage)}"
            )
            raise ValueError(message)

        args = (
            TechniqueArgument(
                'vs_initial_scan', '[bool]', (vs_initial,) *
                4, 'in', [True, False]
            ),
            TechniqueArgument('Voltage_scan', '[single]', voltage, None, None),
            TechniqueArgument(
                'Scan_Rate', '[single]', (scan_rate,)*4, '>=', 0.0),
            TechniqueArgument('Scan_number', 'integer', 2, None, None),
            TechniqueArgument('Record_every_dE', 'single',
                              record_every_dE, '>=', 0.0),
            TechniqueArgument(
                'Average_over_dE', 'bool', average_over_dE, 'in', [True, False]
            ),
            TechniqueArgument('N_Cycles', 'integer', n_cycles, '>=', 0),
            TechniqueArgument(
                'Begin_measuring_I',
                'single',
                begin_measuring_I,
                'in_float_range',
                (0.0, 1.0),
            ),
            TechniqueArgument(
                'End_measuring_I',
                'single',
                end_measuring_I,
                'in_float_range',
                (0.0, 1.0),
            ),
            TechniqueArgument(
                'vs_initial_step', '[bool]', (vs_initial,) *
                2, 'in', [True, False]
            ),
            TechniqueArgument(
                'Voltage_step', '[single]', (voltage[1], voltage[2]), None,
                None),
            TechniqueArgument(
                'Duration_step', '[single]', (duration_step1, duration_step2),
                None, None),
            TechniqueArgument('Step_number', 'integer', 1, None, None),
            TechniqueArgument('Record_every_dT', 'single',
                              record_every_dt, '>=', 0.0),
            TechniqueArgument('Record_every_dI', 'single',
                              record_every_dI, '>=', 0.0),
            TechniqueArgument('Trig_on_off', 'bool',
                              trigger_on_off, 'in', [True, False]),
            TechniqueArgument('I_Range', I_RANGES, I_range,
                              'in', list(I_RANGES.values())),
            TechniqueArgument('E_Range', E_RANGES, E_range,
                              'in', list(E_RANGES.values())),
            TechniqueArgument(
                'Bandwidth', BANDWIDTHS, bandwidth, 'in', list(
                    BANDWIDTHS.values())
            ),
        )
        super().__init__(args, 'biovscan.ecc')


# Section 7.5 in the specification
class CP(Technique):
    """Chrono-Potentiometry (CP) technique class.

    The CP technique returns data on fields (in order):

    * time (float)
    * Ewe (float)
    * I (float)
    * cycle (int)
    """

    #: Data fields definition
    data_fields = [{
        'common': [
            DataField('Ewe', c_float),
            DataField('I', c_float),
            DataField('cycle', c_uint32),
        ]
    }]

    def __init__(
        self,
        current_step: Sequence = (5e-3,),
        duration_step: Sequence = (10.0,),
        vs_initial: bool = False,
        n_cycles: int = 0,
        record_every_dt: float = 0.1,
        record_every_dE: float = 0.001,
        I_range: str = 'KBIO_IRANGE_100uA',
        E_range: str = 'KBIO_ERANGE_2_5',
        bandwidth: str = 'KBIO_BW_5'
    ) -> None:
        """Initialize the CP technique.

        NOTE: The current_step and duration_step must be a sequence with
        the same length.

        Args:
            current_step: Sequence of floats indicating the current steps (A). See
                NOTE above.
            duration_step: Sequence of floats indicating the duration of each
                step (s). See NOTE above.
            vs_initial: Whether the current steps is vs. the initial one.
            n_cycles: The number of times the technique is REPEATED. The default is 0
                which means that the technique will be run once.
            record_every_dt: Record every dt (s).
            record_every_dE: Record every dE (V).
            I_Range: A string describing the I range, see the :data:`.I_RANGES` variable
                for possible values.
            E_range: A string describing the E range to use, see the :data:`.E_RANGES`
                variable for possible values.
            bandwidth: A string describing the bandwidth setting, see the
                :data:`.BANDWIDTHS` variable for possible values.

        Raises:
            ValueError: On bad lengths for the list arguments
        """
        if not len(current_step) == len(duration_step):
            message = (
                'The length of current_step and duration_step must be the same.'
            )
            raise ValueError(message)

        args = (
            TechniqueArgument(
                'Current_step', '[single]', current_step, None, None),
            TechniqueArgument(
                'vs_initial', '[bool]', (vs_initial,)*len(current_step), 'in',
                [True, False]
            ),
            TechniqueArgument(
                'Duration_step', '[single]', duration_step, '>=', 0),
            TechniqueArgument(
                'Step_number', 'integer', len(current_step)-1, 'in', list(range(99))),
            TechniqueArgument('Record_every_dT', 'single',
                              record_every_dt, '>=', 0),
            TechniqueArgument('Record_every_dE', 'single',
                              record_every_dE, '>=', 0),
            TechniqueArgument('N_Cycles', 'integer', n_cycles, '>=', 0),
            TechniqueArgument('I_Range', I_RANGES, I_range,
                              'in', list(I_RANGES.values())),
            TechniqueArgument('E_Range', E_RANGES, E_range,
                              'in', list(E_RANGES.values())),
            TechniqueArgument(
                'Bandwidth', BANDWIDTHS, bandwidth, 'in', list(
                    BANDWIDTHS.values())
            ),
        )
        super().__init__(args, 'cp.ecc')


# Section 7.6 in the specification
class CA(Technique):
    """Chrono-Amperometry (CA) technique class.

    The CA technique returns data on fields (in order):

    * time (float)
    * Ewe (float)
    * I (float)
    * cycle (int)
    """

    # :Data fields definition
    data_fields = [{
        'common': [
            DataField('Ewe', c_float),
            DataField('I', c_float),
            DataField('cycle', c_uint32),
        ]
    }]

    def __init__(
        self,
        voltage_step=(0.5,),
        duration_step=(10.0,),
        vs_initial=False,
        n_cycles=0,
        record_every_dt=0.1,
        record_every_dI=5e-6,
        I_range='KBIO_IRANGE_AUTO',
        E_range='KBIO_ERANGE_2_5',
        bandwidth='KBIO_BW_5'
    ):
        """Initialize the CA technique.

        NOTE: The voltage_step and duration_step must be a sequence with
        the same length.

        Args:
            voltage_step (list): Sequence of floats indicating the
                voltage steps (V). See NOTE above.
            duration_step (list): Sequence of floats indicating the
                duration of each step (s). See NOTE above.
            vs_initial (boolean): Whether the current step is vs. the initial
                one.
            n_cycles: The number of times the technique is REPEATED.
                NOTE: This means that the default value is 0 which means that
                the technique will be run once.
            record_every_dt: Record every dt (s)
            record_every_dI: Record every dI (A)
            I_Range (str): A string describing the I range, see the
                :data:`I_RANGES` module variable for possible values
            E_range (str): A string describing the E range to use, see the
                :data:`E_RANGES` module variable for possible values
            Bandwidth (str): A string describing the bandwidth setting, see the
                :data:`BANDWIDTHS` module variable for possible values

        Raises:
            ValueError: On bad lengths for the list arguments
        """
        if not len(voltage_step) == len(duration_step):
            message = (
                'The length of voltage_step and '
                'duration_step must be the same'
            )
            raise ValueError(message)

        vs_initial = (vs_initial,)*len(voltage_step)

        args = (
            TechniqueArgument(
                'Voltage_step', '[single]', voltage_step, None, None),
            TechniqueArgument(
                'vs_initial', '[bool]', vs_initial, 'in', [True, False]),
            TechniqueArgument(
                'Duration_step', '[single]', duration_step, '>=', 0.0),
            TechniqueArgument(
                'Step_number', 'integer', len(
                    voltage_step)-1, 'in', list(range(99))
            ),
            TechniqueArgument('Record_every_dT', 'single',
                              record_every_dt, '>=', 0.0),
            TechniqueArgument('Record_every_dI', 'single',
                              record_every_dI, '>=', 0.0),
            TechniqueArgument('N_Cycles', 'integer', n_cycles, '>=', 0),
            TechniqueArgument('I_Range', I_RANGES, I_range,
                              'in', list(I_RANGES.values())),
            TechniqueArgument('E_Range', E_RANGES, E_range,
                              'in', list(E_RANGES.values())),
            TechniqueArgument(
                'Bandwidth', BANDWIDTHS, bandwidth, 'in', list(
                    BANDWIDTHS.values())
            ),
        )
        super().__init__(args, 'ca.ecc')


# Section 7.9 in the specification
class CPOWER(Technique):
    """Constant Power (CPOWER) technique class. Only available on VMP3 series.

    The CPOWER technique returns data on fields (in order):

    * time (float)
    * Ewe (float)
    * I (float)
    * P (float)
    * cycle (int)
    """

    # :Data fields definition
    data_fields = [{
        'common': [
            DataField('Ewe', c_float),
            DataField('I', c_float),
            DataField('P', c_float),
            DataField('cycle', c_uint32),
        ]
    }]

    def __init__(
        self,
        power_step=(0.1,),
        duration_step=(10.0,),
        vs_initial=False,
        n_cycles=0,
        record_every_dt=0.1,
        record_every_dE=0.1,
        I_range='KBIO_IRANGE_1mA',
        E_range='KBIO_ERANGE_AUTO',
        bandwidth='KBIO_BW_5'
    ):
        """Initialize the CPOWER technique.

        NOTE: The power_step and duration_step must be a list or
        tuple with the same length.

        Args:
            power_step (list): Sequence of floats indicating the
                power steps (W). See NOTE above.
            duration_step (list): Sequence of floats indicating the
                duration of each step (s). See NOTE above.
            vs_initial (boolean): Whether the current steps is vs. the initial
                one.
            n_cycles: The number of times the technique is REPEATED.
                NOTE: This means that the default value is 0 which means that
                the technique will be run once.
            record_every_dt: Record every dt (s)
            record_every_dE: Record every dE (V)
            I_Range (str): A string describing the I range, see the
                :data:`I_RANGES` module variable for possible values
            E_range (str): A string describing the E range to use, see the
                :data:`E_RANGES` module variable for possible values
            Bandwidth (str): A string describing the bandwidth setting, see the
                :data:`BANDWIDTHS` module variable for possible values

        Raises:
            ValueError: On bad lengths for the list arguments
        """
        if not len(power_step) == len(duration_step):
            message = (
                'The length of voltage_step, vs_initial and '
                'duration_step must be the same'
            )
            raise ValueError(message)

        vs_initial = (vs_initial,)*len(power_step)

        args = (
            TechniqueArgument(
                'Power_step', '[single]', power_step, None, None),
            TechniqueArgument(
                'vs_initial', '[bool]', vs_initial, 'in', [True, False]),
            TechniqueArgument(
                'Duration_step', '[single]', duration_step, '>=', 0.0),
            TechniqueArgument(
                'Step_number', 'integer', len(
                    power_step)-1, 'in', list(range(99))
            ),
            TechniqueArgument('Record_every_dT', 'single',
                              record_every_dt, '>=', 0.0),
            TechniqueArgument('Record_every_dE', 'single',
                              record_every_dE, '>=', 0.0),
            TechniqueArgument('N_Cycles', 'integer', n_cycles, '>=', 0),
            TechniqueArgument('I_Range', I_RANGES, I_range,
                              'in', list(I_RANGES.values())),
            TechniqueArgument('E_Range', E_RANGES, E_range,
                              'in', list(E_RANGES.values())),
            TechniqueArgument(
                'Bandwidth', BANDWIDTHS, bandwidth, 'in', list(
                    BANDWIDTHS.values())
            ),
        )
        super().__init__(args, 'pow.ecc')


# Section 7.11 in the specification
class PEIS(Technique):
    """Potentio Electrochemical Impedance Spectroscopy (PEIS) technique class.

    The PEIS technique returns data with a different set of fields depending
    on which process steps it is in. If it is in process step 0 it returns
    data on the following fields (in order):

    * time (float)
    * Ewe (float)
    * I (float)

    If it is in process 1 it returns data on the following fields:

    * freq (float)
    * abs_Ewe (float)
    * abs_I (float)
    * Phase_Zwe (float)
    * Ewe (float)
    * I (float)
    * abs_Ece (float)
    * abs_Ice (float)
    * Phase_Zce (float)
    * Ece (float)
    * t (float)
    * Irange (float) (VMP series only)

    Which process it is in, can be checked with the ``process`` property on
    the :class:`.KBIOData` object.
    """

    # :Data fields definition
    process0_data_fields = {
        'common': [
            DataField('Ewe', c_float),
            DataField('I', c_float),
        ],
    }

    process1_data_fields = {
        'vmp3': [
            DataField('freq', c_float),
            DataField('abs_Ewe', c_float),
            DataField('abs_I', c_float),
            DataField('Phase_Zwe', c_float),
            DataField('Ewe', c_float),
            DataField('I', c_float),
            DataField('Blank0', c_float),
            DataField('abs_Ece', c_float),
            DataField('abs_Ice', c_float),
            DataField('Phase_Zce', c_float),
            DataField('Ece', c_float),
            DataField('Blank1', c_float),
            DataField('Blank2', c_float),
            DataField('t', c_float),
            DataField('Irange', c_uint32),
        ],
        'sp300': [
            DataField('freq', c_float),
            DataField('abs_Ewe', c_float),
            DataField('abs_I', c_float),
            DataField('Phase_Zwe', c_float),
            DataField('Ewe', c_float),
            DataField('I', c_float),
            DataField('Blank0', c_float),
            DataField('abs_Ece', c_float),
            DataField('abs_Ice', c_float),
            DataField('Phase_Zce', c_float),
            DataField('Ece', c_float),
            DataField('Blank1', c_float),
            DataField('Blank2', c_float),
            DataField('t', c_float),
        ],
    }

    data_fields = [process0_data_fields, process1_data_fields]

    def __init__(
        self,
        initial_voltage_step=0,
        duration_step=5.0,
        vs_initial=False,
        initial_frequency=100.0e3,
        final_frequency=1.0,
        logarithmic_spacing=True,
        amplitude_voltage=0.01,
        frequency_number=51,
        average_n_times=1,
        wait_for_steady=1.0,
        drift_correction=False,
        record_every_dt=0.1,
        record_every_dI=0.1,
        I_range='KBIO_IRANGE_AUTO',
        E_range='KBIO_ERANGE_2_5',
        bandwidth='KBIO_BW_5'
    ):
        """Initialize the PEIS technique.

        Args:
            initial_voltage_step: Before EIS, initial voltage step (V)
            duration_step: Duration to hold voltage before EIS (s)
            vs_initial: Whether the voltage step is vs. the initial one
            initial_frequency: The initial frequency (Hz)
            final_frequency: The final frequency (Hz)
            logarithmic_spacing: Logarithmic/linear frequency spacing
                (True for logarithmic points spacing)
            amplitude_voltage: Amplitude of sinus (V)
            frequency_number: The number of frequencies
            average_n_times: The number of repeat times used for
                frequency averaging
            wait_for_steady: The number of periods to wait before each
                frequency
            drift_correction: Non-stationary drift correction
            record_every_dt: Record every dt (s)
            record_every_dI: Record every dI (A)
            I_Range (str): A string describing the I range, see the
                :data:`I_RANGES` module variable for possible values
            E_range (str): A string describing the E range to use, see the
                :data:`E_RANGES` module variable for possible values
            Bandwidth (str): A string describing the bandwidth setting, see the
                :data:`BANDWIDTHS` module variable for possible values
        """
        args = (
            TechniqueArgument('vs_initial', 'bool',
                              vs_initial, 'in', [True, False]),
            TechniqueArgument('vs_final', 'bool', vs_initial, None, None),
            TechniqueArgument(
                'Initial_Voltage_step', 'single', initial_voltage_step, None,
                None),
            TechniqueArgument(
                'Final_Voltage_step', 'single', initial_voltage_step, None,
                None),
            TechniqueArgument('Duration_step', 'single',
                              duration_step, None, None),
            TechniqueArgument('Step_number', 'integer', 0, None, None),
            TechniqueArgument('Record_every_dT', 'single',
                              record_every_dt, '>=', 0.0),
            TechniqueArgument('Record_every_dI', 'single',
                              record_every_dI, '>=', 0.0),
            TechniqueArgument('Final_frequency', 'single',
                              final_frequency, '>=', 0.0),
            TechniqueArgument(
                'Initial_frequency', 'single', initial_frequency, '>=', 0.0
            ),
            TechniqueArgument(
                'sweep', 'bool', not logarithmic_spacing, 'in', [True, False]),
            TechniqueArgument(
                'Amplitude_Voltage', 'single', amplitude_voltage, None, None
            ),
            TechniqueArgument('Frequency_number', 'integer',
                              frequency_number, '>=', 1),
            TechniqueArgument('Average_N_times', 'integer',
                              average_n_times, '>=', 1),
            TechniqueArgument('Correction', 'bool',
                              drift_correction, 'in', [True, False]),
            TechniqueArgument('Wait_for_steady', 'single',
                              wait_for_steady, '>=', 0.0),
            TechniqueArgument('I_Range', I_RANGES, I_range,
                              'in', list(I_RANGES.values())),
            TechniqueArgument('E_Range', E_RANGES, E_range,
                              'in', list(E_RANGES.values())),
            TechniqueArgument(
                'Bandwidth', BANDWIDTHS, bandwidth, 'in', list(
                    BANDWIDTHS.values())
            ),
        )
        super().__init__(args, 'peis.ecc')


# Section 7.12 in the specification
class SPEIS(Technique):
    """Staircase Potentio Electrochemical Impedance Spectroscopy (SPEIS) technique class.

    The SPEIS technique returns data with a different set of fields depending
    on which process steps it is in. If it is in process step 0 it returns
    data on the following fields (in order):

    * time (float)
    * Ewe (float)
    * I (float)
    * step (int)

    If it is in process 1 it returns data on the following fields:

    * freq (float)
    * abs_Ewe (float)
    * abs_I (float)
    * Phase_Zwe (float)
    * Ewe (float)
    * I (float)
    * abs_Ece (float)
    * abs_Ice (float)
    * Phase_Zce (float)
    * Ece (float)
    * t (float)
    * Irange (float) (VMP series only)
    * step (float)

    Which process it is in, can be checked with the ``process`` property on
    the :class:`.KBIOData` object.

    """

    # :Data fields definition
    _process0_data_fields = {
        'common': [
            DataField('Ewe', c_float),
            DataField('I', c_float),
            DataField('step', c_uint32),
        ],
    }

    _process1_data_fields = {
        'vmp3': [
            DataField('freq', c_float),
            DataField('abs_Ewe', c_float),
            DataField('abs_I', c_float),
            DataField('Phase_Zwe', c_float),
            DataField('Ewe', c_float),
            DataField('I', c_float),
            DataField('Blank0', c_float),
            DataField('abs_Ece', c_float),
            DataField('abs_Ice', c_float),
            DataField('Phase_Zce', c_float),
            DataField('Ece', c_float),
            DataField('Blank1', c_float),
            DataField('Blank2', c_float),
            DataField('t', c_float),
            DataField('Irange', c_uint32),
            DataField('step', c_uint32),
        ],
        'sp300': [
            DataField('freq', c_float),
            DataField('abs_Ewe', c_float),
            DataField('abs_I', c_float),
            DataField('Phase_Zwe', c_float),
            DataField('Ewe', c_float),
            DataField('I', c_float),
            DataField('Blank0', c_float),
            DataField('abs_Ece', c_float),
            DataField('abs_Ice', c_float),
            DataField('Phase_Zce', c_float),
            DataField('Ece', c_float),
            DataField('Blank1', c_float),
            DataField('Blank2', c_float),
            DataField('t', c_float),
            DataField('step', c_uint32),
        ],
    }

    data_fields = [_process0_data_fields, _process1_data_fields]

    def __init__(
        self,
        initial_voltage_step=0.0,
        duration_step=10.0,
        final_voltage_step=0.1,
        vs_initial=False,
        step_number=10,
        initial_frequency=100.0e3,
        final_frequency=1.0,
        logarithmic_spacing=True,
        amplitude_voltage=0.01,
        frequency_number=51,
        average_n_times=1,
        wait_for_steady=1.0,
        drift_correction=False,
        record_every_dt=0.1,
        record_every_dI=0.1,
        I_range='KBIO_IRANGE_AUTO',
        E_range='KBIO_ERANGE_2_5',
        bandwidth='KBIO_BW_5'
    ):
        """Initialize the SPEIS technique.

        Args:
            initial_voltage_step: Initial voltage step before EIS (V)
            duration_step: Duration of step (s)
            final_voltage_step: Final voltage step after EIS (V)
            vs_initial: Whether the voltage step is vs. the initial one
            step_number: The number of voltage steps
            initial_frequency: The initial frequency (Hz)
            final_frequency: The final frequency (Hz)
            logarithmic_spacing: Logarithmic/linear frequency spacing
                (True for logarithmic points spacing)
            amplitude_voltage: Amplitude of sinus (V)
            frequency_number: The number of frequencies
            average_n_times: The number of repeat times used for
                frequency averaging
            wait_for_steady: The number of periods to wait before each
                frequency
            drift_correction: Non-stationary drift correction
            record_every_dt: Record every dt (s)
            record_every_dI: Record every dI (A)
            I_Range (str): A string describing the I range, see the
                :data:`I_RANGES` module variable for possible values
            E_range (str): A string describing the E range to use, see the
                :data:`E_RANGES` module variable for possible values
            Bandwidth (str): A string describing the bandwidth setting, see the
                :data:`BANDWIDTHS` module variable for possible values

        """
        args = (
            TechniqueArgument('vs_initial', 'bool',
                              vs_initial, 'in', [True, False]),
            TechniqueArgument('vs_final', 'bool', vs_initial,
                              'in', [True, False]),
            TechniqueArgument(
                'Initial_Voltage_step', 'single', initial_voltage_step, None,
                None),
            TechniqueArgument(
                'Final_Voltage_step', 'single', final_voltage_step, None, None
            ),
            TechniqueArgument('Duration_step', 'single',
                              duration_step, None, None),
            TechniqueArgument('Step_number', 'integer',
                              step_number, 'in', list(range(99))),
            TechniqueArgument('Record_every_dT', 'single',
                              record_every_dt, '>=', 0.0),
            TechniqueArgument('Record_every_dI', 'single',
                              record_every_dI, '>=', 0.0),
            TechniqueArgument('Final_frequency', 'single',
                              final_frequency, '>=', 0.0),
            TechniqueArgument(
                'Initial_frequency', 'single', initial_frequency, '>=', 0.0
            ),
            TechniqueArgument(
                'sweep', 'bool', not logarithmic_spacing, 'in', [True, False]),
            TechniqueArgument(
                'Amplitude_Voltage', 'single', amplitude_voltage, None, None
            ),
            TechniqueArgument('Frequency_number', 'integer',
                              frequency_number, '>=', 1),
            TechniqueArgument('Average_N_times', 'integer',
                              average_n_times, '>=', 1),
            TechniqueArgument('Correction', 'bool',
                              drift_correction, 'in', [True, False]),
            TechniqueArgument('Wait_for_steady', 'single',
                              wait_for_steady, '>=', 0.0),
            TechniqueArgument('I_Range', I_RANGES, I_range,
                              'in', list(I_RANGES.values())),
            TechniqueArgument('E_Range', E_RANGES, E_range,
                              'in', list(E_RANGES.values())),
            TechniqueArgument(
                'Bandwidth', BANDWIDTHS, bandwidth, 'in', list(
                    BANDWIDTHS.values())
            ),
        )
        super().__init__(args, 'seisp.ecc')


# Section 7.11 in the specification
class GEIS(Technique):
    """Galvano Electrochemical Impedance Spectroscopy (GEIS) technique class.

    The GEIS technique returns data with a different set of fields depending
    on which process steps it is in. If it is in process step 0 it returns
    data on the following fields (in order):

    * time (float)
    * Ewe (float)
    * I (float)

    If it is in process 1 it returns data on the following fields:

    * freq (float)
    * abs_Ewe (float)
    * abs_I (float)
    * Phase_Zwe (float)
    * Ewe (float)
    * I (float)
    * abs_Ece (float)
    * abs_Ice (float)
    * Phase_Zce (float)
    * Ece (float)
    * t (float)
    * Irange (float) (VMP series only)

    Which process it is in, can be checked with the ``process`` property on
    the :class:`.KBIOData` object.

    """

    # :Data fields definition
    process0_data_fields = {
        'common': [
            DataField('Ewe', c_float),
            DataField('I', c_float),
        ],
    }

    process1_data_fields = {
        'vmp3': [
            DataField('freq', c_float),
            DataField('abs_Ewe', c_float),
            DataField('abs_I', c_float),
            DataField('Phase_Zwe', c_float),
            DataField('Ewe', c_float),
            DataField('I', c_float),
            DataField('Blank0', c_float),
            DataField('abs_Ece', c_float),
            DataField('abs_Ice', c_float),
            DataField('Phase_Zce', c_float),
            DataField('Ece', c_float),
            DataField('Blank1', c_float),
            DataField('Blank2', c_float),
            DataField('t', c_float),
            DataField('Irange', c_uint32),
        ],
        'sp300': [
            DataField('freq', c_float),
            DataField('abs_Ewe', c_float),
            DataField('abs_I', c_float),
            DataField('Phase_Zwe', c_float),
            DataField('Ewe', c_float),
            DataField('I', c_float),
            DataField('Blank0', c_float),
            DataField('abs_Ece', c_float),
            DataField('abs_Ice', c_float),
            DataField('Phase_Zce', c_float),
            DataField('Ece', c_float),
            DataField('Blank1', c_float),
            DataField('Blank2', c_float),
            DataField('t', c_float),
        ],
    }

    def __init__(
        self,
        initial_current_step=0.0,
        duration_step=5.0,
        vs_initial=False,
        initial_frequency=100.0e3,
        final_frequency=1.0,
        logarithmic_spacing=True,
        amplitude_current=50.0e-3,
        frequency_number=51,
        average_n_times=1,
        wait_for_steady=1.0,
        drift_correction=False,
        record_every_dt=0.1,
        record_every_dE=0.1,
        I_range='KBIO_IRANGE_1mA',
        E_range='KBIO_ERANGE_AUTO',
        bandwidth='KBIO_BW_5'
    ):
        """Initialize the GEIS technique.

        Args:
            initial_current_step: Initial current step before EIS (A)
            duration_step: Duration of step (s)
            vs_initial: Whether the voltage step is vs. the initial one
            step_number: The number of voltage steps
            initial_frequency: The initial frequency (Hz)
            final_frequency: The final frequency (Hz)
            logarithmic_spacing: Logarithmic/linear frequency spacing
                (True for logarithmic points spacing)
            amplitude_current: Amplitude of sinus (A)
            frequency_number: The number of frequencies
            average_n_times: The number of repeat times used for
                frequency averaging
            wait_for_steady: The number of periods to wait before each
                frequency
            drift_correction: Non-stationary drift correction
            record_every_dt: Record every dt (s)
            record_every_dE: Record every dE (V)
            I_Range (str): A string describing the I range, see the
                :data:`I_RANGES` module variable for possible values
            E_range (str): A string describing the E range to use, see the
                :data:`E_RANGES` module variable for possible values
            bandwidth (str): A string describing the bandwidth setting, see the
                :data:`BANDWIDTHS` module variable for possible values

        """
        args = (
            TechniqueArgument('vs_initial', 'bool', vs_initial, 'in', [True, False]),
            TechniqueArgument('vs_final', 'bool', vs_initial, None, None),
            TechniqueArgument(
                'Initial_Current_step', 'single', initial_current_step, None, None
            ),
            TechniqueArgument(
                'Final_Current_step', 'single', initial_current_step, None, None
            ),
            TechniqueArgument('Duration_step', 'single', duration_step, None, None),
            TechniqueArgument('Step_number', 'integer', 0, None, None),
            TechniqueArgument('Record_every_dT', 'single', record_every_dt, '>=', 0.0),
            TechniqueArgument('Record_every_dE', 'single', record_every_dE, '>=', 0.0),
            TechniqueArgument('Final_frequency', 'single', final_frequency, '>=', 0.0),
            TechniqueArgument(
                'Initial_frequency', 'single', initial_frequency, '>=', 0.0
            ),
            TechniqueArgument(
                'sweep', 'bool', not logarithmic_spacing, 'in', [True, False]
            ),
            TechniqueArgument(
                'Amplitude_Current', 'single', amplitude_current, None, None
            ),
            TechniqueArgument('Frequency_number', 'integer', frequency_number, '>=', 1),
            TechniqueArgument('Average_N_times', 'integer', average_n_times, '>=', 1),
            TechniqueArgument(
                'Correction', 'bool', drift_correction, 'in', [True, False]
            ),
            TechniqueArgument('Wait_for_steady', 'single', wait_for_steady, '>=', 0.0),
            TechniqueArgument(
                'I_Range', I_RANGES, I_range, 'in', list(I_RANGES.values())
            ),
            TechniqueArgument(
                'E_Range', E_RANGES, E_range, 'in', list(E_RANGES.values())
            ),
            TechniqueArgument(
                'Bandwidth', BANDWIDTHS, bandwidth, 'in', list(BANDWIDTHS.values())
            ),
        )
        super().__init__(args, 'geis.ecc')


# Section 7.14 in the specification
class SGEIS(Technique):
    """Staircase Galvano Electrochemical Impedance Spectroscopy (SGEIS) technique class.

    The SGEIS technique returns data with a different set of fields depending
    on which process steps it is in. If it is in process step 0 it returns
    data on the following fields (in order):

    * time (float)
    * Ewe (float)
    * I (float)
    * step (int)

    If it is in process 1 it returns data on the following fields:

    * freq (float)
    * abs_Ewe (float)
    * abs_I (float)
    * Phase_Zwe (float)
    * Ewe (float)
    * I (float)
    * abs_Ece (float)
    * abs_Ice (float)
    * Phase_Zce (float)
    * Ece (float)
    * t (float)
    * Irange (float) (VMP series only)
    * step (float)

    Which process it is in, can be checked with the ``process`` property on the
    :class:`.KBIOData` object.
    """

    # :Data fields definition
    process0_data_fields = {
        'common': [
            DataField('Ewe', c_float),
            DataField('I', c_float),
            DataField('step', c_uint32),
        ],
    }

    process1_data_fields = {
        'vmp3': [
            DataField('freq', c_float),
            DataField('abs_Ewe', c_float),
            DataField('abs_I', c_float),
            DataField('Phase_Zwe', c_float),
            DataField('Ewe', c_float),
            DataField('I', c_float),
            DataField('Blank0', c_float),
            DataField('abs_Ece', c_float),
            DataField('abs_Ice', c_float),
            DataField('Phase_Zce', c_float),
            DataField('Ece', c_float),
            DataField('Blank1', c_float),
            DataField('Blank2', c_float),
            DataField('t', c_float),
            DataField('Irange', c_uint32),
            DataField('step', c_uint32),
        ],
        'sp300': [
            DataField('freq', c_float),
            DataField('abs_Ewe', c_float),
            DataField('abs_I', c_float),
            DataField('Phase_Zwe', c_float),
            DataField('Ewe', c_float),
            DataField('I', c_float),
            DataField('Blank0', c_float),
            DataField('abs_Ece', c_float),
            DataField('abs_Ice', c_float),
            DataField('Phase_Zce', c_float),
            DataField('Ece', c_float),
            DataField('Blank1', c_float),
            DataField('Blank2', c_float),
            DataField('t', c_float),
            DataField('step', c_uint32),
        ],
    }

    data_fields = [process0_data_fields, process1_data_fields]

    def __init__(
        self,
        initial_current_step: float = 0.0,
        duration_step: float = 10.0,
        final_current_step: float = 0.1,
        vs_initial: bool = False,
        step_number: int = 10,
        initial_frequency: float = 100.0e3,
        final_frequency: float = 1.0,
        logarithmic_spacing: bool = True,
        amplitude_current: float = 0.01,
        frequency_number: int = 51,
        average_n_times: int = 1,
        wait_for_steady: float = 1.0,
        drift_correction: bool = False,
        record_every_dt: float = 0.1,
        record_every_dE: float = 0.1,
        I_range: str = 'KBIO_IRANGE_1mA',
        E_range: str = 'KBIO_ERANGE_AUTO',
        bandwidth: str = 'KBIO_BW_5'
    ) -> None:
        """Initialize the SPEIS technique.

        Args:
            initial_current_step: Initial current step before EIS (A)
            duration_step: Duration of step (s).
            final_current_step: Final current step after EIS (V).
            vs_initial: Whether the current step is vs. the initial one.
            step_number: The number of current steps.
            initial_frequency: The initial frequency (Hz).
            final_frequency: The final frequency (Hz).
            logarithmic_spacing: Logarithmic/linear frequency spacing.
                True for logarithmic points spacing.
            amplitude_current: Amplitude of sinus (A).
            frequency_number: The number of frequencies.
            average_n_times: The number of repeat times used for frequency averaging.
            wait_for_steady: The number of periods to wait before each frequency.
            drift_correction: Non-stationary drift correction.
            record_every_dt: Record every dt (s).
            record_every_dI: Record every dI (A).
            I_Range: A string describing the I range, see the :data:`I_RANGES` module
                variable for possible values.
            E_range: A string describing the E range to use, see the :data:`E_RANGES`
                module variable for possible values.
            Bandwidth: A string describing the bandwidth setting, see the
                :data:`BANDWIDTHS` module variable for possible values.
        """
        args = (
            TechniqueArgument('vs_initial', 'bool', vs_initial, 'in', [True, False]),
            TechniqueArgument('vs_final', 'bool', vs_initial, 'in', [True, False]),
            TechniqueArgument(
                'Initial_Current_step', 'single', initial_current_step, None, None
            ),
            TechniqueArgument(
                'Final_Current_step', 'single', final_current_step, None, None
            ),
            TechniqueArgument('Duration_step', 'single', duration_step, None, None),
            TechniqueArgument(
                'Step_number', 'integer', step_number, 'in', list(range(99))
            ),
            TechniqueArgument('Record_every_dT', 'single', record_every_dt, '>=', 0.0),
            TechniqueArgument('Record_every_dE', 'single', record_every_dE, '>=', 0.0),
            TechniqueArgument('Final_frequency', 'single', final_frequency, '>=', 0.0),
            TechniqueArgument(
                'Initial_frequency', 'single', initial_frequency, '>=', 0.0
            ),
            TechniqueArgument(
                'sweep', 'bool', not logarithmic_spacing, 'in', [True, False]
            ),
            TechniqueArgument(
                'Amplitude_Current', 'single', amplitude_current, None, None
            ),
            TechniqueArgument('Frequency_number', 'integer', frequency_number, '>=', 1),
            TechniqueArgument('Average_N_times', 'integer', average_n_times, '>=', 1),
            TechniqueArgument(
                'Correction', 'bool', drift_correction, 'in', [True, False]
            ),
            TechniqueArgument('Wait_for_steady', 'single', wait_for_steady, '>=', 0.0),
            TechniqueArgument(
                'I_Range', I_RANGES, I_range, 'in', list(I_RANGES.values())
            ),
            TechniqueArgument(
                'E_Range', E_RANGES, E_range, 'in', list(E_RANGES.values())
            ),
            TechniqueArgument(
                'Bandwidth', BANDWIDTHS, bandwidth, 'in', list(BANDWIDTHS.values())
            ),
        )
        super().__init__(args, 'seisg.ecc')


# Section 7.28 in the specification
class MIR(Technique):
    """Manual IR (MIR) technique class.

    The MIR technique returns no data.
    """

    # :Data fields definition
    # MIR is for linked experiments and does not record data. Variable data_fields
    # should never be accessed but is defined for consistency with type checking.
    data_fields = [{'common': [DataField('None', c_uint32)]},]

    def __init__(self, rcmp_value: float, rcmp_mode: int = 0):
        """Initialize the MIR technique.

        Args:
            rcmp_value: the R value to compensate.
            rcmp_mode: compensation mode, 0=software, 1=hardware (SP-300 series only).
        """
        args = (
            TechniqueArgument('Rcmp_Value', 'single', rcmp_value, '>=', 0.0),
            TechniqueArgument('Rcmp_Mode', 'integer', rcmp_mode, 'in', [0, 1])
        )
        super().__init__(args, 'IRcmp.ecc')


# Structs
# All structures used by the library are aligned on the double-word. A double word
# refers to a 32-bit entity -> set _pack_ equal to 4 (bytes) to override default data
# alignment.
class DeviceInfos(Structure):
    """Device information structure."""

    _pack_ = 4
    _fields_ = [  # Translated to string with DEVICE_CODES
        ('DeviceCode', c_int32),
        ('RAMsize', c_int32),
        ('CPU', c_int32),
        ('NumberOfChannels', c_int32),
        ('NumberOfSlots', c_int32),
        ('FirmwareVersion', c_int32),
        ('FirmwareDate_yyyy', c_int32),
        ('FirmwareDate_mm', c_int32),
        ('FirmwareDate_dd', c_int32),
        ('HTdisplayOn', c_int32),
        ('NbOfConnectedPC', c_int32),
    ]

    # Hack to include the fields names in doc string (and Sphinx documentation)
    __doc__ += '\n\n    Fields:\n\n' + '\n'.join(
        ['    * {} {}'.format(*field) for field in _fields_]
    )


class ChannelInfos(Structure):
    """Channel information structure."""

    _pack_ = 4
    _fields_ = [
        ('Channel', c_int32),
        ('BoardVersion', c_int32),
        ('BoardSerialNumber', c_int32),
        # Translated to string with FIRMWARE_CODES
        ('FirmwareCode', c_int32),
        ('FirmwareVersion', c_int32),
        ('XilinxVersion', c_int32),
        # Translated to string with AMP_CODES
        ('AmpCode', c_int32),
        # NbAmp is not mentioned in the documentation, but is in
        # in the examples and the info does not make sense
        # without it
        ('NbAmp', c_int32),
        ('LCboard', c_int32),
        ('Zboard', c_int32),
        ('MUXboard', c_int32),
        ('GPRAboard', c_int32),
        ('MemSize', c_int32),
        ('MemFilled', c_int32),
        # Translated to string with STATES
        ('State', c_int32),
        # Translated to string with MAX_I_RANGES
        ('MaxIRange', c_int32),
        # Translated to string with MIN_I_RANGES
        ('MinIRange', c_int32),
        # Translated to string with MAX_BANDWIDTHS
        ('MaxBandwidth', c_int32),
        ('NbOfTechniques', c_int32),
    ]
    # Hack to include the fields names in doc string (and Sphinx documentation)
    __doc__ += '\n\n    Fields:\n\n' + '\n'.join(
        ['    * {} {}'.format(*field) for field in _fields_]
    )


class CurrentValues(Structure):
    """Current values structure."""

    _pack_ = 4
    _fields_ = [
        # Translate to string with STATES
        ('State', c_int32),  # Channel state
        ('MemFilled', c_int32),  # Memory filled (in Bytes)
        ('TimeBase', c_float),  # Time base (s)
        ('Ewe', c_float),  # Working electrode potential (V)
        ('EweRangeMin', c_float),  # Ewe min range (V)
        ('EweRangeMax', c_float),  # Ewe max range (V)
        ('Ece', c_float),  # Counter electrode potential (V)
        ('EceRangeMin', c_float),  # Ece min range (V)
        ('EceRangeMax', c_float),  # Ece max range (V)
        ('Eoverflow', c_int32),  # Potential overflow
        ('I', c_float),  # Current value (A)
        # Translate to string with IRANGE
        ('IRange', c_int32),  # Current range
        ('Ioverflow', c_int32),  # Current overflow
        ('ElapsedTime', c_float),  # Elapsed time
        ('Freq', c_float),  # Frequency (Hz)
        ('Rcomp', c_float),  # R-compenzation (Ohm)
        ('Saturation', c_int32),  # E or/and I saturation
    ]
    # Hack to include the fields names in doc string (and Sphinx documentation)
    __doc__ += '\n\n    Fields:\n\n' + '\n'.join(
        ['    * {} {}'.format(*field) for field in _fields_]
    )


class DataInfos(Structure):
    """DataInfos structure."""

    _pack_ = 4
    _fields_ = [
        ('IRQskipped', c_int32),  # Number of IRQ skipped
        ('NbRows', c_int32),  # Number of rows into the data buffer,
        # i.e. number of points saved in the
        # data buffer
        ('NbCols', c_int32),  # Number of columns into the data
        # buffer, i.e. number of variables
        # defining a point in the data buffer
        ('TechniqueIndex', c_int32),  # Index (0-based) of the
        # technique that has generated
        # the data
        ('TechniqueID', c_int32),  # Identifier of the technique that
        # has generated the data
        ('ProcessIndex', c_int32),  # Index (0-based) of the process
        # of the technique that has
        # generated the data
        ('loop', c_int32),  # Loop number
        ('StartTime', c_double),  # Start time (s)
    ]
    # Hack to include the fields names in doc string (and Sphinx documentation)
    __doc__ += '\n\n    Fields:\n\n' + '\n'.join(
        ['    * {} {}'.format(*field) for field in _fields_]
    )


class TECCParam(Structure):
    """Technique parameter structure."""

    _pack_ = 4
    _fields_ = [
        ('ParamStr', c_char * 64),
        ('ParamType', c_int32),
        ('ParamVal', c_int32),
        ('ParamIndex', c_int32),
    ]
    # Hack to include the fields names in doc string (and Sphinx documentation)
    __doc__ += '\n\n    Fields:\n\n' + '\n'.join(
        ['    * {} {}'.format(*field) for field in _fields_]
    )


class TECCParams(Structure):
    """Technique parameters structure."""

    _pack_ = 4
    _fields_ = [
        ('len', c_int32),
        ('pParams', POINTER(TECCParam)),
    ]
    # Hack to include the fields names in doc string (and Sphinx documentation)
    __doc__ += '\n\n    Fields:\n\n' + '\n'.join(
        ['    * {} {}'.format(*field) for field in _fields_]
    )


# Exceptions
class ECLibException(Exception):
    """Base exception for all ECLib exceptions."""

    def __init__(self, message, error_code) -> None:
        """Initialize base exception."""
        super().__init__(message)
        self.error_code = error_code
        self.message = message

    def __str__(self):
        """__str__ representation of the ECLibException."""
        string = (
            f"{self.__class__.__name__} code: {self.error_code}. Message "
            f"\'{self.message.decode('utf-8')}\'"
        )
        return string

    def __repr__(self):
        """__repr__ representation of the ECLibException."""
        return self.__str__()


class ECLibError(ECLibException):
    """Exception for ECLib errors."""


class ECLibCustomException(ECLibException):
    """Exceptions that does not originate from the lib."""


# Functions
def structure_to_dict(structure: Structure) -> dict:
    """Convert a ctypes.Structure to a dict."""
    out: dict = {}
    for key, _ in structure._fields_:
        out[key] = getattr(structure, key)
    return out


def reverse_dict(dict_: dict) -> dict:
    """Reverse the key/value status of a dict."""
    return {v: k for k, v in dict_.items()}


# Constants
# :Device number to device name translation dict
DEVICE_CODES = {
    0: 'KBIO_DEV_VMP',
    1: 'KBIO_DEV_VMP2',
    2: 'KBIO_DEV_MPG',
    3: 'KBIO_DEV_BISTAT',
    4: 'KBIO_DEV_MCS_200',
    5: 'KBIO_DEV_VMP3',
    6: 'KBIO_DEV_VSP',
    7: 'KBIO_DEV_HCP803',
    8: 'KBIO_DEV_EPP400',
    9: 'KBIO_DEV_EPP4000',
    10: 'KBIO_DEV_BISTAT2',
    11: 'KBIO_DEV_FCT150S',
    12: 'KBIO_DEV_VMP300',
    13: 'KBIO_DEV_SP50',
    14: 'KBIO_DEV_SP150',
    15: 'KBIO_DEV_FCT50S',
    16: 'KBIO_DEV_SP300',
    17: 'KBIO_DEV_CLB500',
    18: 'KBIO_DEV_HCP1005',
    19: 'KBIO_DEV_CLB2000',
    20: 'KBIO_DEV_VSP300',
    21: 'KBIO_DEV_SP200',
    22: 'KBIO_DEV_MPG2',
    23: 'KBIO_DEV_ND1',
    24: 'KBIO_DEV_ND2',
    25: 'KBIO_DEV_ND3',
    26: 'KBIO_DEV_ND4',
    27: 'KBIO_DEV_SP240',
    28: 'KBIO_DEV_MPG205',
    29: 'KBIO_DEV_MPG210',
    30: 'KBIO_DEV_MPG220',
    31: 'KBIO_DEV_MPG240',
    32: 'KBIO_DEV_BP300',
    33: 'KBIO_DEV_VMP3e',
    34: 'KBIO_DEV_VSP3e',
    35: 'KBIO_DEV_SP50E',
    36: 'KBIO_DEV_SP150E',
    255: 'KBIO_DEV_UNKNOWN',
}

# :Firmware number to firmware name translation dict
FIRMWARE_CODES = {
    0: 'KBIO_FIRM_NONE',
    1: 'KBIO_FIRM_INTERPR',
    4: 'KBIO_FIRM_UNKNOWN',
    5: 'KBIO_FIRM_KERNEL',
    8: 'KBIO_FIRM_INVALID',
    10: 'KBIO_FIRM_ECAL',
}

# :Amplifier number to aplifier name translation dict
AMP_CODES = {
    0: 'KBIO_AMPL_NONE',
    1: 'KBIO_AMPL_2A',
    2: 'KBIO_AMPL_1A',
    3: 'KBIO_AMPL_5A',
    4: 'KBIO_AMPL_10A',
    5: 'KBIO_AMPL_20A',
    6: 'KBIO_AMPL_HEUS',
    7: 'KBIO_AMPL_LC',
    8: 'KBIO_AMPL_80A',
    9: 'KBIO_AMPL_4AI',
    10: 'KBIO_AMPL_PAC',
    11: 'KBIO_AMPL_4AI_VSP',
    12: 'KBIO_AMPL_LC_VSP',
    13: 'KBIO_AMPL_UNDEF',
    14: 'KBIO_AMPL_MUIC',
    15: 'KBIO_AMPL_ERROR',
    16: 'KBIO_AMPL_8AI',
    17: 'KBIO_AMPL_LB500',
    18: 'KBIO_AMPL_100A5V',
    19: 'KBIO_AMPL_LB2000',
    20: 'KBIO_AMPL_1A48V',
    21: 'KBIO_AMPL_4A14V',
    22: 'KBIO_AMPL_5A_MPG2B',
    23: 'KBIO_AMPL_10A_MPG2B',
    24: 'KBIO_AMPL_20A_MPG2B',
    25: 'KBIO_AMPL_40A_MPG2B',
    26: 'KBIO_AMPL_COIN_CELL_HOLDER',
    27: 'KBIO_AMPL4_10A5V',
    28: 'KBIO_AMPL4_2A30V',
    129: 'KBIO_AMPL4_1A48VP',
}

# :I range number to I range name translation dict
I_RANGES = {
    -1: 'KBIO_IRANGE_KEEP',
    0: 'KBIO_IRANGE_100pA',
    1: 'KBIO_IRANGE_1nA',
    2: 'KBIO_IRANGE_10nA',
    3: 'KBIO_IRANGE_100nA',
    4: 'KBIO_IRANGE_1uA',
    5: 'KBIO_IRANGE_10uA',
    6: 'KBIO_IRANGE_100uA',
    7: 'KBIO_IRANGE_1mA',
    8: 'KBIO_IRANGE_10mA',
    9: 'KBIO_IRANGE_100mA',
    10: 'KBIO_IRANGE_1A',
    11: 'KBIO_IRANGE_BOOSTER',
    12: 'KBIO_IRANGE_AUTO',
    13: 'KBIO_IRANGE_10pA',  # IRANGE_100pA + Igain x10
    14: 'KBIO_IRANGE_1pA',  # IRANGE_100pA + Igain x100
}

# :E range number to E range name translation dict
E_RANGES = {
    0: 'KBIO_ERANGE_2_5',
    1: 'KBIO_ERANGE_5',
    2: 'KBIO_ERANGE_10',
    3: 'KBIO_ERANGE_AUTO',
}

# :Bandwidth number to bandwidth name translation dict
BANDWIDTHS = {
    -1: 'KBIO_BW_KEEP',
    1: 'KBIO_BW_1',
    2: 'KBIO_BW_2',
    3: 'KBIO_BW_3',
    4: 'KBIO_BW_4',
    5: 'KBIO_BW_5',
    6: 'KBIO_BW_6',
    7: 'KBIO_BW_7',
    8: 'KBIO_BW_8',
    9: 'KBIO_BW_9',
}

# :Filter number to filter name translation dict
FILTERS = {
    -1: 'KBIO_FILTER_RSRVD',
    0: 'KBIO_FILTER_NONE',
    1: 'KBIO_FILTER_50KHZ',
    2: 'KBIO_FILTER_1KHZ',
    3: 'KBIO_FILTER_5KHZ',
}

# :Electrode connection number to electrode connection name translation dict
ELECTRODE_CONNECTIONS = {
    0: 'KBIO_CONN_STD',
    1: 'KBIO_CONN_CETOGRND',
}

# :Channel mode number to channel mode name translation dict
CHANNEL_MODES = {
    0: 'KBIO_MODE_GROUNDED',
    1: 'KBIO_MODE_FLOATING',
}

# :State number to state name translation dict
STATES = {
    0: 'KBIO_STATE_STOP',
    1: 'KBIO_STATE_RUN',
    2: 'KBIO_STATE_PAUSE',
}

# :Technique number to technique name translation dict
TECHNIQUE_IDENTIFIERS = {
    0: 'KBIO_TECHID_NONE',
    100: 'KBIO_TECHID_OCV',
    101: 'KBIO_TECHID_CA',
    102: 'KBIO_TECHID_CP',
    103: 'KBIO_TECHID_CV',
    104: 'KBIO_TECHID_PEIS',
    105: 'KBIO_TECHID_POTPULSE',
    106: 'KBIO_TECHID_GALPULSE',
    107: 'KBIO_TECHID_GEIS',
    108: 'KBIO_TECHID_STACKPEIS_SLAVE',
    109: 'KBIO_TECHID_STACKPEIS',
    110: 'KBIO_TECHID_CPOWER',
    111: 'KBIO_TECHID_CLOAD',
    112: 'KBIO_TECHID_FCT',
    113: 'KBIO_TECHID_SPEIS',
    114: 'KBIO_TECHID_SGEIS',
    115: 'KBIO_TECHID_STACKPDYN',
    116: 'KBIO_TECHID_STACKPDYN_SLAVE',
    117: 'KBIO_TECHID_STACKGDYN',
    118: 'KBIO_TECHID_STACKGEIS_SLAVE',
    119: 'KBIO_TECHID_STACKGEIS',
    120: 'KBIO_TECHID_STACKGDYN_SLAVE',
    121: 'KBIO_TECHID_CPO',
    122: 'KBIO_TECHID_CGA',
    123: 'KBIO_TECHID_COKINE',
    124: 'KBIO_TECHID_PDYN',
    125: 'KBIO_TECHID_GDYN',
    126: 'KBIO_TECHID_CVA',
    127: 'KBIO_TECHID_DPV',
    128: 'KBIO_TECHID_SWV',
    129: 'KBIO_TECHID_NPV',
    130: 'KBIO_TECHID_RNPV',
    131: 'KBIO_TECHID_DNPV',
    132: 'KBIO_TECHID_DPA',
    133: 'KBIO_TECHID_EVT',
    134: 'KBIO_TECHID_LP',
    135: 'KBIO_TECHID_GC',
    136: 'KBIO_TECHID_CPP',
    137: 'KBIO_TECHID_PDP',
    138: 'KBIO_TECHID_PSP',
    139: 'KBIO_TECHID_ZRA',
    140: 'KBIO_TECHID_MIR',
    141: 'KBIO_TECHID_PZIR',
    142: 'KBIO_TECHID_GZIR',
    150: 'KBIO_TECHID_LOOP',
    151: 'KBIO_TECHID_TO',
    152: 'KBIO_TECHID_TI',
    153: 'KBIO_TECHID_TOS',
    155: 'KBIO_TECHID_CPLIMIT',
    156: 'KBIO_TECHID_GDYNLIMIT',
    157: 'KBIO_TECHID_CALIMIT',
    158: 'KBIO_TECHID_PDYNLIMIT',
    159: 'KBIO_TECHID_LASV',
    167: 'KBIO_TECHID_MP',
    169: 'KBIO_TECHID_CASG',
    170: 'KBIO_TECHID_CASP',
}

# :Parameter type number to parameter type name translation dict
PARAMETER_TYPES = {
    0: 'PARAM_INT32',
    1: 'PARAM_BOOLEAN',
    2: 'PARAM_SINGLE',
}

# TODO: Add supported techniques to list.
# :Technique name to technique class translation dict. IMPORTANT. Add newly
# :implemented techniques to this dictionary
TECHNIQUE_IDENTIFIERS_TO_CLASS = {
    'KBIO_TECHID_OCV': OCV,
    'KBIO_TECHID_CV': CV,
    'KBIO_TECHID_CVA': CVA,
    'KBIO_TECHID_CP': CP,
    'KBIO_TECHID_CA': CA,
    'KBIO_TECHID_CPOWER': CPOWER,
    'KBIO_TECHID_PEIS': PEIS,
    'KBIO_TECHID_SPEIS': SPEIS,
    'KBIO_TECHID_GEIS': GEIS,
    'KBIO_TECHID_SGEIS': SGEIS,
}

# :List of devices in the WMP4/SP300 series
SP300SERIES = [
    'KBIO_DEV_SP200',
    'KBIO_DEV_SP240',
    'KBIO_DEV_SP300',
    'KBIO_DEV_BP300',
    'KBIO_DEV_VSP300',
    'KBIO_DEV_VMP300',
]

# :List of devices in the WMP4/SP300 series
VMP3SERIES = [
    'KBIO_DEV_SP50',
    'KBIO_DEV_SP50E',
    'KBIO_DEV_SP150',
    'KBIO_DEV_SP150E',
    'KBIO_DEV_VSP',
    'KBIO_DEV_VSP3e',
    'KBIO_DEV_VMP2',
    'KBIO_DEV_VMP3',
    'KBIO_DEV_VMP3e',
    'KBIO_DEV_BISTAT',
    'KBIO_DEV_HCP803'
    'KBIO_DEV_HCP1005'
    'KBIO_DEV_MPG2'
]

# Hack to make links for classes in the documentation
__doc__ += '\n\nInstrument classes:\n'
for name, klass in inspect.getmembers(sys.modules[__name__], inspect.isclass):
    if issubclass(klass, GeneralPotentiostat) or klass is GeneralPotentiostat:
        __doc__ += ' * :class:`.{.__name__}`\n'.format(klass)

__doc__ += '\n\nTechniques:\n'
for name, klass in inspect.getmembers(sys.modules[__name__], inspect.isclass):
    if issubclass(klass, Technique):
        __doc__ += ' * :class:`.{.__name__}`\n'.format(klass)
