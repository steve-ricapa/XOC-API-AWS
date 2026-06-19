from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DomainEvent:
    name: str
    payload: dict[str, Any]


def build_event(name: str, payload: dict[str, Any]) -> DomainEvent:
    return DomainEvent(name=name, payload=payload)
