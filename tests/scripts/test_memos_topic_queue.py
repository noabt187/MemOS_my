from __future__ import annotations

import sys

from datetime import datetime, timedelta
from pathlib import Path

import pytest


SCRIPTS_DIR = Path(__file__).parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from memos_topic_queue import (
    DEFAULT_TOPIC_QUEUE_POLICY,
    TOPIC_QUEUE_POLICY_VERSION,
    TopicEventWindow,
    TopicQueuePolicy,
    TopicTimeEvidence,
    calculate_approaching_bonus,
    calculate_core_stagnation_penalty,
    calculate_demoted_candidate_penalty,
    calculate_queue_score,
    latest_scheduled_slot,
    next_scheduled_slot,
    resolve_topic_event_window,
)


SHANGHAI = "Asia/Shanghai"


def _now(value: str = "2026-09-01T10:00:00+08:00") -> datetime:
    return datetime.fromisoformat(value)


def _precise_window(start_at: str, end_at: str | None = None) -> TopicEventWindow:
    return TopicEventWindow(
        start_at=start_at,
        end_at=end_at or start_at,
        precision="datetime",
        source_memory_id="memory-1",
    )


def _day_window(start_at: str, end_at: str | None = None) -> TopicEventWindow:
    return TopicEventWindow(
        start_at=start_at,
        end_at=end_at or start_at,
        precision="day",
        source_memory_id="memory-1",
    )


def test_queue_policy_has_fixed_three_plus_twenty_seven_defaults():
    assert TopicQueuePolicy() == DEFAULT_TOPIC_QUEUE_POLICY
    assert DEFAULT_TOPIC_QUEUE_POLICY.core_limit == 3
    assert DEFAULT_TOPIC_QUEUE_POLICY.visible_candidate_limit == 27
    assert DEFAULT_TOPIC_QUEUE_POLICY.scheduled_promotion_margin == 5
    assert DEFAULT_TOPIC_QUEUE_POLICY.immediate_promotion_margin == 10
    assert DEFAULT_TOPIC_QUEUE_POLICY.timezone_name == SHANGHAI
    assert TOPIC_QUEUE_POLICY_VERSION == 1


def test_resolve_topic_event_window_uses_field_priority_and_latest_evidence():
    evidence = [
        TopicTimeEvidence(
            memory_id="older",
            source_recorded_at="2026-08-30T09:00:00+08:00",
            created_at="2026-08-30T09:00:00+08:00",
            event_start_time=None,
            event_start_at="2026-09-03T08:00:00+08:00",
            event_time="2026-09-04T08:00:00+08:00",
            event_end_time=None,
            event_end_at=None,
        ),
        TopicTimeEvidence(
            memory_id="newer",
            source_recorded_at="2026-08-31T09:00:00+08:00",
            created_at="2026-08-29T09:00:00+08:00",
            event_start_time="2026-09-05T10:30:00+08:00",
            event_start_at="2026-09-06T10:30:00+08:00",
            event_time="2026-09-07T10:30:00+08:00",
            event_end_time="2026-09-05T12:00:00+08:00",
            event_end_at="2026-09-05T13:00:00+08:00",
        ),
    ]

    window = resolve_topic_event_window(evidence)

    assert window == TopicEventWindow(
        start_at="2026-09-05T10:30:00+08:00",
        end_at="2026-09-05T12:00:00+08:00",
        precision="datetime",
        source_memory_id="newer",
        conflict=False,
    )


def test_resolve_topic_event_window_detects_equally_new_conflicting_times():
    evidence = [
        TopicTimeEvidence(
            memory_id="memory-1",
            source_recorded_at="2026-08-31T09:00:00+08:00",
            created_at=None,
            event_start_time=None,
            event_start_at="2026-09-02T09:00:00+08:00",
            event_time=None,
            event_end_time=None,
            event_end_at=None,
        ),
        TopicTimeEvidence(
            memory_id="memory-2",
            source_recorded_at="2026-08-31T09:00:00+08:00",
            created_at=None,
            event_start_time=None,
            event_start_at="2026-09-03T09:00:00+08:00",
            event_time=None,
            event_end_time=None,
            event_end_at=None,
        ),
    ]

    window = resolve_topic_event_window(evidence)

    assert window.conflict is True
    assert window.precision == "unknown"
    assert window.start_at is None
    assert window.end_at is None
    assert calculate_approaching_bonus(window, _now(), SHANGHAI) == 0


@pytest.mark.parametrize(
    ("hours_until_event", "expected"),
    [
        (169, 0),
        (168, 4),
        (73, 4),
        (72, 8),
        (49, 8),
        (48, 12),
        (25, 12),
        (24, 16),
    ],
)
def test_precise_time_approaching_bonus_uses_all_boundaries(
    hours_until_event: int,
    expected: float,
):
    now = _now()
    event_at = now + timedelta(hours=hours_until_event)

    assert (
        calculate_approaching_bonus(
            _precise_window(event_at.isoformat()),
            now,
            SHANGHAI,
        )
        == expected
    )


def test_event_calendar_day_keeps_twenty_points_until_local_midnight():
    window = _precise_window("2026-09-01T08:00:00+08:00")

    assert calculate_approaching_bonus(window, _now("2026-09-01T07:00:00+08:00"), SHANGHAI) == 20
    assert calculate_approaching_bonus(window, _now("2026-09-01T23:59:59+08:00"), SHANGHAI) == 20
    assert calculate_approaching_bonus(window, _now("2026-09-02T00:00:00+08:00"), SHANGHAI) == 0


@pytest.mark.parametrize(
    ("event_date", "expected"),
    [
        ("2026-09-01", 20),
        ("2026-09-02", 16),
        ("2026-09-03", 12),
        ("2026-09-04", 8),
        ("2026-09-05", 4),
        ("2026-09-08", 4),
        ("2026-09-09", 0),
        ("2026-08-31", 0),
    ],
)
def test_date_only_approaching_bonus_does_not_invent_an_hour(
    event_date: str,
    expected: float,
):
    assert calculate_approaching_bonus(_day_window(event_date), _now(), SHANGHAI) == expected


def test_active_date_range_receives_twenty_points():
    day_range = _day_window("2026-08-31", "2026-09-03")
    precise_range = _precise_window(
        "2026-08-31T18:00:00+08:00",
        "2026-09-03T09:00:00+08:00",
    )

    assert calculate_approaching_bonus(day_range, _now(), SHANGHAI) == 20
    assert calculate_approaching_bonus(precise_range, _now(), SHANGHAI) == 20
    assert calculate_approaching_bonus(day_range, _now("2026-09-04T00:00:00+08:00"), SHANGHAI) == 0


def test_month_year_unknown_and_conflicting_times_receive_zero():
    windows = [
        TopicEventWindow("2026-09", "2026-09", "month", "memory-1"),
        TopicEventWindow("2026", "2026", "year", "memory-1"),
        TopicEventWindow(None, None, "unknown", None),
        TopicEventWindow(None, None, "unknown", None, conflict=True),
    ]

    assert [calculate_approaching_bonus(item, _now(), SHANGHAI) for item in windows] == [
        0,
        0,
        0,
        0,
    ]


def test_new_and_refreshed_candidates_never_decay():
    now = _now("2026-09-30T12:00:00+08:00")

    assert calculate_demoted_candidate_penalty(None, 0, now) == 0
    assert calculate_queue_score(70, 0, 0).decay_penalty == 0


@pytest.mark.parametrize(
    ("elapsed_days", "expected"),
    [(0, 0), (2, 0), (3, 3), (4, 3), (5, 6), (7, 6), (8, 10), (14, 10), (15, 15)],
)
def test_core_decay_uses_later_of_core_entry_and_last_evidence(
    elapsed_days: int,
    expected: float,
):
    now = _now("2026-09-30T12:00:00+08:00")
    stagnation_start = now - timedelta(days=elapsed_days)
    entered_at = stagnation_start - timedelta(days=5)

    assert (
        calculate_core_stagnation_penalty(
            entered_at.isoformat(),
            stagnation_start.isoformat(),
            0,
            now,
        )
        == expected
    )


def test_core_decay_never_clears_a_retained_penalty_without_new_evidence():
    now = _now("2026-09-30T12:00:00+08:00")

    assert (
        calculate_core_stagnation_penalty(
            (now - timedelta(days=3)).isoformat(),
            None,
            12,
            now,
        )
        == 12
    )


@pytest.mark.parametrize(
    ("elapsed_days", "penalty_at_demotion", "expected"),
    [
        (0, 0, 5),
        (1, 0, 5),
        (2, 0, 10),
        (3, 0, 10),
        (4, 0, 15),
        (7, 0, 15),
        (8, 0, 20),
        (30, 0, 20),
        (2, 17, 17),
        (30, 30, 20),
    ],
)
def test_demoted_decay_preserves_existing_penalty_and_caps_at_twenty(
    elapsed_days: int,
    penalty_at_demotion: float,
    expected: float,
):
    now = _now("2026-09-30T12:00:00+08:00")
    demoted_at = now - timedelta(days=elapsed_days)

    assert (
        calculate_demoted_candidate_penalty(
            demoted_at.isoformat(),
            penalty_at_demotion,
            now,
        )
        == expected
    )


@pytest.mark.parametrize(
    ("importance", "bonus", "penalty", "expected"),
    [
        (70, 16, 10, 76),
        (100, 20, 0, 120),
        (100, 50, 0, 120),
        (-10, 0, 20, 0),
        (60.126, 4.129, 3.111, 61.14),
    ],
)
def test_queue_score_is_importance_plus_bonus_minus_decay_clamped_to_120(
    importance: float,
    bonus: float,
    penalty: float,
    expected: float,
):
    result = calculate_queue_score(importance, bonus, penalty)

    assert result.queue_score == expected
    assert 0 <= result.importance_score <= 100
    assert 0 <= result.approaching_bonus <= 20
    assert 0 <= result.decay_penalty <= 20


@pytest.mark.parametrize(
    ("now_value", "latest_value", "next_value"),
    [
        (
            "2026-09-01T00:00:00+08:00",
            "2026-09-01T00:00:00+08:00",
            "2026-09-01T12:00:00+08:00",
        ),
        (
            "2026-09-01T11:59:59+08:00",
            "2026-09-01T00:00:00+08:00",
            "2026-09-01T12:00:00+08:00",
        ),
        (
            "2026-09-01T12:00:00+08:00",
            "2026-09-01T12:00:00+08:00",
            "2026-09-02T00:00:00+08:00",
        ),
        (
            "2026-09-01T23:59:59+08:00",
            "2026-09-01T12:00:00+08:00",
            "2026-09-02T00:00:00+08:00",
        ),
    ],
)
def test_latest_and_next_slots_are_midnight_and_noon_in_shanghai(
    now_value: str,
    latest_value: str,
    next_value: str,
):
    now = _now(now_value)

    assert latest_scheduled_slot(now, SHANGHAI) == _now(latest_value)
    assert next_scheduled_slot(now, SHANGHAI) == _now(next_value)
