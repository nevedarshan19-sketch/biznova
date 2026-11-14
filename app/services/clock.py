"""Business timezone helpers. Timestamps are stored in naive UTC; user-facing
dates are interpreted in the configured business timezone."""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TZ = "Asia/Kolkata"
COMMON_TIMEZONES = [
    "Asia/Kolkata",
    "Asia/Dubai",
    "Asia/Singapore",
    "Europe/London",
    "Europe/Berlin",
    "America/New_York",
    "America/Chicago",
    "America/Los_Angeles",
    "Australia/Sydney",
    "UTC",
]


def coerce_zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or DEFAULT_TZ)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TZ)


def business_now(tz_name: str | None = None) -> datetime:
    return datetime.now(coerce_zone(tz_name))


def business_today(tz_name: str | None = None) -> date:
    return business_now(tz_name).date()


def utc_to_local(value: datetime | None, tz_name: str | None = None) -> datetime | None:
    if value is None:
        return None
    aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return aware.astimezone(coerce_zone(tz_name))
