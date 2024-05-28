
import logging

import pyvisa

from pymeasure.instruments import Channel, Instrument
from pymeasure.instruments.validators import strict_discrete_set, strict_range

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())


def aera_scan(port, start=1, stop=8, stop_after_first=True):
    """Get addresses of connected Aera MFCs.

    This function will check for a response to a read command for each address
    between `start` and `stop`.

    :param str port: port of connected Aera MFCs
    :param int start: first address to scan
    :param int stop: final address to scan
    :param bool stop_after_first: terminate scan after first valid address

    :returns: list of valid addresses
    """
    rm = pyvisa.ResourceManager()
    inst = rm.open_resource(port)
    inst.write_termination = '\r'
    inst.read_termination = '\r'
    address_list = []
    for address in range(start, stop+1):
        hex_address = hex(address)[2:].zfill(2)
        try:
            inst.query(f"\x02{hex_address}RFX")
        except pyvisa.Error:
            continue
        else:
            address_list.append(address)
        if len(address_list) and stop_after_first:
            break
    inst.close()
    return address_list


def get_address(port, serial_num):
    """Get address of connected MFC from its serial number.

    :param str port: port of connected Aera MFC
    :param str serial_num: serial number of Aera MFC

    :returns: MFC address as an integer if found, None if serial number
        does not correspond to any connected MFCs
    """
    rm = pyvisa.ResourceManager()
    inst = rm.open_resource(port)
    inst.write_termination = '\r'
    inst.read_termination = '\r'
    try:
        response = inst.query(f"\x0200RID{serial_num}")
    except pyvisa.Error:
        inst.close()
        return None
    inst.close()
    address = int(response[1:], base=16)
    return address


def set_address(port, serial_num, address):
    """Set address of connected MFC given its serial number.

    :param str port: port of connected MFC
    :param str serial_num: serial number of MFC
    :param int address: address to assign to MFC

    :returns: 'OK' if successfully set, 'NG' if invalid address, None if serial number
        does not correspond to any connected MFCs
    """
    rm = pyvisa.ResourceManager()
    inst = rm.open_resource(port)
    inst.write_termination = '\r'
    inst.read_termination = '\r'
    hex_address = hex(address)[2:].zfill(2)
    try:
        inst.write(f"\x0200SID{serial_num}{hex_address}")
        response = inst.read()
    except pyvisa.Error:
        inst.close()
        return None
    inst.close()
    return response


class AeraChannel(Channel):
    """Generic digital Aera MFC for specific MFC models to subclass."""

    def __init__(self, parent, address):
        self.hex_id = hex(address)[2:].zfill(2)
        self._alarm = False
        self._error = False
        self._autozero = False
        super().__init__(parent, address)

    def insert_id(self, command):
        """Insert the address in 2-character hex format replacing `placeholder`."""
        return command.format_map({self.placeholder: self.hex_id})

    def _read_error(self):
        """Convert two-hex error code to message(s)."""
        error = self.error
        error_4_7 = int(error[1], base=16)
        error_0_3 = int(error[2], base=16)
        for i, err in enumerate(AERA_ERRORS_0_3):
            if (2**i & error_0_3) == 2**i:
                log.warning("Aera MFC %d %s", self.id, err)
        for i, err in enumerate(AERA_ERRORS_4_7):
            if (2**i & error_4_7) == 2**i:
                log.warning("Aera MFC %d %s", self.id, err)
        if error_4_7 == 0 and error_0_3 == 0 and self._error:
            log.warning("Aera MFC %d error: unknown error", self.id)

    def _read_alarm(self):
        """Convert two-hex alarm code to message(s)."""
        alarm = self.alarm
        external_alarm = int(alarm[1], base=16)
        internal_alarm = int(alarm[2], base=16)
        for i, alarm in enumerate(AERA_EXTERNAL_ALARMS):
            if (2**i & external_alarm) == 2**i:
                log.warning("Aera MFC %d alarm: %s", self.id, alarm)
        for i, alarm in enumerate(AERA_INTERNAL_ALARMS):
            if (2**i & internal_alarm) == 2**i:
                log.warning("Aera MFC %d alarm: %s", self.id, alarm)

    def _update_status(self, status):
        """Get status from first character of read response."""
        if status == 'N':
            self._alarm = False
            self._error = False
            self._autozero = False
        if status == 'Z' and not self._autozero:
            self._autozero = True
            log.info("Aera MFC %d auto zero in progress.", self.id)
        elif status == 'A' and not self._alarm:
            self._alarm = True
            self._read_alarm()
        elif status == 'E' and not self._error:
            self._error = True
            self._read_error()
        elif status == 'X':
            if not self._alarm:
                self._alarm = True
                self._read_alarm()
            if not self._error:
                self._error = True
                self._read_error()

    def values(self, command, separator=',', cast=float, preprocess_reply=None, maxsplit=-1,
               **kwargs):
        """Write a command to the instrument and return a list of formatted
        values from the result.

        Overwrites :class:`.CommonBase` method so alarms and errors, if present, can be
        attributed to correct address.

        :param command: SCPI command to be sent to the instrument.
        :param preprocess_reply: Optional callable used to preprocess the string
            received from the instrument, before splitting it.
            The callable returns the processed string.
        :param separator: A separator character to split the string returned by
            the device into a list.
        :param maxsplit: The string returned by the device is split at most `maxsplit`
            times. -1 (default) indicates no limit.
        :param cast: A type to cast each element of the split string.
        :param kwargs: Keyword arguments to be passed to the :meth:`ask` method.
        :returns: A list of the desired type, or strings where the casting fails.
        """
        response = self.ask(command, **kwargs).strip()
        if callable(preprocess_reply):
            response = preprocess_reply(response)
        status, results = response[0], response[1:]
        results = results.split(separator, maxsplit=maxsplit)
        for i, result in enumerate(results):
            try:
                if cast == bool:
                    # Need to cast to float first since results are usually
                    # strings and bool of a non-empty string is always True
                    results[i] = bool(float(result))
                else:
                    results[i] = cast(result)
            except Exception:
                pass  # Keep as string
        self._update_status(status)
        return results

    ################
    # Flow Control #
    ################

    actual_flow = Channel.measurement(
        "\x02{ch}RFX",
        """Measure the actual flow in % of MFC range.""",
    )

    setpoint = Channel.control(
        "\x02{ch}RFD", "\x02{ch}SFD%.1f",
        """Control the setpoint in % of MFC range.""",
        validator=strict_range,
        values=(0, 100),
        check_set_errors=True,
    )

    mfc_range = Channel.measurement(
        "\x02{ch}RFK",
        """Get the maximum flow rate for the currently selected gas in sccm.
        Override in subclass if this value can be controlled."""
    )

    ramp_time = Channel.control(
        "\x02{ch}RRT", "\x02{ch}SRT%d",
        """Control the MFC setpoint ramping time in seconds (integer).
        Setting zero disables ramping.
        :meth:`write_EEPROM` must be called after if ramp time is to be stored in
        EEPROM.""",
        validator=strict_range,
        values=(0, 999),
        check_set_errors=True,
    )

    valve_mode = Channel.control(
        "\x02{ch}RVM", "\x02{ch}%s",
        """Control the MFC valve mode.
        Valid options are `flow`, `close`, and `open`.""",
        validator=strict_discrete_set,
        values=('flow', 'close', 'open'),
        get_process=lambda v: {'FN': 'flow', 'FC': 'close', 'FO': 'open'}[v],
        set_process=lambda v: {'flow': 'SRS', 'close': 'SVC', 'open': 'SVO'}[v],
        check_set_errors=True,
        cast=str
    )

    ##########
    # Alarms #
    ##########

    flow_tolerance = Channel.control(
        "\x02{ch}RFW", "\x02{ch}SFW%g",
        """Control the flow rate alarm tolerance in percent.
        Valid values are 0 to 100. A setting of 50 sets a tolerance of ±25 percent.
        Alarm triggers if flow is outside tolerance range for longer than the lock time.""",
        validator=strict_range,
        values=(0, 100),
        check_set_errors=True,
        cast=float
    )

    flow_lock_time = Channel.control(
        "\x02{ch}RFT", "\x02{ch}SFT%g",
        """Control the flow rate alarm lock time in seconds.
        Valid values are 0 to 99 seconds.
        Alarm triggers if flow is outside tolerance range for longer than the lock time.""",
        validator=strict_range,
        values=(0, 99),
        check_set_errors=True,
        cast=float
    )

    flow_alarm_enabled = Channel.control(
        "\x02{ch}RFI", "\x02{ch}%s",
        """Control whether the flow rate alarm is enabled.
        Valid options are `True` or `False`.""",
        validator=strict_discrete_set,
        values=(False, True),
        get_process=lambda v: {0: False, 1: True}[v],
        set_process=lambda v: {False: 'SFI', True: 'SAF'}[v],
        check_set_errors=True,
        cast=int
    )

    valve_alarm_value = Channel.control(
        "\x02{ch}RVA", "\x02{ch}SVA%g",
        """Control the valve alarm value in percent.
        Valid values are 0 to 100.""",
        validator=strict_range,
        values=(0, 100),
        check_set_errors=True,
        cast=float
    )

    valve_tolerance = Channel.control(
        "\x02{ch}RVW", "\x02{ch}SVW%g",
        """Control the valve alarm tolerance in percent.
        Valid values are 0 to 100. A setting of 50 sets a tolerance of ±25 percent.""",
        validator=strict_range,
        values=(0, 100),
        check_set_errors=True,
        cast=float
    )

    valve_lock_time = Channel.control(
        "\x02{ch}RVT", "\x02{ch}SVT%g",
        """Control the valve alarm lock time in seconds.
        Valid values are 0 to 99 seconds.""",
        validator=strict_range,
        values=(0, 99),
        check_set_errors=True,
        cast=float
    )

    valve_alarm_enabled = Channel.control(
        "\x02{ch}RVI", "\x02{ch}%s",
        """Control whether the valve alarm is enabled.
        Valid options are `True` or `False`.""",
        validator=strict_discrete_set,
        values=(False, True),
        get_process=lambda v: {0: False, 1: True}[v],
        set_process=lambda v: {False: 'SVI', True: 'SAV'}[v],
        check_set_errors=True,
        cast=int
    )

    ################
    # Mode Setting #
    ################

    digital_mode_enabled = Channel.control(
        "\x02{ch}RMD", "\x02{ch}%s",
        """Control whether the MFC operates in analog or digital mode.
        Valid values are `True` or `False`.""",
        validator=strict_discrete_set,
        values=(False, True),
        get_process=lambda v: {'A': False, 'D': True}[v],
        set_process=lambda v: {False: 'SAM', True: 'SDM'}[v],
        check_set_errors=True,
        cast=str
    )

    #########
    # Other #
    #########

    memo = Channel.control(
        "\x02{ch}RGN", "\x02{ch}SGN%s",
        """Control the memo written in the MFC.
        Memo is a string of maximum 20 characters.
        The name of the gas used is entered by default.
        :meth:`write_EEPROM` must be called after if memo is to be stored in EEPROM.""",
        check_set_errors=True,
        cast=str
    )

    table_memo = Channel.control(
        "\x02{ch}RGX0", "\x02{ch}SGX%s",
        """Control the memo written in the currently selected gas table.
        Memo is a string of maximum 20 characters
        :meth:`write_EEPROM` must be called after if memo is to be stored in EEPROM.""",
        check_set_errors=True,
        cast=str
    )

    gas_table = Channel.measurement(
        "\x02{ch}RGT",
        """Get the number of the currently selected gas table.
        Override in subclass if this value can be controlled.""",
        cast=int
    )

    full_scale_n2 = Channel.measurement(
        "\x02{ch}RFS",
        """Get the N2 equivalent maximum flow rate in sccm.
        Read-only."""
    )

    conversion_factor = Channel.measurement(
        "\x02{ch}RCF",
        """Get the gas conversion factor of the currently selected gas.
        Override in subclass if this value can be controlled."""
    )

    alarm = Channel.measurement(
        "\x02{ch}RAS",
        """"Get alarm status as a two-character string.""",
        cast=str
    )

    error = Channel.measurement(
        "\x02{ch}RER",
        """Get error status as two-character string.""",
        cast=str
    )

    version = Channel.measurement(
        "\x02{ch}RVN",
        """Get the version and series number. Returns x.xx<TAB>S/N """,
    )

    def clear_alarms(self):
        """Clear all MFC alarms."""
        self.write(f"\x02{self.hex_id}SAC")
        self._alarm = False
        self.check_set_errors()

    def clear_errors(self):
        """Clear all MFC errors."""
        self.write(f"\x02{self.hex_id}SEC")
        self._error = False
        self.check_set_errors()

    def start_auto_zero(self):
        """Adjust the zero point of the MFC.

        Before adjusting the zero point:
            * Gas supply before and after MFC must be shut off
            * MFC must be powered on for at least 15 minutes
            * MFC must be at operating temperature
        """
        self.write(f"\x02{self.hex_id}SZP")
        self.check_set_errors()

    def check_set_errors(self):
        """Read 'OK' from MFC after setting."""
        response = self.read()
        if response != 'OK':
            errors = ["Error setting ROD-4.",]
        else:
            errors = []
        return errors

    def write_eeprom(self):
        """Write variables of the current gas table to EEPROM.

        Override in subclass to document which variables specifically are written.
        """
        self.write(f"\x02{self.hex_id}SEP")
        self.check_set_errors()


class AeraMFC(Instrument):
    """Represents collection of Aera mass flow controllers with digital communication.

    Multiple MFCs can be connected in serial to the same port. User must call the
    :meth:`add_channel` method with the appropriate address and MFC class to add an MFC
    before it can be controlled.

    .. code-block:: python

        from pymeasure.instruments.proterial import AeraFCD98x
        mfc_bank = AeraMFC("ASRL1::INSTR")
        for address in range(1, 5):         # Add AeraFCD98x MFCs at channels 1-4
            mfc_bank.add_child(address, AeraFCD98x)

        mfc_bank.ch_1.mfc_range = 500       # Sets Channel 1 MFC range to 500 sccm
        mfc_bank.ch_2.valve_mode = 'flow'   # Sets Channel 2 MFC to flow control
        mfc_bank.ch_3.setpoint = 50         # Sets Channel 3 MFC to flow at 50% of full range
        print(mfc.ch_4.actual_flow)         # Prints Channel 4 MFC actual flow in % of full range
    """

    def __init__(self, adapter, name="Aera MFC Collection", **kwargs):
        super().__init__(
            adapter, name, read_termination='\r', write_termination='\r',
            includeSCPI=False, **kwargs
        )

    def add_channel(self, address, mfc_class=AeraChannel):
        """Add address to Aera MFC collection.

        Channel becomes accessible as :code:`self.ch_#`, where # is the MFC address.

        :param int address: address of Aera MFC
        :param AeraChannel mfc_class: Aera MFC class to add as a address, adds generic
            AeraChannel by default
        """
        if mfc_class is None:
            mfc_class = AeraChannel
        self.add_child(mfc_class, address, collection="channels", prefix="ch_")


AERA_ERRORS_4_7 = ("Zero point correction error: auto zero error 2",)
AERA_ERRORS_0_3 = ("Communication error: wrong input command",
                   "",
                   "EEPROM error: data cannot be written to EEPROM",
                   "Zero point correction error: auto zero error 1")
AERA_EXTERNAL_ALARMS = ("external input HIGH", "external input LOW")
AERA_INTERNAL_ALARMS = ("flow rate HIGH", "flow rate LOW", "valve HIGH", "valve LOW")
