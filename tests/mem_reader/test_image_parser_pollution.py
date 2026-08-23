from unittest.mock import MagicMock

from memos.mem_reader.read_multi_modal.image_parser import ImageParser


class FakeEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2] for _ in texts]


def make_image_message() -> dict:
    return {
        "type": "image_url",
        "image_url": {
            "url": "data:image/png;base64,abc",
            "detail": "high",
            "instruction": "只提取图片里的睡眠数据",
            "source_path": r"D:\pictures\sleep.png",
            "filename": "sleep.png",
            "mime_type": "image/png",
            "file_size": 123,
            "sha256": "abc123",
        },
    }


def test_image_instruction_is_used_as_prompt_context_not_a_memory():
    llm = MagicMock()
    llm.generate.return_value = (
        '{"memory_list": [{"key": "睡眠数据", '
        '"memory_type": "UserMemory", "value": "用户睡眠7小时。", '
        '"tags": ["睡眠"]}], "summary": "图片展示睡眠数据。"}'
    )
    parser = ImageParser(FakeEmbedder(), llm)

    items = parser.parse_fine(
        make_image_message(),
        {"user_id": "user-1", "session_id": "session-1"},
    )

    assert [item.memory for item in items] == ["用户睡眠7小时。"]
    prompt = llm.generate.call_args.args[0][0]["content"][0]["text"]
    assert "只提取图片里的睡眠数据" in prompt
    sent_image_url = llm.generate.call_args.args[0][0]["content"][1]["image_url"]["url"]
    assert sent_image_url == "data:image/png;base64,abc"

    source = items[0].metadata.sources[0]
    assert source.content == r"D:\pictures\sleep.png"
    assert source.url == r"D:\pictures\sleep.png"
    assert source.image_path == r"D:\pictures\sleep.png"
    assert source.image_info["url"] == r"D:\pictures\sleep.png"
    assert source.image_info["sha256"] == "abc123"
    assert source.image_info["inline_data_persisted"] is False
    assert "base64" not in source.model_dump_json()


def test_fast_image_memory_does_not_persist_inline_base64():
    parser = ImageParser(FakeEmbedder())

    items = parser.parse_fast(
        make_image_message(),
        {"user_id": "user-1", "session_id": "session-1"},
    )

    assert len(items) == 1
    assert "base64" not in items[0].memory
    assert "base64" not in items[0].metadata.sources[0].model_dump_json()


def test_fine_pipeline_can_temporarily_retain_inline_data_for_transfer():
    parser = ImageParser(FakeEmbedder())

    items = parser.parse_fast(
        make_image_message(),
        {"user_id": "user-1", "session_id": "session-1"},
        transient_media_source=True,
    )

    assert "base64" not in items[0].memory
    assert items[0].metadata.sources[0].url == "data:image/png;base64,abc"


def test_image_parser_failure_does_not_store_base64_fallback_memory():
    llm = MagicMock()
    llm.generate.side_effect = ValueError("vision request failed")
    parser = ImageParser(FakeEmbedder(), llm)

    items = parser.parse_fine(
        make_image_message(),
        {"user_id": "user-1", "session_id": "session-1"},
    )

    assert items == []


def test_image_parser_empty_memory_list_does_not_store_generic_summary():
    llm = MagicMock()
    llm.generate.return_value = '{"memory_list": [], "summary": "Image analyzed."}'
    parser = ImageParser(FakeEmbedder(), llm)

    items = parser.parse_fine(
        make_image_message(),
        {"user_id": "user-1", "session_id": "session-1"},
    )

    assert items == []
