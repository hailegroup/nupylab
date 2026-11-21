"""Driver for the Keithley 182 Sensitive Digital Voltmeter.

The Model 182 predates SCPI and speaks Keithley's device-dependent command (DDC)
language: a command letter, an optional numeric parameter, and ``X`` to execute,
e.g. ``R0T0X``. Commands are only acted upon when ``X`` is received, so several
may be concatenated into a single write. If any command in a string is invalid,
none of the commands in that string are executed.

Readings are not queried. The 182 sends one reading each time it is addressed to
talk, so :attr:`Keithley182.voltage` reads from the bus without writing a query
first. In a multiple trigger mode the instrument free-runs and each read returns
its latest reading; if a new reading is not yet available, the 182 holds off the
bus until one is, which can look like a VISA timeout, hence the generous default
timeout below. A read ends on EOI, which the 182 asserts with the last character
of a message, rather than on the CR LF it also sends; those characters are
stripped when a reading is parsed.

Factory defaults, restored by :meth:`Keithley182.reset`, are 6.5-digit
resolution (`B1`), readings from the A/D converter (`F0`), reading with prefix
(`G1`), buffer disabled (`I0`), filters enabled (`N1`), medium digital filter
response (`P2`), the 30 V range (`R5`), line cycle integration (`S0`), **trigger
multiple on external (`T6`)**, CR LF terminators (`Y0`), and reading relative
disabled (`Z0`). Since the trigger source defaults to the rear panel input,
:meth:`Keithley182.setup_voltage` selects `T0` (multiple on talk) so that
readings can be taken over the bus alone.

The command set implemented here follows Table 4-1 and paragraphs 4.2.1 through
4.2.25 of the Model 182 instruction manual. The calibration (`C`), analog output
relative (`J`) and analog output (`V`) commands are deliberately not
implemented, nor are the `U3`-`U14` alternate outputs.
"""

import logging
import math
import re
import time
from typing import Optional, Union

from pymeasure.instruments import Instrument
from pymeasure.instruments.validators import strict_discrete_set, strict_range

from nupylab.utilities import NupylabError

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

# Matches "+1.820000E+00" in a reading such as "NDCV+1.820000E+00" (G1 prefix
# format, the factory default) or in a bare reading (G0 format).
_READING_RE = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[EeDd][-+]?\d+)?")


class Keithley182(Instrument):
    """Instrument class for the Keithley 182 sensitive digital voltmeter.

    .. code-block:: python

        voltmeter = Keithley182("GPIB::7::INSTR")
        voltmeter.setup_voltage()
        print(voltmeter.voltage)

    """

    #: Value sent in place of a reading when the input overflows the range.
    OVERFLOW_READING: float = 9.999999e95

    #: Status byte bit set when a reading is waiting to be read, as laid out
    #: by the SRQ mask, see :attr:`srq_mask`.
    READING_DONE: int = 1

    def __init__(
        self,
        adapter,
        name: str = "Keithley 182 Sensitive Digital Voltmeter",
        read_termination: Optional[str] = None,
        timeout: int = 10000,
        **kwargs,
    ) -> None:
        """Connect to the voltmeter.

        Args:
            adapter: PyVISA resource name, e.g. `GPIB::7::INSTR`, or adapter.
            name: name of instrument.
            read_termination: read terminator, none by default. The 182 marks
                the end of a message by asserting EOI with its last character,
                which is how a read here ends; the CR LF it also appends (`Y0`)
                is stripped when the reading is parsed. Matching a terminator
                instead would warn, and eventually desynchronize reads,
                whenever the instrument's own terminator setting differed. Pass
                one only for a 182 with EOI disabled (`K1` or `K3`).
            timeout: VISA timeout in ms. The default is generous because the
                182 holds off the bus until a new reading is available, which
                with slow integration, filtering, and a long trigger interval
                can take several seconds.
            **kwargs: passed on to :class:`pymeasure.instruments.Instrument`.
        """
        self._setup: Optional[dict] = None
        super().__init__(
            adapter,
            name,
            read_termination=read_termination,
            timeout=timeout,
            includeSCPI=False,
            **kwargs,
        )

    # Device-dependent command settings #

    resolution = Instrument.setting(
        "B%dX",
        """Set display resolution in digits.
        Valid options are 3.5, 4.5, 5.5, and 6.5. This sets the front panel
        display only; the integration period sets the usable resolution of
        readings sent over the bus.""",
        validator=strict_discrete_set,
        values={5.5: 0, 6.5: 1, 3.5: 2, 4.5: 3},
        map_values=True,
    )

    filter_damping = Instrument.setting(
        "D%dX",
        """Set filter damping on or off.
        Damping off is the same as a medium digital filter response, damping on
        the same as a slow response.""",
        validator=strict_discrete_set,
        values={False: 0, True: 1},
        map_values=True,
    )

    reading_source = Instrument.setting(
        "F%dX",
        """Set the source of readings sent over the bus.
        Valid options are `adc` (latest reading from the A/D converter), `buffer`
        (one reading from the buffer), `buffer all` (all readings in the buffer,
        separated by commas), `max`, and `min` (maximum or minimum value in the
        buffer). All but `adc` require the buffer to be enabled and filled.""",
        validator=strict_discrete_set,
        values={"adc": 0, "buffer": 1, "buffer all": 2, "max": 3, "min": 4},
        map_values=True,
    )

    reading_format = Instrument.setting(
        "G%dX",
        """Set the format of readings sent over the bus.
        Valid options are `reading`, `reading with prefix`, `reading with
        location`, `reading with location and prefix`, `reading with time
        stamp`, `reading with time stamp and prefix`, `reading with time stamp
        and location`, and `reading with time stamp, location and prefix`.
        Buffer location and time stamp fields are zero unless readings come
        from the buffer.""",
        validator=strict_discrete_set,
        values={
            "reading": 0,
            "reading with prefix": 1,
            "reading with location": 2,
            "reading with location and prefix": 3,
            "reading with time stamp": 4,
            "reading with time stamp and prefix": 5,
            "reading with time stamp and location": 6,
            "reading with time stamp, location and prefix": 7,
        },
        map_values=True,
    )

    eoi_and_holdoff = Instrument.setting(
        "K%dX",
        """Set EOI and bus hold-off on X.
        Valid options are `eoi holdoff`, `holdoff`, `eoi`, and `none`.""",
        validator=strict_discrete_set,
        values={"eoi holdoff": 0, "holdoff": 1, "eoi": 2, "none": 3},
        map_values=True,
    )

    srq_mask = Instrument.setting(
        "M%dX",
        """Set the SRQ mask.
        Valid options are 0 (disabled), 1 (reading done), 2 (buffer half full),
        4 (buffer full), 8 (reading overflow), 16 (ready for command), 32
        (error), and 128 (ready for trigger). Values may be summed.""",
        validator=strict_range,
        values=(0, 191),
    )

    filter_enabled = Instrument.setting(
        "N%dX",
        """Set whether the analog and digital filters are enabled.
        Each filter is configured separately, by :attr:`analog_filter` and
        :attr:`digital_filter`.""",
        validator=strict_discrete_set,
        values={False: 0, True: 1},
        map_values=True,
    )

    analog_filter = Instrument.setting(
        "O%dX",
        """Set the analog filter on or off.
        Takes effect only while :attr:`filter_enabled` is True.""",
        validator=strict_discrete_set,
        values={False: 0, True: 1},
        map_values=True,
    )

    digital_filter = Instrument.setting(
        "P%dX",
        """Set the digital filter response.
        Valid options are `off`, `fast`, `medium`, and `slow`. Takes effect only
        while :attr:`filter_enabled` is True. Readings on the lower ranges are
        excessively noisy with the digital filter off.""",
        validator=strict_discrete_set,
        values={"off": 0, "fast": 1, "medium": 2, "slow": 3},
        map_values=True,
    )

    measurement_range = Instrument.setting(
        "R%dX",
        """Set the measurement range in volts, or `auto` for auto-ranging.
        Valid options are `auto`, 0.003, 0.03, 0.3, 3, and 30. Choose the lowest
        range that does not overflow for the best accuracy and resolution.""",
        validator=strict_discrete_set,
        values={"auto": 0, 0.003: 1, 0.03: 2, 0.3: 3, 3: 4, 30: 5},
        map_values=True,
    )

    auto_range = Instrument.setting(
        "R%dX",
        """Set auto-ranging on or off.
        Switching it off leaves the instrument on its present range.""",
        validator=strict_discrete_set,
        values={True: 0, False: 8},
        map_values=True,
    )

    integration_period = Instrument.setting(
        "S%dX",
        """Set the integration period of the A/D converter.
        Valid options are `line cycle` (16.67 ms at 60 Hz, 20 ms at 50 Hz),
        0.003, and 0.1, in seconds. Use 0.1 for maximum resolution and minimum
        noise, 0.003 for the fastest reading rates.""",
        validator=strict_discrete_set,
        values={"line cycle": 0, 0.003: 1, 0.1: 2},
        map_values=True,
    )

    trigger_mode = Instrument.setting(
        "T%dX",
        """Set the trigger mode and source.
        Valid options are `multiple` or `one-shot` combined with one of `talk`,
        `get`, `x`, `external`, or `manual`, e.g. `one-shot on talk`, as well as
        `disabled`. In a multiple mode the instrument processes readings
        continuously at the trigger interval and a read returns its latest
        reading; in a one-shot mode each reading needs its own trigger.""",
        validator=strict_discrete_set,
        values={
            "multiple on talk": 0,
            "one-shot on talk": 1,
            "multiple on get": 2,
            "one-shot on get": 3,
            "multiple on x": 4,
            "one-shot on x": 5,
            "multiple on external": 6,
            "one-shot on external": 7,
            "multiple on manual": 8,
            "one-shot on manual": 9,
            "disabled": 10,
        },
        map_values=True,
    )

    terminator = Instrument.setting(
        "Y%dX",
        """Set the bus terminator.
        Valid options are `CRLF`, `LFCR`, `CR`, and `LF`. Changing this must be
        matched by the adapter read termination, or reads will hang.""",
        validator=strict_discrete_set,
        values={"CRLF": 0, "LFCR": 1, "CR": 2, "LF": 3},
        map_values=True,
    )

    # Commands that take no option or a free parameter #

    def reset(self) -> None:
        """Recall the factory default setup.

        Note that the factory default trigger source is the rear panel external
        trigger input; see :meth:`setup_voltage`.
        """
        self.write("L1X")

    def device_clear(self) -> None:
        """Send a GPIB device clear (SDC).

        Unlike :meth:`reset`, this is a bus-level command rather than one the
        182 has to parse, so it is the way to reach an instrument that has
        stopped accepting commands. The 182 treats it as a reset to its
        power-on setup, as its manual's command defaults describe.
        """
        self.adapter.connection.clear()

    def recover(self) -> None:
        """Clear the instrument and restore the setup it was last given.

        A read that times out can leave the 182 holding the bus off, after
        which every transfer to it fails, so a caller whose read failed should
        try this before giving up.

        Raises:
            Exception: whatever the clear or the setup raises, if the
                instrument is not reachable at all.
        """
        self.device_clear()
        if self._setup is not None:
            self.setup_voltage(**self._setup)

    def save_setup(self) -> None:
        """Save the current setup as the power-on setup."""
        self.write("L0X")

    def recall_setup(self) -> None:
        """Recall the saved power-on setup."""
        self.write("L2X")

    def trigger(self) -> None:
        """Send an immediate (manual) trigger.

        Triggers the instrument whatever the programmed trigger source, unless
        triggers are disabled.
        """
        self.write("H0X")

    def self_test(self) -> None:
        """Perform the memory test."""
        self.write("H1X")

    def set_trigger_delay(self, delay: Optional[float] = None) -> None:
        """Set the delay between a trigger and the start of a reading.

        Args:
            delay: delay in seconds, from 0.001 to 999.999. `None` disables the
                trigger delay.
        """
        if delay is None:
            self.write("W0X")
            return
        milliseconds = round(strict_range(delay, (1e-3, 999.999)) * 1000)
        self.write("W{:d}X".format(milliseconds))

    def set_trigger_interval(self, interval: float) -> None:
        """Set the interval between readings in a multiple trigger mode.

        Args:
            interval: interval in seconds, from 0.01 to 999.999.
        """
        milliseconds = round(strict_range(interval, (0.01, 999.999)) * 1000)
        self.write("Q{:d}X".format(milliseconds))

    def enable_relative(self, value: Optional[float] = None) -> None:
        """Enable reading relative (zero), subtracting a baseline from readings.

        Args:
            value: relative value in volts, from 1e-9 to 30.3 in magnitude.
                `None` stores the next reading as the relative value.
        """
        if value is None:
            self.write("Z1X")
            return
        if not 1e-9 <= abs(value) <= 30.3:
            raise ValueError(
                "Keithley 182 relative value must be between 1e-9 V and 30.3 V "
                "in magnitude, but got {:g} V.".format(value)
            )
        self.write("Z2,{:g}X".format(value))

    def enable_stored_relative(self) -> None:
        """Enable reading relative using the previously stored relative value."""
        self.write("Z3X")

    def disable_relative(self) -> None:
        """Disable reading relative (zero)."""
        self.write("Z0X")

    def display(self, text: Optional[str] = None, store: bool = False) -> None:
        """Display a string on the front panel.

        Args:
            text: up to 12 characters. `None` restores the normal display.
            store: whether to also store the string in EEROM, from where
                :meth:`display_stored` recalls it.
        """
        if text is None:
            self.write("A0X")
        else:
            self.write("A{:d},{:s}X".format(2 if store else 1, text[:12]))

    def display_stored(self) -> None:
        """Display the string stored in EEROM."""
        self.write("A3X")

    def disable_buffer(self) -> None:
        """Disable the reading buffer."""
        self.write("I0X")

    def enable_buffer(self, size: Optional[int] = None) -> None:
        """Enable the reading buffer.

        Args:
            size: length of the linear buffer, from 1 to 1024 readings. The
                instrument stores that many readings and then stops. `None`
                selects the 1024-reading circular buffer, which overwrites its
                oldest readings instead of stopping.
        """
        if size is None:
            self.write("I2X")
            return
        self.write("I1,{:d}X".format(int(strict_range(int(size), (1, 1024)))))

    def setup_voltage(
        self,
        measurement_range: Union[str, float] = "auto",
        resolution: float = 6.5,
        integration_period: Union[str, float] = "line cycle",
        digital_filter: str = "medium",
        trigger_mode: str = "one-shot on x",
        trigger_interval: Optional[float] = None,
        relative: bool = False,
    ) -> None:
        """Configure the voltmeter for DC voltage measurements.

        Selects readings from the A/D converter in the bare reading format, and
        leaves the instrument taking one reading per trigger. The trigger mode
        has to be set here because the factory default source is the rear panel
        trigger input.

        Pair this with :meth:`measure_voltage`, which triggers a reading and
        waits for the instrument to report it ready. Do not simply read: the
        182 holds the bus off until a reading is available, and a read issued
        when none is coming leaves the instrument unreachable, so that even a
        device clear cannot revive it.

        Args:
            measurement_range: `auto` or a range in volts, see
                :attr:`measurement_range`.
            resolution: display resolution in digits, see :attr:`resolution`.
            integration_period: see :attr:`integration_period`. Pass 0.1 for the
                quietest readings.
            digital_filter: see :attr:`digital_filter`.
            trigger_mode: see :attr:`trigger_mode`. One of the one-shot modes,
                so that each reading is asked for rather than arriving from a
                series the instrument may stop producing.
            trigger_interval: interval between readings in seconds, for a
                multiple trigger mode only. Ignored, and not sent, in a
                one-shot mode, where nothing runs between triggers.
            relative: whether to leave reading relative (zero) enabled. Disabled
                by default so that absolute potentials are reported.
        """
        # Kept so that `recover` can restore this setup after a device clear
        self._setup = {
            "measurement_range": measurement_range,
            "resolution": resolution,
            "integration_period": integration_period,
            "digital_filter": digital_filter,
            "trigger_mode": trigger_mode,
            "trigger_interval": trigger_interval,
            "relative": relative,
        }
        self.measurement_range = measurement_range
        self.resolution = resolution
        self.integration_period = integration_period
        self.filter_enabled = True
        self.digital_filter = digital_filter
        if not relative:
            self.disable_relative()
        self.reading_source = "adc"
        self.reading_format = "reading"
        if trigger_interval is not None and trigger_mode.startswith("multiple"):
            self.set_trigger_interval(trigger_interval)
        self.trigger_mode = trigger_mode

    # Readings #

    @staticmethod
    def parse_reading(reading: str) -> float:
        """Extract the numeric value from a reading string.

        Handles both the bare `G0` format and the prefixed formats, e.g.
        `NDCV+1.820000E+00`, where the prefix marks a normal (`N`), overflowed
        (`O`), or relative (`R`) reading of the A/D converter (`DCV`) or of the
        buffer maximum (`MAX`) or minimum (`MIN`).

        Args:
            reading: reading string as sent by the instrument.

        Returns:
            Reading in volts. An overflow is reported by the instrument as
            :attr:`OVERFLOW_READING` rather than as an error.

        Raises:
            ValueError: if the string holds no number.
        """
        match = _READING_RE.search(reading.strip())
        if match is None:
            raise ValueError(
                "Could not parse Keithley 182 reading: {!r}".format(reading)
            )
        return float(match.group().replace("D", "E").replace("d", "E"))

    @property
    def voltage(self) -> float:
        """Get the latest voltage reading in volts.

        Returns `nan` for an overflowed reading, i.e. one beyond the present
        range, which the instrument reports as +9.999999E+95.
        """
        reading: str = self.read()
        value: float = self.parse_reading(reading)
        if abs(value) >= self.OVERFLOW_READING:
            log.warning(
                "Keithley 182 reading overflows the present range: %s",
                reading.strip(),
            )
            return math.nan
        return value

    def reading_available(self) -> bool:
        """Get whether a reading is waiting to be read.

        Serial polls the instrument. A serial poll is a bus command the 182
        answers even while it is converting, unlike a read, which it holds the
        bus off for until a reading is available.
        """
        return bool(self.adapter.connection.read_stb() & self.READING_DONE)

    def wait_for_reading(
        self, timeout: float = 5.0, interval: float = 0.05
    ) -> bool:
        """Wait until the instrument reports that a reading is ready.

        Args:
            timeout: how long to wait, in seconds.
            interval: how long to wait between serial polls, in seconds.

        Returns:
            Whether a reading became available within `timeout`.
        """
        deadline = time.monotonic() + timeout
        while True:
            if self.reading_available():
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(interval)

    def measure_voltage(self, timeout: float = 5.0) -> float:
        """Trigger one reading, wait for it, and return it.

        The safe way to read this instrument: it asks for a reading, confirms
        by serial poll that one is waiting, and only then reads. Reading
        without that check risks the bus hold-off the 182 applies when no
        reading is available, which leaves it unreachable until it is power
        cycled, a device clear included.

        Args:
            timeout: how long to wait for the reading, in seconds. Allow for
                the integration period and for the digital filter to settle.

        Returns:
            Reading in volts, or `nan` if it overflows the present range.

        Raises:
            NupylabError: if no reading arrives within `timeout`. The
                instrument is left alone rather than read, so it stays usable.
        """
        self.trigger()
        if not self.wait_for_reading(timeout):
            raise NupylabError(
                f"{self.name} reported no reading within {timeout:g} s; "
                "not reading, to leave the instrument on the bus."
            )
        return self.voltage

    @property
    def machine_status(self) -> str:
        """Get the machine status word (U0)."""
        self.write("U0X")
        return self.read().strip()

    @property
    def error_status(self) -> str:
        """Get the error condition word (U1)."""
        self.write("U1X")
        return self.read().strip()

    @property
    def firmware_revision(self) -> str:
        """Get the firmware revision (U2)."""
        self.write("U2X")
        return self.read().strip()
