import json

from memos.graph_dbs.neo4j import _restore_neo4j_metadata, _sanitize_neo4j_metadata


def test_relationship_history_is_stored_as_json_and_restored() -> None:
    historical_events = [
        {"event_id": "event-1", "summary": "一起打羽毛球。"},
        {"event_id": "event-2", "summary": "一起参加聚餐。"},
    ]

    stored = _sanitize_neo4j_metadata({"historical_events": historical_events})

    assert isinstance(stored["historical_events"], str)
    assert json.loads(stored["historical_events"]) == historical_events
    assert _restore_neo4j_metadata(stored)["historical_events"] == historical_events


def test_archived_history_is_restored_for_memory_model_validation() -> None:
    history = [
        {
            "version": 1,
            "memory": "旧的联系人摘要。",
            "update_type": "unrelated",
            "is_fast": False,
        }
    ]

    stored = _sanitize_neo4j_metadata({"history": history})
    restored = _restore_neo4j_metadata(stored)

    assert restored["history"] == history
