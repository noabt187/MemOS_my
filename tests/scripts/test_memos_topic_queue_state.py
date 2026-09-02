from __future__ import annotations

import json
import shutil
import sys

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest


SCRIPTS_DIR = Path(__file__).parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import memos_topic


USER_ID = "queue-user"
CUBE_ID = "queue-cube"


def _metrics(
    topic_key: str,
    score: float,
    observed_at: str,
    status: str = "planned",
    *,
    supporting_memory_ids: list[str] | None = None,
) -> memos_topic.CandidateMetrics:
    memory_ids = supporting_memory_ids or [f"memory-{topic_key}"]
    return memos_topic.CandidateMetrics(
        evidence_count=len(memory_ids),
        relationship_vote_sum=float(len(memory_ids)),
        qualifies=score >= 60,
        supporting_memory_ids=memory_ids,
        latest_evidence_at=observed_at,
        score_breakdown={
            "model": "static_importance_v3",
            "strongest_memory_score": score,
            "supporting_memory_points": 0.0,
            "duplicate_memory_count": 0,
            "counted_memory_ids": memory_ids,
            "importance_score": score,
            "base_score": score,
            "memory_scores": dict.fromkeys(memory_ids, score),
        },
        candidate_reasons=["固定规则测试"],
        progress_status=status,
        importance_score=score,
    )


def _assessment(memory_id: str, score: float = 70.0) -> memos_topic.MemoryAssessment:
    return memos_topic.MemoryAssessment(
        memory_id=memory_id,
        eligible=True,
        agency="committed",
        action_requirement="must_do",
        impact="high",
        explicit_priority="none",
        confidence="high",
        score=score,
        score_breakdown={
            "model": "static_importance_v3",
            "importance_score": score,
            "memory_score": score,
        },
        reasons={},
    )


def _save_memory(
    store: memos_topic.TopicStore,
    *,
    memory_id: str,
    topic_key: str,
    observed_at: str,
    event_time: str,
    event_status: str = "planned",
    text_suffix: str = "",
) -> None:
    store.save_memory(
        user_id=USER_ID,
        cube_id=CUBE_ID,
        memory={
            "id": memory_id,
            "memory": f"用户需要处理 {topic_key}{text_suffix}。",
            "metadata": {
                "status": "activated",
                "created_at": observed_at,
                "info": {
                    "record_type": "event",
                    "event_status": event_status,
                    "event_time": event_time,
                    "source_recorded_at": observed_at,
                },
            },
        },
    )


def _add_topic(
    store: memos_topic.TopicStore,
    topic_key: str,
    score: float,
    *,
    observed_at: str = "2026-09-01T08:00:00+08:00",
    event_time: str = "2026-09-10T10:00:00+08:00",
    event_status: str = "planned",
) -> str:
    memory_id = f"memory-{topic_key}"
    _save_memory(
        store,
        memory_id=memory_id,
        topic_key=topic_key,
        observed_at=observed_at,
        event_time=event_time,
        event_status=event_status,
    )
    return store.upsert_topic(
        user_id=USER_ID,
        cube_id=CUBE_ID,
        topic_key=topic_key,
        draft=memos_topic.TopicDraft(
            topic_text=f"主题 {topic_key}",
            reason_summary="由明确事件支持。",
            reason_evidence=[
                memos_topic.ReasonEvidence(
                    memory_id=memory_id,
                    fact=f"用户需要处理 {topic_key}。",
                    contribution="直接支持该主题。",
                )
            ],
        ),
        metrics=_metrics(topic_key, score, observed_at, event_status),
    )


def _topic(store: memos_topic.TopicStore, topic_key: str) -> dict[str, object]:
    value = store.topic_record(user_id=USER_ID, cube_id=CUBE_ID, topic_key=topic_key)
    assert value is not None
    return value


def _mutate_topic(
    store: memos_topic.TopicStore,
    topic_key: str,
    **updates: object,
) -> None:
    state = store._read()
    scope = store._scope(state, USER_ID, CUBE_ID)
    assert scope is not None
    scope["topics"][topic_key].update(updates)
    store._write(state)


def _scheduled(store: memos_topic.TopicStore, at: str) -> memos_topic.QueueRebalanceResult:
    return store.rebalance_queue(
        user_id=USER_ID,
        cube_id=CUBE_ID,
        now=datetime.fromisoformat(at),
        mode="scheduled",
        policy=memos_topic.DEFAULT_TOPIC_QUEUE_POLICY,
    )


def test_old_snapshot_is_initialized_to_queue_policy_v1_without_llm(tmp_path: Path):
    fixture = Path(__file__).parents[1] / "fixtures" / "topic_queue_v0.json"
    state_path = tmp_path / "topics.json"
    shutil.copyfile(fixture, state_path)
    before = fixture.read_text(encoding="utf-8")

    store = memos_topic.TopicStore(state_path)
    store.rebalance_queue(
        user_id="fixture-user",
        cube_id="fixture-cube",
        now=datetime.fromisoformat("2026-09-01T12:00:00+08:00"),
        mode="scheduled",
        policy=memos_topic.DEFAULT_TOPIC_QUEUE_POLICY,
    )

    items = store.list_topics(
        user_id="fixture-user",
        cube_id="fixture-cube",
        include_suppressed=True,
    )
    assert len([item for item in items if item["lifecycle_status"] == "active"]) == 3
    assert all(item["queue_policy_version"] == 1 for item in items)
    assert fixture.read_text(encoding="utf-8") == before


def test_queue_migration_is_idempotent(tmp_path: Path):
    fixture = Path(__file__).parents[1] / "fixtures" / "topic_queue_v0.json"
    state_path = tmp_path / "topics.json"
    shutil.copyfile(fixture, state_path)
    store = memos_topic.TopicStore(state_path)
    now = "2026-09-01T12:00:00+08:00"

    store.rebalance_queue(
        user_id="fixture-user",
        cube_id="fixture-cube",
        now=datetime.fromisoformat(now),
        mode="scheduled",
        policy=memos_topic.DEFAULT_TOPIC_QUEUE_POLICY,
    )
    first = store.list_topics(
        user_id="fixture-user",
        cube_id="fixture-cube",
        include_suppressed=True,
    )
    store.rebalance_queue(
        user_id="fixture-user",
        cube_id="fixture-cube",
        now=datetime.fromisoformat(now),
        mode="scheduled",
        policy=memos_topic.DEFAULT_TOPIC_QUEUE_POLICY,
    )
    second = store.list_topics(
        user_id="fixture-user",
        cube_id="fixture-cube",
        include_suppressed=True,
    )

    assert second == first


def test_text_only_revision_does_not_clear_decay(tmp_path: Path):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    _add_topic(store, "target", 70)
    _mutate_topic(
        store,
        "target",
        lifecycle_status="suppressed",
        candidate_source="demoted",
        demoted_at="2026-09-01T00:00:00+08:00",
        penalty_at_demotion=10.0,
        decay_penalty=10.0,
    )
    _save_memory(
        store,
        memory_id="memory-target",
        topic_key="target",
        observed_at="2026-09-01T08:00:00+08:00",
        event_time="2026-09-10T10:00:00+08:00",
        text_suffix="（只是润色文字）",
    )
    store.upsert_topic(
        user_id=USER_ID,
        cube_id=CUBE_ID,
        topic_key="target",
        draft=memos_topic.TopicDraft("主题 target", "摘要更新", []),
        metrics=_metrics("target", 70, "2026-09-01T08:00:00+08:00"),
    )

    assert _topic(store, "target")["decay_penalty"] == 10.0
    assert _topic(store, "target")["candidate_source"] == "demoted"


def test_old_historical_image_does_not_clear_decay(tmp_path: Path):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    _add_topic(store, "target", 70, observed_at="2026-09-01T08:00:00+08:00")
    _mutate_topic(
        store,
        "target",
        lifecycle_status="suppressed",
        candidate_source="demoted",
        demoted_at="2026-09-01T00:00:00+08:00",
        penalty_at_demotion=10.0,
        decay_penalty=10.0,
    )
    _save_memory(
        store,
        memory_id="memory-old-image",
        topic_key="target",
        observed_at="2026-08-01T08:00:00+08:00",
        event_time="2026-09-10T10:00:00+08:00",
    )
    store.upsert_topic(
        user_id=USER_ID,
        cube_id=CUBE_ID,
        topic_key="target",
        draft=memos_topic.TopicDraft("主题 target", "加入旧截图", []),
        metrics=_metrics(
            "target",
            70,
            "2026-09-01T08:00:00+08:00",
            supporting_memory_ids=["memory-target", "memory-old-image"],
        ),
    )

    assert _topic(store, "target")["decay_penalty"] == 10.0
    assert _topic(store, "target")["candidate_source"] == "demoted"


def test_newer_duplicate_screenshot_does_not_clear_decay(tmp_path: Path):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    _save_memory(
        store,
        memory_id="memory-original",
        topic_key="duplicate",
        observed_at="2026-09-01T08:00:00+08:00",
        event_time="2026-09-10T10:00:00+08:00",
    )
    original_memory = store.memories_by_ids(["memory-original"])[0]
    original_metrics = memos_topic.compute_topic_metrics(
        assessments=[_assessment("memory-original")],
        memories=[original_memory],
        now=datetime.fromisoformat("2026-09-01T09:00:00+08:00"),
    )
    store.upsert_topic(
        user_id=USER_ID,
        cube_id=CUBE_ID,
        topic_key="duplicate",
        draft=memos_topic.TopicDraft("主题 duplicate", "原始证据", []),
        metrics=original_metrics,
    )
    _mutate_topic(
        store,
        "duplicate",
        lifecycle_status="suppressed",
        candidate_source="demoted",
        demoted_at="2026-09-01T00:00:00+08:00",
        penalty_at_demotion=10.0,
        decay_penalty=10.0,
    )
    _save_memory(
        store,
        memory_id="memory-duplicate",
        topic_key="duplicate",
        observed_at="2026-09-02T08:00:00+08:00",
        event_time="2026-09-10T10:00:00+08:00",
    )
    memories = store.memories_by_ids(["memory-original", "memory-duplicate"])
    duplicate_metrics = memos_topic.compute_topic_metrics(
        assessments=[_assessment("memory-original"), _assessment("memory-duplicate")],
        memories=memories,
        now=datetime.fromisoformat("2026-09-02T09:00:00+08:00"),
    )
    store.upsert_topic(
        user_id=USER_ID,
        cube_id=CUBE_ID,
        topic_key="duplicate",
        draft=memos_topic.TopicDraft("主题 duplicate", "重复截图", []),
        metrics=duplicate_metrics,
    )

    topic = _topic(store, "duplicate")
    assert duplicate_metrics.latest_evidence_at == "2026-09-01T08:00:00+08:00"
    assert duplicate_metrics.supporting_memory_ids == ["memory-original", "memory-duplicate"]
    assert topic["decay_penalty"] == 10.0
    assert topic["candidate_source"] == "demoted"


def test_promoted_topic_keeps_existing_penalty(tmp_path: Path):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    for key, score in (("core-a", 80), ("core-b", 75), ("core-c", 70), ("target", 100)):
        _add_topic(store, key, score)
    _scheduled(store, "2026-09-01T00:00:00+08:00")
    _mutate_topic(
        store,
        "target",
        lifecycle_status="suppressed",
        candidate_source="demoted",
        demoted_at="2026-08-30T00:00:00+08:00",
        penalty_at_demotion=10.0,
        decay_penalty=10.0,
    )

    _scheduled(store, "2026-09-01T12:00:00+08:00")

    target = _topic(store, "target")
    assert target["lifecycle_status"] == "active"
    assert target["candidate_source"] is None
    assert target["decay_penalty"] == 10.0


def test_rank_score_is_kept_as_queue_score_alias(tmp_path: Path):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    _add_topic(store, "alias", 70, event_time="2026-09-02T10:00:00+08:00")
    _scheduled(store, "2026-09-01T12:00:00+08:00")

    topic = _topic(store, "alias")
    assert topic["rank_score"] == topic["queue_score"]
    assert topic["queue_score_breakdown"]["queue_score"] == topic["queue_score"]


def test_queue_mutation_reads_and_writes_state_once(tmp_path: Path, monkeypatch):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    reads = MagicMock(wraps=store._read_unlocked)
    writes = MagicMock(wraps=store._write_unlocked)
    monkeypatch.setattr(store, "_read_unlocked", reads)
    monkeypatch.setattr(store, "_write_unlocked", writes)

    result = store.mutate_queue_state(lambda state: state.setdefault("marker", "ok"))

    assert result == "ok"
    assert reads.call_count == 1
    assert writes.call_count == 1


def test_one_topic_calculation_failure_preserves_its_previous_snapshot(
    tmp_path: Path,
    monkeypatch,
):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    _add_topic(store, "good", 80)
    _add_topic(store, "broken", 70)
    before = _topic(store, "broken")
    original = memos_topic.TopicStore._recalculate_queue_topic

    def recalculate(scope, topic, *, now, policy):
        if topic.get("topic_key") == "broken":
            raise ValueError("fixture failure")
        return original(scope, topic, now=now, policy=policy)

    monkeypatch.setattr(
        memos_topic.TopicStore,
        "_recalculate_queue_topic",
        staticmethod(recalculate),
    )
    _scheduled(store, "2026-09-01T12:00:00+08:00")

    after = _topic(store, "broken")
    for field in ("importance_score", "queue_score", "candidate_source", "decay_penalty"):
        assert after[field] == before[field]
    assert after["last_queue_error"] == "fixture failure"


def test_failed_atomic_replace_leaves_previous_json_readable(tmp_path: Path, monkeypatch):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    old_state = json.loads(store.path.read_text(encoding="utf-8"))
    monkeypatch.setattr(memos_topic.os, "replace", MagicMock(side_effect=OSError("disk full")))

    with pytest.raises(OSError, match="disk full"):
        store.mutate_queue_state(lambda state: state.update({"marker": "new"}))

    assert json.loads(store.path.read_text(encoding="utf-8")) == old_state


def test_candidate_twenty_eight_remains_and_can_reappear_when_time_approaches(
    tmp_path: Path,
):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    for index in range(31):
        _add_topic(store, f"topic-{index:02d}", 100 - index)
    _add_topic(store, "wake-up", 60, event_time="2026-09-30T10:00:00+08:00")
    _scheduled(store, "2026-09-01T00:00:00+08:00")
    first = store.list_queue_snapshot(user_id=USER_ID, cube_id=CUBE_ID)
    assert "wake-up" not in {item["topic_key"] for item in first["items"]}
    assert _topic(store, "wake-up")["lifecycle_status"] == "suppressed"

    _save_memory(
        store,
        memory_id="memory-wake-up",
        topic_key="wake-up",
        observed_at="2026-09-01T08:00:00+08:00",
        event_time="2026-09-02T10:00:00+08:00",
    )
    store.upsert_topic(
        user_id=USER_ID,
        cube_id=CUBE_ID,
        topic_key="wake-up",
        draft=memos_topic.TopicDraft("主题 wake-up", "事件临近", []),
        metrics=_metrics("wake-up", 60, "2026-09-01T08:00:00+08:00"),
    )
    _scheduled(store, "2026-09-01T12:00:00+08:00")
    second = store.list_queue_snapshot(user_id=USER_ID, cube_id=CUBE_ID)

    assert "wake-up" in {item["topic_key"] for item in second["items"]}
    assert second["pool_total"] == 32


def test_equal_scores_use_event_evidence_and_topic_id_tie_breakers(tmp_path: Path):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    _add_topic(store, "later", 70, event_time="2026-09-05T10:00:00+08:00")
    _add_topic(store, "earlier", 70, event_time="2026-09-04T10:00:00+08:00")
    _add_topic(store, "same-z", 70, event_time="2026-09-05T10:00:00+08:00")
    _mutate_topic(store, "later", topic_id="topic-b")
    _mutate_topic(store, "same-z", topic_id="topic-a")

    _scheduled(store, "2026-09-01T12:00:00+08:00")
    ordered = store.list_topics(user_id=USER_ID, cube_id=CUBE_ID, include_suppressed=True)

    assert [item["topic_key"] for item in ordered] == ["earlier", "same-z", "later"]


def test_repeated_rebalance_produces_identical_order(tmp_path: Path):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    for key in ("alpha", "beta", "gamma", "delta"):
        _add_topic(store, key, 70)
    when = "2026-09-01T12:00:00+08:00"
    _scheduled(store, when)
    first = [
        (item["topic_id"], item["lifecycle_status"], item["queue_rank"])
        for item in store.list_topics(user_id=USER_ID, cube_id=CUBE_ID, include_suppressed=True)
    ]
    _scheduled(store, when)
    second = [
        (item["topic_id"], item["lifecycle_status"], item["queue_rank"])
        for item in store.list_topics(user_id=USER_ID, cube_id=CUBE_ID, include_suppressed=True)
    ]

    assert second == first


def test_below_threshold_topic_is_hidden_but_not_retired(tmp_path: Path):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    _add_topic(store, "small", 59)
    _scheduled(store, "2026-09-01T12:00:00+08:00")

    topic = _topic(store, "small")
    assert topic["qualifies"] is False
    assert topic["lifecycle_status"] == "suppressed"
    assert topic["retired_reason"] is None
    assert store.list_queue_snapshot(user_id=USER_ID, cube_id=CUBE_ID)["items"] == []


def test_cancelled_topic_retires_and_fills_vacancy(tmp_path: Path):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    for key, score in (("a", 90), ("b", 80), ("cancelled", 70), ("next", 65)):
        _add_topic(store, key, score)
    _scheduled(store, "2026-09-01T00:00:00+08:00")
    _add_topic(store, "cancelled", 70, event_status="cancelled")

    result = store.rebalance_queue(
        user_id=USER_ID,
        cube_id=CUBE_ID,
        now=datetime.fromisoformat("2026-09-01T01:00:00+08:00"),
        mode="vacancy",
        policy=memos_topic.DEFAULT_TOPIC_QUEUE_POLICY,
    )

    assert _topic(store, "cancelled")["lifecycle_status"] == "retired"
    assert len(result.core_topic_ids) == 3
    assert "next" in {
        item["topic_key"] for item in store.list_topics(user_id=USER_ID, cube_id=CUBE_ID)
    }


def test_rescheduled_topic_clears_past_unconfirmed_and_decay(tmp_path: Path):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    _add_topic(store, "rescheduled", 90, event_time="2026-09-01T10:00:00+08:00")
    _add_topic(store, "a", 80)
    _add_topic(store, "b", 70)
    _scheduled(store, "2026-09-01T09:00:00+08:00")
    _scheduled(store, "2026-09-02T12:00:00+08:00")
    assert _topic(store, "rescheduled")["attention_status"] == "past_unconfirmed"

    _add_topic(
        store,
        "rescheduled",
        90,
        observed_at="2026-09-02T13:00:00+08:00",
        event_time="2026-09-10T10:00:00+08:00",
    )
    store.rebalance_queue(
        user_id=USER_ID,
        cube_id=CUBE_ID,
        now=datetime.fromisoformat("2026-09-02T13:00:00+08:00"),
        mode="ingest",
        policy=memos_topic.DEFAULT_TOPIC_QUEUE_POLICY,
    )

    topic = _topic(store, "rescheduled")
    assert topic["attention_status"] == "open"
    assert topic["candidate_source"] == "refreshed"
    assert topic["decay_penalty"] == 0


def test_new_non_actionable_historical_event_never_enters_candidate_pool(tmp_path: Path):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    _add_topic(
        store,
        "historical",
        90,
        observed_at="2026-08-10T08:00:00+08:00",
        event_time="2026-08-10T10:00:00+08:00",
    )
    _mutate_topic(store, "historical", first_seen_at="2026-09-01T08:00:00+08:00")

    _scheduled(store, "2026-09-01T12:00:00+08:00")

    topic = _topic(store, "historical")
    assert topic["lifecycle_status"] == "retired"
    assert topic["retired_reason"] == "historical_event_before_import"
    assert store.list_queue_snapshot(user_id=USER_ID, cube_id=CUBE_ID)["pool_total"] == 0


def test_date_only_event_does_not_trigger_immediate_replacement(tmp_path: Path):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    for key, score in (("a", 90), ("b", 80), ("c", 70)):
        _add_topic(store, key, score)
    _scheduled(store, "2026-09-01T00:00:00+08:00")
    _add_topic(store, "date-only", 100, event_time="2026-09-01")

    result = store.rebalance_queue(
        user_id=USER_ID,
        cube_id=CUBE_ID,
        now=datetime.fromisoformat("2026-09-01T09:00:00+08:00"),
        mode="ingest",
        policy=memos_topic.DEFAULT_TOPIC_QUEUE_POLICY,
    )

    assert result.promoted_topic_ids == []
    assert _topic(store, "date-only")["lifecycle_status"] == "suppressed"


def test_immediate_promotion_requires_event_before_next_scheduled_slot(tmp_path: Path):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    for key, score in (("a", 90), ("b", 80), ("c", 70)):
        _add_topic(store, key, score)
    _scheduled(store, "2026-09-01T00:00:00+08:00")
    _add_topic(store, "after-slot", 100, event_time="2026-09-01T13:00:00+08:00")

    result = store.rebalance_queue(
        user_id=USER_ID,
        cube_id=CUBE_ID,
        now=datetime.fromisoformat("2026-09-01T09:00:00+08:00"),
        mode="ingest",
        policy=memos_topic.DEFAULT_TOPIC_QUEUE_POLICY,
    )

    assert result.promoted_topic_ids == []
    assert _topic(store, "after-slot")["lifecycle_status"] == "suppressed"


def test_unrelated_ingest_cannot_promote_an_old_urgent_candidate(tmp_path: Path):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    for key, score in (("a", 90), ("b", 80), ("c", 70)):
        _add_topic(store, key, score)
    _scheduled(store, "2026-09-01T00:00:00+08:00")
    _add_topic(store, "old-urgent", 100, event_time="2026-09-01T11:00:00+08:00")
    _add_topic(store, "new-ordinary", 65, event_time="2026-09-10T10:00:00+08:00")
    old_urgent_calculated_at = _topic(store, "old-urgent")["calculated_at"]

    result = store.rebalance_queue(
        user_id=USER_ID,
        cube_id=CUBE_ID,
        now=datetime.fromisoformat("2026-09-01T09:00:00+08:00"),
        mode="ingest",
        policy=memos_topic.DEFAULT_TOPIC_QUEUE_POLICY,
        affected_topic_keys={"new-ordinary"},
    )

    assert result.promoted_topic_ids == []
    assert _topic(store, "old-urgent")["lifecycle_status"] == "suppressed"
    assert _topic(store, "old-urgent")["calculated_at"] == old_urgent_calculated_at


def test_reconcile_limits_queue_refresh_to_the_deactivated_topic(monkeypatch, tmp_path: Path):
    store = MagicMock()
    store.active_memory_ids.return_value = ["memory-a"]
    store.deactivate_memory.return_value = [(USER_ID, CUBE_ID, "topic-a")]
    store.topic_record.return_value = {"selection_version": memos_topic.TOPIC_SELECTION_VERSION}
    client = MagicMock()
    client.get_by_ids.return_value = []
    processor = memos_topic.TopicProcessor(store=store, memos_client=client, llm=MagicMock())
    rebuild = MagicMock(return_value={"topic-a"})
    monkeypatch.setattr(processor, "_rebuild_topics", rebuild)

    removed = processor._reconcile(batch_size=10)

    assert removed == 1
    assert rebuild.call_args.kwargs["affected_memory_ids"] == {"memory-a"}
    assert store.rebalance_queue.call_args.kwargs["affected_topic_keys"] == {"topic-a"}


def test_queue_migration_flag_survives_upsert_before_first_rebalance(tmp_path: Path):
    fixture = Path(__file__).parents[1] / "fixtures" / "topic_queue_v0.json"
    state_path = tmp_path / "topics.json"
    shutil.copyfile(fixture, state_path)
    store = memos_topic.TopicStore(state_path)
    for key, score in (
        ("exam", 90),
        ("interview", 85),
        ("project", 80),
        ("reading", 75),
        ("exercise", 70),
    ):
        store.upsert_topic(
            user_id="fixture-user",
            cube_id="fixture-cube",
            topic_key=key,
            draft=memos_topic.TopicDraft(f"主题 {key}", "迁移前重建", []),
            metrics=_metrics(
                key,
                score,
                "2026-08-31T08:00:00+08:00",
                supporting_memory_ids=[],
            ),
        )

    store.rebalance_queue(
        user_id="fixture-user",
        cube_id="fixture-cube",
        now=datetime.fromisoformat("2026-09-01T12:00:00+08:00"),
        mode="scheduled",
        policy=memos_topic.DEFAULT_TOPIC_QUEUE_POLICY,
    )
    topics = store.list_topics(
        user_id="fixture-user",
        cube_id="fixture-cube",
        include_suppressed=True,
    )
    candidates = [item for item in topics if item["lifecycle_status"] == "suppressed"]

    assert len([item for item in topics if item["lifecycle_status"] == "active"]) == 3
    assert all(item["candidate_source"] == "new" for item in candidates)
    assert all(item["decay_penalty"] == 0 for item in candidates)


def test_failed_core_keeps_its_previous_queue_snapshot(tmp_path: Path, monkeypatch):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    for key, score in (("a", 90), ("b", 80), ("broken-core", 70)):
        _add_topic(store, key, score)
    _scheduled(store, "2026-09-01T00:00:00+08:00")
    before = _topic(store, "broken-core")
    assert before["lifecycle_status"] == "active"
    _add_topic(store, "challenger", 100)
    original = memos_topic.TopicStore._recalculate_queue_topic

    def recalculate(scope, topic, *, now, policy):
        if topic.get("topic_key") == "broken-core":
            raise ValueError("fixture failure")
        return original(scope, topic, now=now, policy=policy)

    monkeypatch.setattr(
        memos_topic.TopicStore,
        "_recalculate_queue_topic",
        staticmethod(recalculate),
    )
    _scheduled(store, "2026-09-01T12:00:00+08:00")
    after = _topic(store, "broken-core")

    for field in (
        "lifecycle_status",
        "candidate_source",
        "attention_status",
        "core_entered_at",
        "demoted_at",
        "decay_penalty",
        "queue_score",
        "queue_rank",
        "calculated_at",
    ):
        assert after[field] == before[field]
    assert after["last_queue_error"] == "fixture failure"


def test_malformed_topic_field_isolated_to_one_topic(tmp_path: Path):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    _add_topic(
        store,
        "broken",
        80,
        observed_at="2026-09-01T08:00:00+08:00",
        event_time="2026-09-01T10:00:00+08:00",
    )
    _add_topic(store, "good", 70)
    _mutate_topic(store, "broken", first_seen_at=123)

    result = _scheduled(store, "2026-09-02T12:00:00+08:00")

    broken = _topic(store, "broken")
    assert broken["last_queue_error"]
    assert "good" in {
        item["topic_key"]
        for item in store.list_topics(user_id=USER_ID, cube_id=CUBE_ID, include_suppressed=True)
    }
    assert result.calculated_at == "2026-09-02T12:00:00+08:00"


def test_naive_event_time_is_interpreted_in_policy_timezone_for_immediate_promotion(
    tmp_path: Path,
):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    for key, score in (("a", 90), ("b", 80), ("c", 70)):
        _add_topic(store, key, score)
    _scheduled(store, "2026-09-01T00:00:00+08:00")
    _add_topic(store, "naive-urgent", 80, event_time="2026-09-01T11:00:00")

    result = store.rebalance_queue(
        user_id=USER_ID,
        cube_id=CUBE_ID,
        now=datetime.fromisoformat("2026-09-01T09:00:00+08:00"),
        mode="ingest",
        policy=memos_topic.DEFAULT_TOPIC_QUEUE_POLICY,
        affected_topic_keys={"naive-urgent"},
    )

    assert len(result.promoted_topic_ids) == 1
    assert _topic(store, "naive-urgent")["lifecycle_status"] == "active"


def test_batch_ingest_promotes_every_urgent_topic_that_clears_the_margin(tmp_path: Path):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    for key, score in (("a", 90), ("b", 80), ("c", 70)):
        _add_topic(store, key, score)
    _scheduled(store, "2026-09-01T00:00:00+08:00")
    _add_topic(store, "urgent-one", 100, event_time="2026-09-01T11:00:00+08:00")
    _add_topic(store, "urgent-two", 90, event_time="2026-09-01T11:30:00+08:00")

    result = store.rebalance_queue(
        user_id=USER_ID,
        cube_id=CUBE_ID,
        now=datetime.fromisoformat("2026-09-01T09:00:00+08:00"),
        mode="ingest",
        policy=memos_topic.DEFAULT_TOPIC_QUEUE_POLICY,
        affected_topic_keys={"urgent-one", "urgent-two"},
    )

    assert len(result.promoted_topic_ids) == 2
    assert len(result.demoted_topic_ids) == 2
    assert _topic(store, "urgent-one")["lifecycle_status"] == "active"
    assert _topic(store, "urgent-two")["lifecycle_status"] == "active"


def test_nonterminal_retired_topic_keeps_zero_queue_score_on_rebalance(tmp_path: Path):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    _add_topic(store, "retired", 90)
    topic = _topic(store, "retired")
    _mutate_topic(
        store,
        "retired",
        lifecycle_status="retired",
        candidate_source=None,
        queue_rank=None,
        retired_reason="supporting_memories_inactive",
        queue_score=0.0,
        rank_score=0.0,
        queue_score_breakdown={
            "importance_score": topic["importance_score"],
            "approaching_bonus": 0.0,
            "decay_penalty": 0.0,
            "queue_score": 0.0,
        },
    )

    _scheduled(store, "2026-09-01T12:00:00+08:00")

    after = _topic(store, "retired")
    assert after["lifecycle_status"] == "retired"
    assert after["candidate_source"] is None
    assert after["queue_rank"] is None
    assert after["queue_score"] == 0
    assert after["rank_score"] == 0


def test_same_scheduled_slot_returns_without_rewriting_state(tmp_path: Path, monkeypatch):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    slot = datetime.fromisoformat("2026-09-01T12:00:00+08:00")
    store.rebalance_all_scopes(now=slot, scheduled_slot=slot)
    writes = MagicMock(wraps=store._write_unlocked)
    monkeypatch.setattr(store, "_write_unlocked", writes)

    result = store.rebalance_all_scopes(now=slot, scheduled_slot=slot)

    assert result["already_applied"] is True
    assert writes.call_count == 0
