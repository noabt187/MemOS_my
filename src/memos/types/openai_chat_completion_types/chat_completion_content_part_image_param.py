from __future__ import annotations

from typing import Literal

from typing_extensions import Required, TypedDict


__all__ = ["ChatCompletionContentPartImageParam", "ImageURL"]


class ImageURL(TypedDict, total=False):
    url: Required[str]
    """Either a URL of the image or the base64 encoded image data."""

    detail: Literal["auto", "low", "high"]
    """Specifies the detail level of the image.

    Learn more in the
    [Vision guide](https://platform.openai.com/docs/guides/vision#low-or-high-fidelity-image-understanding).
    """

    image_id: str
    """Optional custom image id for tracking image sources."""

    instruction: str
    """Optional user guidance for understanding this image."""

    source_path: str
    """Original local path used as a durable source reference."""

    filename: str
    """Original image filename."""

    mime_type: str
    """Detected image MIME type."""

    file_size: int
    """Original image size in bytes."""

    sha256: str
    """SHA-256 digest used to identify the original image."""

    source_recorded_at: str
    """Time at which the image or screenshot was recorded."""


class ChatCompletionContentPartImageParam(TypedDict, total=False):
    image_url: Required[ImageURL]

    type: Required[Literal["image_url"]]
    """The type of the content part."""
