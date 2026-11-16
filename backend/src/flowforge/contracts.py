"""Versioned boundary schemas. No task execution or binding evaluation here."""
from collections import deque
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

NodeKey = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
Edge = Annotated[list[NodeKey], Field(min_length=2, max_length=2)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, validate_default=True)


class RetryPolicy(Contract):
    max_attempts: int = Field(default=3, ge=1, le=5)
    initial_delay_seconds: int = Field(default=1, ge=1, le=30)
    backoff_multiplier: int = Field(default=2, ge=1, le=4)
    max_delay_seconds: int = Field(default=30, ge=1, le=300)

    @model_validator(mode="after")
    def delay_order(self) -> Self:
        if self.max_delay_seconds < self.initial_delay_seconds:
            raise ValueError("max delay must be >= initial delay")
        return self


class InputBinding(Contract):
    source: Literal["run_input", "task_output"]
    node: NodeKey | None = None
    pointer: str = Field(default="", max_length=512, pattern=r"^(?:/(?:[^~/]|~[01])*)*$")

    @model_validator(mode="after")
    def source_node(self) -> Self:
        if (self.source == "task_output") != (self.node is not None):
            raise ValueError("node is required only for task_output")
        return self


class TaskSpec(Contract):
    key: NodeKey
    handler: Literal["validate_document", "extract_metadata", "analyze_content",
                     "aggregate", "generate_report"]
    inputs: dict[NodeKey, InputBinding] = Field(default_factory=dict, max_length=32)
    timeout_seconds: int = Field(default=60, ge=1, le=300)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)


class WorkflowSpec(Contract):
    schema_version: Literal[1]
    name: str = Field(min_length=1, max_length=100, pattern=r"\S")
    nodes: list[TaskSpec] = Field(min_length=1, max_length=100)
    edges: list[Edge] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def valid_graph(self) -> Self:
        keys = {node.key for node in self.nodes}
        if len(keys) != len(self.nodes):
            raise ValueError("duplicate node key")
        parents: dict[str, set[str]] = {key: set() for key in keys}
        children: dict[str, list[str]] = {key: [] for key in keys}
        for edge in self.edges:
            before, after = edge
            if before not in keys or after not in keys or before == after:
                raise ValueError("unknown node or self dependency")
            if before in parents[after]:
                raise ValueError("duplicate edge")
            parents[after].add(before)
            children[before].append(after)
        degree = {key: len(value) for key, value in parents.items()}
        ready = deque(key for key in keys if not degree[key])
        ancestors: dict[str, set[str]] = {key: set() for key in keys}
        visited = 0
        while ready:
            key = ready.popleft()
            visited += 1
            for child in children[key]:
                ancestors[child].update(ancestors[key] | {key})
                degree[child] -= 1
                if not degree[child]:
                    ready.append(child)
        if visited != len(keys):
            raise ValueError("workflow contains a cycle")
        for node in self.nodes:
            for binding in node.inputs.values():
                if binding.node is not None and binding.node not in ancestors[node.key]:
                    raise ValueError("task_output must reference a transitive predecessor")
        return self


class DispatchMessage(Contract):
    schema_version: Literal[1]
    event_id: UUID
    run_id: UUID
    task_run_id: UUID
    dispatch_generation: int = Field(ge=1)
    traceparent: str | None = Field(
        default=None, pattern=r"^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$"
    )
