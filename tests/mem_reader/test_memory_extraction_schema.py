from __future__ import annotations

import re

import pytest

from memos.exceptions import ParserError
from memos.mem_reader import utils
from memos.templates import mem_reader_prompts, mem_reader_strategy_prompts, memory_info_prompts


def test_all_memory_extraction_prompts_use_snake_case_json_keys():
    offenders = []
    modules = (mem_reader_prompts, mem_reader_strategy_prompts, memory_info_prompts)
    for module in modules:
        for name, value in vars(module).items():
            if not name.isupper() or not isinstance(value, str):
                continue
            spaced_keys = re.findall(r'"([^"\r\n]*\s+[^"\r\n]*)"\s*:', value)
            if spaced_keys:
                offenders.append(f"{module.__name__}.{name}: {spaced_keys}")

    assert offenders == []


def test_general_string_prompt_preserves_explicit_timeline_segments():
    prompt = mem_reader_prompts.GENERAL_STRUCT_STRING_READER_PROMPT_ZH

    assert "不依赖特定编号、标题、列表或表格格式" in prompt
    assert "每个明确划分的时间段或时间点活动记录" in prompt
    assert "不得因为相邻记录使用同一应用或涉及同一主题而合并" in prompt
    assert "严格按照原始时间顺序输出" in prompt
    assert "完整日期、开始时间和结束时间" in prompt
    assert "无记录、未知、空白或缺失数据的时间段" in prompt
    assert "EVT-xxx" not in prompt


def test_personal_memory_normalizer_preserves_explicit_timeline_segments():
    prompt = memory_info_prompts.PERSONAL_MEMORY_NORMALIZE_PROMPT_ZH

    assert "按时间顺序记录用户活动的时间流" in prompt
    assert "每个具有明确开始时间、结束时间、时间范围或独立时间点的活动记录" in prompt
    assert "不得因为使用同一应用、涉及同一主题或属于同一行为类型而跨时间段合并" in prompt
    assert "只有属于同一时间段" in prompt


def test_validate_memory_extraction_result_accepts_canonical_schema():
    result = utils.validate_memory_extraction_result(
        {
            "memory_list": [
                {
                    "key": "开发计划",
                    "memory_type": "UserMemory",
                    "value": "用户制定了视频解析器开发计划。",
                    "tags": ["开发"],
                    "evidence_part_ids": ["part_001"],
                    "confidence": 0.9,
                }
            ],
            "summary": "用户制定了开发计划。",
            "sequence_groups": [],
            "uncertainties": [],
        },
        context="interleaved media",
    )

    assert result["memory_list"][0]["value"] == "用户制定了视频解析器开发计划。"
    assert result["memory_list"][0]["evidence_part_ids"] == ["part_001"]


@pytest.mark.parametrize(
    "payload",
    [
        {"memory list": [], "summary": "legacy key"},
        {"memory_list": [], "memory list": [], "summary": "mixed keys"},
        {"summary": "missing list"},
        {"memory_list": {}, "summary": "wrong type"},
        {
            "memory_list": [
                {
                    "key": "bad nested key",
                    "memory type": "UserMemory",
                    "value": "用户制定了计划。",
                    "tags": [],
                }
            ],
            "summary": "wrong nested field",
        },
    ],
)
def test_validate_memory_extraction_result_rejects_noncanonical_schema(payload):
    with pytest.raises(ParserError, match="memory_list"):
        utils.validate_memory_extraction_result(payload, context="test")
