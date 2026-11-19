import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from flowforge.workflows.publication import CATALOG, Document, snapshot


def example():
    return json.loads((Path(__file__).parents[2] / "examples/document-workflow.json").read_text())


def test_snapshot_defaults_and_checksum():
    dag, contracts, checksum = snapshot(example())
    assert dag["nodes"][0]["retry"]["max_attempts"] == 3
    assert len(checksum) == 64
    assert set(contracts) == set(CATALOG)
    reordered = example()
    reordered["nodes"].reverse()
    reordered["edges"].reverse()
    assert snapshot(reordered)[2] == checksum
    explicit = copy.deepcopy(dag)
    assert snapshot(explicit)[2] == checksum
    explicit["nodes"][0]["timeout_seconds"] = 99
    assert snapshot(explicit)[2] != checksum


@pytest.mark.parametrize("change", ["cycle", "unknown", "missing", "extra"])
def test_invalid_publication(change):
    dag = example()
    if change == "cycle":
        dag["edges"].append(["report", "validate"])
    elif change == "unknown":
        dag["nodes"][0]["handler"] = "shell"
    elif change == "missing":
        dag["nodes"][0]["inputs"] = {}
    else:
        dag["nodes"][0]["inputs"]["other"] = dag["nodes"][0]["inputs"]["document"]
    with pytest.raises(ValueError):
        snapshot(dag)


@pytest.mark.parametrize(
    "body",
    [
        {"title": "", "content": "x"},
        {"title": "x", "content": " "},
        {"title": "x", "content": "x", "extra": 1},
        {"title": "x", "content": 42},
    ],
)
def test_document_rejects_invalid_payload(body):
    with pytest.raises(ValidationError):
        Document.model_validate(body)


def test_catalog_schemas():
    for spec in CATALOG.values():
        assert spec[0].model_json_schema()["additionalProperties"] is False
        assert spec[1].model_json_schema()["additionalProperties"] is False
