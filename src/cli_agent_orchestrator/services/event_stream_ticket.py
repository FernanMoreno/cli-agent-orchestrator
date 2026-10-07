"""Process-local, single-use transport capabilities with current authorization.

Credentials stay in server memory; only opaque 30-second capabilities cross URLs.
The validator belongs to the issuing authentication boundary and is run again at
attach and throughout the connection, rather than trusting captured scopes.
"""

import secrets
import threading
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

TICKET_TTL_SECONDS = 30


@dataclass(frozen=True)
class TransportTicket:
    subject: str
    resource: str
    expires_at: float
    validate: Callable[[], Awaitable[list[str]]]


class TicketStore:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._tickets: dict[str, TransportTicket] = {}
        self._lock = threading.Lock()

    def issue(
        self, subject: str, resource: str, validate: Callable[[], Awaitable[list[str]]]
    ) -> str:
        now = self._clock()
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._tickets = {
                key: value for key, value in self._tickets.items() if value.expires_at > now
            }
            if len(self._tickets) >= 4096:
                raise ValueError("ticket capacity exhausted")
            self._tickets[token] = TransportTicket(
                subject, resource, now + TICKET_TTL_SECONDS, validate
            )
        return token

    def consume(self, token: str, resource: str) -> TransportTicket:
        with self._lock:
            ticket = self._tickets.pop(token, None)
        if ticket is None or ticket.expires_at <= self._clock() or ticket.resource != resource:
            raise ValueError("invalid, expired, consumed, or wrong-resource ticket")
        return ticket


store = TicketStore()
