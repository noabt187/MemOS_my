from datetime import datetime

import pytest

from memos.memories.textual.event_lifecycle import transition_to_due_unverified
from memos.memories.textual.item import TextualMemoryItem, TreeNodeTextualMemoryMetadata
from memos.memories.textual.relationship import EventMemoryInfo


MEMORY_ID = "26cf4a79-85a9-4faf-a891-3588e4f74472"


def make_event(
    *,
    event_status: str = "planned",
    memory_status: str = "activated",
    version: int = 3,
) -> TextualMemoryItem:
    return TextualMemoryItem(
        id=MEMORY_ID,
        memory="用户计划于2026年9月1日10:00参加A公司面试。",
        metadata=TreeNodeTextualMemoryMetadata(
            user_id="default",
            status=memory_status,
            memory_type="LongTermMemory",
            version=version,
            updated_at="2026-08-31T12:00:00+08:00",
            info={
                "record_type": "event",
                "event_status": event_status,
                "event_time": "2026-09-01T10:00:00+08:00",
            },
        ),
    )


@pytest.mark.parametrize("event_status", ["planned", "ongoing"])
def test_transition_marks_due_event_without_changing_its_facts(event_status: str) -> None:
    event = make_event(event_status=event_status)
    observed_at = datetime.fromisoformat("2026-09-01T10:01:00+08:00")

    result = transition_to_due_unverified(
        event,
        expected_version=3,
        observed_at=observed_at,
    )

    assert result.outcome == "applied"
    assert result.memory is not None
    assert result.memory.id == event.id
    assert result.memory.memory == event.memory
    assert result.memory.metadata.info == {
        "record_type": "event",
        "event_status": "due_unverified",
        "event_time": "2026-09-01T10:00:00+08:00",
    }
    assert result.memory.metadata.version == 4
    assert result.memory.metadata.updated_at == observed_at.isoformat()
    assert result.memory.metadata.sources == event.metadata.sources
    assert len(result.memory.metadata.history) == 1
    assert result.memory.metadata.history[0].version == 3
    assert result.memory.metadata.history[0].memory == event.memory
    assert event.metadata.info["event_status"] == event_status
    assert event.metadata.version == 3


def test_transition_rejects_stale_expected_version() -> None:
    result = transition_to_due_unverified(
        make_event(version=4),
        expected_version=3,
        observed_at=datetime.fromisoformat("2026-09-01T10:01:00+08:00"),
    )

    assert result.outcome == "conflict"
    assert result.memory is None
    assert result.current_version == 4


def test_immediate_retry_of_applied_due_transition_is_idempotent() -> None:
    result = transition_to_due_unverified(
        make_event(event_status="due_unverified", version=4),
        expected_version=3,
        observed_at=datetime.fromisoformat("2026-09-01T10:02:00+08:00"),
    )

    assert result.outcome == "no_op"
    assert result.reason == "already_due_unverified"


@pytest.mark.parametrize("event_status", ["due_unverified", "completed", "cancelled"])
def test_old_request_version_does_not_bypass_conflict_for_closed_state(event_status: str) -> None:
    result = transition_to_due_unverified(
        make_event(event_status=event_status, version=5),
        expected_version=3,
        observed_at=datetime.fromisoformat("2026-09-01T10:02:00+08:00"),
    )

    assert result.outcome == "conflict"
    assert result.current_version == 5


@pytest.mark.parametrize(
    ("event_status", "memory_status"),
    [
        ("due_unverified", "activated"),
        ("completed", "activated"),
        ("cancelled", "activated"),
        ("planned", "archived"),
        ("ongoing", "deleted"),
    ],
)
def test_transition_is_idempotent_or_closed_for_ineligible_events(
    event_status: str,
    memory_status: str,
) -> None:
    event = make_event(event_status=event_status, memory_status=memory_status)

    result = transition_to_due_unverified(
        event,
        expected_version=event.metadata.version,
        observed_at=datetime.fromisoformat("2026-09-01T10:01:00+08:00"),
    )

    assert result.outcome == "no_op"
    assert result.memory is None
    assert result.current_status == event_status
    assert result.current_version == event.metadata.version


def test_transition_does_not_modify_non_event_memory() -> None:
    memory = make_event()
    memory.metadata.info = {"record_type": "person_relationship_summary"}

    result = transition_to_due_unverified(
        memory,
        expected_version=memory.metadata.version,
        observed_at=datetime.fromisoformat("2026-09-01T10:01:00+08:00"),
    )

    assert result.outcome == "no_op"
    assert result.memory is None


def test_event_info_accepts_due_unverified_as_a_fact_status() -> None:
    info = EventMemoryInfo(event_status="due_unverified")

    assert info.event_status == "due_unverified"
