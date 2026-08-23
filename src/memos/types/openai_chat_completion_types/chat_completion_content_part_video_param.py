from __future__ import annotations

from typing import Literal

from typing_extensions import Required, TypedDict


__all__ = [
    "ChatCompletionContentPartVideoParam",
    "ChatCompletionContentPartVideoURLParam",
    "VideoURL",
]


class VideoURL(TypedDict, total=False):
    url: str
    """URL, local media reference, or short video data URL."""

    sample_fps: float
    """Sampling rate represented by the supplied video or frame sequence."""

    video_id: str
    sha256: str
    media_uri: str
    source_path: str
    duration_ms: int
    raw_fps: float
    frame_count: int


class ChatCompletionContentPartVideoParam(TypedDict, total=False):
    video: Required[VideoURL]
    type: Required[Literal["video"]]


class ChatCompletionContentPartVideoURLParam(TypedDict, total=False):
    video_url: Required[VideoURL]
    type: Required[Literal["video_url"]]
