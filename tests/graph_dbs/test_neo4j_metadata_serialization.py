"""Regression tests for structured metadata stored in Neo4j properties."""

from unittest.mock import MagicMock

from memos.graph_dbs.neo4j import (
    Neo4jGraphDB,
    _flatten_info_fields,
    _sanitize_neo4j_metadata,
)
from memos.graph_dbs.neo4j_community import Neo4jCommunityGraphDB


def _stored_node() -> dict:
    metadata = _sanitize_neo4j_metadata(
        {
            "memory_type": "LongTermMemory",
            "internal_info": {
                "ingest_batch_id": "batch-1",
                "chunk_index": 0,
                "nested": {"source": "sleep"},
            },
        }
    )
    return {"id": "memory-1", "memory": "我是一个程序员", **metadata}


def test_neo4j_parse_node_restores_internal_info_dict() -> None:
    db = Neo4jGraphDB.__new__(Neo4jGraphDB)

    parsed = db._parse_node(_stored_node())

    assert parsed["metadata"]["internal_info"] == {
        "ingest_batch_id": "batch-1",
        "chunk_index": 0,
        "nested": {"source": "sleep"},
    }


def test_neo4j_community_parse_node_restores_internal_info_dict() -> None:
    db = Neo4jCommunityGraphDB.__new__(Neo4jCommunityGraphDB)
    db.vec_db = MagicMock()
    db.vec_db.get_by_id.return_value = None

    parsed = db._parse_node(_stored_node())

    assert parsed["metadata"]["internal_info"]["ingest_batch_id"] == "batch-1"


def test_neo4j_community_parse_nodes_restores_internal_info_dict() -> None:
    db = Neo4jCommunityGraphDB.__new__(Neo4jCommunityGraphDB)
    db.vec_db = MagicMock()
    db.vec_db.get_by_ids.return_value = []

    parsed = db._parse_nodes([_stored_node()])

    assert parsed[0]["metadata"]["internal_info"]["nested"] == {"source": "sleep"}


def test_parse_node_keeps_already_deserialized_internal_info() -> None:
    db = Neo4jGraphDB.__new__(Neo4jGraphDB)
    node = _stored_node()
    node["internal_info"] = {"ingest_batch_id": "batch-1"}

    parsed = db._parse_node(node)

    assert parsed["metadata"]["internal_info"] == {"ingest_batch_id": "batch-1"}


def test_parse_node_restores_custom_fields_under_info() -> None:
    db = Neo4jGraphDB.__new__(Neo4jGraphDB)
    metadata = _flatten_info_fields(
        {
            "memory_type": "LongTermMemory",
            "info": {
                "record_type": "event",
                "event_type": "dining",
                "event_time": "2026-08-18",
            },
        }
    )
    stored = {"id": "memory-event", "memory": "用户参加了聚餐。", **metadata}

    parsed = db._parse_node(stored)

    assert parsed["metadata"]["info"] == {
        "record_type": "event",
        "event_type": "dining",
        "event_time": "2026-08-18",
    }
    assert "record_type" not in parsed["metadata"]


def test_neo4j_community_parse_node_restores_custom_fields_under_info() -> None:
    db = Neo4jCommunityGraphDB.__new__(Neo4jCommunityGraphDB)
    db.vec_db = MagicMock()
    db.vec_db.get_by_id.return_value = None
    metadata = _flatten_info_fields(
        {
            "memory_type": "LongTermMemory",
            "info": {"record_type": "event", "event_status": "ongoing"},
        }
    )

    parsed = db._parse_node({"id": "memory-event", "memory": "用户正在复习。", **metadata})

    assert parsed["metadata"]["info"] == {
        "record_type": "event",
        "event_status": "ongoing",
    }
