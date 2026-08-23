#!/usr/bin/env python3
"""Thin local HTTP bridge for the MemOS dashboard.

The bridge deliberately owns no memory or Topic business logic. It exposes the
existing runtime importer and the rolling Topic JSON snapshot to the local web
frontend.
"""

from __future__ import annotations

import argparse
import os
import re
import uuid

from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import uvicorn

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from memos_chat import (
    MemOSClient,
    MemOSClientError,
    _load_project_env,
    import_memory_file,
)
from memos_topic import (
    TopicStore,
    default_store_path,
    extract_added_memories,
    reconcile_runtime_topics,
)
from pydantic import BaseModel, Field


StoreFactory = Callable[[], TopicStore]
ClientFactory = Callable[[str], MemOSClient]
Importer = Callable[..., tuple[Any, Any]]
Reconciler = Callable[..., int]


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


def create_app(
    *,
    store_factory: StoreFactory | None = None,
    client_factory: ClientFactory | None = None,
    importer: Importer = import_memory_file,
    reconciler: Reconciler = reconcile_runtime_topics,
    upload_dir: Path | None = None,
) -> FastAPI:
    """Create the local bridge with injectable dependencies for tests."""
    resolved_store_factory = store_factory or (lambda: TopicStore(default_store_path()))
    resolved_client_factory = client_factory or (lambda base_url: MemOSClient(base_url))
    resolved_upload_dir = upload_dir or default_upload_dir()

    app = FastAPI(title="MemOS Local Dashboard Bridge", version="1.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

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
            rows = store.list_all_topics(
                user_id=user_id,
                cube_id=cube_id,
                include_suppressed=include_suppressed,
                limit=limit,
            )
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
        try:
            removed = reconciler(base_url=_api_base(), daily_limit=request.daily_limit)
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
        except HTTPException:
            stored_path.unlink(missing_ok=True)
            raise
        except (MemOSClientError, OSError, ValueError) as exc:
            stored_path.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            await file.close()

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


_load_project_env()
app = create_app()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MemOS 本地可视化连接服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8011)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    os.environ["MEMOS_API_BASE_URL"] = args.base_url.rstrip("/")
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
