from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import threading
import time
import urllib.error

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


SCRIPTS_DIR = Path(__file__).parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
SCRIPT_PATH = SCRIPTS_DIR / "memos_topic.py"
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
        daily_limit=3,
        policy=memos_topic.DEFAULT_TOPIC_QUEUE_POLICY,
    )
    processor.reconcile.assert_called_once_with()


def test_refresh_runtime_topics_by_ids_builds_the_existing_processor(monkeypatch, tmp_path: Path):
    processor = SimpleNamespace(refresh_memory_ids=MagicMock(return_value=1))
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

    processed = memos_topic.refresh_runtime_topics_by_ids(
        base_url="http://127.0.0.1:8000",
        user_id="user-1",
        cube_id="cube-1",
        memory_ids=["memory-1"],
        daily_limit=15,
    )

    assert processed == 1
    processor_factory.assert_called_once_with(
        store="store",
        memos_client="client",
        llm="llm",
        daily_limit=3,
        policy=memos_topic.DEFAULT_TOPIC_QUEUE_POLICY,
    )
    processor.refresh_memory_ids.assert_called_once_with(
        memory_ids=["memory-1"],
        user_id="user-1",
        cube_id="cube-1",
    )


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


def test_memory_client_treats_single_get_404_as_a_missing_memory():
    batch_response = MagicMock()
    batch_response.read.return_value = json.dumps({"code": 200, "data": {"memories": []}}).encode(
        "utf-8"
    )
    batch_response.__enter__.return_value = batch_response
    not_found = urllib.error.HTTPError(
        "http://127.0.0.1:8000/product/get_memory/memory-missing",
        404,
        "Not Found",
        hdrs=None,
        fp=None,
    )
    client = memos_topic.MemOSMemoryClient("http://127.0.0.1:8000")
    client.opener.open = MagicMock(side_effect=[batch_response, not_found])

    assert client.get_by_ids(["memory-missing"]) == []


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


def test_one_active_user_commitment_can_become_candidate():
    for initiative_type in ("initiated", "committed"):
        evidence = [
            memos_topic.TagEvidence(
                memory_id="memory-1",
                topic_key="graduation_defense",
                tag_name="毕业答辩",
                relationship="direct",
                initiative_type=initiative_type,
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


def _assessment(memory_id: str, score: float) -> object:
    return memos_topic.MemoryAssessment(
        memory_id=memory_id,
        eligible=True,
        agency="acting",
        action_requirement="ongoing",
        impact="meaningful",
        explicit_priority="none",
        confidence="high",
        score=score,
        score_breakdown={"memory_score": score},
        reasons={"agency": "测试依据"},
    )


def _legacy_v2_assessment(memory_id: str) -> object:
    return memos_topic.MemoryAssessment(
        memory_id=memory_id,
        eligible=True,
        agency="committed",
        action_requirement="must_do",
        impact="meaningful",
        explicit_priority="none",
        confidence="high",
        score=80,
        score_breakdown={
            "agency_points": 25,
            "action_points": 25,
            "urgency_points": 20,
            "impact_points": 10,
            "priority_points": 0,
            "effort_points": 0,
            "confidence_factor": 1,
            "memory_score": 80,
        },
        reasons={"agency": "用户已经明确承诺处理该事件。"},
        effort="none",
        selection_version=2,
    )


def test_active_selection_data_deterministically_upgrades_v2_assessment(tmp_path: Path):
    memory = {
        "id": "memory-legacy",
        "memory": "用户计划明天参加A公司的技术面试。",
        "metadata": {
            "status": "activated",
            "created_at": "2026-09-01T09:00:00+08:00",
            "info": {
                "record_type": "event",
                "event_status": "planned",
                "event_time": "2026-09-02T10:00:00+08:00",
            },
        },
    }
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    store.save_memory(user_id="user-1", cube_id="cube-1", memory=memory)
    store.replace_tags(
        user_id="user-1",
        cube_id="cube-1",
        memory_id="memory-legacy",
        evidence=[],
    )
    store.replace_assessment(
        user_id="user-1",
        cube_id="cube-1",
        assessment=_legacy_v2_assessment("memory-legacy"),
    )

    memories, assessments, _ = store.active_selection_data("user-1", "cube-1")

    assert [item["id"] for item in memories] == ["memory-legacy"]
    upgraded = assessments["memory-legacy"]
    assert upgraded.selection_version == memos_topic.TOPIC_SELECTION_VERSION
    assert upgraded.score == 60
    assert upgraded.score_breakdown["urgency_points"] == 0
    assert upgraded.score_breakdown["importance_score"] == 60
    assert upgraded.score_breakdown["model"] == "static_importance_v3"
    assert store.assessed_memory_ids("user-1", "cube-1") == {"memory-legacy"}


def test_active_selection_data_leaves_unversioned_assessment_pending_for_backfill(
    tmp_path: Path,
):
    memory = {
        "id": "memory-unversioned",
        "memory": "用户记录了一项旧计划。",
        "metadata": {
            "status": "activated",
            "info": {"record_type": "event", "event_status": "planned"},
        },
    }
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    store.save_memory(user_id="user-1", cube_id="cube-1", memory=memory)
    store.replace_assessment(
        user_id="user-1",
        cube_id="cube-1",
        assessment=_legacy_v2_assessment("memory-unversioned"),
    )
    state = store._read()
    scope = store._scope(state, "user-1", "cube-1")
    assert scope is not None
    scope["assessments"]["memory-unversioned"].pop("selection_version")
    store._write(state)

    memories, assessments, tags = store.active_selection_data("user-1", "cube-1")

    assert memories == []
    assert assessments == {}
    assert tags == {}
    assert store.assessed_memory_ids("user-1", "cube-1") == set()


def test_active_selection_data_rejects_future_assessment_version(tmp_path: Path):
    memory = {
        "id": "memory-future",
        "memory": "用户记录了一项未来计划。",
        "metadata": {
            "status": "activated",
            "info": {"record_type": "event", "event_status": "planned"},
        },
    }
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    store.save_memory(user_id="user-1", cube_id="cube-1", memory=memory)
    assessment = replace(_legacy_v2_assessment("memory-future"), selection_version=4)
    store.replace_assessment(
        user_id="user-1",
        cube_id="cube-1",
        assessment=assessment,
    )

    with pytest.raises(ValueError, match="高于当前代码版本"):
        store.active_selection_data("user-1", "cube-1")


def test_legacy_selection_version_does_not_occupy_the_current_queue(tmp_path: Path):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    topic_id = store.upsert_topic(
        user_id="user-1",
        cube_id="cube-1",
        topic_key="legacy_topic",
        draft=memos_topic.TopicDraft(
            topic_text="旧版本 Topic",
            reason_summary="旧版本测试。",
            reason_evidence=[],
        ),
        metrics=memos_topic.CandidateMetrics(
            evidence_count=1,
            relationship_vote_sum=1,
            qualifies=True,
            supporting_memory_ids=["memory-legacy"],
            latest_evidence_at="2026-09-01T09:00:00+08:00",
            score_breakdown={"importance_score": 80, "base_score": 80},
            candidate_reasons=["旧版本测试"],
            progress_status="planned",
            importance_score=80,
        ),
    )
    state = store._read()
    scope = store._scope(state, "user-1", "cube-1")
    assert scope is not None
    topic = scope["topics"]["legacy_topic"]
    topic["selection_version"] = 2
    topic["lifecycle_status"] = "active"
    topic["queue_rank"] = 1
    store._write(state)

    snapshot = store.list_queue_snapshot(user_id="user-1", cube_id="cube-1")
    result = store.rebalance_queue(
        user_id="user-1",
        cube_id="cube-1",
        now=datetime.fromisoformat("2026-09-01T12:00:00+08:00"),
        mode="scheduled",
    )

    assert store.topic_record(
        user_id="user-1", cube_id="cube-1", topic_key="legacy_topic"
    )["topic_id"] == topic_id
    assert snapshot["items"] == []
    assert snapshot["core_count"] == 0
    assert result.core_topic_ids == []


def test_queue_snapshot_does_not_wait_for_a_long_running_writer_transaction(tmp_path: Path):
    state_path = tmp_path / "topics.json"
    store = memos_topic.TopicStore(state_path)
    acquired = threading.Event()
    release = threading.Event()

    def hold_writer_transaction() -> None:
        with store.transaction():
            acquired.set()
            release.wait(timeout=2)

    writer = threading.Thread(target=hold_writer_transaction, daemon=True)
    writer.start()
    assert acquired.wait(timeout=1)
    safety_release = threading.Timer(0.5, release.set)
    safety_release.start()
    started_at = time.monotonic()
    try:
        snapshot = memos_topic.TopicStore(state_path).list_queue_snapshot(
            user_id="user-1",
            cube_id="cube-1",
        )
    finally:
        release.set()
        writer.join(timeout=1)
        safety_release.cancel()

    assert time.monotonic() - started_at < 0.25
    assert snapshot["items"] == []


def test_rebuild_upgrades_legacy_topic_and_preserves_topic_id_without_reanalysis(
    tmp_path: Path,
):
    memory = {
        "id": "memory-interview",
        "memory": "用户计划于2026年9月2日参加A公司的技术面试。",
        "metadata": {
            "status": "activated",
            "created_at": "2026-09-01T09:00:00+08:00",
            "info": {
                "record_type": "event",
                "event_status": "planned",
                "event_time": "2026-09-02T10:00:00+08:00",
                "source_recorded_at": "2026-09-01T09:00:00+08:00",
            },
        },
    }
    evidence = memos_topic.TagEvidence(
        memory_id="memory-interview",
        topic_key="a_company_interview",
        tag_name="A公司面试",
        relationship="direct",
        initiative_type="committed",
        reason="记忆明确记录了A公司的面试计划。",
        evidence_unit="memory-interview",
        observed_at="2026-09-01T09:00:00+08:00",
        event_time="2026-09-02T10:00:00+08:00",
        event_status="planned",
    )
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    store.save_memory(user_id="user-1", cube_id="cube-1", memory=memory)
    store.replace_tags(
        user_id="user-1",
        cube_id="cube-1",
        memory_id="memory-interview",
        evidence=[evidence],
    )
    store.replace_assessment(
        user_id="user-1",
        cube_id="cube-1",
        assessment=_legacy_v2_assessment("memory-interview"),
    )
    original_topic_id = store.upsert_topic(
        user_id="user-1",
        cube_id="cube-1",
        topic_key="a_company_interview",
        draft=memos_topic.TopicDraft(
            topic_text="用户需要关注A公司的技术面试。",
            reason_summary="用户已经确认面试安排。",
            reason_evidence=[
                memos_topic.ReasonEvidence(
                    memory_id="memory-interview",
                    fact="用户计划参加A公司的技术面试。",
                    contribution="证明该面试尚未完成。",
                )
            ],
        ),
        metrics=memos_topic.CandidateMetrics(
            evidence_count=1,
            relationship_vote_sum=1,
            qualifies=True,
            supporting_memory_ids=["memory-interview"],
            latest_evidence_at="2026-09-01T09:00:00+08:00",
            score_breakdown={
                "importance_score": 80,
                "base_score": 80,
                "counted_memory_ids": ["memory-interview"],
                "memory_scores": {"memory-interview": 80},
            },
            candidate_reasons=["旧版本测试"],
            progress_status="planned",
            importance_score=80,
        ),
    )
    state = store._read()
    scope = store._scope(state, "user-1", "cube-1")
    assert scope is not None
    scope["topics"]["a_company_interview"]["selection_version"] = 2
    store._write(state)

    class FakeLLM:
        def analyze_memory(self, memory, catalog):
            raise AssertionError("v2 的离散判断字段足够迁移，不应重新分析这条记忆")

        def generate_topic(self, *, topic_key, evidence, memories, metrics):
            return {
                "topic_text": "用户需要关注A公司的技术面试。",
                "reason_summary": "用户已经确认面试安排，且面试尚未完成。",
                "reason_evidence": [
                    {
                        "memory_id": "memory-interview",
                        "fact": "用户计划参加A公司的技术面试。",
                        "contribution": "证明该面试尚未完成。",
                    }
                ],
            }

    processor = memos_topic.TopicProcessor(
        store=store,
        memos_client=SimpleNamespace(get_by_ids=lambda memory_ids: []),
        llm=FakeLLM(),
    )

    assert (
        processor.process_memory_ids(memory_ids=[], user_id="user-1", cube_id="cube-1")
        == 0
    )
    updated = store.topic_record(
        user_id="user-1",
        cube_id="cube-1",
        topic_key="a_company_interview",
    )
    assert updated is not None
    assert updated["topic_id"] == original_topic_id
    assert updated["selection_version"] == memos_topic.TOPIC_SELECTION_VERSION
    assert updated["versions"][-1]["selection_version"] == 2


def test_offline_selection_upgrade_restores_v2_topic_without_model_or_memos(
    tmp_path: Path,
):
    memory = {
        "id": "memory-offline",
        "memory": "用户计划于2026年9月2日参加A公司的技术面试。",
        "metadata": {
            "status": "activated",
            "created_at": "2026-09-01T09:00:00+08:00",
            "info": {
                "record_type": "event",
                "event_status": "planned",
                "event_time": "2026-09-02T10:00:00+08:00",
            },
        },
    }
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    store.save_memory(user_id="user-1", cube_id="cube-1", memory=memory)
    store.replace_tags(
        user_id="user-1",
        cube_id="cube-1",
        memory_id="memory-offline",
        evidence=[],
    )
    store.replace_assessment(
        user_id="user-1",
        cube_id="cube-1",
        assessment=_legacy_v2_assessment("memory-offline"),
    )
    original_topic_id = store.upsert_topic(
        user_id="user-1",
        cube_id="cube-1",
        topic_key="offline_interview",
        draft=memos_topic.TopicDraft(
            topic_text="用户需要关注A公司的技术面试。",
            reason_summary="用户已经确认面试安排。",
            reason_evidence=[],
        ),
        metrics=memos_topic.CandidateMetrics(
            evidence_count=1,
            relationship_vote_sum=1,
            qualifies=True,
            supporting_memory_ids=["memory-offline"],
            latest_evidence_at="2026-09-01T09:00:00+08:00",
            score_breakdown={"importance_score": 80, "base_score": 80},
            candidate_reasons=["旧版本测试"],
            progress_status="planned",
            importance_score=80,
        ),
    )
    state = store._read()
    scope = store._scope(state, "user-1", "cube-1")
    assert scope is not None
    scope["topics"]["offline_interview"]["selection_version"] = 2
    store._write(state)

    result = store.upgrade_selection_versions(
        user_id="user-1",
        cube_id="cube-1",
        now=datetime.fromisoformat("2026-09-01T12:00:00+08:00"),
    )

    topic = store.topic_record(
        user_id="user-1", cube_id="cube-1", topic_key="offline_interview"
    )
    assert result["upgraded_assessments"] == 1
    assert result["upgraded_topics"] == 1
    assert topic is not None
    assert topic["topic_id"] == original_topic_id
    assert topic["selection_version"] == memos_topic.TOPIC_SELECTION_VERSION
    assert topic["versions"][-1]["selection_version"] == 2
    assert store.list_queue_snapshot(user_id="user-1", cube_id="cube-1")["core_count"] == 1


def test_memory_importance_is_calculated_from_discrete_model_judgements():
    memory = {
        "id": "memory-interview",
        "memory": "用户已经确认今天晚上七点参加求职面试。",
        "metadata": {
            "status": "activated",
            "created_at": "2026-08-25T09:00:00+08:00",
            "info": {
                "record_type": "event",
                "event_status": "planned",
                "event_start_at": "2026-08-25T19:00:00+08:00",
            },
        },
    }
    raw = {
        "assessment": {
            "eligible": True,
            "agency": "committed",
            "action_requirement": "must_do",
            "impact": "meaningful",
            "explicit_priority": "none",
            "effort": "none",
            "confidence": "high",
            "reasons": {
                "agency": "用户已经确认参加面试。",
                "action_requirement": "面试尚未发生，需要用户出席。",
                "impact": "该事件会影响求职进展。",
                "explicit_priority": "用户没有额外强调。",
            },
        }
    }

    assessment = memos_topic.parse_memory_assessment(
        memory,
        raw,
        now=datetime.fromisoformat("2026-08-25T10:00:00+08:00"),
    )

    assert assessment.score == 60.0
    assert assessment.score_breakdown == {
        "agency_points": 25.0,
        "action_points": 25.0,
        "urgency_points": 0.0,
        "impact_points": 10.0,
        "priority_points": 0.0,
        "effort_points": 0.0,
        "confidence_factor": 1.0,
        "memory_score": 60.0,
        "importance_score": 60.0,
        "model": "static_importance_v3",
    }


def test_topic_metrics_keep_importance_static_as_the_deadline_gets_closer():
    memory = {
        "id": "memory-deadline",
        "memory": "用户计划在8月30日提交材料。",
        "metadata": {
            "created_at": "2026-08-25T09:00:00+08:00",
            "info": {
                "record_type": "event",
                "event_status": "planned",
                "event_end_at": "2026-08-30T10:00:00+08:00",
            },
        },
    }
    assessment = memos_topic.parse_memory_assessment(
        memory,
        {
            "assessment": {
                "eligible": True,
                "agency": "observed",
                "action_requirement": "none",
                "impact": "trivial",
                "explicit_priority": "none",
                "effort": "none",
                "confidence": "high",
                "reasons": {},
            }
        },
        now=datetime.fromisoformat("2026-08-25T10:00:00+08:00"),
    )
    assert assessment.score == 0

    metrics = memos_topic.compute_topic_metrics(
        assessments=[assessment],
        memories=[memory],
        now=datetime.fromisoformat("2026-08-29T12:00:00+08:00"),
        threshold=0,
    )

    assert metrics.score_breakdown["memory_scores"]["memory-deadline"] == 0
    assert metrics.score_breakdown["importance_score"] == 0
    assert metrics.score_breakdown["recency_factor"] == 1
    assert metrics.score_breakdown["rank_score"] == 0


def test_legacy_assessment_removes_old_urgency_without_changing_static_dimensions():
    memory = {
        "id": "memory-legacy",
        "memory": "用户计划明天参加面试。",
        "metadata": {
            "created_at": "2026-08-25T09:00:00+08:00",
            "info": {
                "record_type": "event",
                "event_status": "planned",
                "event_time": "2026-08-26T10:00:00+08:00",
            },
        },
    }
    legacy = memos_topic.MemoryAssessment(
        memory_id="memory-legacy",
        eligible=True,
        agency="committed",
        action_requirement="must_do",
        impact="meaningful",
        explicit_priority="none",
        confidence="high",
        score=80,
        score_breakdown={
            "agency_points": 25,
            "action_points": 25,
            "urgency_points": 20,
            "impact_points": 10,
            "priority_points": 0,
            "effort_points": 0,
            "confidence_factor": 1,
            "memory_score": 80,
        },
        reasons={},
    )

    metrics = memos_topic.compute_topic_metrics(
        assessments=[legacy],
        memories=[memory],
        now=datetime.fromisoformat("2026-08-25T10:00:00+08:00"),
    )

    assert metrics.qualifies is True
    assert metrics.importance_score == 60
    assert metrics.score_breakdown["memory_scores"] == {"memory-legacy": 60}


def test_old_unfinished_event_does_not_keep_full_urgency_forever():
    memory = {
        "id": "memory-old",
        "memory": "用户原计划在六月提交材料。",
        "metadata": {
            "info": {
                "record_type": "event",
                "event_status": "planned",
                "event_end_at": "2026-06-01T10:00:00+08:00",
            }
        },
    }

    assert (
        memos_topic._memory_urgency_points(
            memory,
            datetime.fromisoformat("2026-08-25T10:00:00+08:00"),
        )
        == 0
    )


def test_related_memory_scores_use_strongest_plus_half_of_supporting_scores():
    memories = [
        {"id": "memory-1", "memory": "用户正在准备同一场面试。"},
        {"id": "memory-2", "memory": "用户整理了这场面试需要的材料。"},
    ]

    metrics = memos_topic.compute_topic_metrics(
        assessments=[_assessment("memory-1", 10), _assessment("memory-2", 6)],
        memories=memories,
        now=datetime.fromisoformat("2026-08-25T10:00:00+08:00"),
        threshold=12,
    )

    assert metrics.qualifies is True
    assert metrics.score_breakdown["strongest_memory_score"] == 10
    assert metrics.score_breakdown["supporting_memory_points"] == 3
    assert metrics.score_breakdown["base_score"] == 13


def test_terminal_memory_does_not_inflate_an_open_topic_score() -> None:
    memories = [
        {
            "id": "memory-completed",
            "memory": "用户已经完成上一场面试。",
            "metadata": {
                "created_at": "2026-08-24T10:00:00+08:00",
                "info": {"record_type": "event", "event_status": "completed"},
            },
        },
        {
            "id": "memory-ongoing",
            "memory": "用户正在准备当前这场面试。",
            "metadata": {
                "created_at": "2026-08-25T10:00:00+08:00",
                "info": {"record_type": "event", "event_status": "ongoing"},
            },
        },
    ]

    metrics = memos_topic.compute_topic_metrics(
        assessments=[
            _assessment("memory-completed", 90),
            _assessment("memory-ongoing", 30),
        ],
        memories=memories,
        threshold=20,
    )

    assert metrics.qualifies is True
    assert metrics.progress_status == "ongoing"
    assert metrics.supporting_memory_ids == ["memory-ongoing"]
    assert metrics.score_breakdown["base_score"] == 30
    assert metrics.score_breakdown["memory_scores"] == {"memory-ongoing": 30}


@pytest.mark.parametrize("terminal_status", ["completed", "cancelled"])
def test_terminal_memory_is_audited_but_never_reaches_current_topic_models(
    tmp_path: Path,
    terminal_status: str,
) -> None:
    memories = {
        "memory-completed": {
            "id": "memory-completed",
            "memory": "用户已经完成上一轮面试准备。",
            "metadata": {
                "status": "activated",
                "created_at": "2026-08-24T10:00:00+08:00",
                "info": {"record_type": "event", "event_status": terminal_status},
            },
        },
        "memory-ongoing": {
            "id": "memory-ongoing",
            "memory": "用户正在准备当前这轮面试。",
            "metadata": {
                "status": "activated",
                "created_at": "2026-08-25T10:00:00+08:00",
                "info": {"record_type": "event", "event_status": "ongoing"},
            },
        },
    }
    grouping_inputs: list[list[str]] = []
    topic_inputs: list[dict[str, list[str]]] = []

    class FakeLLM:
        def analyze_memory(self, memory, catalog):
            del catalog
            return {
                "assessment": {
                    "eligible": True,
                    "agency": "committed",
                    "action_requirement": "must_do",
                    "impact": "high",
                    "explicit_priority": "must_or_remind",
                    "effort": "substantial",
                    "confidence": "high",
                    "reasons": {
                        "agency": "用户明确投入该事项。",
                        "action_requirement": "该事项需要用户处理。",
                        "impact": "该事项会影响用户安排。",
                        "explicit_priority": "用户明确关注该事项。",
                        "effort": "用户已经投入精力。",
                    },
                },
                "tags": [
                    {
                        "topic_key": "interview_preparation",
                        "tag_name": "面试准备",
                        "relationship": "direct",
                        "initiative_type": "acting",
                        "reason": "记忆明确描述了面试准备。",
                    }
                ],
            }

        def group_memories(self, *, memories, assessments, tags):
            del assessments, tags
            grouping_inputs.append([item["id"] for item in memories])
            return {
                "groups": [
                    {
                        "memory_ids": [item["id"] for item in memories],
                        "topic_kind": "event",
                        "reason": "这些记忆描述同一类面试准备。",
                    }
                ]
            }

        def generate_topic(self, *, topic_key, evidence, memories, metrics):
            del topic_key, metrics
            topic_inputs.append(
                {
                    "memories": [item["id"] for item in memories],
                    "evidence": [item.memory_id for item in evidence],
                }
            )
            return {
                "topic_text": "用户正在准备当前这轮面试。",
                "reason_summary": "当前面试准备仍未完成。",
                "reason_evidence": [
                    {
                        "memory_id": "memory-ongoing",
                        "fact": "用户正在准备当前这轮面试。",
                        "contribution": "证明当前事件仍需关注。",
                    }
                ],
            }

    store = memos_topic.TopicStore(tmp_path / "topics.json")
    processor = memos_topic.TopicProcessor(
        store=store,
        memos_client=SimpleNamespace(
            get_by_ids=lambda memory_ids: [memories[memory_id] for memory_id in memory_ids]
        ),
        llm=FakeLLM(),
    )

    processor.process_memory_ids(
        memory_ids=["memory-completed", "memory-ongoing"],
        user_id="user-1",
        cube_id="cube-1",
    )

    topics = store.list_topics(user_id="user-1", cube_id="cube-1", include_suppressed=True)
    assert grouping_inputs == []
    assert topic_inputs == [{"memories": ["memory-ongoing"], "evidence": ["memory-ongoing"]}]
    assert topics[0]["supporting_memory_ids"] == ["memory-ongoing"]
    assert [item["id"] for item in store.memories_by_ids(["memory-completed"])] == [
        "memory-completed"
    ]


def test_exact_duplicate_memory_only_contributes_its_highest_score():
    memories = [
        {"id": "memory-1", "memory": "用户正在准备同一场面试。"},
        {"id": "memory-2", "memory": " 用户正在准备同一场面试。 "},
    ]

    metrics = memos_topic.compute_topic_metrics(
        assessments=[_assessment("memory-1", 10), _assessment("memory-2", 6)],
        memories=memories,
        threshold=12,
    )

    assert metrics.qualifies is False
    assert metrics.score_breakdown["base_score"] == 10
    assert metrics.score_breakdown["duplicate_memory_count"] == 1


def test_group_parser_keeps_an_omitted_memory_as_a_safe_singleton():
    groups = memos_topic.parse_memory_groups(
        {
            "groups": [
                {
                    "memory_ids": ["memory-1"],
                    "topic_kind": "event",
                    "reason": "模型只明确判断了第一条记忆。",
                }
            ]
        },
        ["memory-1", "memory-2"],
    )

    assert [item.memory_ids for item in groups] == [["memory-1"], ["memory-2"]]
    assert groups[1].reason == "模型未明确归组，按单条事件独立保留"


def test_group_parser_requires_a_concrete_shared_anchor_for_multiple_memories():
    groups = memos_topic.parse_memory_groups(
        {
            "groups": [
                {
                    "memory_ids": ["memory-1", "memory-2"],
                    "topic_kind": "event",
                    "reason": "两条记忆都属于日程管理。",
                }
            ]
        },
        ["memory-1", "memory-2"],
    )

    assert [item.memory_ids for item in groups] == [["memory-1"], ["memory-2"]]
    assert all(item.shared_anchor is None for item in groups)


def test_group_parser_keeps_multiple_memories_with_a_concrete_shared_anchor():
    groups = memos_topic.parse_memory_groups(
        {
            "groups": [
                {
                    "memory_ids": ["memory-1", "memory-2"],
                    "topic_kind": "event",
                    "shared_anchor": "A公司2026年8月28日技术面试",
                    "reason": "两条记忆记录同一次面试的计划和进行状态。",
                }
            ]
        },
        ["memory-1", "memory-2"],
    )

    assert [item.memory_ids for item in groups] == [["memory-1", "memory-2"]]
    assert groups[0].shared_anchor == "A公司2026年8月28日技术面试"


def test_group_parser_rejects_unknown_or_repeated_memory_ids():
    invalid_results = [
        {
            "groups": [
                {
                    "memory_ids": ["memory-unknown"],
                    "topic_kind": "event",
                    "reason": "错误引用。",
                }
            ]
        },
        {
            "groups": [
                {
                    "memory_ids": ["memory-1", "memory-1"],
                    "topic_kind": "event",
                    "reason": "重复引用。",
                }
            ]
        },
    ]

    for raw in invalid_results:
        try:
            memos_topic.parse_memory_groups(raw, ["memory-1"])
        except ValueError:
            pass
        else:
            raise AssertionError("invalid Topic grouping should fail")


def test_tag_parser_accepts_current_agency_values_and_legacy_initiative_alias():
    memory = {
        "id": "memory-1",
        "memory": "用户观看了一段技术视频。",
        "metadata": {
            "status": "activated",
            "created_at": "2026-08-26T09:00:00+08:00",
            "info": {"record_type": "event"},
        },
    }

    for agency, tag_value, expected in (
        ("consumed", "consumed", "consumed"),
        ("committed", "committed", "committed"),
        ("acting", None, "acting"),
        ("committed", "initiated", "initiated"),
    ):
        tag = {
            "topic_key": "technical_video",
            "tag_name": "技术视频",
            "relationship": "direct",
            "reason": "记忆明确记录了用户观看技术视频。",
        }
        if tag_value is not None:
            tag["initiative_type"] = tag_value
        evidence = memos_topic.parse_tag_evidence(
            memory,
            {"assessment": {"agency": agency}, "tags": [tag]},
        )

        assert evidence[0].initiative_type == expected


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


def test_reconcile_retires_a_legacy_topic_after_its_only_memory_is_deleted(tmp_path: Path):
    state_path = tmp_path / "topics.json"
    store = memos_topic.TopicStore(state_path)
    memory = {
        "id": "memory-legacy",
        "memory": "用户曾经准备一场考试。",
        "metadata": {
            "status": "activated",
            "created_at": "2026-08-20T10:00:00+08:00",
            "info": {"record_type": "event", "event_status": "planned"},
        },
    }
    evidence = memos_topic.TagEvidence(
        memory_id="memory-legacy",
        topic_key="final_exam",
        tag_name="期末考试",
        relationship="direct",
        initiative_type="acting",
        reason="记忆明确提到考试准备。",
        evidence_unit="memory-legacy",
        observed_at="2026-08-20T10:00:00+08:00",
        event_time=None,
        event_status="planned",
    )
    metrics = memos_topic.CandidateMetrics(
        evidence_count=1,
        relationship_vote_sum=1,
        qualifies=True,
        supporting_memory_ids=["memory-legacy"],
        latest_evidence_at="2026-08-20T10:00:00+08:00",
        score_breakdown={"base_score": 70, "recency_factor": 1, "rank_score": 70},
        candidate_reasons=["旧版 Topic 测试"],
    )
    store.save_memory(user_id="user-1", cube_id="cube-1", memory=memory)
    store.replace_tags(
        user_id="user-1",
        cube_id="cube-1",
        memory_id="memory-legacy",
        evidence=[evidence],
    )
    store.upsert_topic(
        user_id="user-1",
        cube_id="cube-1",
        topic_key="final_exam",
        draft=memos_topic.TopicDraft(
            topic_text="用户正在准备一场考试。",
            reason_summary="旧版 Topic 测试。",
            reason_evidence=[
                memos_topic.ReasonEvidence(
                    memory_id="memory-legacy",
                    fact="用户曾经准备一场考试。",
                    contribution="这是该 Topic 的唯一依据。",
                )
            ],
        ),
        metrics=metrics,
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    topic = next(iter(state["scopes"].values()))["topics"]["final_exam"]
    topic.pop("selection_version")
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    processor = memos_topic.TopicProcessor(
        store=store,
        memos_client=SimpleNamespace(get_by_ids=lambda memory_ids: []),
        llm=SimpleNamespace(),
    )

    assert processor.reconcile() == 1
    assert store.list_topics(user_id="user-1", cube_id="cube-1") == []


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
                    "event_time": "无法识别的时间",
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
        def analyze_memory(self, memory, catalog):
            return {
                "assessment": {
                    "eligible": True,
                    "agency": "acting",
                    "action_requirement": "ongoing",
                    "impact": "meaningful",
                    "explicit_priority": "important",
                    "effort": "some",
                    "confidence": "high",
                    "reasons": {
                        "agency": "用户正在处理考试相关事项。",
                        "action_requirement": "准备工作仍在继续。",
                        "impact": "考试会影响学习安排。",
                        "explicit_priority": "用户制定了明确计划。",
                        "effort": "用户已经开始查看和规划。",
                    },
                },
                "tags": [
                    {
                        "topic_key": "final_exam",
                        "tag_name": "期末考试",
                        "relationship": "direct",
                        "initiative_type": "acting",
                        "reason": "记忆与期末考试直接相关。",
                    }
                ],
            }

        def group_memories(self, *, memories, assessments, tags):
            return {
                "groups": [
                    {
                        "memory_ids": ["memory-1", "memory-2"],
                        "topic_kind": "event",
                        "shared_anchor": "同一次期末考试准备",
                        "reason": "两条记忆共同说明同一次期末考试准备。",
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
    topics = store.list_topics(user_id="user-1", cube_id="cube-1", include_suppressed=True)
    assert len(topics) == 1
    assert topics[0]["topic_text"] == "用户近期正在集中准备期末考试。"
    assert [item["memory_id"] for item in topics[0]["reason_evidence"]] == [
        "memory-1",
        "memory-2",
    ]

    trace = store.topic_selection_trace(
        user_id="user-1",
        cube_id="cube-1",
        topic_id=topics[0]["topic_id"],
        seat_limit=15,
    )
    assert trace is not None
    assert trace["available"] is True
    assert trace["policy"]["topic_threshold"] == 60.0
    assert trace["policy"]["supporting_weight"] == 0.5
    assert trace["grouping"] == {
        "topic_kind": "event",
        "reason": "两条记忆共同说明同一次期末考试准备。",
        "shared_anchor": "同一次期末考试准备",
        "candidate_tag_keys": ["final_exam"],
        "memory_ids": ["memory-1", "memory-2"],
    }
    assert trace["decision"]["rank_position"] == 1
    assert trace["decision"]["seat_status"] == "suppressed"

    first_memory = trace["memories"][0]
    assert first_memory["memory_id"] == "memory-1"
    assert first_memory["initial_score"] == 54.0
    assert first_memory["current_score"] == 54.0
    assert first_memory["counting_status"] == "counted"
    dimensions = {item["key"]: item for item in first_memory["dimensions"]}
    assert dimensions["agency"] == {
        "key": "agency",
        "title": "主动程度",
        "label": "acting",
        "score_value": 19.0,
        "score_unit": "points",
        "max_value": 25.0,
        "source": "model",
        "reason": "用户正在处理考试相关事项。",
    }
    assert dimensions["urgency"]["label"] == "invalid_event_time"
    assert dimensions["urgency"]["source"] == "time_rule"
    assert dimensions["confidence"]["score_value"] == 1.0
    assert dimensions["confidence"]["score_unit"] == "multiplier"
    assert first_memory["tags"][0]["topic_key"] == "final_exam"
    assert "metadata" not in first_memory
    assert "selection_fingerprint" not in trace


def test_processor_splits_same_coarse_tag_into_two_standalone_topics(tmp_path: Path):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    memories = [
        {
            "id": "memory-interview",
            "memory": "用户今天晚上七点参加面试。",
            "metadata": {
                "status": "activated",
                "created_at": "2026-08-25T09:00:00+08:00",
                "info": {
                    "record_type": "event",
                    "event_status": "planned",
                    "event_start_at": "2099-08-25T19:00:00+08:00",
                },
            },
        },
        {
            "id": "memory-deadline",
            "memory": "用户明天上午十点前完成数据采集和评测。",
            "metadata": {
                "status": "activated",
                "created_at": "2026-08-25T09:05:00+08:00",
                "info": {
                    "record_type": "event",
                    "event_status": "planned",
                    "event_end_at": "2099-08-26T10:00:00+08:00",
                },
            },
        },
    ]

    class FakeLLM:
        def analyze_memory(self, memory, catalog):
            return {
                "assessment": {
                    "eligible": True,
                    "agency": "committed",
                    "action_requirement": "must_do",
                    "impact": "meaningful",
                    "explicit_priority": "important",
                    "effort": "none",
                    "confidence": "high",
                    "reasons": {
                        "agency": "用户明确计划执行。",
                        "action_requirement": "事件尚未完成。",
                        "impact": "会影响用户后续安排。",
                        "explicit_priority": "记忆表达了明确安排。",
                    },
                },
                "tags": [
                    {
                        "topic_key": "schedule_management",
                        "tag_name": "日程管理",
                        "relationship": "direct",
                        "initiative_type": "initiated",
                        "reason": "事件具有明确时间安排。",
                    }
                ],
            }

        def group_memories(self, *, memories, assessments, tags):
            assert {item["memory_id"] for item in assessments} == {
                "memory-interview",
                "memory-deadline",
            }
            return {
                "groups": [
                    {
                        "memory_ids": ["memory-interview"],
                        "topic_kind": "event",
                        "reason": "这是一次面试事件。",
                    },
                    {
                        "memory_ids": ["memory-deadline"],
                        "topic_kind": "event",
                        "reason": "这是另一项任务截止事件。",
                    },
                ]
            }

        def generate_topic(self, *, topic_key, evidence, memories, metrics):
            memory = memories[0]
            return {
                "topic_text": memory["memory"],
                "reason_summary": "这是一条单独达到晋升门槛的重要事件。",
                "reason_evidence": [
                    {
                        "memory_id": memory["id"],
                        "fact": memory["memory"],
                        "contribution": "该记忆本身具有较高关注价值。",
                    }
                ],
            }

    processor = memos_topic.TopicProcessor(
        store=store,
        memos_client=SimpleNamespace(get_by_ids=lambda memory_ids: memories),
        llm=FakeLLM(),
    )

    assert (
        processor.process_memory_ids(
            memory_ids=["memory-interview", "memory-deadline"],
            user_id="user-1",
            cube_id="cube-1",
        )
        == 2
    )
    topics = store.list_topics(user_id="user-1", cube_id="cube-1", include_suppressed=True)
    assert len(topics) == 2
    assert {tuple(item["supporting_memory_ids"]) for item in topics} == {
        ("memory-interview",),
        ("memory-deadline",),
    }


def test_existing_topic_keeps_its_id_when_a_related_memory_joins(tmp_path: Path):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    grouping_calls = []
    memories = {
        "memory-1": {
            "id": "memory-1",
            "memory": "用户开始实现项目甲的解析模块。",
            "metadata": {
                "status": "activated",
                "created_at": "2026-08-25T09:00:00+08:00",
                "info": {"record_type": "event", "event_status": "ongoing"},
            },
        },
        "memory-2": {
            "id": "memory-2",
            "memory": "用户继续为项目甲补充自动化测试。",
            "metadata": {
                "status": "activated",
                "created_at": "2026-08-25T10:00:00+08:00",
                "info": {"record_type": "event", "event_status": "ongoing"},
            },
        },
    }

    class FakeLLM:
        def analyze_memory(self, memory, catalog):
            return {
                "assessment": {
                    "eligible": True,
                    "agency": "committed",
                    "action_requirement": "must_do",
                    "impact": "meaningful",
                    "explicit_priority": "important",
                    "effort": "substantial",
                    "confidence": "high",
                    "reasons": {
                        "agency": "用户正在主动开发。",
                        "action_requirement": "项目仍需继续完成。",
                        "impact": "项目会影响后续工作。",
                        "explicit_priority": "用户正在持续推进。",
                        "effort": "记忆明确记录了实际开发动作。",
                    },
                },
                "tags": [
                    {
                        "topic_key": "project_alpha",
                        "tag_name": "项目甲",
                        "relationship": "direct",
                        "initiative_type": "acting",
                        "reason": "记忆明确属于项目甲。",
                    }
                ],
            }

        def group_memories(self, *, memories, assessments, tags):
            grouping_calls.append([item["id"] for item in memories])
            return {
                "groups": [
                    {
                        "memory_ids": [item["id"] for item in memories],
                        "topic_kind": "event",
                        "shared_anchor": "项目甲解析模块的开发交付",
                        "reason": "这些记忆是同一个项目的连续进展。",
                    }
                ]
            }

        def generate_topic(self, *, topic_key, evidence, memories, metrics):
            return {
                "topic_text": "；".join(item["memory"] for item in memories),
                "reason_summary": "多条记忆记录了同一项目的连续开发进展。",
                "reason_evidence": [
                    {
                        "memory_id": item["id"],
                        "fact": item["memory"],
                        "contribution": "支持项目仍在持续推进。",
                    }
                    for item in memories
                ],
            }

    client = SimpleNamespace(
        get_by_ids=lambda memory_ids: [memories[memory_id] for memory_id in memory_ids]
    )
    processor = memos_topic.TopicProcessor(store=store, memos_client=client, llm=FakeLLM())

    processor.process_memory_ids(
        memory_ids=["memory-1"],
        user_id="user-1",
        cube_id="cube-1",
    )
    first_topic = store.list_topics(user_id="user-1", cube_id="cube-1", include_suppressed=True)[0]
    processor.process_memory_ids(
        memory_ids=["memory-2"],
        user_id="user-1",
        cube_id="cube-1",
    )
    updated_topic = store.list_topics(user_id="user-1", cube_id="cube-1", include_suppressed=True)[
        0
    ]

    assert updated_topic["topic_id"] == first_topic["topic_id"]
    assert set(updated_topic["supporting_memory_ids"]) == {"memory-1", "memory-2"}
    assert updated_topic["selection_version"] == memos_topic.TOPIC_SELECTION_VERSION

    processor.process_memory_ids(memory_ids=[], user_id="user-1", cube_id="cube-1")
    assert len(grouping_calls) == 1

    memories["memory-2"]["memory"] = "用户已经完成项目甲的自动化测试。"
    processor.process_memory_ids(
        memory_ids=["memory-2"],
        user_id="user-1",
        cube_id="cube-1",
    )
    refreshed_topic = store.list_topics(
        user_id="user-1", cube_id="cube-1", include_suppressed=True
    )[0]
    assert "已经完成项目甲的自动化测试" in refreshed_topic["topic_text"]
    assert len(grouping_calls) == 2


def test_processor_only_analyzes_active_event_memories(tmp_path: Path):
    active_event = {
        "id": "event-1",
        "memory": "用户查看了一项普通活动。",
        "metadata": {"status": "activated", "info": {"record_type": "event"}},
    }
    note = {
        "id": "note-1",
        "memory": "这是一条说明文字。",
        "metadata": {"status": "activated", "info": {"record_type": "note"}},
    }
    archived_event = {
        "id": "event-2",
        "memory": "这条事件已经归档。",
        "metadata": {"status": "archived", "info": {"record_type": "event"}},
    }
    analyzed_ids = []

    class FakeLLM:
        def analyze_memory(self, memory, catalog):
            analyzed_ids.append(memory["id"])
            return {
                "assessment": {
                    "eligible": False,
                    "agency": "observed",
                    "action_requirement": "none",
                    "impact": "trivial",
                    "explicit_priority": "none",
                    "effort": "none",
                    "confidence": "high",
                    "reasons": {},
                },
                "tags": [],
            }

    all_memories = [active_event, note, archived_event]
    processor = memos_topic.TopicProcessor(
        store=memos_topic.TopicStore(tmp_path / "topics.json"),
        memos_client=SimpleNamespace(get_by_ids=lambda memory_ids: all_memories),
        llm=FakeLLM(),
    )

    processed = processor.process_memory_ids(
        memory_ids=["event-1", "note-1", "event-2"],
        user_id="user-1",
        cube_id="cube-1",
    )

    assert processed == 1
    assert analyzed_ids == ["event-1"]


def test_invalid_model_assessment_is_not_marked_as_processed(tmp_path: Path):
    memory = {
        "id": "event-invalid",
        "memory": "用户记录了一项事件。",
        "metadata": {"status": "activated", "info": {"record_type": "event"}},
    }

    class FakeLLM:
        def analyze_memory(self, memory, catalog):
            return {"tags": []}

    store = memos_topic.TopicStore(tmp_path / "topics.json")
    processor = memos_topic.TopicProcessor(
        store=store,
        memos_client=SimpleNamespace(get_by_ids=lambda memory_ids: [memory]),
        llm=FakeLLM(),
    )

    try:
        processor.process_memory_ids(
            memory_ids=["event-invalid"],
            user_id="user-1",
            cube_id="cube-1",
        )
    except TypeError:
        pass
    else:
        raise AssertionError("missing assessment should fail and be retried later")

    assert store.assessed_memory_ids("user-1", "cube-1") == set()


def test_ineligible_memory_does_not_pollute_the_tag_catalog(tmp_path: Path):
    memory = {
        "id": "event-ineligible",
        "memory": "这是一条导入说明，不是用户事件。",
        "metadata": {"status": "activated", "info": {"record_type": "event"}},
    }

    class FakeLLM:
        def analyze_memory(self, memory, catalog):
            return {
                "assessment": {
                    "eligible": False,
                    "agency": "observed",
                    "action_requirement": "none",
                    "impact": "trivial",
                    "explicit_priority": "none",
                    "effort": "none",
                    "confidence": "high",
                    "reasons": {},
                },
                "tags": [
                    {
                        "topic_key": "import_instruction",
                        "tag_name": "导入说明",
                        "relationship": "direct",
                        "initiative_type": "observed",
                        "reason": "模型错误地给说明文字加了标签。",
                    }
                ],
            }

    store = memos_topic.TopicStore(tmp_path / "topics.json")
    processor = memos_topic.TopicProcessor(
        store=store,
        memos_client=SimpleNamespace(get_by_ids=lambda memory_ids: [memory]),
        llm=FakeLLM(),
    )
    processor.process_memory_ids(
        memory_ids=["event-ineligible"],
        user_id="user-1",
        cube_id="cube-1",
    )

    assert store.tag_catalog("user-1", "cube-1") == []


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

    add_topic("2026-08-19", "older", 70)
    add_topic("2026-08-20", "top", 90)
    add_topic("2026-08-20", "suppressed", 65)
    store.rebalance(user_id="user-1", cube_id="cube-1", limit=1)

    visible = store.list_all_topics(user_id="user-1", cube_id="cube-1")
    complete = store.list_all_topics(
        user_id="user-1",
        cube_id="cube-1",
        include_suppressed=True,
    )

    assert [item["topic_key"] for item in visible] == ["top", "older", "suppressed"]
    assert [item["topic_key"] for item in complete] == ["top", "older", "suppressed"]


def test_prompts_only_ask_model_for_discrete_judgements():
    assert '"assessment"' in memos_topic.TAG_PROMPT
    assert '"agency"' in memos_topic.TAG_PROMPT
    assert '"effort"' in memos_topic.TAG_PROMPT
    assert '"relationship"' in memos_topic.TAG_PROMPT
    assert '"initiative_type"' in memos_topic.TAG_PROMPT
    assert '"relevance"' not in memos_topic.TAG_PROMPT
    assert "不得输出任何数字分数" in memos_topic.TAG_PROMPT
    assert "相同标签不代表同一件事" in memos_topic.GROUP_PROMPT
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


def test_repeated_terminal_events_do_not_reenter_the_current_topic_queue():
    evidence = [
        memos_topic.TagEvidence(
            memory_id=f"memory-{index}",
            topic_key="finished_interview",
            tag_name="已结束的面试",
            relationship="direct",
            initiative_type="committed",
            reason="记忆只是在记录已经结束的面试。",
            evidence_unit=f"event-{index}",
            observed_at=f"2026-08-2{index}T09:00:00+08:00",
            event_time=f"2026-08-2{index}T10:00:00+08:00",
            event_status=status,
        )
        for index, status in ((0, "completed"), (1, "cancelled"))
    ]

    metrics = memos_topic.compute_candidate_metrics(evidence)

    assert metrics.qualifies is False
    assert metrics.progress_status == "cancelled"


def test_json_store_keeps_yesterday_topic_and_rolls_only_three_core_topics(tmp_path: Path):
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

    add_topic("yesterday", 70.0, "2026-08-19T20:00:00+08:00")
    store.rebalance(user_id="user-1", cube_id="cube-1", limit=15)

    assert [
        item["topic_key"] for item in store.list_topics(user_id="user-1", cube_id="cube-1")
    ] == ["yesterday"]

    for index in range(15):
        add_topic(f"today_{index:02d}", 100.0 - index, "2026-08-20T10:00:00+08:00")
    store.rebalance(user_id="user-1", cube_id="cube-1", limit=15)

    visible = store.list_topics(user_id="user-1", cube_id="cube-1")
    complete = store.list_topics(user_id="user-1", cube_id="cube-1", include_suppressed=True)
    assert len(visible) == 3
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
    assert topic["queue_score_breakdown"]["queue_score"] == topic["rank_score"]
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
        def analyze_memory(self, memory, catalog):
            return {
                "assessment": {
                    "eligible": False,
                    "agency": "observed",
                    "action_requirement": "none",
                    "impact": "trivial",
                    "explicit_priority": "none",
                    "effort": "none",
                    "confidence": "high",
                    "reasons": {},
                },
                "tags": [],
            }

    store = memos_topic.TopicStore(tmp_path / "topics.json")
    store.save_memory(user_id="user-1", cube_id="cube-1", memory=already_processed)
    store.replace_tags(
        user_id="user-1",
        cube_id="cube-1",
        memory_id="memory-1",
        evidence=[],
    )
    store.replace_assessment(
        user_id="user-1",
        cube_id="cube-1",
        assessment=_assessment("memory-1", 0),
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


def test_backfill_reprocesses_old_state_that_has_tags_but_no_assessment(tmp_path: Path):
    memory = {
        "id": "memory-legacy",
        "memory": "用户记录了一项旧事件。",
        "metadata": {
            "status": "activated",
            "info": {"record_type": "event", "event_status": "completed"},
        },
    }
    requested_ids = []

    class FakeClient:
        def list_memories(self, *, user_id, cube_id):
            return [memory]

        def get_by_ids(self, memory_ids):
            requested_ids.extend(memory_ids)
            return [memory] if memory_ids else []

    class FakeLLM:
        def analyze_memory(self, memory, catalog):
            return {
                "assessment": {
                    "eligible": False,
                    "agency": "observed",
                    "action_requirement": "none",
                    "impact": "trivial",
                    "explicit_priority": "none",
                    "effort": "none",
                    "confidence": "high",
                    "reasons": {},
                },
                "tags": [],
            }

    store = memos_topic.TopicStore(tmp_path / "topics.json")
    store.save_memory(user_id="user-1", cube_id="cube-1", memory=memory)
    store.replace_tags(
        user_id="user-1",
        cube_id="cube-1",
        memory_id="memory-legacy",
        evidence=[],
    )
    processor = memos_topic.TopicProcessor(
        store=store,
        memos_client=FakeClient(),
        llm=FakeLLM(),
    )

    result = processor.backfill(user_id="user-1", cube_id="cube-1")

    assert result.pending_memories == 1
    assert result.processed_memories == 1
    assert requested_ids == ["memory-legacy"]
    assert store.assessed_memory_ids("user-1", "cube-1") == {"memory-legacy"}


def test_backfill_reprocesses_a_same_id_memory_after_its_content_changes(tmp_path: Path):
    memory = {
        "id": "memory-interview",
        "memory": "用户计划于2026年8月28日参加A公司的技术面试。",
        "metadata": {
            "status": "activated",
            "version": 1,
            "updated_at": "2026-08-27T09:00:00+08:00",
            "info": {"record_type": "event", "event_status": "planned"},
        },
    }
    analyzed_texts = []

    class FakeClient:
        def list_memories(self, *, user_id, cube_id):
            return [memory]

        def get_by_ids(self, memory_ids):
            return [memory] if memory_ids else []

    class FakeLLM:
        def analyze_memory(self, memory, catalog):
            analyzed_texts.append(memory["memory"])
            return {
                "assessment": {
                    "eligible": False,
                    "agency": "participated",
                    "action_requirement": "none",
                    "impact": "meaningful",
                    "explicit_priority": "none",
                    "effort": "some",
                    "confidence": "high",
                    "reasons": {},
                },
                "tags": [],
            }

    store = memos_topic.TopicStore(tmp_path / "topics.json")
    processor = memos_topic.TopicProcessor(
        store=store,
        memos_client=FakeClient(),
        llm=FakeLLM(),
    )
    first = processor.backfill(user_id="user-1", cube_id="cube-1")

    memory["memory"] = "用户已经完成A公司的技术面试。"
    memory["metadata"]["version"] = 2
    memory["metadata"]["updated_at"] = "2026-08-28T11:00:00+08:00"
    memory["metadata"]["info"]["event_status"] = "completed"
    second = processor.backfill(user_id="user-1", cube_id="cube-1")

    assert first.pending_memories == 1
    assert second.pending_memories == 1
    assert analyzed_texts == [
        "用户计划于2026年8月28日参加A公司的技术面试。",
        "用户已经完成A公司的技术面试。",
    ]
    assert store.memories_by_ids(["memory-interview"])[0]["metadata"]["version"] == 2


def test_reconcile_reprocesses_an_active_memory_with_a_new_revision(tmp_path: Path):
    memory = {
        "id": "memory-task",
        "memory": "用户计划于2026年8月28日完成数据评测。",
        "metadata": {
            "status": "activated",
            "version": 1,
            "updated_at": "2026-08-27T09:00:00+08:00",
            "info": {"record_type": "event", "event_status": "planned"},
        },
    }
    analyzed_statuses = []

    class FakeLLM:
        def analyze_memory(self, memory, catalog):
            analyzed_statuses.append(memory["metadata"]["info"]["event_status"])
            return {
                "assessment": {
                    "eligible": False,
                    "agency": "acting",
                    "action_requirement": "none",
                    "impact": "limited",
                    "explicit_priority": "none",
                    "effort": "some",
                    "confidence": "high",
                    "reasons": {},
                },
                "tags": [],
            }

    client = SimpleNamespace(get_by_ids=lambda memory_ids: [memory] if memory_ids else [])
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    processor = memos_topic.TopicProcessor(store=store, memos_client=client, llm=FakeLLM())
    processor.process_memory_ids(
        memory_ids=["memory-task"],
        user_id="user-1",
        cube_id="cube-1",
    )

    memory["memory"] = "用户已经完成2026年8月28日的数据评测。"
    memory["metadata"]["version"] = 2
    memory["metadata"]["updated_at"] = "2026-08-28T12:00:00+08:00"
    memory["metadata"]["info"]["event_status"] = "completed"

    assert processor.reconcile() == 0
    assert analyzed_statuses == ["planned", "completed"]
    assert store.memories_by_ids(["memory-task"])[0]["memory"].startswith("用户已经完成")


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
    store.replace_assessment(
        user_id="user-1",
        cube_id="cube-1",
        assessment=_assessment("memory-1", 70),
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
    topics = store.list_topics(user_id="user-1", cube_id="cube-1", include_suppressed=True)
    assert [item["topic_key"] for item in topics] == ["final_exam"]


def _queue_metrics(
    topic_key: str,
    score: float,
    observed_at: str,
    progress_status: str = "planned",
) -> object:
    return memos_topic.CandidateMetrics(
        evidence_count=1,
        relationship_vote_sum=1.0,
        qualifies=score >= 60,
        supporting_memory_ids=[f"memory-{topic_key}"],
        latest_evidence_at=observed_at,
        score_breakdown={
            "model": "static_importance_v3",
            "strongest_memory_score": score,
            "supporting_memory_points": 0.0,
            "duplicate_memory_count": 0,
            "counted_memory_ids": [f"memory-{topic_key}"],
            "importance_score": score,
            "base_score": score,
            "recency_factor": 1.0,
            "rank_score": score,
            "memory_scores": {f"memory-{topic_key}": score},
        },
        candidate_reasons=["测试候选"],
        progress_status=progress_status,
        importance_score=score,
    )


def _add_queue_topic(
    store,
    topic_key: str,
    score: float,
    *,
    observed_at: str = "2026-09-01T08:00:00+08:00",
    event_time: str = "2026-09-10T10:00:00+08:00",
    event_status: str = "planned",
) -> str:
    memory_id = f"memory-{topic_key}"
    store.save_memory(
        user_id="user-1",
        cube_id="cube-1",
        memory={
            "id": memory_id,
            "memory": f"用户需要处理 {topic_key}。",
            "metadata": {
                "status": "activated",
                "created_at": observed_at,
                "info": {
                    "record_type": "event",
                    "event_status": event_status,
                    "event_time": event_time,
                    "source_recorded_at": observed_at,
                },
            },
        },
    )
    return store.upsert_topic(
        user_id="user-1",
        cube_id="cube-1",
        topic_key=topic_key,
        draft=memos_topic.TopicDraft(
            topic_text=f"主题 {topic_key}",
            reason_summary="有一条明确事件记忆。",
            reason_evidence=[
                memos_topic.ReasonEvidence(
                    memory_id=memory_id,
                    fact=f"用户需要处理 {topic_key}。",
                    contribution="直接支持该主题。",
                )
            ],
        ),
        metrics=_queue_metrics(topic_key, score, observed_at, event_status),
    )


def test_new_topic_starts_as_suppressed_new_candidate(tmp_path: Path):
    store = memos_topic.TopicStore(tmp_path / "topics.json")

    _add_queue_topic(store, "interview", 70)

    topic = store.topic_record(user_id="user-1", cube_id="cube-1", topic_key="interview")
    assert topic["lifecycle_status"] == "suppressed"
    assert topic["candidate_source"] == "new"
    assert topic["queue_policy_version"] == 1
    assert topic["importance_score"] == 70
    assert topic["decay_penalty"] == 0


def test_scheduled_rebalance_keeps_three_core_and_twenty_seven_visible_candidates(
    tmp_path: Path,
):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    for index in range(35):
        _add_queue_topic(store, f"topic-{index:02d}", 100 - index)

    result = store.rebalance_queue(
        user_id="user-1",
        cube_id="cube-1",
        now=datetime.fromisoformat("2026-09-01T12:00:00+08:00"),
        mode="scheduled",
        policy=memos_topic.DEFAULT_TOPIC_QUEUE_POLICY,
    )
    snapshot = store.list_queue_snapshot(
        user_id="user-1",
        cube_id="cube-1",
        policy=memos_topic.DEFAULT_TOPIC_QUEUE_POLICY,
    )

    assert len(result.core_topic_ids) == 3
    assert len(result.visible_candidate_topic_ids) == 27
    assert result.hidden_candidate_count == 5
    assert snapshot["pool_total"] == 35
    assert snapshot["core_count"] == 3
    assert snapshot["visible_candidate_count"] == 27
    assert snapshot["hidden_candidate_count"] == 5
    assert len(snapshot["items"]) == 30
    assert len(store.list_topics(user_id="user-1", cube_id="cube-1", include_suppressed=True)) == 35


def test_scheduled_promotion_requires_five_point_margin(tmp_path: Path):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    for topic_key, score in (("core-a", 90), ("core-b", 80), ("core-c", 70)):
        _add_queue_topic(store, topic_key, score)
    store.rebalance_queue(
        user_id="user-1",
        cube_id="cube-1",
        now=datetime.fromisoformat("2026-09-01T00:00:00+08:00"),
        mode="scheduled",
        policy=memos_topic.DEFAULT_TOPIC_QUEUE_POLICY,
    )
    _add_queue_topic(store, "challenger", 74)

    first = store.rebalance_queue(
        user_id="user-1",
        cube_id="cube-1",
        now=datetime.fromisoformat("2026-09-01T12:00:00+08:00"),
        mode="scheduled",
        policy=memos_topic.DEFAULT_TOPIC_QUEUE_POLICY,
    )
    assert "challenger" not in {
        item["topic_key"] for item in store.list_topics(user_id="user-1", cube_id="cube-1")
    }
    assert first.promoted_topic_ids == []

    _add_queue_topic(store, "challenger", 75)
    second = store.rebalance_queue(
        user_id="user-1",
        cube_id="cube-1",
        now=datetime.fromisoformat("2026-09-02T00:00:00+08:00"),
        mode="scheduled",
        policy=memos_topic.DEFAULT_TOPIC_QUEUE_POLICY,
    )
    assert "challenger" in {
        item["topic_key"] for item in store.list_topics(user_id="user-1", cube_id="cube-1")
    }
    assert len(second.promoted_topic_ids) == 1
    assert len(second.demoted_topic_ids) == 1


def test_completed_topic_retires_and_immediately_fills_core_vacancy(tmp_path: Path):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    for topic_key, score in (("core-a", 90), ("core-b", 80), ("finished", 70), ("next", 65)):
        _add_queue_topic(store, topic_key, score)
    store.rebalance_queue(
        user_id="user-1",
        cube_id="cube-1",
        now=datetime.fromisoformat("2026-09-01T00:00:00+08:00"),
        mode="scheduled",
        policy=memos_topic.DEFAULT_TOPIC_QUEUE_POLICY,
    )
    _add_queue_topic(store, "finished", 70, event_status="completed")

    result = store.rebalance_queue(
        user_id="user-1",
        cube_id="cube-1",
        now=datetime.fromisoformat("2026-09-01T01:00:00+08:00"),
        mode="vacancy",
        policy=memos_topic.DEFAULT_TOPIC_QUEUE_POLICY,
    )

    finished = store.topic_record(user_id="user-1", cube_id="cube-1", topic_key="finished")
    assert finished["lifecycle_status"] == "retired"
    assert finished["importance_score"] == 70
    assert finished["queue_score"] == 0
    assert len(result.core_topic_ids) == 3
    assert "next" in {
        item["topic_key"] for item in store.list_topics(user_id="user-1", cube_id="cube-1")
    }


def test_past_unconfirmed_core_demotes_and_cannot_repromote_without_new_evidence(
    tmp_path: Path,
):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    _add_queue_topic(store, "past", 90, event_time="2026-09-01T10:00:00+08:00")
    _add_queue_topic(store, "future-a", 80)
    _add_queue_topic(store, "future-b", 70)
    _add_queue_topic(store, "future-c", 65)
    store.rebalance_queue(
        user_id="user-1",
        cube_id="cube-1",
        now=datetime.fromisoformat("2026-09-01T09:00:00+08:00"),
        mode="scheduled",
        policy=memos_topic.DEFAULT_TOPIC_QUEUE_POLICY,
    )

    store.rebalance_queue(
        user_id="user-1",
        cube_id="cube-1",
        now=datetime.fromisoformat("2026-09-02T12:00:00+08:00"),
        mode="scheduled",
        policy=memos_topic.DEFAULT_TOPIC_QUEUE_POLICY,
    )

    past = store.topic_record(user_id="user-1", cube_id="cube-1", topic_key="past")
    assert past["lifecycle_status"] == "suppressed"
    assert past["candidate_source"] == "demoted"
    assert past["attention_status"] == "past_unconfirmed"
    assert "past" not in {
        item["topic_key"] for item in store.list_topics(user_id="user-1", cube_id="cube-1")
    }


def test_ordinary_ingest_does_not_replace_core_but_urgent_ten_point_challenge_does(
    tmp_path: Path,
):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    for topic_key, score in (("core-a", 90), ("core-b", 80), ("core-c", 70)):
        _add_queue_topic(store, topic_key, score)
    store.rebalance_queue(
        user_id="user-1",
        cube_id="cube-1",
        now=datetime.fromisoformat("2026-09-01T00:00:00+08:00"),
        mode="scheduled",
        policy=memos_topic.DEFAULT_TOPIC_QUEUE_POLICY,
    )

    _add_queue_topic(store, "ordinary", 100, event_time="2026-09-10T10:00:00+08:00")
    store.rebalance_queue(
        user_id="user-1",
        cube_id="cube-1",
        now=datetime.fromisoformat("2026-09-01T09:00:00+08:00"),
        mode="ingest",
        policy=memos_topic.DEFAULT_TOPIC_QUEUE_POLICY,
    )
    assert "ordinary" not in {
        item["topic_key"] for item in store.list_topics(user_id="user-1", cube_id="cube-1")
    }

    _add_queue_topic(store, "urgent", 80, event_time="2026-09-01T11:00:00+08:00")
    result = store.rebalance_queue(
        user_id="user-1",
        cube_id="cube-1",
        now=datetime.fromisoformat("2026-09-01T09:00:00+08:00"),
        mode="ingest",
        policy=memos_topic.DEFAULT_TOPIC_QUEUE_POLICY,
    )
    assert len(result.promoted_topic_ids) == 1
    assert "urgent" in {
        item["topic_key"] for item in store.list_topics(user_id="user-1", cube_id="cube-1")
    }


def test_new_candidate_does_not_decay_but_demoted_candidate_reaches_twenty(tmp_path: Path):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    for topic_key, score in (("core-a", 90), ("core-b", 80), ("core-c", 70), ("waiting", 65)):
        _add_queue_topic(store, topic_key, score)
    store.rebalance_queue(
        user_id="user-1",
        cube_id="cube-1",
        now=datetime.fromisoformat("2026-09-01T00:00:00+08:00"),
        mode="scheduled",
        policy=memos_topic.DEFAULT_TOPIC_QUEUE_POLICY,
    )
    _add_queue_topic(store, "challenger", 100)
    store.rebalance_queue(
        user_id="user-1",
        cube_id="cube-1",
        now=datetime.fromisoformat("2026-09-01T12:00:00+08:00"),
        mode="scheduled",
        policy=memos_topic.DEFAULT_TOPIC_QUEUE_POLICY,
    )
    store.rebalance_queue(
        user_id="user-1",
        cube_id="cube-1",
        now=datetime.fromisoformat("2026-09-10T12:00:00+08:00"),
        mode="scheduled",
        policy=memos_topic.DEFAULT_TOPIC_QUEUE_POLICY,
    )

    waiting = store.topic_record(user_id="user-1", cube_id="cube-1", topic_key="waiting")
    demoted = store.topic_record(user_id="user-1", cube_id="cube-1", topic_key="core-c")
    assert waiting["candidate_source"] == "new"
    assert waiting["decay_penalty"] == 0
    assert demoted["candidate_source"] == "demoted"
    assert demoted["decay_penalty"] == 20


def test_status_or_event_time_revision_clears_demoted_decay(tmp_path: Path):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    for topic_key, score in (("core-a", 90), ("core-b", 80), ("target", 70)):
        _add_queue_topic(store, topic_key, score)
    store.rebalance_queue(
        user_id="user-1",
        cube_id="cube-1",
        now=datetime.fromisoformat("2026-09-01T00:00:00+08:00"),
        mode="scheduled",
        policy=memos_topic.DEFAULT_TOPIC_QUEUE_POLICY,
    )
    _add_queue_topic(store, "challenger", 100)
    store.rebalance_queue(
        user_id="user-1",
        cube_id="cube-1",
        now=datetime.fromisoformat("2026-09-10T00:00:00+08:00"),
        mode="scheduled",
        policy=memos_topic.DEFAULT_TOPIC_QUEUE_POLICY,
    )
    before = store.topic_record(user_id="user-1", cube_id="cube-1", topic_key="target")
    assert before["decay_penalty"] > 0

    _add_queue_topic(
        store,
        "target",
        70,
        observed_at="2026-09-01T08:00:00+08:00",
        event_time="2026-09-20T10:00:00+08:00",
    )
    after = store.topic_record(user_id="user-1", cube_id="cube-1", topic_key="target")
    assert after["candidate_source"] == "refreshed"
    assert after["attention_status"] == "open"
    assert after["decay_penalty"] == 0


def test_old_snapshot_migration_selects_three_cores_without_demotion_penalty(tmp_path: Path):
    state_path = tmp_path / "topics.json"
    topics = {}
    for index in range(5):
        score = 90 - index
        topics[f"legacy-{index}"] = {
            "topic_id": f"topic-legacy-{index}",
            "user_id": "user-1",
            "cube_id": "cube-1",
            "topic_key": f"legacy-{index}",
            "topic_text": f"旧主题 {index}",
            "supporting_memory_ids": [],
            "score_breakdown": {
                "model": "static_importance_v3",
                "importance_score": score,
                "base_score": score,
                "rank_score": score,
            },
            "rank_score": score,
            "progress_status": "planned",
            "lifecycle_status": "active",
            "last_evidence_at": "2026-09-01T08:00:00+08:00",
        }
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": "2026-09-01T08:00:00+08:00",
                "scopes": {
                    json.dumps(["user-1", "cube-1"], separators=(",", ":")): {
                        "user_id": "user-1",
                        "cube_id": "cube-1",
                        "memories": {},
                        "tags": {},
                        "assessments": {},
                        "group_cache": {},
                        "topics": topics,
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    store = memos_topic.TopicStore(state_path)

    store.rebalance_queue(
        user_id="user-1",
        cube_id="cube-1",
        now=datetime.fromisoformat("2026-09-01T12:00:00+08:00"),
        mode="scheduled",
        policy=memos_topic.DEFAULT_TOPIC_QUEUE_POLICY,
    )

    all_topics = store.list_topics(user_id="user-1", cube_id="cube-1", include_suppressed=True)
    assert len([item for item in all_topics if item["lifecycle_status"] == "active"]) == 3
    candidates = [item for item in all_topics if item["lifecycle_status"] == "suppressed"]
    assert len(candidates) == 2
    assert all(item["candidate_source"] == "new" for item in candidates)
    assert all(item["decay_penalty"] == 0 for item in candidates)


def test_scheduled_slot_is_idempotent_across_all_scopes(tmp_path: Path):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    _add_queue_topic(store, "interview", 70)
    slot = datetime.fromisoformat("2026-09-01T12:00:00+08:00")

    first = store.rebalance_all_scopes(
        now=slot,
        scheduled_slot=slot,
        policy=memos_topic.DEFAULT_TOPIC_QUEUE_POLICY,
    )
    second = store.rebalance_all_scopes(
        now=slot,
        scheduled_slot=slot,
        policy=memos_topic.DEFAULT_TOPIC_QUEUE_POLICY,
    )

    assert first["already_applied"] is False
    assert second == {
        "already_applied": True,
        "scheduled_slot": slot.isoformat(),
        "scopes": {},
    }


def test_scheduled_runtime_rebalance_uses_only_the_topic_store(monkeypatch, tmp_path: Path):
    store = MagicMock()
    store.rebalance_all_scopes.return_value = {"already_applied": False}
    store_factory = MagicMock(return_value=store)
    monkeypatch.setattr(memos_topic, "_load_project_env", lambda: None)
    monkeypatch.setattr(memos_topic, "default_store_path", lambda: tmp_path / "topics.json")
    monkeypatch.setattr(memos_topic, "TopicStore", store_factory)
    monkeypatch.setattr(
        memos_topic,
        "MemOSMemoryClient",
        MagicMock(side_effect=AssertionError("定时重排不应连接 MemOS")),
    )
    monkeypatch.setattr(
        memos_topic,
        "TopicLLM",
        MagicMock(side_effect=AssertionError("定时重排不应构造 Topic 模型")),
    )
    now = datetime.fromisoformat("2026-09-01T12:05:00+08:00")
    slot = datetime.fromisoformat("2026-09-01T12:00:00+08:00")

    result = memos_topic.rebalance_runtime_topic_queues(
        now=now,
        scheduled_slot=slot,
        policy=memos_topic.DEFAULT_TOPIC_QUEUE_POLICY,
    )

    assert result == {"already_applied": False}
    store_factory.assert_called_once_with(tmp_path / "topics.json")
    store.rebalance_all_scopes.assert_called_once_with(
        now=now,
        scheduled_slot=slot,
        policy=memos_topic.DEFAULT_TOPIC_QUEUE_POLICY,
    )


def test_due_unverified_remains_in_the_current_attention_pool_after_due_date(
    tmp_path: Path,
):
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    _add_queue_topic(
        store,
        "due",
        90,
        event_time="2026-09-01T10:00:00+08:00",
        event_status="due_unverified",
    )
    _add_queue_topic(store, "future-a", 80)
    _add_queue_topic(store, "future-b", 70)
    store.rebalance_queue(
        user_id="user-1",
        cube_id="cube-1",
        now=datetime.fromisoformat("2026-09-02T12:00:00+08:00"),
        mode="scheduled",
        policy=memos_topic.DEFAULT_TOPIC_QUEUE_POLICY,
    )

    due = store.topic_record(user_id="user-1", cube_id="cube-1", topic_key="due")
    assert due["progress_status"] == "due_unverified"
    assert due["attention_status"] == "open"
    assert due["lifecycle_status"] == "active"


def test_due_unverified_remains_an_unclosed_event_status():
    memory = {
        "id": "memory-interview",
        "memory": "用户计划于2026年9月1日参加A公司的技术面试。",
        "metadata": {
            "status": "activated",
            "created_at": "2026-08-31T09:00:00+08:00",
            "info": {
                "record_type": "event",
                "event_status": "due_unverified",
                "event_time": "2026-09-01T10:00:00+08:00",
            },
        },
    }
    raw_analysis = {
        "assessment": {
            "eligible": True,
            "agency": "committed",
            "action_requirement": "must_do",
            "impact": "meaningful",
            "explicit_priority": "important",
            "effort": "none",
            "confidence": "high",
            "reasons": {},
        },
        "tags": [
            {
                "topic_key": "a_company_interview",
                "tag_name": "A公司面试",
                "relationship": "direct",
                "initiative_type": "committed",
                "reason": "记忆明确描述了A公司的技术面试。",
            }
        ],
    }

    evidence = memos_topic.parse_tag_evidence(memory, raw_analysis)
    metrics = memos_topic.compute_candidate_metrics(
        evidence,
        now=datetime.fromisoformat("2026-09-01T11:00:00+08:00"),
    )

    assert evidence[0].event_status == "due_unverified"
    assert metrics.progress_status == "due_unverified"
    assert metrics.qualifies is True


def test_refresh_memory_ids_reuses_topic_analysis_for_lifecycle_only_revision(
    tmp_path: Path,
):
    memory = {
        "id": "memory-interview",
        "memory": "用户计划于2099年9月1日参加A公司的技术面试。",
        "metadata": {
            "status": "activated",
            "version": 1,
            "created_at": "2099-08-31T09:00:00+08:00",
            "updated_at": "2099-08-31T09:00:00+08:00",
            "info": {
                "record_type": "event",
                "event_status": "planned",
                "event_time": "2099-09-01T10:00:00+08:00",
                "source_recorded_at": "2099-08-31T09:00:00+08:00",
            },
        },
    }

    class FakeLLM:
        def __init__(self):
            self.analyze_calls = 0
            self.generate_calls = 0

        def analyze_memory(self, memory, catalog):
            self.analyze_calls += 1
            return {
                "assessment": {
                    "eligible": True,
                    "agency": "committed",
                    "action_requirement": "must_do",
                    "impact": "meaningful",
                    "explicit_priority": "important",
                    "effort": "none",
                    "confidence": "high",
                    "reasons": {
                        "agency": "用户明确计划参加面试。",
                        "action_requirement": "面试仍需要处理。",
                        "impact": "面试会影响用户安排。",
                        "explicit_priority": "用户明确设置了面试计划。",
                        "effort": "没有已投入精力的证据。",
                    },
                },
                "tags": [
                    {
                        "topic_key": "a_company_interview",
                        "tag_name": "A公司面试",
                        "relationship": "direct",
                        "initiative_type": "committed",
                        "reason": "记忆明确描述了A公司的技术面试。",
                    }
                ],
            }

        def generate_topic(self, *, topic_key, evidence, memories, metrics):
            self.generate_calls += 1
            return {
                "topic_text": "用户需要关注A公司的技术面试。",
                "reason_summary": "用户已经明确计划参加该面试。",
                "reason_evidence": [
                    {
                        "memory_id": "memory-interview",
                        "fact": "用户计划参加A公司的技术面试。",
                        "contribution": "证明这是用户尚未闭环的重要事件。",
                    }
                ],
            }

    client = SimpleNamespace(get_by_ids=lambda memory_ids: [memory] if memory_ids else [])
    llm = FakeLLM()
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    processor = memos_topic.TopicProcessor(store=store, memos_client=client, llm=llm)
    processor.process_memory_ids(
        memory_ids=["memory-interview"],
        user_id="user-1",
        cube_id="cube-1",
    )
    initial_topic = store.current_topic_records("user-1", "cube-1", include_retired=True)[0]
    initial_score = initial_topic["score_breakdown"]

    memory["metadata"]["version"] = 2
    memory["metadata"]["updated_at"] = "2099-09-01T10:01:00+08:00"
    memory["metadata"]["info"]["event_status"] = "due_unverified"

    processed = processor.refresh_memory_ids(
        memory_ids=["memory-interview"],
        user_id="user-1",
        cube_id="cube-1",
    )

    refreshed_topic = store.current_topic_records("user-1", "cube-1", include_retired=True)[0]
    stored_memory = store.memories_by_ids(["memory-interview"])[0]
    evidence = store.evidence_for_topic(
        user_id="user-1",
        cube_id="cube-1",
        topic_key="a_company_interview",
    )
    assert processed == 1
    assert llm.analyze_calls == 1
    assert llm.generate_calls == 1
    assert stored_memory["metadata"]["version"] == 2
    assert stored_memory["metadata"]["info"]["event_status"] == "due_unverified"
    assert evidence[0].event_status == "due_unverified"
    assert refreshed_topic["progress_status"] == "due_unverified"
    assert refreshed_topic["supporting_memory_ids"] == ["memory-interview"]
    assert refreshed_topic["score_breakdown"] == initial_score


def test_refresh_memory_ids_deactivates_a_stored_memory_that_disappeared(
    tmp_path: Path,
) -> None:
    memory = {
        "id": "memory-deleted",
        "memory": "用户计划参加一场面试。",
        "metadata": {
            "status": "activated",
            "version": 1,
            "info": {"record_type": "event", "event_status": "planned"},
        },
    }
    store = memos_topic.TopicStore(tmp_path / "topics.json")
    store.save_memory(user_id="user-1", cube_id="cube-1", memory=memory)
    processor = memos_topic.TopicProcessor(
        store=store,
        memos_client=SimpleNamespace(get_by_ids=lambda memory_ids: []),
        llm=SimpleNamespace(),
    )

    processed = processor.refresh_memory_ids(
        memory_ids=["memory-deleted"],
        user_id="user-1",
        cube_id="cube-1",
    )

    assert processed == 1
    assert store.active_memory_scopes("memory-deleted") == []


def test_refresh_memory_ids_retries_an_unknown_memory_that_is_not_visible_yet(
    tmp_path: Path,
) -> None:
    processor = memos_topic.TopicProcessor(
        store=memos_topic.TopicStore(tmp_path / "topics.json"),
        memos_client=SimpleNamespace(get_by_ids=lambda memory_ids: []),
        llm=SimpleNamespace(),
    )

    with pytest.raises(RuntimeError, match="memory-new"):
        processor.refresh_memory_ids(
            memory_ids=["memory-new"],
            user_id="user-1",
            cube_id="cube-1",
        )
