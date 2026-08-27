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


def test_v1_dashboard_and_memories_expose_stable_application_models(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MEMOS_APP_USER_ID", "alice")
    monkeypatch.setenv("MEMOS_APP_CUBE_ID", "daily")
    calls: list[tuple[object, ...]] = []

    class FakeStore:
        path = tmp_path / "topics.json"

        def list_all_topics(self, **kwargs):
            calls.append(("topics", kwargs["user_id"], kwargs["cube_id"]))
            return [
                {
                    "topic_id": "topic-1",
                    "topic_key": "python",
                    "topic_text": "用户正在学习 Python。",
                    "reason_summary": "两条近期记忆提供了证据。",
                    "supporting_memory_ids": ["memory-1"],
                    "reason_evidence": [],
                    "candidate_reasons": [],
                    "score_breakdown": {"base_score": 80},
                    "rank_score": 80,
                    "progress_status": "ongoing",
                    "lifecycle_status": "active",
                    "first_seen_at": "2026-08-20T10:00:00+08:00",
                    "last_evidence_at": "2026-08-24T10:00:00+08:00",
                    "version": 2,
                    "updated_at": "2026-08-24T10:00:00+08:00",
                    "versions": [],
                }
            ]

    class FakeClient:
        def health(self):
            return {"status": "healthy", "version": "1.2.3"}

        def get_memory_dashboard(self, *, user_id: str, cube_id: str):
            calls.append(("dashboard", user_id, cube_id))
            return {
                "data": {
                    "text_mem": [
                        {
                            "memories": [
                                {
                                    "id": "memory-1",
                                    "memory": "2026年8月24日，用户学习了 Python。",
                                    "metadata": {
                                        "key": "学习 Python",
                                        "memory_type": "LongTermMemory",
                                        "created_at": "2026-08-24T10:00:00+08:00",
                                        "tags": ["Python", "学习"],
                                        "embedding": [0.1, 0.2],
                                        "info": {
                                            "record_type": "event",
                                            "event_title": "学习 Python",
                                            "source_type": "local_video",
                                            "source_path": "D:/private/video.mp4",
                                            "media_uri": "oss://private/video.mp4",
                                        },
                                    },
                                }
                            ]
                        }
                    ],
                    "pref_mem": [],
                    "tool_mem": [],
                    "skill_mem": [],
                    "statistics": {
                        "total_text_nodes": 1,
                        "total_preference_nodes": 0,
                        "total_skill_nodes": 0,
                    },
                }
            }

        def get_scheduler_status(self):
            return {"data": {"scheduler_summary": {"total": 3, "in_progress": 1, "waiting": 2}}}

        def get_task_queue_status(self, *, user_id: str):
            calls.append(("queue", user_id))
            return {"data": {"status": "ok"}}

    app = memos_frontend_api.create_app(
        store_factory=FakeStore,
        client_factory=lambda base_url: FakeClient(),
        upload_dir=tmp_path / "uploads",
    )
    client = TestClient(app)

    dashboard_response = client.get("/api/v1/dashboard")
    memories_response = client.get("/api/v1/memories")

    assert dashboard_response.status_code == 200
    dashboard = dashboard_response.json()
    assert dashboard["backend_status"] == "online"
    assert dashboard["service_version"] == "1.2.3"
    assert dashboard["scope"] == {"user_id": "alice", "cube_id": "daily"}
    assert dashboard["counts"] == {
        "memories": 1,
        "preferences": 0,
        "skills": 0,
        "queue_total": 3,
        "queue_running": 1,
        "queue_waiting": 2,
        "active_topics": 1,
    }
    assert dashboard["topics"][0]["title"] == "用户正在学习 Python。"
    assert dashboard["memories"][0]["id"] == "memory-1"

    assert memories_response.status_code == 200
    memories = memories_response.json()
    assert memories["total"] == 1
    assert memories["items"] == [
        {
            "id": "memory-1",
            "title": "学习 Python",
            "content": "2026年8月24日，用户学习了 Python。",
            "memory_type": "LongTermMemory",
            "source": "video",
            "category": "media",
            "created_at": "2026-08-24T10:00:00+08:00",
            "updated_at": None,
            "tags": ["Python", "学习"],
        }
    ]
    serialized = memories_response.text.lower()
    assert "text_mem" not in serialized
    assert "embedding" not in serialized
    assert "source_path" not in serialized
    assert "media_uri" not in serialized
    assert calls.count(("dashboard", "alice", "daily")) == 2


def test_v1_topic_trace_exposes_saved_scoring_process(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MEMOS_APP_USER_ID", "alice")
    monkeypatch.setenv("MEMOS_APP_CUBE_ID", "daily")
    monkeypatch.setenv("MEMOS_TOPIC_DAILY_LIMIT", "7")
    calls: list[tuple[object, ...]] = []

    class FakeStore:
        path = tmp_path / "topics.json"

        def topic_selection_trace(self, **kwargs):
            calls.append(("trace", kwargs))
            if kwargs["topic_id"] == "missing-topic":
                return None
            return {
                "topic_id": "topic-1",
                "topic_key": "python",
                "available": True,
                "unavailable_reason": None,
                "selection_version": 2,
                "policy": {
                    "topic_threshold": 60.0,
                    "supporting_weight": 0.5,
                    "seat_limit": kwargs["seat_limit"],
                    "memory_formula": "min(100, 维度分合计) × 置信系数",
                    "topic_formula": "最强单条 + 其他非重复记忆 × 0.5",
                    "rank_formula": "Topic 基础分 × 新鲜系数",
                    "rubric": [],
                },
                "grouping": {
                    "topic_kind": "event",
                    "reason": "同一次学习任务。",
                    "candidate_tag_keys": ["python"],
                    "memory_ids": ["memory-1"],
                },
                "decision": {
                    "qualifies": True,
                    "base_score": 80.0,
                    "recency_factor": 0.9,
                    "rank_score": 72.0,
                    "rank_position": 1,
                    "seat_status": "active",
                    "candidate_reasons": ["单条记忆达到门槛"],
                },
                "memories": [],
            }

    app = memos_frontend_api.create_app(
        store_factory=FakeStore,
        client_factory=lambda base_url: SimpleNamespace(health=lambda: {"status": "healthy"}),
        upload_dir=tmp_path / "uploads",
    )
    client = TestClient(app)

    response = client.get("/api/v1/topics/topic-1/trace")
    missing_response = client.get("/api/v1/topics/missing-topic/trace")

    assert response.status_code == 200
    assert response.json()["decision"]["rank_score"] == 72.0
    assert response.json()["policy"]["seat_limit"] == 7
    assert missing_response.status_code == 404
    assert calls == [
        (
            "trace",
            {
                "user_id": "alice",
                "cube_id": "daily",
                "topic_id": "topic-1",
                "seat_limit": 7,
            },
        ),
        (
            "trace",
            {
                "user_id": "alice",
                "cube_id": "daily",
                "topic_id": "missing-topic",
                "seat_limit": 7,
            },
        ),
    ]


def test_v1_memory_detail_delete_and_search_keep_workflow_in_application_backend(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("MEMOS_APP_USER_ID", "alice")
    monkeypatch.setenv("MEMOS_APP_CUBE_ID", "daily")
    monkeypatch.setenv("MEMOS_TOPIC_DAILY_LIMIT", "17")
    calls: list[tuple[object, ...]] = []

    class FakeStore:
        path = tmp_path / "topics.json"

        def list_all_topics(self, **kwargs):
            return []

    class FakeClient:
        def get_memory(self, memory_id: str):
            calls.append(("detail", memory_id))
            return {
                "data": {
                    "id": memory_id,
                    "memory": "用户在图书馆学习 Python。",
                    "metadata": {
                        "key": "学习 Python",
                        "memory_type": "LongTermMemory",
                        "confidence": 0.9,
                        "background": "学习记录",
                        "tags": ["Python"],
                        "embedding": [0.1],
                        "info": {
                            "record_type": "event",
                            "event_location": "图书馆",
                            "participants": ["用户"],
                            "source_path": "D:/private/file.md",
                        },
                    },
                }
            }

        def delete_memory(self, memory_id: str):
            calls.append(("delete", memory_id))
            return {"data": {"status": "success"}}

        def search(self, *, user_id: str, cube_id: str, query: str):
            calls.append(("search", user_id, cube_id, query))
            return {
                "data": {
                    "text_mem": [
                        {
                            "id": "memory-7",
                            "memory": "用户正在学习 Python。",
                            "score": 0.88,
                            "metadata": {"key": "Python 学习", "embedding": [0.2]},
                        }
                    ]
                }
            }

    def fake_reconciler(*, base_url: str, daily_limit: int) -> int:
        calls.append(("reconcile", base_url, daily_limit))
        return 1

    app = memos_frontend_api.create_app(
        store_factory=FakeStore,
        client_factory=lambda base_url: FakeClient(),
        reconciler=fake_reconciler,
        upload_dir=tmp_path / "uploads",
    )
    client = TestClient(app)

    detail_response = client.get("/api/v1/memories/memory-3")
    delete_response = client.delete("/api/v1/memories/memory-3")
    search_response = client.post("/api/v1/search", json={"query": "Python"})

    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["memory"]["id"] == "memory-3"
    assert detail["memory"]["structured"]["event_location"] == "图书馆"
    assert "embedding" not in detail_response.text.lower()
    assert "source_path" not in detail_response.text.lower()

    assert delete_response.status_code == 200
    assert delete_response.json() == {
        "ok": True,
        "memory_id": "memory-3",
        "topic_sync": "updated",
        "removed_topic_memories": 1,
    }

    assert search_response.status_code == 200
    assert search_response.json() == {
        "results": [
            {
                "id": "memory-7",
                "title": "Python 学习",
                "content": "用户正在学习 Python。",
                "memory_type": "unknown",
                "source": "direct",
                "category": "other",
                "created_at": None,
                "updated_at": None,
                "tags": [],
                "score": 0.88,
            }
        ],
        "total": 1,
    }
    assert calls == [
        ("detail", "memory-3"),
        ("delete", "memory-3"),
        ("reconcile", "http://127.0.0.1:8000", 17),
        ("search", "alice", "daily", "Python"),
    ]


def test_v1_file_ingestion_uses_backend_scope_and_hides_internal_paths(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MEMOS_APP_USER_ID", "alice")
    monkeypatch.setenv("MEMOS_APP_CUBE_ID", "daily")
    calls: list[tuple[object, ...]] = []

    class FakeStore:
        path = tmp_path / "topics.json"

        def list_all_topics(self, **kwargs):
            return []

    class FakeClient:
        def __init__(self):
            self.last_topic_update = {"processed_memories": 2, "topics": [{}, {}]}
            self.last_topic_error = None

    def fake_importer(client, **kwargs):
        calls.append((kwargs["user_id"], kwargs["cube_id"], kwargs["instruction"]))
        return SimpleNamespace(kind="text"), {"data": [{"id": "memory-1"}, {"id": "memory-2"}]}

    app = memos_frontend_api.create_app(
        store_factory=FakeStore,
        client_factory=lambda base_url: FakeClient(),
        importer=fake_importer,
        upload_dir=tmp_path / "uploads",
    )
    response = TestClient(app).post(
        "/api/v1/ingestions",
        data={"instruction": "按时间提取"},
        files={"file": ("timeline.md", "# 时间流".encode(), "text/markdown")},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "kind": "text",
        "filename": "timeline.md",
        "file_size": len("# 时间流".encode()),
        "memories_created": 2,
        "topic": {"processed_memories": 2, "active_topics": 2, "error": None},
    }
    assert calls == [("alice", "daily", "按时间提取")]
    assert "stored_path" not in response.text
    assert "memos_result" not in response.text
