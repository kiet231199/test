#!/usr/bin/env python3
"""Measure media-check effort modes without adding runtime dependencies."""

import argparse
import ctypes
import json
import os
import statistics
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from media_checker.checker import check, normalize_metrics
from media_checker.compute_budget import EFFORT_LEVELS
from media_checker.descriptors import load_input
from media_checker.metrics import METRIC_HANDLERS
from media_checker.models import CheckRequest


SAMPLE_INTERVAL_SECONDS = 0.005


def _thread_count(process_id: int) -> Optional[int]:
    if os.name == "nt":
        return _windows_thread_count(process_id)

    task_path = Path("/proc") / str(process_id) / "task"

    try:
        return len(tuple(task_path.iterdir()))
    except OSError:
        return None


def _windows_thread_count(process_id: int) -> Optional[int]:
    from ctypes import wintypes

    class ThreadEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", wintypes.LONG),
            ("tpDeltaPri", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error = True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Thread32First.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ThreadEntry),
    ]
    kernel32.Thread32First.restype = wintypes.BOOL
    kernel32.Thread32Next.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ThreadEntry),
    ]
    kernel32.Thread32Next.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000004, 0)

    if snapshot == ctypes.c_void_p(-1).value:
        return None

    entry = ThreadEntry()
    entry.dwSize = ctypes.sizeof(entry)
    count = 0

    try:
        available = kernel32.Thread32First(
            snapshot,
            ctypes.byref(entry),
        )

        while available:
            if entry.th32OwnerProcessID == process_id:
                count += 1

            available = kernel32.Thread32Next(
                snapshot,
                ctypes.byref(entry),
            )
    finally:
        kernel32.CloseHandle(snapshot)

    return count


def _run_once(descriptors, metrics: List[str], effort: str) -> Dict[str, object]:
    stop_sampling = threading.Event()
    peak_threads = [0]

    def sample_threads() -> None:
        while not stop_sampling.wait(SAMPLE_INTERVAL_SECONDS):
            current_threads = _thread_count(os.getpid())

            if current_threads is not None:
                peak_threads[0] = max(peak_threads[0], current_threads)

    sampler = threading.Thread(
        target = sample_threads,
        name = "effort-benchmark-sampler",
        daemon = True,
    )
    sampler.start()
    started_wall = time.perf_counter()
    started_cpu = time.process_time()

    try:
        result = check(CheckRequest(
            input     = descriptors.input,
            reference = descriptors.reference,
            metrics   = tuple(metrics),
            effort    = effort,
        ))
    finally:
        cpu_seconds = time.process_time() - started_cpu
        wall_seconds = time.perf_counter() - started_wall
        stop_sampling.set()
        sampler.join()

    measured_threads = (
        max(1, peak_threads[0] - 1)
        if peak_threads[0]
        else None
    )
    return {
        "status"         : result.status,
        "wall_seconds"   : round(wall_seconds, 6),
        "cpu_seconds"    : round(cpu_seconds, 6),
        "average_cores"  : round(cpu_seconds / wall_seconds, 3),
        "peak_threads"   : measured_threads,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description = "Benchmark media-check native effort budgets",
    )
    parser.add_argument("descriptor", type = Path)
    parser.add_argument(
        "--metrics",
        nargs = "+",
        default = None,
    )
    parser.add_argument(
        "--repetitions",
        type = int,
        default = 3,
    )
    args = parser.parse_args()

    if args.repetitions <= 0:
        parser.error("--repetitions must be positive")

    descriptors = load_input(args.descriptor)
    metrics = list(normalize_metrics(
        args.metrics or tuple(METRIC_HANDLERS)
    ))
    runs = {
        effort : []
        for effort in EFFORT_LEVELS
    }  # type: Dict[str, List[Dict[str, object]]]

    _run_once(descriptors, metrics, "medium")

    for repetition in range(args.repetitions):
        offset = repetition % len(EFFORT_LEVELS)
        effort_order = (
            EFFORT_LEVELS[offset:]
            + EFFORT_LEVELS[:offset]
        )

        for effort in effort_order:
            runs[effort].append(
                _run_once(descriptors, metrics, effort)
            )

    report = {
        "descriptor"  : str(args.descriptor.resolve()),
        "metrics"     : metrics,
        "repetitions" : args.repetitions,
        "efforts"     : {},
    }

    for effort in EFFORT_LEVELS:
        effort_runs = runs[effort]
        report["efforts"][effort] = {
            "mean_wall_seconds" : round(statistics.mean(
                run["wall_seconds"] for run in effort_runs
            ), 6),
            "mean_cpu_seconds" : round(statistics.mean(
                run["cpu_seconds"] for run in effort_runs
            ), 6),
            "mean_average_cores" : round(statistics.mean(
                run["average_cores"] for run in effort_runs
            ), 3),
            "max_peak_threads" : max(
                run["peak_threads"] or 0 for run in effort_runs
            ) or None,
            "runs" : effort_runs,
        }

    print(json.dumps(report, indent = 2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
