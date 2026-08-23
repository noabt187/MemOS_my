from unittest.mock import MagicMock

import pytest

from memos.exceptions import ConfigurationError
from memos.mem_reader.multi_modal_struct import MultiModalStructMemReader
from memos.mem_reader.read_multi_modal.multi_modal_parser import MultiModalParser
from memos.mem_reader.read_multi_modal.video_parser import VideoParser


class FakeEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2] for _ in texts]


def make_video_message() -> dict:
    return {
        "type": "video",
        "video": {
            "url": "https://example.com/recording.mp4",
            "sample_fps": 0.6667,
            "video_id": "video-1",
            "instruction": "重点提取手机上的操作步骤",
            "source_recorded_at": "2026-08-17T15:20:00+08:00",
        },
    }


def test_video_parser_fast_mode_preserves_video_source():
    parser = VideoParser(FakeEmbedder(), MagicMock())

    items = parser.parse_fast(
        make_video_message(),
        {"user_id": "user-1", "session_id": "session-1"},
        need_emb=False,
    )

    assert len(items) == 1
    assert items[0].metadata.tags == ["mode:fast", "multimodal:video"]
    source = items[0].metadata.sources[0]
    assert source.type == "video"
    assert source.url == "https://example.com/recording.mp4"
    assert source.video_info["sample_fps"] == 0.6667
    assert source.video_info["video_id"] == "video-1"


def test_video_parser_accepts_local_media_reference():
    parser = VideoParser(FakeEmbedder(), MagicMock())

    items = parser.parse_fast(
        {
            "type": "video",
            "video": {
                "source_path": "D:/memos-media/recording.mp4",
                "sample_fps": 0.6667,
            },
        },
        {"user_id": "user-1", "session_id": "session-1"},
        need_emb=False,
    )

    source = items[0].metadata.sources[0]
    assert source.url == "D:/memos-media/recording.mp4"
    assert source.video_info["source_path"] == "D:/memos-media/recording.mp4"


def test_video_parser_fine_mode_calls_video_llm_and_returns_memory_items():
    llm = MagicMock()
    llm.generate.return_value = (
        '{"memory_list": [{"key": "打开设置", '
        '"memory_type": "UserMemory", '
        '"value": "用户打开了手机设置页面。", '
        '"tags": ["手机设置"]}], '
        '"summary": "用户在录屏中打开了手机设置页面。"}'
    )
    parser = VideoParser(FakeEmbedder(), llm)

    items = parser.parse_fine(
        make_video_message(),
        {"user_id": "user-1", "session_id": "session-1"},
        lang="zh",
    )

    assert len(items) == 1
    assert items[0].memory == "[2026-08-17 15:20（参考时间）] 用户打开了手机设置页面。"
    assert "video" in items[0].metadata.tags
    assert "visual" in items[0].metadata.tags
    assert items[0].metadata.sources[0].type == "video"

    request_messages = llm.generate.call_args.args[0]
    content = request_messages[0]["content"]
    assert content[0]["type"] == "text"
    assert "连续相同或高度重复的画面" in content[0]["text"]
    assert "重点提取手机上的操作步骤" in content[0]["text"]
    assert "2026-08-17T15:20:00+08:00" in content[0]["text"]
    assert "每条 `value` 必须以时间戳开头" in content[0]["text"]
    assert content[1] == {
        "type": "video_url",
        "video_url": {"url": "https://example.com/recording.mp4"},
    }


def test_video_parser_fine_mode_requires_dedicated_llm_config():
    parser = VideoParser(FakeEmbedder(), None)

    with pytest.raises(ConfigurationError, match="VIDEO_API_KEY"):
        parser.parse_fine(
            make_video_message(),
            {"user_id": "user-1", "session_id": "session-1"},
        )


def test_video_parser_rebuilds_message_from_source():
    parser = VideoParser(FakeEmbedder(), MagicMock())
    source = parser.create_source(
        make_video_message(),
        {"user_id": "user-1", "session_id": "session-1"},
    )

    rebuilt = parser.rebuild_from_source(source)

    assert rebuilt == make_video_message()


def test_multimodal_parser_routes_video_to_video_parser():
    video_llm = MagicMock()
    parser = MultiModalParser(
        FakeEmbedder(),
        llm=MagicMock(),
        video_parser_llm=video_llm,
    )

    selected = parser._get_parser(make_video_message())

    assert selected is parser.video_parser
    assert parser.video_parser.llm is video_llm


def test_multimodal_parser_does_not_swallow_missing_video_llm_error():
    parser = MultiModalParser(FakeEmbedder(), llm=MagicMock(), video_parser_llm=None)

    with pytest.raises(ConfigurationError, match="video_parser_llm"):
        parser.parse(
            make_video_message(),
            {"user_id": "user-1", "session_id": "session-1"},
            mode="fine",
        )


def test_multimodal_message_expansion_keeps_video_as_specialized_part():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "请记住这段操作"},
                make_video_message(),
            ],
        }
    ]

    expanded = MultiModalStructMemReader._expand_multimodal_messages(messages)

    assert make_video_message() in expanded
    assert {"role": "user", "content": "请记住这段操作"} in expanded


def test_video_only_fast_item_skips_generic_text_memory_extraction():
    parser = VideoParser(FakeEmbedder(), MagicMock())
    item = parser.parse_fast(
        make_video_message(),
        {"user_id": "user-1", "session_id": "session-1"},
        need_emb=False,
    )[0]

    assert MultiModalStructMemReader._is_file_url_only_item(item) is True
