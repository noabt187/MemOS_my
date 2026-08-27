from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import urllib.error

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

    assert assessment.score == 80.0
    assert assessment.score_breakdown == {
        "agency_points": 25.0,
        "action_points": 25.0,
        "urgency_points": 20.0,
        "impact_points": 10.0,
        "priority_points": 0.0,
        "effort_points": 0.0,
        "confidence_factor": 1.0,
        "memory_score": 80.0,
    }


def test_topic_metrics_recalculate_urgency_as_the_deadline_gets_closer():
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
    assert assessment.score == 12

    metrics = memos_topic.compute_topic_metrics(
        assessments=[assessment],
        memories=[memory],
        now=datetime.fromisoformat("2026-08-29T12:00:00+08:00"),
        threshold=0,
    )

    assert metrics.score_breakdown["memory_scores"]["memory-deadline"] == 20


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
    topics = store.list_topics(user_id="user-1", cube_id="cube-1")
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
        "candidate_tag_keys": ["final_exam"],
        "memory_ids": ["memory-1", "memory-2"],
    }
    assert trace["decision"]["rank_position"] == 1
    assert trace["decision"]["seat_status"] == "active"

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
                    "event_start_at": "2026-08-25T19:00:00+08:00",
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
                    "event_end_at": "2026-08-26T10:00:00+08:00",
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
    topics = store.list_topics(user_id="user-1", cube_id="cube-1")
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
    first_topic = store.list_topics(user_id="user-1", cube_id="cube-1")[0]
    processor.process_memory_ids(
        memory_ids=["memory-2"],
        user_id="user-1",
        cube_id="cube-1",
    )
    updated_topic = store.list_topics(user_id="user-1", cube_id="cube-1")[0]

    assert updated_topic["topic_id"] == first_topic["topic_id"]
    assert set(updated_topic["supporting_memory_ids"]) == {"memory-1", "memory-2"}
    assert updated_topic["selection_version"] == memos_topic.TOPIC_SELECTION_VERSION

    processor.process_memory_ids(memory_ids=[], user_id="user-1", cube_id="cube-1")
    assert len(grouping_calls) == 1

    store.retire_topic(
        user_id="user-1",
        cube_id="cube-1",
        topic_key=updated_topic["topic_key"],
    )
    processor.process_memory_ids(memory_ids=[], user_id="user-1", cube_id="cube-1")
    reactivated_topic = store.list_topics(user_id="user-1", cube_id="cube-1")[0]
    assert reactivated_topic["topic_id"] == first_topic["topic_id"]

    memories["memory-2"]["memory"] = "用户已经完成项目甲的自动化测试。"
    processor.process_memory_ids(
        memory_ids=["memory-2"],
        user_id="user-1",
        cube_id="cube-1",
    )
    refreshed_topic = store.list_topics(user_id="user-1", cube_id="cube-1")[0]
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
    topics = store.list_topics(user_id="user-1", cube_id="cube-1")
    assert [item["topic_key"] for item in topics] == ["final_exam"]
