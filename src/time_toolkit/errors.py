from __future__ import annotations

from enum import IntEnum
from typing import Any


class ExitCode(IntEnum):
    OK = 0
    GENERAL = 1
    USAGE = 2
    AUTH = 3
    PERMISSION = 4
    NOT_FOUND = 5
    NETWORK = 6
    CONFLICT = 7
    CONFIRMATION_REQUIRED = 8


class TimeToolkitError(Exception):
    """Expected error that can be rendered without a traceback."""

    def __init__(
        self,
        message: str,
        *,
        exit_code: ExitCode = ExitCode.GENERAL,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code
        self.status_code = status_code
        self.details = details or {}


class UsageError(TimeToolkitError):
    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=ExitCode.USAGE)


class ConfigError(TimeToolkitError):
    pass


class AuthenticationError(TimeToolkitError):
    def __init__(self, message: str = "Authentication failed or expired") -> None:
        super().__init__(message, exit_code=ExitCode.AUTH, status_code=401)


class PermissionError(TimeToolkitError):
    def __init__(
        self, message: str = "The account does not have permission for this action"
    ) -> None:
        super().__init__(message, exit_code=ExitCode.PERMISSION, status_code=403)


class NotFoundError(TimeToolkitError):
    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=ExitCode.NOT_FOUND, status_code=404)


class NetworkError(TimeToolkitError):
    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=ExitCode.NETWORK)


class ConflictError(TimeToolkitError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, exit_code=ExitCode.CONFLICT, details=details)


class ConfirmationRequired(TimeToolkitError):
    def __init__(self, message: str = "Explicit confirmation is required") -> None:
        super().__init__(message, exit_code=ExitCode.CONFIRMATION_REQUIRED)
