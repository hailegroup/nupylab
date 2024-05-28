
from aera_mfc import AeraChannel
from pymeasure.instruments import Channel
from pymeasure.instruments.validators import strict_discrete_set, strict_range


class AeraFCD98x(AeraChannel):
    """Implementation of Aera FC-D98x series mass flow controller."""

    def __init__(self, parent, address):
        if 1 <= address <= 239:
            super().__init__(parent, address)
        else:
            raise ValueError(f"Aera FC-D98x address must be between 1-239, not {address}.")

    ################
    # Flow Control #
    ################

    mfc_range = Channel.control(
        "\x02{ch}RFK", "\x02{ch}SFK%.2f",
        """Control the MFC range in sccm.
        The upper set limit is the full scale flow rate, the lower set limit is the full
        scale flow rate divided by 3.4.
        Gas conversion factor should also be adjusted if this value is changed.
        :meth:`write_EEPROM` must be called after if range setting is to be stored in
        EEPROM.""",
        validator=strict_range,
        values=(0, 200000),
        check_set_errors=True,
    )

    ##########
    # Alarms #
    ##########

    external_alarm_value = Channel.control(
        "\x02{ch}REA", "\x02{ch}SEA%g",
        """Control the external alarm value in percent.
        Valid values are 0 to 100.""",
        validator=strict_range,
        values=(0, 100),
        check_set_errors=True,
        cast=float
    )

    external_tolerance = Channel.control(
        "\x02{ch}REW", "\x02{ch}SEW%g",
        """Control the external alarm tolerance in percent.
        Valid values are 0 to 100. A setting of 50 sets a tolerance of ±25 percent.""",
        validator=strict_range,
        values=(0, 100),
        check_set_errors=True,
        cast=float
    )

    external_lock_time = Channel.control(
        "\x02{ch}RET", "\x02{ch}SET%g",
        """Control the external alarm lock time in seconds.
        Valid values are 0 to 99 seconds.""",
        validator=strict_range,
        values=(0, 99),
        check_set_errors=True,
        cast=float
    )

    external_alarm_enabled = Channel.control(
        "\x02{ch}REI", "\x02{ch}%s",
        """Control whether the external alarm is enabled.
        Valid options are `True` or `False`.""",
        validator=strict_discrete_set,
        values=(False, True),
        get_process=lambda v: {0: False, 1: True}[v],
        set_process=lambda v: {False: 'SEI', True: 'SAE'}[v],
        check_set_errors=True,
        cast=int
    )

    #########
    # Other #
    #########

    conversion_factor = Channel.control(
        "\x02{ch}RCF", "\x02{ch}SCF%g",
        """Control the gas conversion factor of the currently selected gas.
        MFC range should also be adjusted if this value is changed.
        :meth:`write_EEPROM` must be called after if CF setting is to be stored in
        EEPROM.""",
    )

    gas_table = Channel.control(
        "\x02{ch}RGT", "\x02{ch}SGT%d",
        """Control the number of the currently selected gas table.
        Valid values are integers between 1-4.
        :meth:`write_EEPROM` must be called after if gas table number setting is to be
        stored in EEPROM.""",
        validator=strict_discrete_set,
        values=(1, 2, 3, 4),
        check_set_errors=True,
        cast=int
    )

    def write_eeprom(self):
        """Write variables of the current gas table to EEPROM.

        Written variables are
            * :attr:`setpoint`
            * :attr:`gas_table`
            * :attr:`ramp_time`
            * :attr:`conversion_factor`
            * :attr:`mfc_range`
            * :attr:`memo`
            * :attr:`table_memo`
            * alarm settings
        """
        self.write(f"\x02{self.hex_id}SEP")
        self.check_set_errors()
