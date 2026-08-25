import json

from datetime import datetime, timedelta, timezone

from memos.memories.textual.item import TextualMemoryItem, TreeNodeTextualMemoryMetadata
from memos.memories.textual.relationship import (
    MemoryInfoEnricher,
    PersonalMemoryNormalizer,
    RelationshipUpdater,
    decode_historical_events,
)


class FakeLLM:
    def __init__(self, responses: list[dict]):
        self.responses = list(responses)

    def generate(self, messages: list[dict]) -> str:
        assert messages[0]["role"] == "user"
        return json.dumps(self.responses.pop(0), ensure_ascii=False)


class RawFakeLLM:
    def __init__(self, response: str):
        self.response = response

    def generate(self, messages: list[dict]) -> str:
        assert messages[0]["role"] == "user"
        return self.response


class FakeEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text))] for text in texts]


class FakeGraphStore:
    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}

    def get_by_metadata(self, filters: list[dict], **kwargs) -> list[str]:
        matches = []
        for node_id, node in self.nodes.items():
            metadata = node["metadata"]
            if all(metadata.get(item["field"]) == item["value"] for item in filters):
                matches.append(node_id)
        return matches

    def get_node(self, node_id: str, **kwargs) -> dict | None:
        return self.nodes.get(node_id)

    def get_nodes(self, ids: list[str], **kwargs) -> list[dict]:
        return [self.nodes[node_id] for node_id in ids if node_id in self.nodes]


class FakeTextMemory:
    def __init__(self, graph_store: FakeGraphStore) -> None:
        self.graph_store = graph_store
        self.embedder = FakeEmbedder()
        self.saved: list[TextualMemoryItem] = []

    def add(self, memories: list[TextualMemoryItem], user_name: str | None = None) -> list[str]:
        ids = []
        for item in memories:
            self.saved.append(item.model_copy(deep=True))
            dumped = item.metadata.model_dump(exclude_none=True)
            info = dumped.pop("info", {})
            dumped.update(info)
            self.graph_store.nodes[item.id] = {
                "id": item.id,
                "memory": item.memory,
                "metadata": dumped,
            }
            ids.append(item.id)
        return ids


def make_memory(memory: str, info: dict | None = None) -> TextualMemoryItem:
    return TextualMemoryItem(
        memory=memory,
        metadata=TreeNodeTextualMemoryMetadata(
            user_id="user-1",
            session_id="session-1",
            memory_type="UserMemory",
            embedding=[0.1],
            info=info or {},
        ),
    )


def test_memory_info_enricher_adds_fixed_event_schema_and_person_keys() -> None:
    llm = FakeLLM(
        [
            {
                "items": [
                    {
                        "index": 0,
                        "record_type": "event",
                        "assertion_basis": "explicit_single",
                        "event_type": "sports",
                        "event_status": "completed",
                        "event_actor": "林誉恒",
                        "event_action": "和用户一起打羽毛球",
                        "event_target": None,
                        "participants": ["用户", "林誉恒"],
                        "event_location": "球馆",
                        "event_time": "2026-07-09T18:30:00+08:00",
                        "event_time_text": "周四晚上六点半",
                        "source_recorded_at": None,
                    }
                ]
            }
        ]
    )
    memory = make_memory(
        "2026年7月9日晚上，用户和林誉恒一起打羽毛球。",
        {"source_recorded_at": "2026-07-10T08:00:00+08:00"},
    )

    MemoryInfoEnricher(llm).enrich([memory], user_id="user-1")

    info = memory.metadata.info
    assert info["record_type"] == "event"
    assert info["participants"] == ["用户", "林誉恒"]
    assert info["participant_keys"][0] == "user-1"
    assert info["participant_keys"][1].startswith("person_")
    assert info["event_group_id"] is None
    assert info["series_id"] is None
    assert info["source_recorded_at"] == "2026-07-10T08:00:00+08:00"
    assert "event_purpose" not in info
    assert "purpose_basis" not in info


def test_personal_memory_normalizer_merges_dependent_fragments_into_one_event() -> None:
    llm = FakeLLM(
        [
            {
                "events": [
                    {
                        "source_indices": [0, 1, 2],
                        "key": "用户与林誉恒聚餐",
                        "memory": (
                            "2026年8月18日18:30至20:10左右，用户和林誉恒在渔火餐厅"
                            "共进晚餐，以庆祝用户完成期末项目。"
                        ),
                        "info": {
                            "record_type": "event",
                            "assertion_basis": "explicit_multiple",
                            "event_group_id": "dinner-20260818-lin",
                            "series_id": None,
                            "event_type": "dining",
                            "event_status": "completed",
                            "event_actor": "用户",
                            "event_action": "和林誉恒共进晚餐",
                            "event_target": None,
                            "participants": ["用户", "林誉恒"],
                            "event_location": "渔火餐厅",
                            "event_time": ("2026-08-18T18:30:00+08:00/2026-08-18T20:10:00+08:00"),
                            "event_time_text": "8月18日18:30至20:10左右",
                            "source_recorded_at": "2026-08-19T10:00:00+08:00",
                        },
                    }
                ],
                "contact_updates": [],
                "discarded": [],
            }
        ]
    )
    candidates = [
        make_memory("用户和林誉恒在渔火餐厅聚餐。"),
        make_memory("此次聚餐是为了庆祝用户完成期末项目。"),
        make_memory("聚餐于20:10左右结束。"),
    ]

    result = PersonalMemoryNormalizer(llm, FakeEmbedder()).normalize(
        candidates,
        user_id="user-1",
        source_material="原始聚餐记录",
    )

    assert len(result.events) == 1
    assert result.events[0].memory.startswith("2026年8月18日18:30至20:10左右")
    assert result.events[0].metadata.info["record_type"] == "event"
    participant_keys = result.events[0].metadata.info["participant_keys"]
    assert participant_keys[0] == "user-1"
    assert participant_keys[1].startswith("person_")
    assert "event_purpose" not in result.events[0].metadata.info
    assert result.contact_updates == []


def test_personal_memory_normalizer_keeps_complete_events_before_truncated_tail() -> None:
    response = """{
      "events": [
        {
          "source_indices": [0],
          "key": "事件一",
          "memory": "完整事件一",
          "info": {"record_type": "event"}
        },
        {
          "source_indices": [1],
          "key": "事件二",
          "memory": "完整事件二",
          "info": {"record_type": "event"}
        },
        {
          "source_indices": [2],
          "key": "事件三",
          "memory": "被截断的事件
    """
    candidates = [make_memory(f"候选事件{i}") for i in range(3)]

    result = PersonalMemoryNormalizer(RawFakeLLM(response), FakeEmbedder()).normalize(
        candidates,
        user_id="user-1",
    )

    assert [event.memory for event in result.events] == ["完整事件一", "完整事件二"]
    assert result.contact_updates == []
    assert result.discarded == [{"source_indices": [2], "reason": "incomplete_normalizer_output"}]


def test_personal_memory_normalizer_converts_preference_to_ongoing_event() -> None:
    llm = FakeLLM(
        [
            {
                "events": [
                    {
                        "source_indices": [0],
                        "key": "用户的回复风格偏好",
                        "memory": "用户偏好简短、清晰、直接的回复。",
                        "info": {
                            "record_type": "event",
                            "assertion_basis": "explicit_single",
                            "event_group_id": "preference:response_style",
                            "series_id": None,
                            "event_type": "preference",
                            "event_status": "ongoing",
                            "event_actor": "用户",
                            "event_action": "偏好",
                            "event_target": "简短、清晰、直接的回复",
                            "participants": ["用户"],
                            "event_location": None,
                            "event_time": None,
                            "event_time_text": None,
                            "source_recorded_at": "2026-08-20T09:00:00+08:00",
                        },
                    }
                ],
                "contact_updates": [],
                "discarded": [],
            }
        ]
    )

    result = PersonalMemoryNormalizer(llm, FakeEmbedder()).normalize(
        [make_memory("我喜欢更简短的回复。")],
        user_id="user-1",
        source_material="我喜欢更简短的回复。",
    )

    assert len(result.events) == 1
    assert result.events[0].metadata.info["event_type"] == "preference"
    assert result.events[0].metadata.info["event_status"] == "ongoing"
    assert result.events[0].metadata.info["event_time"] is None


def test_personal_memory_normalizer_routes_relationship_fact_without_storing_it_as_event() -> None:
    llm = FakeLLM(
        [
            {
                "events": [],
                "contact_updates": [
                    {
                        "source_indices": [0],
                        "person_name": "林誉恒",
                        "person_aliases": ["林誉恒"],
                        "relations": ["college_classmate"],
                        "assertion_basis": "explicit_single",
                    }
                ],
                "discarded": [],
            }
        ]
    )

    result = PersonalMemoryNormalizer(llm, FakeEmbedder()).normalize(
        [make_memory("林誉恒与用户是大学同学。")],
        user_id="user-1",
        source_material="林誉恒与用户是大学同学。",
    )

    assert result.events == []
    assert len(result.contact_updates) == 1
    assert result.contact_updates[0].person_name == "林誉恒"
    assert result.contact_updates[0].relations == ["college_classmate"]

    graph = FakeGraphStore()
    text_mem = FakeTextMemory(graph)
    relationship_ids = RelationshipUpdater(text_mem=text_mem, llm=None).process_contact_updates(
        result.contact_updates,
        user_id="user-1",
        user_name="cube-1",
    )

    assert len(relationship_ids) == 1
    relationship = graph.nodes[relationship_ids[0]]
    assert relationship["metadata"]["record_type"] == "person_relationship_summary"
    assert relationship["metadata"]["relations"] == ["college_classmate"]


def test_relationship_updater_keeps_full_history_and_updates_same_contact() -> None:
    graph = FakeGraphStore()
    text_mem = FakeTextMemory(graph)
    llm = FakeLLM(
        [
            {
                "memory": "林誉恒是用户的羽毛球伙伴。",
                "relations": ["sports_partner"],
                "assertion_basis": "explicit_single",
                "confidence": 82,
            }
        ]
    )
    updater = RelationshipUpdater(text_mem=text_mem, llm=llm, refresh_event_interval=10)
    first = make_memory(
        "2026年7月9日晚上，用户和林誉恒一起去球馆打羽毛球。",
        {
            "record_type": "event",
            "assertion_basis": "explicit_single",
            "participants": ["用户", "林誉恒"],
            "participant_keys": ["user-1", "person_lin"],
            "event_time": "2026-07-09T18:30:00+08:00",
        },
    )
    second = make_memory(
        "2026年7月12日，用户和林誉恒一起参加部门聚餐。",
        {
            "record_type": "event",
            "assertion_basis": "explicit_single",
            "participants": ["用户", "林誉恒"],
            "participant_keys": ["user-1", "person_lin"],
            "event_time": "2026-07-12",
        },
    )

    first_ids = updater.process([first], ["event-1"], user_id="user-1", user_name="cube-1")
    second_ids = updater.process([second], ["event-2"], user_id="user-1", user_name="cube-1")

    assert first_ids == second_ids
    relationship = graph.nodes[first_ids[0]]
    history = decode_historical_events(relationship["metadata"]["historical_events"])
    assert [item.event_id for item in history] == ["event-1", "event-2"]
    assert history[0].summary == first.memory
    assert history[1].summary == second.memory
    assert relationship["metadata"]["relation_key"] == "user-1:person_lin"


def test_relationship_updater_replaces_duplicate_event_summary() -> None:
    graph = FakeGraphStore()
    text_mem = FakeTextMemory(graph)
    updater = RelationshipUpdater(text_mem=text_mem, llm=None)
    event = make_memory(
        "用户和林誉恒打羽毛球。",
        {
            "record_type": "event",
            "participants": ["用户", "林誉恒"],
            "participant_keys": ["user-1", "person_lin"],
            "event_time": "2026-07-09",
        },
    )

    updater.process([event], ["event-1"], user_id="user-1", user_name="cube-1")
    event.memory = "2026年7月9日，用户和林誉恒在球馆打羽毛球。"
    relationship_ids = updater.process([event], ["event-1"], user_id="user-1", user_name="cube-1")

    history = decode_historical_events(
        graph.nodes[relationship_ids[0]]["metadata"]["historical_events"]
    )
    assert len(history) == 1
    assert history[0].summary == event.memory


def test_cleanup_removes_missing_events_and_resaves_relationship() -> None:
    graph = FakeGraphStore()
    text_mem = FakeTextMemory(graph)
    updater = RelationshipUpdater(text_mem=text_mem, llm=None)
    old_check = (datetime.now(timezone.utc) - timedelta(days=11)).isoformat()
    relationship = make_memory(
        "林誉恒是用户的联系人。",
        {
            "record_type": "person_relationship_summary",
            "assertion_basis": "uncertain",
            "relation_key": "user-1:person_lin",
            "person_key": "person_lin",
            "person_name": "林誉恒",
            "person_aliases": ["林誉恒"],
            "relations": [],
            "relation_status": "active",
            "historical_events": [
                {"event_id": "event-existing", "summary": "一起打羽毛球。"},
                {"event_id": "event-missing", "summary": "一起吃饭。"},
            ],
            "last_observed_at": "2026-07-09",
            "history_checked_at": old_check,
        },
    )
    text_mem.add([relationship], user_name="cube-1")
    graph.nodes["event-existing"] = {
        "id": "event-existing",
        "memory": "一起打羽毛球。",
        "metadata": {"record_type": "event"},
    }

    result = updater.cleanup_stale_history(user_name="cube-1", force=False)

    assert result.checked_relationships == 1
    assert result.removed_references == 1
    history = decode_historical_events(
        graph.nodes[relationship.id]["metadata"]["historical_events"]
    )
    assert [item.event_id for item in history] == ["event-existing"]


def test_decode_historical_events_accepts_neo4j_serialized_values() -> None:
    raw = [
        json.dumps({"event_id": "event-1", "summary": "一起打球。"}, ensure_ascii=False),
        json.dumps({"event_id": "event-2", "summary": "一起吃饭。"}, ensure_ascii=False),
    ]

    decoded = decode_historical_events(raw)

    assert [item.event_id for item in decoded] == ["event-1", "event-2"]


def test_relationship_updater_ignores_events_that_do_not_include_user() -> None:
    graph = FakeGraphStore()
    text_mem = FakeTextMemory(graph)
    updater = RelationshipUpdater(text_mem=text_mem, llm=None)
    event = make_memory(
        "张三和李四一起参加了聚餐。",
        {
            "record_type": "event",
            "participants": ["张三", "李四"],
            "participant_keys": ["person_zhang", "person_li"],
            "event_time": "2026-07-09",
        },
    )

    relationship_ids = updater.process(
        [event], ["event-third-party"], user_id="user-1", user_name="cube-1"
    )

    assert relationship_ids == []
    assert graph.nodes == {}
