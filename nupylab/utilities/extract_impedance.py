"""Extract impedance data from NUPyLab exports into ZView format.

Station exports write every instrument reading to a single file, so rows where the
potentiostat did not return a measurement are filled with NaN. This module pulls out
the rows that do contain impedance data and writes them as f, Zreal, -Zimaginary.

Columns are located by name rather than position so the same lookup works across
stations with different exports.

Run directly to convert an export that has already been written:

    python extract_impedance.py DATA_2026-04-01_1.csv
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional, Sequence, Tuple, Union

import pandas as pd

log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

FREQUENCY_KEYWORDS: Tuple[str, ...] = ("frequency",)
ZRE_KEYWORDS: Tuple[str, ...] = ("z_re", "zreal", "z real")
ZIM_KEYWORDS: Tuple[str, ...] = ("-z_im", "z_im", "zimaginary", "z imaginary")

ZVIEW_HEADER: Tuple[str, ...] = ("f", "Zreal", "-Zimaginary")
ZVIEW_SUFFIX: str = "_ZView"


def find_column(
    columns: Sequence[str], keywords: Sequence[str]
) -> Optional[str]:
    """Find the first column whose name contains one of `keywords`.

    Args:
        columns: column names to search.
        keywords: lowercase substrings to match against.

    Returns:
        the matching column name, or None if nothing matches.
    """
    for column in columns:
        lowered = column.lower()
        if any(keyword in lowered for keyword in keywords):
            return column
    return None


def impedance_columns(
    columns: Sequence[str],
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Locate the frequency, real, and imaginary impedance columns.

    Args:
        columns: column names to search, typically a procedure's `DATA_COLUMNS`.

    Returns:
        tuple of frequency, Z real, and negative Z imaginary column names. Entries
        are None where no match was found.
    """
    return (
        find_column(columns, FREQUENCY_KEYWORDS),
        find_column(columns, ZRE_KEYWORDS),
        find_column(columns, ZIM_KEYWORDS),
    )


def zview_path(data_path: Union[str, Path]) -> Path:
    """Build the ZView export path that corresponds to a data file.

    Args:
        data_path: path of the main export.

    Returns:
        path with the ZView suffix appended to the file name.
    """
    data_path = Path(data_path)
    return data_path.with_name(data_path.stem + ZVIEW_SUFFIX + data_path.suffix)


def _header_offset(data_path: Path) -> int:
    """Count the comment lines written above the column header."""
    with open(data_path, "r") as f:
        for i, line in enumerate(f):
            if not line.startswith("#"):
                return i
    return 0


def extract_impedance(
    data_path: Union[str, Path], output_path: Optional[Union[str, Path]] = None
) -> Path:
    """Write the impedance rows of an export to a separate ZView file.

    Args:
        data_path: path of the export to read.
        output_path: where to write the result. Defaults to the input file name with
            the ZView suffix appended.

    Returns:
        path of the file that was written.

    Raises:
        ValueError if the export has no impedance columns.
    """
    data_path = Path(data_path)
    if output_path is None:
        output_path = zview_path(data_path)
    output_path = Path(output_path)

    data = pd.read_csv(data_path, skiprows=_header_offset(data_path))
    frequency, z_re, z_im = impedance_columns(data.columns)
    if not (frequency and z_re and z_im):
        raise ValueError(
            f"No impedance columns found in {data_path.name}. Columns present: "
            f"{list(data.columns)}"
        )

    impedance = data[[frequency, z_re, z_im]].dropna()
    impedance.columns = list(ZVIEW_HEADER)
    impedance.to_csv(output_path, index=False)
    log.info("Wrote %d impedance rows to %s", len(impedance), output_path)
    return output_path


def main() -> None:
    """Convert exports passed on the command line."""
    parser = argparse.ArgumentParser(
        description="Extract impedance data from a NUPyLab export into ZView format."
    )
    parser.add_argument("data_files", nargs="+", type=Path, help="exports to convert")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="output path, only valid with a single input file",
    )
    args = parser.parse_args()

    if args.output is not None and len(args.data_files) > 1:
        parser.error("--output cannot be used with more than one input file")

    for data_file in args.data_files:
        if not data_file.exists():
            print(f"File not found: {data_file}")
            sys.exit(1)
        try:
            written = extract_impedance(data_file, args.output)
        except ValueError as e:
            print(e)
            sys.exit(1)
        print(f"Wrote {written}")


if __name__ == "__main__":
    main()
