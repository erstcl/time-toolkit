from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from time_toolkit.errors import UsageError

_RELATIVE = re.compile(r"^(\d+)([dhm])$")


def parse_time_bound(
    value: str | None,
    *,
    end_of_day: bool = False,
    timezone_name: str = "UTC",
    now: datetime | None = None,
) -> int | None:
    if not value:
        return None
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise UsageError(f"Unknown timezone: {timezone_name}") from exc

    current = now or datetime.now(tz=zone)
    relative = _RELATIVE.fullmatch(value.strip().lower())
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2)
        delta = {
            "d": timedelta(days=amount),
            "h": timedelta(hours=amount),
            "m": timedelta(minutes=amount),
        }[unit]
        return int((current - delta).timestamp() * 1000)

    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise UsageError(
            f"Invalid time {value!r}; use YYYY-MM-DD, ISO-8601, 7d, 24h, or 30m"
        ) from exc
    date_only = re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw) is not None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    if date_only and end_of_day:
        parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999000)
    return int(parsed.astimezone(UTC).timestamp() * 1000)


def iso_utc(timestamp_ms: int) -> str:
    if not timestamp_ms:
        return ""
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).isoformat()
