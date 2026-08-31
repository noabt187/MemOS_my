#!/usr/bin/env python3
"""Small interactive client for a locally deployed MemOS REST API.

Normal input chats with MemOS.  The client also exposes a few colon commands:

    :remember <text-or-media>       Store text, a local image, or a video.
    :import <file-path>             Auto-detect text, image, Markdown, or video.
    :mixed-file <markdown-path>     Import ordered text and local images from Markdown.
    :image <path> [| focus]         Store a local image with optional focus.
    :video <path-or-url> [| focus]  Store a video with optional focus.
    :search <query>                 Search the current memory cube.
    :help                           Show help.
    :quit                           Exit.

Local video upload uses the OSS SDK already declared by MemOS's ``skill-mem`` extra.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


MAX_IMAGE_BYTES = 10 * 1024 * 1024
SUPPORTED_IMAGE_EXTENSIONS = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
SUPPORTED_TEXT_EXTENSIONS = {".text", ".txt"}
SUPPORTED_MARKDOWN_EXTENSIONS = {".markdown", ".md"}
SUPPORTED_VIDEO_EXTENSIONS = {
    ".avi",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".webm",
}
DEFAULT_IMAGE_CONTEXT = (
    "用户正在把这张图片导入记忆库。请使用系统内置的图片解析流程，"
    "识别图片内容并提取值得长期记住的信息；请使用中文，且不要猜测图片中不存在的信息。"
)
DEFAULT_VIDEO_CONTEXT = (
    "用户正在把这段视频导入记忆库。请使用系统内置的视频解析流程，"
    "按时间顺序识别界面变化、用户操作和可见结果，合并连续重复画面，"
    "提取值得长期记住的行为与偏好；请使用中文，且不要猜测视频中不存在的信息。"
)


class MemOSClientError(RuntimeError):
    """Raised when the local MemOS API cannot satisfy a request."""


@dataclass(frozen=True)
class OSSVideoConfig:
    """Minimal OSS settings needed to upload a private video."""

    region: str
    bucket: str
    endpoint: str | None
    object_prefix: str
    signed_url_expires_seconds: int


@dataclass(frozen=True)
class OSSVideoUpload:
    """References returned after uploading a local video to OSS."""

    download_url: str
    media_uri: str
    object_key: str


@dataclass(frozen=True)
class ImportedFile:
    """A local file classified into one existing MemOS ingestion path."""

    kind: str
    path: Path
    text: str = ""
    entries: list[dict[str, str]] | None = None
    instruction: str | None = None

    def __post_init__(self) -> None:
        if self.entries is None:
            object.__setattr__(self, "entries", [])


VideoUploader = Callable[[Path, str, str], OSSVideoUpload]


def _current_source_recorded_at() -> str:
    """Return the source time for content created interactively right now."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _file_source_recorded_at(path: Path) -> str:
    """Use a local file's own timestamp instead of its later import time."""
    return datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds")


def _load_project_env() -> None:
    """Load simple KEY=VALUE entries from the repository's private .env file."""
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.is_file():
        return

    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[7:].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


def _read_oss_video_config() -> OSSVideoConfig:
    required_names = (
        "OSS_REGION",
        "OSS_BUCKET",
        "OSS_ACCESS_KEY_ID",
        "OSS_ACCESS_KEY_SECRET",
    )
    missing = [name for name in required_names if not os.getenv(name, "").strip()]
    if missing:
        raise MemOSClientError("本地视频上传缺少 OSS 配置：" + "、".join(missing))

    expires_text = os.getenv("OSS_SIGNED_URL_EXPIRES_SECONDS", "7200").strip()
    try:
        expires_seconds = int(expires_text)
    except ValueError as exc:
        raise MemOSClientError("OSS_SIGNED_URL_EXPIRES_SECONDS 必须是整数秒数。") from exc
    if not 60 <= expires_seconds <= 7 * 24 * 60 * 60:
        raise MemOSClientError("OSS_SIGNED_URL_EXPIRES_SECONDS 必须在 60 到 604800 秒之间。")

    return OSSVideoConfig(
        region=os.environ["OSS_REGION"].strip(),
        bucket=os.environ["OSS_BUCKET"].strip(),
        endpoint=os.getenv("OSS_ENDPOINT", "").strip() or None,
        object_prefix=os.getenv("OSS_OBJECT_PREFIX", "memos/videos").strip("/") or "memos/videos",
        signed_url_expires_seconds=expires_seconds,
    )


def _upload_video_to_oss(path: Path, mime_type: str, sha256: str) -> OSSVideoUpload:
    """Upload a local video to private OSS and return a temporary GET URL."""
    config = _read_oss_video_config()
    try:
        import alibabacloud_oss_v2 as oss
    except ImportError as exc:
        raise MemOSClientError(
            "缺少 OSS SDK。请使用 start_memos_chat.ps1 或 start_memos_chat.cmd 启动。"
        ) from exc

    object_key = f"{config.object_prefix}/{sha256}{path.suffix.lower()}"
    try:
        sdk_config = oss.config.load_default()
        sdk_config.credentials_provider = oss.credentials.EnvironmentVariableCredentialsProvider()
        sdk_config.region = config.region
        if config.endpoint:
            sdk_config.endpoint = config.endpoint

        client = oss.Client(sdk_config)
        client.put_object_from_file(
            oss.PutObjectRequest(
                bucket=config.bucket,
                key=object_key,
                content_type=mime_type,
                metadata={"sha256": sha256},
            ),
            str(path),
        )
        signed = client.presign(
            oss.GetObjectRequest(bucket=config.bucket, key=object_key),
            expires=timedelta(seconds=config.signed_url_expires_seconds),
        )
    except Exception as exc:
        raise MemOSClientError(f"视频上传 OSS 失败：{exc}") from exc

    return OSSVideoUpload(
        download_url=signed.url,
        media_uri=f"oss://{config.bucket}/{object_key}",
        object_key=object_key,
    )


def _strip_matching_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1].strip()
    return value


def split_image_input(value: str) -> tuple[str, str | None]:
    """Split ``path | optional focus`` entered by the user."""
    raw = value.strip()
    if " | " in raw:
        path, instruction = raw.split(" | ", 1)
        return _strip_matching_quotes(path), instruction.strip() or None
    return _strip_matching_quotes(raw), None


def _detect_image_mime(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"BM"):
        return "image/bmp"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _load_image(image_path: str) -> tuple[Path, bytes, str]:
    path = Path(image_path).expanduser()
    try:
        path = path.resolve(strict=True)
    except OSError as exc:
        raise MemOSClientError(f"找不到图片：{image_path}") from exc
    if not path.is_file():
        raise MemOSClientError(f"这不是一个文件：{path}")
    size = path.stat().st_size
    if size > MAX_IMAGE_BYTES:
        raise MemOSClientError(
            f"图片为 {size / 1024 / 1024:.1f} MB，超过当前 10 MB 限制；请先压缩图片。"
        )
    data = path.read_bytes()
    mime_type = _detect_image_mime(data)
    if mime_type is None:
        raise MemOSClientError("文件不是受支持的图片；目前支持 PNG、JPEG、GIF、BMP 和 WebP。")
    return path, data, mime_type


def _iter_markdown_images(markdown: str):
    """Yield Markdown image spans while allowing parentheses inside image paths."""
    cursor = 0
    while True:
        start = markdown.find("![", cursor)
        if start < 0:
            return
        alt_end = markdown.find("](", start + 2)
        if alt_end < 0:
            return

        depth = 1
        target_start = alt_end + 2
        index = target_start
        while index < len(markdown) and depth:
            if markdown[index] == "(":
                depth += 1
            elif markdown[index] == ")":
                depth -= 1
            index += 1
        if depth:
            return

        yield start, index, markdown[start + 2 : alt_end], markdown[target_start : index - 1]
        cursor = index


def _markdown_image_target(raw_target: str) -> str:
    """Remove optional Markdown title syntax from one local image target."""
    target = raw_target.strip()
    if target.startswith("<"):
        closing = target.find(">")
        if closing < 0:
            raise MemOSClientError(f"Markdown 图片路径缺少结束符号 >：{raw_target}")
        return target[1:closing].strip()

    titled = re.match(r"^(?P<path>.+?)\s+(?P<quote>['\"]).*(?P=quote)$", target)
    if titled:
        return titled.group("path").strip()
    return target


def parse_mixed_markdown(markdown_path: str) -> tuple[Path, list[dict[str, str]]]:
    """Read ordered text and local-image references from a Markdown document."""
    path = Path(_strip_matching_quotes(markdown_path)).expanduser()
    try:
        path = path.resolve(strict=True)
    except OSError as exc:
        raise MemOSClientError(f"找不到 Markdown 文件：{markdown_path}") from exc
    if not path.is_file():
        raise MemOSClientError(f"这不是一个文件：{path}")
    if path.suffix.lower() not in {".md", ".markdown"}:
        raise MemOSClientError("图文文件必须是 .md 或 .markdown 格式。")

    try:
        markdown = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise MemOSClientError("Markdown 文件不是 UTF-8 编码，请先另存为 UTF-8。") from exc
    markdown = re.sub(r"<!--.*?-->", "", markdown, flags=re.DOTALL)

    entries: list[dict[str, str]] = []
    cursor = 0
    image_count = 0
    for start, end, alt, raw_target in _iter_markdown_images(markdown):
        text_before = markdown[cursor:start].strip()
        if text_before:
            entries.append({"type": "text", "text": text_before})

        target = _markdown_image_target(raw_target)
        parsed_target = urllib.parse.urlparse(target)
        if parsed_target.scheme.lower() in {"http", "https"}:
            raise MemOSClientError(f"Markdown 暂不支持网络图片，请先下载到本地：{target}")

        candidate = Path(target).expanduser()
        if not candidate.is_absolute():
            candidate = path.parent / candidate
        try:
            image_path, _, _ = _load_image(str(candidate))
        except MemOSClientError as exc:
            raise MemOSClientError(f"Markdown 中找不到图片 {target}：{exc}") from exc

        image_entry = {"type": "image_path", "path": str(image_path)}
        if alt.strip():
            image_entry["alt"] = alt.strip()
        entries.append(image_entry)
        image_count += 1
        cursor = end

    remaining_text = markdown[cursor:].strip()
    if remaining_text:
        entries.append({"type": "text", "text": remaining_text})

    if image_count == 0:
        raise MemOSClientError("Markdown 中没有找到图片，请使用 ![说明](图片路径) 插入图片。")
    return path, entries


def _resolve_import_path(file_path: str) -> Path:
    path = Path(_strip_matching_quotes(file_path)).expanduser()
    try:
        path = path.resolve(strict=True)
    except OSError as exc:
        raise MemOSClientError(f"找不到文件：{file_path}") from exc
    if not path.is_file():
        raise MemOSClientError(f"这不是一个文件：{path}")
    return path


def _read_text_file(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = data.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
        if not text:
            raise MemOSClientError(f"文件中没有可导入的文字：{path}")
        return text
    raise MemOSClientError(f"无法读取文字文件，请将它另存为 UTF-8：{path}")


def classify_import_file(file_path: str) -> ImportedFile:
    """Classify one local file without asking the user to choose a parser."""
    path = _resolve_import_path(file_path)
    suffix = path.suffix.lower()

    if suffix in SUPPORTED_IMAGE_EXTENSIONS:
        image_path, _, _ = _load_image(str(path))
        return ImportedFile(kind="image", path=image_path)

    if suffix in SUPPORTED_VIDEO_EXTENSIONS:
        return ImportedFile(kind="video", path=path)

    if suffix in SUPPORTED_TEXT_EXTENSIONS:
        return ImportedFile(kind="text", path=path, text=_read_text_file(path))

    if suffix in SUPPORTED_MARKDOWN_EXTENSIONS:
        markdown = _read_text_file(path)
        if next(_iter_markdown_images(markdown), None) is None:
            return ImportedFile(kind="text", path=path, text=markdown)

        markdown_path, entries = parse_mixed_markdown(str(path))
        text_entries = [entry for entry in entries if entry.get("type") == "text"]
        image_entries = [entry for entry in entries if entry.get("type") == "image_path"]
        if not text_entries and len(image_entries) == 1:
            image = image_entries[0]
            return ImportedFile(
                kind="image",
                path=Path(image["path"]),
                instruction=image.get("alt") or None,
            )
        return ImportedFile(kind="mixed", path=markdown_path, entries=entries)

    supported = "、".join(
        sorted(
            SUPPORTED_IMAGE_EXTENSIONS
            | SUPPORTED_TEXT_EXTENSIONS
            | SUPPORTED_MARKDOWN_EXTENSIONS
            | SUPPORTED_VIDEO_EXTENSIONS
        )
    )
    raise MemOSClientError(f"不支持 {suffix or '无扩展名'} 文件；目前支持：{supported}")


def _looks_like_image_path(value: str) -> bool:
    path_text, _ = split_image_input(value)
    path = Path(path_text).expanduser()
    return path.is_file() or (
        path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
        and path.is_absolute()
    )


def _is_http_url(value: str) -> bool:
    parsed = urllib.parse.urlparse(value)
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def _looks_like_video_source(value: str) -> bool:
    source, _ = split_image_input(value)
    if _is_http_url(source):
        url_path = urllib.parse.urlparse(source).path
        return Path(url_path).suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS

    path = Path(source).expanduser()
    return (path.is_file() and path.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS) or (
        path.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS and path.is_absolute()
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as video_file:
        while chunk := video_file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _load_video_reference(video_path: str) -> tuple[Path, str, int, str]:
    path = Path(video_path).expanduser()
    try:
        path = path.resolve(strict=True)
    except OSError as exc:
        raise MemOSClientError(f"找不到视频：{video_path}") from exc
    if not path.is_file():
        raise MemOSClientError(f"这不是一个文件：{path}")
    if path.suffix.lower() not in SUPPORTED_VIDEO_EXTENSIONS:
        supported = "、".join(sorted(SUPPORTED_VIDEO_EXTENSIONS))
        raise MemOSClientError(f"不支持这个视频格式；目前支持：{supported}")

    mime_type = mimetypes.guess_type(path.name)[0] or "video/mp4"
    return path, mime_type, path.stat().st_size, _sha256_file(path)


class MemOSClient:
    def __init__(
        self,
        base_url: str,
        timeout: int = 180,
        video_uploader: VideoUploader | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.video_uploader = video_uploader or _upload_video_to_oss
        self.last_topic_update: dict[str, Any] | None = None
        self.last_topic_error: str | None = None
        # Do not send localhost requests through Windows/system HTTP proxies.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        timeout: int | None = None,
    ) -> Any:
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"

        request = urllib.request.Request(
            f"{self.base_url}{path}", data=body, headers=headers, method=method
        )
        effective_timeout = timeout if timeout is not None else self.timeout
        try:
            with self.opener.open(request, timeout=effective_timeout) as response:
                text = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            raise MemOSClientError(f"HTTP {exc.code}: {text}") from exc
        except urllib.error.URLError as exc:
            raise MemOSClientError(f"无法连接 MemOS：{exc.reason}") from exc
        except (TimeoutError, OSError) as exc:
            if isinstance(exc, TimeoutError) or "timed out" in str(exc).lower():
                raise MemOSClientError(
                    f"等待 MemOS 超过 {effective_timeout} 秒。后端可能仍在处理，"
                    "请稍后先用 :search 检查，不要立即重复导入。"
                ) from exc
            raise MemOSClientError(f"与 MemOS 的连接中断：{exc}") from exc

        if not text:
            return None
        try:
            result = json.loads(text)
        except json.JSONDecodeError as exc:
            raise MemOSClientError(f"MemOS 返回的不是 JSON：{text[:500]}") from exc

        if path == "/product/add" and payload and _topic_enabled():
            self.last_topic_update = None
            self.last_topic_error = None
            try:
                from memos_topic import process_runtime_topics

                user_id = str(payload.get("user_id") or "default")
                cube_id = str((payload.get("writable_cube_ids") or ["default_cube"])[0])
                if isinstance(result.get("data"), list) and result["data"]:
                    self.last_topic_update = process_runtime_topics(
                        base_url=self.base_url,
                        user_id=user_id,
                        cube_id=cube_id,
                        add_response=result,
                    )
            except (ImportError, OSError, RuntimeError, ValueError) as exc:
                self.last_topic_error = str(exc)
        return result

    def health(self, timeout: int = 5) -> Any:
        return self._request("GET", "/health", timeout=timeout)

    def get_memory(self, memory_id: str) -> Any:
        """Return one memory by id through the existing MemOS product API."""
        encoded_id = urllib.parse.quote(memory_id.strip(), safe="")
        if not encoded_id:
            raise MemOSClientError("记忆 ID 不能为空。")
        return self._request("GET", f"/product/get_memory/{encoded_id}")

    def get_memory_dashboard(self, *, user_id: str, cube_id: str) -> Any:
        """Return the raw dashboard payload for the application backend."""
        return self._request(
            "POST",
            "/product/get_memory_dashboard",
            {
                "mem_cube_id": cube_id,
                "user_id": user_id,
                "include_preference": True,
                "include_tool_memory": True,
                "include_skill_memory": True,
                "page": 1,
                "page_size": 500,
            },
        )

    def get_scheduler_status(self) -> Any:
        """Return MemOS scheduler status for backend-side dashboard aggregation."""
        return self._request("GET", "/product/scheduler/allstatus")

    def get_task_queue_status(self, *, user_id: str) -> Any:
        """Return the task queue status for one application user."""
        encoded_user_id = urllib.parse.quote(user_id.strip(), safe="")
        return self._request(
            "GET", f"/product/scheduler/task_queue_status?user_id={encoded_user_id}"
        )

    def delete_memory(self, memory_id: str) -> Any:
        """Delete one memory through the existing MemOS product API."""
        normalized_id = memory_id.strip()
        if not normalized_id:
            raise MemOSClientError("记忆 ID 不能为空。")
        return self._request(
            "POST",
            "/product/delete_memory",
            {"memory_ids": [normalized_id], "auto_cleanup_working": True},
        )

    def chat(
        self,
        *,
        user_id: str,
        cube_id: str,
        session_id: str,
        query: str,
        model: str | None,
    ) -> str:
        payload: dict[str, Any] = {
            "user_id": user_id,
            "query": query,
            "readable_cube_ids": [cube_id],
            "writable_cube_ids": [cube_id],
            "session_id": session_id,
            "mode": "fast",
            "top_k": 5,
            # Normal chat is read-only by default. Explicit :remember/:image/:video
            # commands are the only paths that write memories from this client.
            "add_message_on_answer": False,
        }
        if model:
            payload["model_name_or_path"] = model

        result = self._request("POST", "/product/chat/complete", payload)
        if not isinstance(result, dict):
            return str(result)
        data = result.get("data", {})
        if isinstance(data, dict):
            return str(data.get("response") or data.get("answer") or data)
        return str(data or result)

    def remember(self, *, user_id: str, cube_id: str, text: str) -> Any:
        source_recorded_at = _current_source_recorded_at()
        return self._request(
            "POST",
            "/product/add",
            {
                "user_id": user_id,
                "writable_cube_ids": [cube_id],
                "messages": text,
                "async_mode": "sync",
                "mode": "fine",
                "info": {
                    "source_type": "text_input",
                    "ingest_batch_id": f"text-{uuid.uuid4().hex}",
                    "source_recorded_at": source_recorded_at,
                },
            },
        )

    def remember_text_file(
        self,
        *,
        user_id: str,
        cube_id: str,
        text: str,
        source_path: str,
        session_id: str | None = None,
    ) -> Any:
        """Import a text file through the complete text extraction path."""
        path = _resolve_import_path(source_path)
        source_recorded_at = _file_source_recorded_at(path)
        payload: dict[str, Any] = {
            "user_id": user_id,
            "writable_cube_ids": [cube_id],
            "messages": text,
            "async_mode": "sync",
            "mode": "fine",
            "custom_tags": ["文字文件导入"],
            "info": {
                "source_type": "local_text_file",
                "ingest_batch_id": f"text-file-{uuid.uuid4().hex}",
                "source_path": str(path),
                "filename": path.name,
                "file_size": path.stat().st_size,
                "source_recorded_at": source_recorded_at,
            },
        }
        if session_id:
            payload["session_id"] = session_id
        return self._request("POST", "/product/add", payload, timeout=max(self.timeout, 600))

    def remember_image(
        self,
        *,
        user_id: str,
        cube_id: str,
        image_path: str,
        instruction: str | None = None,
        session_id: str | None = None,
    ) -> Any:
        """Import one local image through MemOS's built-in ImageParser."""
        path, data, mime_type = _load_image(image_path)
        data_url = f"data:{mime_type};base64," + base64.b64encode(data).decode("ascii")
        sha256 = hashlib.sha256(data).hexdigest()
        context = DEFAULT_IMAGE_CONTEXT
        if instruction:
            context += f"\n用户额外关注：{instruction.strip()}"
        source_recorded_at = _file_source_recorded_at(path)

        payload: dict[str, Any] = {
            "user_id": user_id,
            "writable_cube_ids": [cube_id],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": data_url,
                                "detail": "high",
                                "instruction": context,
                                "source_path": str(path),
                                "filename": path.name,
                                "mime_type": mime_type,
                                "file_size": len(data),
                                "sha256": sha256,
                                "source_recorded_at": source_recorded_at,
                            },
                        },
                    ],
                }
            ],
            "async_mode": "sync",
            "mode": "fine",
            "custom_tags": ["图片导入"],
            "info": {
                "source_type": "local_image",
                "ingest_batch_id": f"image-{uuid.uuid4().hex}",
                "source_path": str(path),
                "filename": path.name,
                "mime_type": mime_type,
                "file_size": len(data),
                "sha256": sha256,
                "source_recorded_at": source_recorded_at,
            },
        }
        if session_id:
            payload["session_id"] = session_id
        # Image fine-mode invokes several extraction stages and can take minutes.
        return self._request("POST", "/product/add", payload, timeout=max(self.timeout, 600))

    def remember_mixed(
        self,
        *,
        user_id: str,
        cube_id: str,
        entries: list[dict[str, str]],
        session_id: str | None = None,
        source_document_path: str | None = None,
    ) -> Any:
        """Submit ordered text and images for one joint MemOS analysis."""
        if not entries:
            raise MemOSClientError("图文内容不能为空。")

        content: list[dict[str, Any]] = []
        text_count = 0
        image_count = 0
        source_recorded_at = _current_source_recorded_at()

        for entry in entries:
            entry_type = entry.get("type")
            if entry_type == "text":
                value = entry.get("text", "").strip()
                if not value:
                    raise MemOSClientError("图文内容中不能包含空文字。")
                content.append({"type": "text", "text": value})
                text_count += 1
                continue

            if entry_type == "image_path":
                image_path = entry.get("path", "")
                path, image_data, mime_type = _load_image(image_path)
                sha256 = hashlib.sha256(image_data).hexdigest()
                data_url = f"data:{mime_type};base64,{base64.b64encode(image_data).decode('ascii')}"
                alt = entry.get("alt", "").strip()
                if alt:
                    content.append({"type": "text", "text": f"图片说明：{alt}"})
                    text_count += 1
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": data_url,
                            "detail": "high",
                            "source_path": str(path),
                            "filename": path.name,
                            "mime_type": mime_type,
                            "file_size": len(image_data),
                            "sha256": sha256,
                            "source_recorded_at": _file_source_recorded_at(path),
                        },
                    }
                )
                image_count += 1
                continue

            raise MemOSClientError(f"不支持的图文内容类型：{entry_type!r}")

        if image_count == 0:
            raise MemOSClientError("图文联合解析至少需要一张图片。")
        if text_count == 0 and image_count < 2:
            raise MemOSClientError("只有一张图片时请使用 :image；图文模式还需要文字。")

        ingest_batch_id = f"mixed-{uuid.uuid4().hex}"
        source_document: Path | None = None
        if source_document_path:
            try:
                source_document = Path(source_document_path).expanduser().resolve(strict=True)
            except OSError as exc:
                raise MemOSClientError(f"找不到来源 Markdown：{source_document_path}") from exc
            source_recorded_at = _file_source_recorded_at(source_document)
        payload: dict[str, Any] = {
            "user_id": user_id,
            "writable_cube_ids": [cube_id],
            "messages": [
                {
                    "role": "user",
                    "message_id": ingest_batch_id,
                    "content": content,
                }
            ],
            "async_mode": "sync",
            "mode": "fine",
            "custom_tags": ["图文导入", "Markdown导入"] if source_document else ["图文导入"],
            "info": {
                "source_type": "mixed_markdown" if source_document else "mixed_media",
                "ingest_batch_id": ingest_batch_id,
                "source_recorded_at": source_recorded_at,
            },
        }
        if source_document:
            payload["info"]["source_document_path"] = str(source_document)
            payload["info"]["source_document_filename"] = source_document.name
        if session_id:
            payload["session_id"] = session_id

        return self._request("POST", "/product/add", payload, timeout=max(self.timeout, 600))

    def remember_video(
        self,
        *,
        user_id: str,
        cube_id: str,
        video_source: str,
        instruction: str | None = None,
        session_id: str | None = None,
    ) -> Any:
        """Import one video through MemOS's built-in VideoParser."""
        source = _strip_matching_quotes(video_source)
        if not source:
            raise MemOSClientError("请提供视频路径或 HTTP(S) URL。")

        video_info: dict[str, Any]
        if _is_http_url(source):
            video_info = {"url": source}
            source_recorded_at = None
            parsed_path = Path(urllib.parse.urlparse(source).path)
            source_metadata: dict[str, Any] = {
                "source_type": "remote_video",
                "source_url": source,
                "filename": parsed_path.name or None,
            }
        else:
            path, mime_type, file_size, sha256 = _load_video_reference(source)
            source_recorded_at = _file_source_recorded_at(path)
            upload = self.video_uploader(path, mime_type, sha256)
            video_info = {
                "url": upload.download_url,
                "media_uri": upload.media_uri,
                "oss_object_key": upload.object_key,
                "source_path": str(path),
                "sha256": sha256,
            }
            source_metadata = {
                "source_type": "oss_video",
                "source_path": str(path),
                "filename": path.name,
                "mime_type": mime_type,
                "file_size": file_size,
                "sha256": sha256,
                "media_uri": upload.media_uri,
                "oss_object_key": upload.object_key,
            }

        context = DEFAULT_VIDEO_CONTEXT
        if instruction:
            context += f"\n用户额外关注：{instruction.strip()}"
        video_info["instruction"] = context
        if source_recorded_at is not None:
            video_info["source_recorded_at"] = source_recorded_at
            source_metadata["source_recorded_at"] = source_recorded_at
        source_metadata["ingest_batch_id"] = f"video-{uuid.uuid4().hex}"

        payload: dict[str, Any] = {
            "user_id": user_id,
            "writable_cube_ids": [cube_id],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "video", "video": video_info},
                    ],
                }
            ],
            "async_mode": "sync",
            "mode": "fine",
            "custom_tags": ["视频导入"],
            "info": source_metadata,
        }
        if session_id:
            payload["session_id"] = session_id
        # Video understanding can be substantially slower than image parsing.
        return self._request("POST", "/product/add", payload, timeout=max(self.timeout, 1800))

    def search(self, *, user_id: str, cube_id: str, query: str) -> Any:
        return self._request(
            "POST",
            "/product/search",
            {
                "user_id": user_id,
                "query": query,
                "readable_cube_ids": [cube_id],
                "mode": "fast",
                "top_k": 5,
            },
        )


def _topic_enabled() -> bool:
    configured = os.getenv(
        "MEMOS_TOPIC_ENABLED",
        os.getenv("MEMOS_TOPIC_OUTBOX_ENABLED", "1"),
    )
    return configured.strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def import_memory_file(
    client: MemOSClient,
    *,
    user_id: str,
    cube_id: str,
    session_id: str,
    file_path: str,
    instruction: str | None = None,
) -> tuple[ImportedFile, Any]:
    """Route one file to the existing text, image, mixed-media, or video path."""
    imported = classify_import_file(file_path)
    if imported.kind == "text":
        result = client.remember_text_file(
            user_id=user_id,
            cube_id=cube_id,
            text=imported.text,
            source_path=str(imported.path),
            session_id=session_id,
        )
    elif imported.kind == "image":
        result = client.remember_image(
            user_id=user_id,
            cube_id=cube_id,
            image_path=str(imported.path),
            instruction=instruction or imported.instruction,
            session_id=session_id,
        )
    elif imported.kind == "mixed":
        result = client.remember_mixed(
            user_id=user_id,
            cube_id=cube_id,
            entries=imported.entries or [],
            session_id=session_id,
            source_document_path=str(imported.path),
        )
    elif imported.kind == "video":
        result = client.remember_video(
            user_id=user_id,
            cube_id=cube_id,
            video_source=str(imported.path),
            instruction=instruction,
            session_id=session_id,
        )
    else:
        raise MemOSClientError(f"无法处理文件类型：{imported.kind}")
    return imported, result


HELP = """命令：
  直接输入问题          与 MemOS 对话；默认不把问答自动写入记忆库
  :import "文件路径"   自动识别 TXT、Markdown、图片或视频并选择正确解析器
                       也可以把本地文件直接拖到终端后按回车
                       写入成功后自动计算并打印最新 Topic
  :remember <内容>     写入文字；图片或视频路径会自动使用对应解析器
  :mixed              进入图文混合输入；保持文字和图片的先后顺序联合解析
  :mixed-file "记录.md"
                       从 Markdown 一次导入有顺序的文字和本地图片
  :image <图片路径>    导入一张本地图片（路径可直接拖入终端）
  :image <路径> | <关注点>
                       可选：例如“完整提取聊天文字和时间”
  :video <路径或URL>   导入视频；本地视频会先上传到私有 OSS
  :video <路径或URL> | <关注点>
                       可选：例如“提取依次打开的页面和每一步操作”
  :remember-video ...  与 :video 相同
  :search <问题>       搜索当前记忆库
  :help                显示帮助
  :quit                退出

图片和视频默认使用 MemOS 内置中文分析规则，不需要自己写英文 instruction。
视频模型凭证只在 MemOS 服务端读取，需要配置 VIDEO_PARSER_MODEL、
VIDEO_API_KEY 和 VIDEO_API_BASE；本终端不会读取或传输模型 API Key。
本地视频上传需要在项目 .env 配置 OSS_REGION、OSS_BUCKET、
OSS_ACCESS_KEY_ID 和 OSS_ACCESS_KEY_SECRET；OSS_ENDPOINT 可选。
"""


def print_mixed_markdown_preview(
    markdown_path: Path,
    entries: list[dict[str, str]],
    preview_limit: int = 20,
) -> None:
    """Print a bounded preview so large Markdown files do not flood the terminal."""
    text_count = sum(entry.get("type") == "text" for entry in entries)
    image_count = sum(entry.get("type") == "image_path" for entry in entries)
    print(f"Markdown：{markdown_path}")
    print(f"准备导入：{text_count} 段文字，{image_count} 张图片，共 {len(entries)} 项。")

    visible_indexes = list(range(len(entries)))
    if len(entries) > preview_limit:
        head_count = max(1, preview_limit - 5)
        visible_indexes = [*range(head_count), *range(len(entries) - 5, len(entries))]

    previous_index = -1
    for index in visible_indexes:
        if index > previous_index + 1:
            print(f"... 中间省略 {index - previous_index - 1} 项 ...")
        entry = entries[index]
        if entry.get("type") == "image_path":
            detail = entry.get("path", "")
            if entry.get("alt"):
                detail += f"（说明：{entry['alt']}）"
            label = "图片"
        else:
            detail = " ".join(entry.get("text", "").split())
            if len(detail) > 100:
                detail = f"{detail[:100]}…"
            label = "文字"
        print(f"{index + 1}. {label}：{detail}")
        previous_index = index


def collect_mixed_entries() -> list[dict[str, str]] | None:
    """在终端中收集有顺序的文字和图片片段。"""
    entries: list[dict[str, str]] = []
    print("已进入图文混合输入。普通内容按文字保存。")
    print("输入 :image <路径> 添加图片；:done 提交；:undo 撤销；:cancel 取消。")

    while True:
        try:
            value = input("图文> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消图文输入。")
            return None

        if not value:
            continue
        if value == ":cancel":
            print("已取消图文输入。")
            return None
        if value == ":done":
            if not entries:
                print("还没有添加内容。")
                continue
            return entries
        if value == ":undo":
            if entries:
                removed = entries.pop()
                print(f"已撤销：{removed['type']}")
            else:
                print("没有可以撤销的内容。")
            continue
        if value == ":show":
            if not entries:
                print("还没有添加内容。")
                continue
            for index, entry in enumerate(entries, start=1):
                detail = entry.get("text") or entry.get("path") or ""
                print(f"{index}. {entry['type']}: {detail}")
            continue
        if value == ":help":
            print("普通内容=文字；:image <路径>=图片；:show=查看；:undo=撤销；:done=提交。")
            continue
        if value.startswith(":image "):
            image_path = _strip_matching_quotes(value[7:].strip())
            try:
                path, _, _ = _load_image(image_path)
            except MemOSClientError as exc:
                print(f"图片无效：{exc}")
                continue
            entries.append({"type": "image_path", "path": str(path)})
            print(f"已添加图片：{path.name}")
            continue

        entries.append({"type": "text", "text": value})
        print("已添加文字。")


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _looks_like_supported_import_file(value: str) -> bool:
    raw = _strip_matching_quotes(value)
    if not raw:
        return False
    path = Path(raw).expanduser()
    return path.is_file() and path.suffix.lower() in (
        SUPPORTED_IMAGE_EXTENSIONS
        | SUPPORTED_TEXT_EXTENSIONS
        | SUPPORTED_MARKDOWN_EXTENSIONS
        | SUPPORTED_VIDEO_EXTENSIONS
    )


def _print_import_result(imported: ImportedFile, result: Any) -> None:
    labels = {
        "text": "纯文字",
        "image": "单张图片",
        "mixed": "Markdown 图文",
        "video": "视频",
    }
    label = labels.get(imported.kind, imported.kind)
    memories = result.get("data") if isinstance(result, dict) else None
    if isinstance(memories, list) and not memories:
        print(f"已自动识别为{label}，但未写入记忆：{imported.path.name}")
    else:
        print(f"已自动识别为{label}并写入记忆：{imported.path.name}")
    if isinstance(result, dict) and result.get("message"):
        print(f"MemOS: {result['message']}")
    if isinstance(memories, list):
        if not memories:
            print("本次没有生成可保存的记忆，也没有进入 Topic 计算。")
        elif _topic_enabled():
            print(f"本次共生成 {len(memories)} 条记忆；已自动进行 Topic 计算。")
        else:
            print(f"本次共生成 {len(memories)} 条记忆。")


def _print_topic_update(client: MemOSClient) -> None:
    if client.last_topic_error:
        print(
            f"记忆已经保存，但 Topic 本次自动处理失败：{client.last_topic_error}",
            file=sys.stderr,
        )
        return
    update = client.last_topic_update
    if not isinstance(update, dict):
        return

    processed = int(update.get("processed_memories") or 0)
    print(f"Topic：已自动处理了 {processed} 条新记忆。")
    topics = update.get("topics")
    if not isinstance(topics, list) or not topics:
        print("已完成标签提取和候选计算；当前还没有达到生成阈值的 Topic。")
        return

    print(f"当前滚动 Topic（最多 {update.get('rolling_limit', 15)} 个）：")
    for index, topic in enumerate(topics, start=1):
        if not isinstance(topic, dict):
            continue
        print(f"{index}. {topic.get('topic_text', '')}")
        if topic.get("reason_summary"):
            print(f"   理由：{topic['reason_summary']}")
        evidence = topic.get("reason_evidence")
        if not isinstance(evidence, list):
            continue
        for item in evidence:
            if not isinstance(item, dict):
                continue
            memory_id = str(item.get("memory_id") or "")
            fact = str(item.get("fact") or "")
            contribution = str(item.get("contribution") or "")
            print(f"   证据 {memory_id}: {fact}（{contribution}）")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MemOS 本地终端聊天客户端")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--user", default="default", help="MemOS 用户 ID")
    parser.add_argument("--cube", default="default_cube", help="读写的记忆库 ID")
    parser.add_argument("--session", default=f"terminal-{uuid.uuid4().hex[:8]}", help="会话 ID")
    parser.add_argument(
        "--model",
        default=None,
        help="CHAT_MODEL_LIST.support_models 中声明的模型名",
    )
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument(
        "--startup-timeout",
        type=int,
        default=180,
        help="等待 MemOS 后端完成启动的秒数",
    )
    return parser.parse_args()


def wait_until_healthy(
    client: MemOSClient,
    startup_timeout: int = 180,
    poll_interval: float = 2.0,
) -> Any:
    """Wait for the API to finish starting instead of failing on its first disconnect."""
    deadline = time.monotonic() + max(0, startup_timeout)
    last_error: MemOSClientError | None = None

    while True:
        try:
            result = client.health(timeout=5)
            if not isinstance(result, dict) or result.get("status") not in {"healthy", "ok"}:
                raise MemOSClientError(f"健康检查返回异常：{result}")
            return result
        except MemOSClientError as exc:
            last_error = exc

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MemOSClientError(
                f"MemOS 后端在 {startup_timeout} 秒内没有准备完成；最后一次错误：{last_error}"
            ) from last_error
        time.sleep(min(poll_interval, remaining))


def main() -> int:
    _load_project_env()
    args = parse_args()
    client = MemOSClient(args.base_url, args.timeout)

    print(f"正在等待 MemOS 后端启动（最多 {args.startup_timeout} 秒）……")
    try:
        wait_until_healthy(client, args.startup_timeout)
    except MemOSClientError as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        print("请先运行：cd D:\\project-memo\\MemOS\\docker; docker compose up -d")
        return 1

    print("MemOS 终端客户端已连接")
    print(f"用户：{args.user}  记忆库：{args.cube}  会话：{args.session}")
    if args.model:
        print(f"模型：{args.model}")
    print(HELP)

    while True:
        try:
            text = input("你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            return 0

        if not text:
            continue
        if text.lower() in {":quit", ":exit", "quit", "exit"}:
            print("再见。")
            return 0
        if text == ":help":
            print(HELP)
            continue

        try:
            if text == ":import":
                print('请提供一个文件路径，例如：:import "D:\\memory-input\\今天的记录.md"')
            elif text.startswith(":import "):
                imported, result = import_memory_file(
                    client,
                    user_id=args.user,
                    cube_id=args.cube,
                    session_id=args.session,
                    file_path=text[len(":import ") :],
                )
                _print_import_result(imported, result)
                _print_topic_update(client)
            elif text == ":mixed-file":
                print('请提供 Markdown 路径，例如：:mixed-file "D:\\memory-input\\记录.md"')
            elif text.startswith(":mixed-file "):
                markdown_path, entries = parse_mixed_markdown(text[len(":mixed-file ") :])
                print_mixed_markdown_preview(markdown_path, entries)
                try:
                    confirmation = input("确认导入？[Y/n] ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    print("\n已取消导入。")
                    continue
                if confirmation not in {"", "y", "yes"}:
                    print("已取消导入。")
                    continue
                result = client.remember_mixed(
                    user_id=args.user,
                    cube_id=args.cube,
                    entries=entries,
                    session_id=args.session,
                    source_document_path=str(markdown_path),
                )
                print(f"已从 Markdown 联合解析图文并写入记忆：{markdown_path.name}")
                if isinstance(result, dict) and result.get("message"):
                    print(f"MemOS: {result['message']}")
                _print_topic_update(client)
            elif text == ":mixed":
                entries = collect_mixed_entries()
                if entries is None:
                    continue
                result = client.remember_mixed(
                    user_id=args.user,
                    cube_id=args.cube,
                    entries=entries,
                    session_id=args.session,
                )
                print("已按原始顺序联合解析图文，并写入记忆。")
                if isinstance(result, dict) and result.get("message"):
                    print(f"MemOS: {result['message']}")
                _print_topic_update(client)
            elif text.startswith((":remember-video ", ":video ")):
                prefix = ":remember-video " if text.startswith(":remember-video ") else ":video "
                video_source, instruction = split_image_input(text[len(prefix) :].strip())
                result = client.remember_video(
                    user_id=args.user,
                    cube_id=args.cube,
                    video_source=video_source,
                    instruction=instruction,
                    session_id=args.session,
                )
                display_name = (
                    Path(urllib.parse.urlparse(video_source).path).name
                    if _is_http_url(video_source)
                    else Path(video_source).name
                )
                print(f"已从视频写入记忆：{display_name or video_source}")
                if isinstance(result, dict) and result.get("message"):
                    print(f"MemOS: {result['message']}")
                _print_topic_update(client)
            elif text.startswith(":remember "):
                content = text[10:].strip()
                if _looks_like_video_source(content):
                    video_source, instruction = split_image_input(content)
                    result = client.remember_video(
                        user_id=args.user,
                        cube_id=args.cube,
                        video_source=video_source,
                        instruction=instruction,
                        session_id=args.session,
                    )
                    print(f"已从视频写入记忆：{Path(video_source).name}")
                elif _looks_like_image_path(content):
                    image_path, instruction = split_image_input(content)
                    result = client.remember_image(
                        user_id=args.user,
                        cube_id=args.cube,
                        image_path=image_path,
                        instruction=instruction,
                        session_id=args.session,
                    )
                    print(f"已从图片写入记忆：{Path(image_path).name}")
                else:
                    result = client.remember(user_id=args.user, cube_id=args.cube, text=content)
                    print("已写入文字记忆。")
                if isinstance(result, dict) and result.get("message"):
                    print(f"MemOS: {result['message']}")
                _print_topic_update(client)
            elif text.startswith(":image "):
                image_path, instruction = split_image_input(text[7:].strip())
                result = client.remember_image(
                    user_id=args.user,
                    cube_id=args.cube,
                    image_path=image_path,
                    instruction=instruction,
                    session_id=args.session,
                )
                print(f"已从图片写入记忆：{Path(image_path).name}")
                if isinstance(result, dict) and result.get("message"):
                    print(f"MemOS: {result['message']}")
                _print_topic_update(client)
            elif text.startswith(":search "):
                result = client.search(user_id=args.user, cube_id=args.cube, query=text[8:].strip())
                print_json(result)
            elif text.startswith(":"):
                print("未知命令。输入 :help 查看帮助。")
            elif _looks_like_supported_import_file(text):
                imported, result = import_memory_file(
                    client,
                    user_id=args.user,
                    cube_id=args.cube,
                    session_id=args.session,
                    file_path=text,
                )
                _print_import_result(imported, result)
                _print_topic_update(client)
            else:
                answer = client.chat(
                    user_id=args.user,
                    cube_id=args.cube,
                    session_id=args.session,
                    query=text,
                    model=args.model,
                )
                print(f"MemOS> {answer}")
        except MemOSClientError as exc:
            print(f"请求失败：{exc}", file=sys.stderr)
            if "you_bailian_api_key" in str(exc):
                print("请先把 .env 中 CHAT_MODEL_LIST 的占位 API Key 换成真实密钥。")
            if any(
                name in str(exc)
                for name in ("VIDEO_PARSER_MODEL", "VIDEO_API_KEY", "VIDEO_API_BASE")
            ):
                print(
                    "请检查服务端 .env 中独立的视频配置：VIDEO_PARSER_MODEL、"
                    "VIDEO_API_KEY、VIDEO_API_BASE。",
                    file=sys.stderr,
                )
            if "OSS_" in str(exc) or "上传 OSS" in str(exc):
                print(
                    "请检查项目 .env 中的 OSS_REGION、OSS_BUCKET、"
                    "OSS_ACCESS_KEY_ID、OSS_ACCESS_KEY_SECRET 和可选 OSS_ENDPOINT。",
                    file=sys.stderr,
                )


if __name__ == "__main__":
    raise SystemExit(main())
