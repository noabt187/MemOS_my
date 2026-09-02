from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from memos.exceptions import VectorDBError
from memos.graph_dbs.neo4j_community import Neo4jCommunityGraphDB
from memos.vec_dbs.item import VecDBItem


class FailingVectorDatabase:
    def add(self, items) -> None:
        raise OSError("vector database unavailable")


def test_strict_add_node_stops_before_neo4j_when_vector_write_fails() -> None:
    graph_store = object.__new__(Neo4jCommunityGraphDB)
    graph_store.config = SimpleNamespace(use_multi_db=False, user_name=None)
    graph_store.vec_db = FailingVectorDatabase()
    graph_store.driver = MagicMock()
    graph_store.db_name = "neo4j"

    with pytest.raises(OSError, match="vector database unavailable"):
        graph_store.add_node_strict(
            "11111111-1111-4111-8111-111111111111",
            "用户正在参加A公司的技术面试。",
            {
                "embedding": [0.1, 0.2],
                "created_at": "2026-08-31T10:00:00+08:00",
                "updated_at": "2026-08-31T10:00:00+08:00",
                "memory_type": "LongTermMemory",
                "status": "activated",
            },
            user_name="cube-1",
        )

    graph_store.driver.session.assert_not_called()


def test_strict_add_node_reuses_the_stored_vector_when_embedding_is_missing() -> None:
    graph_store = object.__new__(Neo4jCommunityGraphDB)
    graph_store.config = SimpleNamespace(use_multi_db=False, user_name=None)
    graph_store.vec_db = MagicMock()
    graph_store.vec_db.get_by_ids.return_value = [
        VecDBItem(
            id="22222222-2222-4222-8222-222222222222",
            vector=[0.4, 0.5],
            payload={"memory": "旧内容"},
        )
    ]
    graph_store.driver = MagicMock()
    graph_store.db_name = "neo4j"

    graph_store.add_node_strict(
        "22222222-2222-4222-8222-222222222222",
        "用户正在参加A公司的技术面试。",
        {
            "created_at": "2026-08-31T10:00:00+08:00",
            "updated_at": "2026-08-31T10:00:00+08:00",
            "memory_type": "LongTermMemory",
            "status": "activated",
        },
        user_name="cube-1",
    )

    graph_store.vec_db.get_by_ids.assert_called_once_with(["22222222-2222-4222-8222-222222222222"])
    written_item = graph_store.vec_db.add.call_args.args[0][0]
    assert written_item.vector == [0.4, 0.5]
    graph_store.driver.session.assert_called_once()


def test_strict_add_node_rejects_a_missing_stored_vector_before_neo4j() -> None:
    graph_store = object.__new__(Neo4jCommunityGraphDB)
    graph_store.config = SimpleNamespace(use_multi_db=False, user_name=None)
    graph_store.vec_db = MagicMock()
    graph_store.vec_db.get_by_ids.return_value = []
    graph_store.driver = MagicMock()
    graph_store.db_name = "neo4j"

    with pytest.raises(VectorDBError, match="requires an embedding"):
        graph_store.add_node_strict(
            "44444444-4444-4444-8444-444444444444",
            "用户正在参加A公司的技术面试。",
            {
                "created_at": "2026-08-31T10:00:00+08:00",
                "updated_at": "2026-08-31T10:00:00+08:00",
                "memory_type": "LongTermMemory",
                "status": "activated",
            },
            user_name="cube-1",
        )

    graph_store.vec_db.add.assert_not_called()
    graph_store.driver.session.assert_not_called()


def test_strict_add_node_writes_vector_before_neo4j_on_success() -> None:
    graph_store = object.__new__(Neo4jCommunityGraphDB)
    graph_store.config = SimpleNamespace(use_multi_db=False, user_name=None)
    graph_store.vec_db = MagicMock()
    graph_store.driver = MagicMock()
    graph_store.db_name = "neo4j"

    graph_store.add_node_strict(
        "33333333-3333-4333-8333-333333333333",
        "用户正在参加A公司的技术面试。",
        {
            "embedding": [0.1, 0.2],
            "created_at": "2026-08-31T10:00:00+08:00",
            "updated_at": "2026-08-31T10:00:00+08:00",
            "memory_type": "LongTermMemory",
            "status": "activated",
        },
        user_name="cube-1",
    )

    graph_store.vec_db.add.assert_called_once()
    session = graph_store.driver.session.return_value.__enter__.return_value
    session.run.assert_called_once()
    assert session.run.call_args.kwargs["metadata"]["vector_sync"] == "success"
