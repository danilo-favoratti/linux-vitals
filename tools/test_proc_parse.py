#!/usr/bin/env python3
"""Cross-check the /proc fast path against psutil for a sample of processes."""

from __future__ import annotations

import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import psutil  # noqa: E402

from vitals.sampler import Sampler, _parse_stat  # noqa: E402


def check_parser() -> int:
    """Every readable process must parse, including odd comm values."""
    failures = 0
    checked = 0
    for proc in psutil.process_iter():
        try:
            with open(f"/proc/{proc.pid}/stat", "rb") as handle:
                raw = handle.read()
            comm, ppid, state, ticks, threads, rss = _parse_stat(raw)
        except OSError:
            continue
        except (ValueError, IndexError) as exc:
            print(f"  PARSE FAIL pid={proc.pid}: {exc}")
            failures += 1
            continue
        checked += 1
        try:
            if proc.ppid() != ppid:
                print(f"  ppid mismatch pid={proc.pid}: {ppid} vs {proc.ppid()}")
                failures += 1
            if proc.num_threads() != threads:
                print(f"  threads mismatch pid={proc.pid}: "
                      f"{threads} vs {proc.num_threads()}")
                failures += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    print(f"parsed {checked} processes, {failures} mismatches")
    return failures


def check_values() -> int:
    """RSS and CPU% should track psutil closely."""
    sampler = Sampler()
    sampler.include_kernel = True
    sampler.sample()
    time.sleep(1.5)
    snapshot = sampler.sample()
    rows = {row.pid: row for row in snapshot.processes}

    rss_bad = 0
    compared = 0
    for pid, row in list(rows.items())[:400]:
        try:
            expected = psutil.Process(pid).memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        compared += 1
        if expected and abs(row.rss - expected) / expected > 0.25:
            rss_bad += 1
            if rss_bad < 4:
                print(f"  rss off pid={pid} {row.name}: "
                      f"{row.rss} vs psutil {expected}")
    print(f"compared rss for {compared} processes, {rss_bad} outliers")

    busiest = sorted(snapshot.processes, key=lambda r: r.cpu, reverse=True)[:5]
    print("busiest by /proc math:")
    for row in busiest:
        print(f"  {row.name:<28} pid={row.pid:<8} cpu={row.cpu:6.1f}% "
              f"rss={row.rss / 1024 / 1024:8.1f} MB thr={row.threads} "
              f"state={row.status}")
    total_cpu = sum(r.cpu for r in snapshot.processes)
    print(f"sum of process cpu% = {total_cpu:.1f} "
          f"(system total was {snapshot.cpu_total * sampler.core_count:.1f})")
    sampler.shutdown()
    return rss_bad


def main() -> int:
    print("== parser cross-check ==")
    failures = check_parser()
    print("\n== value cross-check ==")
    failures += check_values()
    print("\nRESULT:", "OK" if failures == 0 else f"{failures} problems")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
