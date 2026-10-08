"""Bounded throughput probing with automatic backoff, independent of message content."""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path


class AdaptiveRate:
    def __init__(self, status_path: Path, *, window: float = 60, stop_check=None):
        self.path = status_path
        self.window = window
        self.lock = threading.RLock()
        self.steps = [8, 12, 16, 20, 24, 32, 40]
        self.index = 0
        self.rate = 8.0
        self.best_rate = 8.0
        self.best_throughput = 0.0
        self.mode = "setup"
        self.phase_started = time.monotonic()
        self.next_request = 0.0
        self.blocked_until = 0.0
        self.sent = self.responses = self.errors = 0
        self.history = []
        self.plateaus = 0
        self.stop_check = stop_check
        self.last_backoff = None
        self.phase = "setup"

    def _write(self):
        payload = {
            "mode": self.mode,
            "phase": self.phase,
            "configured_requests_per_second": self.rate,
            "best_rate": self.best_rate,
            "best_measured_requests_per_second": self.best_throughput,
            "window_seconds": self.window,
            "history": self.history,
            "last_backoff": self.last_backoff,
        }
        temp = self.path.with_suffix(".json.tmp")
        descriptor = os.open(temp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(temp, self.path)

    def begin(self, phase: str = "work"):
        with self.lock:
            if self.mode == "setup" or (phase != self.phase and self.last_backoff is None):
                self.phase = phase
                self.index = 0
                self.rate = self.best_rate = 8.0
                self.best_throughput = 0.0
                self.plateaus = 0
                self.mode = "warming"
                self.phase_started = time.monotonic()
                self.sent = self.responses = self.errors = 0
                self._write()

    def _roll(self):
        elapsed = time.monotonic() - self.phase_started
        if self.mode not in {"warming", "probing"} or elapsed < self.window:
            return
        observed = self.responses / elapsed
        stable = self.errors == 0 and self.responses >= 20 and self.responses >= self.sent * 0.90
        self.history.append(
            {
                "requested_rate": self.rate,
                "observed_rate": round(observed, 2),
                "seconds": round(elapsed, 2),
                "requests": self.sent,
                "responses": self.responses,
                "errors": self.errors,
                "stable": stable,
                "phase": self.mode,
                "work_phase": self.phase,
            }
        )
        if self.mode == "warming":
            self.mode = "probing" if stable else "warming"
        elif not stable:
            self.mode = "holding"
            self.rate = self.best_rate
        else:
            if observed > self.best_throughput * 1.05:
                self.best_throughput = observed
                self.best_rate = self.rate
                self.plateaus = 0
            else:
                self.plateaus += 1
            if self.plateaus >= 2 or self.index == len(self.steps) - 1:
                self.mode = "holding"
                self.rate = self.best_rate
            else:
                self.index += 1
                self.rate = float(self.steps[self.index])
        self.sent = self.responses = self.errors = 0
        self.phase_started = time.monotonic()
        self._write()

    def wait(self):
        with self.lock:
            self._roll()
            target = max(time.monotonic(), self.next_request, self.blocked_until)
            self.next_request = target + 1 / self.rate
        while True:
            if self.stop_check:
                self.stop_check()
            with self.lock:
                remaining = max(target, self.blocked_until) - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(remaining, 1))
        with self.lock:
            self.sent += 1

    def response(self, status: int, retry_after: str = ""):
        with self.lock:
            self.responses += 1
            if status in {429, 500, 502, 503, 504}:
                self.errors += 1
                try:
                    pause = float(retry_after)
                except ValueError:
                    try:
                        pause = (
                            parsedate_to_datetime(retry_after) - datetime.now(UTC)
                        ).total_seconds()
                    except (TypeError, ValueError):
                        pause = 10
                self.rate = max(1.0, min(self.best_rate, self.rate / 2))
                self.last_backoff = {"status": status, "pause_seconds": max(2, pause)}
                self.best_rate = self.rate
                self.mode = "holding"
                self.blocked_until = max(self.blocked_until, time.monotonic() + max(2, pause))
                self._write()

    def network_failure(self):
        with self.lock:
            self.errors += 1
            self.rate = max(1.0, min(self.best_rate, self.rate / 2))
            self.best_rate = self.rate
            self.mode = "holding"
            self.last_backoff = {"status": "network", "pause_seconds": 0}
            self._write()
