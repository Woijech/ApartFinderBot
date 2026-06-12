"""Minimal Prometheus metrics registry for runtime monitoring."""

from __future__ import annotations

import threading
from collections import defaultdict
from collections.abc import Iterable

LabelSet = tuple[tuple[str, str], ...]

DEFAULT_DURATION_BUCKETS = (0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0)


class MetricsRegistry:
    """Collect counters and histograms and render Prometheus text format."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, dict[LabelSet, float]] = defaultdict(dict)
        self._histograms: dict[str, dict[LabelSet, list[float]]] = defaultdict(dict)

    def inc_counter(self, name: str, amount: float = 1, **labels: object) -> None:
        """Increase one counter sample."""
        label_set = normalize_labels(labels)
        with self._lock:
            self._counters[name][label_set] = (
                self._counters[name].get(label_set, 0.0) + amount
            )

    def observe_histogram(self, name: str, value: float, **labels: object) -> None:
        """Record one histogram observation."""
        label_set = normalize_labels(labels)
        with self._lock:
            self._histograms[name].setdefault(label_set, []).append(value)

    def render(self) -> str:
        """Render all metrics in Prometheus text exposition format."""
        with self._lock:
            counters = {
                name: dict(samples) for name, samples in self._counters.items()
            }
            histograms = {
                name: {labels: list(values) for labels, values in samples.items()}
                for name, samples in self._histograms.items()
            }

        lines: list[str] = []
        lines.extend(render_counter("new_ads_found_total", counters))
        lines.extend(render_counter("source_errors_total", counters))
        lines.extend(render_counter("notifications_sent_total", counters))
        lines.extend(render_counter("empty_results_total", counters))
        lines.extend(
            render_histogram("subscription_check_duration_seconds", histograms)
        )
        lines.extend(render_histogram("source_response_time_seconds", histograms))
        return "\n".join(lines) + "\n"


metrics = MetricsRegistry()


def observe_subscription_check_duration(
    seconds: float,
    *,
    property_type: str,
) -> None:
    """Record one full subscription source check duration."""
    metrics.observe_histogram(
        "subscription_check_duration_seconds",
        seconds,
        property_type=property_type,
    )


def observe_source_response_time(seconds: float, *, source: str) -> None:
    """Record source search response duration."""
    metrics.observe_histogram(
        "source_response_time_seconds",
        seconds,
        source=source,
    )


def inc_new_ads_found(amount: int, *, source: str) -> None:
    """Count newly discovered ads by source."""
    if amount > 0:
        metrics.inc_counter("new_ads_found_total", amount, source=source)


def inc_source_error(*, source: str, error_type: str) -> None:
    """Count source-level failures by source and error type."""
    metrics.inc_counter(
        "source_errors_total",
        source=source,
        error_type=error_type,
    )


def inc_notifications_sent(amount: int = 1, *, source: str) -> None:
    """Count sent Telegram notifications by source."""
    if amount > 0:
        metrics.inc_counter("notifications_sent_total", amount, source=source)


def inc_empty_result(*, source: str) -> None:
    """Count source searches that returned no listings."""
    metrics.inc_counter("empty_results_total", source=source)


def render_prometheus_metrics() -> str:
    """Return all collected metrics as Prometheus text."""
    return metrics.render()


def normalize_labels(labels: dict[str, object]) -> LabelSet:
    """Return a stable, string-only label tuple."""
    return tuple(sorted((name, str(value)) for name, value in labels.items()))


def render_counter(
    name: str,
    counters: dict[str, dict[LabelSet, float]],
) -> list[str]:
    """Render one counter metric if it has samples."""
    samples = counters.get(name, {})
    if not samples:
        return []
    lines = [
        f"# HELP {name} {help_text(name)}",
        f"# TYPE {name} counter",
    ]
    for labels, value in sorted(samples.items()):
        lines.append(f"{name}{format_labels(labels)} {format_float(value)}")
    return lines


def render_histogram(
    name: str,
    histograms: dict[str, dict[LabelSet, list[float]]],
) -> list[str]:
    """Render one histogram metric if it has samples."""
    samples = histograms.get(name, {})
    if not samples:
        return []
    lines = [
        f"# HELP {name} {help_text(name)}",
        f"# TYPE {name} histogram",
    ]
    for labels, values in sorted(samples.items()):
        sorted_values = sorted(values)
        for bucket in DEFAULT_DURATION_BUCKETS:
            count = sum(1 for value in sorted_values if value <= bucket)
            bucket_labels = labels + (("le", format_float(bucket)),)
            lines.append(
                f"{name}_bucket{format_labels(bucket_labels)} {format_float(count)}"
            )
        inf_labels = labels + (("le", "+Inf"),)
        lines.append(
            f"{name}_bucket{format_labels(inf_labels)} {format_float(len(values))}"
        )
        lines.append(f"{name}_sum{format_labels(labels)} {format_float(sum(values))}")
        lines.append(
            f"{name}_count{format_labels(labels)} {format_float(len(values))}"
        )
    return lines


def format_labels(labels: Iterable[tuple[str, str]]) -> str:
    """Format Prometheus labels."""
    labels = tuple(labels)
    if not labels:
        return ""
    values = ",".join(f'{name}="{escape_label(value)}"' for name, value in labels)
    return "{" + values + "}"


def escape_label(value: str) -> str:
    """Escape a Prometheus label value."""
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def format_float(value: float | int) -> str:
    """Format numeric metric values compactly."""
    return f"{float(value):.12g}"


def help_text(name: str) -> str:
    """Return short metric help text."""
    return {
        "subscription_check_duration_seconds": (
            "Duration of one subscription source check."
        ),
        "source_response_time_seconds": "Duration of one source search.",
        "new_ads_found_total": "Number of newly discovered ads.",
        "source_errors_total": "Number of source-level errors.",
        "notifications_sent_total": "Number of sent Telegram notifications.",
        "empty_results_total": "Number of source searches with no listings.",
    }[name]
