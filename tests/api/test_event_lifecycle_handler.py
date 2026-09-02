from datetime import datetime
from unittest.mock import Mock

import pytest

from pydantic import ValidationError

from memos.api.handlers.base_handler import HandlerDependencies
from memos.api.handlers.event_lifecycle_handler import EventLifecycleHandler
from memos.api.product_models import EventLifecycleTransitionRequest
from memos.exceptions import MemoryError as MemOSMemoryError
from memos.memories.textual.item import TextualMemoryItem, TreeNodeTextualMemoryMetadata


MEMORY_ID = "26cf4a79-85a9-4faf-a891-3588e4f74472"


class StrictFailingGraphStore:
    def __init__(self) -> None:
        self.regular_calls = 0
        self.strict_calls = 0

    def add_node(self, memory_id, memory, metadata, user_name=None) -> None:
        self.regular_calls += 1

    def add_node_strict(self, memory_id, memory, metadata, user_name=None) -> None:
        self.strict_calls += 1
        raise OSError("qdrant unavailable")


def make_event(*, event_status: str = "planned", version: int = 3) -> TextualMemoryItem:
    return TextualMemoryItem(
        id=MEMORY_ID,
        memory="用户计划于2026年9月1日10:00参加A公司面试。",
        metadata=TreeNodeTextualMemoryMetadata(
            user_id="default",
            status="activated",
            memory_type="LongTermMemory",
            embedding=[0.1],
            version=version,
            info={"record_type": "event", "event_status": event_status},
        ),
    )


def make_request(*, expected_version: int = 3) -> EventLifecycleTransitionRequest:
    return EventLifecycleTransitionRequest(
        user_id="default",
        cube_id="default_cube",
        memory_id=MEMORY_ID,
        expected_version=expected_version,
        to_status="due_unverified",
        observed_at=datetime.fromisoformat("2026-09-01T10:01:00+08:00"),
    )


def test_handler_persists_applied_transition_under_same_id() -> None:
    text_mem = Mock()
    text_mem.graph_store = Mock(spec=["add_node"])
    text_mem.get.return_value = make_event()
    cube = Mock(text_mem=text_mem)
    handler = EventLifecycleHandler(HandlerDependencies(naive_mem_cube=cube))

    response = handler.handle_transition(make_request())

    assert response.data is not None
    assert response.data.outcome == "applied"
    assert response.data.memory_id == MEMORY_ID
    assert response.data.previous_version == 3
    assert response.data.current_version == 4
    persisted = text_mem.graph_store.add_node.call_args
    assert persisted.args[0] == MEMORY_ID
    assert persisted.args[2]["info"]["event_status"] == "due_unverified"
    assert persisted.args[2]["updated_at"] == "2026-09-01T10:01:00+08:00"
    text_mem.get.assert_called_once_with(MEMORY_ID, user_name="default_cube")
    assert persisted.kwargs == {"user_name": "default_cube"}


def test_handler_does_not_report_applied_when_graph_write_fails() -> None:
    text_mem = Mock()
    text_mem.graph_store = Mock(spec=["add_node"])
    text_mem.graph_store.add_node.side_effect = OSError("neo4j unavailable")
    text_mem.get.return_value = make_event()
    cube = Mock(text_mem=text_mem)
    handler = EventLifecycleHandler(HandlerDependencies(naive_mem_cube=cube))

    with pytest.raises(MemOSMemoryError, match="event lifecycle"):
        handler.handle_transition(make_request())


def test_handler_prefers_strict_graph_write_for_lifecycle_transitions() -> None:
    text_mem = Mock()
    graph_store = StrictFailingGraphStore()
    text_mem.graph_store = graph_store
    text_mem.get.return_value = make_event()
    cube = Mock(text_mem=text_mem)
    handler = EventLifecycleHandler(HandlerDependencies(naive_mem_cube=cube))

    with pytest.raises(MemOSMemoryError, match="event lifecycle"):
        handler.handle_transition(make_request())

    assert graph_store.strict_calls == 1
    assert graph_store.regular_calls == 0


def test_handler_returns_conflict_without_writing() -> None:
    text_mem = Mock()
    text_mem.get.return_value = make_event(version=4)
    cube = Mock(text_mem=text_mem)
    handler = EventLifecycleHandler(HandlerDependencies(naive_mem_cube=cube))

    response = handler.handle_transition(make_request(expected_version=3))

    assert response.data is not None
    assert response.data.outcome == "conflict"
    assert response.data.current_version == 4
    text_mem.graph_store.add_node.assert_not_called()


def test_handler_returns_not_found_without_writing() -> None:
    text_mem = Mock()
    text_mem.get.side_effect = ValueError("not found")
    cube = Mock(text_mem=text_mem)
    handler = EventLifecycleHandler(HandlerDependencies(naive_mem_cube=cube))

    response = handler.handle_transition(make_request())

    assert response.data is not None
    assert response.data.outcome == "not_found"
    assert response.data.current_version is None
    text_mem.graph_store.add_node.assert_not_called()


def test_request_rejects_arbitrary_target_status() -> None:
    with pytest.raises(ValidationError):
        EventLifecycleTransitionRequest(
            user_id="default",
            cube_id="default_cube",
            memory_id=MEMORY_ID,
            expected_version=3,
            to_status="completed",
            observed_at="2026-09-01T10:01:00+08:00",
        )
