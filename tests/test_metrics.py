from apartmentfinder.infrastructure.metrics import MetricsRegistry


def test_metrics_registry_renders_counter_with_labels() -> None:
    registry = MetricsRegistry()

    registry.inc_counter("notifications_sent_total", source="kufar")
    registry.inc_counter("notifications_sent_total", source="kufar")

    text = registry.render()

    assert "# TYPE notifications_sent_total counter" in text
    assert 'notifications_sent_total{source="kufar"} 2' in text


def test_metrics_registry_renders_histogram_buckets() -> None:
    registry = MetricsRegistry()

    registry.observe_histogram(
        "subscription_check_duration_seconds",
        0.2,
        property_type="room",
    )

    text = registry.render()

    assert "# TYPE subscription_check_duration_seconds histogram" in text
    assert (
        'subscription_check_duration_seconds_bucket{property_type="room",le="0.25"} 1'
        in text
    )
    assert 'subscription_check_duration_seconds_sum{property_type="room"} 0.2' in text
    assert 'subscription_check_duration_seconds_count{property_type="room"} 1' in text


def test_metrics_registry_escapes_label_values() -> None:
    registry = MetricsRegistry()

    registry.inc_counter(
        "source_errors_total",
        source='bad"source',
        error_type="Line\nBreak",
    )

    text = registry.render()

    assert 'error_type="Line\\nBreak"' in text
    assert 'source="bad\\"source"' in text
