#!/usr/bin/env python3
"""Public application API for MemOS clients.

The service owns authentication, uploads, Topic coordination, and stable API
responses while delegating memory operations to the private MemOS core.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import uuid

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Protocol
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import uvicorn

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from memos_app_auth import (
    LOCAL_COOKIE_NAME,
    SECURE_COOKIE_NAME,
    SESSION_TTL_MS,
    auth_configuration_error,
    auth_cookie_name,
    create_session_token,
    request_session_token,
    secure_cookie_enabled,
    verify_access_password,
    verify_session_token,
)
from memos_chat import (
    MemOSClient,
    MemOSClientError,
    _load_project_env,
    import_memory_file,
)
from memos_plan_tracker import PlanTracker, PlanTrackerStore, PlanTrackerWorker
from memos_topic import (
    TopicStore,
    default_store_path,
    extract_added_memories,
    rebalance_runtime_topic_queues,
    reconcile_runtime_topics,
    topic_memory_revision,
)
from memos_topic_queue import (
    DEFAULT_TOPIC_QUEUE_POLICY,
    TOPIC_QUEUE_POLICY_VERSION,
    TopicQueuePolicy,
    latest_scheduled_slot,
    next_scheduled_slot,
)
from pydantic import BaseModel, Field

from memos.log import get_logger


StoreFactory = Callable[[], TopicStore]
ClientFactory = Callable[[str], MemOSClient]
Importer = Callable[..., tuple[Any, Any]]
Reconciler = Callable[..., int]
logger = get_logger(__name__)
DEFAULT_CORS_ALLOWED_ORIGINS = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
)


class RuntimeContext(BaseModel):
    user_id: str = Field(default="default", min_length=1, max_length=200)
    cube_id: str = Field(default="default_cube", min_length=1, max_length=200)


class RememberRequest(RuntimeContext):
    text: str = Field(min_length=1, max_length=2_000_000)


class ChatRequest(RuntimeContext):
    query: str = Field(min_length=1, max_length=100_000)
    session_id: str = Field(default="", max_length=200)
    model: str | None = Field(default=None, max_length=300)


class SearchRequest(RuntimeContext):
    query: str = Field(min_length=1, max_length=100_000)


class VideoRequest(RuntimeContext):
    url: str = Field(min_length=1, max_length=10_000)
    instruction: str | None = Field(default=None, max_length=100_000)


class ReconcileRequest(BaseModel):
    daily_limit: int = Field(default=15, ge=1, le=100)


class AppRememberRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2_000_000)


class AppChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=100_000)
    session_id: str = Field(default="", max_length=200)
    model: str | None = Field(default=None, max_length=300)


class AppSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=100_000)


class AppVideoRequest(BaseModel):
    url: str = Field(min_length=1, max_length=10_000)
    instruction: str | None = Field(default=None, max_length=100_000)


class AccessPasswordRequest(BaseModel):
    password: str = Field(default="", max_length=10_000)


class TopicMaintainer(Protocol):
    def __call__(
        self,
        *,
        now: datetime,
        scheduled_slot: datetime,
        policy: TopicQueuePolicy,
    ) -> dict[str, Any]:
        raise NotImplementedError


class TopicWaitUntil(Protocol):
    async def __call__(self, target_slot: datetime, stop_event: asyncio.Event) -> bool:
        raise NotImplementedError


def default_upload_dir() -> Path:
    configured = os.getenv("MEMOS_WEB_UPLOAD_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[1] / ".memos" / "uploads"


def _api_base() -> str:
    return os.getenv("MEMOS_API_BASE_URL", "http://127.0.0.1:8000").strip().rstrip("/")


def _max_upload_bytes() -> int:
    raw = os.getenv("MEMOS_WEB_UPLOAD_MAX_BYTES", str(2 * 1024 * 1024 * 1024)).strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 2 * 1024 * 1024 * 1024


def _application_scope() -> tuple[str, str]:
    user_id = os.getenv("MEMOS_APP_USER_ID", "default").strip() or "default"
    cube_id = os.getenv("MEMOS_APP_CUBE_ID", "default_cube").strip() or "default_cube"
    return user_id, cube_id


def _topic_scheduler_enabled() -> bool:
    return os.getenv("MEMOS_TOPIC_SCHEDULER_ENABLED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


async def wait_until_topic_slot(target_slot: datetime, stop_event: asyncio.Event) -> bool:
    """Return true when shutdown interrupts the wait, false when the slot is reached."""
    zone = target_slot.tzinfo or ZoneInfo(DEFAULT_TOPIC_QUEUE_POLICY.timezone_name)
    delay = max(0.0, (target_slot - datetime.now(zone)).total_seconds())
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=delay)
    except TimeoutError:
        return False
    return True


async def run_topic_scheduler(
    *,
    maintainer: TopicMaintainer,
    stop_event: asyncio.Event,
    clock: Callable[[], datetime],
    wait_until: TopicWaitUntil,
    policy: TopicQueuePolicy,
) -> None:
    """Catch up one latest slot, then maintain the Topic queues at 00:00 and 12:00."""
    now = clock()
    last_dispatched_slot = latest_scheduled_slot(now, policy.timezone_name)
    await asyncio.to_thread(
        maintainer,
        now=now,
        scheduled_slot=last_dispatched_slot,
        policy=policy,
    )

    while not stop_event.is_set():
        target_slot = next_scheduled_slot(clock(), policy.timezone_name)
        if await wait_until(target_slot, stop_event):
            return
        if stop_event.is_set():
            return
        now = clock()
        scheduled_slot = latest_scheduled_slot(now, policy.timezone_name)
        if scheduled_slot == last_dispatched_slot:
            continue
        await asyncio.to_thread(
            maintainer,
            now=now,
            scheduled_slot=scheduled_slot,
            policy=policy,
        )
        last_dispatched_slot = scheduled_slot


def _plan_tracker_enabled() -> bool:
    return os.getenv("MEMOS_PLAN_TRACKER_ENABLED", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _positive_env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default)).strip()))
    except ValueError:
        return default


def _plan_tracker_state_path() -> Path:
    configured = os.getenv("MEMOS_PLAN_TRACKER_STATE", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[1] / ".memos" / "plan_tracker" / "tracker.json"


def _cors_allowed_origins() -> list[str]:
    raw = os.getenv("MEMOS_CORS_ALLOWED_ORIGINS", "").strip()
    candidates = raw.split(",") if raw else list(DEFAULT_CORS_ALLOWED_ORIGINS)
    origins: list[str] = []
    for candidate in candidates:
        origin = candidate.strip().rstrip("/")
        if not origin:
            continue
        parsed = urlsplit(origin)
        if (
            origin == "*"
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "MEMOS_CORS_ALLOWED_ORIGINS must contain exact HTTP(S) origins "
                "without paths or wildcards."
            )
        if origin not in origins:
            origins.append(origin)
    return origins


def _safe_filename(filename: str | None) -> str:
    name = Path(filename or "upload.bin").name.strip() or "upload.bin"
    cleaned = re.sub(r"[^\w.()\[\]\-\u4e00-\u9fff]+", "_", name)
    return cleaned[:180] or "upload.bin"


def _created_memory_count(result: Any) -> int:
    extracted = extract_added_memories(result)
    if extracted:
        return len(extracted)
    data = result.get("data") if isinstance(result, dict) else None
    return len(data) if isinstance(data, list) else 0


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _info(metadata: dict[str, Any]) -> dict[str, Any]:
    value = metadata.get("info")
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _source_kind(metadata: dict[str, Any], info: dict[str, Any]) -> str:
    source = str(metadata.get("source") or info.get("source_type") or "").lower()
    if source in {"video", "oss_video", "remote_video", "local_video"} or any(
        info.get(key) for key in ("video_id", "media_uri", "duration_ms")
    ):
        return "video"
    if source in {"image", "local_image"}:
        return "image"
    if source in {"mixed", "mixed_media", "mixed_markdown"}:
        return "mixed"
    if source in {"text", "text_input", "local_text_file"}:
        return "text"
    if source in {"conversation", "chat"}:
        return "conversation"
    return "direct"


def _memory_summary(item: Any, *, include_score: bool = False) -> dict[str, Any] | None:
    row = _mapping(item)
    memory_id = str(row.get("id") or row.get("memory_id") or "").strip()
    content = str(row.get("memory") or row.get("value") or row.get("text") or "").strip()
    if not memory_id or not content:
        return None
    metadata = _mapping(row.get("metadata"))
    info = _info(metadata)
    source = _source_kind(metadata, info)
    record_type = str(info.get("record_type") or "").lower()
    if source in {"video", "image", "mixed"}:
        category = "media"
    elif record_type in {"contact", "relationship"}:
        category = "contact"
    elif record_type == "event":
        category = "event"
    else:
        category = "other"
    title = str(metadata.get("key") or info.get("event_title") or content[:80]).strip()
    result: dict[str, Any] = {
        "id": memory_id,
        "title": title,
        "content": content,
        "memory_type": str(metadata.get("memory_type") or "unknown"),
        "source": source,
        "category": category,
        "created_at": metadata.get("created_at"),
        "updated_at": metadata.get("updated_at"),
        "tags": _string_list(metadata.get("tags")),
    }
    if include_score:
        score = row.get("score")
        result["score"] = score if isinstance(score, (int, float)) else None
    return result


def _dashboard_memories(result: Any) -> list[dict[str, Any]]:
    data = _mapping(_mapping(result).get("data"))
    summaries: list[dict[str, Any]] = []
    for key in ("text_mem", "pref_mem", "tool_mem", "skill_mem"):
        for group in _list(data.get(key)):
            for item in _list(_mapping(group).get("memories")):
                summary = _memory_summary(item)
                if summary is not None:
                    summaries.append(summary)
    return summaries


def _search_memories(result: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for child in value:
                visit(child)
            return
        if not isinstance(value, dict):
            return
        summary = _memory_summary(value, include_score=True)
        if summary is not None and summary["id"] not in seen:
            seen.add(summary["id"])
            found.append(summary)
            return
        for child in value.values():
            visit(child)

    visit(result)
    return found


_STRUCTURED_MEMORY_KEYS = (
    "record_type",
    "event_title",
    "event_time",
    "event_start_time",
    "event_end_time",
    "event_start_at",
    "event_end_at",
    "event_status",
    "event_actor",
    "event_action",
    "event_target",
    "participants",
    "event_location",
    "source_recorded_at",
    "contact_name",
    "relations",
    "relationship",
)


def _memory_detail(result: Any) -> dict[str, Any] | None:
    data = _mapping(result).get("data")
    if isinstance(data, list):
        item = next((entry for entry in data if isinstance(entry, dict)), None)
    else:
        item = data
    summary = _memory_summary(item)
    if summary is None:
        return None
    metadata = _mapping(_mapping(item).get("metadata"))
    info = _info(metadata)
    return {
        **summary,
        "confidence": metadata.get("confidence"),
        "background": metadata.get("background"),
        "structured": {
            key: info[key] for key in _STRUCTURED_MEMORY_KEYS if info.get(key) is not None
        },
    }


def _topic_item(topic: Any) -> dict[str, Any]:
    row = _mapping(topic)
    stored_queue_breakdown = _mapping(row.get("queue_score_breakdown"))
    stored_score_breakdown = _mapping(row.get("score_breakdown"))

    def score_value(*values: Any, default: float = 0.0) -> float:
        return next(
            (
                float(value)
                for value in values
                if not isinstance(value, bool) and isinstance(value, (int, float))
            ),
            default,
        )

    importance_score = score_value(
        row.get("importance_score"),
        stored_queue_breakdown.get("importance_score"),
        stored_score_breakdown.get("importance_score"),
        stored_score_breakdown.get("base_score"),
    )
    approaching_bonus = score_value(
        row.get("approaching_bonus"),
        stored_queue_breakdown.get("approaching_bonus"),
    )
    decay_penalty = score_value(
        row.get("decay_penalty"),
        stored_queue_breakdown.get("decay_penalty"),
    )
    queue_score = score_value(
        row.get("queue_score"),
        stored_queue_breakdown.get("queue_score"),
        row.get("rank_score"),
    )
    queue_score_breakdown = {
        "importance_score": importance_score,
        "approaching_bonus": approaching_bonus,
        "decay_penalty": decay_penalty,
        "queue_score": queue_score,
    }
    complete_importance_fields = {
        "strongest_memory_score",
        "supporting_memory_points",
        "duplicate_memory_count",
        "counted_memory_ids",
        "importance_score",
        "base_score",
        "memory_scores",
    }
    incomplete_importance_breakdown = (
        "importance_score" in stored_score_breakdown
        and not complete_importance_fields.issubset(stored_score_breakdown)
    )
    if incomplete_importance_breakdown:
        stored_base_score = stored_score_breakdown.get("base_score")
        score_breakdown = {
            "model": "partial",
            "base_score": (
                float(stored_base_score)
                if not isinstance(stored_base_score, bool)
                and isinstance(stored_base_score, (int, float))
                else None
            ),
        }
    else:
        score_breakdown = dict(stored_score_breakdown)
    score_breakdown.update(
        {
            "recency_factor": 1.0,
            "rank_score": queue_score,
        }
    )
    evidence = [
        {
            "memory_id": str(item.get("memory_id") or ""),
            "fact": str(item.get("fact") or ""),
            "contribution": str(item.get("contribution") or ""),
        }
        for item in (_mapping(value) for value in _list(row.get("reason_evidence")))
        if item.get("memory_id")
    ]
    versions = [
        {
            "version": item.get("version"),
            "title": str(item.get("topic_text") or ""),
            "reason": str(item.get("reason_summary") or ""),
            "updated_at": item.get("updated_at") or item.get("created_at"),
        }
        for item in (_mapping(value) for value in _list(row.get("versions")))
    ]
    return {
        "id": str(row.get("topic_id") or row.get("topic_key") or ""),
        "key": str(row.get("topic_key") or ""),
        "title": str(row.get("topic_text") or ""),
        "reason": str(row.get("reason_summary") or ""),
        "status": str(row.get("lifecycle_status") or "unknown"),
        "progress": str(row.get("progress_status") or "unknown"),
        "queue_rank": row.get("queue_rank"),
        "candidate_source": row.get("candidate_source"),
        "attention_status": str(row.get("attention_status") or "open"),
        "importance_score": importance_score,
        "approaching_bonus": approaching_bonus,
        "decay_penalty": decay_penalty,
        "queue_score": queue_score,
        "score": queue_score,
        "queue_score_breakdown": queue_score_breakdown,
        "supporting_memory_ids": _string_list(row.get("supporting_memory_ids")),
        "evidence": evidence,
        "candidate_reasons": _string_list(row.get("candidate_reasons")),
        "score_breakdown": score_breakdown,
        "core_entered_at": row.get("core_entered_at"),
        "demoted_at": row.get("demoted_at"),
        "calculated_at": row.get("calculated_at"),
        "first_seen_at": row.get("first_seen_at"),
        "last_evidence_at": row.get("last_evidence_at"),
        "version": row.get("version"),
        "updated_at": row.get("updated_at"),
        "versions": versions,
    }


def _topic_update(client: Any) -> dict[str, Any]:
    update = _mapping(getattr(client, "last_topic_update", None))
    topics = _list(update.get("topics"))
    core_count = update.get("core_count")
    if not isinstance(core_count, int):
        queued_topics = [item for item in topics if isinstance(item, dict)]
        has_queue_status = any(
            item.get("lifecycle_status") in {"active", "suppressed"} for item in queued_topics
        )
        core_count = (
            sum(item.get("lifecycle_status") == "active" for item in queued_topics)
            if has_queue_status
            else len(topics)
        )
    return {
        "processed_memories": int(update.get("processed_memories") or 0),
        "active_topics": core_count,
        "error": getattr(client, "last_topic_error", None),
    }


def _delete_succeeded(result: Any) -> bool:
    return _mapping(_mapping(result).get("data")).get("status") == "success"


def create_app(
    *,
    store_factory: StoreFactory | None = None,
    client_factory: ClientFactory | None = None,
    importer: Importer = import_memory_file,
    reconciler: Reconciler = reconcile_runtime_topics,
    upload_dir: Path | None = None,
    auth_required: bool | None = False,
    tracker_worker: PlanTrackerWorker | None = None,
    topic_scheduler_enabled: bool | None = False,
    topic_maintainer: TopicMaintainer = rebalance_runtime_topic_queues,
    topic_policy: TopicQueuePolicy | None = None,
    topic_wait_until: TopicWaitUntil = wait_until_topic_slot,
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    """Create the application API and optionally own the Topic scheduler lifespan."""
    resolved_store_factory = store_factory or (lambda: TopicStore(default_store_path()))
    resolved_client_factory = client_factory or (lambda base_url: MemOSClient(base_url))
    resolved_upload_dir = upload_dir or default_upload_dir()
    raw_auth_required = os.getenv("MEMOS_AUTH_REQUIRED", "").strip().lower()
    configured_auth_required = (
        raw_auth_required in {"1", "true", "yes", "on"}
        if raw_auth_required
        else bool(
            os.getenv("MEMOS_ACCESS_PASSWORD_HASH", "").strip()
            or os.getenv("MEMOS_SESSION_SECRET", "").strip()
        )
    )
    resolved_auth_required = configured_auth_required if auth_required is None else auth_required
    resolved_topic_scheduler_enabled = (
        _topic_scheduler_enabled() if topic_scheduler_enabled is None else topic_scheduler_enabled
    )
    resolved_topic_policy = topic_policy or DEFAULT_TOPIC_QUEUE_POLICY
    resolved_clock = clock or (lambda: datetime.now(ZoneInfo(resolved_topic_policy.timezone_name)))

    @asynccontextmanager
    async def application_lifespan(lifespan_app: FastAPI) -> AsyncIterator[None]:
        lifespan_app.state.topic_scheduler_error = None
        if tracker_worker is not None:
            tracker_worker.start()
        topic_stop_event: asyncio.Event | None = None
        try:
            if resolved_topic_scheduler_enabled:
                topic_stop_event = asyncio.Event()
                lifespan_app.state.topic_scheduler_stop_event = topic_stop_event
                lifespan_app.state.topic_scheduler_task = asyncio.create_task(
                    run_topic_scheduler(
                        maintainer=topic_maintainer,
                        stop_event=topic_stop_event,
                        clock=resolved_clock,
                        wait_until=topic_wait_until,
                        policy=resolved_topic_policy,
                    ),
                    name="memos-topic-scheduler",
                )
                topic_task = lifespan_app.state.topic_scheduler_task

                def record_topic_scheduler_result(completed_task: asyncio.Task[None]) -> None:
                    if completed_task.cancelled():
                        return
                    error = completed_task.exception()
                    if error is not None:
                        lifespan_app.state.topic_scheduler_error = str(error)
                        logger.error(
                            "Topic scheduler failed: %s",
                            error,
                            exc_info=(type(error), error, error.__traceback__),
                        )
                    elif topic_stop_event is not None and not topic_stop_event.is_set():
                        message = "Topic scheduler stopped unexpectedly."
                        lifespan_app.state.topic_scheduler_error = message
                        logger.error(message)

                topic_task.add_done_callback(record_topic_scheduler_result)
            yield
        finally:
            try:
                topic_task = lifespan_app.state.topic_scheduler_task
                if topic_task is not None:
                    assert topic_stop_event is not None
                    topic_stop_event.set()
                    await topic_task
            finally:
                lifespan_app.state.topic_scheduler_task = None
                lifespan_app.state.topic_scheduler_stop_event = None
                if tracker_worker is not None:
                    tracker_worker.stop()

    app = FastAPI(
        title="MemOS Application API",
        version="2.0.0",
        lifespan=application_lifespan,
    )
    app.state.topic_scheduler_task = None
    app.state.topic_scheduler_stop_event = None
    app.state.topic_scheduler_error = None
    app.state.topic_policy = resolved_topic_policy
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_allowed_origins(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    def request_is_authenticated(request: Request) -> bool:
        token = request_session_token(
            cookies=dict(request.cookies),
            authorization=request.headers.get("authorization"),
        )
        return verify_session_token(token)

    def notify_plan_tracker(
        result: Any,
        *,
        user_id: str,
        cube_id: str,
        topic_error: Any = None,
    ) -> None:
        if tracker_worker is None:
            return
        try:
            tracker_worker.register_add_response(
                result,
                user_id=user_id,
                cube_id=cube_id,
                topic_sync_pending=bool(topic_error),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning("Failed to register newly ingested events with tracker: %s", exc)

    @app.middleware("http")
    async def protect_application_api(request: Request, call_next):
        public_paths = {
            "/api/v1/health",
            "/api/v1/auth/login",
            "/api/v1/auth/mobile/login",
            "/api/v1/auth/logout",
            "/api/v1/auth/session",
        }
        if (
            resolved_auth_required
            and request.method != "OPTIONS"
            and request.url.path.startswith("/api/v1/")
            and request.url.path not in public_paths
            and not request_is_authenticated(request)
        ):
            return JSONResponse(status_code=401, content={"detail": "请先输入访问密码。"})
        return await call_next(request)

    @app.post("/api/v1/auth/login")
    def application_login(request: AccessPasswordRequest) -> JSONResponse:
        configuration_error = auth_configuration_error()
        if configuration_error:
            raise HTTPException(status_code=503, detail=configuration_error)
        if not verify_access_password(request.password):
            raise HTTPException(status_code=401, detail="访问密码不正确。")
        response = JSONResponse({"ok": True})
        response.set_cookie(
            key=auth_cookie_name(),
            value=create_session_token(),
            max_age=SESSION_TTL_MS // 1000,
            httponly=True,
            secure=secure_cookie_enabled(),
            samesite="strict",
            path="/",
        )
        return response

    @app.post("/api/v1/auth/mobile/login")
    def application_mobile_login(request: AccessPasswordRequest) -> JSONResponse:
        configuration_error = auth_configuration_error()
        if configuration_error:
            raise HTTPException(status_code=503, detail=configuration_error)
        if not verify_access_password(request.password):
            raise HTTPException(status_code=401, detail="访问密码不正确。")
        return JSONResponse(
            {
                "ok": True,
                "token_type": "Bearer",
                "session_token": create_session_token(),
                "expires_in": SESSION_TTL_MS // 1000,
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/v1/auth/session")
    def application_session(request: Request) -> dict[str, bool]:
        return {"authenticated": not resolved_auth_required or request_is_authenticated(request)}

    @app.post("/api/v1/auth/logout")
    def application_logout() -> JSONResponse:
        response = JSONResponse({"ok": True})
        response.delete_cookie(LOCAL_COOKIE_NAME, path="/")
        response.delete_cookie(SECURE_COOKIE_NAME, path="/", secure=True)
        return response

    def application_topics(*, include_suppressed: bool = True) -> dict[str, Any]:
        user_id, cube_id = _application_scope()
        store = resolved_store_factory()
        list_queue_snapshot = getattr(store, "list_queue_snapshot", None)
        if callable(list_queue_snapshot):
            snapshot = _mapping(
                list_queue_snapshot(
                    user_id=user_id,
                    cube_id=cube_id,
                    policy=resolved_topic_policy,
                )
            )
        else:
            # Some authentication-only test doubles predate the queue snapshot API.
            snapshot = {
                "items": [],
                "pool_total": 0,
                "candidate_pool_total": 0,
                "hidden_candidate_count": 0,
                "queue_calculated_at": None,
            }
        rows = [_mapping(row) for row in _list(snapshot.get("items"))]
        core_items = [_topic_item(row) for row in rows if row.get("lifecycle_status") == "active"][
            : resolved_topic_policy.core_limit
        ]
        candidate_items = [
            _topic_item(row) for row in rows if row.get("lifecycle_status") == "suppressed"
        ][: resolved_topic_policy.visible_candidate_limit]
        items = [*core_items, *(candidate_items if include_suppressed else [])]
        return {
            "total": len(items),
            "returned": len(items),
            "pool_total": int(snapshot.get("pool_total") or 0),
            "candidate_pool_total": int(snapshot.get("candidate_pool_total") or 0),
            "core_limit": resolved_topic_policy.core_limit,
            "visible_candidate_limit": resolved_topic_policy.visible_candidate_limit,
            "core_count": len(core_items),
            "visible_candidate_count": len(candidate_items) if include_suppressed else 0,
            "hidden_candidate_count": int(snapshot.get("hidden_candidate_count") or 0),
            "calculated_at": snapshot.get("queue_calculated_at"),
            "items": items,
        }

    @app.get("/api/v1/health")
    def application_health() -> dict[str, Any]:
        store = resolved_store_factory()
        try:
            memos_health = _mapping(resolved_client_factory(_api_base()).health())
            reachable = True
        except (MemOSClientError, OSError):
            memos_health = {}
            reachable = False
        topic_scheduler_error = app.state.topic_scheduler_error
        return {
            "status": "healthy" if reachable and not topic_scheduler_error else "degraded",
            "dependencies": {
                "memos": "online" if reachable else "offline",
                "topics": (
                    "error"
                    if topic_scheduler_error
                    else "online"
                    if Path(store.path).is_file()
                    else "empty"
                ),
            },
            "service_version": memos_health.get("version"),
        }

    @app.get("/api/v1/dashboard")
    def application_dashboard() -> dict[str, Any]:
        user_id, cube_id = _application_scope()
        client = resolved_client_factory(_api_base())
        try:
            health_result = _mapping(client.health())
            memory_result = client.get_memory_dashboard(user_id=user_id, cube_id=cube_id)
        except (MemOSClientError, OSError, ValueError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        try:
            scheduler_result = _mapping(client.get_scheduler_status())
        except (MemOSClientError, OSError, ValueError):
            scheduler_result = {}
        with suppress(MemOSClientError, OSError, ValueError):
            client.get_task_queue_status(user_id=user_id)

        memories = _dashboard_memories(memory_result)
        memory_data = _mapping(_mapping(memory_result).get("data"))
        statistics = _mapping(memory_data.get("statistics"))
        scheduler_summary = _mapping(
            _mapping(scheduler_result.get("data")).get("scheduler_summary")
        )
        try:
            topics_result = application_topics(include_suppressed=False)["items"]
        except (OSError, ValueError):
            topics_result = []
        active_topics = [topic for topic in topics_result if topic["status"] == "active"]
        health_data = _mapping(health_result.get("data"))
        health_status = str(health_result.get("status") or health_data.get("status") or "").lower()
        return {
            "backend_status": "online" if health_status == "healthy" else "degraded",
            "service_version": health_result.get("version") or health_data.get("version"),
            "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "scope": {"user_id": user_id, "cube_id": cube_id},
            "counts": {
                "memories": int(statistics.get("total_text_nodes") or len(memories)),
                "preferences": int(statistics.get("total_preference_nodes") or 0),
                "skills": int(statistics.get("total_skill_nodes") or 0),
                "queue_total": int(scheduler_summary.get("total") or 0),
                "queue_running": int(scheduler_summary.get("in_progress") or 0),
                "queue_waiting": int(scheduler_summary.get("waiting") or 0),
                "active_topics": len(active_topics),
            },
            "topics": active_topics,
            "memories": memories,
        }

    @app.get("/api/v1/memories")
    def application_memories(
        query: str = "", category: str = "all", limit: int = 500
    ) -> dict[str, Any]:
        user_id, cube_id = _application_scope()
        try:
            result = resolved_client_factory(_api_base()).get_memory_dashboard(
                user_id=user_id, cube_id=cube_id
            )
        except (MemOSClientError, OSError, ValueError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        items = _dashboard_memories(result)
        normalized_query = query.strip().lower()
        normalized_category = category.strip().lower()
        if normalized_category and normalized_category != "all":
            items = [item for item in items if item["category"] == normalized_category]
        if normalized_query:
            items = [
                item
                for item in items
                if normalized_query
                in " ".join([item["title"], item["content"], *item["tags"]]).lower()
            ]
        items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        bounded_limit = min(500, max(1, limit))
        return {
            "scope": {"user_id": user_id, "cube_id": cube_id},
            "total": len(items),
            "items": items[:bounded_limit],
        }

    @app.get("/api/v1/memories/{memory_id}")
    def application_memory(memory_id: str) -> dict[str, Any]:
        try:
            result = resolved_client_factory(_api_base()).get_memory(memory_id)
        except (MemOSClientError, OSError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        memory = _memory_detail(result)
        if memory is None:
            raise HTTPException(status_code=404, detail="没有找到这条记忆。")
        return {"memory": memory}

    @app.delete("/api/v1/memories/{memory_id}")
    def application_delete_memory(memory_id: str) -> dict[str, Any]:
        normalized_id = memory_id.strip()
        if not normalized_id or len(normalized_id) > 500:
            raise HTTPException(status_code=400, detail="记忆 ID 不合法。")
        try:
            result = resolved_client_factory(_api_base()).delete_memory(normalized_id)
        except (MemOSClientError, OSError, ValueError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if not _delete_succeeded(result):
            raise HTTPException(status_code=502, detail="MemOS 没有确认删除成功。")

        try:
            removed = reconciler(
                base_url=_api_base(),
                daily_limit=resolved_topic_policy.core_limit,
            )
            topic_sync = "updated"
        except (MemOSClientError, OSError, ValueError):
            removed = 0
            topic_sync = "pending"
        return {
            "ok": True,
            "memory_id": normalized_id,
            "topic_sync": topic_sync,
            "removed_topic_memories": removed,
        }

    @app.get("/api/v1/topics")
    def application_topic_list(include_suppressed: bool = True) -> dict[str, Any]:
        return application_topics(include_suppressed=include_suppressed)

    @app.get("/api/v1/topics/{topic_id}/trace")
    def application_topic_trace(topic_id: str) -> dict[str, Any]:
        user_id, cube_id = _application_scope()
        store = resolved_store_factory()
        trace = store.topic_selection_trace(
            user_id=user_id,
            cube_id=cube_id,
            topic_id=topic_id,
            seat_limit=resolved_topic_policy.core_limit,
        )
        if trace is None:
            raise HTTPException(status_code=404, detail="没有找到这条 Topic。")
        result = dict(trace)
        if result.get("available") is False:
            return result
        policy = dict(_mapping(result.get("policy")))
        policy.update(
            {
                "queue_policy_version": TOPIC_QUEUE_POLICY_VERSION,
                "core_limit": resolved_topic_policy.core_limit,
                "visible_candidate_limit": resolved_topic_policy.visible_candidate_limit,
                "scheduled_promotion_margin": resolved_topic_policy.scheduled_promotion_margin,
                "immediate_promotion_margin": resolved_topic_policy.immediate_promotion_margin,
                "queue_formula": "importance_score + approaching_bonus - decay_penalty",
                "seat_limit": resolved_topic_policy.core_limit,
            }
        )
        result["policy"] = policy

        decision = dict(_mapping(result.get("decision")))
        topic_row: dict[str, Any] = {}
        topic_key = str(result.get("topic_key") or "").strip()
        topic_record = getattr(store, "topic_record", None)
        if topic_key and callable(topic_record):
            topic_row = _mapping(
                topic_record(user_id=user_id, cube_id=cube_id, topic_key=topic_key)
            )

        def decision_score(field: str, legacy_field: str | None = None) -> float:
            values = [topic_row.get(field), decision.get(field)]
            if legacy_field is not None:
                values.append(decision.get(legacy_field))
            return next(
                (
                    float(value)
                    for value in values
                    if not isinstance(value, bool) and isinstance(value, (int, float))
                ),
                0.0,
            )

        importance_score = decision_score("importance_score", "base_score")
        approaching_bonus = decision_score("approaching_bonus")
        decay_penalty = decision_score("decay_penalty")
        queue_score = decision_score("queue_score", "rank_score")
        queue_rank = topic_row.get("queue_rank", decision.get("queue_rank"))
        if queue_rank is None:
            queue_rank = decision.get("rank_position")
        decision.update(
            {
                "importance_score": importance_score,
                "approaching_bonus": approaching_bonus,
                "decay_penalty": decay_penalty,
                "queue_score": queue_score,
                "queue_rank": queue_rank,
                "candidate_source": topic_row.get(
                    "candidate_source", decision.get("candidate_source")
                ),
                "attention_status": topic_row.get(
                    "attention_status", decision.get("attention_status", "open")
                ),
                "base_score": importance_score,
                "recency_factor": 1.0,
                "rank_score": queue_score,
            }
        )
        result["decision"] = decision
        return result

    @app.post("/api/v1/topics/reconcile")
    def application_topic_reconcile() -> dict[str, Any]:
        try:
            removed = reconciler(
                base_url=_api_base(),
                daily_limit=resolved_topic_policy.core_limit,
            )
        except (MemOSClientError, OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "removed_memories": removed}

    @app.post("/api/v1/ingestions/text")
    def application_remember(request: AppRememberRequest) -> dict[str, Any]:
        user_id, cube_id = _application_scope()
        client = resolved_client_factory(_api_base())
        try:
            result = client.remember(user_id=user_id, cube_id=cube_id, text=request.text.strip())
        except (MemOSClientError, OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        notify_plan_tracker(
            result,
            user_id=user_id,
            cube_id=cube_id,
            topic_error=getattr(client, "last_topic_error", None),
        )
        return {
            "ok": True,
            "memories_created": _created_memory_count(result),
            "topic": _topic_update(client),
        }

    @app.post("/api/v1/chat")
    def application_chat(request: AppChatRequest) -> dict[str, Any]:
        user_id, cube_id = _application_scope()
        session_id = request.session_id.strip() or f"web-{uuid.uuid4().hex[:12]}"
        try:
            answer = resolved_client_factory(_api_base()).chat(
                user_id=user_id,
                cube_id=cube_id,
                session_id=session_id,
                query=request.query.strip(),
                model=request.model.strip() if request.model else None,
            )
        except (MemOSClientError, OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"response": answer, "session_id": session_id}

    @app.post("/api/v1/search")
    def application_search(request: AppSearchRequest) -> dict[str, Any]:
        user_id, cube_id = _application_scope()
        try:
            result = resolved_client_factory(_api_base()).search(
                user_id=user_id, cube_id=cube_id, query=request.query.strip()
            )
        except (MemOSClientError, OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        memories = _search_memories(result)
        return {"results": memories, "total": len(memories)}

    @app.post("/api/v1/ingestions/video")
    def application_video(request: AppVideoRequest) -> dict[str, Any]:
        url = request.url.strip()
        if not url.lower().startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="远程视频只支持 HTTP(S) URL。")
        user_id, cube_id = _application_scope()
        client = resolved_client_factory(_api_base())
        try:
            result = client.remember_video(
                user_id=user_id,
                cube_id=cube_id,
                video_source=url,
                instruction=request.instruction.strip() if request.instruction else None,
                session_id=None,
            )
        except (MemOSClientError, OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        notify_plan_tracker(
            result,
            user_id=user_id,
            cube_id=cube_id,
            topic_error=getattr(client, "last_topic_error", None),
        )
        return {
            "ok": True,
            "kind": "video",
            "memories_created": _created_memory_count(result),
            "topic": _topic_update(client),
        }

    async def process_file_upload(
        *, file: UploadFile, user_id: str, cube_id: str, instruction: str
    ) -> tuple[Any, Any, Any, str, int, Path]:
        resolved_upload_dir.mkdir(parents=True, exist_ok=True)
        filename = _safe_filename(file.filename)
        stored_path = resolved_upload_dir / f"{uuid.uuid4().hex}-{filename}"
        total = 0
        max_bytes = _max_upload_bytes()
        try:
            with stored_path.open("wb") as destination:
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > max_bytes:
                        raise HTTPException(status_code=413, detail="文件超过允许的上传大小。")
                    destination.write(chunk)

            client = resolved_client_factory(_api_base())
            imported, result = importer(
                client,
                user_id=user_id,
                cube_id=cube_id,
                session_id=f"web-{uuid.uuid4().hex[:12]}",
                file_path=str(stored_path),
                instruction=instruction.strip() or None,
            )
            return client, imported, result, filename, total, stored_path
        except HTTPException:
            stored_path.unlink(missing_ok=True)
            raise
        except (MemOSClientError, OSError, ValueError) as exc:
            stored_path.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            await file.close()

    @app.post("/api/v1/ingestions")
    async def application_ingest(
        file: Annotated[UploadFile, File()],
        instruction: Annotated[str, Form()] = "",
    ) -> dict[str, Any]:
        user_id, cube_id = _application_scope()
        client, imported, result, filename, total, _ = await process_file_upload(
            file=file,
            user_id=user_id,
            cube_id=cube_id,
            instruction=instruction,
        )
        notify_plan_tracker(
            result,
            user_id=user_id,
            cube_id=cube_id,
            topic_error=getattr(client, "last_topic_error", None),
        )
        return {
            "ok": True,
            "kind": imported.kind,
            "filename": filename,
            "file_size": total,
            "memories_created": _created_memory_count(result),
            "topic": _topic_update(client),
        }

    @app.get("/health")
    def health() -> dict[str, Any]:
        store = resolved_store_factory()
        try:
            memos_health = resolved_client_factory(_api_base()).health()
            memos_reachable = True
        except (MemOSClientError, OSError) as exc:
            memos_health = {"error": str(exc)}
            memos_reachable = False
        return {
            "status": "healthy" if memos_reachable else "degraded",
            "memos_reachable": memos_reachable,
            "memos": memos_health,
            "topic_state_exists": Path(store.path).is_file(),
        }

    @app.get("/topics")
    def topics(
        user_id: str = "default",
        cube_id: str = "default_cube",
        topic_date: str | None = None,
        include_suppressed: bool = True,
        limit: int = 500,
    ) -> dict[str, Any]:
        store = resolved_store_factory()
        if topic_date:
            rows = store.list_topics(
                user_id=user_id,
                cube_id=cube_id,
                topic_date=topic_date,
                include_suppressed=include_suppressed,
            )
        else:
            snapshot = store.list_queue_snapshot(
                user_id=user_id,
                cube_id=cube_id,
                policy=resolved_topic_policy,
            )
            rows = _list(_mapping(snapshot).get("items"))
            if not include_suppressed:
                rows = [row for row in rows if _mapping(row).get("lifecycle_status") == "active"]
        response_limit = min(
            max(1, limit),
            resolved_topic_policy.core_limit + resolved_topic_policy.visible_candidate_limit,
        )
        rows = rows[:response_limit]
        return {
            "source": "json_snapshot",
            "total": len(rows),
            "user_id": user_id,
            "cube_id": cube_id,
            "topics": rows,
        }

    @app.post("/runtime/remember")
    def runtime_remember(request: RememberRequest) -> dict[str, Any]:
        client = resolved_client_factory(_api_base())
        try:
            result = client.remember(
                user_id=request.user_id,
                cube_id=request.cube_id,
                text=request.text.strip(),
            )
        except (MemOSClientError, OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        notify_plan_tracker(
            result,
            user_id=request.user_id,
            cube_id=request.cube_id,
            topic_error=getattr(client, "last_topic_error", None),
        )

        return {
            "ok": True,
            "memories_created": _created_memory_count(result),
            "memos_result": jsonable_encoder(result),
            "topic_update": jsonable_encoder(client.last_topic_update),
            "topic_error": client.last_topic_error,
        }

    @app.post("/runtime/chat")
    def runtime_chat(request: ChatRequest) -> dict[str, Any]:
        session_id = request.session_id.strip() or f"web-{uuid.uuid4().hex[:12]}"
        try:
            answer = resolved_client_factory(_api_base()).chat(
                user_id=request.user_id,
                cube_id=request.cube_id,
                session_id=session_id,
                query=request.query.strip(),
                model=request.model.strip() if request.model else None,
            )
        except (MemOSClientError, OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "response": answer, "session_id": session_id}

    @app.post("/runtime/search")
    def runtime_search(request: SearchRequest) -> dict[str, Any]:
        try:
            result = resolved_client_factory(_api_base()).search(
                user_id=request.user_id,
                cube_id=request.cube_id,
                query=request.query.strip(),
            )
        except (MemOSClientError, OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "memos_result": jsonable_encoder(result)}

    @app.post("/runtime/video")
    def runtime_video(request: VideoRequest) -> dict[str, Any]:
        url = request.url.strip()
        if not url.lower().startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="远程视频只支持 HTTP(S) URL。")
        client = resolved_client_factory(_api_base())
        try:
            result = client.remember_video(
                user_id=request.user_id,
                cube_id=request.cube_id,
                video_source=url,
                instruction=request.instruction.strip() if request.instruction else None,
                session_id=None,
            )
        except (MemOSClientError, OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        notify_plan_tracker(
            result,
            user_id=request.user_id,
            cube_id=request.cube_id,
            topic_error=getattr(client, "last_topic_error", None),
        )
        return {
            "ok": True,
            "memories_created": _created_memory_count(result),
            "memos_result": jsonable_encoder(result),
            "topic_update": jsonable_encoder(client.last_topic_update),
            "topic_error": client.last_topic_error,
        }

    @app.get("/runtime/memories/{memory_id}")
    def runtime_memory(memory_id: str) -> dict[str, Any]:
        try:
            result = resolved_client_factory(_api_base()).get_memory(memory_id)
        except (MemOSClientError, OSError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"ok": True, "memory": jsonable_encoder(result)}

    @app.post("/topics/reconcile")
    def topics_reconcile(request: ReconcileRequest) -> dict[str, Any]:
        del request
        try:
            removed = reconciler(
                base_url=_api_base(),
                daily_limit=resolved_topic_policy.core_limit,
            )
        except (MemOSClientError, OSError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "removed_memories": removed}

    @app.post("/ingest")
    async def ingest(
        file: Annotated[UploadFile, File()],
        user_id: Annotated[str, Form()] = "default",
        cube_id: Annotated[str, Form()] = "default_cube",
        instruction: Annotated[str, Form()] = "",
    ) -> dict[str, Any]:
        client, imported, result, filename, total, stored_path = await process_file_upload(
            file=file,
            user_id=user_id,
            cube_id=cube_id,
            instruction=instruction,
        )
        notify_plan_tracker(
            result,
            user_id=user_id,
            cube_id=cube_id,
            topic_error=getattr(client, "last_topic_error", None),
        )

        return {
            "ok": True,
            "kind": imported.kind,
            "filename": filename,
            "file_size": total,
            "stored_path": str(stored_path),
            "memories_created": _created_memory_count(result),
            "memos_result": jsonable_encoder(result),
            "topic_update": jsonable_encoder(client.last_topic_update),
            "topic_error": client.last_topic_error,
        }

    return app


def _build_plan_tracker_worker() -> PlanTrackerWorker | None:
    if not _plan_tracker_enabled():
        return None
    user_id, cube_id = _application_scope()
    topic_store = TopicStore(default_store_path())

    def refresh_topic(user_id: str, cube_id: str, memory_id: str) -> int:
        from memos_topic import refresh_runtime_topics_by_ids

        return refresh_runtime_topics_by_ids(
            base_url=_api_base(),
            user_id=user_id,
            cube_id=cube_id,
            memory_ids=[memory_id],
            daily_limit=DEFAULT_TOPIC_QUEUE_POLICY.core_limit,
        )

    def topic_needs_sync(
        user_id: str,
        cube_id: str,
        memory_id: str,
        memory: dict[str, Any] | None,
    ) -> bool:
        stored_revision = topic_store.stored_memory_revision(user_id, cube_id, memory_id)
        if memory is None:
            return stored_revision is not None
        return stored_revision != topic_memory_revision(memory)

    tracker = PlanTracker(
        store=PlanTrackerStore(_plan_tracker_state_path()),
        client=MemOSClient(_api_base()),
        user_id=user_id,
        cube_id=cube_id,
        topic_refresher=refresh_topic,
        topic_sync_checker=topic_needs_sync,
        retry_base_seconds=_positive_env_int("MEMOS_PLAN_TRACKER_INTERVAL_SECONDS", 60),
    )
    return PlanTrackerWorker(
        tracker,
        interval_seconds=_positive_env_int("MEMOS_PLAN_TRACKER_INTERVAL_SECONDS", 60),
        reconcile_seconds=_positive_env_int("MEMOS_PLAN_TRACKER_RECONCILE_SECONDS", 900),
    )


_load_project_env()
app = create_app(
    auth_required=None,
    tracker_worker=_build_plan_tracker_worker(),
    topic_scheduler_enabled=None,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MemOS 本地可视化连接服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8011)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    os.environ["MEMOS_API_BASE_URL"] = args.base_url.rstrip("/")
    runtime_app = create_app(
        auth_required=None,
        tracker_worker=_build_plan_tracker_worker(),
        topic_scheduler_enabled=None,
    )
    uvicorn.run(runtime_app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
