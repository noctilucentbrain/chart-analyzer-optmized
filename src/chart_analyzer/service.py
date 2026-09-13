from __future__ import annotations

import logging
from dataclasses import dataclass
from threading import Event, Lock, Thread

from chart_analyzer.app import run_service
from chart_analyzer.config import AppConfig


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ServiceStatus:
    running: bool


class ServiceController:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._lock = Lock()
        self._stop_event = Event()
        self._thread: Thread | None = None

    def start(
        self,
        interval_seconds: float | None = None,
        event_interval_seconds: float | None = None,
    ) -> ServiceStatus:
        with self._lock:
            if self.is_running:
                return self.status()

            self._stop_event = Event()
            self._thread = Thread(
                target=run_service,
                kwargs={
                    "config": self.config,
                    "interval_seconds": interval_seconds,
                    "event_interval_seconds": event_interval_seconds,
                    "stop_event": self._stop_event,
                },
                daemon=True,
                name="chart-analyzer-service",
            )
            self._thread.start()
            LOGGER.info("Service controller started background service")
            return self.status()

    def stop(self, timeout: float = 30) -> ServiceStatus:
        with self._lock:
            if self._thread is None:
                return self.status()
            self._stop_event.set()
            thread = self._thread

        thread.join(timeout=timeout)

        with self._lock:
            if not thread.is_alive():
                self._thread = None
            return self.status()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def status(self) -> ServiceStatus:
        return ServiceStatus(running=self.is_running)
