from __future__ import annotations

import importlib.util
import sys

from pathlib import Path
from types import SimpleNamespace

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

        def list_all_topics(self, **kwargs):
            calls.append(kwargs)
            return [
                {
                    "topic_id": "topic-1",
                    "topic_date": "2026-08-20",
                    "topic_key": "final_exam",
                    "topic_text": "用户正在准备期末考试。",
                    "lifecycle_status": "active",
                    "rank_score": 0.88,
                    "reason_evidence": [],
                    "supporting_memory_ids": ["memory-1"],
                }
            ]

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
            "include_suppressed": True,
            "limit": 500,
        }
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

    app = memos_frontend_api.create_app(
        store_factory=FakeStore,
        client_factory=lambda base_url: FakeClient(),
        upload_dir=tmp_path / "uploads",
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
    assert reconcile_calls == [("http://127.0.0.1:8000", 15)]
    assert detail_response.status_code == 200
    assert detail_response.json()["memory"]["data"]["id"] == "memory-3"
