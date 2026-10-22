"""Process lifecycle: accept runs, track them, stop them on SIGTERM.

The HTTP handlers ask :class:`Lifecycle` before starting a run. Once shutdown
starts, new runs are refused and every tracked run is marked ``interrupted``
(not ``failed``): there is no verdict, so the request can be run again from its
recorded base commit. The workflow also observes the stop event at phase
boundaries so a long model call does not keep the process alive indefinitely.
"""

from __future__ import annotations

import threading


class ShuttingDown(Exception):
    """Caliper is stopping and no longer accepts new runs."""


class Lifecycle:
    def __init__(self, requests, stop_event: threading.Event | None = None):
        self.requests = requests
        self.stop_event = stop_event or threading.Event()
        self._lock = threading.Lock()
        self._accepting = True
        self._active: set[str] = set()

    @property
    def accepting_runs(self) -> bool:
        with self._lock:
            return self._accepting

    @property
    def active_runs(self) -> list[str]:
        with self._lock:
            return sorted(self._active)

    def begin(self, req_id: str) -> None:
        with self._lock:
            if not self._accepting:
                raise ShuttingDown(
                    "Caliper is shutting down and is not accepting new runs. "
                    "Start it again, then run the request."
                )
            self._active.add(req_id)

    def end(self, req_id: str) -> None:
        with self._lock:
            self._active.discard(req_id)

    def start_shutdown(self) -> None:
        with self._lock:
            self._accepting = False
            self.stop_event.set()

    def shutdown(self, reason: str = "Caliper is shutting down; this run was interrupted and can be run again.") -> list[dict]:
        self.start_shutdown()
        active = self.active_runs
        return self.requests.mark_interrupted(
            reason, status="interrupted", ids=active or None
        )

    def wait_idle(self, timeout: float = 10.0) -> None:
        """Give observed runs a chance to stop at a phase boundary."""
        import time

        deadline = time.monotonic() + max(0.0, timeout)
        while self.active_runs and time.monotonic() < deadline:
            time.sleep(0.05)
        # After shutdown every run is interrupted; nothing is active any more.
        with self._lock:
            self._active.clear()
