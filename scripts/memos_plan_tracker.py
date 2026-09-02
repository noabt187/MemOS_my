#!/usr/bin/env python3
"""Deterministic lifecycle tracking for scheduled MemOS events.

The tracker keeps only a rebuildable scheduling index. Event facts remain in
MemOS, and time-based checks may only mark an exact event as due-unverified.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time as time_module

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Protocol
from uuid import uuid4

from memos.log import get_logger


logger = get_logger(__name__)
SCHEMA_VERSION = 1
TRACKING_STATES = {"scheduled", "waiting_evidence", "unscheduled", "error"}
TERMINAL_EVENT_STATUSES = {"completed", "cancelled"}
TRACKER_ENTRY_FIELDS = (
    "memory_id",
    "user_id",
    "cube_id",
    "last_seen_version",
    "check_at",
    "next_check_at",
    "last_checked_at",
    "tracking_state",
    "failure_count",
    "topic_sync_pending",
)
_DATE_ONLY_PATTERN = re.compile(r"^(\d{4})(?:-|/|\.|年)(\d{1,2})(?:-|/|\.|月)(\d{1,2})日?$")


class PlanTrackerClient(Protocol):
    def list_text_memories(self, *, user_id: str, cube_id: str) -> list[dict[str, Any]]: ...

    def get_memory(self, memory_id: str) -> Any: ...

    def transition_event_lifecycle(
        self,
        *,
        user_id: str,
        cube_id: str,
        memory_id: str,
        expected_version: int,
        to_status: str,
        observed_at: str,
    ) -> Any: ...


TopicRefresher = Callable[[str, str, str], Any]
TopicSyncChecker = Callable[[str, str, str, dict[str, Any] | None], bool]
TrackerScope = tuple[str, str]
TrackerRegistration = tuple[str, str, str, bool]


@dataclass(frozen=True)
class TrackingDecision:
    tracking_state: str
    check_at: datetime | None


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _memory_metadata(memory: dict[str, Any]) -> dict[str, Any]:
    return _mapping(memory.get("metadata"))


def _memory_info(memory: dict[str, Any]) -> dict[str, Any]:
    value = _memory_metadata(memory).get("info")
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _memory_id(memory: dict[str, Any]) -> str:
    return str(memory.get("id") or memory.get("memory_id") or "").strip()


def _memory_version(memory: dict[str, Any]) -> int:
    value = _memory_metadata(memory).get("version", 1)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 1


def _parse_absolute_time(value: Any, timezone_info: tzinfo) -> datetime | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    date_match = _DATE_ONLY_PATTERN.fullmatch(normalized)
    if date_match is not None:
        try:
            date_value = date(*(int(part) for part in date_match.groups()))
        except ValueError:
            return None
        return datetime.combine(date_value, time.max, tzinfo=timezone_info)

    iso_value = normalized[:-1] + "+00:00" if normalized.endswith("Z") else normalized
    try:
        parsed = datetime.fromisoformat(iso_value)
    except ValueError:
        return None
    if parsed.year < 1000:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone_info)


def tracking_decision(
    memory: dict[str, Any],
    *,
    timezone_info: tzinfo,
    was_tracked: bool = False,
) -> TrackingDecision | None:
    """Return deterministic scheduling state for one current memory."""
    metadata = _memory_metadata(memory)
    info = _memory_info(memory)
    if str(metadata.get("status") or "").strip().lower() != "activated":
        return None
    if str(info.get("record_type") or "").strip().lower() != "event":
        return None

    event_status = str(info.get("event_status") or "uncertain").strip().lower()
    if event_status in TERMINAL_EVENT_STATUSES or event_status == "uncertain":
        return None
    if event_status == "due_unverified":
        return TrackingDecision("waiting_evidence", None)

    if event_status == "planned":
        for field_name in ("event_end_time", "event_time", "event_start_time"):
            check_at = _parse_absolute_time(info.get(field_name), timezone_info)
            if check_at is not None:
                return TrackingDecision("scheduled", check_at)
        return TrackingDecision("unscheduled", None)

    if event_status == "ongoing":
        check_at = _parse_absolute_time(info.get("event_end_time"), timezone_info)
        if check_at is not None:
            return TrackingDecision("scheduled", check_at)
        if was_tracked:
            return TrackingDecision("waiting_evidence", None)
    return None


def tracker_entry(
    memory: dict[str, Any],
    *,
    user_id: str,
    cube_id: str,
    timezone_info: tzinfo,
    was_tracked: bool = False,
) -> dict[str, Any]:
    """Build the minimal persisted index entry for one eligible event."""
    memory_id = _memory_id(memory)
    decision = tracking_decision(
        memory,
        timezone_info=timezone_info,
        was_tracked=was_tracked,
    )
    if not memory_id or decision is None:
        raise ValueError("记忆不属于计划追踪范围。")
    check_at = decision.check_at.isoformat() if decision.check_at is not None else None
    return {
        "memory_id": memory_id,
        "user_id": user_id,
        "cube_id": cube_id,
        "last_seen_version": _memory_version(memory),
        "check_at": check_at,
        "next_check_at": check_at,
        "last_checked_at": None,
        "tracking_state": decision.tracking_state,
        "failure_count": 0,
        "topic_sync_pending": False,
    }


def _topic_sync_only_entry(
    memory: dict[str, Any],
    *,
    user_id: str,
    cube_id: str,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _pending_topic_sync_entry(
        _memory_id(memory),
        user_id=user_id,
        cube_id=cube_id,
        last_seen_version=_memory_version(memory),
        existing=existing,
    )


def _pending_topic_sync_entry(
    memory_id: str,
    *,
    user_id: str,
    cube_id: str,
    last_seen_version: int | None = None,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_version = last_seen_version
    if resolved_version is None:
        resolved_version = int(existing.get("last_seen_version") or 0) if existing else 0
    return {
        "memory_id": memory_id,
        "user_id": user_id,
        "cube_id": cube_id,
        "last_seen_version": resolved_version,
        "check_at": None,
        "next_check_at": None,
        "last_checked_at": existing.get("last_checked_at") if existing else None,
        "tracking_state": "error",
        "failure_count": int(existing.get("failure_count") or 0) if existing else 0,
        "topic_sync_pending": True,
    }


def _is_topic_sync_only(entry: dict[str, Any] | None) -> bool:
    return bool(
        entry
        and entry.get("topic_sync_pending")
        and entry.get("tracking_state") == "error"
        and entry.get("check_at") is None
        and entry.get("next_check_at") is None
    )


class PlanTrackerStore:
    """Thread-safe, atomically persisted scheduling index."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self._lock = threading.RLock()
        self._state = self._load()

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "events": {}}

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return self._empty_state()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Plan tracker index is unreadable and will be rebuilt: %s", exc)
            return self._empty_state()
        if (
            not isinstance(raw, dict)
            or raw.get("schema_version") != SCHEMA_VERSION
            or not isinstance(raw.get("events"), dict)
        ):
            logger.warning("Plan tracker index has an invalid structure and will be rebuilt")
            return self._empty_state()
        events: dict[str, dict[str, Any]] = {}
        for key, value in raw["events"].items():
            if not isinstance(value, dict):
                continue
            try:
                entry = self._sanitize_entry(dict(value, memory_id=value.get("memory_id") or key))
            except ValueError:
                continue
            events[entry["memory_id"]] = entry
        return {"schema_version": SCHEMA_VERSION, "events": events}

    @staticmethod
    def _sanitize_entry(entry: dict[str, Any]) -> dict[str, Any]:
        memory_id = str(entry.get("memory_id") or "").strip()
        tracking_state = str(entry.get("tracking_state") or "")
        if not memory_id or tracking_state not in TRACKING_STATES:
            raise ValueError("计划追踪索引项不合法。")
        sanitized = {
            field_name: deepcopy(entry.get(field_name)) for field_name in TRACKER_ENTRY_FIELDS
        }
        sanitized["memory_id"] = memory_id
        sanitized["tracking_state"] = tracking_state
        return sanitized

    def _write_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        try:
            with temporary_path.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(self._state, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._state)

    def get(self, memory_id: str) -> dict[str, Any] | None:
        with self._lock:
            entry = self._state["events"].get(memory_id)
            return deepcopy(entry) if isinstance(entry, dict) else None

    def upsert(self, entry: dict[str, Any]) -> None:
        sanitized = self._sanitize_entry(entry)
        with self._lock:
            self._state["events"][sanitized["memory_id"]] = sanitized
            self._write_locked()

    def remove(self, memory_id: str) -> None:
        with self._lock:
            if self._state["events"].pop(memory_id, None) is not None:
                self._write_locked()

    def replace_scope(
        self,
        *,
        user_id: str,
        cube_id: str,
        entries: dict[str, dict[str, Any]],
    ) -> None:
        with self._lock:
            other_scopes = {
                memory_id: entry
                for memory_id, entry in self._state["events"].items()
                if entry.get("user_id") != user_id or entry.get("cube_id") != cube_id
            }
            other_scopes.update(
                {memory_id: self._sanitize_entry(entry) for memory_id, entry in entries.items()}
            )
            self._state = {"schema_version": SCHEMA_VERSION, "events": other_scopes}
            self._write_locked()


class PlanTracker:
    """Reconcile event schedules and perform narrow due-status transitions."""

    def __init__(
        self,
        *,
        store: PlanTrackerStore,
        client: PlanTrackerClient,
        user_id: str,
        cube_id: str,
        timezone_info: tzinfo | None = None,
        topic_refresher: TopicRefresher | None = None,
        topic_sync_checker: TopicSyncChecker | None = None,
        retry_base_seconds: int = 60,
        retry_max_seconds: int = 3600,
    ) -> None:
        self.store = store
        self.client = client
        self.user_id = user_id
        self.cube_id = cube_id
        self.timezone_info = timezone_info or datetime.now().astimezone().tzinfo
        if self.timezone_info is None:
            raise ValueError("无法确定计划追踪器时区。")
        self.topic_refresher = topic_refresher
        self.topic_sync_checker = topic_sync_checker
        self.retry_base_seconds = max(1, retry_base_seconds)
        self.retry_max_seconds = max(self.retry_base_seconds, retry_max_seconds)
        self._operation_lock = threading.RLock()

    def for_scope(self, *, user_id: str, cube_id: str) -> PlanTracker:
        """Create a tracker for another application scope over the same backend."""
        if user_id == self.user_id and cube_id == self.cube_id:
            return self
        return PlanTracker(
            store=self.store,
            client=self.client,
            user_id=user_id,
            cube_id=cube_id,
            timezone_info=self.timezone_info,
            topic_refresher=self.topic_refresher,
            topic_sync_checker=self.topic_sync_checker,
            retry_base_seconds=self.retry_base_seconds,
            retry_max_seconds=self.retry_max_seconds,
        )

    @staticmethod
    def _now() -> datetime:
        return datetime.now().astimezone()

    @staticmethod
    def _unwrap_memory(response: Any) -> dict[str, Any] | None:
        if not isinstance(response, dict):
            return None
        data = response.get("data", response)
        if isinstance(data, dict):
            return data if _memory_id(data) else None
        if isinstance(data, list):
            return next(
                (item for item in data if isinstance(item, dict) and _memory_id(item)),
                None,
            )
        return None

    @staticmethod
    def _response_outcome(response: Any) -> str:
        if not isinstance(response, dict):
            return ""
        data = response.get("data")
        return str(data.get("outcome") or "") if isinstance(data, dict) else ""

    def _entry_from_memory(
        self,
        memory: dict[str, Any],
        *,
        existing: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        was_tracked = existing is not None and not _is_topic_sync_only(existing)
        decision = tracking_decision(
            memory,
            timezone_info=self.timezone_info,
            was_tracked=was_tracked,
        )
        memory_id = _memory_id(memory)
        if not memory_id or decision is None:
            return None
        entry = tracker_entry(
            memory,
            user_id=self.user_id,
            cube_id=self.cube_id,
            timezone_info=self.timezone_info,
            was_tracked=was_tracked,
        )
        if existing is not None:
            entry["last_checked_at"] = existing.get("last_checked_at")
            entry["topic_sync_pending"] = bool(existing.get("topic_sync_pending"))
            if (
                was_tracked
                and int(existing.get("last_seen_version") or 1) == entry["last_seen_version"]
                and existing.get("tracking_state") == "error"
            ):
                entry["tracking_state"] = "error"
                entry["failure_count"] = int(existing.get("failure_count") or 0)
                entry["next_check_at"] = existing.get("next_check_at")
        return entry

    def reconcile(self, *, now: datetime | None = None) -> None:
        """Rebuild this scope from current MemOS events, then process overdue items."""
        with self._operation_lock:
            current = self.store.snapshot()["events"]
            scope_entries = {
                memory_id: entry
                for memory_id, entry in current.items()
                if entry.get("user_id") == self.user_id and entry.get("cube_id") == self.cube_id
            }
            memories = self.client.list_text_memories(user_id=self.user_id, cube_id=self.cube_id)
            rebuilt: dict[str, dict[str, Any]] = {}
            returned_ids: set[str] = set()
            for memory in memories:
                memory_id = _memory_id(memory)
                if not memory_id:
                    continue
                returned_ids.add(memory_id)
                existing = scope_entries.get(memory_id)
                entry = self._entry_from_memory(memory, existing=existing)
                needs_topic_sync = _memory_info(memory).get(
                    "record_type"
                ) == "event" and self._topic_needs_sync(memory_id, memory)
                if entry is None and (
                    needs_topic_sync or (existing and existing.get("topic_sync_pending"))
                ):
                    entry = _topic_sync_only_entry(
                        memory,
                        user_id=self.user_id,
                        cube_id=self.cube_id,
                        existing=existing,
                    )
                elif entry is not None and needs_topic_sync:
                    entry["topic_sync_pending"] = True
                if entry is not None:
                    rebuilt[memory_id] = entry
            for memory_id, existing in scope_entries.items():
                if memory_id in returned_ids or memory_id in rebuilt:
                    continue
                needs_topic_sync = self._topic_needs_sync(memory_id, None)
                if existing.get("topic_sync_pending") or needs_topic_sync:
                    rebuilt[memory_id] = _pending_topic_sync_entry(
                        memory_id,
                        user_id=self.user_id,
                        cube_id=self.cube_id,
                        existing=existing,
                    )
            self.store.replace_scope(user_id=self.user_id, cube_id=self.cube_id, entries=rebuilt)
            self._retry_topic_sync()
            self.check_due(now=now)

    def _topic_needs_sync(
        self,
        memory_id: str,
        memory: dict[str, Any] | None,
    ) -> bool:
        if self.topic_sync_checker is None:
            return False
        try:
            return bool(
                self.topic_sync_checker(
                    self.user_id,
                    self.cube_id,
                    memory_id,
                    memory,
                )
            )
        except Exception as exc:  # noqa: BLE001 - callback failures must not stop reconciliation
            logger.warning("Failed to inspect Topic revision for %s: %s", memory_id, exc)
            return True

    def refresh_memory_ids(
        self,
        memory_ids: list[str],
        *,
        now: datetime | None = None,
        topic_sync_pending: bool = False,
    ) -> None:
        """Incrementally refresh exact memories produced by a successful ingestion."""
        with self._operation_lock:
            unique_ids = dict.fromkeys(item.strip() for item in memory_ids if item.strip())
            has_pending_topic_sync = False
            for memory_id in unique_ids:
                existing = self.store.get(memory_id)
                try:
                    memory = self._unwrap_memory(self.client.get_memory(memory_id))
                except Exception as exc:  # noqa: BLE001 - one bad record must not stop the worker
                    logger.warning("Failed to refresh tracked event %s: %s", memory_id, exc)
                    if topic_sync_pending or (existing and existing.get("topic_sync_pending")):
                        self.store.upsert(
                            _pending_topic_sync_entry(
                                memory_id,
                                user_id=self.user_id,
                                cube_id=self.cube_id,
                                existing=existing,
                            )
                        )
                        has_pending_topic_sync = True
                    continue
                if memory is None:
                    if topic_sync_pending or (existing and existing.get("topic_sync_pending")):
                        self.store.upsert(
                            _pending_topic_sync_entry(
                                memory_id,
                                user_id=self.user_id,
                                cube_id=self.cube_id,
                                existing=existing,
                            )
                        )
                        has_pending_topic_sync = True
                    else:
                        self.store.remove(memory_id)
                    continue
                entry = self._entry_from_memory(memory, existing=existing)
                if entry is None:
                    if topic_sync_pending or (existing and existing.get("topic_sync_pending")):
                        entry = _topic_sync_only_entry(
                            memory,
                            user_id=self.user_id,
                            cube_id=self.cube_id,
                            existing=existing,
                        )
                        self.store.upsert(entry)
                        has_pending_topic_sync = True
                    else:
                        self.store.remove(memory_id)
                else:
                    if topic_sync_pending:
                        entry["topic_sync_pending"] = True
                    self.store.upsert(entry)
                    has_pending_topic_sync = has_pending_topic_sync or bool(
                        entry.get("topic_sync_pending")
                    )
            if has_pending_topic_sync:
                self._retry_topic_sync()

    def persist_ingestion_registration(
        self,
        memory_ids: list[str],
        *,
        topic_sync_pending: bool = False,
    ) -> None:
        """Persist ingestion-side retry state without racing a scope reconcile."""
        if not topic_sync_pending:
            return
        with self._operation_lock:
            for memory_id in dict.fromkeys(item.strip() for item in memory_ids if item.strip()):
                existing = self.store.get(memory_id)
                if existing is not None:
                    pending_entry = dict(existing, topic_sync_pending=True)
                else:
                    pending_entry = _pending_topic_sync_entry(
                        memory_id,
                        user_id=self.user_id,
                        cube_id=self.cube_id,
                    )
                self.store.upsert(pending_entry)

    def _mark_error(self, entry: dict[str, Any], *, now: datetime, error: Exception) -> None:
        failures = int(entry.get("failure_count") or 0) + 1
        retry_exponent = min(failures - 1, 16)
        delay = min(self.retry_max_seconds, self.retry_base_seconds * (2**retry_exponent))
        failed_entry = dict(entry)
        failed_entry.update(
            {
                "tracking_state": "error",
                "failure_count": failures,
                "last_checked_at": now.isoformat(),
                "next_check_at": (now + timedelta(seconds=delay)).isoformat(),
            }
        )
        self.store.upsert(failed_entry)
        logger.warning("Plan tracker check failed for %s: %s", entry["memory_id"], error)

    def _sync_topic(self, entry: dict[str, Any]) -> None:
        pending_entry = dict(entry, topic_sync_pending=True)
        self.store.upsert(pending_entry)
        if self.topic_refresher is None:
            return
        try:
            self.topic_refresher(
                str(entry["user_id"]),
                str(entry["cube_id"]),
                str(entry["memory_id"]),
            )
        except Exception as exc:  # noqa: BLE001 - Topic is retried from durable state
            logger.warning("Topic refresh is pending for %s: %s", entry["memory_id"], exc)
            return
        if _is_topic_sync_only(pending_entry):
            self.store.remove(str(pending_entry["memory_id"]))
        else:
            self.store.upsert(dict(pending_entry, topic_sync_pending=False))

    def _refresh_after_race(self, entry: dict[str, Any]) -> None:
        memory_id = str(entry["memory_id"])
        try:
            memory = self._unwrap_memory(self.client.get_memory(memory_id))
        except (OSError, RuntimeError, ValueError) as exc:
            self._mark_error(entry, now=self._now(), error=exc)
            return
        if memory is None:
            self.store.remove(memory_id)
            return
        self._persist_refreshed_memory(entry, memory)

    def _persist_refreshed_memory(
        self,
        entry: dict[str, Any],
        memory: dict[str, Any],
    ) -> None:
        memory_id = str(entry["memory_id"])
        refreshed = self._entry_from_memory(memory, existing=entry)
        if refreshed is None:
            if entry.get("topic_sync_pending"):
                pending = _topic_sync_only_entry(
                    memory,
                    user_id=str(entry["user_id"]),
                    cube_id=str(entry["cube_id"]),
                    existing=entry,
                )
                self.store.upsert(pending)
                self._sync_topic(pending)
            else:
                self.store.remove(memory_id)
        else:
            self.store.upsert(refreshed)
            if refreshed.get("topic_sync_pending"):
                self._sync_topic(refreshed)

    def _process_due_entry(self, entry: dict[str, Any], *, now: datetime) -> None:
        memory_id = str(entry["memory_id"])
        try:
            memory = self._unwrap_memory(self.client.get_memory(memory_id))
        except (OSError, RuntimeError, ValueError) as exc:
            self._mark_error(entry, now=now, error=exc)
            return
        if memory is None:
            self.store.remove(memory_id)
            return

        if _memory_version(memory) != int(entry.get("last_seen_version") or 1):
            self._persist_refreshed_memory(entry, memory)
            return

        decision = tracking_decision(
            memory,
            timezone_info=self.timezone_info,
            was_tracked=True,
        )
        if decision is None:
            self.store.remove(memory_id)
            return
        if decision.tracking_state != "scheduled" or decision.check_at is None:
            refreshed = self._entry_from_memory(memory, existing=entry)
            if refreshed is not None:
                self.store.upsert(refreshed)
            return
        if decision.check_at > now:
            refreshed = self._entry_from_memory(memory, existing=entry)
            if refreshed is not None:
                self.store.upsert(refreshed)
            return

        expected_version = _memory_version(memory)
        pending_entry = dict(
            entry,
            topic_sync_pending=True,
            last_checked_at=now.isoformat(),
        )
        self.store.upsert(pending_entry)
        entry = pending_entry
        try:
            response = self.client.transition_event_lifecycle(
                user_id=str(entry["user_id"]),
                cube_id=str(entry["cube_id"]),
                memory_id=memory_id,
                expected_version=expected_version,
                to_status="due_unverified",
                observed_at=now.isoformat(),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            self._mark_error(entry, now=now, error=exc)
            return

        outcome = self._response_outcome(response)
        if outcome == "not_found":
            self.store.remove(memory_id)
            return
        if outcome in {"conflict", "no_op"}:
            self._refresh_after_race(entry)
            return
        if outcome != "applied":
            self._mark_error(entry, now=now, error=ValueError("未知的生命周期转换响应"))
            return

        refreshed = dict(entry)
        response_data = _mapping(_mapping(response).get("data"))
        refreshed.update(
            {
                "last_seen_version": int(
                    response_data.get("current_version") or expected_version + 1
                ),
                "check_at": None,
                "next_check_at": None,
                "last_checked_at": now.isoformat(),
                "tracking_state": "waiting_evidence",
                "failure_count": 0,
            }
        )
        self._sync_topic(refreshed)

    def _retry_topic_sync(self) -> None:
        for entry in self.store.snapshot()["events"].values():
            if (
                entry.get("user_id") == self.user_id
                and entry.get("cube_id") == self.cube_id
                and entry.get("topic_sync_pending")
            ):
                self._sync_topic(entry)

    def check_due(self, *, now: datetime | None = None) -> None:
        """Check only index entries whose persisted retry/check time has arrived."""
        with self._operation_lock:
            observed_at = now or self._now()
            if observed_at.tzinfo is None:
                observed_at = observed_at.replace(tzinfo=self.timezone_info)
            entries = self.store.snapshot()["events"].values()
            for entry in entries:
                if entry.get("user_id") != self.user_id or entry.get("cube_id") != self.cube_id:
                    continue
                if entry.get("tracking_state") not in {"scheduled", "error"}:
                    continue
                next_check = _parse_absolute_time(entry.get("next_check_at"), self.timezone_info)
                if next_check is not None and next_check <= observed_at:
                    self._process_due_entry(entry, now=observed_at)


def extract_added_memory_ids(response: Any) -> list[str]:
    """Extract concrete IDs from one synchronous MemOS add response."""
    if not isinstance(response, dict) or response.get("code", 200) != 200:
        return []
    data = response.get("data")
    if not isinstance(data, list):
        return []
    memory_ids: list[str] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        memory_id = str(item.get("memory_id") or item.get("id") or "").strip()
        if memory_id and memory_id not in memory_ids:
            memory_ids.append(memory_id)
    return memory_ids


class PlanTrackerWorker:
    """Single-process background loop hosted by the application backend."""

    def __init__(
        self,
        tracker: PlanTracker,
        *,
        interval_seconds: int = 60,
        reconcile_seconds: int = 900,
    ) -> None:
        self.tracker = tracker
        self.interval_seconds = max(1, interval_seconds)
        self.reconcile_seconds = max(self.interval_seconds, reconcile_seconds)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._registrations: Queue[TrackerRegistration | None] = Queue()
        self._trackers_lock = threading.RLock()
        self._trackers: dict[TrackerScope, PlanTracker] = {
            (tracker.user_id, tracker.cube_id): tracker
        }

    def _tracker_for_scope(self, *, user_id: str, cube_id: str) -> PlanTracker:
        scope = (user_id, cube_id)
        with self._trackers_lock:
            scoped_tracker = self._trackers.get(scope)
            if scoped_tracker is None:
                scoped_tracker = self.tracker.for_scope(user_id=user_id, cube_id=cube_id)
                self._trackers[scope] = scoped_tracker
            return scoped_tracker

    def _all_trackers(self) -> list[PlanTracker]:
        store = getattr(self.tracker, "store", None)
        if store is not None:
            persisted_entries = store.snapshot()["events"].values()
            for entry in persisted_entries:
                user_id = str(entry.get("user_id") or "").strip()
                cube_id = str(entry.get("cube_id") or "").strip()
                if user_id and cube_id:
                    self._tracker_for_scope(user_id=user_id, cube_id=cube_id)
        with self._trackers_lock:
            return list(self._trackers.values())

    def _discard_stale_stop_signals(self) -> None:
        pending_registrations: list[TrackerRegistration] = []
        while True:
            try:
                registration = self._registrations.get_nowait()
            except Empty:
                break
            if registration is not None:
                pending_registrations.append(registration)
        for registration in pending_registrations:
            self._registrations.put(registration)

    def _process_registrations(self, first: TrackerRegistration | None = None) -> None:
        registrations: dict[tuple[str, str, str], bool] = {}
        if first is not None:
            registrations[first[:3]] = first[3]
        while True:
            try:
                item = self._registrations.get_nowait()
            except Empty:
                break
            if item is None:
                self._stop_event.set()
                continue
            user_id, cube_id, memory_id, topic_sync_pending = item
            key = (user_id, cube_id, memory_id)
            registrations[key] = registrations.get(key, False) or topic_sync_pending

        registrations_by_scope: dict[TrackerScope, dict[str, bool]] = {}
        for (user_id, cube_id, memory_id), topic_sync_pending in registrations.items():
            scope_registrations = registrations_by_scope.setdefault((user_id, cube_id), {})
            scope_registrations[memory_id] = topic_sync_pending

        for (user_id, cube_id), scope_registrations in registrations_by_scope.items():
            scoped_tracker = self._tracker_for_scope(user_id=user_id, cube_id=cube_id)
            pending_topic_ids = [
                memory_id for memory_id, pending in scope_registrations.items() if pending
            ]
            ordinary_ids = [
                memory_id for memory_id, pending in scope_registrations.items() if not pending
            ]
            if pending_topic_ids:
                scoped_tracker.refresh_memory_ids(
                    pending_topic_ids,
                    topic_sync_pending=True,
                )
            if ordinary_ids:
                scoped_tracker.refresh_memory_ids(ordinary_ids)

    def _process_registrations_safely(self, first: TrackerRegistration | None = None) -> None:
        try:
            self._process_registrations(first)
        except Exception:
            logger.exception("Plan tracker registration failed")

    def _run(self) -> None:
        next_reconcile_at = 0.0
        while not self._stop_event.is_set():
            self._process_registrations_safely()
            monotonic_now = time_module.monotonic()
            should_reconcile = monotonic_now >= next_reconcile_at
            for scoped_tracker in self._all_trackers():
                try:
                    if should_reconcile:
                        scoped_tracker.reconcile()
                    else:
                        scoped_tracker.check_due()
                except Exception:
                    logger.exception(
                        "Plan tracker worker iteration failed for %s/%s",
                        scoped_tracker.user_id,
                        scoped_tracker.cube_id,
                    )
            if should_reconcile:
                next_reconcile_at = monotonic_now + self.reconcile_seconds
            try:
                registration = self._registrations.get(timeout=self.interval_seconds)
            except Empty:
                continue
            if registration is None:
                self._stop_event.set()
                continue
            self._process_registrations_safely(registration)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._discard_stale_stop_signals()
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="memos-plan-tracker",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._registrations.put(None)
        if self._thread is not None:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                logger.warning("Plan tracker worker did not stop within five seconds")
                return
        self._thread = None

    def register_add_response(
        self,
        response: Any,
        *,
        user_id: str,
        cube_id: str,
        topic_sync_pending: bool = False,
    ) -> None:
        memory_ids = extract_added_memory_ids(response)
        scoped_tracker = self._tracker_for_scope(user_id=user_id, cube_id=cube_id)
        persist_registration = getattr(scoped_tracker, "persist_ingestion_registration", None)
        if callable(persist_registration):
            try:
                persist_registration(
                    memory_ids,
                    topic_sync_pending=topic_sync_pending,
                )
            except (OSError, ValueError) as exc:
                logger.warning("Failed to persist ingestion registration: %s", exc)
        for memory_id in memory_ids:
            self._registrations.put((user_id, cube_id, memory_id, topic_sync_pending))
