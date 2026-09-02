from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import threading

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from fastapi.testclient import TestClient


SCRIPTS_DIR = Path(__file__).parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
SCRIPT_PATH = SCRIPTS_DIR / "memos_frontend_api.py"
SPEC = importlib.util.spec_from_file_location("memos_frontend_api", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
memos_frontend_api = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = memos_frontend_api
SPEC.loader.exec_module(memos_frontend_api)


def test_topics_endpoint_reads_rolling_json_snapshot(tmp_path: Path):
    calls: list[dict[str, object]] = []

    class FakeStore:
        path = tmp_path / "topics.json"

        def list_queue_snapshot(self, **kwargs):
            calls.append(kwargs)
            return {
                "items": [
                    {
                        "topic_id": "topic-1",
                        "topic_date": "2026-08-20",
                        "topic_key": "final_exam",
                        "topic_text": "用户正在准备期末考试。",
                        "lifecycle_status": "active",
                        "qualifies": True,
                        "queue_rank": 1,
                        "rank_score": 0.88,
                        "reason_evidence": [],
                        "supporting_memory_ids": ["memory-1"],
                    }
                ],
                "pool_total": 1,
                "candidate_pool_total": 0,
                "core_count": 1,
                "visible_candidate_count": 0,
                "hidden_candidate_count": 0,
                "queue_calculated_at": None,
            }

    app = memos_frontend_api.create_app(
        store_factory=FakeStore,
        client_factory=lambda base_url: SimpleNamespace(health=lambda: {"status": "healthy"}),
        upload_dir=tmp_path / "uploads",
    )
    response = TestClient(app).get(
        "/topics?user_id=default&cube_id=default_cube&include_suppressed=true"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "json_snapshot"
    assert body["total"] == 1
    assert body["topics"][0]["topic_key"] == "final_exam"
    assert calls == [
        {
            "user_id": "default",
            "cube_id": "default_cube",
            "policy": memos_frontend_api.DEFAULT_TOPIC_QUEUE_POLICY,
        }
    ]


def test_application_lifespan_starts_and_stops_injected_tracker_worker(tmp_path: Path):
    calls: list[str] = []

    class FakeStore:
        path = tmp_path / "topics.json"

        def list_all_topics(self, **kwargs):
            return []

    class FakeWorker:
        def start(self) -> None:
            calls.append("start")

        def stop(self) -> None:
            calls.append("stop")

    app = memos_frontend_api.create_app(
        store_factory=FakeStore,
        client_factory=lambda base_url: SimpleNamespace(health=lambda: {"status": "healthy"}),
        upload_dir=tmp_path / "uploads",
        tracker_worker=FakeWorker(),
    )

    with TestClient(app) as client:
        assert client.get("/api/v1/health").status_code == 200

    assert calls == ["start", "stop"]


def test_scheduler_uses_asia_shanghai_midnight_and_noon():
    current = [datetime(2026, 8, 31, 3, 50, tzinfo=timezone.utc)]
    calls: list[dict[str, object]] = []
    waited_slots: list[str] = []

    def maintain_topics(**kwargs):
        calls.append(kwargs)
        return {"ok": True}

    async def fake_wait(target_slot: datetime, stop_event: asyncio.Event) -> bool:
        waited_slots.append(target_slot.isoformat())
        if len(waited_slots) == 1:
            current[0] = datetime(2026, 8, 31, 4, 0, tzinfo=timezone.utc)
            return False
        stop_event.set()
        return True

    async def exercise() -> None:
        await memos_frontend_api.run_topic_scheduler(
            maintainer=maintain_topics,
            stop_event=asyncio.Event(),
            clock=lambda: current[0],
            wait_until=fake_wait,
            policy=memos_frontend_api.DEFAULT_TOPIC_QUEUE_POLICY,
        )

    asyncio.run(exercise())

    assert [call["scheduled_slot"].isoformat() for call in calls] == [
        "2026-08-31T00:00:00+08:00",
        "2026-08-31T12:00:00+08:00",
    ]
    assert [call["now"] for call in calls] == [
        datetime(2026, 8, 31, 3, 50, tzinfo=timezone.utc),
        datetime(2026, 8, 31, 4, 0, tzinfo=timezone.utc),
    ]
    assert all(call["policy"] is memos_frontend_api.DEFAULT_TOPIC_QUEUE_POLICY for call in calls)
    assert waited_slots == [
        "2026-08-31T12:00:00+08:00",
        "2026-09-01T00:00:00+08:00",
    ]


def test_scheduler_catches_up_exactly_one_missed_slot_on_startup():
    now = datetime.fromisoformat("2026-09-03T18:00:00+08:00")
    calls: list[dict[str, object]] = []

    async def stop_immediately(_: datetime, stop_event: asyncio.Event) -> bool:
        stop_event.set()
        return True

    async def exercise() -> None:
        await memos_frontend_api.run_topic_scheduler(
            maintainer=lambda **kwargs: calls.append(kwargs) or {"ok": True},
            stop_event=asyncio.Event(),
            clock=lambda: now,
            wait_until=stop_immediately,
            policy=memos_frontend_api.DEFAULT_TOPIC_QUEUE_POLICY,
        )

    asyncio.run(exercise())

    assert len(calls) == 1
    assert calls[0]["scheduled_slot"].isoformat() == "2026-09-03T12:00:00+08:00"


def test_scheduler_does_not_run_twice_for_the_same_slot():
    now = datetime.fromisoformat("2026-08-31T13:00:00+08:00")
    calls: list[dict[str, object]] = []
    waits = 0

    async def fake_wait(_: datetime, stop_event: asyncio.Event) -> bool:
        nonlocal waits
        waits += 1
        if waits == 1:
            return False
        stop_event.set()
        return True

    async def exercise() -> None:
        await memos_frontend_api.run_topic_scheduler(
            maintainer=lambda **kwargs: calls.append(kwargs) or {"ok": True},
            stop_event=asyncio.Event(),
            clock=lambda: now,
            wait_until=fake_wait,
            policy=memos_frontend_api.DEFAULT_TOPIC_QUEUE_POLICY,
        )

    asyncio.run(exercise())

    assert len(calls) == 1
    assert calls[0]["scheduled_slot"].isoformat() == "2026-08-31T12:00:00+08:00"


def test_lifespan_starts_and_stops_one_topic_scheduler_task(tmp_path: Path):
    now = datetime.fromisoformat("2026-08-31T13:15:00+08:00")
    calls: list[dict[str, object]] = []
    maintenance_started = threading.Event()

    def maintain_topics(**kwargs):
        calls.append(kwargs)
        maintenance_started.set()
        return {"ok": True}

    async def wait_for_stop(_: datetime, stop_event: asyncio.Event) -> bool:
        await stop_event.wait()
        return True

    class FakeStore:
        path = tmp_path / "topics.json"

        def list_all_topics(self, **kwargs):
            return []

    app = memos_frontend_api.create_app(
        store_factory=FakeStore,
        client_factory=lambda base_url: SimpleNamespace(health=lambda: {"status": "healthy"}),
        upload_dir=tmp_path / "uploads",
        topic_scheduler_enabled=True,
        topic_maintainer=maintain_topics,
        topic_wait_until=wait_for_stop,
        clock=lambda: now,
    )

    with TestClient(app) as client:
        assert maintenance_started.wait(timeout=2)
        task = app.state.topic_scheduler_task
        assert task is not None
        assert not task.done()
        assert client.get("/api/v1/health").status_code == 200
        assert app.state.topic_scheduler_task is task
        assert len(calls) == 1

    assert task.done()
    assert app.state.topic_scheduler_task is None
    assert app.state.topic_scheduler_stop_event is None


def test_scheduler_failure_is_visible_and_shutdown_still_cleans_up(monkeypatch, tmp_path: Path):
    worker_calls: list[str] = []
    maintenance_failed = threading.Event()
    error_logged = threading.Event()
    observed_health: dict[str, object] = {}
    logged_errors: list[tuple[object, ...]] = []
    maintenance_failed_during_runtime = False
    error_logged_during_runtime = False

    def record_error(*args, **kwargs) -> None:
        logged_errors.append((*args, kwargs))
        error_logged.set()

    monkeypatch.setattr(memos_frontend_api.logger, "error", record_error)

    class FakeStore:
        path = tmp_path / "topics.json"

    class FakeWorker:
        def start(self) -> None:
            worker_calls.append("start")

        def stop(self) -> None:
            worker_calls.append("stop")

    def fail_maintenance(**kwargs):
        maintenance_failed.set()
        raise RuntimeError("scheduled rebalance failed")

    app = memos_frontend_api.create_app(
        store_factory=FakeStore,
        client_factory=lambda base_url: SimpleNamespace(health=lambda: {"status": "healthy"}),
        upload_dir=tmp_path / "uploads",
        tracker_worker=FakeWorker(),
        topic_scheduler_enabled=True,
        topic_maintainer=fail_maintenance,
        clock=lambda: datetime.fromisoformat("2026-08-31T13:15:00+08:00"),
    )

    with (
        pytest.raises(RuntimeError, match="scheduled rebalance failed"),
        TestClient(app) as client,
    ):
        maintenance_failed_during_runtime = maintenance_failed.wait(timeout=2)
        error_logged_during_runtime = error_logged.wait(timeout=2)
        health = client.get("/api/v1/health")
        observed_health.update(health.json())

    assert maintenance_failed_during_runtime
    assert error_logged_during_runtime
    assert observed_health["status"] == "degraded"
    assert observed_health["dependencies"] == {"memos": "online", "topics": "error"}
    assert app.state.topic_scheduler_error == "scheduled rebalance failed"
    assert logged_errors[0][0] == "Topic scheduler failed: %s"
    assert logged_errors[0][-1]["exc_info"][1].args == ("scheduled rebalance failed",)
    assert worker_calls == ["start", "stop"]
    assert app.state.topic_scheduler_task is None
    assert app.state.topic_scheduler_stop_event is None


def test_scheduler_shutdown_waits_for_running_to_thread_maintenance(tmp_path: Path):
    maintenance_started = threading.Event()
    allow_maintenance_to_finish = threading.Event()
    maintenance_finished = threading.Event()
    lifespan_entered = threading.Event()
    lifespan_exited = threading.Event()
    thread_errors: list[BaseException] = []
    scheduler_tasks: list[asyncio.Task[None]] = []
    scheduler_stop_events: list[asyncio.Event] = []

    class FakeStore:
        path = tmp_path / "topics.json"

    def blocking_maintenance(**kwargs):
        maintenance_started.set()
        if not allow_maintenance_to_finish.wait(timeout=5):
            raise TimeoutError("test did not release maintenance")
        maintenance_finished.set()
        return {"ok": True}

    app = memos_frontend_api.create_app(
        store_factory=FakeStore,
        upload_dir=tmp_path / "uploads",
        topic_scheduler_enabled=True,
        topic_maintainer=blocking_maintenance,
        clock=lambda: datetime.fromisoformat("2026-08-31T13:15:00+08:00"),
    )

    def run_lifespan() -> None:
        try:
            with TestClient(app):
                scheduler_tasks.append(app.state.topic_scheduler_task)
                scheduler_stop_events.append(app.state.topic_scheduler_stop_event)
                lifespan_entered.set()
        except BaseException as exc:
            thread_errors.append(exc)
        finally:
            lifespan_exited.set()

    lifespan_thread = threading.Thread(target=run_lifespan, daemon=True)
    lifespan_thread.start()

    assert lifespan_entered.wait(timeout=2)
    assert maintenance_started.wait(timeout=2)
    assert not lifespan_exited.wait(timeout=0.1)
    assert not maintenance_finished.is_set()
    assert scheduler_stop_events[0].is_set()
    assert not scheduler_tasks[0].cancelled()

    allow_maintenance_to_finish.set()
    assert lifespan_exited.wait(timeout=2)
    lifespan_thread.join(timeout=2)

    assert not lifespan_thread.is_alive()
    assert maintenance_finished.is_set()
    assert thread_errors == []
    assert scheduler_tasks[0].done()
    assert not scheduler_tasks[0].cancelled()
    assert app.state.topic_scheduler_task is None
    assert app.state.topic_scheduler_stop_event is None


def test_scheduler_enabled_environment_controls_production_app(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("MEMOS_TOPIC_SCHEDULER_ENABLED", raising=False)
    assert memos_frontend_api._topic_scheduler_enabled() is True

    for value in ("true", "1", "yes", "on", " YES "):
        monkeypatch.setenv("MEMOS_TOPIC_SCHEDULER_ENABLED", value)
        assert memos_frontend_api._topic_scheduler_enabled() is True

    for value in ("false", "0", "no", "off", "unexpected", ""):
        monkeypatch.setenv("MEMOS_TOPIC_SCHEDULER_ENABLED", value)
        assert memos_frontend_api._topic_scheduler_enabled() is False

    monkeypatch.setenv("MEMOS_TOPIC_SCHEDULER_ENABLED", "false")

    class FakeStore:
        path = tmp_path / "topics.json"

        def list_all_topics(self, **kwargs):
            return []

    app = memos_frontend_api.create_app(
        store_factory=FakeStore,
        upload_dir=tmp_path / "uploads",
        topic_scheduler_enabled=None,
        topic_maintainer=lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("disabled scheduler must not run")
        ),
    )

    with TestClient(app):
        assert app.state.topic_scheduler_task is None


def test_scheduled_rebalance_does_not_construct_memos_client_or_topic_llm(
    monkeypatch, tmp_path: Path
):
    state_path = tmp_path / "topics.json"
    monkeypatch.setenv("MEMOS_TOPIC_STATE", str(state_path))
    topic_module = sys.modules["memos_topic"]

    def forbidden_constructor(*args, **kwargs):
        raise AssertionError("scheduled queue rebalance must stay local and deterministic")

    monkeypatch.setattr(topic_module, "MemOSMemoryClient", forbidden_constructor)
    monkeypatch.setattr(topic_module, "TopicLLM", forbidden_constructor)
    maintenance_finished = threading.Event()

    def maintain_topics(**kwargs):
        result = memos_frontend_api.rebalance_runtime_topic_queues(**kwargs)
        maintenance_finished.set()
        return result

    async def wait_for_stop(_: datetime, stop_event: asyncio.Event) -> bool:
        await stop_event.wait()
        return True

    app = memos_frontend_api.create_app(
        upload_dir=tmp_path / "uploads",
        topic_scheduler_enabled=True,
        topic_maintainer=maintain_topics,
        topic_wait_until=wait_for_stop,
        clock=lambda: datetime.fromisoformat("2026-08-31T13:15:00+08:00"),
    )

    with TestClient(app):
        assert maintenance_finished.wait(timeout=2)

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["last_scheduled_slot"] == "2026-08-31T12:00:00+08:00"


def test_ingest_refresh_does_not_consume_the_scheduled_slot(tmp_path: Path):
    now = datetime.fromisoformat("2026-08-31T13:15:00+08:00")
    maintenance_calls: list[dict[str, object]] = []
    maintenance_started = threading.Event()

    def maintain_topics(**kwargs):
        maintenance_calls.append(kwargs)
        maintenance_started.set()
        return {"ok": True}

    async def wait_for_stop(_: datetime, stop_event: asyncio.Event) -> bool:
        await stop_event.wait()
        return True

    class FakeStore:
        path = tmp_path / "topics.json"

        def list_all_topics(self, **kwargs):
            return []

    class FakeClient:
        def __init__(self) -> None:
            self.last_topic_update = {"processed_memories": 1, "topics": []}
            self.last_topic_error = None

        def health(self):
            return {"status": "healthy"}

        def remember(self, *, user_id: str, cube_id: str, text: str):
            return {"message": "ok", "data": [{"id": "memory-1"}]}

    app = memos_frontend_api.create_app(
        store_factory=FakeStore,
        client_factory=lambda base_url: FakeClient(),
        upload_dir=tmp_path / "uploads",
        topic_scheduler_enabled=True,
        topic_maintainer=maintain_topics,
        topic_wait_until=wait_for_stop,
        clock=lambda: now,
    )

    with TestClient(app) as client:
        assert maintenance_started.wait(timeout=2)
        assert len(maintenance_calls) == 1
        response = client.post(
            "/runtime/remember",
            json={"user_id": "alice", "cube_id": "daily", "text": "记录一次新进展。"},
        )
        assert response.status_code == 200
        assert len(maintenance_calls) == 1


def test_topic_update_counts_only_core_topics():
    client = SimpleNamespace(
        last_topic_error=None,
        last_topic_update={
            "processed_memories": 2,
            "core_count": 1,
            "topics": [
                {"lifecycle_status": "active"},
                {"lifecycle_status": "suppressed"},
            ],
        },
    )

    assert memos_frontend_api._topic_update(client) == {
        "processed_memories": 2,
        "active_topics": 1,
        "error": None,
    }


def _queue_topic(
    key: str,
    *,
    status: str,
    queue_rank: int,
    importance_score: float = 70,
    approaching_bonus: float = 16,
    decay_penalty: float = 10,
    candidate_source: str | None = None,
    attention_status: str = "open",
) -> dict[str, object]:
    queue_score = importance_score + approaching_bonus - decay_penalty
    calculated_at = "2026-09-01T12:00:00+08:00"
    return {
        "topic_id": f"topic-{key}",
        "topic_key": key,
        "topic_text": f"主题 {key}",
        "reason_summary": f"{key} 的测试理由。",
        "lifecycle_status": status,
        "progress_status": "planned",
        "supporting_memory_ids": [f"memory-{key}"],
        "reason_evidence": [],
        "candidate_reasons": ["测试候选"],
        "score_breakdown": {"importance_score": importance_score},
        "rank_score": queue_score,
        "importance_score": importance_score,
        "approaching_bonus": approaching_bonus,
        "decay_penalty": decay_penalty,
        "queue_score": queue_score,
        "queue_score_breakdown": {
            "importance_score": importance_score,
            "approaching_bonus": approaching_bonus,
            "decay_penalty": decay_penalty,
            "queue_score": queue_score,
        },
        "queue_rank": queue_rank,
        "candidate_source": candidate_source,
        "attention_status": attention_status,
        "core_entered_at": "2026-09-01T00:00:00+08:00" if status == "active" else None,
        "demoted_at": ("2026-09-01T08:00:00+08:00" if candidate_source == "demoted" else None),
        "calculated_at": calculated_at,
        "first_seen_at": "2026-08-31T08:00:00+08:00",
        "last_evidence_at": "2026-09-01T08:00:00+08:00",
        "version": 1,
        "updated_at": calculated_at,
        "versions": [],
    }


def _queue_snapshot() -> dict[str, object]:
    cores = [
        _queue_topic(f"core-{index}", status="active", queue_rank=index) for index in range(1, 4)
    ]
    visible_candidates = [
        _queue_topic(
            f"candidate-{index}",
            status="suppressed",
            queue_rank=index,
            candidate_source="new",
        )
        for index in range(1, 28)
    ]
    return {
        "items": [*cores, *visible_candidates],
        "pool_total": 35,
        "candidate_pool_total": 32,
        "core_count": 3,
        "visible_candidate_count": 27,
        "hidden_candidate_count": 5,
        "queue_calculated_at": "2026-09-01T12:00:00+08:00",
    }


def test_v1_topics_returns_three_core_and_twenty_seven_visible_candidates(tmp_path: Path):
    calls: list[dict[str, object]] = []

    class FakeStore:
        path = tmp_path / "topics.json"

        def list_queue_snapshot(self, **kwargs):
            calls.append(kwargs)
            return _queue_snapshot()

    app = memos_frontend_api.create_app(
        store_factory=FakeStore,
        upload_dir=tmp_path / "uploads",
    )
    response = TestClient(app).get("/api/v1/topics")

    assert response.status_code == 200
    body = response.json()
    assert {key: body[key] for key in body if key != "items"} == {
        "total": 30,
        "returned": 30,
        "pool_total": 35,
        "candidate_pool_total": 32,
        "core_limit": 3,
        "visible_candidate_limit": 27,
        "core_count": 3,
        "visible_candidate_count": 27,
        "hidden_candidate_count": 5,
        "calculated_at": "2026-09-01T12:00:00+08:00",
    }
    assert [item["key"] for item in body["items"][:3]] == [
        "core-1",
        "core-2",
        "core-3",
    ]
    assert [item["queue_rank"] for item in body["items"][3:]] == list(range(1, 28))
    assert calls == [
        {
            "user_id": "default",
            "cube_id": "default_cube",
            "policy": memos_frontend_api.DEFAULT_TOPIC_QUEUE_POLICY,
        }
    ]


def test_v1_topics_reports_hidden_candidates_without_returning_them(tmp_path: Path):
    class FakeStore:
        path = tmp_path / "topics.json"

        def list_queue_snapshot(self, **kwargs):
            return _queue_snapshot()

    app = memos_frontend_api.create_app(
        store_factory=FakeStore,
        upload_dir=tmp_path / "uploads",
    )
    body = TestClient(app).get("/api/v1/topics").json()

    assert body["hidden_candidate_count"] == 5
    assert body["candidate_pool_total"] == 32
    assert len(body["items"]) == 30
    assert all(item["key"] != "candidate-28" for item in body["items"])


def test_v1_topic_item_exposes_queue_score_breakdown_and_real_rank(tmp_path: Path):
    topic = _queue_topic(
        "interview",
        status="suppressed",
        queue_rank=2,
        candidate_source="demoted",
        attention_status="past_unconfirmed",
    )

    class FakeStore:
        path = tmp_path / "topics.json"

        def list_queue_snapshot(self, **kwargs):
            return {
                "items": [topic],
                "pool_total": 1,
                "candidate_pool_total": 1,
                "core_count": 0,
                "visible_candidate_count": 1,
                "hidden_candidate_count": 0,
                "queue_calculated_at": "2026-09-01T12:00:00+08:00",
            }

    app = memos_frontend_api.create_app(
        store_factory=FakeStore,
        upload_dir=tmp_path / "uploads",
    )
    item = TestClient(app).get("/api/v1/topics").json()["items"][0]

    assert item["queue_rank"] == 2
    assert item["candidate_source"] == "demoted"
    assert item["attention_status"] == "past_unconfirmed"
    assert item["importance_score"] == 70
    assert item["approaching_bonus"] == 16
    assert item["decay_penalty"] == 10
    assert item["queue_score"] == 76
    assert item["score"] == 76
    assert item["queue_score_breakdown"] == {
        "importance_score": 70,
        "approaching_bonus": 16,
        "decay_penalty": 10,
        "queue_score": 76,
    }
    assert item["score_breakdown"] == {
        "model": "partial",
        "base_score": None,
        "recency_factor": 1.0,
        "rank_score": 76,
    }
    assert item["core_entered_at"] is None
    assert item["demoted_at"] == "2026-09-01T08:00:00+08:00"
    assert item["calculated_at"] == "2026-09-01T12:00:00+08:00"


def test_v1_dashboard_returns_only_three_core_topics(tmp_path: Path):
    calls: list[str] = []

    class FakeStore:
        path = tmp_path / "topics.json"

        def list_queue_snapshot(self, **kwargs):
            calls.append("snapshot")
            return _queue_snapshot()

    class FakeClient:
        def health(self):
            return {"status": "healthy", "version": "1.2.3"}

        def get_memory_dashboard(self, **kwargs):
            return {"data": {"statistics": {}}}

        def get_scheduler_status(self):
            return {}

        def get_task_queue_status(self, **kwargs):
            return {}

    app = memos_frontend_api.create_app(
        store_factory=FakeStore,
        client_factory=lambda base_url: FakeClient(),
        upload_dir=tmp_path / "uploads",
    )
    body = TestClient(app).get("/api/v1/dashboard").json()

    assert [item["key"] for item in body["topics"]] == ["core-1", "core-2", "core-3"]
    assert body["counts"]["active_topics"] == 3
    assert calls == ["snapshot"]


def test_v1_topic_trace_exposes_queue_policy_and_decision(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MEMOS_TOPIC_DAILY_LIMIT", "15")
    calls: list[dict[str, object]] = []
    topic = _queue_topic(
        "interview",
        status="suppressed",
        queue_rank=2,
        candidate_source="demoted",
        attention_status="past_unconfirmed",
    )

    class FakeStore:
        path = tmp_path / "topics.json"

        def topic_selection_trace(self, **kwargs):
            calls.append(kwargs)
            return {
                "topic_id": "topic-interview",
                "topic_key": "interview",
                "available": True,
                "policy": {"seat_limit": 15, "rubric": []},
                "grouping": {"shared_anchor": "2026-09-05 的面试"},
                "decision": {
                    "base_score": 70,
                    "recency_factor": 0.5,
                    "rank_score": 35,
                },
                "memories": [],
            }

        def topic_record(self, **kwargs):
            assert kwargs == {
                "user_id": "default",
                "cube_id": "default_cube",
                "topic_key": "interview",
            }
            return topic

    app = memos_frontend_api.create_app(
        store_factory=FakeStore,
        upload_dir=tmp_path / "uploads",
    )
    response = TestClient(app).get("/api/v1/topics/topic-interview/trace")

    assert response.status_code == 200
    body = response.json()
    assert body["policy"] == {
        "seat_limit": 3,
        "rubric": [],
        "queue_policy_version": 1,
        "core_limit": 3,
        "visible_candidate_limit": 27,
        "scheduled_promotion_margin": 5,
        "immediate_promotion_margin": 10,
        "queue_formula": "importance_score + approaching_bonus - decay_penalty",
    }
    assert body["decision"] == {
        "base_score": 70,
        "recency_factor": 1.0,
        "rank_score": 76,
        "importance_score": 70,
        "approaching_bonus": 16,
        "decay_penalty": 10,
        "queue_score": 76,
        "queue_rank": 2,
        "candidate_source": "demoted",
        "attention_status": "past_unconfirmed",
    }
    assert body["grouping"]["shared_anchor"] == "2026-09-05 的面试"
    assert calls == [
        {
            "user_id": "default",
            "cube_id": "default_cube",
            "topic_id": "topic-interview",
            "seat_limit": 3,
        }
    ]


def test_v1_topics_without_suppressed_returns_only_core_and_keeps_pool_counts(
    tmp_path: Path,
):
    class FakeStore:
        path = tmp_path / "topics.json"

        def list_queue_snapshot(self, **kwargs):
            return _queue_snapshot()

    app = memos_frontend_api.create_app(
        store_factory=FakeStore,
        upload_dir=tmp_path / "uploads",
    )
    body = TestClient(app).get("/api/v1/topics?include_suppressed=false").json()

    assert body["total"] == 3
    assert body["returned"] == 3
    assert body["pool_total"] == 35
    assert body["candidate_pool_total"] == 32
    assert body["core_count"] == 3
    assert body["visible_candidate_count"] == 0
    assert body["hidden_candidate_count"] == 5
    assert [item["status"] for item in body["items"]] == ["active", "active", "active"]


def test_legacy_topics_route_limits_response_without_truncating_persistent_pool(
    tmp_path: Path,
):
    snapshot = _queue_snapshot()
    stored_rows = [
        *snapshot["items"],
        *[
            _queue_topic(
                f"candidate-{index}",
                status="suppressed",
                queue_rank=index,
                candidate_source="new",
            )
            for index in range(28, 33)
        ],
    ]

    class FakeStore:
        path = tmp_path / "topics.json"

        def list_queue_snapshot(self, **kwargs):
            return snapshot

    app = memos_frontend_api.create_app(
        store_factory=FakeStore,
        upload_dir=tmp_path / "uploads",
    )
    client = TestClient(app)

    legacy = client.get("/topics?limit=500").json()
    current = client.get("/api/v1/topics").json()

    assert legacy["total"] == 30
    assert len(legacy["topics"]) == 30
    assert all(topic["topic_key"] != "candidate-28" for topic in legacy["topics"])
    assert len(stored_rows) == 35
    assert current["pool_total"] == 35
    assert current["hidden_candidate_count"] == 5


def test_both_reconcile_routes_cannot_restore_fifteen_core_seats(tmp_path: Path):
    calls: list[dict[str, object]] = []

    class FakeStore:
        path = tmp_path / "topics.json"

    def fake_reconciler(**kwargs):
        calls.append(kwargs)
        return 0

    app = memos_frontend_api.create_app(
        store_factory=FakeStore,
        reconciler=fake_reconciler,
        upload_dir=tmp_path / "uploads",
    )
    client = TestClient(app)

    v1_response = client.post("/api/v1/topics/reconcile")
    legacy_response = client.post("/topics/reconcile", json={"daily_limit": 15})

    assert v1_response.status_code == 200
    assert legacy_response.status_code == 200
    assert calls == [
        {"base_url": "http://127.0.0.1:8000", "daily_limit": 3},
        {"base_url": "http://127.0.0.1:8000", "daily_limit": 3},
    ]


def test_ingest_endpoint_persists_file_and_reuses_runtime_importer(tmp_path: Path):
    imported_paths: list[Path] = []

    class FakeStore:
        path = tmp_path / "topics.json"

        def list_all_topics(self, **kwargs):
            return []

    class FakeClient:
        def __init__(self) -> None:
            self.last_topic_update = {
                "rolling_limit": 15,
                "topics": [{"topic_key": "project", "topic_text": "用户正在推进项目。"}],
            }
            self.last_topic_error = None

        def health(self):
            return {"status": "healthy"}

    def fake_importer(client, **kwargs):
        path = Path(kwargs["file_path"])
        assert path.read_text(encoding="utf-8") == "今天完成了项目原型。"
        imported_paths.append(path)
        return SimpleNamespace(kind="text", path=path), {"message": "ok", "data": []}

    app = memos_frontend_api.create_app(
        store_factory=FakeStore,
        client_factory=lambda base_url: FakeClient(),
        importer=fake_importer,
        upload_dir=tmp_path / "uploads",
    )
    response = TestClient(app).post(
        "/ingest",
        data={"user_id": "default", "cube_id": "default_cube"},
        files={"file": ("today.txt", "今天完成了项目原型。".encode(), "text/plain")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "text"
    assert body["memories_created"] == 0
    assert body["topic_update"]["topics"][0]["topic_key"] == "project"
    assert imported_paths[0].is_file()
    assert imported_paths[0].parent == tmp_path / "uploads"
    assert imported_paths[0].name.endswith("-today.txt")


def test_runtime_remember_endpoint_reuses_memos_client_and_returns_topic_update(tmp_path: Path):
    calls: list[tuple[str, str, str]] = []
    tracker_calls: list[tuple[object, str, str, bool]] = []

    class FakeStore:
        path = tmp_path / "topics.json"

        def list_all_topics(self, **kwargs):
            return []

    class FakeClient:
        def __init__(self) -> None:
            self.last_topic_update = {
                "processed_memories": 1,
                "topics": [{"topic_key": "exam", "topic_text": "用户正在准备期末考试。"}],
            }
            self.last_topic_error = None

        def health(self):
            return {"status": "healthy"}

        def remember(self, *, user_id: str, cube_id: str, text: str):
            calls.append((user_id, cube_id, text))
            return {"message": "ok", "data": [{"id": "memory-1"}]}

    class FakeWorker:
        def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

        def register_add_response(
            self,
            result,
            *,
            user_id: str,
            cube_id: str,
            topic_sync_pending: bool = False,
        ) -> None:
            tracker_calls.append((result, user_id, cube_id, topic_sync_pending))

    app = memos_frontend_api.create_app(
        store_factory=FakeStore,
        client_factory=lambda base_url: FakeClient(),
        upload_dir=tmp_path / "uploads",
        tracker_worker=FakeWorker(),
    )
    response = TestClient(app).post(
        "/runtime/remember",
        json={
            "user_id": "alice",
            "cube_id": "daily",
            "text": "今天完成了项目原型。",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["memories_created"] == 1
    assert body["topic_update"]["processed_memories"] == 1
    assert calls == [("alice", "daily", "今天完成了项目原型。")]
    assert tracker_calls == [
        ({"message": "ok", "data": [{"id": "memory-1"}]}, "alice", "daily", False)
    ]


def test_runtime_remember_marks_topic_failure_for_tracker_retry(tmp_path: Path) -> None:
    tracker_calls: list[bool] = []

    class FakeStore:
        path = tmp_path / "topics.json"

        def list_all_topics(self, **kwargs):
            return []

    class FakeClient:
        last_topic_update = None
        last_topic_error = "topic unavailable"

        def health(self):
            return {"status": "healthy"}

        def remember(self, *, user_id: str, cube_id: str, text: str):
            return {"code": 200, "data": [{"id": "memory-1"}]}

    class FakeWorker:
        def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

        def register_add_response(
            self,
            result,
            *,
            user_id: str,
            cube_id: str,
            topic_sync_pending: bool = False,
        ) -> None:
            tracker_calls.append(topic_sync_pending)

    app = memos_frontend_api.create_app(
        store_factory=FakeStore,
        client_factory=lambda base_url: FakeClient(),
        upload_dir=tmp_path / "uploads",
        tracker_worker=FakeWorker(),
    )

    response = TestClient(app).post(
        "/runtime/remember",
        json={"user_id": "alice", "cube_id": "daily", "text": "任务已经完成。"},
    )

    assert response.status_code == 200
    assert tracker_calls == [True]


def test_runtime_chat_and_search_endpoints_keep_processing_in_backend(tmp_path: Path):
    calls: list[tuple[object, ...]] = []

    class FakeStore:
        path = tmp_path / "topics.json"

        def list_all_topics(self, **kwargs):
            return []

    class FakeClient:
        def health(self):
            return {"status": "healthy"}

        def chat(
            self,
            *,
            user_id: str,
            cube_id: str,
            session_id: str,
            query: str,
            model: str | None,
        ):
            calls.append(("chat", user_id, cube_id, session_id, query, model))
            return "后端回答"

        def search(self, *, user_id: str, cube_id: str, query: str):
            calls.append(("search", user_id, cube_id, query))
            return {"message": "ok", "data": [{"id": "memory-2", "memory": "相关记忆"}]}

    app = memos_frontend_api.create_app(
        store_factory=FakeStore,
        client_factory=lambda base_url: FakeClient(),
        upload_dir=tmp_path / "uploads",
    )
    client = TestClient(app)

    chat_response = client.post(
        "/runtime/chat",
        json={
            "user_id": "alice",
            "cube_id": "daily",
            "session_id": "session-1",
            "query": "我最近在忙什么？",
            "model": "qwen3-max-preview",
        },
    )
    search_response = client.post(
        "/runtime/search",
        json={"user_id": "alice", "cube_id": "daily", "query": "期末考试"},
    )

    assert chat_response.status_code == 200
    assert chat_response.json() == {
        "ok": True,
        "response": "后端回答",
        "session_id": "session-1",
    }
    assert search_response.status_code == 200
    assert search_response.json()["memos_result"]["data"][0]["id"] == "memory-2"
    assert calls == [
        ("chat", "alice", "daily", "session-1", "我最近在忙什么？", "qwen3-max-preview"),
        ("search", "alice", "daily", "期末考试"),
    ]


def test_runtime_video_endpoint_accepts_a_remote_url(tmp_path: Path):
    calls: list[tuple[object, ...]] = []

    class FakeStore:
        path = tmp_path / "topics.json"

        def list_all_topics(self, **kwargs):
            return []

    class FakeClient:
        def __init__(self) -> None:
            self.last_topic_update = {"processed_memories": 1, "topics": []}
            self.last_topic_error = None

        def health(self):
            return {"status": "healthy"}

        def remember_video(self, **kwargs):
            calls.append(tuple(kwargs.values()))
            return {"data": [{"id": "video-memory"}]}

    app = memos_frontend_api.create_app(
        store_factory=FakeStore,
        client_factory=lambda base_url: FakeClient(),
        upload_dir=tmp_path / "uploads",
    )
    response = TestClient(app).post(
        "/runtime/video",
        json={
            "user_id": "alice",
            "cube_id": "daily",
            "url": "https://media.example/recording.mp4",
            "instruction": "识别操作过程",
        },
    )

    assert response.status_code == 200
    assert response.json()["memories_created"] == 1
    assert calls == [
        (
            "alice",
            "daily",
            "https://media.example/recording.mp4",
            "识别操作过程",
            None,
        )
    ]


def test_topic_reconcile_and_memory_detail_endpoints(tmp_path: Path):
    reconcile_calls: list[tuple[str, int]] = []

    class FakeStore:
        path = tmp_path / "topics.json"

        def list_all_topics(self, **kwargs):
            return []

    class FakeClient:
        def health(self):
            return {"status": "healthy"}

        def get_memory(self, memory_id: str):
            assert memory_id == "memory-3"
            return {"message": "ok", "data": {"id": memory_id, "memory": "测试记忆"}}

    def fake_reconciler(*, base_url: str, daily_limit: int) -> int:
        reconcile_calls.append((base_url, daily_limit))
        return 2

    app = memos_frontend_api.create_app(
        store_factory=FakeStore,
        client_factory=lambda base_url: FakeClient(),
        reconciler=fake_reconciler,
        upload_dir=tmp_path / "uploads",
    )
    client = TestClient(app)

    reconcile_response = client.post("/topics/reconcile", json={"daily_limit": 15})
    detail_response = client.get("/runtime/memories/memory-3")

    assert reconcile_response.status_code == 200
    assert reconcile_response.json() == {"ok": True, "removed_memories": 2}
    assert reconcile_calls == [("http://127.0.0.1:8000", 3)]
    assert detail_response.status_code == 200
    assert detail_response.json()["memory"]["data"]["id"] == "memory-3"


def test_v1_dashboard_and_memories_expose_stable_application_models(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MEMOS_APP_USER_ID", "alice")
    monkeypatch.setenv("MEMOS_APP_CUBE_ID", "daily")
    calls: list[tuple[object, ...]] = []

    class FakeStore:
        path = tmp_path / "topics.json"

        def list_all_topics(self, **kwargs):
            calls.append(("topics", kwargs["user_id"], kwargs["cube_id"]))
            return [
                {
                    "topic_id": "topic-1",
                    "topic_key": "python",
                    "topic_text": "用户正在学习 Python。",
                    "reason_summary": "两条近期记忆提供了证据。",
                    "supporting_memory_ids": ["memory-1"],
                    "reason_evidence": [],
                    "candidate_reasons": [],
                    "score_breakdown": {"base_score": 80},
                    "rank_score": 80,
                    "progress_status": "ongoing",
                    "lifecycle_status": "active",
                    "first_seen_at": "2026-08-20T10:00:00+08:00",
                    "last_evidence_at": "2026-08-24T10:00:00+08:00",
                    "version": 2,
                    "updated_at": "2026-08-24T10:00:00+08:00",
                    "versions": [],
                }
            ]

        def list_queue_snapshot(self, **kwargs):
            rows = self.list_all_topics(**kwargs)
            return {
                "items": rows,
                "pool_total": 1,
                "candidate_pool_total": 0,
                "core_count": 1,
                "visible_candidate_count": 0,
                "hidden_candidate_count": 0,
                "queue_calculated_at": None,
            }

    class FakeClient:
        def health(self):
            return {"status": "healthy", "version": "1.2.3"}

        def get_memory_dashboard(self, *, user_id: str, cube_id: str):
            calls.append(("dashboard", user_id, cube_id))
            return {
                "data": {
                    "text_mem": [
                        {
                            "memories": [
                                {
                                    "id": "memory-1",
                                    "memory": "2026年8月24日，用户学习了 Python。",
                                    "metadata": {
                                        "key": "学习 Python",
                                        "memory_type": "LongTermMemory",
                                        "created_at": "2026-08-24T10:00:00+08:00",
                                        "tags": ["Python", "学习"],
                                        "embedding": [0.1, 0.2],
                                        "info": {
                                            "record_type": "event",
                                            "event_title": "学习 Python",
                                            "source_type": "local_video",
                                            "source_path": "D:/private/video.mp4",
                                            "media_uri": "oss://private/video.mp4",
                                        },
                                    },
                                }
                            ]
                        }
                    ],
                    "pref_mem": [],
                    "tool_mem": [],
                    "skill_mem": [],
                    "statistics": {
                        "total_text_nodes": 1,
                        "total_preference_nodes": 0,
                        "total_skill_nodes": 0,
                    },
                }
            }

        def get_scheduler_status(self):
            return {"data": {"scheduler_summary": {"total": 3, "in_progress": 1, "waiting": 2}}}

        def get_task_queue_status(self, *, user_id: str):
            calls.append(("queue", user_id))
            return {"data": {"status": "ok"}}

    app = memos_frontend_api.create_app(
        store_factory=FakeStore,
        client_factory=lambda base_url: FakeClient(),
        upload_dir=tmp_path / "uploads",
    )
    client = TestClient(app)

    dashboard_response = client.get("/api/v1/dashboard")
    memories_response = client.get("/api/v1/memories")

    assert dashboard_response.status_code == 200
    dashboard = dashboard_response.json()
    assert dashboard["backend_status"] == "online"
    assert dashboard["service_version"] == "1.2.3"
    assert dashboard["scope"] == {"user_id": "alice", "cube_id": "daily"}
    assert dashboard["counts"] == {
        "memories": 1,
        "preferences": 0,
        "skills": 0,
        "queue_total": 3,
        "queue_running": 1,
        "queue_waiting": 2,
        "active_topics": 1,
    }
    assert dashboard["topics"][0]["title"] == "用户正在学习 Python。"
    assert dashboard["memories"][0]["id"] == "memory-1"

    assert memories_response.status_code == 200
    memories = memories_response.json()
    assert memories["total"] == 1
    assert memories["items"] == [
        {
            "id": "memory-1",
            "title": "学习 Python",
            "content": "2026年8月24日，用户学习了 Python。",
            "memory_type": "LongTermMemory",
            "source": "video",
            "category": "media",
            "created_at": "2026-08-24T10:00:00+08:00",
            "updated_at": None,
            "tags": ["Python", "学习"],
        }
    ]
    serialized = memories_response.text.lower()
    assert "text_mem" not in serialized
    assert "embedding" not in serialized
    assert "source_path" not in serialized
    assert "media_uri" not in serialized
    assert calls.count(("dashboard", "alice", "daily")) == 2


def test_v1_topic_trace_exposes_saved_scoring_process(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MEMOS_APP_USER_ID", "alice")
    monkeypatch.setenv("MEMOS_APP_CUBE_ID", "daily")
    monkeypatch.setenv("MEMOS_TOPIC_DAILY_LIMIT", "7")
    calls: list[tuple[object, ...]] = []

    class FakeStore:
        path = tmp_path / "topics.json"

        def topic_selection_trace(self, **kwargs):
            calls.append(("trace", kwargs))
            if kwargs["topic_id"] == "missing-topic":
                return None
            return {
                "topic_id": "topic-1",
                "topic_key": "python",
                "available": True,
                "unavailable_reason": None,
                "selection_version": 2,
                "policy": {
                    "topic_threshold": 60.0,
                    "supporting_weight": 0.5,
                    "seat_limit": kwargs["seat_limit"],
                    "memory_formula": "min(100, 维度分合计) × 置信系数",
                    "topic_formula": "最强单条 + 其他非重复记忆 × 0.5",
                    "rank_formula": "Topic 基础分 × 新鲜系数",
                    "rubric": [],
                },
                "grouping": {
                    "topic_kind": "event",
                    "reason": "同一次学习任务。",
                    "candidate_tag_keys": ["python"],
                    "memory_ids": ["memory-1"],
                },
                "decision": {
                    "qualifies": True,
                    "base_score": 80.0,
                    "recency_factor": 0.9,
                    "rank_score": 72.0,
                    "rank_position": 1,
                    "seat_status": "active",
                    "candidate_reasons": ["单条记忆达到门槛"],
                },
                "memories": [],
            }

    app = memos_frontend_api.create_app(
        store_factory=FakeStore,
        client_factory=lambda base_url: SimpleNamespace(health=lambda: {"status": "healthy"}),
        upload_dir=tmp_path / "uploads",
    )
    client = TestClient(app)

    response = client.get("/api/v1/topics/topic-1/trace")
    missing_response = client.get("/api/v1/topics/missing-topic/trace")

    assert response.status_code == 200
    assert response.json()["decision"]["rank_score"] == 72.0
    assert response.json()["policy"]["seat_limit"] == 3
    assert missing_response.status_code == 404
    assert calls == [
        (
            "trace",
            {
                "user_id": "alice",
                "cube_id": "daily",
                "topic_id": "topic-1",
                "seat_limit": 3,
            },
        ),
        (
            "trace",
            {
                "user_id": "alice",
                "cube_id": "daily",
                "topic_id": "missing-topic",
                "seat_limit": 3,
            },
        ),
    ]


def test_v1_memory_detail_delete_and_search_keep_workflow_in_application_backend(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("MEMOS_APP_USER_ID", "alice")
    monkeypatch.setenv("MEMOS_APP_CUBE_ID", "daily")
    monkeypatch.setenv("MEMOS_TOPIC_DAILY_LIMIT", "17")
    calls: list[tuple[object, ...]] = []

    class FakeStore:
        path = tmp_path / "topics.json"

        def list_all_topics(self, **kwargs):
            return []

    class FakeClient:
        def get_memory(self, memory_id: str):
            calls.append(("detail", memory_id))
            return {
                "data": {
                    "id": memory_id,
                    "memory": "用户在图书馆学习 Python。",
                    "metadata": {
                        "key": "学习 Python",
                        "memory_type": "LongTermMemory",
                        "confidence": 0.9,
                        "background": "学习记录",
                        "tags": ["Python"],
                        "embedding": [0.1],
                        "info": {
                            "record_type": "event",
                            "event_start_time": "2026-08-24T09:00:00+08:00",
                            "event_end_time": "2026-08-24T10:00:00+08:00",
                            "event_location": "图书馆",
                            "participants": ["用户"],
                            "source_path": "D:/private/file.md",
                        },
                    },
                }
            }

        def delete_memory(self, memory_id: str):
            calls.append(("delete", memory_id))
            return {"data": {"status": "success"}}

        def search(self, *, user_id: str, cube_id: str, query: str):
            calls.append(("search", user_id, cube_id, query))
            return {
                "data": {
                    "text_mem": [
                        {
                            "id": "memory-7",
                            "memory": "用户正在学习 Python。",
                            "score": 0.88,
                            "metadata": {"key": "Python 学习", "embedding": [0.2]},
                        }
                    ]
                }
            }

    def fake_reconciler(*, base_url: str, daily_limit: int) -> int:
        calls.append(("reconcile", base_url, daily_limit))
        return 1

    app = memos_frontend_api.create_app(
        store_factory=FakeStore,
        client_factory=lambda base_url: FakeClient(),
        reconciler=fake_reconciler,
        upload_dir=tmp_path / "uploads",
    )
    client = TestClient(app)

    detail_response = client.get("/api/v1/memories/memory-3")
    delete_response = client.delete("/api/v1/memories/memory-3")
    search_response = client.post("/api/v1/search", json={"query": "Python"})

    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["memory"]["id"] == "memory-3"
    assert detail["memory"]["structured"]["event_location"] == "图书馆"
    assert detail["memory"]["structured"]["event_start_time"] == ("2026-08-24T09:00:00+08:00")
    assert detail["memory"]["structured"]["event_end_time"] == ("2026-08-24T10:00:00+08:00")
    assert "embedding" not in detail_response.text.lower()
    assert "source_path" not in detail_response.text.lower()

    assert delete_response.status_code == 200
    assert delete_response.json() == {
        "ok": True,
        "memory_id": "memory-3",
        "topic_sync": "updated",
        "removed_topic_memories": 1,
    }

    assert search_response.status_code == 200
    assert search_response.json() == {
        "results": [
            {
                "id": "memory-7",
                "title": "Python 学习",
                "content": "用户正在学习 Python。",
                "memory_type": "unknown",
                "source": "direct",
                "category": "other",
                "created_at": None,
                "updated_at": None,
                "tags": [],
                "score": 0.88,
            }
        ],
        "total": 1,
    }
    assert calls == [
        ("detail", "memory-3"),
        ("delete", "memory-3"),
        ("reconcile", "http://127.0.0.1:8000", 3),
        ("search", "alice", "daily", "Python"),
    ]


def test_v1_file_ingestion_uses_backend_scope_and_hides_internal_paths(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MEMOS_APP_USER_ID", "alice")
    monkeypatch.setenv("MEMOS_APP_CUBE_ID", "daily")
    calls: list[tuple[object, ...]] = []

    class FakeStore:
        path = tmp_path / "topics.json"

        def list_all_topics(self, **kwargs):
            return []

    class FakeClient:
        def __init__(self):
            self.last_topic_update = {"processed_memories": 2, "topics": [{}, {}]}
            self.last_topic_error = None

    def fake_importer(client, **kwargs):
        calls.append((kwargs["user_id"], kwargs["cube_id"], kwargs["instruction"]))
        return SimpleNamespace(kind="text"), {"data": [{"id": "memory-1"}, {"id": "memory-2"}]}

    app = memos_frontend_api.create_app(
        store_factory=FakeStore,
        client_factory=lambda base_url: FakeClient(),
        importer=fake_importer,
        upload_dir=tmp_path / "uploads",
    )
    response = TestClient(app).post(
        "/api/v1/ingestions",
        data={"instruction": "按时间提取"},
        files={"file": ("timeline.md", "# 时间流".encode(), "text/markdown")},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "kind": "text",
        "filename": "timeline.md",
        "file_size": len("# 时间流".encode()),
        "memories_created": 2,
        "topic": {"processed_memories": 2, "active_topics": 2, "error": None},
    }
    assert calls == [("alice", "daily", "按时间提取")]
    assert "stored_path" not in response.text
    assert "memos_result" not in response.text
