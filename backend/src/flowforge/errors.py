class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status, self.code, self.message = status, code, message


def unauthorized() -> ApiError:
    return ApiError(401, "UNAUTHENTICATED", "Authentication required or credentials invalid")
