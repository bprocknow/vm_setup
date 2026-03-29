"""Application error types."""

from __future__ import annotations


class AppError(Exception):
    """Base error that carries a CLI exit code."""

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


class ValidationError(AppError):
    """Raised when user or host-side validation fails."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("\n".join(errors), exit_code=2)


class CommandError(AppError):
    """Raised when an external command fails."""

    def __init__(self, message: str, *, exit_code: int = 1) -> None:
        super().__init__(message, exit_code=exit_code)
