from datetime import UTC, datetime

from apartmentfinder.infrastructure.health import HealthState


def test_health_state_reports_process_alive() -> None:
    state = HealthState(role="test", check_database=lambda: None)

    payload = state.health_payload()

    assert payload["status"] == "ok"
    assert payload["role"] == "test"
    assert "started_at" in payload


def test_readiness_state_checks_database() -> None:
    state = HealthState(role="test", check_database=lambda: None)

    ready, payload = state.readiness_payload()

    assert ready is True
    assert payload["status"] == "ready"
    checks = payload["checks"]
    assert checks["postgresql"]["ok"] is True
    assert checks["queue"]["status"] == "not_configured"


def test_readiness_state_fails_without_required_poll() -> None:
    state = HealthState(
        role="worker",
        check_database=lambda: None,
        require_recent_poll=True,
    )

    ready, payload = state.readiness_payload()

    assert ready is False
    assert payload["status"] == "not_ready"
    assert payload["checks"]["last_successful_poll"]["ok"] is False


def test_readiness_state_accepts_recent_poll() -> None:
    state = HealthState(
        role="worker",
        check_database=lambda: None,
        require_recent_poll=True,
    )
    state.mark_successful_poll(datetime.now(UTC))

    ready, payload = state.readiness_payload()

    assert ready is True
    assert payload["checks"]["last_successful_poll"]["ok"] is True


def test_readiness_state_reports_database_failure() -> None:
    def fail() -> None:
        raise RuntimeError("database down")

    state = HealthState(role="test", check_database=fail)

    ready, payload = state.readiness_payload()

    assert ready is False
    assert payload["checks"]["postgresql"]["ok"] is False
    assert payload["checks"]["postgresql"]["error_type"] == "RuntimeError"


def test_readiness_state_marks_stale_poll_unready() -> None:
    state = HealthState(
        role="worker",
        check_database=lambda: None,
        require_recent_poll=True,
        poll_max_age_seconds=0.001,
    )
    state.mark_successful_poll(datetime(2026, 1, 1, tzinfo=UTC))

    ready, payload = state.readiness_payload()

    assert ready is False
    assert payload["checks"]["last_successful_poll"]["ok"] is False
