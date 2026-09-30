"""Shared helpers for the pathway-structure services (steps, branches, transitions, groups)."""

import re
from typing import Any, TypeVar

from sqlalchemy.orm import Session

from chembook3d.models import Status

T = TypeVar("T")

COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")


class RecordError(ValueError):
    """The request breaks a rule; nothing was changed."""


class RecordNotFound(LookupError):
    pass


def get(session: Session, model: type[T], record_id: str | None, what: str) -> T:
    record = session.get(model, record_id) if record_id else None
    if record is None:
        raise RecordNotFound(f"{what} not found")
    return record


def text_value(field: str, value: Any) -> str:
    if not isinstance(value, str):
        raise RecordError(f"{field} must be text")
    return value


def status_value(value: Any) -> str:
    if value not in {s.value for s in Status}:
        raise RecordError(f"unknown status '{value}'")
    return value


def colour_value(value: Any) -> str:
    if not isinstance(value, str) or not COLOUR.match(value):
        raise RecordError("colour must be written as #rrggbb")
    return value.lower()


def number_value(field: str, value: Any) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise RecordError(f"{field} must be a number")
    return float(value)
