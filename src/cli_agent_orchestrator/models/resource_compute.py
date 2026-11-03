"""Validated local RAM/CPU observations with no authority or storage claims."""

from __future__ import annotations

import math
from dataclasses import dataclass


def _integer(name: str, value: object, *, minimum: int = 0) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


def _validate_observed_at(value: float) -> None:
    try:
        valid = (
            not isinstance(value, bool)
            and isinstance(value, (int, float))
            and math.isfinite(value)
            and value >= 0
        )
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError("clock value must be finite and within its nonnegative range")


@dataclass(frozen=True)
class ResourceHeadroom:
    """One domain's paired quantities; reserves use each domain's limit."""

    available_units: int
    limit_units: int

    def __post_init__(self) -> None:
        _integer("available_units", self.available_units)
        _integer("limit_units", self.limit_units, minimum=1)
        if self.available_units > self.limit_units:
            raise ValueError("domain headroom cannot exceed its limit")


@dataclass(frozen=True)
class ComputeObservation:
    """Fresh RAM/CPU facts with no storage value or capacity implication."""

    observed_at: float
    memory_available_bytes: int
    memory_limit_bytes: int
    cpu_available_millicores: int
    cpu_limit_millicores: int
    memory_domains: tuple[ResourceHeadroom, ...]
    cpu_domains: tuple[ResourceHeadroom, ...]

    def __post_init__(self) -> None:
        _validate_observed_at(self.observed_at)
        for name in ("memory_available_bytes", "cpu_available_millicores"):
            _integer(name, getattr(self, name))
        for name in ("memory_limit_bytes", "cpu_limit_millicores"):
            _integer(name, getattr(self, name), minimum=1)
        for domains in (self.memory_domains, self.cpu_domains):
            if (
                not isinstance(domains, tuple)
                or not domains
                or any(type(domain) is not ResourceHeadroom for domain in domains)
            ):
                raise ValueError("compute observation requires verified resource domains")
        if (
            self.memory_available_bytes
            != min(domain.available_units for domain in self.memory_domains)
            or self.memory_limit_bytes != min(domain.limit_units for domain in self.memory_domains)
            or self.cpu_available_millicores
            != min(domain.available_units for domain in self.cpu_domains)
            or self.cpu_limit_millicores != min(domain.limit_units for domain in self.cpu_domains)
        ):
            raise ValueError("compute totals differ from their verified domains")
