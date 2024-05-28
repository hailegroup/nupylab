
from aera_mfc import AeraChannel


class AeraFCPA78x(AeraChannel):
    """Implementation of Aera FC-PA78x series mass flow controller."""

    def __init__(self, parent, address):
        if 1 <= address <= 127:
            super().__init__(parent, address)
        else:
            raise ValueError(f"Aera FC-D98x address must be between 1-127, not {address}.")

    def write_eeprom(self):
        """Write variables of the current gas table to EEPROM.

        Written variables are
            * :attr:`setpoint`
            * :attr:`ramp_time`
            * :attr:`memo`
            * :attr:`table_memo`
            * alarm settings
        """
        self.write(f"\x02{self.hex_id}SEP")
        self.check_set_errors()
