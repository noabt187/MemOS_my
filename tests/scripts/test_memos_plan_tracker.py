from __future__ import annotations

import importlib.util
import json
import sys
import threading

from datetime import datetime, timedelta, timezone
from pathlib import Path


SCRIPTS_DIR = Path(__file__).parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
SCRIPT_PATH = SCRIPTS_DIR / "memos_plan_tracker.py"
SPEC = importlib.util.spec_from_file_location("memos_plan_tracker", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
memos_plan_tracker = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = memos_plan_tracker
SPEC.loader.exec_module(memos_plan_tracker)

SHANGHAI = timezone(timedelta(hours=8))


def event_memory(
    memory_id: str,
    event_status: str,
    *,
    version: int = 1,
    event_end_time: str | None = None,
    event_time: str | None = None,
    event_start_time: str | None = None,
    node_status: str = "activated",
    record_type: str = "event",
) -> dict:
    info = {
        "record_type": record_type,
        "event_status": event_status,
    }
    if event_end_time is not None:
        info["event_end_time"] = event_end_time
    if event_time is not None:
        info["event_time"] = event_time
    if event_start_time is not None:
        info["event_start_time"] = event_start_time
    return {
        "id": memory_id,
        "memory": f"event {memory_id}",
        "metadata": {
            "status": node_status,
            "version": version,
            "memory_type": "LongTermMemory",
            "info": info,
        },
    }


class FakeClient:
    def __init__(self, memories: list[dict]) -> None:
        self.memories = {item["id"]: item for item in memories}
        self.transition_calls: list[dict] = []
        self.list_calls: list[tuple[str, str]] = []
        self.transition_outcome = "applied"
        self.fail_transition = False

    def list_text_memories(self, *, user_id: str, cube_id: str) -> list[dict]:
        self.list_calls.append((user_id, cube_id))
        return list(self.memories.values())

    def get_memory(self, memory_id: str) -> dict:
        memory = self.memories.get(memory_id)
        return {"data": memory} if memory is not None else {"data": None}

    def transition_event_lifecycle(self, **kwargs) -> dict:
        if self.fail_transition:
            raise OSError("core unavailable")
        self.transition_calls.append(kwargs)
        memory = self.memories.get(kwargs["memory_id"])
        if self.transition_outcome == "applied" and memory is not None:
            memory["metadata"]["info"]["event_status"] = kwargs["to_status"]
            memory["metadata"]["version"] += 1
        return {
            "data": {
                "outcome": self.transition_outcome,
                "memory_id": kwargs["memory_id"],
            }
        }


def test_planned_check_time_prefers_end_then_event_then_start() -> None:
    end_memory = event_memory(
        "end",
        "planned",
        event_end_time="2026-09-03T12:00:00+08:00",
        event_time="2026-09-02T12:00:00+08:00",
        event_start_time="2026-09-01T12:00:00+08:00",
    )
    event_only = event_memory(
        "event",
        "planned",
        event_time="2026-09-02T12:00:00+08:00",
        event_start_time="2026-09-01T12:00:00+08:00",
    )
    start_only = event_memory(
        "start",
        "planned",
        event_start_time="2026-09-01T12:00:00+08:00",
    )

    assert memos_plan_tracker.tracking_decision(end_memory, timezone_info=SHANGHAI).check_at == (
        datetime(2026, 9, 3, 12, tzinfo=SHANGHAI)
    )
    assert memos_plan_tracker.tracking_decision(event_only, timezone_info=SHANGHAI).check_at == (
        datetime(2026, 9, 2, 12, tzinfo=SHANGHAI)
    )
    assert memos_plan_tracker.tracking_decision(start_only, timezone_info=SHANGHAI).check_at == (
        datetime(2026, 9, 1, 12, tzinfo=SHANGHAI)
    )


def test_date_only_uses_end_of_day_and_relative_time_is_unscheduled() -> None:
    date_only = event_memory("date", "planned", event_time="2026-09-03")
    chinese_date = event_memory("chinese-date", "planned", event_time="2026年9月3日")
    slash_date = event_memory("slash-date", "planned", event_time="2026/9/3")
    short_iso_date = event_memory("short-iso-date", "planned", event_time="2026-9-3")
    relative = event_memory("relative", "planned", event_time="明天上午")

    decision = memos_plan_tracker.tracking_decision(date_only, timezone_info=SHANGHAI)
    assert decision.tracking_state == "scheduled"
    assert decision.check_at == datetime(2026, 9, 3, 23, 59, 59, 999999, tzinfo=SHANGHAI)
    for memory in (chinese_date, slash_date, short_iso_date):
        parsed = memos_plan_tracker.tracking_decision(memory, timezone_info=SHANGHAI)
        assert parsed.check_at == datetime(2026, 9, 3, 23, 59, 59, 999999, tzinfo=SHANGHAI)
    assert (
        memos_plan_tracker.tracking_decision(relative, timezone_info=SHANGHAI).tracking_state
        == "unscheduled"
    )


def test_ongoing_requires_end_time_unless_it_was_already_tracked() -> None:
    ongoing = event_memory("ongoing", "ongoing")
    scheduled = event_memory(
        "scheduled",
        "ongoing",
        event_end_time="2026-09-03T12:00:00+08:00",
    )

    assert memos_plan_tracker.tracking_decision(ongoing, timezone_info=SHANGHAI) is None
    assert (
        memos_plan_tracker.tracking_decision(
            ongoing,
            was_tracked=True,
            timezone_info=SHANGHAI,
        ).tracking_state
        == "waiting_evidence"
    )
    assert (
        memos_plan_tracker.tracking_decision(scheduled, timezone_info=SHANGHAI).tracking_state
        == "scheduled"
    )


def test_store_recovers_from_invalid_json_and_writes_atomically(tmp_path: Path) -> None:
    state_path = tmp_path / "tracker.json"
    state_path.write_text("not json", encoding="utf-8")
    store = memos_plan_tracker.PlanTrackerStore(state_path)

    assert store.snapshot() == {"schema_version": 1, "events": {}}

    store.upsert(
        {
            "memory_id": "memory-1",
            "user_id": "default",
            "cube_id": "default_cube",
            "last_seen_version": 1,
            "check_at": None,
            "next_check_at": None,
            "last_checked_at": None,
            "tracking_state": "unscheduled",
            "failure_count": 0,
            "topic_sync_pending": False,
            "memory": "this event body must never be copied into the index",
        }
    )

    persisted_entry = json.loads(state_path.read_text(encoding="utf-8"))["events"]["memory-1"]
    assert persisted_entry["tracking_state"] == "unscheduled"
    assert "memory" not in persisted_entry
    assert not list(tmp_path.glob("*.tmp"))


def test_reconcile_rebuilds_index_and_removes_ineligible_events(tmp_path: Path) -> None:
    store = memos_plan_tracker.PlanTrackerStore(tmp_path / "tracker.json")
    store.upsert(
        {
            "memory_id": "removed",
            "user_id": "default",
            "cube_id": "default_cube",
            "last_seen_version": 1,
            "check_at": None,
            "next_check_at": None,
            "last_checked_at": None,
            "tracking_state": "waiting_evidence",
            "failure_count": 0,
            "topic_sync_pending": False,
        }
    )
    client = FakeClient(
        [
            event_memory("future", "planned", event_time="2026-09-03T12:00:00+08:00"),
            event_memory("missing-time", "planned"),
            event_memory("ordinary-ongoing", "ongoing"),
            event_memory("done", "completed"),
            event_memory(
                "not-event",
                "planned",
                event_time="2026-09-03T12:00:00+08:00",
                record_type="contact",
            ),
        ]
    )
    tracker = memos_plan_tracker.PlanTracker(
        store=store,
        client=client,
        user_id="default",
        cube_id="default_cube",
        timezone_info=SHANGHAI,
    )

    tracker.reconcile(now=datetime(2026, 9, 1, 12, tzinfo=SHANGHAI))

    events = store.snapshot()["events"]
    assert set(events) == {"future", "missing-time"}
    assert events["future"]["tracking_state"] == "scheduled"
    assert events["missing-time"]["tracking_state"] == "unscheduled"


def test_reconcile_repairs_a_topic_revision_gap_after_restart(tmp_path: Path) -> None:
    memory = event_memory(
        "future",
        "planned",
        version=2,
        event_time="2026-09-03T12:00:00+08:00",
    )
    synced_ids: set[str] = set()

    def topic_needs_sync(
        user_id: str,
        cube_id: str,
        memory_id: str,
        candidate: dict | None,
    ) -> bool:
        assert (user_id, cube_id) == ("default", "default_cube")
        return candidate is not None and memory_id not in synced_ids

    def refresh_topic(user_id: str, cube_id: str, memory_id: str) -> None:
        assert (user_id, cube_id) == ("default", "default_cube")
        synced_ids.add(memory_id)

    store = memos_plan_tracker.PlanTrackerStore(tmp_path / "tracker.json")
    tracker = memos_plan_tracker.PlanTracker(
        store=store,
        client=FakeClient([memory]),
        user_id="default",
        cube_id="default_cube",
        timezone_info=SHANGHAI,
        topic_refresher=refresh_topic,
        topic_sync_checker=topic_needs_sync,
    )

    tracker.reconcile(now=datetime(2026, 9, 1, 8, tzinfo=SHANGHAI))

    assert synced_ids == {"future"}
    assert store.get("future")["last_seen_version"] == 2
    assert store.get("future")["topic_sync_pending"] is False


def test_reconcile_repairs_an_untracked_terminal_event_then_drops_it(tmp_path: Path) -> None:
    memory = event_memory("done", "completed", version=3)
    refreshed: list[str] = []
    store = memos_plan_tracker.PlanTrackerStore(tmp_path / "tracker.json")
    tracker = memos_plan_tracker.PlanTracker(
        store=store,
        client=FakeClient([memory]),
        user_id="default",
        cube_id="default_cube",
        timezone_info=SHANGHAI,
        topic_refresher=lambda user_id, cube_id, memory_id: refreshed.append(memory_id),
        topic_sync_checker=(lambda user_id, cube_id, memory_id, candidate: candidate is not None),
    )

    tracker.reconcile(now=datetime(2026, 9, 1, 8, tzinfo=SHANGHAI))

    assert refreshed == ["done"]
    assert store.get("done") is None


def test_reconcile_preserves_unresolved_topic_retry(tmp_path: Path) -> None:
    store = memos_plan_tracker.PlanTrackerStore(tmp_path / "tracker.json")
    store.upsert(
        {
            "memory_id": "temporarily-missing",
            "user_id": "default",
            "cube_id": "default_cube",
            "last_seen_version": 0,
            "check_at": None,
            "next_check_at": None,
            "last_checked_at": None,
            "tracking_state": "error",
            "failure_count": 0,
            "topic_sync_pending": True,
        }
    )

    def fail_topic_refresh(user_id: str, cube_id: str, memory_id: str) -> None:
        raise RuntimeError(memory_id)

    tracker = memos_plan_tracker.PlanTracker(
        store=store,
        client=FakeClient([]),
        user_id="default",
        cube_id="default_cube",
        timezone_info=SHANGHAI,
        topic_refresher=fail_topic_refresh,
    )

    tracker.reconcile(now=datetime(2026, 9, 1, 12, tzinfo=SHANGHAI))

    entry = store.get("temporarily-missing")
    assert entry is not None
    assert entry["topic_sync_pending"] is True


def test_topic_only_placeholder_does_not_enroll_unscheduled_ongoing_event(
    tmp_path: Path,
) -> None:
    store = memos_plan_tracker.PlanTrackerStore(tmp_path / "tracker.json")
    store.upsert(
        {
            "memory_id": "ongoing",
            "user_id": "default",
            "cube_id": "default_cube",
            "last_seen_version": 0,
            "check_at": None,
            "next_check_at": None,
            "last_checked_at": None,
            "tracking_state": "error",
            "failure_count": 0,
            "topic_sync_pending": True,
        }
    )
    tracker = memos_plan_tracker.PlanTracker(
        store=store,
        client=FakeClient([event_memory("ongoing", "ongoing", version=2)]),
        user_id="default",
        cube_id="default_cube",
        timezone_info=SHANGHAI,
        topic_refresher=lambda user_id, cube_id, memory_id: None,
    )

    tracker.reconcile(now=datetime(2026, 9, 1, 12, tzinfo=SHANGHAI))

    assert store.get("ongoing") is None


def test_topic_only_placeholder_becomes_a_real_scheduled_entry(tmp_path: Path) -> None:
    store = memos_plan_tracker.PlanTrackerStore(tmp_path / "tracker.json")
    store.upsert(
        {
            "memory_id": "planned",
            "user_id": "default",
            "cube_id": "default_cube",
            "last_seen_version": 2,
            "check_at": None,
            "next_check_at": None,
            "last_checked_at": None,
            "tracking_state": "error",
            "failure_count": 0,
            "topic_sync_pending": True,
        }
    )
    tracker = memos_plan_tracker.PlanTracker(
        store=store,
        client=FakeClient(
            [
                event_memory(
                    "planned",
                    "planned",
                    version=2,
                    event_time="2026-09-03T12:00:00+08:00",
                )
            ]
        ),
        user_id="default",
        cube_id="default_cube",
        timezone_info=SHANGHAI,
        topic_refresher=lambda user_id, cube_id, memory_id: None,
    )

    tracker.reconcile(now=datetime(2026, 9, 1, 12, tzinfo=SHANGHAI))

    entry = store.get("planned")
    assert entry is not None
    assert entry["tracking_state"] == "scheduled"
    assert entry["topic_sync_pending"] is False


def test_due_event_transitions_by_exact_id_and_refreshes_topic(tmp_path: Path) -> None:
    memory = event_memory(
        "memory-1",
        "planned",
        version=3,
        event_end_time="2026-09-01T10:00:00+08:00",
    )
    client = FakeClient([memory])
    topic_calls: list[tuple[str, str, str]] = []
    store = memos_plan_tracker.PlanTrackerStore(tmp_path / "tracker.json")
    tracker = memos_plan_tracker.PlanTracker(
        store=store,
        client=client,
        user_id="alice",
        cube_id="daily",
        timezone_info=SHANGHAI,
        topic_refresher=lambda user_id, cube_id, memory_id: topic_calls.append(
            (user_id, cube_id, memory_id)
        ),
    )
    observed_at = datetime(2026, 9, 1, 10, 1, tzinfo=SHANGHAI)

    tracker.reconcile(now=observed_at)

    assert client.transition_calls == [
        {
            "user_id": "alice",
            "cube_id": "daily",
            "memory_id": "memory-1",
            "expected_version": 3,
            "to_status": "due_unverified",
            "observed_at": observed_at.isoformat(),
        }
    ]
    entry = store.get("memory-1")
    assert entry["last_seen_version"] == 4
    assert entry["tracking_state"] == "waiting_evidence"
    assert entry["topic_sync_pending"] is False
    assert topic_calls == [("alice", "daily", "memory-1")]


def test_due_transition_is_marked_for_topic_sync_before_calling_core(tmp_path: Path) -> None:
    memory = event_memory(
        "memory-1",
        "planned",
        version=3,
        event_end_time="2026-09-01T10:00:00+08:00",
    )
    client = FakeClient([memory])
    store = memos_plan_tracker.PlanTrackerStore(tmp_path / "tracker.json")
    tracker = memos_plan_tracker.PlanTracker(
        store=store,
        client=client,
        user_id="default",
        cube_id="default_cube",
        timezone_info=SHANGHAI,
    )
    tracker.reconcile(now=datetime(2026, 9, 1, 9, tzinfo=SHANGHAI))

    original_transition = client.transition_event_lifecycle

    def assert_pending_before_transition(**kwargs) -> dict:
        assert store.get("memory-1")["topic_sync_pending"] is True
        return original_transition(**kwargs)

    client.transition_event_lifecycle = assert_pending_before_transition
    tracker.check_due(now=datetime(2026, 9, 1, 10, 1, tzinfo=SHANGHAI))

    assert store.get("memory-1")["tracking_state"] == "waiting_evidence"


def test_lost_transition_response_retries_topic_sync_after_restart(tmp_path: Path) -> None:
    memory = event_memory(
        "memory-1",
        "planned",
        version=3,
        event_end_time="2026-09-01T10:00:00+08:00",
    )
    client = FakeClient([memory])
    store = memos_plan_tracker.PlanTrackerStore(tmp_path / "tracker.json")
    topic_calls: list[str] = []
    tracker = memos_plan_tracker.PlanTracker(
        store=store,
        client=client,
        user_id="default",
        cube_id="default_cube",
        timezone_info=SHANGHAI,
        topic_refresher=lambda user_id, cube_id, memory_id: topic_calls.append(memory_id),
    )
    tracker.reconcile(now=datetime(2026, 9, 1, 9, tzinfo=SHANGHAI))

    def apply_then_lose_response(**kwargs) -> dict:
        memory["metadata"]["info"]["event_status"] = "due_unverified"
        memory["metadata"]["version"] = 4
        raise OSError("response lost")

    client.transition_event_lifecycle = apply_then_lose_response
    tracker.check_due(now=datetime(2026, 9, 1, 10, 1, tzinfo=SHANGHAI))
    assert store.get("memory-1")["topic_sync_pending"] is True

    restarted = memos_plan_tracker.PlanTracker(
        store=memos_plan_tracker.PlanTrackerStore(tmp_path / "tracker.json"),
        client=client,
        user_id="default",
        cube_id="default_cube",
        timezone_info=SHANGHAI,
        topic_refresher=lambda user_id, cube_id, memory_id: topic_calls.append(memory_id),
    )
    restarted.reconcile(now=datetime(2026, 9, 1, 10, 2, tzinfo=SHANGHAI))

    assert topic_calls == ["memory-1"]
    assert restarted.store.get("memory-1")["topic_sync_pending"] is False


def test_due_unverified_event_waits_for_evidence_without_repeated_transition(
    tmp_path: Path,
) -> None:
    memory = event_memory(
        "memory-1",
        "due_unverified",
        version=4,
        event_end_time="2026-09-01T10:00:00+08:00",
    )
    client = FakeClient([memory])
    store = memos_plan_tracker.PlanTrackerStore(tmp_path / "tracker.json")
    tracker = memos_plan_tracker.PlanTracker(
        store=store,
        client=client,
        user_id="default",
        cube_id="default_cube",
        timezone_info=SHANGHAI,
    )
    now = datetime(2026, 9, 2, 10, tzinfo=SHANGHAI)

    tracker.reconcile(now=now)
    tracker.check_due(now=now + timedelta(days=1))

    assert client.transition_calls == []
    entry = store.get("memory-1")
    assert entry["last_seen_version"] == 4
    assert entry["tracking_state"] == "waiting_evidence"
    assert entry["next_check_at"] is None


def test_failed_topic_refresh_stays_pending_until_reconcile(tmp_path: Path) -> None:
    memory = event_memory(
        "memory-1",
        "planned",
        event_end_time="2026-09-01T10:00:00+08:00",
    )
    client = FakeClient([memory])
    refresh_calls: list[str] = []

    def refresh_topic(user_id: str, cube_id: str, memory_id: str) -> None:
        refresh_calls.append(memory_id)
        if len(refresh_calls) == 1:
            raise OSError("topic unavailable")

    store = memos_plan_tracker.PlanTrackerStore(tmp_path / "tracker.json")
    tracker = memos_plan_tracker.PlanTracker(
        store=store,
        client=client,
        user_id="default",
        cube_id="default_cube",
        timezone_info=SHANGHAI,
        topic_refresher=refresh_topic,
    )
    now = datetime(2026, 9, 1, 10, 1, tzinfo=SHANGHAI)

    tracker.reconcile(now=now)
    assert store.get("memory-1")["topic_sync_pending"] is True

    tracker.check_due(now=now + timedelta(minutes=1))
    assert refresh_calls == ["memory-1"]

    tracker.reconcile(now=now + timedelta(minutes=15))
    assert refresh_calls == ["memory-1", "memory-1"]
    assert store.get("memory-1")["topic_sync_pending"] is False


def test_completed_event_keeps_failed_topic_sync_until_retry(tmp_path: Path) -> None:
    completed = event_memory("memory-1", "completed", version=4)
    client = FakeClient([completed])
    refresh_calls: list[str] = []

    def refresh_topic(user_id: str, cube_id: str, memory_id: str) -> None:
        refresh_calls.append(memory_id)
        if len(refresh_calls) == 1:
            raise OSError("topic unavailable")

    store = memos_plan_tracker.PlanTrackerStore(tmp_path / "tracker.json")
    tracker = memos_plan_tracker.PlanTracker(
        store=store,
        client=client,
        user_id="default",
        cube_id="default_cube",
        timezone_info=SHANGHAI,
        topic_refresher=refresh_topic,
    )

    tracker.refresh_memory_ids(["memory-1"], topic_sync_pending=True)
    pending = store.get("memory-1")
    assert pending["topic_sync_pending"] is True
    assert pending["check_at"] is None

    tracker.reconcile(now=datetime(2026, 9, 1, 12, tzinfo=SHANGHAI))

    assert refresh_calls == ["memory-1", "memory-1"]
    assert store.get("memory-1") is None


def test_newer_completed_version_wins_over_stale_due_entry(tmp_path: Path) -> None:
    completed = event_memory(
        "memory-1",
        "completed",
        version=4,
        event_end_time="2026-09-01T10:00:00+08:00",
    )
    client = FakeClient([completed])
    store = memos_plan_tracker.PlanTrackerStore(tmp_path / "tracker.json")
    store.upsert(
        memos_plan_tracker.tracker_entry(
            event_memory(
                "memory-1",
                "planned",
                version=3,
                event_end_time="2026-09-01T10:00:00+08:00",
            ),
            user_id="default",
            cube_id="default_cube",
            timezone_info=SHANGHAI,
        )
    )
    tracker = memos_plan_tracker.PlanTracker(
        store=store,
        client=client,
        user_id="default",
        cube_id="default_cube",
        timezone_info=SHANGHAI,
    )

    tracker.check_due(now=datetime(2026, 9, 1, 10, 1, tzinfo=SHANGHAI))

    assert client.transition_calls == []
    assert store.get("memory-1") is None


def test_transition_conflict_reloads_latest_memory_without_overwrite(tmp_path: Path) -> None:
    planned = event_memory(
        "memory-1",
        "planned",
        version=3,
        event_end_time="2026-09-01T10:00:00+08:00",
    )
    client = FakeClient([planned])
    client.transition_outcome = "conflict"
    topic_calls: list[str] = []
    store = memos_plan_tracker.PlanTrackerStore(tmp_path / "tracker.json")
    store.upsert(
        memos_plan_tracker.tracker_entry(
            planned,
            user_id="default",
            cube_id="default_cube",
            timezone_info=SHANGHAI,
        )
    )
    tracker = memos_plan_tracker.PlanTracker(
        store=store,
        client=client,
        user_id="default",
        cube_id="default_cube",
        timezone_info=SHANGHAI,
        topic_refresher=lambda user_id, cube_id, memory_id: topic_calls.append(memory_id),
    )

    def conflict_then_complete(**kwargs) -> dict:
        client.transition_calls.append(kwargs)
        client.memories["memory-1"]["metadata"]["version"] = 4
        client.memories["memory-1"]["metadata"]["info"]["event_status"] = "completed"
        return {"data": {"outcome": "conflict", "memory_id": "memory-1"}}

    client.transition_event_lifecycle = conflict_then_complete
    tracker.check_due(now=datetime(2026, 9, 1, 10, 1, tzinfo=SHANGHAI))

    assert len(client.transition_calls) == 1
    assert store.get("memory-1") is None
    assert topic_calls == ["memory-1"]


def test_newer_terminal_version_keeps_pending_topic_refresh(tmp_path: Path) -> None:
    planned = event_memory(
        "memory-1",
        "planned",
        version=3,
        event_end_time="2026-09-01T10:00:00+08:00",
    )
    completed = event_memory(
        "memory-1",
        "completed",
        version=4,
        event_end_time="2026-09-01T10:00:00+08:00",
    )
    client = FakeClient([completed])
    topic_calls: list[str] = []
    store = memos_plan_tracker.PlanTrackerStore(tmp_path / "tracker.json")
    entry = memos_plan_tracker.tracker_entry(
        planned,
        user_id="default",
        cube_id="default_cube",
        timezone_info=SHANGHAI,
    )
    store.upsert(dict(entry, topic_sync_pending=True))
    tracker = memos_plan_tracker.PlanTracker(
        store=store,
        client=client,
        user_id="default",
        cube_id="default_cube",
        timezone_info=SHANGHAI,
        topic_refresher=lambda user_id, cube_id, memory_id: topic_calls.append(memory_id),
    )

    tracker.check_due(now=datetime(2026, 9, 1, 10, 1, tzinfo=SHANGHAI))

    assert client.transition_calls == []
    assert topic_calls == ["memory-1"]
    assert store.get("memory-1") is None


def test_topic_failure_survives_temporary_memory_refresh_failure(tmp_path: Path) -> None:
    client = FakeClient([])

    def fail_get_memory(memory_id: str) -> dict:
        raise OSError(f"temporary read failure for {memory_id}")

    client.get_memory = fail_get_memory
    store = memos_plan_tracker.PlanTrackerStore(tmp_path / "tracker.json")
    tracker = memos_plan_tracker.PlanTracker(
        store=store,
        client=client,
        user_id="default",
        cube_id="default_cube",
        timezone_info=SHANGHAI,
    )

    tracker.refresh_memory_ids(["memory-1"], topic_sync_pending=True)

    entry = store.get("memory-1")
    assert entry is not None
    assert entry["topic_sync_pending"] is True
    assert entry["tracking_state"] == "error"


def test_unexpected_topic_failure_is_kept_for_retry(tmp_path: Path) -> None:
    planned = event_memory(
        "memory-1",
        "planned",
        event_time="2026-09-03T12:00:00+08:00",
    )
    client = FakeClient([planned])
    store = memos_plan_tracker.PlanTrackerStore(tmp_path / "tracker.json")

    def fail_topic_refresh(user_id: str, cube_id: str, memory_id: str) -> None:
        raise KeyError(memory_id)

    tracker = memos_plan_tracker.PlanTracker(
        store=store,
        client=client,
        user_id="default",
        cube_id="default_cube",
        timezone_info=SHANGHAI,
        topic_refresher=fail_topic_refresh,
    )

    tracker.refresh_memory_ids(["memory-1"], topic_sync_pending=True)

    entry = store.get("memory-1")
    assert entry is not None
    assert entry["topic_sync_pending"] is True


def test_core_failure_keeps_event_with_bounded_retry(tmp_path: Path) -> None:
    planned = event_memory(
        "memory-1",
        "planned",
        version=3,
        event_end_time="2026-09-01T10:00:00+08:00",
    )
    client = FakeClient([planned])
    client.fail_transition = True
    store = memos_plan_tracker.PlanTrackerStore(tmp_path / "tracker.json")
    store.upsert(
        memos_plan_tracker.tracker_entry(
            planned,
            user_id="default",
            cube_id="default_cube",
            timezone_info=SHANGHAI,
        )
    )
    tracker = memos_plan_tracker.PlanTracker(
        store=store,
        client=client,
        user_id="default",
        cube_id="default_cube",
        timezone_info=SHANGHAI,
        retry_base_seconds=60,
    )
    now = datetime(2026, 9, 1, 10, 1, tzinfo=SHANGHAI)

    tracker.check_due(now=now)

    entry = store.get("memory-1")
    assert entry["tracking_state"] == "error"
    assert entry["failure_count"] == 1
    assert entry["next_check_at"] == (now + timedelta(seconds=60)).isoformat()


def test_reconcile_preserves_backoff_for_the_same_failed_version(tmp_path: Path) -> None:
    planned = event_memory(
        "memory-1",
        "planned",
        version=3,
        event_end_time="2026-09-01T10:00:00+08:00",
    )
    client = FakeClient([planned])
    client.fail_transition = True
    store = memos_plan_tracker.PlanTrackerStore(tmp_path / "tracker.json")
    tracker = memos_plan_tracker.PlanTracker(
        store=store,
        client=client,
        user_id="default",
        cube_id="default_cube",
        timezone_info=SHANGHAI,
        retry_base_seconds=60,
    )
    failure_time = datetime(2026, 9, 1, 10, 1, tzinfo=SHANGHAI)
    tracker.reconcile(now=failure_time)
    failed = store.get("memory-1")

    tracker.reconcile(now=failure_time + timedelta(seconds=10))

    preserved = store.get("memory-1")
    assert preserved["tracking_state"] == "error"
    assert preserved["failure_count"] == failed["failure_count"]
    assert preserved["next_check_at"] == failed["next_check_at"]


def test_worker_registers_new_memory_ids_asynchronously(tmp_path: Path) -> None:
    refreshed = threading.Event()
    calls: list[list[str]] = []

    class FakeTracker:
        user_id = "default"
        cube_id = "default_cube"

        def reconcile(self) -> None:
            return None

        def check_due(self) -> None:
            return None

        def refresh_memory_ids(
            self,
            memory_ids: list[str],
            *,
            topic_sync_pending: bool = False,
        ) -> None:
            calls.append(memory_ids)
            refreshed.set()

    worker = memos_plan_tracker.PlanTrackerWorker(
        FakeTracker(),
        interval_seconds=30,
        reconcile_seconds=900,
    )
    response = {"code": 200, "data": [{"memory_id": "memory-1"}]}

    worker.register_add_response(
        response,
        user_id="default",
        cube_id="default_cube",
    )
    assert calls == []

    worker.start()
    try:
        assert refreshed.wait(timeout=2)
    finally:
        worker.stop()
    assert calls == [["memory-1"]]


def test_worker_registers_and_persists_an_event_for_any_runtime_scope(tmp_path: Path) -> None:
    planned = event_memory(
        "memory-alice",
        "planned",
        event_end_time="2026-09-03T12:00:00+08:00",
    )

    class ScopedClient(FakeClient):
        def list_text_memories(self, *, user_id: str, cube_id: str) -> list[dict]:
            self.list_calls.append((user_id, cube_id))
            if (user_id, cube_id) == ("alice", "daily"):
                return list(self.memories.values())
            return []

    client = ScopedClient([planned])
    store = memos_plan_tracker.PlanTrackerStore(tmp_path / "tracker.json")
    tracker = memos_plan_tracker.PlanTracker(
        store=store,
        client=client,
        user_id="default",
        cube_id="default_cube",
        timezone_info=SHANGHAI,
    )
    worker = memos_plan_tracker.PlanTrackerWorker(tracker)

    worker.register_add_response(
        {"code": 200, "data": [{"memory_id": "memory-alice"}]},
        user_id="alice",
        cube_id="daily",
    )
    worker._process_registrations_safely()

    entry = store.get("memory-alice")
    assert entry is not None
    assert (entry["user_id"], entry["cube_id"]) == ("alice", "daily")


def test_worker_reconciles_persisted_nondefault_scopes_after_restart(tmp_path: Path) -> None:
    planned = event_memory(
        "memory-alice",
        "planned",
        event_end_time="2000-01-01T12:00:00+08:00",
    )
    scope_reconciled = threading.Event()

    class ScopedClient(FakeClient):
        def list_text_memories(self, *, user_id: str, cube_id: str) -> list[dict]:
            self.list_calls.append((user_id, cube_id))
            if (user_id, cube_id) == ("alice", "daily"):
                scope_reconciled.set()
                return list(self.memories.values())
            return []

    client = ScopedClient([planned])
    store = memos_plan_tracker.PlanTrackerStore(tmp_path / "tracker.json")
    store.upsert(
        memos_plan_tracker.tracker_entry(
            planned,
            user_id="alice",
            cube_id="daily",
            timezone_info=SHANGHAI,
        )
    )
    restarted_store = memos_plan_tracker.PlanTrackerStore(tmp_path / "tracker.json")
    tracker = memos_plan_tracker.PlanTracker(
        store=restarted_store,
        client=client,
        user_id="default",
        cube_id="default_cube",
        timezone_info=SHANGHAI,
    )
    worker = memos_plan_tracker.PlanTrackerWorker(
        tracker,
        interval_seconds=30,
        reconcile_seconds=900,
    )

    worker.start()
    try:
        assert scope_reconciled.wait(timeout=2)
    finally:
        worker.stop()

    assert ("alice", "daily") in client.list_calls
    assert len(client.transition_calls) == 1
    transition = dict(client.transition_calls[0])
    assert transition.pop("observed_at")
    assert transition == {
        "user_id": "alice",
        "cube_id": "daily",
        "memory_id": "memory-alice",
        "expected_version": 1,
        "to_status": "due_unverified",
    }


def test_persisting_topic_retry_cannot_be_overwritten_by_concurrent_reconcile(
    tmp_path: Path,
) -> None:
    planned = event_memory(
        "memory-1",
        "planned",
        event_end_time="2026-09-03T12:00:00+08:00",
    )
    reconcile_started = threading.Event()
    allow_reconcile = threading.Event()

    class BlockingClient(FakeClient):
        def list_text_memories(self, *, user_id: str, cube_id: str) -> list[dict]:
            reconcile_started.set()
            assert allow_reconcile.wait(timeout=2)
            return super().list_text_memories(user_id=user_id, cube_id=cube_id)

    store = memos_plan_tracker.PlanTrackerStore(tmp_path / "tracker.json")
    tracker = memos_plan_tracker.PlanTracker(
        store=store,
        client=BlockingClient([planned]),
        user_id="default",
        cube_id="default_cube",
        timezone_info=SHANGHAI,
    )
    reconcile_thread = threading.Thread(target=tracker.reconcile)
    reconcile_thread.start()
    assert reconcile_started.wait(timeout=2)

    registration_started = threading.Event()
    registration_finished = threading.Event()

    def persist_registration() -> None:
        registration_started.set()
        tracker.persist_ingestion_registration(
            ["memory-1"],
            topic_sync_pending=True,
        )
        registration_finished.set()

    registration_thread = threading.Thread(target=persist_registration)
    registration_thread.start()
    assert registration_started.wait(timeout=2)
    assert registration_finished.wait(timeout=0.05) is False
    allow_reconcile.set()
    reconcile_thread.join(timeout=2)
    registration_thread.join(timeout=2)

    assert registration_finished.is_set()
    entry = store.get("memory-1")
    assert entry is not None
    assert entry["topic_sync_pending"] is True


def test_worker_persists_topic_retry_before_background_processing(tmp_path: Path) -> None:
    store = memos_plan_tracker.PlanTrackerStore(tmp_path / "tracker.json")
    tracker = memos_plan_tracker.PlanTracker(
        store=store,
        client=FakeClient([]),
        user_id="default",
        cube_id="default_cube",
        timezone_info=SHANGHAI,
    )
    worker = memos_plan_tracker.PlanTrackerWorker(tracker)

    worker.register_add_response(
        {"code": 200, "data": [{"memory_id": "memory-1"}]},
        user_id="default",
        cube_id="default_cube",
        topic_sync_pending=True,
    )

    entry = store.get("memory-1")
    assert entry is not None
    assert entry["topic_sync_pending"] is True


def test_worker_can_start_after_an_earlier_stop(tmp_path: Path) -> None:
    refreshed = threading.Event()

    class FakeTracker:
        user_id = "default"
        cube_id = "default_cube"

        def reconcile(self) -> None:
            return None

        def check_due(self) -> None:
            return None

        def refresh_memory_ids(
            self,
            memory_ids: list[str],
            *,
            topic_sync_pending: bool = False,
        ) -> None:
            assert memory_ids == ["memory-1"]
            assert topic_sync_pending is False
            refreshed.set()

    worker = memos_plan_tracker.PlanTrackerWorker(
        FakeTracker(),
        interval_seconds=30,
        reconcile_seconds=900,
    )
    worker.stop()
    worker.register_add_response(
        {"code": 200, "data": [{"memory_id": "memory-1"}]},
        user_id="default",
        cube_id="default_cube",
    )

    worker.start()
    try:
        assert refreshed.wait(timeout=2)
        assert worker._stop_event.is_set() is False
    finally:
        worker.stop()


def test_worker_processes_registrations_after_a_full_restart(tmp_path: Path) -> None:
    refreshed = threading.Event()
    calls: list[list[str]] = []

    class FakeTracker:
        user_id = "default"
        cube_id = "default_cube"

        def reconcile(self) -> None:
            return None

        def check_due(self) -> None:
            return None

        def refresh_memory_ids(
            self,
            memory_ids: list[str],
            *,
            topic_sync_pending: bool = False,
        ) -> None:
            del topic_sync_pending
            calls.append(memory_ids)
            refreshed.set()

    worker = memos_plan_tracker.PlanTrackerWorker(
        FakeTracker(),
        interval_seconds=30,
        reconcile_seconds=900,
    )

    worker.start()
    worker.register_add_response(
        {"code": 200, "data": [{"memory_id": "memory-1"}]},
        user_id="default",
        cube_id="default_cube",
    )
    assert refreshed.wait(timeout=2)
    worker.stop()

    refreshed.clear()
    worker.register_add_response(
        {"code": 200, "data": [{"memory_id": "memory-2"}]},
        user_id="default",
        cube_id="default_cube",
    )
    worker.start()
    try:
        assert refreshed.wait(timeout=2)
    finally:
        worker.stop()

    assert calls == [["memory-1"], ["memory-2"]]


def test_worker_survives_an_unexpected_registration_failure(tmp_path: Path) -> None:
    first_failed = threading.Event()
    second_processed = threading.Event()

    class FakeTracker:
        user_id = "default"
        cube_id = "default_cube"

        def reconcile(self) -> None:
            return None

        def check_due(self) -> None:
            return None

        def refresh_memory_ids(
            self,
            memory_ids: list[str],
            *,
            topic_sync_pending: bool = False,
        ) -> None:
            del topic_sync_pending
            if memory_ids == ["memory-1"]:
                first_failed.set()
                raise KeyError("unexpected tracker dependency failure")
            if memory_ids == ["memory-2"]:
                second_processed.set()

    worker = memos_plan_tracker.PlanTrackerWorker(
        FakeTracker(),
        interval_seconds=30,
        reconcile_seconds=900,
    )
    worker.start()
    try:
        worker.register_add_response(
            {"code": 200, "data": [{"memory_id": "memory-1"}]},
            user_id="default",
            cube_id="default_cube",
        )
        assert first_failed.wait(timeout=2)
        worker.register_add_response(
            {"code": 200, "data": [{"memory_id": "memory-2"}]},
            user_id="default",
            cube_id="default_cube",
        )
        assert second_processed.wait(timeout=2)
    finally:
        worker.stop()


def test_memos_client_paginates_text_memories_and_calls_exact_transition(monkeypatch) -> None:
    from memos_chat import MemOSClient

    client = MemOSClient("http://127.0.0.1:8000")
    calls: list[tuple[str, str, dict]] = []

    def fake_request(method: str, path: str, payload=None, timeout=None):
        calls.append((method, path, payload))
        if path == "/product/get_memory_dashboard":
            page = payload["page"]
            memories = (
                [event_memory("memory-1", "planned"), event_memory("memory-2", "ongoing")]
                if page == 1
                else [event_memory("memory-3", "completed")]
            )
            return {
                "data": {
                    "text_mem": [{"cube_id": "daily", "memories": memories}],
                    "statistics": {"total_text_nodes": 3},
                }
            }
        return {"data": {"outcome": "applied"}}

    monkeypatch.setattr(client, "_request", fake_request)

    memories = client.list_text_memories(user_id="alice", cube_id="daily", page_size=2)
    result = client.transition_event_lifecycle(
        user_id="alice",
        cube_id="daily",
        memory_id="memory-1",
        expected_version=3,
        to_status="due_unverified",
        observed_at="2026-09-01T10:01:00+08:00",
    )

    assert [item["id"] for item in memories] == ["memory-1", "memory-2", "memory-3"]
    assert [call[2]["page"] for call in calls[:2]] == [1, 2]
    assert all(
        call[2]["filter"] == {"and": [{"status": "activated"}, {"record_type": "event"}]}
        for call in calls[:2]
    )
    assert calls[-1] == (
        "POST",
        "/product/event_lifecycle/transition",
        {
            "user_id": "alice",
            "cube_id": "daily",
            "memory_id": "memory-1",
            "expected_version": 3,
            "to_status": "due_unverified",
            "observed_at": "2026-09-01T10:01:00+08:00",
        },
    )
    assert result["data"]["outcome"] == "applied"


def test_compose_files_persist_plan_tracker_separately() -> None:
    repository = Path(__file__).parents[2]
    local_compose = (repository / "docker" / "docker-compose.yml").read_text(encoding="utf-8")
    server_compose = (repository / "deploy" / "server" / "docker-compose.yml").read_text(
        encoding="utf-8"
    )

    assert "MEMOS_PLAN_TRACKER_STATE: /data/plan-tracker/tracker.json" in local_compose
    assert "../.memos/plan_tracker:/data/plan-tracker" in local_compose
    assert "MEMOS_PLAN_TRACKER_STATE: /data/plan-tracker/tracker.json" in server_compose
    assert "plan_tracker_data:/data/plan-tracker" in server_compose
    assert "plan_tracker_data:" in server_compose
