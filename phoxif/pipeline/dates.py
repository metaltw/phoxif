"""Deterministic date-evidence ladder for photo and messaging imports."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_FULL_PATTERNS = (
    re.compile(r"^(\d{8})[-_](\d{6})"),
    re.compile(r"^IMG_(\d{8})_(\d{6})", re.IGNORECASE),
)
_EPOCH_PATTERNS = (
    re.compile(r"^mmexport(\d{13})", re.IGNORECASE),
    re.compile(r"^wx_camera_(\d{13})", re.IGNORECASE),
)
_WHATSAPP_PATTERN = re.compile(r"^IMG-(\d{8})-WA\d+", re.IGNORECASE)
_FOLDER_DAY = re.compile(r"^((?:19|20)\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?([0-2]\d|3[01])")
_FOLDER_MONTH = re.compile(r"^((?:19|20)\d{2})(?:[-_.年]?(0[1-9]|1[0-2]))?")
_SUSPICIOUS_DATES = {(1970, 1, 1), (1980, 1, 1), (2000, 1, 1)}


@dataclass(frozen=True)
class DateEvidence:
    """One accepted rung in the date-confidence ladder."""

    value: datetime
    source: str
    confidence: int
    estimated: bool
    precision: str | None = None

    @property
    def exif_value(self) -> str:
        return self.value.strftime("%Y:%m:%d %H:%M:%S")

    @property
    def keywords(self) -> list[str]:
        if not self.estimated:
            return []
        values = ["phoxif:date-estimated", f"phoxif:date-src:{self.source}"]
        if self.precision is not None:
            values.append(f"phoxif:date-precision:{self.precision}")
        return values


def _accepted(
    value: datetime,
    *,
    source: str,
    confidence: int,
    estimated: bool,
    earliest: datetime,
    now: datetime,
    precision: str | None = None,
) -> DateEvidence | None:
    if value.tzinfo is None or earliest.tzinfo is None or now.tzinfo is None:
        raise ValueError("Date sanity bounds must be timezone-aware")
    if not earliest <= value <= now:
        return None
    if (value.year, value.month, value.day) in _SUSPICIOUS_DATES:
        return None
    return DateEvidence(value, source, confidence, estimated, precision)


def parse_native(
    value: str | None,
    *,
    timezone_name: str,
    earliest: datetime,
    now: datetime,
) -> DateEvidence | None:
    """Parse a trustworthy native media date without marking it estimated."""
    if not value:
        return None
    parsed: datetime | None = None
    for pattern in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(value[:19], pattern).replace(tzinfo=ZoneInfo(timezone_name))
            break
        except ValueError:
            continue
    if parsed is None:
        return None
    return _accepted(
        parsed,
        source="native-exif",
        confidence=1,
        estimated=False,
        earliest=earliest,
        now=now,
    )


def parse_filename(
    name: str,
    *,
    timezone_name: str,
    earliest: datetime,
    now: datetime,
) -> DateEvidence | None:
    """Parse supported camera, WeChat, and WhatsApp filename evidence."""
    zone = ZoneInfo(timezone_name)
    stem = Path(name).stem
    for pattern in _FULL_PATTERNS:
        match = pattern.match(stem)
        if match is None:
            continue
        try:
            value = datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S").replace(tzinfo=zone)
        except ValueError:
            return None
        return _accepted(
            value,
            source="filename-date",
            confidence=2,
            estimated=True,
            earliest=earliest,
            now=now,
        )

    for pattern in _EPOCH_PATTERNS:
        match = pattern.match(stem)
        if match is None:
            continue
        value = datetime.fromtimestamp(int(match.group(1)) / 1000, tz=timezone.utc).astimezone(zone)
        epoch_floor = datetime(2011, 1, 1, tzinfo=zone)
        return _accepted(
            value,
            source="filename-epoch",
            confidence=3,
            estimated=True,
            earliest=max(earliest, epoch_floor),
            now=now,
        )

    match = _WHATSAPP_PATTERN.match(stem)
    if match is None:
        return None
    try:
        day = datetime.strptime(match.group(1), "%Y%m%d").date()
        value = datetime.combine(day, time(12), tzinfo=zone)
    except ValueError:
        return None
    return _accepted(
        value,
        source="filename-date",
        confidence=3,
        estimated=True,
        precision="day",
        earliest=max(earliest, datetime(2010, 1, 1, tzinfo=zone)),
        now=now,
    )


def parse_folder(
    original_path: Path,
    *,
    timezone_name: str,
    earliest: datetime,
    now: datetime,
) -> DateEvidence | None:
    """Use the innermost dated folder, preferring day over month/year matches."""
    zone = ZoneInfo(timezone_name)
    for part in reversed(original_path.parent.parts):
        day_match = _FOLDER_DAY.match(part)
        if day_match is not None:
            try:
                value = datetime(
                    int(day_match.group(1)),
                    int(day_match.group(2)),
                    int(day_match.group(3)),
                    12,
                    tzinfo=zone,
                )
            except ValueError:
                continue
            accepted = _accepted(
                value,
                source="folder-name",
                confidence=5,
                estimated=True,
                precision="day",
                earliest=earliest,
                now=now,
            )
            if accepted is not None:
                return accepted

        month_match = _FOLDER_MONTH.match(part)
        if month_match is None:
            continue
        year = int(month_match.group(1))
        month_text = month_match.group(2)
        month = int(month_text) if month_text else 7
        precision = "month" if month_text else "year"
        day = 15 if month_text else 1
        accepted = _accepted(
            datetime(year, month, day, 12, tzinfo=zone),
            source="folder-name",
            confidence=5,
            estimated=True,
            precision=precision,
            earliest=earliest,
            now=now,
        )
        if accepted is not None:
            return accepted
    return None


def parse_mtime(
    value: str | None,
    *,
    timezone_name: str,
    earliest: datetime,
    now: datetime,
) -> DateEvidence | None:
    """Use immutable ingest mtime evidence as the lowest-confidence fallback."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
    else:
        parsed = parsed.astimezone(ZoneInfo(timezone_name))
    return _accepted(
        parsed,
        source="ingest-mtime",
        confidence=6,
        estimated=True,
        earliest=earliest,
        now=now,
    )


def interpolate(
    before: tuple[datetime, DateEvidence],
    target_mtime: datetime,
    after: tuple[datetime, DateEvidence],
    *,
    max_span_hours: int = 48,
) -> DateEvidence | None:
    """Linearly interpolate a target bracketed by two trusted dated neighbors."""
    before_mtime, before_date = before
    after_mtime, after_date = after
    span = (after_mtime - before_mtime).total_seconds()
    if span <= 0 or span > max_span_hours * 3600:
        return None
    offset = (target_mtime - before_mtime).total_seconds()
    if not 0 <= offset <= span:
        return None
    date_span = (after_date.value - before_date.value).total_seconds()
    value = before_date.value + (after_date.value - before_date.value) * (offset / span)
    if abs(date_span) > max_span_hours * 3600:
        return None
    return DateEvidence(value, "batch-interp", 4, True)
