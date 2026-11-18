from uuid import UUID

import pytest

from flowforge.runs.input import fingerprint, resolve_pointer

WORKFLOW = UUID(int=1)


@pytest.mark.parametrize(
    ("pointer", "expected"),
    [
        ("", {"a/b": {"~": [None, "ok"]}, "": 2}),
        ("/a~1b/~0/0", None),
        ("/a~1b/~0/1", "ok"),
        ("/", 2),
    ],
)
def test_resolve_pointer(pointer, expected):
    assert resolve_pointer({"a/b": {"~": [None, "ok"]}, "": 2}, pointer) == expected


@pytest.mark.parametrize("pointer", ["/missing", "/a/-", "/a/01", "/a/+1", "/a/1", "/a/0/key"])
def test_missing_or_invalid_pointer(pointer):
    with pytest.raises(ValueError):
        resolve_pointer({"a": [None]}, pointer)


def test_fingerprint_preserves_intent():
    value = {"a": {"x": 1, "y": 2}, "b": [1, 2]}
    base = fingerprint(WORKFLOW, 1, value)
    assert base == fingerprint(WORKFLOW, 1, {"b": [1, 2], "a": {"y": 2, "x": 1}})
    assert base != fingerprint(WORKFLOW, 1, {**value, "b": [2, 1]})
    assert base != fingerprint(WORKFLOW, 1, {**value, "b": [1.0, 2]})
    assert base != fingerprint(WORKFLOW, 2, value)
    assert base != fingerprint(UUID(int=2), 1, value)
