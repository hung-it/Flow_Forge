import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from flowforge.contracts import DispatchMessage, WorkflowSpec


def example():
    path = Path(__file__).resolve().parents[2] / "examples/document-workflow.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_example_and_defaults():
    spec = WorkflowSpec.model_validate(example())
    assert spec.schema_version == 1
    assert spec.nodes[0].retry.max_attempts == 3
    assert spec.nodes[0].timeout_seconds == 60


@pytest.mark.parametrize("change", ["cycle", "missing", "duplicate", "handler", "binding",
                                    "empty", "timeout", "extra", "version", "edge_shape",
                                    "self", "duplicate_edge", "pointer", "source", "retry"])
def test_invalid_workflow(change):
    data = example()
    if change == "cycle":
        data["edges"].append(["report", "validate"])
    elif change == "missing":
        data["edges"].append(["unknown", "report"])
    elif change == "duplicate":
        data["nodes"].append(data["nodes"][0])
    elif change == "handler":
        data["nodes"][0]["handler"] = "exec_python"
    elif change == "binding":
        data["nodes"][0]["inputs"] = {
            "document": {"source": "task_output", "node": "report", "pointer": ""}
        }
    elif change == "empty":
        data["nodes"] = []
    elif change == "timeout":
        data["nodes"][0]["timeout_seconds"] = 301
    elif change == "extra":
        data["nodes"][0]["depends_on"] = []
    elif change == "edge_shape":
        data["edges"].append(["validate"])
    elif change == "self":
        data["edges"].append(["validate", "validate"])
    elif change == "duplicate_edge":
        data["edges"].append(data["edges"][0])
    elif change == "pointer":
        data["nodes"][0]["inputs"]["document"]["pointer"] = "/~2"
    elif change == "source":
        data["nodes"][0]["inputs"]["document"]["node"] = "validate"
    elif change == "retry":
        data["nodes"][0]["retry"] = {"initial_delay_seconds": 10, "max_delay_seconds": 2}
    else:
        data["schema_version"] = 2
    with pytest.raises(ValidationError):
        WorkflowSpec.model_validate(data)


def test_dispatch_rejects_unknown_version_and_payload():
    from uuid import uuid4

    data = dict(schema_version=1, event_id=uuid4(), run_id=uuid4(),
                task_run_id=uuid4(), dispatch_generation=1)
    assert DispatchMessage.model_validate(data).dispatch_generation == 1
    with pytest.raises(ValidationError):
        DispatchMessage.model_validate({**data, "schema_version": 2})
    with pytest.raises(ValidationError):
        DispatchMessage.model_validate({**data, "input": {"secret": "not allowed"}})
