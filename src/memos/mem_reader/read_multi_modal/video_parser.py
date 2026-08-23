"""Parser for video and video_url content parts."""

import re

from datetime import datetime
from typing import Any

from memos.embedders.base import BaseEmbedder
from memos.exceptions import ConfigurationError, ParserError
from memos.llms.base import BaseLLM
from memos.log import get_logger
from memos.mem_reader.utils import validate_memory_extraction_result
from memos.memories.textual.item import (
    SourceMessage,
    TextualMemoryItem,
    TreeNodeTextualMemoryMetadata,
)
from memos.templates.mem_reader_prompts import VIDEO_ANALYSIS_PROMPT_EN, VIDEO_ANALYSIS_PROMPT_ZH
from memos.types.openai_chat_completion_types import (
    ChatCompletionContentPartVideoParam,
    ChatCompletionContentPartVideoURLParam,
)

from .base import _derive_key
from .image_parser import ImageParser
from .utils import detect_lang


logger = get_logger(__name__)


_TIMESTAMP_PREFIX_RE = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}[^\]]*\]")


def _reference_timestamp_prefix(source_recorded_at: str) -> str:
    """Build an honest fallback prefix when the model cannot read screen time."""
    if not source_recorded_at:
        return ""
    try:
        parsed = datetime.fromisoformat(source_recorded_at.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return f"[{parsed.strftime('%Y-%m-%d %H:%M')}（参考时间）]"


def _ensure_timestamp_prefix(value: str, source_recorded_at: str) -> str:
    if _TIMESTAMP_PREFIX_RE.match(value):
        return value
    prefix = _reference_timestamp_prefix(source_recorded_at)
    return f"{prefix} {value}" if prefix else value


VideoMessage = ChatCompletionContentPartVideoParam | ChatCompletionContentPartVideoURLParam


class VideoParser(ImageParser):
    """Parse a complete video with a dedicated video-capable vision LLM."""

    def __init__(self, embedder: BaseEmbedder, llm: BaseLLM | None = None):
        super().__init__(embedder, llm)

    @staticmethod
    def _get_video_payload(message: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
        input_type = message.get("type", "video")
        field_name = "video_url" if input_type == "video_url" else "video"
        payload = message.get(field_name, {})

        if isinstance(payload, dict):
            video_info = dict(payload)
            url = str(
                video_info.get("url")
                or video_info.get("media_uri")
                or video_info.get("source_path")
                or ""
            )
        else:
            url = str(payload)
            video_info = {"url": url}

        if url:
            video_info.setdefault("url", url)
        return url, video_info, input_type

    @staticmethod
    def _display_reference(url: str) -> str:
        if url.startswith("data:video"):
            return "[inline video data omitted]"
        return url

    def create_source(self, message: VideoMessage, info: dict[str, Any]) -> SourceMessage:
        """Create a traceable video source without creating a new memory type."""
        if not isinstance(message, dict):
            return SourceMessage(type="video", content=str(message))

        url, video_info, input_type = self._get_video_payload(message)
        return SourceMessage(
            type="video",
            content=self._display_reference(url),
            url=url,
            video_info=video_info,
            input_type=input_type,
        )

    def rebuild_from_source(self, source: SourceMessage) -> VideoMessage:
        """Rebuild the original video content part for asynchronous fine parsing."""
        input_type = getattr(source, "input_type", "video")
        field_name = "video_url" if input_type == "video_url" else "video"
        video_info = dict(getattr(source, "video_info", None) or {})
        url = (
            getattr(source, "url", "")
            or video_info.get("media_uri", "")
            or video_info.get("source_path", "")
        )
        if url:
            video_info["url"] = url
        return {"type": input_type, field_name: video_info}

    def parse_fast(
        self,
        message: VideoMessage,
        info: dict[str, Any],
        **kwargs,
    ) -> list[TextualMemoryItem]:
        """Preserve a compact video source for the existing fine pipeline."""
        if not isinstance(message, dict):
            logger.warning("[VideoParser] Expected dict, got %s", type(message))
            return []

        source = self.create_source(message, info)
        url = getattr(source, "url", "")
        if not url:
            logger.warning("[VideoParser] No video URL or media reference found")
            return []

        info_ = (info or {}).copy()
        user_id = info_.pop("user_id", "")
        session_id = info_.pop("session_id", "")
        content = f"[video]: {self._display_reference(url)}"
        need_emb = kwargs.get("need_emb", True)

        return [
            TextualMemoryItem(
                memory=content,
                metadata=TreeNodeTextualMemoryMetadata(
                    user_id=user_id,
                    session_id=session_id,
                    memory_type="UserMemory",
                    status="activated",
                    tags=["mode:fast", "multimodal:video"],
                    key=_derive_key(content),
                    embedding=self.embedder.embed([content])[0] if need_emb else None,
                    usage=[],
                    sources=[source],
                    background="",
                    confidence=0.99,
                    type="fact",
                    info=info_,
                ),
            )
        ]

    def parse_fine(
        self,
        message: VideoMessage,
        info: dict[str, Any],
        **kwargs,
    ) -> list[TextualMemoryItem]:
        """Send the complete video to the dedicated video vision LLM once."""
        if not self.llm:
            raise ConfigurationError(
                "Video input requires mem_reader.config.video_parser_llm. "
                "Configure video_parser_llm or set VIDEO_PARSER_MODEL, "
                "VIDEO_API_KEY, and VIDEO_API_BASE."
            )
        if not isinstance(message, dict):
            logger.warning("[VideoParser] Expected dict, got %s", type(message))
            return []

        url, video_info, _ = self._get_video_payload(message)
        if not url:
            logger.warning("[VideoParser] No video URL or media reference found")
            return []

        info = info or {}
        source = self.create_source(message, info)
        context_items = kwargs.get("context_items")
        instruction = str(video_info.get("instruction", "")).strip()
        source_recorded_at = str(
            video_info.get("source_recorded_at") or info.get("source_recorded_at") or ""
        ).strip()
        lang = detect_lang(instruction) if instruction else kwargs.get("lang")
        if context_items:
            for item in context_items:
                if (
                    not instruction
                    and hasattr(item, "memory")
                    and item.memory
                    and not item.memory.startswith("[video]:")
                ):
                    lang = detect_lang(item.memory)
                    source.lang = lang
                    break
        if not lang:
            lang = "en"
        if not getattr(source, "lang", None):
            source.lang = lang

        prompt = VIDEO_ANALYSIS_PROMPT_ZH if lang == "zh" else VIDEO_ANALYSIS_PROMPT_EN
        context_text = ""
        if context_items:
            context_text = "\n".join(
                item.memory
                for item in context_items
                if hasattr(item, "memory")
                and item.memory
                and not item.memory.startswith("[video]:")
            ).strip()
        if instruction:
            context_text = "\n".join(part for part in (context_text, instruction) if part)
        prompt = prompt.replace("{context}", context_text)
        prompt = prompt.replace(
            "{source_recorded_at}", source_recorded_at or "not provided / 未提供"
        )

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "video_url", "video_url": {"url": url}},
                ],
            }
        ]

        try:
            response_text = self.llm.generate(messages)
            if not response_text:
                logger.warning("[VideoParser] Empty response from video parser LLM")
                return []

            response_json = validate_memory_extraction_result(
                self._parse_json_result(response_text),
                context="video extraction",
            )
            if not response_json:
                logger.warning("[VideoParser] Failed to parse video parser LLM response")
                return []

            memory_list = response_json["memory_list"]
            if not memory_list:
                logger.warning("[VideoParser] No memory items extracted from video")
                return []

            memory_items = []
            for mem_data in memory_list:
                memory_type = (
                    mem_data.get("memory_type", "LongTermMemory")
                    .replace("长期记忆", "LongTermMemory")
                    .replace("用户记忆", "UserMemory")
                )
                if memory_type not in ["LongTermMemory", "UserMemory"]:
                    memory_type = "LongTermMemory"

                value = mem_data.get("value", "").strip()
                if not value:
                    continue
                value = _ensure_timestamp_prefix(value, source_recorded_at)
                tags = mem_data.get("tags", [])
                if not isinstance(tags, list):
                    tags = []
                normalized_tags = [str(tag) for tag in tags]
                lower_tags = [tag.lower() for tag in normalized_tags]
                if "video" not in lower_tags:
                    normalized_tags.append("video")
                if "visual" not in lower_tags:
                    normalized_tags.append("visual")

                key = mem_data.get("key", "")
                memory_items.append(
                    self._create_memory_item(
                        value=value,
                        info=info,
                        memory_type=memory_type,
                        tags=normalized_tags,
                        key=key or _derive_key(value),
                        sources=[source],
                        background=response_json.get("summary", ""),
                        **kwargs,
                    )
                )
            return memory_items
        except ParserError:
            raise
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            logger.error("[VideoParser] Error processing video in fine mode: %s", exc)
            return []
