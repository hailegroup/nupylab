"""Measure the fixed time penalty of loading + arming + tearing down a Biologic
PEIS technique, excluding the time spent actually sweeping the frequencies.

Three independent estimates are produced:

  1. Host-side call timing -- how long each blocking ``BL_*`` DLL call takes
     (parameter marshalling, USB round trip, firmware ack).
  2. Arm latency -- wall-clock gap between ``BL_StartChannel`` returning and
     the channel actually reporting KBIO_STATE_RUN.
  3. Intercept of a total-time vs. number-of-frequencies regression, which
     needs no assumption about what the firmware does per point.

Run with --params-only to time just the ctypes/DLL parameter marshalling; that
mode needs the EC-Lab DLL present but no instrument attached.

Example:
    python -m nupylab.utilities.biologic_benchmark --port USB0 \
        --model SP200 --channel 0 --repeats 10 --points 1,2,4,8,16
"""
from __future__ import annotations

import argparse
import statistics
from time import perf_counter, sleep
from typing import Callable, Dict, List, Optional

import numpy as np

from nupylab.drivers.biologic import OCV, PEIS, BiologicPotentiostat

# Matches nupylab.instruments.ac_potentiostat.biologic.PEIS_DICT, minus the
# frequency settings, which the benchmark varies.
PEIS_BASE = {
    "initial_voltage_step": 0.0,
    "duration_step": 0.0,          # no pre-EIS hold: that is user time, not overhead
    "vs_initial": False,
    "logarithmic_spacing": True,
    "amplitude_voltage": 0.01,
    "average_n_times": 1,
    "wait_for_steady": 1.0,
    "drift_correction": False,
    "record_every_dt": 0.0,
    "record_every_di": 0.0,
    "i_range": "KBIO_IRANGE_AUTO",
    "e_range": "KBIO_ERANGE_2_5",
    "bandwidth": "KBIO_BW_5",
}


def time_call(fn: Callable, *args, **kwargs):
    """Return (elapsed_seconds, result) for a single call."""
    t0 = perf_counter()
    result = fn(*args, **kwargs)
    return perf_counter() - t0, result


def build_peis(fmax: float, fmin: float, n_points: int) -> PEIS:
    """Build a PEIS technique object with an explicit frequency count."""
    return PEIS(
        initial_frequency=fmax,
        final_frequency=fmin,
        frequency_number=n_points,
        **PEIS_BASE,
    )


def measure_poll_cost(bio: BiologicPotentiostat, channel: int, n: int = 50) -> float:
    """Median duration of one BL_GetCurrentValues round trip.

    This is the resolution floor for every latency measured by polling.
    """
    samples = []
    for _ in range(n):
        dt, _ = time_call(bio.get_current_values, channel)
        samples.append(dt)
    return statistics.median(samples)


def run_cycle(
    bio: BiologicPotentiostat,
    channel: int,
    technique: PEIS,
    ocv: Optional[OCV] = None,
    arm_timeout: float = 5.0,
    run_timeout: float = 300.0,
) -> Dict[str, float]:
    """Load, start, wait for completion, and stop one PEIS technique.

    If ``ocv`` is given, the channel is first put into a running OCV and the
    switch is timed the way nupylab actually does it (stop -> load -> start),
    which is the realistic cost inside ``Biologic.get_data``.
    """
    out: Dict[str, float] = {}

    if ocv is not None:
        bio.load_technique(channel, ocv, first=True, last=True)
        bio.start_channel(channel)
        # Let OCV settle into RUN so the stop below is a real interrupt.
        deadline = perf_counter() + arm_timeout
        while perf_counter() < deadline:
            if bio.get_current_values(channel)["State"] == 1:
                break
        out["t_stop_running"], _ = time_call(bio.stop_channel, channel)

    t_begin = perf_counter()
    out["t_load_technique"], _ = time_call(
        bio.load_technique, channel, technique, True, True
    )
    out["t_start_channel"], _ = time_call(bio.start_channel, channel)
    t_start_returned = perf_counter()

    # --- arm latency: BL_StartChannel returned, but is the channel running? ---
    saw_run = False
    deadline = t_start_returned + arm_timeout
    while perf_counter() < deadline:
        if bio.get_current_values(channel)["State"] == 1:
            saw_run = True
            break
    t_running = perf_counter()
    out["t_arm"] = (t_running - t_start_returned) if saw_run else float("nan")

    # --- run to completion, tracking the instrument's own clock ---
    max_elapsed = 0.0
    last_point_t = float("nan")
    deadline = perf_counter() + run_timeout
    while perf_counter() < deadline:
        cv = bio.get_current_values(channel)
        max_elapsed = max(max_elapsed, cv["ElapsedTime"])
        data = bio.get_data(channel)
        if data is not None and "t" in data.data_field_names:
            t_vals = getattr(data, "t")
            if len(t_vals):
                last_point_t = float(t_vals[-1])
        if cv["State"] == 0:
            break
    t_done = perf_counter()

    out["t_stop_channel"], _ = time_call(bio.stop_channel, channel)
    t_end = perf_counter()

    out["t_total_host"] = t_end - t_begin
    out["t_instrument_elapsed"] = max_elapsed
    out["t_last_point"] = last_point_t
    # Everything the host spent that the instrument did not report as run time.
    out["t_overhead"] = out["t_total_host"] - max_elapsed
    return out


def summarize(label: str, values: List[float]) -> str:
    clean = [v for v in values if v == v]  # drop NaN
    if not clean:
        return f"{label:<26} (not observed)"
    return (
        f"{label:<26} median {statistics.median(clean) * 1e3:9.2f} ms   "
        f"min {min(clean) * 1e3:9.2f}   max {max(clean) * 1e3:9.2f}   n={len(clean)}"
    )


def params_only(model: str, port: str, eclib_path: Optional[str], reps: int) -> None:
    """Time parameter marshalling alone; no instrument needed."""
    bio = BiologicPotentiostat(model, port, eclib_path)
    build, marshal = [], []
    for _ in range(reps):
        dt_build, tech = time_call(build_peis, 1.0e5, 1.0, 51)
        # c_args is cached on the technique, so this is the one-time cost of
        # the 19 BL_Define*Parameter calls for a PEIS.
        dt_marshal, _ = time_call(tech.c_args, bio)
        build.append(dt_build)
        marshal.append(dt_marshal)
    print(summarize("PEIS object construction", build))
    print(summarize("c_args (19 DLL params)", marshal))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port", default="USB0")
    p.add_argument("--model", default="SP200")
    p.add_argument("--channel", type=int, default=0)
    p.add_argument("--eclib-path", default=None)
    p.add_argument("--repeats", type=int, default=10)
    p.add_argument(
        "--points",
        default="1,2,4,8,16",
        help="comma-separated frequency_number values for the regression",
    )
    p.add_argument("--fmax", type=float, default=200.0e3)
    p.add_argument(
        "--fmin",
        type=float,
        default=100.0e3,
        help="keep this high so per-point 1/f time stays negligible",
    )
    p.add_argument(
        "--from-ocv",
        action="store_true",
        help="time the realistic OCV->PEIS switch instead of loading onto an idle channel",
    )
    p.add_argument("--params-only", action="store_true")
    args = p.parse_args()

    if args.params_only:
        params_only(args.model, args.port, args.eclib_path, args.repeats)
        return

    bio = BiologicPotentiostat(args.model, args.port, args.eclib_path)

    print("=== one-time session cost ===")
    dt_connect, _ = time_call(bio.connect)
    print(f"{'BL_Connect':<26} {dt_connect * 1e3:9.2f} ms")
    chan_bool = [0] * 16
    chan_bool[args.channel] = 1
    dt_fw, _ = time_call(bio.load_firmware, chan_bool)
    print(f"{'BL_LoadFirmware(force)':<26} {dt_fw * 1e3:9.2f} ms")
    dt_fw2, _ = time_call(bio.load_firmware, chan_bool, False)
    print(f"{'BL_LoadFirmware(no force)':<26} {dt_fw2 * 1e3:9.2f} ms")

    poll_cost = measure_poll_cost(bio, args.channel)
    print(f"{'BL_GetCurrentValues':<26} {poll_cost * 1e3:9.2f} ms  (latency resolution)")

    ocv = (
        OCV(duration=3600, record_every_de=1.0, record_every_dt=1.0,
            e_range="KBIO_ERANGE_AUTO")
        if args.from_ocv
        else None
    )

    try:
        point_counts = [int(x) for x in args.points.split(",")]
        totals: Dict[int, List[float]] = {}

        for n_points in point_counts:
            print(f"\n=== frequency_number = {n_points} "
                  f"({args.fmax:g} -> {args.fmin:g} Hz) ===")
            runs: List[Dict[str, float]] = []
            for _ in range(args.repeats):
                technique = build_peis(args.fmax, args.fmin, n_points)
                runs.append(run_cycle(bio, args.channel, technique, ocv=ocv))
                sleep(0.2)  # let the channel settle between cycles

            for key in (
                "t_stop_running",
                "t_load_technique",
                "t_start_channel",
                "t_arm",
                "t_stop_channel",
                "t_total_host",
                "t_instrument_elapsed",
                "t_overhead",
            ):
                if key in runs[0]:
                    print(summarize(key, [r[key] for r in runs]))
            totals[n_points] = [r["t_total_host"] for r in runs]

        if len(point_counts) >= 2:
            xs = np.array(point_counts, dtype=float)
            ys = np.array([statistics.median(totals[n]) for n in point_counts])
            slope, intercept = np.polyfit(xs, ys, 1)
            print("\n=== regression: total host time vs. frequency count ===")
            print(f"  per-point cost   {slope * 1e3:9.2f} ms/frequency")
            print(f"  fixed overhead   {intercept * 1e3:9.2f} ms  <-- the time penalty")
    finally:
        try:
            bio.stop_channel(args.channel)
        except Exception:  # channel may already be stopped
            pass
        bio.disconnect()


if __name__ == "__main__":
    main()
