"""Handler for exact event lifecycle transitions."""

from memos.api.handlers.base_handler import BaseHandler
from memos.api.product_models import (
    EventLifecycleTransitionData,
    EventLifecycleTransitionRequest,
    EventLifecycleTransitionResponse,
)
from memos.exceptions import MemoryError as MemOSMemoryError
from memos.memories.textual.event_lifecycle import transition_to_due_unverified
from memos.memories.textual.event_upsert import event_upsert_lock
from memos.memories.textual.item import TextualMemoryItem


class EventLifecycleHandler(BaseHandler):
    """Apply deterministic lifecycle updates without semantic retrieval or an LLM."""

    def handle_transition(
        self,
        request: EventLifecycleTransitionRequest,
    ) -> EventLifecycleTransitionResponse:
        """Apply an allowed transition to one exact memory version."""
        self._validate_dependencies("naive_mem_cube")
        text_mem = self.naive_mem_cube.text_mem

        with event_upsert_lock(request.cube_id):
            try:
                memory = text_mem.get(request.memory_id, user_name=request.cube_id)
            except (KeyError, ValueError):
                return _not_found_response(request.memory_id)

            if memory.metadata.user_id not in {None, request.user_id}:
                return _not_found_response(request.memory_id)

            result = transition_to_due_unverified(
                memory,
                expected_version=request.expected_version,
                observed_at=request.observed_at,
            )
            if result.memory is not None:
                _persist_transition(text_mem, result.memory, user_name=request.cube_id)

        return EventLifecycleTransitionResponse(
            data=EventLifecycleTransitionData(
                outcome=result.outcome,
                memory_id=request.memory_id,
                previous_status=result.previous_status,
                current_status=result.current_status,
                previous_version=result.previous_version,
                current_version=result.current_version,
                reason=result.reason,
            )
        )


def _not_found_response(memory_id: str) -> EventLifecycleTransitionResponse:
    return EventLifecycleTransitionResponse(
        data=EventLifecycleTransitionData(
            outcome="not_found",
            memory_id=memory_id,
            reason="memory_not_found",
        )
    )


def _persist_transition(text_mem, memory: TextualMemoryItem, *, user_name: str) -> None:
    """Write one exact revision without the general add path rewriting its timestamp."""
    graph_store = getattr(text_mem, "graph_store", None)
    strict_add_node = getattr(graph_store, "add_node_strict", None)
    add_node = (
        strict_add_node if callable(strict_add_node) else getattr(graph_store, "add_node", None)
    )
    if not callable(add_node):
        raise MemOSMemoryError("Unable to persist event lifecycle transition")
    try:
        add_node(
            memory.id,
            memory.memory,
            memory.metadata.model_dump(exclude_none=True),
            user_name=user_name,
        )
    except Exception as exc:
        raise MemOSMemoryError("Unable to persist event lifecycle transition") from exc
