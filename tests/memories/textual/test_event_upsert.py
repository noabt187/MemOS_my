import pytest

from memos.exceptions import MemoryError as MemOSMemoryError
from memos.memories.textual.event_upsert import (
    apply_event_upserts,
    retrieve_existing_event_candidates,
)
from memos.memories.textual.item import TextualMemoryItem, TreeNodeTextualMemoryMetadata


class FakeEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text))] for text in texts]


class FakeTextMemory:
    def __init__(self, memories: list[TextualMemoryItem] | None = None) -> None:
        self.embedder = FakeEmbedder()
        self.nodes = {memory.id: memory.model_copy(deep=True) for memory in memories or []}
        self.add_calls = 0

    def add(
        self,
        memories: list[TextualMemoryItem],
        user_name: str | None = None,
    ) -> list[str]:
        self.add_calls += 1
        for memory in memories:
            self.nodes[memory.id] = memory.model_copy(deep=True)
        return [memory.id for memory in memories]

    def get(self, memory_id: str, user_name: str | None = None) -> TextualMemoryItem:
        return self.nodes[memory_id].model_copy(deep=True)


class FakeGraphStore:
    def __init__(self, memory_id: str) -> None:
        self.memory_id = memory_id
        self.search_kwargs = None

    def search_by_embedding(self, embedding, **kwargs):
        self.search_kwargs = kwargs
        return [{"id": self.memory_id, "score": 0.9}]


class FailingRevisionGraphStore:
    def add_node(self, memory_id, memory, metadata, user_name=None):
        raise OSError("database unavailable")


class StrictFailingRevisionGraphStore:
    def __init__(self) -> None:
        self.regular_calls = 0
        self.strict_calls = 0

    def add_node(self, memory_id, memory, metadata, user_name=None):
        self.regular_calls += 1

    def add_node_strict(self, memory_id, memory, metadata, user_name=None):
        self.strict_calls += 1
        raise OSError("vector database unavailable")


def make_event(
    memory: str,
    *,
    status: str,
    event_time: str = "2026-08-28T10:00:00+08:00",
) -> TextualMemoryItem:
    return TextualMemoryItem(
        memory=memory,
        metadata=TreeNodeTextualMemoryMetadata(
            user_id="user-1",
            session_id="session-1",
            status="activated",
            memory_type="LongTermMemory",
            embedding=[0.1],
            key="A公司技术面试",
            info={
                "record_type": "event",
                "event_type": "meeting",
                "event_status": status,
                "event_actor": "用户",
                "event_action": "参加技术面试",
                "event_target": "A公司",
                "event_time": event_time,
            },
        ),
    )


def set_decision(
    event: TextualMemoryItem,
    operation: str,
    *,
    target_memory_id: str | None = None,
    changed_fields: list[str] | None = None,
) -> None:
    event.metadata.internal_info = {
        "event_upsert": {
            "operation": operation,
            "target_memory_id": target_memory_id,
            "changed_fields": changed_fields or [],
            "reason": "测试决策",
        }
    }


def test_update_keeps_one_active_id_and_appends_one_history_snapshot() -> None:
    existing = make_event(
        "用户计划于2026年8月28日10:00参加A公司的技术面试。",
        status="planned",
    )
    incoming = make_event(
        "用户于2026年8月28日10:00开始参加A公司的技术面试。",
        status="ongoing",
    )
    incoming.metadata.info["event_start_time"] = "2026-08-28T10:00:00+08:00"
    set_decision(
        incoming,
        "UPDATE",
        target_memory_id=existing.id,
        changed_fields=["event_status", "event_start_time", "memory"],
    )
    text_mem = FakeTextMemory([existing])

    result = apply_event_upserts(
        text_mem=text_mem,
        events=[incoming],
        existing_events=[existing],
        user_name="cube-1",
    )

    assert result.added == 0
    assert result.updated == 1
    assert result.unchanged == 0
    assert result.memory_ids == [existing.id]
    assert list(text_mem.nodes) == [existing.id]
    updated = text_mem.nodes[existing.id]
    assert updated.metadata.version == 2
    assert updated.metadata.info["event_status"] == "ongoing"
    assert updated.metadata.info["event_start_time"] == "2026-08-28T10:00:00+08:00"
    assert [item.memory for item in updated.metadata.history] == [existing.memory]


def test_model_decided_planned_reschedule_updates_same_event_without_event_number() -> None:
    existing = make_event(
        "用户计划于2026年9月2日15:00在星河科技园参加产品经理岗位面试。",
        status="planned",
        event_time="2026-09-02T15:00:00+08:00",
    )
    existing.metadata.info.update(
        {
            "event_target": "产品经理岗位",
            "event_location": "星河科技园A座12楼",
        }
    )
    rescheduled = make_event(
        "HR通知用户，同一场产品经理岗位面试改到2026年9月8日15:00，地点不变。",
        status="planned",
        event_time="2026-09-08T15:00:00+08:00",
    )
    rescheduled.metadata.info.update(
        {
            "event_target": "产品经理岗位",
            "event_location": "星河科技园A座12楼",
        }
    )
    set_decision(
        rescheduled,
        "UPDATE",
        target_memory_id=existing.id,
        changed_fields=["event_time", "memory"],
    )
    text_mem = FakeTextMemory([existing])

    result = apply_event_upserts(
        text_mem=text_mem,
        events=[rescheduled],
        existing_events=[existing],
        user_name="cube-1",
    )

    assert result.added == 0
    assert result.updated == 1
    assert result.memory_ids == [existing.id]
    assert list(text_mem.nodes) == [existing.id]
    updated = text_mem.nodes[existing.id]
    assert updated.metadata.version == 2
    assert updated.metadata.info["event_time"] == "2026-09-08T15:00:00+08:00"
    assert [item.memory for item in updated.metadata.history] == [existing.memory]


def test_repeated_ongoing_observation_is_not_written_or_versioned() -> None:
    existing = make_event(
        "用户正在参加A公司的技术面试。",
        status="ongoing",
    )
    repeated = make_event(
        "用户仍在参加A公司的技术面试。",
        status="ongoing",
    )
    set_decision(repeated, "NONE", target_memory_id=existing.id)
    text_mem = FakeTextMemory([existing])

    result = apply_event_upserts(
        text_mem=text_mem,
        events=[repeated],
        existing_events=[existing],
        user_name="cube-1",
    )

    assert result.memory_ids == []
    assert result.added == 0
    assert result.updated == 0
    assert result.unchanged == 1
    assert text_mem.add_calls == 0
    assert text_mem.nodes[existing.id].metadata.version == 1
    assert text_mem.nodes[existing.id].metadata.history == []


def test_new_event_is_added_without_reusing_an_unrelated_candidate() -> None:
    existing = make_event(
        "用户计划于2026年8月28日10:00参加A公司的技术面试。",
        status="planned",
    )
    incoming = make_event(
        "用户计划于2026年8月28日15:00参加B公司的技术面试。",
        status="planned",
        event_time="2026-08-28T15:00:00+08:00",
    )
    incoming.metadata.info["event_target"] = "B公司"
    set_decision(incoming, "ADD")
    text_mem = FakeTextMemory([existing])

    result = apply_event_upserts(
        text_mem=text_mem,
        events=[incoming],
        existing_events=[existing],
        user_name="cube-1",
    )

    assert result.added == 1
    assert result.updated == 0
    assert set(text_mem.nodes) == {existing.id, incoming.id}


def test_program_does_not_override_model_update_when_event_target_changes() -> None:
    existing = make_event(
        "用户计划于2026年8月28日10:00参加A公司的技术面试。",
        status="planned",
    )
    incoming = make_event(
        "用户计划于2026年8月28日15:00参加B公司的技术面试。",
        status="planned",
        event_time="2026-08-28T15:00:00+08:00",
    )
    incoming.metadata.info["event_target"] = "B公司"
    set_decision(
        incoming,
        "UPDATE",
        target_memory_id=existing.id,
        changed_fields=["event_time", "event_target", "memory"],
    )
    text_mem = FakeTextMemory([existing])

    result = apply_event_upserts(
        text_mem=text_mem,
        events=[incoming],
        existing_events=[existing],
        user_name="cube-1",
    )

    assert result.added == 0
    assert result.updated == 1
    assert list(text_mem.nodes) == [existing.id]
    assert text_mem.nodes[existing.id].memory == incoming.memory
    assert text_mem.nodes[existing.id].metadata.info["event_target"] == "B公司"


def test_program_does_not_override_model_update_when_event_date_changes() -> None:
    existing = make_event(
        "用户计划于2026年8月28日10:00参加A公司的技术面试。",
        status="planned",
    )
    incoming = make_event(
        "用户计划于2026年9月10日15:00参加A公司的另一场技术面试。",
        status="planned",
        event_time="2026-09-10T15:00:00+08:00",
    )
    set_decision(
        incoming,
        "UPDATE",
        target_memory_id=existing.id,
        changed_fields=["event_time", "memory"],
    )
    text_mem = FakeTextMemory([existing])

    result = apply_event_upserts(
        text_mem=text_mem,
        events=[incoming],
        existing_events=[existing],
        user_name="cube-1",
    )

    assert result.added == 0
    assert result.updated == 1
    assert list(text_mem.nodes) == [existing.id]
    assert text_mem.nodes[existing.id].metadata.info["event_time"] == ("2026-09-10T15:00:00+08:00")


def test_program_does_not_override_model_update_when_event_time_changes() -> None:
    existing = make_event(
        "用户计划于2026年8月28日10:00参加A公司的技术面试。",
        status="planned",
    )
    incoming = make_event(
        "用户计划于2026年8月28日15:00参加A公司的另一场技术面试。",
        status="planned",
        event_time="2026-08-28T15:00:00+08:00",
    )
    set_decision(
        incoming,
        "UPDATE",
        target_memory_id=existing.id,
        changed_fields=["event_time", "memory"],
    )
    text_mem = FakeTextMemory([existing])

    result = apply_event_upserts(
        text_mem=text_mem,
        events=[incoming],
        existing_events=[existing],
        user_name="cube-1",
    )

    assert result.added == 0
    assert result.updated == 1
    assert list(text_mem.nodes) == [existing.id]
    assert text_mem.nodes[existing.id].metadata.info["event_time"] == ("2026-08-28T15:00:00+08:00")


def test_program_does_not_override_model_update_when_time_field_changes() -> None:
    existing = make_event(
        "用户计划于2026年8月28日10:00参加A公司的技术面试。",
        status="planned",
    )
    incoming = make_event(
        "用户计划于2026年8月28日15:00参加A公司的另一场技术面试。",
        status="planned",
    )
    incoming.metadata.info.pop("event_time")
    incoming.metadata.info["event_start_time"] = "2026-08-28T15:00:00+08:00"
    set_decision(
        incoming,
        "UPDATE",
        target_memory_id=existing.id,
        changed_fields=["event_start_time", "memory"],
    )
    text_mem = FakeTextMemory([existing])

    result = apply_event_upserts(
        text_mem=text_mem,
        events=[incoming],
        existing_events=[existing],
        user_name="cube-1",
    )

    assert result.added == 0
    assert result.updated == 1
    assert list(text_mem.nodes) == [existing.id]
    assert text_mem.nodes[existing.id].metadata.info["event_start_time"] == (
        "2026-08-28T15:00:00+08:00"
    )


def test_program_does_not_override_model_update_when_event_action_changes() -> None:
    existing = make_event(
        "用户计划于2026年8月28日10:00参加A公司的技术面试。",
        status="planned",
    )
    incoming = make_event(
        "用户计划于2026年8月28日10:00向A公司提交入职材料。",
        status="planned",
    )
    incoming.metadata.info["event_action"] = "提交入职材料"
    set_decision(
        incoming,
        "UPDATE",
        target_memory_id=existing.id,
        changed_fields=["event_action", "memory"],
    )
    text_mem = FakeTextMemory([existing])

    result = apply_event_upserts(
        text_mem=text_mem,
        events=[incoming],
        existing_events=[existing],
        user_name="cube-1",
    )

    assert result.added == 0
    assert result.updated == 1
    assert list(text_mem.nodes) == [existing.id]
    assert text_mem.nodes[existing.id].metadata.info["event_action"] == "提交入职材料"


def test_program_does_not_override_model_update_when_participants_change() -> None:
    existing = make_event(
        "用户计划于2026年8月28日10:00与张三参加A公司的技术面试。",
        status="planned",
    )
    existing.metadata.info["participants"] = ["用户", "张三"]
    incoming = make_event(
        "用户计划于2026年8月28日10:00与李四参加A公司的技术面试。",
        status="planned",
    )
    incoming.metadata.info["participants"] = ["用户", "李四"]
    set_decision(
        incoming,
        "UPDATE",
        target_memory_id=existing.id,
        changed_fields=["participants", "memory"],
    )
    text_mem = FakeTextMemory([existing])

    result = apply_event_upserts(
        text_mem=text_mem,
        events=[incoming],
        existing_events=[existing],
        user_name="cube-1",
    )

    assert result.added == 0
    assert result.updated == 1
    assert list(text_mem.nodes) == [existing.id]
    assert text_mem.nodes[existing.id].metadata.info["participants"] == ["用户", "李四"]


def test_program_does_not_require_programmatic_identity_anchors_for_model_update() -> None:
    existing = make_event(
        "用户计划于2026年8月28日10:00处理一个任务。",
        status="planned",
    )
    existing.metadata.info.update(
        {
            "event_action": "处理",
            "event_target": "任务",
            "participants": ["用户"],
            "participant_keys": ["default"],
        }
    )
    incoming = make_event(
        "用户计划于2026年8月28日10:00处理另一个任务。",
        status="planned",
    )
    incoming.metadata.info.update(
        {
            "event_action": "处理",
            "event_target": "任务",
            "participants": ["用户"],
            "participant_keys": ["default"],
        }
    )
    set_decision(
        incoming,
        "UPDATE",
        target_memory_id=existing.id,
        changed_fields=["memory"],
    )
    text_mem = FakeTextMemory([existing])

    result = apply_event_upserts(
        text_mem=text_mem,
        events=[incoming],
        existing_events=[existing],
        user_name="cube-1",
    )

    assert result.added == 0
    assert result.updated == 1
    assert list(text_mem.nodes) == [existing.id]
    assert text_mem.nodes[existing.id].memory == incoming.memory


def test_program_does_not_override_model_update_for_event_time_timezone_change() -> None:
    existing = make_event(
        "用户计划于2026年8月28日10:00参加A公司的技术面试。",
        status="planned",
        event_time="2026-08-28T10:00:00",
    )
    incoming = make_event(
        "用户计划于2026年8月28日10:00参加A公司的技术面试。",
        status="planned",
        event_time="2026-08-28T10:00:00+08:00",
    )
    set_decision(
        incoming,
        "UPDATE",
        target_memory_id=existing.id,
        changed_fields=["event_time", "memory"],
    )
    text_mem = FakeTextMemory([existing])

    result = apply_event_upserts(
        text_mem=text_mem,
        events=[incoming],
        existing_events=[existing],
        user_name="cube-1",
    )

    assert result.added == 0
    assert result.updated == 1
    assert list(text_mem.nodes) == [existing.id]
    assert text_mem.nodes[existing.id].metadata.info["event_time"] == ("2026-08-28T10:00:00+08:00")


def test_program_uses_model_update_even_when_structured_facts_change() -> None:
    existing = make_event(
        "用户计划于2026年8月28日10:00参加A公司的技术面试。",
        status="planned",
    )
    existing.metadata.info["event_group_id"] = "interview-20260828"
    incoming = make_event(
        "用户计划于2026年8月28日10:00参加B公司的技术面试。",
        status="planned",
    )
    incoming.metadata.info.update(
        {
            "event_group_id": "interview-20260828",
            "event_target": "B公司",
        }
    )
    set_decision(
        incoming,
        "UPDATE",
        target_memory_id=existing.id,
        changed_fields=["event_target", "memory"],
    )
    text_mem = FakeTextMemory([existing])

    result = apply_event_upserts(
        text_mem=text_mem,
        events=[incoming],
        existing_events=[existing],
        user_name="cube-1",
    )

    assert result.added == 0
    assert result.updated == 1
    assert list(text_mem.nodes) == [existing.id]
    assert text_mem.nodes[existing.id].metadata.info["event_target"] == "B公司"


def test_program_trusts_model_update_for_generic_event_descriptions() -> None:
    existing = make_event(
        "用户计划完成一个任务。",
        status="planned",
    )
    existing.metadata.info.update(
        {
            "event_action": "完成",
            "event_target": "任务",
            "event_time": None,
        }
    )
    incoming = make_event(
        "用户开始处理另一个任务。",
        status="ongoing",
    )
    incoming.metadata.info.update(
        {
            "event_action": "完成",
            "event_target": "任务",
            "event_time": None,
        }
    )
    set_decision(
        incoming,
        "UPDATE",
        target_memory_id=existing.id,
        changed_fields=["event_status", "memory"],
    )
    text_mem = FakeTextMemory([existing])

    result = apply_event_upserts(
        text_mem=text_mem,
        events=[incoming],
        existing_events=[existing],
        user_name="cube-1",
    )

    assert result.added == 0
    assert result.updated == 1
    assert list(text_mem.nodes) == [existing.id]
    assert text_mem.nodes[existing.id].metadata.info["event_status"] == "ongoing"


def test_program_uses_model_none_without_rechecking_event_identity() -> None:
    existing = make_event(
        "用户计划于2026年8月28日10:00参加A公司的技术面试。",
        status="planned",
    )
    incoming = make_event(
        "用户计划于2026年8月28日15:00参加B公司的技术面试。",
        status="planned",
        event_time="2026-08-28T15:00:00+08:00",
    )
    incoming.metadata.info["event_target"] = "B公司"
    set_decision(incoming, "NONE", target_memory_id=existing.id)
    text_mem = FakeTextMemory([existing])

    result = apply_event_upserts(
        text_mem=text_mem,
        events=[incoming],
        existing_events=[existing],
        user_name="cube-1",
    )

    assert result.added == 0
    assert result.updated == 0
    assert result.unchanged == 1
    assert list(text_mem.nodes) == [existing.id]


@pytest.mark.parametrize("operation", ["UPDATE", "NONE"])
def test_model_target_must_be_in_the_supplied_candidate_set(operation: str) -> None:
    existing = make_event(
        "用户计划参加A公司的技术面试。",
        status="planned",
    )
    incoming = make_event(
        "用户收到了一条关于面试的新消息。",
        status="uncertain",
    )
    set_decision(incoming, operation, target_memory_id=existing.id)
    text_mem = FakeTextMemory([existing])

    result = apply_event_upserts(
        text_mem=text_mem,
        events=[incoming],
        existing_events=[],
        user_name="cube-1",
    )

    assert result.added == 1
    assert result.updated == 0
    assert result.unchanged == 0
    assert set(text_mem.nodes) == {existing.id, incoming.id}
    trace = text_mem.nodes[incoming.id].metadata.internal_info["event_upsert"]
    assert trace["operation"] == "ADD"
    assert trace["target_memory_id"] is None
    assert "不在本次候选集合" in trace["reason"]


def test_program_does_not_rejudge_model_update_for_unresolved_text() -> None:
    existing = make_event(
        "用户计划完成所属项目未说明的数据采集任务。",
        status="planned",
    )
    incoming = make_event(
        "用户正在完成所属项目未说明的数据采集任务。",
        status="ongoing",
    )
    for event in (existing, incoming):
        event.metadata.info["event_target"] = "所属项目未说明的数据采集任务"
        event.metadata.info["event_action"] = "完成数据采集"
    set_decision(
        incoming,
        "UPDATE",
        target_memory_id=existing.id,
        changed_fields=["event_status", "memory"],
    )
    text_mem = FakeTextMemory([existing])

    result = apply_event_upserts(
        text_mem=text_mem,
        events=[incoming],
        existing_events=[existing],
        user_name="cube-1",
    )

    assert result.added == 0
    assert result.updated == 1
    assert list(text_mem.nodes) == [existing.id]
    assert text_mem.nodes[existing.id].metadata.info["event_status"] == "ongoing"


def test_program_trusts_model_when_newer_evidence_reopens_a_terminal_event() -> None:
    existing = make_event(
        "用户已经完成A公司的技术面试。",
        status="completed",
    )
    incoming = make_event(
        "用户正在参加A公司的技术面试。",
        status="ongoing",
    )
    set_decision(
        incoming,
        "UPDATE",
        target_memory_id=existing.id,
        changed_fields=["event_status", "memory"],
    )
    text_mem = FakeTextMemory([existing])

    result = apply_event_upserts(
        text_mem=text_mem,
        events=[incoming],
        existing_events=[existing],
        user_name="cube-1",
    )

    assert result.added == 0
    assert result.updated == 1
    assert list(text_mem.nodes) == [existing.id]
    assert text_mem.nodes[existing.id].metadata.info["event_status"] == "ongoing"


def test_older_source_evidence_cannot_update_a_newer_event_revision() -> None:
    existing = make_event(
        "用户计划于2026年8月28日10:00参加A公司的技术面试。",
        status="planned",
    )
    existing.metadata.info["source_recorded_at"] = "2026-08-28T09:30:00+08:00"
    incoming = make_event(
        "用户于2026年8月28日10:00开始参加A公司的技术面试。",
        status="ongoing",
    )
    incoming.metadata.info["source_recorded_at"] = "2026-08-28T09:00:00+08:00"
    set_decision(
        incoming,
        "UPDATE",
        target_memory_id=existing.id,
        changed_fields=["event_status", "memory"],
    )
    text_mem = FakeTextMemory([existing])

    result = apply_event_upserts(
        text_mem=text_mem,
        events=[incoming],
        existing_events=[existing],
        user_name="cube-1",
    )

    assert result.added == 1
    assert result.updated == 0
    assert text_mem.nodes[existing.id].metadata.info["event_status"] == "planned"


@pytest.mark.parametrize(
    ("existing_source", "incoming_source"),
    [
        ("2026-08-28T09:30:00+08:00", "2026-08-28T09:00:00"),
        ("2026-08-28T09:30:00", "2026-08-28T09:00:00+08:00"),
        ("2026-08-28T09:30:00+08:00", "2026-08-27"),
    ],
)
def test_older_source_formats_cannot_update_a_newer_event_revision(
    existing_source: str,
    incoming_source: str,
) -> None:
    existing = make_event(
        "用户计划于2026年8月28日10:00参加A公司的技术面试。",
        status="planned",
    )
    existing.metadata.info["source_recorded_at"] = existing_source
    incoming = make_event(
        "用户于2026年8月28日10:00开始参加A公司的技术面试。",
        status="ongoing",
    )
    incoming.metadata.info["source_recorded_at"] = incoming_source
    set_decision(
        incoming,
        "UPDATE",
        target_memory_id=existing.id,
        changed_fields=["event_status", "memory"],
    )
    text_mem = FakeTextMemory([existing])

    result = apply_event_upserts(
        text_mem=text_mem,
        events=[incoming],
        existing_events=[existing],
        user_name="cube-1",
    )

    assert result.added == 1
    assert result.updated == 0
    assert text_mem.nodes[existing.id].metadata.info["event_status"] == "planned"


def test_mixed_timezone_source_times_cannot_overwrite_an_event_revision() -> None:
    existing = make_event(
        "用户正在参加A公司的技术面试。",
        status="ongoing",
        event_time="2026-09-01T13:00:00+08:00",
    )
    existing.metadata.info["source_recorded_at"] = "2026-09-01T05:00:00+00:00"
    incoming = make_event(
        "用户已经完成A公司的技术面试。",
        status="completed",
        event_time="2026-09-01T13:00:00+08:00",
    )
    incoming.metadata.info["source_recorded_at"] = "2026-09-01T10:00:00"
    set_decision(
        incoming,
        "UPDATE",
        target_memory_id=existing.id,
        changed_fields=["event_status", "memory"],
    )
    text_mem = FakeTextMemory([existing])

    result = apply_event_upserts(
        text_mem=text_mem,
        events=[incoming],
        existing_events=[existing],
        user_name="cube-1",
    )

    assert result.updated == 0
    assert result.added == 1
    assert text_mem.nodes[existing.id].metadata.info["event_status"] == "ongoing"


def test_model_decided_due_event_reschedule_updates_same_id() -> None:
    existing = make_event(
        "用户的A公司技术面试已经到期，但结果尚未确认。",
        status="due_unverified",
    )
    postponed = make_event(
        "用户的A公司技术面试已延期至2026年9月10日15:00。",
        status="planned",
        event_time="2026-09-10T15:00:00+08:00",
    )
    set_decision(
        postponed,
        "UPDATE",
        target_memory_id=existing.id,
        changed_fields=["event_status", "event_time", "memory"],
    )
    text_mem = FakeTextMemory([existing])

    result = apply_event_upserts(
        text_mem=text_mem,
        events=[postponed],
        existing_events=[existing],
        user_name="cube-1",
    )

    assert result.updated == 1
    assert result.added == 0
    assert list(text_mem.nodes) == [existing.id]
    assert text_mem.nodes[existing.id].metadata.info["event_status"] == "planned"
    assert text_mem.nodes[existing.id].metadata.info["event_time"] == "2026-09-10T15:00:00+08:00"


def test_program_does_not_override_model_due_event_update() -> None:
    existing = make_event(
        "用户的A公司技术面试已经到期，但结果尚未确认。",
        status="due_unverified",
    )
    unsupported = make_event(
        "用户的A公司技术面试可能延期。",
        status="planned",
    )
    set_decision(
        unsupported,
        "UPDATE",
        target_memory_id=existing.id,
        changed_fields=["event_status", "memory"],
    )
    text_mem = FakeTextMemory([existing])

    result = apply_event_upserts(
        text_mem=text_mem,
        events=[unsupported],
        existing_events=[existing],
        user_name="cube-1",
    )

    assert result.updated == 1
    assert result.added == 0
    assert list(text_mem.nodes) == [existing.id]
    assert text_mem.nodes[existing.id].metadata.info["event_status"] == "planned"


def test_explicit_completion_updates_the_same_event_id() -> None:
    existing = make_event(
        "用户计划于2026年8月28日10:00参加A公司的技术面试。",
        status="planned",
    )
    completed = make_event(
        "用户已于2026年8月28日完成A公司的技术面试。",
        status="completed",
    )
    set_decision(
        completed,
        "UPDATE",
        target_memory_id=existing.id,
        changed_fields=["event_status", "memory"],
    )
    text_mem = FakeTextMemory([existing])

    result = apply_event_upserts(
        text_mem=text_mem,
        events=[completed],
        existing_events=[existing],
        user_name="cube-1",
    )

    assert result.updated == 1
    assert result.added == 0
    assert list(text_mem.nodes) == [existing.id]
    assert text_mem.nodes[existing.id].metadata.info["event_status"] == "completed"


def test_model_decided_cancellation_updates_the_same_event_id() -> None:
    existing = make_event(
        "用户计划在明天下午参加产品经理岗位面试。",
        status="planned",
    )
    cancelled = make_event(
        "HR通知用户，之前安排的那场产品经理岗位面试已经取消。",
        status="cancelled",
    )
    set_decision(
        cancelled,
        "UPDATE",
        target_memory_id=existing.id,
        changed_fields=["event_status", "memory"],
    )
    text_mem = FakeTextMemory([existing])

    result = apply_event_upserts(
        text_mem=text_mem,
        events=[cancelled],
        existing_events=[existing],
        user_name="cube-1",
    )

    assert result.updated == 1
    assert result.added == 0
    assert result.memory_ids == [existing.id]
    assert list(text_mem.nodes) == [existing.id]
    updated = text_mem.nodes[existing.id]
    assert updated.metadata.version == 2
    assert updated.metadata.info["event_status"] == "cancelled"
    assert [item.memory for item in updated.metadata.history] == [existing.memory]


def test_lifecycle_action_wording_does_not_split_the_same_completed_event() -> None:
    existing = make_event(
        "用户正在参加A公司的技术面试。",
        status="ongoing",
    )
    completed = make_event(
        "用户已于2026年8月28日结束A公司的技术面试。",
        status="completed",
    )
    completed.metadata.info["event_action"] = "结束"
    set_decision(
        completed,
        "UPDATE",
        target_memory_id=existing.id,
        changed_fields=["event_status", "event_action", "memory"],
    )
    text_mem = FakeTextMemory([existing])

    result = apply_event_upserts(
        text_mem=text_mem,
        events=[completed],
        existing_events=[existing],
        user_name="cube-1",
    )

    assert result.updated == 1
    assert result.added == 0
    assert list(text_mem.nodes) == [existing.id]
    assert text_mem.nodes[existing.id].metadata.info["event_status"] == "completed"


def test_program_uses_model_none_for_a_valid_candidate() -> None:
    existing = make_event(
        "用户计划于2026年8月28日10:00参加A公司的技术面试。",
        status="planned",
    )
    completed = make_event(
        "用户已于2026年8月28日完成A公司的技术面试。",
        status="completed",
    )
    set_decision(completed, "NONE", target_memory_id=existing.id)
    text_mem = FakeTextMemory([existing])

    result = apply_event_upserts(
        text_mem=text_mem,
        events=[completed],
        existing_events=[existing],
        user_name="cube-1",
    )

    assert result.unchanged == 1
    assert result.updated == 0
    assert result.added == 0
    assert list(text_mem.nodes) == [existing.id]
    assert text_mem.nodes[existing.id].metadata.info["event_status"] == "planned"


def test_exact_event_update_propagates_storage_failures() -> None:
    existing = make_event(
        "用户计划于2026年8月28日10:00参加A公司的技术面试。",
        status="planned",
    )
    incoming = make_event(
        "用户正在参加A公司的技术面试。",
        status="ongoing",
    )
    set_decision(
        incoming,
        "UPDATE",
        target_memory_id=existing.id,
        changed_fields=["event_status", "memory"],
    )
    text_mem = FakeTextMemory([existing])
    text_mem.graph_store = FailingRevisionGraphStore()

    with pytest.raises(MemOSMemoryError, match="persist event revision"):
        apply_event_upserts(
            text_mem=text_mem,
            events=[incoming],
            existing_events=[existing],
            user_name="cube-1",
        )

    assert text_mem.add_calls == 0


def test_exact_event_update_prefers_a_strict_graph_writer() -> None:
    existing = make_event(
        "用户计划于2026年8月28日10:00参加A公司的技术面试。",
        status="planned",
    )
    incoming = make_event(
        "用户正在参加A公司的技术面试。",
        status="ongoing",
    )
    set_decision(
        incoming,
        "UPDATE",
        target_memory_id=existing.id,
        changed_fields=["event_status", "memory"],
    )
    text_mem = FakeTextMemory([existing])
    graph_store = StrictFailingRevisionGraphStore()
    text_mem.graph_store = graph_store

    with pytest.raises(MemOSMemoryError, match="persist event revision"):
        apply_event_upserts(
            text_mem=text_mem,
            events=[incoming],
            existing_events=[existing],
            user_name="cube-1",
        )

    assert graph_store.strict_calls == 1
    assert graph_store.regular_calls == 0
    assert text_mem.add_calls == 0


def test_candidate_retrieval_only_requests_active_long_term_events() -> None:
    existing = make_event("用户计划参加A公司的技术面试。", status="planned")
    incoming = make_event("用户正在参加A公司的技术面试。", status="ongoing")
    text_mem = FakeTextMemory([existing])
    text_mem.graph_store = FakeGraphStore(existing.id)

    candidates = retrieve_existing_event_candidates(
        text_mem,
        [incoming],
        "cube-1",
    )

    assert [candidate.id for candidate in candidates] == [existing.id]
    assert text_mem.graph_store.search_kwargs == {
        "top_k": 5,
        "scope": "LongTermMemory",
        "status": "activated",
        "search_filter": {"record_type": "event"},
        "user_name": "cube-1",
    }
