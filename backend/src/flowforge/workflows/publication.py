"""Trusted handler contracts and deterministic immutable publication snapshots."""

import hashlib
import json
from typing import Any

from pydantic import Field, field_validator

from flowforge.contracts import Contract, WorkflowSpec


class Document(Contract):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=12000)

    @field_validator("title", "content")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Must not be blank")
        return value


class Metadata(Contract):
    title: str = Field(min_length=1, max_length=200)
    character_count: int = Field(ge=0)


class Analysis(Contract):
    word_count: int = Field(ge=0)
    line_count: int = Field(ge=0)


class Summary(Contract):
    metadata: Metadata
    analysis: Analysis


class Report(Contract):
    summary: Summary
    report_text: str


class DocumentInput(Contract):
    document: Document


class ReportInput(Contract):
    summary: Summary


CATALOG: dict[str, tuple[type[Contract], type[Contract]]] = {
    "validate_document": (DocumentInput, Document),
    "extract_metadata": (DocumentInput, Metadata),
    "analyze_content": (DocumentInput, Analysis),
    "aggregate": (Summary, Summary),
    "generate_report": (ReportInput, Report),
}


class InvalidHandlerBindings(ValueError):
    def __init__(self, node_index: int) -> None:
        super().__init__("Handler binding names must match required inputs")
        self.node_index = node_index


def snapshot(raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str]:
    spec = WorkflowSpec.model_validate(raw)
    contracts: dict[str, Any] = {}
    for index, node in enumerate(spec.nodes):
        inputs, output = CATALOG[node.handler]
        if set(node.inputs) != set(inputs.model_fields):
            raise InvalidHandlerBindings(index)
        contracts[node.handler] = {
            "version": 1,
            "input": inputs.model_json_schema(),
            "output": output.model_json_schema(),
        }
    dag = spec.model_dump(mode="json")
    dag["nodes"].sort(key=lambda n: n["key"])
    dag["edges"].sort()
    encoded = json.dumps(
        {"dag": dag, "contracts": contracts},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    return dag, contracts, hashlib.sha256(encoded).hexdigest()
