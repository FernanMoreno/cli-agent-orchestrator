"""Infrastructure-independent browser authentication contracts."""

from dataclasses import dataclass


class BrowserAuthError(Exception):
    """A safe, fixed error code suitable for the HTTP boundary."""

    def __init__(self, code: str, status: int = 401, retry_after: int | None = None):
        super().__init__(code)
        self.code = code
        self.status = status
        self.retry_after = retry_after


@dataclass(frozen=True)
class BrowserSessionPolicy:
    temporal_idle_seconds: int = 28800
    temporal_absolute_seconds: int = 86400
    remembered_idle_seconds: int = 604800
    remembered_absolute_seconds: int = 2592000
    access_seconds: int = 3600

    def __post_init__(self):
        for value in vars(self).values():
            if type(value) is not int or value <= 0:
                raise ValueError("Browser session limits must be positive integers")
        for idle, absolute in (
            (self.temporal_idle_seconds, self.temporal_absolute_seconds),
            (self.remembered_idle_seconds, self.remembered_absolute_seconds),
        ):
            if idle > absolute or self.access_seconds > absolute:
                raise ValueError("Browser session limits are inconsistent")
