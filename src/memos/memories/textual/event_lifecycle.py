"""Deterministic lifecycle transitions for event memories."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from memos.memories.textual.item import ArchivedTextualMemory, TextualMemoryItem


EVENT_LIFECYCLE_TRACE_KEY = "event_lifecycle"
TransitionOutcome = Literal["applied", "no_op", "conflict"]


@dataclass(frozen=True)
class EventLifecycleTransitionResult:
    """Result of an exact, version-checked event status transition."""

    outcome: TransitionOutcome
    memory: TextualMemoryItem | None
    previous_status: str | None
    current_status: str | None
    previous_version: int
    current_version: int
    reason: str


def transition_to_due_unverified(
    memory: TextualMemoryItem,
    *,
    expected_version: int,
    observed_at: datetime,
) -> EventLifecycleTransitionResult:
    """Mark an active planned or ongoing event as due without inferring its result."""
    info = dict(memory.metadata.info or {})
    event_status = _optional_text(info.get("event_status"))
    current_version = memory.metadata.version

    if memory.metadata.status != "activated":
        return _unchanged_result(memory, event_status, "memory_not_active")
    if info.get("record_type") != "event":
        return _unchanged_result(memory, event_status, "not_event")
    if event_status == "due_unverified" and expected_version == current_version - 1:
        return _unchanged_result(memory, event_status, "already_due_unverified")
    if current_version != expected_version:
        return EventLifecycleTransitionResult(
            outcome="conflict",
            memory=None,
            previous_status=event_status,
            current_status=event_status,
            previous_version=expected_version,
            current_version=current_version,
            reason="version_conflict",
        )
    if event_status == "due_unverified":
        return _unchanged_result(memory, event_status, "already_due_unverified")
    if event_status in {"completed", "cancelled"}:
        return _unchanged_result(memory, event_status, "event_closed")
    if event_status not in {"planned", "ongoing"}:
        return _unchanged_result(memory, event_status, "transition_not_allowed")

    observed_at_text = observed_at.isoformat()
    updated = memory.model_copy(deep=True)
    updated_info = dict(updated.metadata.info or {})
    updated_info["event_status"] = "due_unverified"
    updated.metadata.info = updated_info
    updated.metadata.version = current_version + 1
    updated.metadata.updated_at = observed_at_text
    updated.metadata.history = [
        *memory.metadata.history,
        ArchivedTextualMemory(
            version=current_version,
            is_fast=bool(memory.metadata.is_fast),
            memory=memory.memory,
            update_type="feedback",
            created_at=memory.metadata.updated_at,
            memory_form="event",
        ),
    ]
    updated.metadata.internal_info = _append_transition_trace(
        memory,
        from_status=event_status,
        observed_at=observed_at_text,
    )
    return EventLifecycleTransitionResult(
        outcome="applied",
        memory=updated,
        previous_status=event_status,
        current_status="due_unverified",
        previous_version=current_version,
        current_version=current_version + 1,
        reason="deadline_reached_without_outcome_evidence",
    )


def _unchanged_result(
    memory: TextualMemoryItem,
    event_status: str | None,
    reason: str,
) -> EventLifecycleTransitionResult:
    return EventLifecycleTransitionResult(
        outcome="no_op",
        memory=None,
        previous_status=event_status,
        current_status=event_status,
        previous_version=memory.metadata.version,
        current_version=memory.metadata.version,
        reason=reason,
    )


def _append_transition_trace(
    memory: TextualMemoryItem,
    *,
    from_status: str,
    observed_at: str,
) -> dict:
    internal_info = dict(memory.metadata.internal_info or {})
    existing_trace = internal_info.get(EVENT_LIFECYCLE_TRACE_KEY)
    transitions = list(existing_trace) if isinstance(existing_trace, list) else []
    transitions.append(
        {
            "from_status": from_status,
            "to_status": "due_unverified",
            "observed_at": observed_at,
            "version": memory.metadata.version + 1,
        }
    )
    internal_info[EVENT_LIFECYCLE_TRACE_KEY] = transitions
    return internal_info


def _optional_text(value: object) -> str | None:
    return str(value) if value is not None else None
