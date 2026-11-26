from typing import TypedDict


class ErrorDetail(TypedDict):
    loc: list[str | int]
    type: str
    message: str


class ApiError(Exception):
    def __init__(
        self, status: int, code: str, message: str, *, details: list[ErrorDetail] | None = None
    ) -> None:
        self.status, self.code, self.message = status, code, message
        self.details = details if details is not None else []


def unauthorized() -> ApiError:
    return ApiError(401, "UNAUTHENTICATED", "Authentication required or credentials invalid")
