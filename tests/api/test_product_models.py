"""Unit tests for API request-model OpenAPI schemas.

These tests lock the OpenAPI schema behaviour of request models so the
interactive docs (``/docs``) stay consistent with the documented contract.

Regression guard for issue #1505: the ``/product/add`` example must render
``messages`` as a structured message list instead of a bare ``"string"``.
Because ``messages`` is typed as ``str | MessageList | RawMessageList``, Swagger
UI would otherwise pick the leading ``str`` branch of the ``anyOf`` and show
``"messages": "string"``, which misleads users into sending plain text.
"""

from memos.api.product_models import APIADDRequest


def test_add_request_exposes_model_level_example():
    """APIADDRequest must ship a model-level example for the interactive docs."""
    schema = APIADDRequest.model_json_schema()

    assert "example" in schema, "APIADDRequest should define a model-level example"


def test_add_request_example_messages_is_structured_list():
    """The example's ``messages`` must be a non-empty list of role/content items."""
    example = APIADDRequest.model_json_schema()["example"]

    messages = example.get("messages")
    assert isinstance(messages, list), "messages example must be a list, not a bare string"
    assert messages, "messages example should not be empty"

    first = messages[0]
    assert first.get("role"), "each example message needs a role"
    assert first.get("content"), "each example message needs content"


def test_add_request_example_covers_core_fields():
    """The example should be a copy-paste-ready payload for the core add flow."""
    example = APIADDRequest.model_json_schema()["example"]

    assert "user_id" in example
    assert "writable_cube_ids" in example


def test_add_request_preserves_ordered_image_source_metadata():
    """Mixed-image source references must survive request validation."""
    request = APIADDRequest.model_validate(
        {
            "user_id": "user-1",
            "writable_cube_ids": ["cube-1"],
            "messages": [
                {
                    "role": "user",
                    "message_id": "mixed-1",
                    "content": [
                        {"type": "text", "text": "before"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64,AAAA",
                                "detail": "high",
                                "instruction": "read visible evidence",
                                "source_path": "D:/pictures/a.png",
                                "filename": "a.png",
                                "mime_type": "image/png",
                                "file_size": 4,
                                "sha256": "abc123",
                                "source_recorded_at": "2026-08-18T12:00:00+08:00",
                            },
                        },
                        {"type": "text", "text": "after"},
                    ],
                }
            ],
            "async_mode": "sync",
            "mode": "fine",
        }
    )

    content = request.model_dump()["messages"][0]["content"]
    assert [part["type"] for part in content] == ["text", "image_url", "text"]
    image_info = content[1]["image_url"]
    assert image_info["source_path"] == "D:/pictures/a.png"
    assert image_info["filename"] == "a.png"
    assert image_info["mime_type"] == "image/png"
    assert image_info["file_size"] == 4
    assert image_info["sha256"] == "abc123"
    assert image_info["source_recorded_at"] == "2026-08-18T12:00:00+08:00"
    assert image_info["instruction"] == "read visible evidence"
