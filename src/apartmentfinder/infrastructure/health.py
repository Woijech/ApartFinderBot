"""Lightweight operational health and readiness HTTP server."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from apartmentfinder.infrastructure.metrics import render_prometheus_metrics

logger = logging.getLogger(__name__)

CheckResult = dict[str, Any]


@dataclass
class HealthState:
    """Mutable runtime state exposed through operational endpoints."""

    role: str
    check_database: Callable[[], None]
    require_recent_poll: bool = False
    poll_max_age_seconds: float = 900
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_successful_poll_at: datetime | None = None

    def mark_successful_poll(self, when: datetime | None = None) -> None:
        """Record that the worker completed one polling tick."""
        self.last_successful_poll_at = when or datetime.now(UTC)

    def health_payload(self) -> CheckResult:
        """Return a liveness payload that does not touch dependencies."""
        return {
            "status": "ok",
            "role": self.role,
            "started_at": self.started_at.isoformat(),
        }

    def readiness_payload(self) -> tuple[bool, CheckResult]:
        """Return dependency readiness with per-check details."""
        checks = {
            "config": {"ok": True},
            "postgresql": self._postgresql_check(),
            "queue": {"ok": True, "status": "not_configured"},
            "last_successful_poll": self._poll_check(),
        }
        ready = all(check["ok"] for check in checks.values())
        return ready, {
            "status": "ready" if ready else "not_ready",
            "role": self.role,
            "checks": checks,
        }

    def _postgresql_check(self) -> CheckResult:
        try:
            self.check_database()
        except Exception as error:
            return {
                "ok": False,
                "error_type": type(error).__name__,
                "error": str(error),
            }
        return {"ok": True}

    def _poll_check(self) -> CheckResult:
        if not self.require_recent_poll:
            return {"ok": True, "status": "not_required"}
        if self.last_successful_poll_at is None:
            return {"ok": False, "error": "no_successful_poll_yet"}
        age_seconds = (
            datetime.now(UTC) - self.last_successful_poll_at
        ).total_seconds()
        return {
            "ok": age_seconds <= self.poll_max_age_seconds,
            "last_successful_poll_at": self.last_successful_poll_at.isoformat(),
            "age_seconds": round(age_seconds, 3),
            "max_age_seconds": self.poll_max_age_seconds,
        }


class HealthServer:
    """Handle lifecycle for the operational HTTP server thread."""

    def __init__(self, host: str, port: int, state: HealthState) -> None:
        self._server = ThreadingHTTPServer(
            (host, port),
            self._handler_class(state),
        )
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name=f"{state.role}-health-server",
            daemon=True,
        )

    @property
    def port(self) -> int:
        """Return the bound TCP port."""
        return int(self._server.server_address[1])

    def start(self) -> None:
        """Start serving health endpoints in a daemon thread."""
        self._thread.start()

    def close(self) -> None:
        """Stop the HTTP server and release the bound socket."""
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    @staticmethod
    def _handler_class(state: HealthState) -> type[BaseHTTPRequestHandler]:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path == "/health":
                    self._write_json(HTTPStatus.OK, state.health_payload())
                    return
                if self.path == "/readiness":
                    ready, payload = state.readiness_payload()
                    status = HTTPStatus.OK if ready else HTTPStatus.SERVICE_UNAVAILABLE
                    self._write_json(status, payload)
                    return
                if self.path == "/metrics":
                    self._write_text(
                        HTTPStatus.OK,
                        render_prometheus_metrics(),
                        "text/plain; version=0.0.4; charset=utf-8",
                    )
                    return
                self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

            def log_message(self, format: str, *args: object) -> None:
                logger.debug(
                    "health_http_request client=%s " + format,
                    self.client_address[0],
                    *args,
                )

            def _write_json(self, status: HTTPStatus, payload: CheckResult) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _write_text(
                self,
                status: HTTPStatus,
                body: str,
                content_type: str,
            ) -> None:
                encoded = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        return Handler


def start_health_server(host: str, port: int, state: HealthState) -> HealthServer:
    """Start a lightweight HTTP server for operational checks."""
    server = HealthServer(host, port, state)
    server.start()
    logger.info(
        "health_server_started role=%s host=%s port=%s",
        state.role,
        host,
        server.port,
    )
    return server
