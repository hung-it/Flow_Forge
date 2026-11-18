import hashlib
import json
import re
from uuid import UUID

from pydantic import JsonValue


def resolve_pointer(value: JsonValue, pointer: str) -> JsonValue:
    if not pointer:
        return value
    if not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
        raise ValueError("Invalid JSON Pointer")
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and token in value:
            value = value[token]
        elif isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]*", token):
            index = int(token)
            if index >= len(value):
                raise ValueError("Array index out of range")
            value = value[index]
        else:
            raise ValueError("JSON Pointer does not resolve")
    return value


def fingerprint(workflow_id: UUID, version: int, value: dict[str, JsonValue]) -> str:
    canonical = json.dumps(
        {"workflow_id": str(workflow_id), "version": version, "input": value},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
