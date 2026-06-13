from __future__ import annotations

import json
import logging
import os
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


_TRACING_CONFIGURED = False
_REQUESTS_INSTRUMENTED = False


def setup_observability(service_name: str, *, flask_app: Any | None = None) -> None:
    configure_logging(service_name)
    configure_tracing(service_name, flask_app=flask_app)


def configure_logging(service_name: str) -> Path:
    log_dir = Path(os.getenv("VALLE_LOG_DIR", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = Path(
        os.getenv("VALLE_LOG_FILE", str(log_dir / f"{service_name}.log"))
    )
    log_file.parent.mkdir(parents=True, exist_ok=True)

    level = getattr(logging, os.getenv("VALLE_LOG_LEVEL", "INFO").upper(), logging.INFO)
    max_bytes = _env_int("VALLE_LOG_MAX_BYTES", 10_000_000)
    backup_count = _env_int("VALLE_LOG_BACKUP_COUNT", 5)

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(JsonLogFormatter(service_name))
    file_handler.addFilter(PortalRequestLogFilter())

    handlers: list[logging.Handler] = [file_handler]
    if _env_bool("VALLE_LOG_CONSOLE", True):
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        console_handler.addFilter(PortalRequestLogFilter())
        handlers.append(console_handler)

    logging.basicConfig(level=level, handlers=handlers, force=True)
    _configure_third_party_loggers()
    logging.getLogger("valle.observability").info(
        "logging configured service=%s file=%s", service_name, log_file
    )
    return log_file


def configure_tracing(service_name: str, *, flask_app: Any | None = None) -> None:
    if not _env_bool("VALLE_OTEL_ENABLED", True):
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logging.getLogger("valle.observability").warning(
            "OpenTelemetry SDK is not installed; tracing disabled"
        )
        return

    global _TRACING_CONFIGURED
    if not _TRACING_CONFIGURED:
        resource = Resource.create(
            {
                "service.name": service_name,
                "service.version": _package_version(),
            }
        )
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(
            BatchSpanProcessor(_JsonFileSpanExporter(service_name))
        )

        otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        if otlp_endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                    OTLPSpanExporter,
                )

                provider.add_span_processor(
                    BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint))
                )
            except ImportError:
                logging.getLogger("valle.observability").warning(
                    "OTLP exporter is not installed; OTLP tracing disabled"
                )

        trace.set_tracer_provider(provider)
        _TRACING_CONFIGURED = True

    _instrument_requests()
    _instrument_logging()
    if flask_app is not None:
        _instrument_flask(flask_app)


def get_tracer(name: str) -> Any:
    try:
        from opentelemetry import trace
    except ImportError:
        return _NoopTracer()
    return trace.get_tracer(name)


class JsonLogFormatter(logging.Formatter):
    def __init__(self, service_name: str) -> None:
        super().__init__()
        self._service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "service": self._service_name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        trace_id = getattr(record, "otelTraceID", "")
        span_id = getattr(record, "otelSpanID", "")
        if trace_id:
            payload["trace_id"] = trace_id
        if span_id:
            payload["span_id"] = span_id
        return json.dumps(payload, sort_keys=True)


class PortalRequestLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not _env_bool("VALLE_LOG_SUPPRESS_PORTAL_POLLING", True):
            return True
        if record.name != "werkzeug":
            return True
        message = record.getMessage()
        return not _is_successful_portal_request(message)


class _JsonFileSpanExporter:
    def __init__(self, service_name: str) -> None:
        from opentelemetry.sdk.trace.export import SpanExportResult

        self._result = SpanExportResult.SUCCESS
        log_dir = Path(os.getenv("VALLE_LOG_DIR", "logs"))
        log_dir.mkdir(parents=True, exist_ok=True)
        self._path = Path(
            os.getenv(
                "VALLE_OTEL_TRACES_FILE",
                str(log_dir / f"{service_name}.traces.jsonl"),
            )
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def export(self, spans: Any) -> Any:
        with self._lock, self._path.open("a", encoding="utf-8") as handle:
            for span in spans:
                handle.write(json.dumps(_span_to_dict(span), sort_keys=True))
                handle.write("\n")
        return self._result

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True


class _NoopTracer:
    def start_as_current_span(self, name: str, **kwargs: Any) -> Any:
        return _NoopSpanContext()


class _NoopSpanContext:
    def __enter__(self) -> "_NoopSpanContext":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        return None

    def set_attribute(self, key: str, value: Any) -> None:
        return None


def _span_to_dict(span: Any) -> dict[str, Any]:
    context = span.get_span_context()
    parent = span.parent
    return {
        "name": span.name,
        "context": {
            "trace_id": f"{context.trace_id:032x}",
            "span_id": f"{context.span_id:016x}",
        },
        "parent_id": f"{parent.span_id:016x}" if parent else None,
        "start_time": span.start_time,
        "end_time": span.end_time,
        "status": {
            "status_code": str(span.status.status_code),
            "description": span.status.description,
        },
        "attributes": dict(span.attributes or {}),
        "events": [
            {
                "name": event.name,
                "timestamp": event.timestamp,
                "attributes": dict(event.attributes or {}),
            }
            for event in span.events
        ],
    }


def _instrument_requests() -> None:
    global _REQUESTS_INSTRUMENTED
    if _REQUESTS_INSTRUMENTED:
        return
    try:
        from opentelemetry.instrumentation.requests import RequestsInstrumentor
    except ImportError:
        return
    RequestsInstrumentor().instrument()
    _REQUESTS_INSTRUMENTED = True


def _instrument_logging() -> None:
    try:
        from opentelemetry.instrumentation.logging import LoggingInstrumentor
    except ImportError:
        return
    LoggingInstrumentor().instrument(set_logging_format=False)


def _instrument_flask(flask_app: Any) -> None:
    try:
        from opentelemetry.instrumentation.flask import FlaskInstrumentor
    except ImportError:
        return
    FlaskInstrumentor().instrument_app(flask_app)


def _package_version() -> str:
    try:
        from importlib.metadata import version

        return version("valle")
    except Exception:
        return "0.1.0"


def _configure_third_party_loggers() -> None:
    if _env_bool("VALLE_VERBOSE_SDK_LOGS", False):
        return
    for logger_name in (
        "azure.core.pipeline.policies.http_logging_policy",
        "crewai_core.settings",
        "httpx",
    ):
        logging.getLogger(logger_name).setLevel(logging.WARNING)


def _is_successful_portal_request(message: str) -> bool:
    if " 200 " not in message and " 204 " not in message and " 304 " not in message:
        return False
    return any(
        path in message
        for path in (
            "GET /portal/api/",
            "GET /portal/camera.mjpg",
            "GET /favicon.ico",
        )
    )


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        logging.getLogger("valle.observability").warning(
            "%s must be an integer; using %d", name, default
        )
        return default
