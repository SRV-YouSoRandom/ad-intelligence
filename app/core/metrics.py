"""Custom metrics collector for observability."""

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MetricsCollector:
    """In-memory metrics collector for the Ad Intelligence platform."""

    _counters: dict[str, int] = field(default_factory=dict)
    _timings: dict[str, list[float]] = field(default_factory=dict)
    _start_time: float = field(default_factory=time.time)

    def increment(self, name: str, value: int = 1) -> None:
        """Increment a counter metric."""
        self._counters[name] = self._counters.get(name, 0) + value

    def record_timing(self, name: str, duration_ms: float) -> None:
        """Record a timing measurement in milliseconds."""
        if name not in self._timings:
            self._timings[name] = []
        self._timings[name].append(duration_ms)

    def get_summary(self) -> dict[str, Any]:
        """Return a summary of all collected metrics."""
        timing_stats = {}
        for name, values in self._timings.items():
            if values:
                timing_stats[name] = {
                    "count": len(values),
                    "avg_ms": round(sum(values) / len(values), 2),
                    "min_ms": round(min(values), 2),
                    "max_ms": round(max(values), 2),
                }

        return {
            "uptime_seconds": round(time.time() - self._start_time, 1),
            "counters": dict(self._counters),
            "timings": timing_stats,
        }

    def reset(self) -> None:
        """Reset all metrics."""
        self._counters.clear()
        self._timings.clear()
        self._start_time = time.time()


# Global singleton
metrics = MetricsCollector()
