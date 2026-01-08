from __future__ import annotations

import datetime as _dt


def today_iso() -> str:
    return _dt.date.today().isoformat()


def validate_iso_date(value: str) -> str:
    """
    Validate YYYY-MM-DD date strings (ISO-8601 date).
    Returns the input if valid, raises ValueError if not.
    """
    try:
        _dt.date.fromisoformat(value)
    except Exception as e:  # pragma: no cover
        raise ValueError(f"Invalid date '{value}'. Expected YYYY-MM-DD.") from e
    return value


def coalesce(a, b):
    return a if a is not None else b

