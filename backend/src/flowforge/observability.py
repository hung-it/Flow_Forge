import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from threading import Thread

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_ON, ParentBased
from opentelemetry.trace import SpanKind, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, start_http_server

from flowforge.config import Settings

_CONTEXT = TraceContextTextMapPropagator()
_TRACEPARENT = re.compile(r"00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}")
_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
_FIELDS = {
    "event", "request_id", "run_id", "task_id", "event_id", "generation", "attempt",
    "worker_id", "status", "outcome", "error_code", "method", "route", "confirmed", "recorded",
    "trace_id", "span_id",
}
request_ref = ContextVar[str | None]("flowforge_request_id", default=None)
_ROLE = "flowforge"
_tracer: trace.Tracer = trace.NoOpTracer()
metrics: "Metrics | None" = None


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "time": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "service": _ROLE,
            "logger": record.name,
            "message": record.getMessage(),
        }
        fields = getattr(record, "fields", {})
        data.update({key: value for key, value in fields.items() if key in _FIELDS})
        if request_id := request_ref.get():
            data["request_id"] = request_id
        context = trace.get_current_span().get_span_context()
        if context.is_valid:
            data["trace_id"] = f"{context.trace_id:032x}"
            data["span_id"] = f"{context.span_id:016x}"
        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)
        return json.dumps(data)


def configure_logging(role: str = "flowforge") -> None:
    global _ROLE
    _ROLE = role
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def log_event(logger: logging.Logger, name: str, *, parent: str | None = None,
              **fields: object) -> None:
    if context := trace_parent(parent):
        info = trace.get_current_span(context).get_span_context()
        fields.update(trace_id=f"{info.trace_id:032x}", span_id=f"{info.span_id:016x}")
    logger.info(name, extra={"fields": {"event": name, **fields}})


def http_method(value: str) -> str:
    return value if value in _METHODS else "OTHER"


def trace_parent(value: object) -> Context | None:
    if not isinstance(value, str) or _TRACEPARENT.fullmatch(value) is None:
        return None
    context = _CONTEXT.extract({"traceparent": value}, context=Context())
    return context if trace.get_current_span(context).get_span_context().is_valid else None


def trace_header() -> str | None:
    context = trace.get_current_span().get_span_context()
    if not context.is_valid or not context.trace_flags.sampled:
        return None
    carrier: dict[str, str] = {}
    _CONTEXT.inject(carrier)
    return carrier.get("traceparent")


@contextmanager
def span(
    name: str,
    *,
    parent: object = None,
    root: bool = False,
    kind: SpanKind = SpanKind.INTERNAL,
    attributes: Mapping[str, str | int | bool] | None = None,
) -> Iterator[trace.Span]:
    context = (trace_parent(parent) or Context()) if root or parent is not None else None
    with _tracer.start_as_current_span(
        name, context=context, kind=kind, attributes=attributes,
        record_exception=False, set_status_on_exception=False,
    ) as current:
        try:
            yield current
        except Exception:
            current.set_status(StatusCode.ERROR)
            raise


def link_run(current: trace.Span, parent: object) -> None:
    context = trace_parent(parent)
    if context is not None:
        current.add_link(trace.get_current_span(context).get_span_context(), attributes={})


class Metrics:
    def __init__(self) -> None:
        self.registry = registry = CollectorRegistry()
        http_labels = ("method", "route", "status_class")
        self.requests = Counter(
            "flowforge_http_requests", "HTTP responses", http_labels, registry=registry,
        )
        self.http_duration = Histogram(
            "flowforge_http_request_duration_seconds", "HTTP response latency", http_labels,
            buckets=(.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10, 15), registry=registry,
        )
        self.runs = Counter(
            "flowforge_workflow_runs", "Committed workflow run events", ("outcome",),
            registry=registry,
        )
        self.attempts = Counter(
            "flowforge_task_attempts", "Committed terminal attempts", ("outcome",),
            registry=registry,
        )
        self.task_duration = Histogram(
            "flowforge_task_attempt_duration_seconds", "Persisted attempt duration", ("outcome",),
            buckets=(.01, .05, .1, .25, .5, 1, 2.5, 5, 10, 30, 60, 120, 300, 900),
            registry=registry,
        )
        self.retries = Counter(
            "flowforge_task_retries", "Committed retry transitions", ("reason",), registry=registry,
        )
        self.expirations = Counter(
            "flowforge_lease_expirations", "Committed recovery of expired attempts", ("reason",),
            registry=registry,
        )
        self.dispatches = Counter(
            "flowforge_scheduler_dispatches", "Committed new outbox events", registry=registry,
        )
        self.redrives = Counter(
            "flowforge_scheduler_redrives", "Committed same-generation redrives", registry=registry,
        )
        self.publishes = Counter(
            "flowforge_outbox_publish", "Publish outcomes", ("outcome",), registry=registry,
        )
        self.outbox_pending = Gauge(
            "flowforge_outbox_pending_events", "Unpublished pending and claimed events",
            registry=registry,
        )
        self.outbox_age = Gauge(
            "flowforge_outbox_oldest_seconds", "Age of the oldest unpublished event",
            registry=registry,
        )
        self.outbox_freshness = Gauge(
            "flowforge_outbox_snapshot_timestamp_seconds", "Unix timestamp of the last snapshot",
            registry=registry,
        )

    def attempt(self, outcome: str, duration: float) -> None:
        self.attempts.labels(outcome).inc()
        self.task_duration.labels(outcome).observe(max(0, duration))


def record_http(method: str, route: str, status: int, seconds: float) -> None:
    if current := metrics:
        labels = (method, route, f"{status // 100}xx")
        current.requests.labels(*labels).inc()
        current.http_duration.labels(*labels).observe(seconds)


@asynccontextmanager
async def telemetry(role: str, settings: Settings) -> AsyncIterator[None]:
    global _tracer, metrics
    configure_logging(role)
    if not settings.telemetry_enabled:
        yield
        return
    current = Metrics()
    server = None
    try:
        server, _ = start_http_server(9464, registry=current.registry)
    except OSError:
        logging.getLogger(__name__).warning("metrics listener unavailable")
    provider = TracerProvider(
        resource=Resource({"service.name": f"flowforge-{role}"}),
        sampler=ParentBased(ALWAYS_ON), shutdown_on_exit=False,
    )
    provider.add_span_processor(BatchSpanProcessor(
        OTLPSpanExporter(endpoint=settings.otlp_traces_endpoint, timeout=2),
        max_queue_size=2048, max_export_batch_size=256, schedule_delay_millis=500,
    ))
    _tracer, metrics = provider.get_tracer("flowforge"), current
    try:
        yield
    finally:
        _tracer, metrics = trace.NoOpTracer(), None
        if server is not None:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
        shutdown = Thread(target=provider.shutdown, daemon=True)
        shutdown.start()
        await asyncio.to_thread(shutdown.join, 3)
