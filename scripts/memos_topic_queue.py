"""Deterministic priority rules for the Topic 3+27 queues.

This module is deliberately independent from MemOS storage, JSON state, and
models.  Callers normalize memory metadata into :class:`TopicTimeEvidence` and
pass explicit timestamps into the functions below.
"""

from __future__ import annotations

import math
import re

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


TOPIC_CORE_LIMIT = 3
TOPIC_VISIBLE_CANDIDATE_LIMIT = 27
TOPIC_SCORE_THRESHOLD = 60.0
TOPIC_SCHEDULED_PROMOTION_MARGIN = 5.0
TOPIC_IMMEDIATE_PROMOTION_MARGIN = 10.0
TOPIC_QUEUE_SCORE_MAX = 120.0
TOPIC_TIMEZONE = "Asia/Shanghai"
TOPIC_QUEUE_POLICY_VERSION = 1

_DAY_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH_PATTERN = re.compile(r"^\d{4}-\d{2}$")
_YEAR_PATTERN = re.compile(r"^\d{4}$")


@dataclass(frozen=True)
class TopicTimeEvidence:
    """Normalized time fields from one supporting memory."""

    memory_id: str
    source_recorded_at: str | None
    created_at: str | None
    event_start_time: str | None
    event_start_at: str | None
    event_time: str | None
    event_end_time: str | None
    event_end_at: str | None


@dataclass(frozen=True)
class TopicEventWindow:
    """The newest unambiguous event window for one Topic."""

    start_at: str | None
    end_at: str | None
    precision: str
    source_memory_id: str | None
    conflict: bool = False


@dataclass(frozen=True)
class TopicQueueScore:
    """Inspectable parts of the final queue score."""

    importance_score: float
    approaching_bonus: float
    decay_penalty: float
    queue_score: float


@dataclass(frozen=True)
class TopicQueuePolicy:
    """The fixed first-version Topic queue policy."""

    core_limit: int = TOPIC_CORE_LIMIT
    visible_candidate_limit: int = TOPIC_VISIBLE_CANDIDATE_LIMIT
    scheduled_promotion_margin: float = TOPIC_SCHEDULED_PROMOTION_MARGIN
    immediate_promotion_margin: float = TOPIC_IMMEDIATE_PROMOTION_MARGIN
    timezone_name: str = TOPIC_TIMEZONE


DEFAULT_TOPIC_QUEUE_POLICY = TopicQueuePolicy()


@dataclass(frozen=True)
class _ResolvedEvidenceWindow:
    window: TopicEventWindow
    recorded_at: datetime | None
    source_index: int


def _clean(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _parse_iso_datetime(value: str | None) -> datetime | None:
    cleaned = _clean(value)
    if cleaned is None:
        return None
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _value_precision(value: str | None) -> str:
    cleaned = _clean(value)
    if cleaned is None:
        return "unknown"
    if _DAY_PATTERN.fullmatch(cleaned):
        try:
            date.fromisoformat(cleaned)
        except ValueError:
            return "unknown"
        return "day"
    if _MONTH_PATTERN.fullmatch(cleaned):
        try:
            year_text, month_text = cleaned.split("-", 1)
            date(int(year_text), int(month_text), 1)
        except ValueError:
            return "unknown"
        return "month"
    if _YEAR_PATTERN.fullmatch(cleaned):
        return "year"
    return "datetime" if _parse_iso_datetime(cleaned) is not None else "unknown"


def _first_value(*values: str | None) -> str | None:
    return next((cleaned for value in values if (cleaned := _clean(value)) is not None), None)


def _canonical_time(value: str | None, precision: str) -> str | None:
    cleaned = _clean(value)
    if cleaned is None:
        return None
    if precision == "datetime":
        parsed = _parse_iso_datetime(cleaned)
        return parsed.isoformat() if parsed is not None else None
    return cleaned


def _canonical_window(window: TopicEventWindow) -> tuple[str | None, str | None, str]:
    return (
        _canonical_time(window.start_at, window.precision),
        _canonical_time(window.end_at, window.precision),
        window.precision,
    )


def _window_from_evidence(
    item: TopicTimeEvidence,
    source_index: int,
) -> _ResolvedEvidenceWindow | None:
    start_at = _first_value(item.event_start_time, item.event_start_at, item.event_time)
    end_at = _first_value(item.event_end_time, item.event_end_at)
    if start_at is None and end_at is None:
        return None
    if start_at is None:
        start_at = end_at
    if end_at is None:
        end_at = start_at
    assert start_at is not None
    assert end_at is not None

    precision = _value_precision(start_at)
    end_precision = _value_precision(end_at)
    if precision == "unknown" or end_precision == "unknown":
        return None

    recorded_at = _parse_iso_datetime(item.source_recorded_at)
    if recorded_at is None:
        recorded_at = _parse_iso_datetime(item.created_at)
    return _ResolvedEvidenceWindow(
        window=TopicEventWindow(
            start_at=start_at,
            end_at=end_at,
            precision=precision,
            source_memory_id=item.memory_id,
        ),
        recorded_at=recorded_at,
        source_index=source_index,
    )


def resolve_topic_event_window(evidence: list[TopicTimeEvidence]) -> TopicEventWindow:
    """Choose the newest clear event time without inventing missing precision."""

    candidates = [
        candidate
        for index, item in enumerate(evidence)
        if (candidate := _window_from_evidence(item, index)) is not None
    ]
    if not candidates:
        return TopicEventWindow(None, None, "unknown", None)

    dated = [item for item in candidates if item.recorded_at is not None]
    if dated:
        newest_at = max(item.recorded_at for item in dated)
        newest = [item for item in dated if item.recorded_at == newest_at]
        canonical = {_canonical_window(item.window) for item in newest}
        if len(canonical) > 1:
            return TopicEventWindow(None, None, "unknown", None, conflict=True)
        return max(newest, key=lambda item: item.source_index).window

    # Without a usable record timestamp, preserve the supplied source order and
    # treat the last clear item as the newest instead of manufacturing a date.
    return max(candidates, key=lambda item: item.source_index).window


def _as_zone_datetime(value: datetime, zone: ZoneInfo) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=zone)
    return value.astimezone(zone)


def _event_datetime(value: str | None, zone: ZoneInfo) -> datetime | None:
    cleaned = _clean(value)
    if cleaned is None or _DAY_PATTERN.fullmatch(cleaned):
        return None
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(zone)


def _event_date(value: str | None, zone: ZoneInfo) -> date | None:
    cleaned = _clean(value)
    if cleaned is None:
        return None
    if _DAY_PATTERN.fullmatch(cleaned):
        try:
            return date.fromisoformat(cleaned)
        except ValueError:
            return None
    parsed = _event_datetime(cleaned, zone)
    return parsed.date() if parsed is not None else None


def calculate_approaching_bonus(
    window: TopicEventWindow,
    now: datetime,
    timezone_name: str = TOPIC_TIMEZONE,
) -> float:
    """Return the deterministic 0-20 bonus for an approaching event."""

    if window.conflict or window.precision not in {"day", "datetime", "hour", "minute"}:
        return 0.0

    zone = ZoneInfo(timezone_name)
    local_now = _as_zone_datetime(now, zone)
    start_date = _event_date(window.start_at, zone)
    end_date = _event_date(window.end_at, zone) or start_date
    if start_date is None or end_date is None or end_date < start_date:
        return 0.0

    today = local_now.date()
    if start_date <= today <= end_date:
        return 20.0
    if today > end_date:
        return 0.0

    if window.precision == "day":
        days_until_event = (start_date - today).days
        if days_until_event == 1:
            return 16.0
        if days_until_event == 2:
            return 12.0
        if days_until_event == 3:
            return 8.0
        if 4 <= days_until_event <= 7:
            return 4.0
        return 0.0

    start_at = _event_datetime(window.start_at, zone)
    if start_at is None:
        return 0.0
    hours_until_event = (start_at - local_now).total_seconds() / 3600
    if hours_until_event <= 0:
        return 0.0
    if hours_until_event <= 24:
        return 16.0
    if hours_until_event <= 48:
        return 12.0
    if hours_until_event <= 72:
        return 8.0
    if hours_until_event <= 168:
        return 4.0
    return 0.0


def _bounded_number(value: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return minimum
    if not math.isfinite(number):
        return minimum
    return max(minimum, min(maximum, number))


def _elapsed_complete_days(start_at: str | None, now: datetime) -> int | None:
    parsed = _parse_iso_datetime(start_at)
    if parsed is None:
        return None
    reference_now = now
    if reference_now.tzinfo is None:
        reference_now = reference_now.replace(tzinfo=timezone.utc)
    elapsed_seconds = (reference_now.astimezone(timezone.utc) - parsed).total_seconds()
    return max(0, math.floor(elapsed_seconds / 86_400))


def _core_decay_target(stagnation_days: int) -> float:
    if stagnation_days <= 2:
        return 0.0
    if stagnation_days <= 4:
        return 3.0
    if stagnation_days <= 7:
        return 6.0
    if stagnation_days <= 14:
        return 10.0
    return 15.0


def calculate_core_stagnation_penalty(
    core_entered_at: str | None,
    last_evidence_at: str | None,
    current_penalty: float,
    now: datetime,
) -> float:
    """Increase core decay from the later entry/evidence time, never decrease it."""

    current = _bounded_number(current_penalty, 0.0, 20.0)
    starts = [
        parsed
        for value in (core_entered_at, last_evidence_at)
        if (parsed := _parse_iso_datetime(value)) is not None
    ]
    if not starts:
        return current
    stagnation_start = max(starts)
    reference_now = now
    if reference_now.tzinfo is None:
        reference_now = reference_now.replace(tzinfo=timezone.utc)
    elapsed_seconds = (reference_now.astimezone(timezone.utc) - stagnation_start).total_seconds()
    stagnation_days = max(0, math.floor(elapsed_seconds / 86_400))
    return min(20.0, max(current, _core_decay_target(stagnation_days)))


def _demoted_decay_target(demoted_days: int) -> float:
    if demoted_days <= 1:
        return 5.0
    if demoted_days <= 3:
        return 10.0
    if demoted_days <= 7:
        return 15.0
    return 20.0


def calculate_demoted_candidate_penalty(
    demoted_at: str | None,
    penalty_at_demotion: float,
    now: datetime,
) -> float:
    """Return fast decay for a former core Topic, capped at twenty points."""

    retained = _bounded_number(penalty_at_demotion, 0.0, 20.0)
    demoted_days = _elapsed_complete_days(demoted_at, now)
    if demoted_days is None:
        return retained
    return min(20.0, max(retained, _demoted_decay_target(demoted_days)))


def calculate_queue_score(
    importance_score: float,
    approaching_bonus: float,
    decay_penalty: float,
) -> TopicQueueScore:
    """Calculate ``importance + approaching - decay`` with explicit bounds."""

    importance = _bounded_number(importance_score, 0.0, 100.0)
    bonus = _bounded_number(approaching_bonus, 0.0, 20.0)
    penalty = _bounded_number(decay_penalty, 0.0, 20.0)
    score = round(
        max(0.0, min(TOPIC_QUEUE_SCORE_MAX, importance + bonus - penalty)),
        2,
    )
    return TopicQueueScore(
        importance_score=importance,
        approaching_bonus=bonus,
        decay_penalty=penalty,
        queue_score=score,
    )


def latest_scheduled_slot(
    now: datetime,
    timezone_name: str = TOPIC_TIMEZONE,
) -> datetime:
    """Return the latest local 00:00 or 12:00 slot at or before ``now``."""

    zone = ZoneInfo(timezone_name)
    local_now = _as_zone_datetime(now, zone)
    slot_time = time(hour=12) if local_now.time() >= time(hour=12) else time.min
    return datetime.combine(local_now.date(), slot_time, tzinfo=zone)


def next_scheduled_slot(
    now: datetime,
    timezone_name: str = TOPIC_TIMEZONE,
) -> datetime:
    """Return the next local 00:00 or 12:00 slot strictly after ``now``."""

    zone = ZoneInfo(timezone_name)
    local_now = _as_zone_datetime(now, zone)
    if local_now.time() < time(hour=12):
        return datetime.combine(local_now.date(), time(hour=12), tzinfo=zone)
    return datetime.combine(local_now.date() + timedelta(days=1), time.min, tzinfo=zone)


__all__ = [
    "DEFAULT_TOPIC_QUEUE_POLICY",
    "TOPIC_CORE_LIMIT",
    "TOPIC_IMMEDIATE_PROMOTION_MARGIN",
    "TOPIC_QUEUE_POLICY_VERSION",
    "TOPIC_QUEUE_SCORE_MAX",
    "TOPIC_SCHEDULED_PROMOTION_MARGIN",
    "TOPIC_SCORE_THRESHOLD",
    "TOPIC_TIMEZONE",
    "TOPIC_VISIBLE_CANDIDATE_LIMIT",
    "TopicEventWindow",
    "TopicQueuePolicy",
    "TopicQueueScore",
    "TopicTimeEvidence",
    "calculate_approaching_bonus",
    "calculate_core_stagnation_penalty",
    "calculate_demoted_candidate_penalty",
    "calculate_queue_score",
    "latest_scheduled_slot",
    "next_scheduled_slot",
    "resolve_topic_event_window",
]
