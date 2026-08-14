#!/usr/bin/env python3
"""Time the sampling paths so the tick loop stays cheap."""

from __future__ import annotations

import pathlib
import statistics
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from vitals.sampler import Sampler  # noqa: E402


def time_calls(label: str, fn, rounds: int = 6, pause: float = 0.25) -> None:
    timings = []
    for _ in range(rounds):
        time.sleep(pause)
        started = time.perf_counter()
        fn()
        timings.append((time.perf_counter() - started) * 1000)
    print(f"{label:<38} median {statistics.median(timings):6.1f} ms   "
          f"max {max(timings):6.1f} ms")


def main() -> int:
    sampler = Sampler()
    sampler.sample()
    time.sleep(0.5)

    time_calls("metrics only (no processes)",
               lambda: sampler.sample(want_processes=False))

    sampler.include_kernel = False
    sampler.sample()
    time_calls("with processes, kernel hidden",
               lambda: sampler.sample(want_processes=True))

    sampler.include_kernel = True
    sampler.sample()
    time_calls("with processes, kernel included",
               lambda: sampler.sample(want_processes=True))

    snap = sampler.snapshot
    print(f"\nprocesses total {snap.proc_count}, listed {snap.proc_shown}, "
          f"threads {snap.thread_count}")
    sampler.include_kernel = False
    sampler.sample()
    print(f"kernel hidden -> listed {sampler.snapshot.proc_shown}")
    sampler.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
