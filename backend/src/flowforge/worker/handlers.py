import asyncio
from dataclasses import dataclass
from typing import cast
from uuid import UUID

from pydantic import JsonValue

from flowforge.contracts import Contract
from flowforge.workflows.publication import DocumentInput, ReportInput, Summary

Output = dict[str, JsonValue]


@dataclass(frozen=True)
class TaskContext:
    run_id: UUID
    task_id: UUID
    attempt_no: int
    cancelled: asyncio.Event

    @property
    def side_effect_key(self) -> str:
        return f"task:{self.task_id}"


def execute(handler: str, model: Contract, context: TaskContext) -> Output:
    """Pure demo handlers use the same schemas as publication."""
    if context.cancelled.is_set():
        raise asyncio.CancelledError
    if isinstance(model, DocumentInput):
        document = model.document
        if handler == "validate_document":
            return document.model_dump(mode="json")
        if handler == "extract_metadata":
            payload = {"title": document.title, "character_count": len(document.content)}
        else:
            payload = {
                "word_count": len(document.content.split()),
                "line_count": len(document.content.splitlines()),
            }
    elif isinstance(model, Summary):
        payload = model.model_dump(mode="json")
    else:
        summary = cast(ReportInput, model).summary
        payload = {
            "summary": summary.model_dump(mode="json"),
            "report_text": (
                f"{summary.metadata.title}: {summary.analysis.word_count} words, "
                f"{summary.analysis.line_count} lines, "
                f"{summary.metadata.character_count} characters"
            ),
        }
    return cast(Output, payload)
