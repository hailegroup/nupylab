"""MFC flow calibrations: what a setpoint in percent actually flows, in sccm.

An Aera MFC is set and read in percent of its own range, and the flow that
percent corresponds to depends on the gas. Calibrating one means flowing
through it at known setpoints and fitting a straight line to what came out,

.. math::

    Q = m p + b

with `p` the setpoint in percent, `Q` the flow in sccm, `m` a slope in
sccm/percent and `b` an intercept in sccm. That is the same shape as the pO2
sensor's calibration and is kept in the same way, so the two read alike.

Every calibration the station has taken lives in one file,
`mfc_calibrations.csv` beside this module, a row per calibration of its date,
who took it, which MFC, the gas it was actually taken in, the gas it is being
used for, and the two coefficients.

The two gas columns are deliberate. A calibration taken in synthetic air,
roughly 20 percent O2 in nitrogen, is close enough to use for 10 percent O2 in
nitrogen, and recording both says which it is: `calibration_gas` is what
really went through the MFC, `gas` is what the row is being used for. A row
where they differ is a substitution, and :attr:`MFCCalibration.substituted`
says so, so that it can be seen rather than forgotten.

Gases are offered from a list which starts at :data:`DEFAULT_GASES`, grows with
every gas named in the calibration file, and grows again with any the user
adds, which are kept in `mfc_gases.json` beside this module. Like the
calibrations, both files belong to the station rather than to the source, so
neither is kept in version control.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import re
from datetime import date, datetime
from typing import List, NamedTuple, Optional, Sequence, Tuple

from nupylab.utilities import NupylabError

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

#: Where this station keeps every MFC calibration it has taken, beside this
#: module: one row per calibration, in the order they were added.
CALIBRATION_FILE: str = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "mfc_calibrations.csv"
)

#: Where gases the user has added are kept, beside this module.
GAS_FILE: str = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "mfc_gases.json"
)

#: How old a calibration may be before the GUI says so, in days. Saying so is
#: all it does: an old calibration still sets a flow, just less accurately.
CALIBRATION_WARNING_DAYS: int = 365

#: Columns of the calibration file, in order.
CALIBRATION_FIELDS: Tuple[str, ...] = (
    "date",
    "calibrator",
    "mfc",
    "calibration_gas",
    "gas",
    "m",
    "b",
)

#: Gases offered before anything has been calibrated in them. The list also
#: holds every gas named in the calibration file and every gas the user has
#: added, see :func:`gas_choices`.
DEFAULT_GASES: Tuple[str, ...] = (
    "Synthetic Air",
    "Argon",
    "Nitrogen",
    "10% O2 bal. Ar",
)


def match_key(text: Optional[str]) -> str:
    """Get the form of a name that matching compares.

    Gas and MFC names are typed by hand, so `argon` and `Argon ` are the same
    gas and a calibration recorded under either should be found by the other.

    Args:
        text: the name as written.

    Returns:
        The name folded for comparison, with surrounding and repeated spaces
        taken out.
    """
    return re.sub(r"\s+", " ", str(text or "").strip()).casefold()


class MFCCalibration(NamedTuple):
    """One MFC flow calibration: its coefficients and where it came from.

    The coefficients are those of the flow against the setpoint,
    `Q = m p + b`: `m` is a slope in sccm per percent and `b` an intercept in
    sccm.
    """

    m: float
    b: float
    mfc: str = ""
    calibration_gas: str = ""
    gas: str = ""
    calibrated_by: str = ""
    date: Optional[date] = None

    # What it converts #

    def flow(self, percent: float) -> float:
        """Get the flow a setpoint in percent corresponds to.

        Args:
            percent: setpoint as a percentage of the MFC range.

        Returns:
            Flow in sccm.
        """
        return self.m * float(percent) + self.b

    def percent(self, flow: float) -> float:
        """Get the setpoint in percent that would give a flow.

        Args:
            flow: wanted flow in sccm.

        Returns:
            Setpoint as a percentage of the MFC range. Zero if the slope is
            zero, which is a calibration that says nothing about the MFC.
        """
        if not self.m:
            log.warning(
                "MFC calibration %s has a slope of zero, so no setpoint "
                "corresponds to a flow",
                self.describe(),
            )
            return 0.0
        return (float(flow) - self.b) / self.m

    # Where it came from #

    @property
    def date_text(self) -> str:
        """Get the calibration date as `November 21, 2026`."""
        return self.date.strftime("%B %d, %Y") if self.date else "unknown date"

    @property
    def short_date(self) -> str:
        """Get the calibration date as `2026-11-21`.

        For the inputs panel, which carries one of these per MFC and has no
        room for the long form.
        """
        return self.date.strftime("%Y-%m-%d") if self.date else "an unknown date"

    @property
    def substituted(self) -> bool:
        """Get whether this row is used for a gas other than its own.

        A calibration taken in one gas and used for another is a substitution,
        which is allowed and recorded rather than hidden.
        """
        if not self.calibration_gas or not self.gas:
            return False
        return match_key(self.calibration_gas) != match_key(self.gas)

    def describe(self) -> str:
        """Get a one-line summary of when, by whom, on what, and in what gas."""
        in_gas = self.calibration_gas or "unknown gas"
        if self.substituted:
            in_gas = "{}, used for {}".format(self.calibration_gas, self.gas)
        return "{}, {}, by {}, in {}".format(
            self.mfc or "unknown MFC",
            self.date_text,
            self.calibrated_by or "unknown",
            in_gas,
        )

    def older_than(self, days: int = CALIBRATION_WARNING_DAYS) -> bool:
        """Get whether the calibration was taken more than `days` ago.

        Args:
            days: how long a calibration is trusted for.

        Returns:
            Whether it is older than that. A calibration with no date is not
            taken to be stale, since nothing is known about when it was taken.
        """
        if self.date is None:
            return False
        return (date.today() - self.date).days > days

    def is_for(self, mfc: str, gas: str) -> bool:
        """Get whether this calibration is the one for an MFC and a gas.

        Args:
            mfc: name of the MFC.
            gas: gas the MFC is flowing.

        Returns:
            Whether both match, ignoring case and stray spaces.
        """
        return (
            match_key(self.mfc) == match_key(mfc)
            and match_key(self.gas) == match_key(gas)
        )

    def as_row(self) -> List[str]:
        """Get this calibration as a row of the calibration file."""
        return [
            self.date.strftime("%Y%m%d") if self.date else "",
            self.calibrated_by,
            self.mfc,
            self.calibration_gas,
            self.gas,
            repr(self.m),
            repr(self.b),
        ]


def _parse_date(text: str) -> date:
    """Read a calibration date written as YYYYMMDD.

    Args:
        text: the date as written, where dashes and slashes are ignored.

    Returns:
        The date.

    Raises:
        ValueError: if it is not a date in that form.
    """
    return datetime.strptime(re.sub(r"[-/]", "", text.strip()), "%Y%m%d").date()


def _parse_row(row: Sequence[str], path: str, number: int) -> MFCCalibration:
    """Read one row of a calibration file.

    Args:
        row: the row's cells, as :data:`CALIBRATION_FIELDS` orders them.
        path: path of the file, for the error message.
        number: row number in the file, for the error message.

    Returns:
        The calibration the row holds.

    Raises:
        NupylabError: if the row is short, or its date or coefficients cannot
            be read.
    """
    if len(row) < len(CALIBRATION_FIELDS):
        raise NupylabError(
            "MFC calibration file {}, row {}: expected {} columns ({}), "
            "found {}.".format(
                path,
                number,
                len(CALIBRATION_FIELDS),
                ", ".join(CALIBRATION_FIELDS),
                len(row),
            )
        )
    taken, calibrator, mfc, calibration_gas, gas, m, b = (
        cell.strip() for cell in row[: len(CALIBRATION_FIELDS)]
    )
    try:
        when = _parse_date(taken)
    except ValueError:
        raise NupylabError(
            "MFC calibration file {}, row {}: `{}` is not a date as "
            "YYYYMMDD.".format(path, number, taken)
        )
    try:
        coefficients = (float(m), float(b))
    except ValueError:
        raise NupylabError(
            "MFC calibration file {}, row {}: `{}` and `{}` are not both "
            "numbers.".format(path, number, m, b)
        )
    return MFCCalibration(
        m=coefficients[0],
        b=coefficients[1],
        mfc=mfc,
        calibration_gas=calibration_gas,
        gas=gas,
        calibrated_by=calibrator,
        date=when,
    )


def load_calibrations(path: str = CALIBRATION_FILE) -> List[MFCCalibration]:
    """Read every calibration in a calibration file, newest first.

    A first row whose first cell is not a date is taken for a header and
    skipped, as are blank rows.

    Args:
        path: path to the calibration file, this station's by default.

    Returns:
        The calibrations, newest first, and among those taken the same day the
        one added last first. Empty if the file does not exist, which is how a
        station that has not taken one yet reads.

    Raises:
        NupylabError: if the file cannot be read, or holds a row that cannot.
            One bad row stops the whole file: a calibration quietly dropped is
            worse than one that has to be fixed by hand.
    """
    if not os.path.exists(path):
        return []
    calibrations: List[MFCCalibration] = []
    try:
        with open(path, "r", newline="") as calibration_file:
            for number, row in enumerate(csv.reader(calibration_file), start=1):
                if not row or not any(cell.strip() for cell in row):
                    continue
                if number == 1:
                    try:
                        _parse_date(row[0])
                    except ValueError:
                        continue  # A header, not a calibration
                calibrations.append(_parse_row(row, path, number))
    except OSError as e:
        raise NupylabError(
            "Could not open MFC calibration file {}: {}".format(path, e)
        )
    # Newest first, and among calibrations taken the same day, the last one
    # added first, since that is the one that corrected the others
    numbered = list(enumerate(calibrations))
    numbered.sort(key=lambda pair: (pair[1].date, pair[0]), reverse=True)
    return [calibration for _, calibration in numbered]


def calibrations_for(
    calibrations: Sequence[MFCCalibration], mfc: str, gas: str
) -> List[MFCCalibration]:
    """Pick out the calibrations of one MFC in one gas.

    Args:
        calibrations: calibrations to search, newest first.
        mfc: name of the MFC.
        gas: gas the MFC is flowing.

    Returns:
        Those that are for that pair, in the order given.
    """
    return [c for c in calibrations if c.is_for(mfc, gas)]


def latest_calibration(
    path: str = CALIBRATION_FILE,
    mfc: Optional[str] = None,
    gas: Optional[str] = None,
) -> Optional[MFCCalibration]:
    """Read the newest calibration of one MFC in one gas.

    Args:
        path: path to the calibration file, this station's by default.
        mfc: name of the MFC, or None for any.
        gas: gas the MFC is flowing, or None for any.

    Returns:
        The newest calibration for that pair, or `None` if the file holds
        none.

    Raises:
        NupylabError: if the file cannot be read.
    """
    calibrations = load_calibrations(path)
    if mfc is not None and gas is not None:
        calibrations = calibrations_for(calibrations, mfc, gas)
    return calibrations[0] if calibrations else None


#: Separates the parts of a selection. Illegal in a Windows path, and in none
#: of the gas or MFC names the GUI offers.
SELECTION_SEPARATOR: str = "|"


def encode_selection(
    path: str = CALIBRATION_FILE,
    mfc: str = "",
    gas: str = "",
    when: Optional[date] = None,
) -> str:
    """Write down which calibration is in use, as one string.

    A GUI parameter holds a single value, so the file, the MFC and gas it is
    searched for, and the calibration chosen within it travel together.

    Args:
        path: path of the calibration file.
        mfc: name of the MFC.
        gas: gas the MFC is flowing.
        when: date of the chosen calibration. `None` means whichever is newest
            for that MFC and gas, which is what a station wants unless it says
            otherwise.

    Returns:
        The selection, e.g. `C:/.../mfc_calibrations.csv|MFC 1|Argon|20261121`.
    """
    return SELECTION_SEPARATOR.join(
        (
            path,
            str(mfc or ""),
            str(gas or ""),
            when.strftime("%Y%m%d") if when else "",
        )
    )


def decode_selection(value: str) -> Tuple[str, str, str, Optional[date]]:
    """Read back a selection written by :func:`encode_selection`.

    Args:
        value: the selection, or a bare path.

    Returns:
        The calibration file, the MFC, the gas, and the date chosen in it if
        one was. A bare path gives an empty MFC and gas and no date.
    """
    text = (value or "").strip()
    parts = text.split(SELECTION_SEPARATOR)
    if len(parts) < 4:
        return text, "", "", None
    # The path is whatever is left once the three known fields are taken off
    # the end, so a path that holds a separator still reads back whole
    path = SELECTION_SEPARATOR.join(parts[:-3])
    mfc, gas, chosen = parts[-3], parts[-2], parts[-1]
    if not chosen:
        return path, mfc, gas, None
    try:
        return path, mfc, gas, _parse_date(chosen)
    except ValueError:
        return path, mfc, gas, None


def load_selection(value: str) -> Optional[MFCCalibration]:
    """Read the calibration a selection points at.

    Args:
        value: the selection, see :func:`encode_selection`.

    Returns:
        The chosen calibration, the newest for its MFC and gas if none was
        chosen, or `None` if the file holds none for that pair. A chosen
        calibration that is no longer in the file, because it was edited out,
        also gives the newest for the pair.

    Raises:
        NupylabError: if the file cannot be read.
    """
    path, mfc, gas, chosen = decode_selection(value)
    if not path:
        return None
    calibrations = load_calibrations(path)
    if mfc or gas:
        calibrations = calibrations_for(calibrations, mfc, gas)
    if not calibrations:
        return None
    if chosen is not None:
        for calibration in calibrations:
            if calibration.date == chosen:
                return calibration
        log.warning(
            "No %s calibration in %s dated %s for %s, using the newest instead",
            mfc or "MFC",
            path,
            chosen.strftime("%Y%m%d"),
            gas or "that gas",
        )
    return calibrations[0]


def create_calibration_file(path: str = CALIBRATION_FILE) -> str:
    """Start an empty calibration file, with its header and no calibrations.

    For a station that has not recorded one yet. An existing file is left
    exactly as it is, since it may hold calibrations.

    Args:
        path: path to the calibration file, this station's by default.

    Returns:
        The path, whether it was created here or was already there.

    Raises:
        NupylabError: if the file cannot be written.
    """
    if os.path.exists(path):
        return path
    try:
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(path, "w", newline="") as calibration_file:
            csv.writer(calibration_file).writerow(CALIBRATION_FIELDS)
    except OSError as e:
        raise NupylabError(
            "Could not create MFC calibration file {}: {}".format(path, e)
        )
    log.info("Started an MFC calibration file at %s", path)
    return path


def append_calibration(
    calibration: MFCCalibration, path: str = CALIBRATION_FILE
) -> None:
    """Add one calibration to a calibration file.

    Creates the file, with its header, if it is not there yet.

    Args:
        calibration: the calibration to record. It needs a date.
        path: path to the calibration file, this station's by default.

    Raises:
        NupylabError: if the calibration has no date, or the file cannot be
            written.
    """
    if calibration.date is None:
        raise NupylabError("An MFC calibration needs the date it was taken.")
    exists = os.path.exists(path) and os.path.getsize(path) > 0
    try:
        with open(path, "a", newline="") as calibration_file:
            writer = csv.writer(calibration_file)
            if not exists:
                writer.writerow(CALIBRATION_FIELDS)
            writer.writerow(calibration.as_row())
    except OSError as e:
        raise NupylabError(
            "Could not write MFC calibration file {}: {}".format(path, e)
        )


# The gases on offer #


def load_gases(path: str = GAS_FILE) -> List[str]:
    """Read the gases the user has added.

    Args:
        path: path to the gas file, this station's by default.

    Returns:
        The gases, in the order they were added. Empty if there is no file or
        it cannot be read: the gas list is a convenience, so a broken file is
        worth a warning rather than a failure to start.
    """
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as gas_file:
            gases = json.load(gas_file)
    except (OSError, ValueError) as e:
        log.warning("Could not read %s, offering the usual gases: %s", path, e)
        return []
    if not isinstance(gases, list):
        return []
    return [str(gas).strip() for gas in gases if str(gas).strip()]


def save_gases(gases: Sequence[str], path: str = GAS_FILE) -> None:
    """Write the gases the user has added.

    Args:
        gases: the gases to remember.
        path: path to the gas file, this station's by default.
    """
    try:
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(path, "w", encoding="utf-8") as gas_file:
            json.dump(list(gases), gas_file, indent=2)
    except OSError as e:
        log.warning("Could not write %s: %s", path, e)


def add_gas(gas: str, path: str = GAS_FILE) -> List[str]:
    """Remember one more gas, so that it is offered from now on.

    Args:
        gas: name of the gas.
        path: path to the gas file, this station's by default.

    Returns:
        Every gas now on offer, see :func:`gas_choices`.
    """
    gas = str(gas or "").strip()
    added = load_gases(path)
    known = {match_key(name) for name in list(DEFAULT_GASES) + added}
    if gas and match_key(gas) not in known:
        added.append(gas)
        save_gases(added, path)
        log.info("Added %s to the gases on offer", gas)
    return gas_choices(path=path)


def gas_choices(
    calibration_path: str = CALIBRATION_FILE, path: str = GAS_FILE
) -> List[str]:
    """Get every gas to offer, in the order they should be shown.

    Args:
        calibration_path: calibration file whose gases to include.
        path: path to the gas file, this station's by default.

    Returns:
        :data:`DEFAULT_GASES` first, then the gases the user has added, then
        any other gas named in the calibration file, each once.
    """
    recorded: List[str] = []
    try:
        for calibration in load_calibrations(calibration_path):
            recorded.append(calibration.gas)
            recorded.append(calibration.calibration_gas)
    except NupylabError as e:
        log.warning("Could not read the gases already calibrated: %s", e)
    choices: List[str] = []
    seen = set()
    for gas in list(DEFAULT_GASES) + load_gases(path) + recorded:
        gas = str(gas or "").strip()
        key = match_key(gas)
        if gas and key not in seen:
            seen.add(key)
            choices.append(gas)
    return choices
