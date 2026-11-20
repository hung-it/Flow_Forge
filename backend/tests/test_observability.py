import json
import logging

from flowforge import observability as obs


def test_trace_context_rejects_malformed_and_zero_ids():
    valid = "00-" + "1" * 32 + "-" + "2" * 16 + "-01"
    assert obs.trace_parent(valid) is not None
    for value in (None, 1, b"invalid", "bad", valid.replace("1" * 32, "0" * 32)):
        assert obs.trace_parent(value) is None


def test_structured_event_allows_only_operational_fields():
    record = logging.LogRecord("test", logging.INFO, "", 0, "run created", (), None)
    record.fields = {
        "event": "run.created", "run_id": "run-1", "request_id": "request-1",
        "authorization": "private", "input": {"secret": "private"},
    }
    data = json.loads(obs.JsonFormatter().format(record))
    assert data["event"] == "run.created"
    assert data["run_id"] == "run-1"
    assert data["request_id"] == "request-1"
    assert "private" not in json.dumps(data)


def test_http_label_normalization():
    assert obs.http_method("GET") == "GET"
    assert obs.http_method("arbitrary-value") == "OTHER"
