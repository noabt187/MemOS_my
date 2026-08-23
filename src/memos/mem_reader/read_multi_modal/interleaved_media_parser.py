"""Joint parser for ordered text-and-image inputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from memos.exceptions import ParserError
from memos.log import get_logger
from memos.mem_reader.utils import parse_json_result, validate_memory_extraction_result
from memos.memories.textual.item import SourceMessage, TextualMemoryItem
from memos.templates.mem_reader_prompts import (
    INTERLEAVED_MEDIA_ANALYSIS_PROMPT_EN,
    INTERLEAVED_MEDIA_ANALYSIS_PROMPT_ZH,
)

from .base import _derive_key
from .image_parser import ImageParser
from .utils import detect_lang


if TYPE_CHECKING:
    from memos.embedders.base import BaseEmbedder
    from memos.llms.base import BaseLLM


logger = get_logger(__name__)


@dataclass(frozen=True)
class OrderedMediaPart:
    """One normalized input part in its original sequence position."""

    part_id: str
    sequence_index: int
    kind: Literal["text", "image"]
    role: str | None
    chat_time: str | None
    message_id: str | None
    text: str | None = None
    image_message: dict[str, Any] | None = None


class InterleavedMediaParser(ImageParser):
    """Understand an ordered text-and-image sequence in one vision-model call."""

    _IMAGE_TYPES: ClassVar[frozenset[str]] = frozenset({"image_url"})
    _UNSUPPORTED_MEDIA_TYPES: ClassVar[frozenset[str]] = frozenset(
        {"audio", "file", "video", "video_url"}
    )

    def __init__(self, embedder: BaseEmbedder, llm: BaseLLM | None = None) -> None:
        super().__init__(embedder, llm)

    @classmethod
    def can_parse(cls, messages: Any) -> bool:
        """Return whether input should use joint ordered image understanding."""
        if not isinstance(messages, list):
            return False

        text_count = 0
        image_count = 0
        for message in messages:
            if not isinstance(message, dict):
                continue
            if message.get("type") in cls._UNSUPPORTED_MEDIA_TYPES:
                return False

            content = message.get("content")
            if isinstance(content, str):
                text_count += bool(content.strip())
                continue
            if not isinstance(content, list):
                content = [message] if message.get("type") else []

            for part in content:
                if not isinstance(part, dict):
                    text_count += bool(str(part).strip())
                    continue
                part_type = part.get("type", "")
                if part_type in cls._UNSUPPORTED_MEDIA_TYPES:
                    return False
                if part_type == "text":
                    text_count += bool(str(part.get("text", "")).strip())
                elif part_type in cls._IMAGE_TYPES:
                    image_count += 1

        return image_count >= 1 and (text_count >= 1 or image_count >= 2)

    @staticmethod
    def _normalise_confidence(value: Any) -> float:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return 0.99
        if 1 < confidence <= 100:
            confidence /= 100
        return min(1.0, max(0.0, confidence))

    @classmethod
    def _normalise_memory_type(cls, value: Any) -> str:
        memory_type = (
            str(value or "LongTermMemory")
            .replace("长期记忆", "LongTermMemory")
            .replace("用户记忆", "UserMemory")
        )
        if memory_type not in {"LongTermMemory", "UserMemory"}:
            return "LongTermMemory"
        return memory_type

    @staticmethod
    def _merge_tags(model_tags: Any, custom_tags: list[str] | None) -> list[str]:
        tags = list(model_tags) if isinstance(model_tags, list) else []
        for tag in custom_tags or []:
            if tag not in tags:
                tags.append(tag)
        return tags

    @classmethod
    def _normalise_parts(cls, messages: list[Any]) -> list[OrderedMediaPart]:
        raw_parts: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, str):
                raw_parts.append((message, {"type": "text", "text": content}))
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        raw_parts.append((message, part))
                    elif str(part).strip():
                        raw_parts.append((message, {"type": "text", "text": str(part)}))
            elif message.get("type"):
                raw_parts.append((message, message))

        parts: list[OrderedMediaPart] = []
        for message, raw_part in raw_parts:
            part_type = raw_part.get("type", "")
            if part_type == "text":
                text = str(raw_part.get("text", "")).strip()
                if not text:
                    continue
                kind: Literal["text", "image"] = "text"
                image_message = None
            elif part_type in cls._IMAGE_TYPES:
                text = None
                kind = "image"
                image_message = raw_part
            else:
                continue

            sequence_index = len(parts)
            parts.append(
                OrderedMediaPart(
                    part_id=f"part_{sequence_index + 1:03d}",
                    sequence_index=sequence_index,
                    kind=kind,
                    role=message.get("role"),
                    chat_time=message.get("chat_time"),
                    message_id=message.get("message_id"),
                    text=text,
                    image_message=image_message,
                )
            )
        return parts

    def _source_for_part(
        self,
        part: OrderedMediaPart,
        info: dict[str, Any],
    ) -> SourceMessage:
        if part.kind == "text":
            return SourceMessage(
                type="chat",
                role=part.role if part.role in {"user", "assistant", "system", "tool"} else None,
                content=part.text,
                chat_time=part.chat_time,
                message_id=part.message_id,
                part_id=part.part_id,
                sequence_index=part.sequence_index,
            )

        source = self.create_source(
            part.image_message or {},
            info,
            retain_inline_data=False,
        )
        source.role = part.role if part.role in {"user", "assistant", "system", "tool"} else None
        source.chat_time = part.chat_time
        source.message_id = part.message_id
        source.part_id = part.part_id
        source.sequence_index = part.sequence_index
        return source

    @staticmethod
    def _provider_image_part(part: OrderedMediaPart) -> dict[str, Any]:
        image_url = (part.image_message or {}).get("image_url", {})
        if isinstance(image_url, str):
            url = image_url
            detail = "auto"
        else:
            url = image_url.get("url", "")
            detail = image_url.get("detail", "auto")
        return {
            "type": "image_url",
            "image_url": {"url": url, "detail": detail},
        }

    @staticmethod
    def _choose_prompt(parts: list[OrderedMediaPart]) -> str:
        text = "\n".join(part.text or "" for part in parts if part.kind == "text")
        return (
            INTERLEAVED_MEDIA_ANALYSIS_PROMPT_ZH
            if detect_lang(text or "图像") == "zh"
            else INTERLEAVED_MEDIA_ANALYSIS_PROMPT_EN
        )

    def _build_provider_messages(self, parts: list[OrderedMediaPart]) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = [{"type": "text", "text": self._choose_prompt(parts)}]
        for part in parts:
            role = part.role or "user"
            if part.kind == "text":
                content.append(
                    {
                        "type": "text",
                        "text": f"[{part.part_id} | {role} | 文字]\n{part.text}",
                    }
                )
                continue

            filename = ""
            image_url = (part.image_message or {}).get("image_url", {})
            if isinstance(image_url, dict):
                filename = str(image_url.get("filename") or "")
            content.append(
                {
                    "type": "text",
                    "text": f"[{part.part_id} | {role} | 图片{f'：{filename}' if filename else ''}]",
                }
            )
            content.append(self._provider_image_part(part))
        return [{"role": "user", "content": content}]

    def parse_fine(
        self,
        messages: list[Any],
        info: dict[str, Any],
        **kwargs: Any,
    ) -> list[TextualMemoryItem]:
        """Parse a complete ordered sequence and return native MemOS memory items."""
        if not self.llm:
            logger.warning("[InterleavedMediaParser] Vision LLM is not configured")
            return []

        parts = self._normalise_parts(messages)
        if not parts:
            return []
        source_by_id = {part.part_id: self._source_for_part(part, info) for part in parts}

        try:
            response_text = self.llm.generate(self._build_provider_messages(parts))
            response_json = validate_memory_extraction_result(
                parse_json_result(response_text or ""),
                context="interleaved media extraction",
            )
        except ParserError:
            raise
        except Exception as exc:  # noqa: BLE001 - provider SDKs expose unrelated errors
            logger.error("[InterleavedMediaParser] Joint model call failed: %s", exc)
            return []

        memory_list = response_json["memory_list"]

        all_sources = list(source_by_id.values())
        custom_tags = kwargs.get("custom_tags")
        background = str(response_json.get("summary", ""))
        memory_items: list[TextualMemoryItem] = []
        for memory_data in memory_list:
            if not isinstance(memory_data, dict):
                continue
            value = str(memory_data.get("value", "")).strip()
            if not value:
                continue

            evidence_ids = memory_data.get("evidence_part_ids", [])
            evidence_sources = (
                [source_by_id[part_id] for part_id in evidence_ids if part_id in source_by_id]
                if isinstance(evidence_ids, list)
                else []
            )
            if not evidence_sources:
                evidence_sources = all_sources

            item = self._create_memory_item(
                value=value,
                info=info,
                memory_type=self._normalise_memory_type(memory_data.get("memory_type")),
                tags=self._merge_tags(memory_data.get("tags"), custom_tags),
                key=str(memory_data.get("key") or _derive_key(value)),
                sources=evidence_sources,
                background=background,
                **kwargs,
            )
            item.metadata.confidence = self._normalise_confidence(memory_data.get("confidence"))
            memory_items.append(item)

        return memory_items
