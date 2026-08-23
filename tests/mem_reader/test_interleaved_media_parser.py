from unittest.mock import MagicMock

import pytest

from memos.exceptions import ParserError
from memos.mem_reader.multi_modal_struct import MultiModalStructMemReader
from memos.mem_reader.read_multi_modal.interleaved_media_parser import InterleavedMediaParser


class FakeEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2] for _ in texts]


def make_interleaved_messages() -> list[dict]:
    return [
        {
            "role": "user",
            "message_id": "message-1",
            "content": [
                {"type": "text", "text": "中午11:10我自己做了一会饭"},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/jpeg;base64,bHVuY2g=",
                        "detail": "high",
                        "source_path": r"D:\pictures\lunch.jpg",
                        "filename": "lunch.jpg",
                        "mime_type": "image/jpeg",
                        "file_size": 5,
                        "sha256": "lunch-sha",
                    },
                },
                {"type": "text", "text": "然后我一边吃饭一边刷视频"},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64,dmlkZW8=",
                        "detail": "high",
                        "source_path": r"D:\pictures\video.png",
                        "filename": "video.png",
                        "mime_type": "image/png",
                        "file_size": 5,
                        "sha256": "video-sha",
                    },
                },
            ],
        }
    ]


def test_detects_text_image_interleaving_and_multi_image_input():
    assert InterleavedMediaParser.can_parse(make_interleaved_messages()) is True
    assert (
        InterleavedMediaParser.can_parse(
            [
                {
                    "role": "user",
                    "content": [
                        make_interleaved_messages()[0]["content"][1],
                        make_interleaved_messages()[0]["content"][3],
                    ],
                }
            ]
        )
        is True
    )
    assert (
        InterleavedMediaParser.can_parse(
            [{"role": "user", "content": [make_interleaved_messages()[0]["content"][1]]}]
        )
        is False
    )


def test_joint_parser_preserves_order_and_returns_native_memory_items():
    llm = MagicMock()
    llm.generate.return_value = (
        '{"sequence_groups": [], "memory_list": ['
        '{"key": "制作午饭", "memory_type": "LongTermMemory", '
        '"value": "用户在中午11:10左右制作了午饭。", '
        '"tags": ["午饭"], "evidence_part_ids": ["part_001", "part_002"], '
        '"confidence": 0.9}, '
        '{"key": "吃饭时刷视频", "memory_type": "LongTermMemory", '
        '"value": "用户吃饭时刷了视频。", '
        '"tags": ["视频"], "evidence_part_ids": ["part_003", "part_004"], '
        '"confidence": 0.8}], '
        '"summary": "用户做饭后边吃边看视频。", "uncertainties": []}'
    )
    parser = InterleavedMediaParser(FakeEmbedder(), llm)

    items = parser.parse_fine(
        make_interleaved_messages(),
        {
            "user_id": "user-1",
            "session_id": "session-1",
            "ingest_batch_id": "batch-1",
        },
        custom_tags=["图文导入"],
    )

    assert [item.memory for item in items] == [
        "用户在中午11:10左右制作了午饭。",
        "用户吃饭时刷了视频。",
    ]
    assert items[0].metadata.memory_type == "LongTermMemory"
    assert items[0].metadata.tags == ["午饭", "图文导入"]
    assert items[0].metadata.info["ingest_batch_id"] == "batch-1"
    assert items[0].metadata.background == "用户做饭后边吃边看视频。"
    assert items[0].metadata.confidence == 0.9

    first_sources = items[0].metadata.sources
    assert [source.part_id for source in first_sources] == ["part_001", "part_002"]
    assert first_sources[0].content == "中午11:10我自己做了一会饭"
    assert first_sources[1].image_info["source_path"] == r"D:\pictures\lunch.jpg"
    assert "base64" not in first_sources[1].model_dump_json()

    second_sources = items[1].metadata.sources
    assert [source.part_id for source in second_sources] == ["part_003", "part_004"]
    assert second_sources[1].image_info["source_path"] == r"D:\pictures\video.png"

    request_content = llm.generate.call_args.args[0][0]["content"]
    labels = [part["text"] for part in request_content if part["type"] == "text"]
    assert "part_001" in labels[1]
    assert "part_002" in labels[2]
    assert "part_003" in labels[3]
    assert "part_004" in labels[4]
    sent_images = [part for part in request_content if part["type"] == "image_url"]
    assert [part["image_url"]["url"] for part in sent_images] == [
        "data:image/jpeg;base64,bHVuY2g=",
        "data:image/png;base64,dmlkZW8=",
    ]
    assert llm.generate.call_count == 1


def test_joint_parser_uses_canonical_memory_list_schema():
    llm = MagicMock()
    llm.generate.return_value = (
        '{"sequence_groups": [], "memory_list": ['
        '{"key": "视频解析器开发计划", "memory_type": "UserMemory", '
        '"value": "用户计划在2026年8月22日晚上前完成视频解析器原型。", '
        '"tags": ["开发计划"], "evidence_part_ids": ["part_001"], '
        '"confidence": 1.0}], '
        '"summary": "用户制定了视频解析器开发计划。", "uncertainties": []}'
    )
    parser = InterleavedMediaParser(FakeEmbedder(), llm)

    items = parser.parse_fine(
        make_interleaved_messages(),
        {"user_id": "user-1", "session_id": "session-1"},
    )

    assert [item.memory for item in items] == ["用户计划在2026年8月22日晚上前完成视频解析器原型。"]
    assert [source.part_id for source in items[0].metadata.sources] == ["part_001"]


def test_joint_parser_rejects_legacy_spaced_memory_list_key():
    llm = MagicMock()
    llm.generate.return_value = '{"memory list": [], "summary": "legacy"}'
    parser = InterleavedMediaParser(FakeEmbedder(), llm)

    with pytest.raises(ParserError, match="memory_list"):
        parser.parse_fine(
            make_interleaved_messages(),
            {"user_id": "user-1", "session_id": "session-1"},
        )


def test_joint_parser_does_not_inherit_dates_across_unrelated_events():
    llm = MagicMock()
    llm.generate.return_value = '{"memory_list": [], "summary": "", "uncertainties": []}'
    parser = InterleavedMediaParser(FakeEmbedder(), llm)

    parser.parse_fine(
        make_interleaved_messages(),
        {"user_id": "user-1", "session_id": "session-1"},
    )

    prompt = llm.generate.call_args.args[0][0]["content"][0]["text"]
    assert "相同主题不代表同一个事件" in prompt
    assert "不得把后文日期倒推给前面没有日期的事件" in prompt
    assert "不同日期的记录必须拆成不同记忆" in prompt


def test_multimodal_reader_routes_fine_interleaved_input_before_legacy_expansion():
    reader = object.__new__(MultiModalStructMemReader)
    reader.multi_modal_parser = MagicMock()
    reader.multi_modal_parser.can_parse_interleaved.return_value = True
    expected = [MagicMock()]
    reader.multi_modal_parser.parse_interleaved.return_value = expected
    info = {
        "user_id": "user-1",
        "session_id": "session-1",
        "custom_tags": ["图文导入"],
    }

    result = reader._process_multi_modal_data(
        make_interleaved_messages(),
        info,
        mode="fine",
        user_context=None,
    )

    assert result == expected
    reader.multi_modal_parser.parse_interleaved.assert_called_once_with(
        make_interleaved_messages(),
        {"user_id": "user-1", "session_id": "session-1"},
        custom_tags=["图文导入"],
        user_context=None,
    )
