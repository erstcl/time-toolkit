from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from time_toolkit.dates import parse_time_bound
from time_toolkit.errors import UsageError


def test_relative_time_keeps_exact_minutes():
    now = datetime(2026, 7, 19, 12, 30, tzinfo=ZoneInfo("Europe/Moscow"))
    value = parse_time_bound("30m", timezone_name="Europe/Moscow", now=now)
    expected = datetime(2026, 7, 19, 12, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    assert value == int(expected.timestamp() * 1000)


def test_date_only_until_is_inclusive_in_profile_timezone():
    value = parse_time_bound("2026-07-19", end_of_day=True, timezone_name="Europe/Moscow")
    expected = datetime(2026, 7, 19, 23, 59, 59, 999000, tzinfo=ZoneInfo("Europe/Moscow"))
    assert value == int(expected.timestamp() * 1000)


def test_invalid_date_is_usage_error():
    with pytest.raises(UsageError):
        parse_time_bound("last Tuesday")
