"""UTC storage <-> local display. One timezone setting for the whole app."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

FMT = "%Y-%m-%dT%H:%M:%SZ"


def zone(name):
    """ZoneInfo, except that UTC works without a timezone database (Windows
    has none until tzdata is installed)."""
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name == "UTC":
            return timezone.utc
        raise ZoneInfoNotFoundError(
            f"no timezone {name!r}; on Windows, install tzdata (pip install tzdata)")


def parse_utc(s):
    return datetime.strptime(s, FMT).replace(tzinfo=timezone.utc)


def fmt_utc(dt):
    return dt.astimezone(timezone.utc).strftime(FMT)


def seconds(start_utc, end_utc):
    return int((parse_utc(end_utc) - parse_utc(start_utc)).total_seconds())


def local(s, tz):
    return parse_utc(s).astimezone(zone(tz))


def local_to_utc(date_s, time_s, tz):
    """'2026-09-01', '12:11:21' in tz -> UTC string."""
    dt = datetime.fromisoformat(f"{date_s}T{time_s}").replace(tzinfo=zone(tz))
    return fmt_utc(dt)


def day_bounds_utc(d_from, d_to, tz):
    """Inclusive local dates -> [start, end) in UTC strings."""
    z = zone(tz)
    a = datetime.combine(date.fromisoformat(d_from), datetime.min.time(), z)
    b = datetime.combine(date.fromisoformat(d_to) + timedelta(days=1),
                         datetime.min.time(), z)
    return fmt_utc(a), fmt_utc(b)


def week_start(d, week_start_iso=1):
    """Local date -> first day of its week (week_start_iso: 1=Mon .. 7=Sun)."""
    return d - timedelta(days=(d.isoweekday() - week_start_iso) % 7)


def hms(sec):
    sec = int(round(sec))
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"
