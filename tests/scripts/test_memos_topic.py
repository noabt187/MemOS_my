from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock


SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "memos_topic.py"
SPEC = importlib.util.spec_from_file_location("memos_topic", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
memos_topic = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = memos_topic
SPEC.loader.exec_module(memos_topic)


def test_extract_added_memories_keeps_every_memory_from_one_import():
    response = {
        "code": 200,
        "data": [
            {
                "memory_id": "memory-1",
                "memory": "用户查看了考试安排。",
                "memory_type": "UserMemory",
                "cube_id": "cube-1",
            },
            {
                "memory_id": "memory-2",
                "memory": "用户制定了复习计划。",
                "memory_type": "LongTermMemory",
                "cube_id": "cube-1",
            },
        ],
    }

    memories = memos_topic.extract_added_memories(response)

    assert [item["memory_id"] for item in memories] == ["memory-1", "memory-2"]


def test_default_store_is_json_not_database(monkeypatch):
    monkeypatch.delenv("MEMOS_TOPIC_STATE", raising=False)
    monkeypatch.delenv("MEMOS_TOPIC_DB", raising=False)

    assert memos_topic.default_store_path().name == "topics.json"


def test_reconcile_runtime_topics_builds_the_existing_processor(monkeypatch, tmp_path: Path):
    processor = SimpleNamespace(reconcile=MagicMock(return_value=3))
    processor_factory = MagicMock(return_value=processor)
    monkeypatch.setattr(memos_topic, "_load_project_env", lambda: None)
    monkeypatch.setattr(memos_topic, "default_store_path", lambda: tmp_path / "topics.json")
    monkeypatch.setattr(memos_topic, "TopicProcessor", processor_factory)
    monkeypatch.setattr(memos_topic, "TopicStore", MagicMock(return_value="store"))
    monkeypatch.setattr(memos_topic, "MemOSMemoryClient", MagicMock(return_value="client"))
    monkeypatch.setattr(memos_topic, "TopicLLM", MagicMock(return_value="llm"))
    monkeypatch.setattr(
        memos_topic.TopicModelConfig,
        "from_env",
        MagicMock(return_value="config"),
    )

    removed = memos_topic.reconcile_runtime_topics(
        base_url="http://127.0.0.1:8000",
        daily_limit=15,
    )

    assert removed == 3
    processor_factory.assert_called_once_with(
        store="store",
        memos_client="client",
        llm="llm",
        daily_limit=15,
    )
    processor.reconcile.assert_called_once_with()


def test_memory_client_falls_back_to_single_get_when_batch_endpoint_misses_memory():
    batch_response = MagicMock()
    batch_response.read.return_value = json.dumps({"code": 200, "data": {"memories": []}}).encode(
        "utf-8"
    )
    batch_response.__enter__.return_value = batch_response
    single_response = MagicMock()
    single_response.read.return_value = json.dumps(
        {
            "code": 200,
            "data": {
                "id": "memory-1",
                "memory": "用户制定了视频解析器开发计划。",
                "metadata": {"info": {"record_type": "event"}},
            },
        }
    ).encode("utf-8")
    single_response.__enter__.return_value = single_response
    client = memos_topic.MemOSMemoryClient("http://127.0.0.1:8000")
    client.opener.open = MagicMock(side_effect=[batch_response, single_response])

    memories = client.get_by_ids(["memory-1"])

    assert [item["id"] for item in memories] == ["memory-1"]
    assert client.opener.open.call_count == 2
    assert (
        client.opener.open.call_args_list[1]
        .args[0]
        .full_url.endswith("/product/get_memory/memory-1")
    )


def test_candidate_metrics_deduplicate_continuous_evidence_units():
    evidence = [
        memos_topic.TagEvidence(
            memory_id="memory-1",
            topic_key="final_exam",
            tag_name="期末考试",
            relationship="direct",
            initiative_type="acting",
            reason="用户查看了考试安排。",
            evidence_unit="series-1:10:00",
            observed_at="2026-08-19T10:00:00+08:00",
            event_time=None,
            event_status="ongoing",
        ),
        memos_topic.TagEvidence(
            memory_id="memory-2",
            topic_key="final_exam",
            tag_name="期末考试",
            relationship="direct",
            initiative_type="acting",
            reason="同一连续截图再次显示复习页面。",
            evidence_unit="series-1:10:00",
            observed_at="2026-08-19T10:15:00+08:00",
            event_time=None,
            event_status="ongoing",
        ),
        memos_topic.TagEvidence(
            memory_id="memory-3",
            topic_key="final_exam",
            tag_name="期末考试",
            relationship="direct",
            initiative_type="acting",
            reason="用户稍后制定了复习计划。",
            evidence_unit="memory-3",
            observed_at="2026-08-19T14:00:00+08:00",
            event_time=None,
            event_status="ongoing",
        ),
    ]

    metrics = memos_topic.compute_candidate_metrics(
        evidence,
        now=datetime(2026, 8, 19, 15, 0, tzinfo=timezone.utc),
    )

    assert metrics.evidence_count == 2
    assert metrics.relationship_vote_sum == 2.0
    assert metrics.qualifies is True
    assert set(metrics.supporting_memory_ids) == {"memory-1", "memory-2", "memory-3"}


def test_one_active_user_initiated_memory_can_become_candidate():
    evidence = [
        memos_topic.TagEvidence(
            memory_id="memory-1",
            topic_key="graduation_defense",
            tag_name="毕业答辩",
            relationship="direct",
            initiative_type="initiated",
            reason="记忆明确说明明天进行毕业答辩。",
            evidence_unit="memory-1",
            observed_at="2026-08-19T10:00:00+08:00",
            event_time="2026-08-20T10:00:00+08:00",
            event_status="planned",
        )
    ]

    metrics = memos_topic.compute_candidate_metrics(evidence)

    assert metrics.evidence_count == 1
    assert metrics.qualifies is True


def test_rank_formula_uses_visible_base_and_recency():
    score = memos_topic.calculate_rank_score(
        base_score=80,
        recency_factor=0.75,
    )

    assert score == 60.0


def test_topic_draft_requires_memory_level_reason_evidence():
    raw = {
        "topic_text": "用户近期正在集中准备期末考试。",
        "reason_summary": "考试临近，并且用户持续复习。",
        "reason_evidence": [
            {
                "memory_id": "memory-1",
                "fact": "用户查看了考试时间和考场。",
                "contribution": "证明考试安排已经明确。",
            },
            {
                "memory_id": "memory-2",
                "fact": "用户制定了三天复习计划。",
                "contribution": "证明用户正在持续准备。",
            },
        ],
    }

    draft = memos_topic.parse_topic_draft(raw, {"memory-1", "memory-2"})

    assert draft.reason_summary == "考试临近，并且用户持续复习。"
    assert [item.memory_id for item in draft.reason_evidence] == ["memory-1", "memory-2"]


def test_topic_draft_rejects_reason_without_memory_id():
    raw = {
        "topic_text": "用户近期正在集中准备期末考试。",
        "reason_summary": "多条记忆显示考试临近。",
        "reason_evidence": [],
    }

    try:
        memos_topic.parse_topic_draft(raw, {"memory-1"})
    except ValueError as exc:
        assert "reason_evidence" in str(exc)
    else:
        raise AssertionError("topic reason without evidence should fail")


def test_deactivate_memory_removes_its_topic_vote(tmp_path: Path):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    memory = {
        "id": "memory-1",
        "memory": "用户查看了考试安排。",
        "metadata": {
            "created_at": "2026-08-19T10:00:00+08:00",
            "info": {
                "record_type": "event",
                "source_recorded_at": "2026-08-19T10:00:00+08:00",
            },
        },
    }
    evidence = memos_topic.TagEvidence(
        memory_id="memory-1",
        topic_key="final_exam",
        tag_name="期末考试",
        relationship="direct",
        initiative_type="acting",
        reason="记忆明确提到考试安排。",
        evidence_unit="memory-1",
        observed_at="2026-08-19T10:00:00+08:00",
        event_time=None,
        event_status="ongoing",
    )
    store.save_memory(user_id="user-1", cube_id="cube-1", memory=memory)
    store.replace_tags(
        user_id="user-1",
        cube_id="cube-1",
        memory_id="memory-1",
        evidence=[evidence],
    )

    affected = store.deactivate_memory("memory-1")

    assert affected == [("user-1", "cube-1", "final_exam")]
    assert (
        store.evidence_for_topic(
            user_id="user-1",
            cube_id="cube-1",
            topic_date="2026-08-19",
            topic_key="final_exam",
        )
        == []
    )


def test_processor_creates_topic_with_traceable_reason(tmp_path: Path):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    response = {
        "code": 200,
        "data": [
            {"memory_id": "memory-1", "memory": "用户查看了考试安排。"},
            {"memory_id": "memory-2", "memory": "用户制定了复习计划。"},
        ],
    }
    memories = [
        {
            "id": "memory-1",
            "memory": "用户查看了考试安排。",
            "metadata": {
                "status": "activated",
                "created_at": "2026-08-19T10:00:00+08:00",
                "info": {
                    "record_type": "event",
                    "source_recorded_at": "2026-08-19T10:00:00+08:00",
                },
            },
        },
        {
            "id": "memory-2",
            "memory": "用户制定了复习计划。",
            "metadata": {
                "status": "activated",
                "created_at": "2026-08-19T11:00:00+08:00",
                "info": {
                    "record_type": "event",
                    "source_recorded_at": "2026-08-19T11:00:00+08:00",
                },
            },
        },
    ]

    class FakeLLM:
        def extract_tags(self, memory, catalog):
            return {
                "tags": [
                    {
                        "topic_key": "final_exam",
                        "tag_name": "期末考试",
                        "relationship": "direct",
                        "initiative_type": "acting",
                        "reason": "记忆与期末考试直接相关。",
                    }
                ]
            }

        def generate_topic(self, *, topic_key, evidence, memories, metrics):
            return {
                "topic_text": "用户近期正在集中准备期末考试。",
                "reason_summary": "考试安排明确，并且用户已经开始复习。",
                "reason_evidence": [
                    {
                        "memory_id": "memory-1",
                        "fact": "用户查看了考试安排。",
                        "contribution": "证明考试安排已经进入用户关注范围。",
                    },
                    {
                        "memory_id": "memory-2",
                        "fact": "用户制定了复习计划。",
                        "contribution": "证明用户正在采取复习行动。",
                    },
                ],
            }

    processor = memos_topic.TopicProcessor(
        store=store,
        memos_client=SimpleNamespace(get_by_ids=lambda memory_ids: memories),
        llm=FakeLLM(),
    )

    assert (
        processor.process_added_response(
            response=response,
            user_id="user-1",
            cube_id="cube-1",
        )
        == 2
    )
    topics = store.list_topics(user_id="user-1", cube_id="cube-1")
    assert len(topics) == 1
    assert topics[0]["topic_text"] == "用户近期正在集中准备期末考试。"
    assert [item["memory_id"] for item in topics[0]["reason_evidence"]] == [
        "memory-1",
        "memory-2",
    ]


def test_list_all_topics_returns_rolling_active_and_suppressed_items(tmp_path: Path):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    metrics = memos_topic.CandidateMetrics(
        evidence_count=2,
        relationship_vote_sum=2.0,
        qualifies=True,
        supporting_memory_ids=["memory-1"],
        latest_evidence_at="2026-08-20T10:00:00+08:00",
        score_breakdown={
            "evidence_points": 20.0,
            "initiative_points": 18.75,
            "urgency_points": 0.0,
            "continuity_points": 5.0,
            "status_points": 10.0,
            "base_score": 53.75,
            "recency_factor": 1.0,
            "rank_score": 53.75,
        },
        candidate_reasons=["有两个独立事件"],
    )

    def add_topic(topic_date: str, topic_key: str, rank_score: float) -> None:
        store.upsert_topic(
            user_id="user-1",
            cube_id="cube-1",
            topic_date=topic_date,
            topic_key=topic_key,
            draft=memos_topic.TopicDraft(
                topic_text=f"主题 {topic_key}",
                reason_summary="有明确记忆证据。",
                reason_evidence=[
                    memos_topic.ReasonEvidence(
                        memory_id="memory-1",
                        fact="用户执行了一项活动。",
                        contribution="支持该主题。",
                    )
                ],
            ),
            metrics=metrics,
            rank_score=rank_score,
        )

    add_topic("2026-08-19", "older", 0.7)
    add_topic("2026-08-20", "top", 0.9)
    add_topic("2026-08-20", "suppressed", 0.5)
    store.rebalance(user_id="user-1", cube_id="cube-1", limit=1)

    visible = store.list_all_topics(user_id="user-1", cube_id="cube-1")
    complete = store.list_all_topics(
        user_id="user-1",
        cube_id="cube-1",
        include_suppressed=True,
    )

    assert [item["topic_key"] for item in visible] == ["top"]
    assert [item["topic_key"] for item in complete] == ["top", "older", "suppressed"]


def test_prompts_only_ask_model_for_discrete_judgements():
    assert '"relationship"' in memos_topic.TAG_PROMPT
    assert '"initiative_type"' in memos_topic.TAG_PROMPT
    assert '"relevance"' not in memos_topic.TAG_PROMPT
    assert '"importance_score"' not in memos_topic.TOPIC_PROMPT
    assert '"urgency_score"' not in memos_topic.TOPIC_PROMPT
    assert '"execution_score"' not in memos_topic.TOPIC_PROMPT


def test_deterministic_candidate_score_has_visible_breakdown():
    evidence = [
        memos_topic.TagEvidence(
            memory_id="memory-1",
            topic_key="final_exam",
            tag_name="期末考试",
            relationship="direct",
            initiative_type="initiated",
            reason="用户明确制定了复习计划。",
            evidence_unit="event-1",
            observed_at="2026-08-19T10:00:00+08:00",
            event_time="2026-08-21T09:00:00+08:00",
            event_status="planned",
        ),
        memos_topic.TagEvidence(
            memory_id="memory-2",
            topic_key="final_exam",
            tag_name="期末考试",
            relationship="direct",
            initiative_type="acting",
            reason="用户开始复习高数。",
            evidence_unit="event-2",
            observed_at="2026-08-20T09:00:00+08:00",
            event_time="2026-08-21T09:00:00+08:00",
            event_status="ongoing",
        ),
    ]

    metrics = memos_topic.compute_candidate_metrics(
        evidence,
        now=datetime.fromisoformat("2026-08-20T10:00:00+08:00"),
    )

    assert metrics.evidence_count == 2
    assert metrics.qualifies is True
    assert metrics.score_breakdown == {
        "evidence_points": 20.0,
        "initiative_points": 25.0,
        "urgency_points": 20.0,
        "continuity_points": 10.0,
        "status_points": 10.0,
        "base_score": 85.0,
        "recency_factor": 1.0,
        "rank_score": 85.0,
    }


def test_single_passive_memory_is_not_candidate_but_active_plan_is():
    passive = memos_topic.TagEvidence(
        memory_id="memory-1",
        topic_key="coffee",
        tag_name="咖啡",
        relationship="direct",
        initiative_type="observed",
        reason="截图里出现了一杯咖啡。",
        evidence_unit="event-1",
        observed_at="2026-08-20T09:00:00+08:00",
        event_time=None,
        event_status="completed",
    )
    active = memos_topic.TagEvidence(
        memory_id="memory-2",
        topic_key="final_exam",
        tag_name="期末考试",
        relationship="direct",
        initiative_type="initiated",
        reason="用户制定了期末考试复习计划。",
        evidence_unit="event-2",
        observed_at="2026-08-20T09:00:00+08:00",
        event_time=None,
        event_status="planned",
    )

    assert memos_topic.compute_candidate_metrics([passive]).qualifies is False
    assert memos_topic.compute_candidate_metrics([active]).qualifies is True


def test_json_store_keeps_yesterday_topic_and_rolls_only_top_fifteen(tmp_path: Path):
    state_path = tmp_path / "topics.json"
    store = memos_topic.TopicStore(state_path)

    def add_topic(topic_key: str, rank_score: float, observed_at: str) -> None:
        metrics = memos_topic.CandidateMetrics(
            evidence_count=2,
            relationship_vote_sum=2.0,
            qualifies=True,
            supporting_memory_ids=[f"memory-{topic_key}"],
            latest_evidence_at=observed_at,
            score_breakdown={
                "evidence_points": rank_score,
                "initiative_points": 0.0,
                "urgency_points": 0.0,
                "continuity_points": 0.0,
                "status_points": 0.0,
                "base_score": rank_score,
                "recency_factor": 1.0,
                "rank_score": rank_score,
            },
            candidate_reasons=["测试候选"],
        )
        store.upsert_topic(
            user_id="user-1",
            cube_id="cube-1",
            topic_key=topic_key,
            draft=memos_topic.TopicDraft(
                topic_text=f"主题 {topic_key}",
                reason_summary="有明确记忆证据。",
                reason_evidence=[
                    memos_topic.ReasonEvidence(
                        memory_id=f"memory-{topic_key}",
                        fact="用户执行了一项活动。",
                        contribution="支持该主题。",
                    )
                ],
            ),
            metrics=metrics,
        )

    add_topic("yesterday", 10.0, "2026-08-19T20:00:00+08:00")
    store.rebalance(user_id="user-1", cube_id="cube-1", limit=15)

    assert [
        item["topic_key"] for item in store.list_topics(user_id="user-1", cube_id="cube-1")
    ] == ["yesterday"]

    for index in range(15):
        add_topic(f"today_{index:02d}", 100.0 - index, "2026-08-20T10:00:00+08:00")
    store.rebalance(user_id="user-1", cube_id="cube-1", limit=15)

    visible = store.list_topics(user_id="user-1", cube_id="cube-1")
    complete = store.list_topics(user_id="user-1", cube_id="cube-1", include_suppressed=True)
    assert len(visible) == 15
    assert "yesterday" not in {item["topic_key"] for item in visible}
    assert (
        next(item for item in complete if item["topic_key"] == "yesterday")["lifecycle_status"]
        == "suppressed"
    )
    assert json.loads(state_path.read_text(encoding="utf-8"))["schema_version"] == 1
    assert not state_path.read_bytes().startswith(b"SQLite format 3")


def test_topic_snapshot_keeps_only_minimal_memory_fields_without_base64(tmp_path: Path):
    state_path = tmp_path / "topics.json"
    store = memos_topic.TopicStore(state_path)
    inline_image = "data:image/png;base64,SECRET_IMAGE_DATA"
    memory = {
        "id": "memory-1",
        "memory": "用户查看了一张活动照片。",
        "metadata": {
            "status": "activated",
            "created_at": "2026-08-20T10:00:00+08:00",
            "info": {
                "record_type": "event",
                "event_status": "completed",
                "source_recorded_at": "2026-08-20T09:59:00+08:00",
            },
            "sources": [{"content": inline_image, "url": inline_image}],
        },
    }

    store.save_memory(user_id="user-1", cube_id="cube-1", memory=memory)

    serialized = state_path.read_text(encoding="utf-8")
    restored = store.memories_by_ids(["memory-1"])[0]
    assert "SECRET_IMAGE_DATA" not in serialized
    assert "base64," not in serialized.lower()
    assert restored["memory"] == "用户查看了一张活动照片。"
    assert restored["metadata"]["info"]["record_type"] == "event"
    assert "sources" not in restored["metadata"]


def test_migrate_legacy_sqlite_preserves_topics_evidence_and_versions(tmp_path: Path):
    legacy_path = tmp_path / "topics.db"
    state_path = tmp_path / "topics.json"
    memory = {
        "id": "memory-1",
        "memory": "用户制定了期末考试复习计划。",
        "embedding": [0.1, 0.2, 0.3],
        "metadata": {
            "status": "activated",
            "created_at": "2026-08-19T09:00:00+08:00",
            "source_url": "https://example.invalid/private-source",
            "info": {
                "record_type": "event",
                "event_status": "planned",
                "event_start_at": "2026-08-22T09:00:00+08:00",
                "source_recorded_at": "2026-08-19T09:00:00+08:00",
            },
        },
    }
    with sqlite3.connect(legacy_path) as connection:
        connection.executescript(
            """
            CREATE TABLE memory_snapshots (
                memory_id TEXT PRIMARY KEY, user_id TEXT, cube_id TEXT,
                memory_text TEXT, memory_json TEXT, observed_at TEXT,
                active INTEGER, processed_at TEXT
            );
            CREATE TABLE memory_tags (
                memory_id TEXT, user_id TEXT, cube_id TEXT, topic_date TEXT,
                topic_key TEXT, tag_name TEXT, relevance REAL, importance REAL,
                urgency REAL, execution REAL, reason TEXT, evidence_unit TEXT,
                observed_at TEXT, active INTEGER
            );
            CREATE TABLE daily_topics (
                topic_id TEXT PRIMARY KEY, user_id TEXT, cube_id TEXT,
                topic_date TEXT, topic_key TEXT, topic_text TEXT,
                reason_summary TEXT, reason_evidence_json TEXT,
                supporting_memory_ids_json TEXT, importance_score REAL,
                urgency_score REAL, support_score REAL, recency_score REAL,
                execution_score REAL, rank_score REAL, progress_status TEXT,
                lifecycle_status TEXT, version INTEGER, created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE topic_versions (
                topic_id TEXT, version INTEGER, snapshot_json TEXT, created_at TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO memory_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "memory-1",
                "default",
                "default_cube",
                memory["memory"],
                json.dumps(memory, ensure_ascii=False),
                "2026-08-19T09:00:00+08:00",
                1,
                "2026-08-19T09:01:00+08:00",
            ),
        )
        connection.execute(
            "INSERT INTO memory_tags VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "memory-1",
                "default",
                "default_cube",
                "2026-08-19",
                "final_exam",
                "期末考试",
                0.95,
                0.9,
                0.8,
                0.85,
                "用户明确制定了复习计划。",
                "memory-1",
                "2026-08-19T09:00:00+08:00",
                1,
            ),
        )
        reason_evidence = json.dumps(
            [
                {
                    "memory_id": "memory-1",
                    "fact": "用户制定了复习计划。",
                    "contribution": "证明用户主动准备考试。",
                }
            ],
            ensure_ascii=False,
        )
        connection.execute(
            "INSERT INTO daily_topics VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "topic-legacy",
                "default",
                "default_cube",
                "2026-08-19",
                "final_exam",
                "用户正在准备期末考试。",
                "考试临近且用户已经开始行动。",
                reason_evidence,
                json.dumps(["memory-1"]),
                0.9,
                0.8,
                0.8,
                0.9,
                0.85,
                0.84,
                "ongoing",
                "active",
                2,
                "2026-08-19T09:05:00+08:00",
                "2026-08-20T09:05:00+08:00",
            ),
        )
        connection.execute(
            "INSERT INTO topic_versions VALUES (?, ?, ?, ?)",
            (
                "topic-legacy",
                1,
                json.dumps(
                    {"topic_text": "用户开始关注期末考试。", "version": 1},
                    ensure_ascii=False,
                ),
                "2026-08-19T09:05:00+08:00",
            ),
        )

    original_bytes = legacy_path.read_bytes()
    summary = memos_topic.migrate_legacy_sqlite(legacy_path, state_path)

    assert summary == {
        "memories": 1,
        "tags": 1,
        "topics": 1,
        "versions": 1,
        "state_path": str(state_path),
    }
    assert legacy_path.read_bytes() == original_bytes
    store = memos_topic.TopicStore(state_path)
    topic = store.list_all_topics(
        user_id="default", cube_id="default_cube", include_suppressed=True
    )[0]
    assert topic["topic_id"] == "topic-legacy"
    assert topic["topic_key"] == "final_exam"
    assert topic["rank_score"] <= 100
    assert topic["score_breakdown"]["rank_score"] == topic["rank_score"]
    assert topic["candidate_reasons"]
    assert topic["versions"][0]["topic_text"] == "用户开始关注期末考试。"
    migrated_state = json.loads(state_path.read_text(encoding="utf-8"))
    migrated_memory = next(iter(migrated_state["scopes"].values()))["memories"]["memory-1"][
        "memory"
    ]
    assert set(migrated_memory) == {"id", "memory", "memory_type", "metadata"}
    assert set(migrated_memory["metadata"]) == {"status", "created_at", "info"}
    evidence = store.evidence_for_topic(
        user_id="default", cube_id="default_cube", topic_key="final_exam"
    )
    assert evidence[0].relationship == "direct"
    assert evidence[0].initiative_type == "acting"
    assert evidence[0].event_status == "planned"
    assert evidence[0].event_time == "2026-08-22T09:00:00+08:00"


def test_memory_client_lists_text_memories_from_dashboard():
    response = MagicMock()
    response.read.return_value = json.dumps(
        {
            "code": 200,
            "data": {
                "text_mem": [
                    {
                        "cube_id": "cube-1",
                        "memories": [
                            {
                                "id": "memory-1",
                                "memory": "用户制定了复习计划。",
                                "metadata": {
                                    "status": "activated",
                                    "info": {"record_type": "event"},
                                },
                            }
                        ],
                    }
                ],
                "statistics": {"total_text_nodes": 1},
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")
    response.__enter__.return_value = response
    client = memos_topic.MemOSMemoryClient("http://127.0.0.1:8000")
    client.opener.open = MagicMock(return_value=response)

    memories = client.list_memories(user_id="user-1", cube_id="cube-1")

    assert [item["id"] for item in memories] == ["memory-1"]
    request = client.opener.open.call_args.args[0]
    assert request.full_url.endswith("/product/get_memory_dashboard")
    assert json.loads(request.data) == {
        "mem_cube_id": "cube-1",
        "user_id": "user-1",
        "include_preference": False,
        "include_tool_memory": False,
        "include_skill_memory": False,
        "page": None,
        "page_size": None,
    }


def test_backfill_selects_only_unprocessed_events_from_requested_batch(tmp_path: Path):
    def event_memory(memory_id: str, batch_id: str) -> dict:
        return {
            "id": memory_id,
            "memory": f"事件 {memory_id}",
            "metadata": {
                "status": "activated",
                "created_at": "2026-08-24T09:00:00+08:00",
                "info": {
                    "record_type": "event",
                    "ingest_batch_id": batch_id,
                    "event_status": "ongoing",
                },
            },
        }

    already_processed = event_memory("memory-1", "batch-current")
    pending = event_memory("memory-2", "batch-current")
    other_batch = event_memory("memory-3", "batch-other")
    non_event = event_memory("memory-4", "batch-current")
    non_event["metadata"]["info"]["record_type"] = "note"
    all_memories = [already_processed, pending, other_batch, non_event]

    class FakeClient:
        def __init__(self):
            self.requested_ids = []

        def list_memories(self, *, user_id, cube_id):
            assert (user_id, cube_id) == ("user-1", "cube-1")
            return all_memories

        def get_by_ids(self, memory_ids):
            self.requested_ids.extend(memory_ids)
            wanted = set(memory_ids)
            return [item for item in all_memories if item["id"] in wanted]

    class FakeLLM:
        def extract_tags(self, memory, catalog):
            return {"tags": []}

    store = memos_topic.TopicStore(tmp_path / "topics.json")
    store.save_memory(user_id="user-1", cube_id="cube-1", memory=already_processed)
    store.replace_tags(
        user_id="user-1",
        cube_id="cube-1",
        memory_id="memory-1",
        evidence=[],
    )
    client = FakeClient()
    processor = memos_topic.TopicProcessor(store=store, memos_client=client, llm=FakeLLM())

    result = processor.backfill(
        user_id="user-1",
        cube_id="cube-1",
        ingest_batch_id="batch-current",
    )

    assert result.selected_memories == 2
    assert result.pending_memories == 1
    assert result.processed_memories == 1
    assert client.requested_ids == ["memory-2"]
    assert store.tagged_memory_ids("user-1", "cube-1") == {"memory-1", "memory-2"}


def test_backfill_rebuilds_missing_topic_after_all_tags_were_saved(tmp_path: Path):
    memory = {
        "id": "memory-1",
        "memory": "用户主动制定了考试复习计划。",
        "metadata": {
            "status": "activated",
            "created_at": "2026-08-24T09:00:00+08:00",
            "info": {
                "record_type": "event",
                "ingest_batch_id": "batch-current",
                "event_status": "planned",
            },
        },
    }
    evidence = memos_topic.TagEvidence(
        memory_id="memory-1",
        topic_key="final_exam",
        tag_name="期末考试",
        relationship="direct",
        initiative_type="initiated",
        reason="用户主动制定了考试复习计划。",
        evidence_unit="memory-1",
        observed_at="2026-08-24T09:00:00+08:00",
        event_time=None,
        event_status="planned",
    )

    class FakeLLM:
        def generate_topic(self, *, topic_key, evidence, memories, metrics):
            return {
                "topic_text": "用户正在主动准备期末考试。",
                "reason_summary": "用户已经制定了明确的复习计划。",
                "reason_evidence": [
                    {
                        "memory_id": "memory-1",
                        "fact": "用户制定了考试复习计划。",
                        "contribution": "证明用户主动开始准备考试。",
                    }
                ],
            }

    store = memos_topic.TopicStore(tmp_path / "topics.json")
    store.save_memory(user_id="user-1", cube_id="cube-1", memory=memory)
    store.replace_tags(
        user_id="user-1",
        cube_id="cube-1",
        memory_id="memory-1",
        evidence=[evidence],
    )
    client = SimpleNamespace(
        list_memories=lambda **kwargs: [memory],
        get_by_ids=lambda memory_ids: [],
    )
    processor = memos_topic.TopicProcessor(store=store, memos_client=client, llm=FakeLLM())

    result = processor.backfill(
        user_id="user-1",
        cube_id="cube-1",
        ingest_batch_id="batch-current",
    )

    assert result.pending_memories == 0
    assert result.processed_memories == 0
    topics = store.list_topics(user_id="user-1", cube_id="cube-1")
    assert [item["topic_key"] for item in topics] == ["final_exam"]
