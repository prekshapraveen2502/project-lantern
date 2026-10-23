"""Run statistics for the Part 0 scale-up test (brief Part 0, item 5).

For each unit of work (a ticker, a deal's CS1 stage, a web page) this records wall time, how many
units it covered (filings, pages, records), status (ok / failed, with the error), and peak memory.
Measured the same way as CS1 Part 10 (reports/benchmarks.md): seconds, peak RSS in MiB, failures.

Memory: a background thread samples the resident memory of this process plus all its child
processes every 50 ms (psutil), so the CS1 scripts run as subprocesses (parse_text, layout,
Docling, Tesseract) are included. Summing processes can double-count shared pages, and a peak
shorter than 50 ms can be missed; both are stated limitations.

Failures do not stop the other items: each item's exception is recorded, and after the last item
finish() writes the CSV and then raises if anything failed, so the DVC stage still fails loudly.

Timings vary between runs, so they are written OUTSIDE the stage outputs (default
data/bench/cs2/<stage>.csv, appended per run), as CS1 Part 10 did with data/bench/. Stage outputs
stay reproducible.
"""
from __future__ import annotations

import csv
import platform
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import psutil

MIB = 2 ** 20
FIELDS = ["run_at", "stage", "item", "unit", "units", "seconds", "s_per_unit", "peak_rss_mib",
          "status", "error", "machine"]


class PeakRSS:
    """Peak resident memory (bytes) of this process + all descendants, sampled every `interval` s."""

    def __init__(self, interval: float = 0.05):
        self.interval, self.peak = interval, 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _sample(self) -> int:
        me = psutil.Process()
        total = me.memory_info().rss
        for child in me.children(recursive=True):
            try:
                total += child.memory_info().rss
            except psutil.Error:            # the child exited between listing and reading
                pass
        return total

    def _run(self) -> None:
        while not self._stop.is_set():
            self.peak = max(self.peak, self._sample())
            self._stop.wait(self.interval)

    def __enter__(self) -> "PeakRSS":
        self.peak = self._sample()
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join()
        self.peak = max(self.peak, self._sample())


class RunStats:
    def __init__(self, stage: str, path: Path | str | None = None):
        self.stage = stage
        self.path = Path(path) if path else Path("data/bench/cs2") / f"{stage}.csv"
        self.run_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.machine = f"{platform.system()} {platform.machine()} | {psutil.cpu_count(logical=True)} threads | " \
                       f"{round(psutil.virtual_memory().total / 2**30, 1)} GiB | Python {platform.python_version()}"
        self.rows: list[dict] = []

    @contextmanager
    def item(self, name: str, unit: str):
        """with stats.item("AAPL", "filings") as r: ...; r["units"] = 2
        An exception inside the block is recorded and swallowed, so the next item still runs."""
        rec = {"run_at": self.run_at, "stage": self.stage, "item": name, "unit": unit, "units": 0,
               "seconds": None, "s_per_unit": None, "peak_rss_mib": None, "status": "ok", "error": "",
               "machine": self.machine}
        t0 = time.perf_counter()
        with PeakRSS() as pk:
            try:
                yield rec
            except Exception as e:                          # noqa: BLE001 - recorded, re-raised in finish()
                rec["status"], rec["error"] = "failed", f"{type(e).__name__}: {e}"[:300]
                print(f"[ERROR] {self.stage} {name}: {rec['error']}")
        rec["seconds"] = round(time.perf_counter() - t0, 3)
        rec["s_per_unit"] = round(rec["seconds"] / rec["units"], 3) if rec["units"] else None
        rec["peak_rss_mib"] = round(pk.peak / MIB, 1)
        self.rows.append(rec)
        print(f"[STATS] {self.stage} {name}: {rec['seconds']} s, {rec['units']} {unit}, "
              f"peak {rec['peak_rss_mib']} MiB, {rec['status']}")

    def finish(self) -> None:
        """Append this run's rows to the CSV, then fail if any item failed."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new = not self.path.exists()
        with open(self.path, "a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
            if new:
                w.writeheader()
            w.writerows(self.rows)
        failed = [r["item"] for r in self.rows if r["status"] != "ok"]
        print(f"[STATS] {self.stage}: {len(self.rows)} items, {len(failed)} failed -> {self.path}")
        if failed:
            raise SystemExit(f"{self.stage}: {len(failed)} item(s) failed: {failed} (see {self.path})")
