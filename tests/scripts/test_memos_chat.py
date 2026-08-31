from __future__ import annotations

import hashlib
import importlib.util
import json
import sys

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock


SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "memos_chat.py"
SPEC = importlib.util.spec_from_file_location("memos_chat", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
memos_chat = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = memos_chat
SPEC.loader.exec_module(memos_chat)


def test_get_memory_encodes_the_id_and_uses_product_endpoint():
    client = memos_chat.MemOSClient("http://127.0.0.1:8000")
    client._request = MagicMock(return_value={"data": {"id": "memory/with space"}})

    result = client.get_memory("memory/with space")

    assert result == {"data": {"id": "memory/with space"}}
    client._request.assert_called_once_with("GET", "/product/get_memory/memory%2Fwith%20space")


def test_remember_video_uploads_local_file_to_oss(tmp_path: Path):
    video_path = tmp_path / "phone-recording.mp4"
    video_data = b"small fake mp4 for request-shape test"
    video_path.write_bytes(video_data)
    video_uploader = MagicMock(
        return_value=memos_chat.OSSVideoUpload(
            download_url="https://bucket.oss-cn-hangzhou.aliyuncs.com/videos/file.mp4?signed=1",
            media_uri="oss://bucket/videos/file.mp4",
            object_key="videos/file.mp4",
        )
    )
    client = memos_chat.MemOSClient("http://127.0.0.1:8000", video_uploader=video_uploader)
    client._request = MagicMock(return_value={"message": "ok"})

    result = client.remember_video(
        user_id="user-1",
        cube_id="cube-1",
        video_source=str(video_path),
        instruction="提取用户依次打开了哪些页面",
        session_id="session-1",
    )

    assert result == {"message": "ok"}
    method, endpoint, payload = client._request.call_args.args
    assert (method, endpoint) == ("POST", "/product/add")
    assert len(payload["messages"][0]["content"]) == 1
    video_part = payload["messages"][0]["content"][0]
    assert video_part["type"] == "video"
    assert video_part["video"]["url"].startswith("https://bucket.oss-cn-hangzhou.aliyuncs.com/")
    assert video_part["video"]["media_uri"] == "oss://bucket/videos/file.mp4"
    assert video_part["video"]["source_path"] == str(video_path.resolve())
    assert video_part["video"]["sha256"] == hashlib.sha256(video_data).hexdigest()
    assert "提取用户依次打开了哪些页面" in video_part["video"]["instruction"]
    assert video_part["video"]["source_recorded_at"] == memos_chat._file_source_recorded_at(
        video_path
    )
    assert "base64" not in video_part["video"]["url"]
    video_uploader.assert_called_once_with(
        video_path.resolve(), "video/mp4", hashlib.sha256(video_data).hexdigest()
    )
    assert payload["info"]["oss_object_key"] == "videos/file.mp4"
    assert payload["info"]["media_uri"] == "oss://bucket/videos/file.mp4"
    assert payload["async_mode"] == "sync"
    assert payload["mode"] == "fine"
    assert payload["session_id"] == "session-1"
    assert payload["info"]["source_recorded_at"] == video_part["video"]["source_recorded_at"]
    assert client._request.call_args.kwargs["timeout"] == 1800


def test_remember_video_accepts_http_url_without_reading_a_local_file():
    video_uploader = MagicMock()
    client = memos_chat.MemOSClient("http://127.0.0.1:8000", video_uploader=video_uploader)
    client._request = MagicMock(return_value={"message": "ok"})

    client.remember_video(
        user_id="user-1",
        cube_id="cube-1",
        video_source="https://media.example/phone-recording.mp4",
    )

    payload = client._request.call_args.args[2]
    assert len(payload["messages"][0]["content"]) == 1
    video_info = payload["messages"][0]["content"][0]["video"]
    assert video_info["url"] == "https://media.example/phone-recording.mp4"
    assert video_info["instruction"] == memos_chat.DEFAULT_VIDEO_CONTEXT
    assert "source_recorded_at" not in video_info
    assert payload["info"]["source_type"] == "remote_video"
    assert "source_recorded_at" not in payload["info"]
    video_uploader.assert_not_called()


def test_remember_image_keeps_instruction_inside_image_part(tmp_path: Path):
    image_path = tmp_path / "screen.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"fake png")
    client = memos_chat.MemOSClient("http://127.0.0.1:8000")
    client._request = MagicMock(return_value={"message": "ok"})

    client.remember_image(
        user_id="user-1",
        cube_id="cube-1",
        image_path=str(image_path),
        instruction="只提取屏幕中的睡眠数据",
    )

    payload = client._request.call_args.args[2]
    assert len(payload["messages"][0]["content"]) == 1
    image_info = payload["messages"][0]["content"][0]["image_url"]
    assert "只提取屏幕中的睡眠数据" in image_info["instruction"]
    assert image_info["source_recorded_at"]
    assert image_info["source_path"] == str(image_path.resolve())
    assert image_info["filename"] == image_path.name
    assert image_info["mime_type"] == "image/png"
    assert image_info["file_size"] == image_path.stat().st_size
    assert image_info["sha256"] == hashlib.sha256(image_path.read_bytes()).hexdigest()


def test_remember_mixed_preserves_text_image_order_and_uses_fine_mode(tmp_path: Path):
    first_image = tmp_path / "first.png"
    second_image = tmp_path / "second.jpg"
    first_image.write_bytes(b"\x89PNG\r\n\x1a\nfirst")
    second_image.write_bytes(b"\xff\xd8\xffsecond-jpeg")
    client = memos_chat.MemOSClient("http://127.0.0.1:8000")
    client._request = MagicMock(return_value={"message": "ok"})

    result = client.remember_mixed(
        user_id="user-1",
        cube_id="cube-1",
        session_id="session-1",
        entries=[
            {"type": "text", "text": "今天去了公园"},
            {"type": "image_path", "path": str(first_image)},
            {"type": "text", "text": "后来开始下雨"},
            {"type": "image_path", "path": str(second_image)},
        ],
    )

    assert result == {"message": "ok"}
    method, endpoint, payload = client._request.call_args.args
    assert (method, endpoint) == ("POST", "/product/add")
    content = payload["messages"][0]["content"]
    assert [part["type"] for part in content] == [
        "text",
        "image_url",
        "text",
        "image_url",
    ]
    assert content[0]["text"] == "今天去了公园"
    assert content[2]["text"] == "后来开始下雨"
    assert content[1]["image_url"]["source_path"] == str(first_image.resolve())
    assert content[3]["image_url"]["source_path"] == str(second_image.resolve())
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert content[3]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert payload["async_mode"] == "sync"
    assert payload["mode"] == "fine"
    assert payload["custom_tags"] == ["图文导入"]
    assert payload["info"]["source_type"] == "mixed_media"
    assert payload["info"]["ingest_batch_id"]
    assert payload["session_id"] == "session-1"
    assert client._request.call_args.kwargs["timeout"] == 600


def test_parse_mixed_markdown_preserves_text_and_relative_image_order(tmp_path: Path):
    images_dir = tmp_path / "images"
    images_dir.mkdir()
    first_image = images_dir / "玄武湖入口.png"
    second_image = images_dir / "咖啡店.jpg"
    first_image.write_bytes(b"\x89PNG\r\n\x1a\nfirst")
    second_image.write_bytes(b"\xff\xd8\xffsecond")
    markdown_path = tmp_path / "今天的记录.md"
    markdown_path.write_text(
        """# 今天的活动

今天上午去了玄武湖。

![玄武湖入口](images/玄武湖入口.png)

<!-- 这个备注不应发送给模型 -->
后来开始下雨，于是去了咖啡店。

![咖啡店](images/咖啡店.jpg)
""",
        encoding="utf-8",
    )

    resolved_path, entries = memos_chat.parse_mixed_markdown(str(markdown_path))

    assert resolved_path == markdown_path.resolve()
    assert [entry["type"] for entry in entries] == [
        "text",
        "image_path",
        "text",
        "image_path",
    ]
    assert "今天上午去了玄武湖" in entries[0]["text"]
    assert entries[1] == {
        "type": "image_path",
        "path": str(first_image.resolve()),
        "alt": "玄武湖入口",
    }
    assert "这个备注" not in "".join(entry.get("text", "") for entry in entries)
    assert "后来开始下雨" in entries[2]["text"]
    assert entries[3]["path"] == str(second_image.resolve())


def test_parse_mixed_markdown_reports_missing_image(tmp_path: Path):
    markdown_path = tmp_path / "记录.md"
    markdown_path.write_text("今天出门了。\n\n![照片](missing.png)", encoding="utf-8")

    try:
        memos_chat.parse_mixed_markdown(str(markdown_path))
    except memos_chat.MemOSClientError as exc:
        assert "missing.png" in str(exc)
        assert "找不到图片" in str(exc)
    else:
        raise AssertionError("missing Markdown image should fail")


def test_remember_mixed_records_markdown_source_and_uses_image_alt_text(tmp_path: Path):
    image_path = tmp_path / "screen.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    markdown_path = tmp_path / "记录.md"
    markdown_path.write_text("placeholder", encoding="utf-8")
    client = memos_chat.MemOSClient("http://127.0.0.1:8000")
    client._request = MagicMock(return_value={"message": "ok"})

    client.remember_mixed(
        user_id="user-1",
        cube_id="cube-1",
        entries=[
            {"type": "text", "text": "今天查看了睡眠记录"},
            {"type": "image_path", "path": str(image_path), "alt": "昨晚睡眠数据"},
        ],
        source_document_path=str(markdown_path),
    )

    payload = client._request.call_args.args[2]
    content = payload["messages"][0]["content"]
    assert [part["type"] for part in content] == ["text", "text", "image_url"]
    assert content[1]["text"] == "图片说明：昨晚睡眠数据"
    assert payload["info"]["source_document_path"] == str(markdown_path.resolve())
    assert payload["info"]["source_document_filename"] == "记录.md"


def test_runtime_help_lists_markdown_mixed_import_command():
    assert ':mixed-file "记录.md"' in memos_chat.HELP


def test_classify_import_file_routes_plain_text_to_text(tmp_path: Path):
    text_path = tmp_path / "记录.txt"
    text_path.write_text("今天完成了项目汇报。", encoding="utf-8")

    imported = memos_chat.classify_import_file(str(text_path))

    assert imported.kind == "text"
    assert imported.path == text_path.resolve()
    assert imported.text == "今天完成了项目汇报。"
    assert imported.entries == []


def test_classify_import_file_routes_one_image_to_image(tmp_path: Path):
    image_path = tmp_path / "睡眠.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nimage")

    imported = memos_chat.classify_import_file(str(image_path))

    assert imported.kind == "image"
    assert imported.path == image_path.resolve()


def test_classify_import_file_routes_markdown_text_and_image_to_mixed(tmp_path: Path):
    image_path = tmp_path / "睡眠.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    markdown_path = tmp_path / "记录.md"
    markdown_path.write_text(
        "今天查看了睡眠数据。\n\n![睡眠记录](睡眠.png)",
        encoding="utf-8",
    )

    imported = memos_chat.classify_import_file(str(markdown_path))

    assert imported.kind == "mixed"
    assert [entry["type"] for entry in imported.entries] == ["text", "image_path"]


def test_classify_import_file_routes_text_only_markdown_to_text(tmp_path: Path):
    markdown_path = tmp_path / "记录.md"
    markdown_path.write_text("# 今日记录\n\n今天完成了项目汇报。", encoding="utf-8")

    imported = memos_chat.classify_import_file(str(markdown_path))

    assert imported.kind == "text"
    assert "今天完成了项目汇报" in imported.text


def test_import_memory_file_routes_text_file_to_fine_text_parser(tmp_path: Path):
    text_path = tmp_path / "记录.txt"
    text_path.write_text("今天完成了项目汇报。", encoding="utf-8")
    client = MagicMock()
    client.remember_text_file.return_value = {"message": "ok", "data": []}

    imported, result = memos_chat.import_memory_file(
        client,
        user_id="user-1",
        cube_id="cube-1",
        session_id="session-1",
        file_path=str(text_path),
    )

    assert imported.kind == "text"
    assert result["message"] == "ok"
    client.remember_text_file.assert_called_once_with(
        user_id="user-1",
        cube_id="cube-1",
        text="今天完成了项目汇报。",
        source_path=str(text_path.resolve()),
        session_id="session-1",
    )


def test_classify_import_file_rejects_unsupported_extension(tmp_path: Path):
    file_path = tmp_path / "记录.bin"
    file_path.write_bytes(b"binary")

    try:
        memos_chat.classify_import_file(str(file_path))
    except memos_chat.MemOSClientError as exc:
        assert "不支持" in str(exc)
    else:
        raise AssertionError("unsupported files should fail")


def test_runtime_help_lists_one_file_auto_import_command():
    assert ':import "文件路径"' in memos_chat.HELP


def test_successful_add_skips_topic_when_disabled(monkeypatch):
    process = MagicMock()
    monkeypatch.setitem(
        sys.modules,
        "memos_topic",
        SimpleNamespace(process_runtime_topics=process),
    )
    monkeypatch.setenv("MEMOS_TOPIC_ENABLED", "0")
    response = MagicMock()
    response.read.return_value = json.dumps(
        {
            "code": 200,
            "message": "Memory added successfully",
            "data": [{"memory_id": "memory-1", "memory": "测试记忆"}],
        }
    ).encode("utf-8")
    response.__enter__.return_value = response
    client = memos_chat.MemOSClient("http://127.0.0.1:8000")
    client.opener.open = MagicMock(return_value=response)
    payload = {
        "user_id": "user-1",
        "writable_cube_ids": ["cube-1"],
        "messages": "测试记忆",
        "info": {"source_type": "text_input", "ingest_batch_id": "batch-1"},
    }

    client._request("POST", "/product/add", payload)

    process.assert_not_called()


def test_successful_add_automatically_processes_rolling_topics(monkeypatch):
    process = MagicMock(
        return_value={
            "processed_memories": 1,
            "rolling_limit": 15,
            "topics": [
                {
                    "topic_text": "用户正在推进视频解析器开发。",
                    "reason_summary": "开发计划和交付时间已经明确。",
                }
            ],
        }
    )
    monkeypatch.setitem(
        sys.modules,
        "memos_topic",
        SimpleNamespace(process_runtime_topics=process),
    )
    monkeypatch.setenv("MEMOS_TOPIC_ENABLED", "1")
    response = MagicMock()
    response.read.return_value = json.dumps(
        {
            "code": 200,
            "message": "Memory added successfully",
            "data": [{"memory_id": "memory-1", "memory": "测试记忆"}],
        }
    ).encode("utf-8")
    response.__enter__.return_value = response
    client = memos_chat.MemOSClient("http://127.0.0.1:8000")
    client.opener.open = MagicMock(return_value=response)
    payload = {
        "user_id": "user-1",
        "writable_cube_ids": ["cube-1"],
        "messages": "测试记忆",
        "info": {"source_type": "text_input", "ingest_batch_id": "batch-1"},
    }

    client._request("POST", "/product/add", payload)

    process.assert_called_once_with(
        base_url="http://127.0.0.1:8000",
        user_id="user-1",
        cube_id="cube-1",
        add_response={
            "code": 200,
            "message": "Memory added successfully",
            "data": [{"memory_id": "memory-1", "memory": "测试记忆"}],
        },
    )
    assert client.last_topic_update["processed_memories"] == 1


def test_print_topic_update_shows_generated_topic_and_reason(capsys):
    client = memos_chat.MemOSClient("http://127.0.0.1:8000")
    client.last_topic_update = {
        "processed_memories": 1,
        "rolling_limit": 15,
        "topics": [
            {
                "topic_text": "用户正在推进视频解析器开发。",
                "reason_summary": "开发任务和交付时间已经明确。",
            }
        ],
    }

    memos_chat._print_topic_update(client)

    output = capsys.readouterr().out
    assert "自动处理了 1 条新记忆" in output
    assert "当前滚动 Topic（最多 15 个）" in output
    assert "用户正在推进视频解析器开发" in output
    assert "开发任务和交付时间已经明确" in output


def test_zero_memory_import_result_does_not_claim_memory_or_topic_queue(capsys, tmp_path: Path):
    imported = memos_chat.ImportedFile(path=tmp_path / "empty.md", kind="mixed")

    memos_chat._print_import_result(
        imported,
        {"code": 200, "message": "Memory added successfully", "data": []},
    )

    output = capsys.readouterr().out
    assert "本次没有生成可保存的记忆" in output
    assert "并写入记忆" not in output
    assert "已加入外部 Topic" not in output


def test_chat_does_not_write_conversation_to_memory_by_default():
    client = memos_chat.MemOSClient("http://127.0.0.1:8000")
    client._request = MagicMock(return_value={"data": {"response": "ok"}})

    client.chat(
        user_id="user-1",
        cube_id="cube-1",
        session_id="session-1",
        query="我是谁",
        model=None,
    )

    payload = client._request.call_args.args[2]
    assert payload["add_message_on_answer"] is False


def test_wait_until_healthy_retries_a_temporary_disconnect(monkeypatch):
    client = MagicMock()
    client.health.side_effect = [
        memos_chat.MemOSClientError("connection closed"),
        {"status": "healthy"},
    ]
    sleep = MagicMock()
    monkeypatch.setattr(memos_chat.time, "sleep", sleep)

    result = memos_chat.wait_until_healthy(client, startup_timeout=10, poll_interval=0.01)

    assert result == {"status": "healthy"}
    assert client.health.call_count == 2
    sleep.assert_called_once()


def test_wait_until_healthy_reports_timeout_without_traceback():
    client = MagicMock()
    client.health.side_effect = memos_chat.MemOSClientError("connection closed")

    try:
        memos_chat.wait_until_healthy(client, startup_timeout=0)
    except memos_chat.MemOSClientError as exc:
        assert "没有准备完成" in str(exc)
        assert "connection closed" in str(exc)
    else:
        raise AssertionError("expected startup timeout")


def test_remember_auto_detection_recognizes_supported_video_path(tmp_path: Path):
    video_path = tmp_path / "frames.webm"
    video_path.write_bytes(b"fake webm")

    assert memos_chat._looks_like_video_source(str(video_path))
    assert memos_chat._looks_like_video_source("https://media.example/recording.mov")
    assert not memos_chat._looks_like_video_source("今天打开了手机设置")


def test_runtime_help_points_to_dedicated_server_side_video_credentials():
    assert "VIDEO_PARSER_MODEL" in memos_chat.HELP
    assert "VIDEO_API_KEY" in memos_chat.HELP
    assert "VIDEO_API_BASE" in memos_chat.HELP
    assert "QWEN_API_KEY" not in memos_chat.HELP


def test_runtime_help_lists_required_oss_settings():
    assert "OSS_REGION" in memos_chat.HELP
    assert "OSS_BUCKET" in memos_chat.HELP
    assert "OSS_ACCESS_KEY_ID" in memos_chat.HELP
    assert "OSS_ACCESS_KEY_SECRET" in memos_chat.HELP


def test_read_oss_video_config_reports_all_missing_required_settings(monkeypatch):
    for name in (
        "OSS_REGION",
        "OSS_BUCKET",
        "OSS_ACCESS_KEY_ID",
        "OSS_ACCESS_KEY_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)

    try:
        memos_chat._read_oss_video_config()
    except memos_chat.MemOSClientError as exc:
        message = str(exc)
    else:
        raise AssertionError("missing OSS configuration should fail")

    assert "OSS_REGION" in message
    assert "OSS_BUCKET" in message
    assert "OSS_ACCESS_KEY_ID" in message
    assert "OSS_ACCESS_KEY_SECRET" in message


def test_upload_video_to_oss_uploads_private_object_and_presigns_get(tmp_path, monkeypatch):
    video_path = tmp_path / "recording.mp4"
    video_path.write_bytes(b"fake video")
    for name, value in {
        "OSS_REGION": "cn-hangzhou",
        "OSS_BUCKET": "private-memory-videos",
        "OSS_ACCESS_KEY_ID": "test-access-key-id",
        "OSS_ACCESS_KEY_SECRET": "test-access-key-secret",
        "OSS_ENDPOINT": "https://oss-cn-hangzhou.aliyuncs.com",
        "OSS_OBJECT_PREFIX": "memos/videos",
        "OSS_SIGNED_URL_EXPIRES_SECONDS": "7200",
    }.items():
        monkeypatch.setenv(name, value)

    sdk_config = SimpleNamespace(credentials_provider=None, region=None, endpoint=None)
    client = MagicMock()
    client.presign.return_value = SimpleNamespace(
        url="https://private-memory-videos.oss-cn-hangzhou.aliyuncs.com/file.mp4?signed=1"
    )
    fake_oss = SimpleNamespace(
        config=SimpleNamespace(load_default=MagicMock(return_value=sdk_config)),
        credentials=SimpleNamespace(
            EnvironmentVariableCredentialsProvider=MagicMock(return_value="credentials")
        ),
        Client=MagicMock(return_value=client),
        PutObjectRequest=lambda **kwargs: SimpleNamespace(**kwargs),
        GetObjectRequest=lambda **kwargs: SimpleNamespace(**kwargs),
    )
    monkeypatch.setitem(sys.modules, "alibabacloud_oss_v2", fake_oss)

    result = memos_chat._upload_video_to_oss(video_path, "video/mp4", "abc123")

    put_request, uploaded_path = client.put_object_from_file.call_args.args
    assert put_request.bucket == "private-memory-videos"
    assert put_request.key == "memos/videos/abc123.mp4"
    assert put_request.content_type == "video/mp4"
    assert uploaded_path == str(video_path)
    get_request = client.presign.call_args.args[0]
    assert get_request.bucket == "private-memory-videos"
    assert get_request.key == "memos/videos/abc123.mp4"
    assert client.presign.call_args.kwargs["expires"] == timedelta(seconds=7200)
    assert result.media_uri == "oss://private-memory-videos/memos/videos/abc123.mp4"
    assert result.download_url.endswith("?signed=1")


def test_import_memory_file_forwards_optional_instruction_to_video(tmp_path: Path):
    video_path = tmp_path / "phone-recording.mp4"
    video_path.write_bytes(b"fake video")
    client = SimpleNamespace(remember_video=MagicMock(return_value={"message": "ok"}))

    imported, result = memos_chat.import_memory_file(
        client,
        user_id="default",
        cube_id="default_cube",
        session_id="web-upload",
        file_path=str(video_path),
        instruction="重点识别用户依次打开了哪些页面",
    )

    assert imported.kind == "video"
    assert result == {"message": "ok"}
    assert client.remember_video.call_args.kwargs["instruction"] == "重点识别用户依次打开了哪些页面"
