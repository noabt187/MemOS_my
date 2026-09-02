"""Persist model-decided add, update, and no-op operations for event memories."""

import json
import threading

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from memos.exceptions import MemoryError as MemOSMemoryError
from memos.log import get_logger
from memos.memories.textual.item import ArchivedTextualMemory, TextualMemoryItem


logger = get_logger(__name__)

EVENT_UPSERT_TRACE_KEY = "event_upsert"
_EVENT_LOCKS: dict[str, threading.RLock] = {}
_EVENT_LOCKS_GUARD = threading.Lock()
_IGNORED_EQUALITY_INFO_FIELDS = {"source_recorded_at"}


@dataclass(frozen=True)
class EventUpsertResult:
    """Result of persisting a normalized event batch."""

    memories: list[TextualMemoryItem]
    memory_ids: list[str]
    added: int
    updated: int
    unchanged: int


@contextmanager
def event_upsert_lock(scope: str) -> Iterator[None]:
    """Serialize event decisions for one memory cube inside this process."""
    with _EVENT_LOCKS_GUARD:
        lock = _EVENT_LOCKS.setdefault(scope, threading.RLock())
    with lock:
        yield


def retrieve_existing_event_candidates(
    text_mem: Any,
    memories: list[TextualMemoryItem],
    user_name: str,
    *,
    top_k: int = 5,
) -> list[TextualMemoryItem]:
    """Retrieve active event memories that may describe the same real event."""
    graph_store = getattr(text_mem, "graph_store", None)
    embedder = getattr(text_mem, "embedder", None)
    if graph_store is None or embedder is None or not memories:
        return []

    candidate_ids: list[str] = []
    try:
        for memory in memories:
            embedding = getattr(memory.metadata, "embedding", None)
            if not embedding:
                embedding = embedder.embed([memory.memory])[0]
            hits = graph_store.search_by_embedding(
                embedding,
                top_k=top_k,
                scope="LongTermMemory",
                status="activated",
                search_filter={"record_type": "event"},
                user_name=user_name,
            )
            candidate_ids.extend(
                str(hit["id"]) for hit in hits if isinstance(hit, dict) and hit.get("id")
            )
    except Exception:
        logger.exception("Failed to retrieve existing event candidates")
        return []

    candidates: list[TextualMemoryItem] = []
    for memory_id in dict.fromkeys(candidate_ids):
        try:
            candidate = text_mem.get(memory_id, user_name=user_name)
        except (KeyError, ValueError):
            continue
        info = candidate.metadata.info or {}
        if candidate.metadata.status == "activated" and info.get("record_type") == "event":
            candidates.append(candidate)
    return candidates


def apply_event_upserts(
    text_mem: Any,
    events: list[TextualMemoryItem],
    existing_events: list[TextualMemoryItem],
    user_name: str,
) -> EventUpsertResult:
    """Apply model-decided ADD, UPDATE, and NONE operations in one write batch.

    Event identity is a semantic decision made by the normalization model. This
    layer only verifies that an UPDATE/NONE target came from the supplied
    candidate set and that older source evidence cannot overwrite newer state.
    """
    existing_by_id = {memory.id: memory for memory in existing_events}
    to_write: list[TextualMemoryItem] = []
    additions: list[TextualMemoryItem] = []
    updates: list[TextualMemoryItem] = []
    added = 0
    updated = 0
    unchanged = 0

    for event in events:
        decision = _upsert_decision(event)
        operation = decision["operation"]
        target_memory_id = decision["target_memory_id"]

        if operation in {"UPDATE", "NONE"} and target_memory_id:
            existing = existing_by_id.get(target_memory_id)
            if existing is None:
                logger.warning(
                    "Event update target %s was not supplied as a candidate; "
                    "storing as a new event",
                    target_memory_id,
                )
                decision = _rejected_target_decision(decision)
                operation = "ADD"
                target_memory_id = None
            elif operation == "UPDATE" and _incoming_source_is_older(
                existing.metadata.info or {},
                event.metadata.info or {},
            ):
                decision = _rejected_stale_update_decision(decision)
                operation = "ADD"
                target_memory_id = None
            elif operation == "NONE":
                unchanged += 1
                continue
            else:
                updated_event = _updated_event(existing, event, decision)
                if _same_event_state(existing, updated_event):
                    unchanged += 1
                    continue
                to_write.append(updated_event)
                updates.append(updated_event)
                existing_by_id[updated_event.id] = updated_event
                updated += 1
                continue
        elif operation in {"UPDATE", "NONE"}:
            decision = _rejected_target_decision(decision)
            operation = "ADD"

        event.metadata.status = "activated"
        event.metadata.internal_info = dict(event.metadata.internal_info or {})
        event.metadata.internal_info[EVENT_UPSERT_TRACE_KEY] = {
            **decision,
            "operation": "ADD",
            "target_memory_id": None,
        }
        to_write.append(event)
        additions.append(event)
        existing_by_id[event.id] = event
        added += 1

    updated_ids = _persist_exact_event_revisions(text_mem, updates, user_name=user_name)
    added_ids = text_mem.add(additions, user_name=user_name) if additions else []
    persisted_ids = set(updated_ids) | set(added_ids)
    memory_ids = [memory.id for memory in to_write if memory.id in persisted_ids]
    return EventUpsertResult(
        memories=to_write,
        memory_ids=memory_ids,
        added=added,
        updated=updated,
        unchanged=unchanged,
    )


def _upsert_decision(event: TextualMemoryItem) -> dict[str, Any]:
    internal_info = event.metadata.internal_info or {}
    raw = internal_info.get(EVENT_UPSERT_TRACE_KEY)
    decision = raw if isinstance(raw, dict) else {}
    operation = str(decision.get("operation") or "ADD").upper()
    if operation not in {"ADD", "UPDATE", "NONE"}:
        operation = "ADD"
    target_memory_id = str(decision.get("target_memory_id") or "").strip() or None
    changed_fields = decision.get("changed_fields")
    if not isinstance(changed_fields, list):
        changed_fields = []
    return {
        "operation": operation,
        "target_memory_id": target_memory_id,
        "changed_fields": list(dict.fromkeys(str(field) for field in changed_fields if field)),
        "reason": str(decision.get("reason") or "").strip(),
    }


def _rejected_target_decision(decision: dict[str, Any]) -> dict[str, Any]:
    reason = str(decision.get("reason") or "").strip()
    rejection = "模型指定的旧事件不在本次候选集合中，按独立事件新增"
    return {
        **decision,
        "operation": "ADD",
        "target_memory_id": None,
        "reason": f"{reason}；{rejection}" if reason else rejection,
    }


def _rejected_stale_update_decision(decision: dict[str, Any]) -> dict[str, Any]:
    reason = str(decision.get("reason") or "").strip()
    rejection = "本次来源时间早于旧事件已有证据，不能覆盖较新的事件版本"
    return {
        **decision,
        "operation": "ADD",
        "target_memory_id": None,
        "reason": f"{reason}；{rejection}" if reason else rejection,
    }


def _incoming_source_is_older(
    existing_info: dict[str, Any],
    incoming_info: dict[str, Any],
) -> bool:
    existing_time = _parse_source_time(existing_info.get("source_recorded_at"))
    incoming_time = _parse_source_time(incoming_info.get("source_recorded_at"))
    if existing_time is None or incoming_time is None:
        return False
    if _datetime_is_aware(existing_time) != _datetime_is_aware(incoming_time):
        return True
    existing_time, incoming_time = _make_datetimes_comparable(existing_time, incoming_time)
    return incoming_time < existing_time


def _parse_source_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _make_datetimes_comparable(left: datetime, right: datetime) -> tuple[datetime, datetime]:
    if left.tzinfo is None and right.tzinfo is not None:
        left = left.replace(tzinfo=right.tzinfo)
    elif left.tzinfo is not None and right.tzinfo is None:
        right = right.replace(tzinfo=left.tzinfo)
    return left, right


def _datetime_is_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _updated_event(
    existing: TextualMemoryItem,
    incoming: TextualMemoryItem,
    decision: dict[str, Any],
) -> TextualMemoryItem:
    updated = existing.model_copy(deep=True)
    updated.memory = incoming.memory
    updated.metadata.key = incoming.metadata.key or existing.metadata.key
    updated.metadata.tags = _deduplicate_strings(
        [*(existing.metadata.tags or []), *(incoming.metadata.tags or [])]
    )
    updated.metadata.sources = _merge_sources(existing, incoming)
    updated.metadata.background = incoming.metadata.background or existing.metadata.background
    updated.metadata.embedding = incoming.metadata.embedding or existing.metadata.embedding
    updated.metadata.info = _merge_event_info(existing, incoming, decision["changed_fields"])
    updated.metadata.status = "activated"
    updated.metadata.is_fast = False
    updated.metadata.version = existing.metadata.version + 1
    updated.metadata.updated_at = datetime.now().astimezone().isoformat()
    updated.metadata.history = [
        *existing.metadata.history,
        ArchivedTextualMemory(
            version=existing.metadata.version,
            is_fast=bool(existing.metadata.is_fast),
            memory=existing.memory,
            update_type="extract",
            created_at=existing.metadata.updated_at,
            memory_form="event",
        ),
    ]
    updated.metadata.internal_info = {
        **(existing.metadata.internal_info or {}),
        **(incoming.metadata.internal_info or {}),
        EVENT_UPSERT_TRACE_KEY: decision,
    }
    return updated


def _merge_event_info(
    existing: TextualMemoryItem,
    incoming: TextualMemoryItem,
    changed_fields: list[str],
) -> dict[str, Any]:
    existing_info = dict(existing.metadata.info or {})
    incoming_info = dict(incoming.metadata.info or {})
    merged = dict(existing_info)
    for field, value in incoming_info.items():
        if value not in (None, "", [], {}) or field in changed_fields:
            merged[field] = value
    return merged


def _merge_sources(
    existing: TextualMemoryItem,
    incoming: TextualMemoryItem,
) -> list[Any]:
    merged: list[Any] = []
    seen: set[str] = set()
    for source in [*(existing.metadata.sources or []), *(incoming.metadata.sources or [])]:
        payload = source.model_dump(exclude_none=True) if hasattr(source, "model_dump") else source
        identity = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        if identity in seen:
            continue
        seen.add(identity)
        merged.append(source.model_copy(deep=True) if hasattr(source, "model_copy") else source)
    return merged


def _same_event_state(existing: TextualMemoryItem, updated: TextualMemoryItem) -> bool:
    existing_info = {
        key: value
        for key, value in (existing.metadata.info or {}).items()
        if key not in _IGNORED_EQUALITY_INFO_FIELDS
    }
    updated_info = {
        key: value
        for key, value in (updated.metadata.info or {}).items()
        if key not in _IGNORED_EQUALITY_INFO_FIELDS
    }
    return (
        _normalized_text(existing.memory) == _normalized_text(updated.memory)
        and existing_info == updated_info
    )


def _persist_exact_event_revisions(
    text_mem: Any,
    memories: list[TextualMemoryItem],
    *,
    user_name: str,
) -> list[str]:
    if not memories:
        return []
    graph_store = getattr(text_mem, "graph_store", None)
    strict_add_node = getattr(graph_store, "add_node_strict", None)
    add_node = (
        strict_add_node if callable(strict_add_node) else getattr(graph_store, "add_node", None)
    )
    if not callable(add_node):
        return text_mem.add(memories, user_name=user_name)

    persisted_ids: list[str] = []
    for memory in memories:
        try:
            add_node(
                memory.id,
                memory.memory,
                memory.metadata.model_dump(exclude_none=True),
                user_name=user_name,
            )
        except Exception as exc:
            raise MemOSMemoryError("Unable to persist event revision") from exc
        persisted_ids.append(memory.id)
    return persisted_ids


def _normalized_text(value: str) -> str:
    return "".join(value.casefold().split())


def _deduplicate_strings(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
