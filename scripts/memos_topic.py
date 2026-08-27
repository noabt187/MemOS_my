#!/usr/bin/env python3
"""External, explainable rolling Topic processor for memories created by MemOS.

MemOS remains the source of truth for memories.  This module keeps only a small,
human-readable JSON snapshot used to maintain fifteen rolling Topic seats across
calendar days.  Models classify evidence and write summaries; every score and
promotion/eviction decision is calculated by deterministic rules in this file.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TAG_PROMPT = """你是个人记忆关注价值与主题候选分析器。

请只根据给出的单条事件记忆完成两件事：
1. 给出程序计算重要性分数所需的离散判断；
2. 提取用于寻找相关记忆的候选标签。

你不能决定最终 Topic，也不能输出任何数字分数。

规则：
1. eligible 仅表示这是不是一条有实际内容、可以参与 Topic 选择的用户事件；系统指令、导入说明、空泛总结和明显猜测应为 false。
2. agency 只能是 observed、consumed、participated、acting、committed：仅看到、被动消费、参与、主动行动、明确决定或承诺。
3. action_requirement 只能是 none、optional、ongoing、clear_next_action、must_do。
4. impact 只能是 trivial、limited、meaningful、high；没有明确依据时不得因为领域名称而判为 high。
5. explicit_priority 只能是 none、interested、important、must_or_remind；只有用户明确表达时才能选择后两项。
6. effort 只能是 none、some、substantial，表示记忆是否明确记录了用户已经投入时间或精力；仅有计划但尚未行动时必须是 none。
7. confidence 只能是 low、medium、high，表示上述判断有多少直接证据。
8. reasons 必须分别说明 agency、action_requirement、impact、explicit_priority、effort 的原文依据；信息不足要直说，不得补充事实。
9. 提取 1 到 5 个真正相关的候选标签；无主题价值时可以返回空列表。
10. topic_key 必须简短、稳定，使用小写英文和下划线，例如 final_exam。
11. 优先复用已有标签目录；标签只用于寻找候选记忆，相同标签不代表同一个 Topic。
12. relationship 只能是 direct、related、weak；initiative_type 必须直接复用 assessment.agency，取值同样只能是 observed、consumed、participated、acting、committed。
13. 不得输出任何数字分数，只输出 JSON。

输出格式：
{
  "assessment": {
    "eligible": true,
    "agency": "acting",
    "action_requirement": "ongoing",
    "impact": "meaningful",
    "explicit_priority": "none",
    "effort": "some",
    "confidence": "high",
    "reasons": {
      "agency": "记忆明确说明用户正在复习",
      "action_requirement": "复习仍在进行",
      "impact": "考试会影响学习安排",
      "explicit_priority": "用户没有额外强调",
      "effort": "记忆说明用户已经开始复习"
    }
  },
  "tags": [
    {
      "topic_key": "final_exam",
      "tag_name": "期末考试",
      "relationship": "direct",
      "initiative_type": "acting",
      "reason": "记忆明确说明用户正在复习期末考试内容"
    }
  ]
}
"""

GROUP_PROMPT = """你是个人记忆 Topic 分组检查器。

候选记忆已经通过标签做了初筛，但相同标签不代表同一件事。请根据每条记忆的正文、时间和标签，把它们分成能够共同说明一个具体 Topic 的小组。

规则：
1. 同一事件的通知、准备、进展和结果可以放在一组。
2. 不同事件只有在共同体现一个具体、清晰的持续兴趣或行为时，才能放在一个 pattern 小组。
3. 仅仅属于同一大类、使用同一应用、人物相同或都带有“日程管理”等宽泛标签，不能作为合并理由。
4. 如果只能得到“用户有一些日程”“用户使用某应用”等空泛结论，必须拆开。
5. 每个输入 memory_id 必须且只能出现一次；不确定时保留为单条小组。
6. topic_kind 只能是 event 或 pattern。
7. 不得编造 memory_id 或记忆中不存在的事实。
8. 不负责打分，只输出 JSON。

输出格式：
{
  "groups": [
    {
      "memory_ids": ["真实记忆ID"],
      "topic_kind": "event",
      "reason": "这些记忆为什么能共同说明一个具体 Topic"
    }
  ]
}
"""

TOPIC_PROMPT = """你是个人滚动 Topic 总结器。

你会收到同一主题下的全部有效记忆。请综合全部证据，生成一句可变化的 Topic。

规则：
1. topic_text 必须是一句具体、自然、可读的话，不能只是一个标签。
2. reason_summary 简要说明为什么选择这个 Topic。
3. reason_evidence 必须逐条列出证据；每条必须包含真实 memory_id。
4. fact 只写对应记忆明确表达的事实。
5. contribution 说明这条事实如何支持 Topic，例如证明时间临近、持续行动或重要结果。
6. 不允许只写“多条记忆显示……”而不列出具体记忆。
7. 不得引用输入之外的 memory_id。
8. 排名分数已由程序根据单条记忆分数以及支持记忆的半权累积算好；你不得重新打分。
9. 只输出 JSON。

输出格式：
{
  "topic_text": "用户近期正在集中准备期末考试。",
  "reason_summary": "考试时间已经明确，并且用户正在持续复习。",
  "reason_evidence": [
    {
      "memory_id": "真实记忆ID",
      "fact": "用户查看了考试日期和考场。",
      "contribution": "证明考试安排已经明确。"
    }
  ]
}
"""


def _clamp(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _datetime_sort_value(value: Any) -> float:
    parsed = _parse_datetime(str(value or ""))
    return parsed.timestamp() if parsed is not None else 0.0


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("模型没有返回 JSON 对象") from None
        value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise TypeError("模型返回值必须是 JSON 对象")
    return value


@dataclass(frozen=True)
class TagEvidence:
    memory_id: str
    topic_key: str
    tag_name: str
    relationship: str
    initiative_type: str
    reason: str
    evidence_unit: str
    observed_at: str
    event_time: str | None
    event_status: str


@dataclass(frozen=True)
class CandidateMetrics:
    evidence_count: int
    relationship_vote_sum: float
    qualifies: bool
    supporting_memory_ids: list[str]
    latest_evidence_at: str | None
    score_breakdown: dict[str, Any]
    candidate_reasons: list[str]
    progress_status: str = "uncertain"


@dataclass(frozen=True)
class MemoryAssessment:
    memory_id: str
    eligible: bool
    agency: str
    action_requirement: str
    impact: str
    explicit_priority: str
    confidence: str
    score: float
    score_breakdown: dict[str, float]
    reasons: dict[str, str]
    effort: str = "none"
    selection_version: int = 2


@dataclass(frozen=True)
class MemoryGroup:
    memory_ids: list[str]
    topic_kind: str
    reason: str


@dataclass(frozen=True)
class ReasonEvidence:
    memory_id: str
    fact: str
    contribution: str


@dataclass(frozen=True)
class TopicDraft:
    topic_text: str
    reason_summary: str
    reason_evidence: list[ReasonEvidence]


@dataclass(frozen=True)
class BackfillResult:
    selected_memories: int
    pending_memories: int
    processed_memories: int


def extract_added_memories(response: Any) -> list[dict[str, Any]]:
    """Return every concrete memory produced by one synchronous MemOS add call."""
    if not isinstance(response, dict) or response.get("code", 200) != 200:
        return []
    data = response.get("data")
    if not isinstance(data, list):
        return []
    result = []
    for item in data:
        if isinstance(item, dict) and str(item.get("memory_id", "")).strip():
            result.append(dict(item))
    return result


RELATIONSHIP_WEIGHTS = {"direct": 1.0, "related": 0.5, "weak": 0.0}
INITIATIVE_WEIGHTS = {
    "initiated": 1.0,
    "committed": 1.0,
    "acting": 0.75,
    "participated": 0.4,
    "consumed": 0.0,
    "observed": 0.0,
}
STATUS_WEIGHTS = {
    "ongoing": 1.0,
    "planned": 0.7,
    "uncertain": 0.3,
    "completed": 0.2,
    "cancelled": 0.0,
}

AGENCY_POINTS = {
    "observed": 0.0,
    "consumed": 5.0,
    "participated": 12.0,
    "acting": 19.0,
    "committed": 25.0,
}
ACTION_POINTS = {
    "none": 0.0,
    "optional": 5.0,
    "ongoing": 15.0,
    "clear_next_action": 20.0,
    "must_do": 25.0,
}
IMPACT_POINTS = {
    "trivial": 0.0,
    "limited": 5.0,
    "meaningful": 10.0,
    "high": 15.0,
}
PRIORITY_POINTS = {
    "none": 0.0,
    "interested": 3.0,
    "important": 7.0,
    "must_or_remind": 10.0,
}
EFFORT_POINTS = {"none": 0.0, "some": 3.0, "substantial": 5.0}
CONFIDENCE_FACTORS = {"low": 0.5, "medium": 0.8, "high": 1.0}
TOPIC_SCORE_THRESHOLD = 60.0
TOPIC_SUPPORTING_WEIGHT = 0.5
TOPIC_SELECTION_VERSION = 2
TOPIC_SCORE_REFRESH_SECONDS = 15 * 60

_TRACE_DIMENSION_SPECS = (
    ("agency", "主动程度", "agency_points", AGENCY_POINTS, "model"),
    ("action_requirement", "行动要求", "action_points", ACTION_POINTS, "model"),
    ("impact", "影响程度", "impact_points", IMPACT_POINTS, "model"),
    (
        "explicit_priority",
        "明确优先级",
        "priority_points",
        PRIORITY_POINTS,
        "model",
    ),
    ("effort", "已投入精力", "effort_points", EFFORT_POINTS, "model_or_duration_rule"),
)
_URGENCY_RUBRIC = {
    "completed_or_cancelled": 0.0,
    "no_event_time": 0.0,
    "invalid_event_time": 0.0,
    "expired_over_24_hours": 0.0,
    "within_24_hours": 20.0,
    "within_72_hours": 16.0,
    "within_7_days": 12.0,
    "within_30_days": 6.0,
    "later_than_30_days": 2.0,
}

_TOPIC_STORE_LOCKS: dict[str, Any] = {}
_TOPIC_STORE_LOCKS_GUARD = threading.Lock()


def _shared_topic_store_lock(path: Path) -> Any:
    key = str(path.resolve())
    with _TOPIC_STORE_LOCKS_GUARD:
        return _TOPIC_STORE_LOCKS.setdefault(key, threading.RLock())


def _recency_factor(latest: datetime | None, now: datetime) -> float:
    if latest is None:
        return 0.25
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    age_hours = max(0.0, (now - latest.astimezone(now.tzinfo)).total_seconds() / 3600)
    if age_hours <= 24:
        return 1.0
    if age_hours <= 72:
        return 0.9
    if age_hours <= 24 * 7:
        return 0.75
    if age_hours <= 24 * 30:
        return 0.5
    return 0.25


def _urgency_weight(item: TagEvidence, now: datetime) -> float:
    if item.event_status in {"completed", "cancelled"}:
        return 0.0
    event_time = _parse_datetime(item.event_time)
    if event_time is None:
        return 0.0
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    hours = (event_time.astimezone(now.tzinfo) - now).total_seconds() / 3600
    if hours <= 24:
        return 1.0
    if hours <= 72:
        return 0.8
    if hours <= 24 * 7:
        return 0.6
    if hours <= 24 * 30:
        return 0.3
    return 0.1


def calculate_rank_score(*, base_score: float, recency_factor: float) -> float:
    """Return the visible 0-100 score used by the rolling fifteen seats."""
    return round(max(0.0, min(100.0, base_score)) * _clamp(recency_factor), 2)


def compute_candidate_metrics(
    evidence: list[TagEvidence], now: datetime | None = None
) -> CandidateMetrics:
    """Calculate a Topic candidate using only fixed, inspectable rules."""
    if not evidence:
        return CandidateMetrics(
            evidence_count=0,
            relationship_vote_sum=0.0,
            qualifies=False,
            supporting_memory_ids=[],
            latest_evidence_at=None,
            score_breakdown={
                "evidence_points": 0.0,
                "initiative_points": 0.0,
                "urgency_points": 0.0,
                "continuity_points": 0.0,
                "status_points": 0.0,
                "base_score": 0.0,
                "recency_factor": 0.25,
                "rank_score": 0.0,
            },
            candidate_reasons=[],
        )

    best_by_unit: dict[str, TagEvidence] = {}
    for item in evidence:
        current = best_by_unit.get(item.evidence_unit)
        item_weight = RELATIONSHIP_WEIGHTS.get(item.relationship, 0.0)
        current_weight = RELATIONSHIP_WEIGHTS.get(current.relationship, 0.0) if current else -1
        if current is None or item_weight > current_weight:
            best_by_unit[item.evidence_unit] = item

    counted = list(best_by_unit.values())
    reference_now = now or datetime.now().astimezone()
    valid_observed = [parsed for item in counted if (parsed := _parse_datetime(item.observed_at))]
    latest = max(valid_observed) if valid_observed else None
    unique_days = {item.astimezone(reference_now.tzinfo).date() for item in valid_observed}

    relationship_vote_sum = round(
        sum(RELATIONSHIP_WEIGHTS.get(item.relationship, 0.0) for item in counted), 2
    )
    evidence_points = round(30 * min(relationship_vote_sum / 3, 1.0), 2)
    initiative_weight = max(
        (INITIATIVE_WEIGHTS.get(item.initiative_type, 0.0) for item in counted), default=0.0
    )
    initiative_points = round(25 * initiative_weight, 2)
    urgency_weight = max((_urgency_weight(item, reference_now) for item in counted), default=0.0)
    urgency_points = round(20 * urgency_weight, 2)
    continuity_points = round(15 * min(len(unique_days) / 3, 1.0), 2)
    status_weight = max(
        (STATUS_WEIGHTS.get(item.event_status, 0.3) for item in counted), default=0.3
    )
    status_points = round(10 * status_weight, 2)
    base_score = round(
        evidence_points + initiative_points + urgency_points + continuity_points + status_points,
        2,
    )
    recency_factor = _recency_factor(latest, reference_now)
    rank_score = calculate_rank_score(
        base_score=base_score,
        recency_factor=recency_factor,
    )

    count = len(counted)
    repeated_evidence = count >= 2 and relationship_vote_sum >= 1.5
    active_initiative = any(
        item.initiative_type in {"initiated", "committed"}
        and item.event_status in {"planned", "ongoing"}
        for item in counted
    )
    near_deadline = urgency_weight >= 0.6
    candidate_reasons = []
    if repeated_evidence:
        candidate_reasons.append(f"有 {count} 个独立事件单元直接或明确支持该主题")
    if active_initiative:
        candidate_reasons.append("用户主动发起了计划中或进行中的事件")
    if near_deadline:
        candidate_reasons.append("存在七天内、已经到期或逾期的事件时间")
    qualifies = repeated_evidence or active_initiative or near_deadline
    dominant_status = max(
        (item.event_status for item in counted),
        key=lambda value: STATUS_WEIGHTS.get(value, 0.3),
        default="uncertain",
    )
    return CandidateMetrics(
        evidence_count=count,
        relationship_vote_sum=relationship_vote_sum,
        qualifies=qualifies,
        supporting_memory_ids=list(dict.fromkeys(item.memory_id for item in evidence)),
        latest_evidence_at=latest.isoformat() if latest else None,
        score_breakdown={
            "evidence_points": evidence_points,
            "initiative_points": initiative_points,
            "urgency_points": urgency_points,
            "continuity_points": continuity_points,
            "status_points": status_points,
            "base_score": base_score,
            "recency_factor": recency_factor,
            "rank_score": rank_score,
        },
        candidate_reasons=candidate_reasons,
        progress_status=dominant_status,
    )


def parse_topic_draft(
    raw: dict[str, Any],
    allowed_memory_ids: set[str],
    required_memory_ids: set[str] | None = None,
) -> TopicDraft:
    topic_text = str(raw.get("topic_text", "")).strip()
    reason_summary = str(raw.get("reason_summary", "")).strip()
    evidence_raw = raw.get("reason_evidence")
    if not topic_text or not reason_summary:
        raise ValueError("Topic 必须包含 topic_text 和 reason_summary")
    if not isinstance(evidence_raw, list) or not evidence_raw:
        raise ValueError("Topic 必须包含逐条 reason_evidence")

    reason_evidence = []
    for item in evidence_raw:
        if not isinstance(item, dict):
            raise TypeError("reason_evidence 的每一项必须是对象")
        memory_id = str(item.get("memory_id", "")).strip()
        fact = str(item.get("fact", "")).strip()
        contribution = str(item.get("contribution", "")).strip()
        if not memory_id or not fact or not contribution:
            raise ValueError("reason_evidence 必须包含 memory_id、fact 和 contribution")
        if memory_id not in allowed_memory_ids:
            raise ValueError(f"reason_evidence 引用了未知记忆：{memory_id}")
        reason_evidence.append(ReasonEvidence(memory_id, fact, contribution))

    cited_ids = {item.memory_id for item in reason_evidence}
    missing_ids = set(required_memory_ids or set()) - cited_ids
    if missing_ids:
        raise ValueError("reason_evidence 没有解释这些计分记忆：" + "、".join(sorted(missing_ids)))

    return TopicDraft(
        topic_text=topic_text,
        reason_summary=reason_summary,
        reason_evidence=reason_evidence,
    )


def _trace_number(mapping: dict[str, Any], key: str) -> float:
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"Topic 透明追踪缺少数值字段：{key}")
    return float(value)


def _trace_string(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Topic 透明追踪缺少文本字段：{key}")
    return value.strip()


def _trace_rubric(
    key: str,
    title: str,
    values: dict[str, float],
    *,
    score_unit: str = "points",
) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "score_unit": score_unit,
        "options": [
            {"label": label, "score_value": float(score_value)}
            for label, score_value in values.items()
        ],
    }


def _topic_trace_policy(seat_limit: int) -> dict[str, Any]:
    return {
        "topic_threshold": TOPIC_SCORE_THRESHOLD,
        "supporting_weight": TOPIC_SUPPORTING_WEIGHT,
        "seat_limit": max(1, seat_limit),
        "memory_formula": "min(100, 维度分合计) × 置信系数",
        "topic_formula": (
            "规范化后正文相同（忽略大小写和多余空白）的记忆只保留最高分；"
            "最强单条 + 其他非重复记忆 × 0.5"
        ),
        "rank_formula": (
            "Topic 基础分 × 新鲜系数（24 小时内 1.0，72 小时内 0.9，7 天内 0.75，"
            "30 天内 0.5，更早或没有时间 0.25）"
        ),
        "rubric": [
            _trace_rubric("agency", "主动程度", AGENCY_POINTS),
            _trace_rubric("action_requirement", "行动要求", ACTION_POINTS),
            _trace_rubric("urgency", "时间紧迫度", _URGENCY_RUBRIC),
            _trace_rubric("impact", "影响程度", IMPACT_POINTS),
            _trace_rubric("explicit_priority", "明确优先级", PRIORITY_POINTS),
            _trace_rubric(
                "effort",
                "已投入精力（模型分与时长规则取较高值：满 5 分钟但不足 20 分钟 1 分，"
                "满 20 分钟但不足 60 分钟 3 分，满 60 分钟 5 分）",
                EFFORT_POINTS,
            ),
            _trace_rubric(
                "confidence",
                "证据置信度",
                CONFIDENCE_FACTORS,
                score_unit="multiplier",
            ),
        ],
    }


def _urgency_trace_label(memory: dict[str, Any], score_value: float) -> tuple[str, str]:
    info = _memory_info(memory)
    event_status = str(info.get("event_status") or "uncertain").strip().lower()
    if event_status in {"completed", "cancelled"}:
        return "completed_or_cancelled", "事件已经完成或取消，时间紧迫度不加分。"

    event_time = _memory_event_time(memory)
    if event_time is None:
        return "no_event_time", "记忆没有保存可用于计算紧迫度的事件时间。"
    if _parse_datetime(event_time) is None:
        return "invalid_event_time", "保存的事件时间无法解析，初评时按无有效时间计 0 分。"

    labels_by_score = {
        0.0: ("expired_over_24_hours", "事件时间已经过去超过 24 小时。"),
        20.0: ("within_24_hours", "评估时距离事件时间不超过 24 小时。"),
        16.0: ("within_72_hours", "事件时间位于首次评估后的 72 小时内。"),
        12.0: ("within_7_days", "事件时间位于首次评估后的 7 天内。"),
        6.0: ("within_30_days", "事件时间位于首次评估后的 30 天内。"),
        2.0: ("later_than_30_days", "事件时间晚于首次评估时间 30 天以上。"),
    }
    if score_value not in labels_by_score:
        raise ValueError("Topic 透明追踪中的紧迫度分值不属于当前规则")
    return labels_by_score[score_value]


def _topic_trace_dimensions(
    memory: dict[str, Any],
    assessment: dict[str, Any],
) -> list[dict[str, Any]]:
    breakdown = assessment.get("score_breakdown")
    reasons = assessment.get("reasons")
    if not isinstance(breakdown, dict) or not isinstance(reasons, dict):
        raise TypeError("Topic 透明追踪缺少单条记忆评分明细")

    dimensions = []
    for key, title, points_key, rubric, source in _TRACE_DIMENSION_SPECS:
        label = _trace_string(assessment, key)
        if label not in rubric:
            raise ValueError(f"Topic 透明追踪中的 {key} 标签无效")
        reason = _trace_string(reasons, key)
        score_value = _trace_number(breakdown, points_key)
        dimension_source = source
        if key == "effort" and score_value > float(rubric[label]):
            dimension_source = "duration_rule"
            reason = f"事件时长规则将该项提高到 {score_value:g} 分；模型依据：{reason}"
        dimensions.append(
            {
                "key": key,
                "title": title,
                "label": label,
                "score_value": score_value,
                "score_unit": "points",
                "max_value": float(max(rubric.values())),
                "source": dimension_source,
                "reason": reason,
            }
        )

    urgency_points = _trace_number(breakdown, "urgency_points")
    urgency_label, urgency_reason = _urgency_trace_label(memory, urgency_points)
    dimensions.insert(
        2,
        {
            "key": "urgency",
            "title": "时间紧迫度",
            "label": urgency_label,
            "score_value": urgency_points,
            "score_unit": "points",
            "max_value": max(_URGENCY_RUBRIC.values()),
            "source": "time_rule",
            "reason": urgency_reason,
        },
    )

    confidence_label = _trace_string(assessment, "confidence")
    if confidence_label not in CONFIDENCE_FACTORS:
        raise ValueError("Topic 透明追踪中的 confidence 标签无效")
    confidence_factor = _trace_number(breakdown, "confidence_factor")
    dimensions.append(
        {
            "key": "confidence",
            "title": "证据置信度",
            "label": confidence_label,
            "score_value": confidence_factor,
            "score_unit": "multiplier",
            "max_value": max(CONFIDENCE_FACTORS.values()),
            "source": "model",
            "reason": f"模型将本次判断的置信度标记为 {confidence_label}。",
        }
    )
    return dimensions


def _unavailable_topic_trace(topic: dict[str, Any], reason: str) -> dict[str, Any]:
    selection_version = topic.get("selection_version")
    if isinstance(selection_version, bool) or not isinstance(selection_version, (int, float)):
        selection_version = None
    return {
        "topic_id": str(topic.get("topic_id") or ""),
        "topic_key": str(topic.get("topic_key") or ""),
        "available": False,
        "unavailable_reason": reason,
        "selection_version": selection_version,
        "policy": None,
        "grouping": None,
        "decision": None,
        "memories": [],
    }


class TopicStore:
    """Small JSON snapshot for rolling Topics; MemOS remains the memory database."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = _shared_topic_store_lock(self.path)
        if not self.path.exists():
            self._write(self._empty_state())

    def transaction(self) -> Any:
        """Serialize one complete Topic update across store instances in this process."""
        return self._lock

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {"schema_version": 1, "updated_at": _now_iso(), "scopes": {}}

    @staticmethod
    def _scope_key(user_id: str, cube_id: str) -> str:
        return json.dumps([user_id, cube_id], ensure_ascii=False, separators=(",", ":"))

    def _read(self) -> dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                return self._empty_state()
            try:
                state = json.loads(self.path.read_text(encoding="utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(
                    f"Topic 状态文件不是有效 JSON：{self.path}。旧 SQLite 文件不会自动覆盖。"
                ) from exc
            if not isinstance(state, dict) or state.get("schema_version") != 1:
                raise ValueError(f"不支持的 Topic 状态文件：{self.path}")
            state.setdefault("scopes", {})
            for scope in state["scopes"].values():
                if isinstance(scope, dict):
                    scope.setdefault("memories", {})
                    scope.setdefault("tags", {})
                    scope.setdefault("assessments", {})
                    scope.setdefault("group_cache", {})
                    scope.setdefault("topics", {})
            return state

    def _write(self, state: dict[str, Any]) -> None:
        with self._lock:
            state["updated_at"] = _now_iso()
            temporary = self.path.with_name(f".{self.path.name}.tmp")
            temporary.write_text(
                json.dumps(state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, self.path)

    def _scope(
        self,
        state: dict[str, Any],
        user_id: str,
        cube_id: str,
        *,
        create: bool = False,
    ) -> dict[str, Any] | None:
        scopes = state.setdefault("scopes", {})
        key = self._scope_key(user_id, cube_id)
        scope = scopes.get(key)
        if scope is None and create:
            scope = {
                "user_id": user_id,
                "cube_id": cube_id,
                "memories": {},
                "tags": {},
                "assessments": {},
                "group_cache": {},
                "topics": {},
            }
            scopes[key] = scope
        return scope

    def save_memory(self, *, user_id: str, cube_id: str, memory: dict[str, Any]) -> None:
        memory_id = _memory_id(memory)
        if not memory_id:
            raise ValueError("不能保存没有 memory_id 的 Topic 证据")
        original_metadata = (
            memory.get("metadata") if isinstance(memory.get("metadata"), dict) else {}
        )
        observed_at = _memory_observed_at(memory)
        minimal_memory = {
            "id": memory_id,
            "memory": _memory_text(memory),
            "memory_type": memory.get("memory_type"),
            "metadata": {
                "status": original_metadata.get("status", "activated"),
                "created_at": (
                    original_metadata.get("created_at") or memory.get("created_at") or observed_at
                ),
                "info": _memory_info(memory),
            },
        }
        state = self._read()
        scope = self._scope(state, user_id, cube_id, create=True)
        assert scope is not None
        scope["memories"][memory_id] = {
            "memory": minimal_memory,
            "observed_at": observed_at,
            "active": True,
            "processed_at": _now_iso(),
        }
        self._write(state)

    def replace_tags(
        self,
        *,
        user_id: str,
        cube_id: str,
        memory_id: str,
        evidence: list[TagEvidence],
    ) -> None:
        state = self._read()
        scope = self._scope(state, user_id, cube_id, create=True)
        assert scope is not None
        scope["tags"][memory_id] = [asdict(item) for item in evidence]
        self._write(state)

    def replace_assessment(
        self,
        *,
        user_id: str,
        cube_id: str,
        assessment: MemoryAssessment,
    ) -> None:
        state = self._read()
        scope = self._scope(state, user_id, cube_id, create=True)
        assert scope is not None
        scope["assessments"][assessment.memory_id] = asdict(assessment)
        self._write(state)

    def assessed_memory_ids(self, user_id: str, cube_id: str) -> set[str]:
        state = self._read()
        scope = self._scope(state, user_id, cube_id)
        if scope is None:
            return set()
        return {
            str(memory_id)
            for memory_id, assessment in scope["assessments"].items()
            if isinstance(assessment, dict)
            and assessment.get("selection_version") == TOPIC_SELECTION_VERSION
        }

    def active_selection_data(
        self,
        user_id: str,
        cube_id: str,
    ) -> tuple[list[dict[str, Any]], dict[str, MemoryAssessment], dict[str, list[TagEvidence]]]:
        state = self._read()
        scope = self._scope(state, user_id, cube_id)
        if scope is None:
            return [], {}, {}
        memories = []
        assessments = {}
        tags_by_memory = {}
        for memory_id, record in scope["memories"].items():
            if not record.get("active", False):
                continue
            assessment_raw = scope["assessments"].get(memory_id)
            if not isinstance(assessment_raw, dict):
                continue
            try:
                assessment = MemoryAssessment(**assessment_raw)
            except TypeError as exc:
                raise ValueError(f"记忆 {memory_id} 的 Topic assessment 状态无效") from exc
            if assessment.selection_version != TOPIC_SELECTION_VERSION:
                raise ValueError(f"记忆 {memory_id} 的 Topic assessment 版本需要重新处理")
            memories.append(record["memory"])
            assessments[memory_id] = assessment
            tags_by_memory[memory_id] = [
                TagEvidence(**item)
                for item in scope["tags"].get(memory_id, [])
                if isinstance(item, dict)
            ]
        return memories, assessments, tags_by_memory

    def current_topic_records(
        self,
        user_id: str,
        cube_id: str,
        *,
        include_retired: bool = False,
    ) -> list[dict[str, Any]]:
        state = self._read()
        scope = self._scope(state, user_id, cube_id)
        if scope is None:
            return []
        statuses = {"active", "suppressed"}
        if include_retired:
            statuses.add("retired")
        return [
            dict(item)
            for item in scope["topics"].values()
            if item.get("lifecycle_status") in statuses
        ]

    def stored_topic_keys(self, user_id: str, cube_id: str) -> set[str]:
        state = self._read()
        scope = self._scope(state, user_id, cube_id)
        if scope is None:
            return set()
        return {str(topic_key) for topic_key in scope["topics"]}

    def topic_record(
        self,
        *,
        user_id: str,
        cube_id: str,
        topic_key: str,
    ) -> dict[str, Any] | None:
        state = self._read()
        scope = self._scope(state, user_id, cube_id)
        if scope is None or topic_key not in scope["topics"]:
            return None
        return dict(scope["topics"][topic_key])

    def topic_selection_trace(
        self,
        *,
        user_id: str,
        cube_id: str,
        topic_id: str,
        seat_limit: int = 15,
    ) -> dict[str, Any] | None:
        """Project saved state without recalculation; current_score is the Topic snapshot score."""
        state = self._read()
        scope = self._scope(state, user_id, cube_id)
        if scope is None:
            return None

        topic = next(
            (
                item
                for item in scope["topics"].values()
                if isinstance(item, dict) and str(item.get("topic_id") or "") == topic_id
            ),
            None,
        )
        if topic is None:
            return None
        if topic.get("selection_version") != TOPIC_SELECTION_VERSION:
            return _unavailable_topic_trace(
                topic,
                "历史 Topic 未保存完整的单条记忆初评过程。",
            )

        try:
            supporting_memory_ids_raw = topic.get("supporting_memory_ids")
            if not isinstance(supporting_memory_ids_raw, list) or not supporting_memory_ids_raw:
                raise ValueError("Topic 透明追踪缺少支持记忆")
            supporting_memory_ids = [
                str(memory_id).strip() for memory_id in supporting_memory_ids_raw
            ]
            if any(not memory_id for memory_id in supporting_memory_ids):
                raise ValueError("Topic 透明追踪包含无效的支持记忆 ID")

            score_breakdown = topic.get("score_breakdown")
            if not isinstance(score_breakdown, dict):
                raise TypeError("Topic 透明追踪缺少 Topic 分数明细")
            base_score = _trace_number(score_breakdown, "base_score")
            recency_factor = _trace_number(score_breakdown, "recency_factor")
            rank_score = _trace_number(score_breakdown, "rank_score")

            counted_memory_ids_raw = score_breakdown.get("counted_memory_ids")
            memory_scores = score_breakdown.get("memory_scores")
            if not isinstance(counted_memory_ids_raw, list) or not isinstance(memory_scores, dict):
                raise TypeError("Topic 透明追踪缺少记忆计分结果")
            counted_memory_ids = {
                str(memory_id).strip()
                for memory_id in counted_memory_ids_raw
                if str(memory_id).strip()
            }

            candidate_reasons_raw = topic.get("candidate_reasons")
            if not isinstance(candidate_reasons_raw, list) or any(
                not isinstance(reason, str) for reason in candidate_reasons_raw
            ):
                raise ValueError("Topic 透明追踪缺少候选判定原因")
            candidate_reasons = [
                reason.strip() for reason in candidate_reasons_raw if reason.strip()
            ]

            topic_kind = _trace_string(topic, "topic_kind")
            grouping_reason = _trace_string(topic, "grouping_reason")
            candidate_tag_keys_raw = topic.get("candidate_tag_keys")
            if not isinstance(candidate_tag_keys_raw, list) or any(
                not isinstance(key, str) for key in candidate_tag_keys_raw
            ):
                raise ValueError("Topic 透明追踪缺少候选标签")
            candidate_tag_keys = [key.strip() for key in candidate_tag_keys_raw if key.strip()]

            ranked_topics = [
                item
                for item in scope["topics"].values()
                if isinstance(item, dict)
                and item.get("lifecycle_status") in {"active", "suppressed"}
            ]
            ranked_topics.sort(
                key=lambda item: (
                    -_trace_number(item, "rank_score"),
                    -_datetime_sort_value(item.get("last_evidence_at")),
                    str(item.get("topic_key") or ""),
                )
            )
            rank_position = next(
                (
                    index
                    for index, item in enumerate(ranked_topics, start=1)
                    if str(item.get("topic_id") or "") == topic_id
                ),
                None,
            )
            if rank_position is None:
                raise ValueError("Topic 当前不在滚动席位候选池中")
            seat_status = _trace_string(topic, "lifecycle_status")

            memories = []
            for memory_id in supporting_memory_ids:
                record = scope["memories"].get(memory_id)
                assessment = scope["assessments"].get(memory_id)
                tags_raw = scope["tags"].get(memory_id)
                if not isinstance(record, dict) or not isinstance(assessment, dict):
                    raise TypeError(f"记忆 {memory_id} 缺少初评状态")
                if assessment.get("selection_version") != TOPIC_SELECTION_VERSION:
                    raise ValueError(f"记忆 {memory_id} 的初评版本不支持透明追踪")
                if not isinstance(tags_raw, list):
                    raise TypeError(f"记忆 {memory_id} 缺少候选标签状态")

                stored_memory = record.get("memory")
                if not isinstance(stored_memory, dict):
                    raise TypeError(f"记忆 {memory_id} 缺少正文快照")
                active = record.get("active")
                eligible = assessment.get("eligible")
                if not isinstance(active, bool) or not isinstance(eligible, bool):
                    raise TypeError(f"记忆 {memory_id} 缺少资格状态")

                dimensions = _topic_trace_dimensions(stored_memory, assessment)
                initial_score = _trace_number(assessment, "score")
                current_score = _trace_number(memory_scores, memory_id)
                confidence_dimension = next(
                    dimension for dimension in dimensions if dimension["key"] == "confidence"
                )
                raw_points = round(
                    sum(
                        float(dimension["score_value"])
                        for dimension in dimensions
                        if dimension["score_unit"] == "points"
                    ),
                    2,
                )

                tags = []
                for raw_tag in tags_raw:
                    if not isinstance(raw_tag, dict):
                        raise TypeError(f"记忆 {memory_id} 包含无效候选标签")
                    tags.append(
                        {
                            "topic_key": _trace_string(raw_tag, "topic_key"),
                            "tag_name": _trace_string(raw_tag, "tag_name"),
                            "relationship": _trace_string(raw_tag, "relationship"),
                            "reason": _trace_string(raw_tag, "reason"),
                        }
                    )

                assessed_at_raw = record.get("processed_at")
                assessed_at = assessed_at_raw if isinstance(assessed_at_raw, str) else None
                memories.append(
                    {
                        "memory_id": memory_id,
                        "text": _memory_text(stored_memory),
                        "active": active,
                        "assessed_at": assessed_at,
                        "eligible": eligible,
                        "initial_score": initial_score,
                        "current_score": current_score,
                        "counting_status": (
                            "counted" if memory_id in counted_memory_ids else "duplicate"
                        ),
                        "raw_points": raw_points,
                        "confidence_factor": float(confidence_dimension["score_value"]),
                        "dimensions": dimensions,
                        "tags": tags,
                    }
                )
        except (KeyError, StopIteration, TypeError, ValueError) as exc:
            return _unavailable_topic_trace(topic, str(exc))

        return {
            "topic_id": str(topic["topic_id"]),
            "topic_key": str(topic["topic_key"]),
            "available": True,
            "unavailable_reason": None,
            "selection_version": TOPIC_SELECTION_VERSION,
            "policy": _topic_trace_policy(seat_limit),
            "grouping": {
                "topic_kind": topic_kind,
                "reason": grouping_reason,
                "candidate_tag_keys": candidate_tag_keys,
                "memory_ids": supporting_memory_ids,
            },
            "decision": {
                "qualifies": base_score >= TOPIC_SCORE_THRESHOLD,
                "base_score": base_score,
                "recency_factor": recency_factor,
                "rank_score": rank_score,
                "rank_position": rank_position,
                "seat_status": seat_status,
                "candidate_reasons": candidate_reasons,
            },
            "memories": memories,
        }

    def grouping_cache(self, user_id: str, cube_id: str) -> dict[str, list[MemoryGroup]]:
        state = self._read()
        scope = self._scope(state, user_id, cube_id)
        if scope is None:
            return {}
        result: dict[str, list[MemoryGroup]] = {}
        for fingerprint, groups in scope["group_cache"].items():
            if not isinstance(groups, list):
                continue
            try:
                result[str(fingerprint)] = [MemoryGroup(**item) for item in groups]
            except (TypeError, ValueError):
                continue
        return result

    def replace_grouping_cache(
        self,
        *,
        user_id: str,
        cube_id: str,
        groups_by_fingerprint: dict[str, list[MemoryGroup]],
    ) -> None:
        state = self._read()
        scope = self._scope(state, user_id, cube_id, create=True)
        assert scope is not None
        scope["group_cache"] = {
            fingerprint: [asdict(group) for group in groups]
            for fingerprint, groups in groups_by_fingerprint.items()
        }
        scope["last_rebuilt_at"] = _now_iso()
        self._write(state)

    def scopes_due_for_score_refresh(
        self,
        *,
        now: datetime,
        interval_seconds: int = TOPIC_SCORE_REFRESH_SECONDS,
    ) -> set[tuple[str, str]]:
        state = self._read()
        result: set[tuple[str, str]] = set()
        for scope in state["scopes"].values():
            has_active_assessment = any(
                record.get("active", False) and memory_id in scope["assessments"]
                for memory_id, record in scope["memories"].items()
            )
            if not has_active_assessment:
                continue
            last_rebuilt = _parse_datetime(str(scope.get("last_rebuilt_at") or ""))
            if last_rebuilt is None:
                result.add((str(scope["user_id"]), str(scope["cube_id"])))
                continue
            reference_now = now
            if reference_now.tzinfo is None:
                reference_now = reference_now.replace(tzinfo=timezone.utc)
            elapsed = (
                reference_now - last_rebuilt.astimezone(reference_now.tzinfo)
            ).total_seconds()
            if elapsed >= interval_seconds:
                result.add((str(scope["user_id"]), str(scope["cube_id"])))
        return result

    def tag_catalog(self, user_id: str, cube_id: str, limit: int = 100) -> list[dict[str, str]]:
        state = self._read()
        scope = self._scope(state, user_id, cube_id)
        if scope is None:
            return []
        counts: dict[str, tuple[int, str]] = {}
        for memory_id, tags in scope["tags"].items():
            if not scope["memories"].get(memory_id, {}).get("active", False):
                continue
            assessment = scope["assessments"].get(memory_id)
            if isinstance(assessment, dict) and assessment.get("eligible") is False:
                continue
            for item in tags:
                key = str(item.get("topic_key", ""))
                count, _ = counts.get(key, (0, ""))
                counts[key] = (count + 1, str(item.get("tag_name", "")))
        ordered = sorted(counts.items(), key=lambda item: (-item[1][0], item[0]))[:limit]
        return [{"topic_key": key, "tag_name": value[1]} for key, value in ordered]

    def topic_keys(self, user_id: str, cube_id: str) -> list[str]:
        """Return every key that still has active memory evidence."""
        return [item["topic_key"] for item in self.tag_catalog(user_id, cube_id, limit=2000)]

    def has_topic(self, user_id: str, cube_id: str, topic_key: str) -> bool:
        state = self._read()
        scope = self._scope(state, user_id, cube_id)
        return bool(scope and topic_key in scope["topics"])

    def refresh_topic_metrics(
        self,
        *,
        user_id: str,
        cube_id: str,
        topic_key: str,
        metrics: CandidateMetrics,
        topic_kind: str | None = None,
        grouping_reason: str | None = None,
        candidate_tag_keys: list[str] | None = None,
        selection_fingerprint: str | None = None,
    ) -> None:
        """Refresh deterministic scores without paying for a new model summary."""
        state = self._read()
        scope = self._scope(state, user_id, cube_id)
        if scope is None or topic_key not in scope["topics"]:
            return
        topic = scope["topics"][topic_key]
        topic["supporting_memory_ids"] = metrics.supporting_memory_ids
        topic["candidate_reasons"] = metrics.candidate_reasons
        topic["score_breakdown"] = metrics.score_breakdown
        topic["rank_score"] = metrics.score_breakdown["rank_score"]
        topic["progress_status"] = metrics.progress_status
        topic["last_evidence_at"] = metrics.latest_evidence_at or topic.get("last_evidence_at")
        topic["selection_version"] = TOPIC_SELECTION_VERSION
        if topic_kind is not None:
            topic["topic_kind"] = topic_kind
        if grouping_reason is not None:
            topic["grouping_reason"] = grouping_reason
        if candidate_tag_keys is not None:
            topic["candidate_tag_keys"] = candidate_tag_keys
        if selection_fingerprint is not None:
            topic["selection_fingerprint"] = selection_fingerprint
        topic["updated_at"] = _now_iso()
        self._write(state)

    def evidence_for_topic(
        self,
        *,
        user_id: str,
        cube_id: str,
        topic_key: str,
        topic_date: str | None = None,
    ) -> list[TagEvidence]:
        del topic_date  # Topics intentionally span midnight and are never partitioned by date.
        state = self._read()
        scope = self._scope(state, user_id, cube_id)
        if scope is None:
            return []
        result = []
        for memory_id, tags in scope["tags"].items():
            if not scope["memories"].get(memory_id, {}).get("active", False):
                continue
            for item in tags:
                if item.get("topic_key") == topic_key:
                    result.append(TagEvidence(**item))
        return sorted(result, key=lambda item: item.observed_at)

    def memories_by_ids(self, memory_ids: list[str]) -> list[dict[str, Any]]:
        wanted = set(memory_ids)
        result: dict[str, dict[str, Any]] = {}
        state = self._read()
        for scope in state["scopes"].values():
            for memory_id, record in scope["memories"].items():
                if memory_id in wanted and record.get("active", False):
                    result[memory_id] = record["memory"]
        return [result[memory_id] for memory_id in memory_ids if memory_id in result]

    def active_memory_ids(self) -> list[str]:
        state = self._read()
        return sorted(
            memory_id
            for scope in state["scopes"].values()
            for memory_id, record in scope["memories"].items()
            if record.get("active", False)
        )

    def tagged_memory_ids(self, user_id: str, cube_id: str) -> set[str]:
        """Return memories whose Topic tag extraction has completed, including empty results."""
        state = self._read()
        scope = self._scope(state, user_id, cube_id)
        if scope is None:
            return set()
        return {str(memory_id) for memory_id in scope["tags"]}

    def deactivate_memory(self, memory_id: str) -> list[tuple[str, str, str]]:
        """Stop one deleted/archived MemOS memory from voting and return affected Topics."""
        state = self._read()
        affected: list[tuple[str, str, str]] = []
        for scope in state["scopes"].values():
            record = scope["memories"].get(memory_id)
            if not record or not record.get("active", False):
                continue
            record["active"] = False
            topic_keys = {str(item.get("topic_key")) for item in scope["tags"].get(memory_id, [])}
            topic_keys.update(
                str(topic_key)
                for topic_key, topic in scope["topics"].items()
                if memory_id in topic.get("supporting_memory_ids", [])
            )
            affected.extend(
                (str(scope["user_id"]), str(scope["cube_id"]), key) for key in topic_keys
            )
        self._write(state)
        return affected

    def retire_topic(
        self,
        *,
        user_id: str,
        cube_id: str,
        topic_key: str,
        topic_date: str | None = None,
    ) -> None:
        del topic_date
        state = self._read()
        scope = self._scope(state, user_id, cube_id)
        if scope and topic_key in scope["topics"]:
            scope["topics"][topic_key]["lifecycle_status"] = "retired"
            scope["topics"][topic_key]["updated_at"] = _now_iso()
            self._write(state)

    def retire_unmatched_topics(
        self,
        *,
        user_id: str,
        cube_id: str,
        kept_topic_keys: set[str],
        include_legacy: bool = False,
    ) -> None:
        """Retire current Topics that no longer have a qualifying memory group."""
        state = self._read()
        scope = self._scope(state, user_id, cube_id)
        if scope is None:
            return
        changed = False
        now = _now_iso()
        for topic_key, topic in scope["topics"].items():
            if topic_key in kept_topic_keys:
                continue
            if topic.get("lifecycle_status") not in {"active", "suppressed"}:
                continue
            if not include_legacy and topic.get("selection_version") != TOPIC_SELECTION_VERSION:
                continue
            topic["lifecycle_status"] = "retired"
            topic["updated_at"] = now
            changed = True
        if changed:
            self._write(state)

    def upsert_topic(
        self,
        *,
        user_id: str,
        cube_id: str,
        topic_key: str,
        draft: TopicDraft,
        metrics: CandidateMetrics,
        topic_date: str | None = None,
        rank_score: float | None = None,
        topic_kind: str = "event",
        grouping_reason: str = "",
        candidate_tag_keys: list[str] | None = None,
        selection_fingerprint: str = "",
    ) -> str:
        del topic_date
        now = _now_iso()
        state = self._read()
        scope = self._scope(state, user_id, cube_id, create=True)
        assert scope is not None
        existing = scope["topics"].get(topic_key)
        topic_id = existing["topic_id"] if existing else f"topic-{uuid.uuid4().hex}"
        versions = list(existing.get("versions", [])) if existing else []
        if existing:
            versions.append({key: value for key, value in existing.items() if key != "versions"})
            versions = versions[-20:]
        effective_rank = (
            float(rank_score)
            if rank_score is not None
            else float(metrics.score_breakdown.get("rank_score", 0.0))
        )
        last_evidence_at = metrics.latest_evidence_at or now
        scope["topics"][topic_key] = {
            "topic_id": topic_id,
            "user_id": user_id,
            "cube_id": cube_id,
            "topic_key": topic_key,
            "topic_text": draft.topic_text,
            "reason_summary": draft.reason_summary,
            "reason_evidence": [asdict(item) for item in draft.reason_evidence],
            "supporting_memory_ids": metrics.supporting_memory_ids,
            "candidate_reasons": metrics.candidate_reasons,
            "score_breakdown": metrics.score_breakdown,
            "rank_score": effective_rank,
            "progress_status": metrics.progress_status,
            "topic_kind": topic_kind,
            "grouping_reason": grouping_reason,
            "candidate_tag_keys": list(candidate_tag_keys or []),
            "selection_version": TOPIC_SELECTION_VERSION,
            "selection_fingerprint": selection_fingerprint,
            "lifecycle_status": "active",
            "topic_date": _topic_date(last_evidence_at),
            "first_seen_at": existing.get("first_seen_at", now) if existing else now,
            "last_evidence_at": last_evidence_at,
            "version": int(existing.get("version", 0)) + 1 if existing else 1,
            "updated_at": now,
            "versions": versions,
        }
        self._write(state)
        return topic_id

    def rebalance(
        self,
        *,
        user_id: str,
        cube_id: str,
        limit: int,
        topic_date: str | None = None,
    ) -> None:
        del topic_date
        state = self._read()
        scope = self._scope(state, user_id, cube_id)
        if scope is None:
            return
        eligible = [
            item
            for item in scope["topics"].values()
            if item.get("lifecycle_status") in {"active", "suppressed"}
        ]
        eligible.sort(
            key=lambda item: (
                -float(item.get("rank_score", 0.0)),
                -_datetime_sort_value(item.get("last_evidence_at")),
                str(item.get("topic_key", "")),
            )
        )
        active_ids = {item["topic_id"] for item in eligible[: max(1, limit)]}
        for item in eligible:
            item["lifecycle_status"] = "active" if item["topic_id"] in active_ids else "suppressed"
        self._write(state)

    def list_topics(
        self,
        *,
        user_id: str,
        cube_id: str,
        topic_date: str | None = None,
        include_suppressed: bool = False,
    ) -> list[dict[str, Any]]:
        del topic_date
        state = self._read()
        scope = self._scope(state, user_id, cube_id)
        if scope is None:
            return []
        statuses = {"active", "suppressed"} if include_suppressed else {"active"}
        result = [
            item for item in scope["topics"].values() if item.get("lifecycle_status") in statuses
        ]
        result.sort(
            key=lambda item: (
                -float(item.get("rank_score", 0.0)),
                -_datetime_sort_value(item.get("last_evidence_at")),
            )
        )
        return json.loads(json.dumps(result, ensure_ascii=False))

    def list_all_topics(
        self,
        *,
        user_id: str,
        cube_id: str,
        include_suppressed: bool = False,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Return the current rolling pool; dates are display metadata, not partitions."""
        return self.list_topics(
            user_id=user_id,
            cube_id=cube_id,
            include_suppressed=include_suppressed,
        )[: max(1, min(limit, 2000))]


def default_store_path() -> Path:
    configured = os.getenv("MEMOS_TOPIC_STATE", "").strip()
    if configured:
        return Path(configured).expanduser()
    legacy = os.getenv("MEMOS_TOPIC_DB", "").strip()
    if legacy:
        return Path(legacy).expanduser().with_suffix(".json")
    return Path(__file__).resolve().parents[1] / ".memos" / "topic" / "topics.json"


def _legacy_json(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, dict | list):
        return value
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback
    return parsed


def _legacy_memory(row: sqlite3.Row) -> dict[str, Any]:
    raw = _legacy_json(row["memory_json"], {})
    original = dict(raw) if isinstance(raw, dict) else {}
    memory_id = str(row["memory_id"] or _memory_id(original)).strip()
    metadata = original.get("metadata")
    if not isinstance(metadata, dict):
        metadata = _legacy_json(metadata, {})
    metadata = dict(metadata) if isinstance(metadata, dict) else {}
    info = metadata.get("info")
    if not isinstance(info, dict):
        info = _legacy_json(info, {})
    return {
        "id": memory_id,
        "memory": str(row["memory_text"] or _memory_text(original)).strip(),
        "memory_type": original.get("memory_type"),
        "metadata": {
            "status": metadata.get("status", "activated"),
            "created_at": metadata.get("created_at") or original.get("created_at"),
            "info": dict(info) if isinstance(info, dict) else {},
        },
    }


def _legacy_score(value: Any, maximum: float) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    normalized = score / 100 if score > 1 else score
    return round(max(0.0, min(1.0, normalized)) * maximum, 2)


def _legacy_score_breakdown(row: sqlite3.Row) -> dict[str, float]:
    evidence_points = _legacy_score(row["support_score"], 30)
    initiative_points = _legacy_score(row["execution_score"], 25)
    urgency_points = _legacy_score(row["urgency_score"], 20)
    continuity_points = _legacy_score(row["importance_score"], 15)
    status_points = 3.0
    base_score = round(
        evidence_points + initiative_points + urgency_points + continuity_points + status_points,
        2,
    )
    recency_factor = _clamp(row["recency_score"])
    return {
        "evidence_points": evidence_points,
        "initiative_points": initiative_points,
        "urgency_points": urgency_points,
        "continuity_points": continuity_points,
        "status_points": status_points,
        "base_score": base_score,
        "recency_factor": recency_factor,
        "rank_score": calculate_rank_score(
            base_score=base_score,
            recency_factor=recency_factor,
        ),
    }


def migrate_legacy_sqlite(
    legacy_path: str | Path,
    state_path: str | Path,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Convert the legacy daily SQLite snapshot into the rolling JSON snapshot.

    The SQLite database is opened read-only. JSON is first written and parsed at
    a temporary path, then atomically moved into place.
    """
    source = Path(legacy_path).resolve()
    destination = Path(state_path).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"旧 Topic 数据库不存在：{source}")
    if source == destination:
        raise ValueError("旧 SQLite 数据库和新 JSON 状态文件不能是同一路径")
    if destination.exists() and destination.stat().st_size > 0 and not overwrite:
        raise FileExistsError(
            f"目标 Topic 状态文件已经存在：{destination}。确认后可使用 --overwrite 覆盖。"
        )

    state = TopicStore._empty_state()
    scope_topics: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    topic_versions: dict[str, list[dict[str, Any]]] = {}
    topic_ids_by_key: dict[tuple[str, str, str], set[str]] = {}
    counts = {"memories": 0, "tags": 0, "topics": 0, "versions": 0}

    source_uri = f"file:{source.as_posix()}?mode=ro"
    with sqlite3.connect(source_uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        table_names = {
            str(row["name"])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }

        if "memory_snapshots" in table_names:
            for row in connection.execute("SELECT * FROM memory_snapshots"):
                user_id = str(row["user_id"] or "default")
                cube_id = str(row["cube_id"] or "default_cube")
                scope_key = TopicStore._scope_key(user_id, cube_id)
                scope = state["scopes"].setdefault(
                    scope_key,
                    {
                        "user_id": user_id,
                        "cube_id": cube_id,
                        "memories": {},
                        "tags": {},
                        "topics": {},
                    },
                )
                memory = _legacy_memory(row)
                memory_id = _memory_id(memory)
                scope["memories"][memory_id] = {
                    "memory": memory,
                    "observed_at": str(row["observed_at"] or _memory_observed_at(memory)),
                    "active": bool(row["active"]),
                    "processed_at": str(row["processed_at"] or _now_iso()),
                }
                counts["memories"] += 1

        if "memory_tags" in table_names:
            for row in connection.execute("SELECT * FROM memory_tags"):
                user_id = str(row["user_id"] or "default")
                cube_id = str(row["cube_id"] or "default_cube")
                scope_key = TopicStore._scope_key(user_id, cube_id)
                scope = state["scopes"].setdefault(
                    scope_key,
                    {
                        "user_id": user_id,
                        "cube_id": cube_id,
                        "memories": {},
                        "tags": {},
                        "topics": {},
                    },
                )
                memory_id = str(row["memory_id"] or "").strip()
                memory_record = scope["memories"].get(memory_id, {})
                memory = memory_record.get("memory", {})
                info = _memory_info(memory) if isinstance(memory, dict) else {}
                relevance = _clamp(row["relevance"])
                execution = _clamp(row["execution"])
                evidence = TagEvidence(
                    memory_id=memory_id,
                    topic_key=str(row["topic_key"] or "").strip(),
                    tag_name=str(row["tag_name"] or "").strip(),
                    relationship=(
                        "direct" if relevance >= 0.75 else "related" if relevance >= 0.4 else "weak"
                    ),
                    initiative_type=(
                        "acting"
                        if execution >= 0.75
                        else "participated"
                        if execution >= 0.35
                        else "observed"
                    ),
                    reason=str(row["reason"] or "").strip(),
                    evidence_unit=str(row["evidence_unit"] or memory_id).strip(),
                    observed_at=str(
                        row["observed_at"] or memory_record.get("observed_at") or _now_iso()
                    ),
                    event_time=str(
                        info.get("event_start_at")
                        or info.get("event_end_at")
                        or info.get("event_time")
                        or ""
                    )
                    or None,
                    event_status=str(info.get("event_status") or "uncertain"),
                )
                scope["tags"].setdefault(memory_id, []).append(asdict(evidence))
                counts["tags"] += 1

        if "daily_topics" in table_names:
            for row in connection.execute("SELECT * FROM daily_topics"):
                item = dict(row)
                key = (
                    str(row["user_id"] or "default"),
                    str(row["cube_id"] or "default_cube"),
                    str(row["topic_key"] or "").strip(),
                )
                scope_topics.setdefault(key, []).append(item)
                topic_ids_by_key.setdefault(key, set()).add(str(row["topic_id"] or ""))

        if "topic_versions" in table_names:
            for row in connection.execute("SELECT * FROM topic_versions"):
                snapshot = _legacy_json(row["snapshot_json"], {})
                if not isinstance(snapshot, dict):
                    snapshot = {}
                snapshot = dict(snapshot)
                snapshot.setdefault("version", int(row["version"] or 0))
                snapshot.setdefault("updated_at", str(row["created_at"] or ""))
                topic_versions.setdefault(str(row["topic_id"] or ""), []).append(snapshot)
                counts["versions"] += 1

    for (user_id, cube_id, topic_key), rows in scope_topics.items():
        rows.sort(
            key=lambda item: str(
                item.get("updated_at") or item.get("created_at") or item.get("topic_date") or ""
            )
        )
        latest = rows[-1]
        scope = state["scopes"][TopicStore._scope_key(user_id, cube_id)]
        evidence = []
        for tags in scope["tags"].values():
            evidence.extend(
                TagEvidence(**item) for item in tags if item.get("topic_key") == topic_key
            )
        metrics = compute_candidate_metrics(evidence)
        score_breakdown = (
            metrics.score_breakdown if evidence else _legacy_score_breakdown(latest)  # type: ignore[arg-type]
        )
        candidate_reasons = list(metrics.candidate_reasons)
        if not candidate_reasons:
            candidate_reasons = ["由旧版每日 Topic 数据迁移，等待新证据刷新候选依据"]
        last_evidence_at = metrics.latest_evidence_at or str(
            latest.get("updated_at") or latest.get("created_at") or _now_iso()
        )
        first_seen_at = min(
            str(item.get("created_at") or item.get("updated_at") or last_evidence_at)
            for item in rows
        )
        imported_versions = []
        for topic_id in topic_ids_by_key[(user_id, cube_id, topic_key)]:
            imported_versions.extend(topic_versions.get(topic_id, []))
        imported_versions.sort(
            key=lambda item: (int(item.get("version", 0)), item.get("updated_at", ""))
        )
        reason_evidence = _legacy_json(latest.get("reason_evidence_json"), [])
        supporting_ids = _legacy_json(latest.get("supporting_memory_ids_json"), [])
        if not isinstance(reason_evidence, list):
            reason_evidence = []
        if not isinstance(supporting_ids, list):
            supporting_ids = []
        topic_id = str(latest.get("topic_id") or f"topic-{uuid.uuid4().hex}")
        scope["topics"][topic_key] = {
            "topic_id": topic_id,
            "user_id": user_id,
            "cube_id": cube_id,
            "topic_key": topic_key,
            "topic_text": str(latest.get("topic_text") or topic_key),
            "reason_summary": str(latest.get("reason_summary") or "由旧版 Topic 数据迁移"),
            "reason_evidence": reason_evidence,
            "supporting_memory_ids": metrics.supporting_memory_ids or supporting_ids,
            "candidate_reasons": candidate_reasons,
            "score_breakdown": score_breakdown,
            "rank_score": float(score_breakdown["rank_score"]),
            "progress_status": str(latest.get("progress_status") or metrics.progress_status),
            "lifecycle_status": str(latest.get("lifecycle_status") or "active"),
            "topic_date": _topic_date(last_evidence_at),
            "first_seen_at": first_seen_at,
            "last_evidence_at": last_evidence_at,
            "version": int(latest.get("version") or 1),
            "updated_at": str(latest.get("updated_at") or last_evidence_at),
            "versions": imported_versions[-20:],
        }
        counts["topics"] += 1

    for scope in state["scopes"].values():
        eligible = [
            item
            for item in scope["topics"].values()
            if item.get("lifecycle_status") in {"active", "suppressed"}
        ]
        eligible.sort(
            key=lambda item: (
                -float(item.get("rank_score", 0.0)),
                -_datetime_sort_value(item.get("last_evidence_at")),
            )
        )
        active_ids = {item["topic_id"] for item in eligible[:15]}
        for item in eligible:
            item["lifecycle_status"] = "active" if item["topic_id"] in active_ids else "suppressed"

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        validated = json.loads(temporary.read_text(encoding="utf-8"))
        if not isinstance(validated, dict) or validated.get("schema_version") != 1:
            raise ValueError("迁移结果不是有效的 Topic JSON 状态文件")
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()

    return {**counts, "state_path": str(destination)}


class MemOSMemoryClient:
    def __init__(self, base_url: str, timeout: int = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def get_by_ids(self, memory_ids: list[str]) -> list[dict[str, Any]]:
        if not memory_ids:
            return []
        request = urllib.request.Request(
            f"{self.base_url}/product/get_memory_by_ids",
            data=json.dumps(memory_ids).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"读取 MemOS 记忆失败：{exc}") from exc
        memories = (payload.get("data") or {}).get("memories", [])
        result = [item for item in memories if isinstance(item, dict)]

        # Some MemOS graph backends currently miss records in the batch route
        # even though the single-memory route can retrieve the same IDs.
        returned_ids = {_memory_id(item) for item in result}
        for memory_id in memory_ids:
            if memory_id in returned_ids:
                continue
            encoded_id = urllib.parse.quote(memory_id, safe="")
            single_request = urllib.request.Request(
                f"{self.base_url}/product/get_memory/{encoded_id}",
                headers={"Accept": "application/json"},
                method="GET",
            )
            try:
                with self.opener.open(single_request, timeout=self.timeout) as response:
                    single_payload = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    continue
                raise RuntimeError(f"读取 MemOS 记忆 {memory_id} 失败：{exc}") from exc
            except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"读取 MemOS 记忆 {memory_id} 失败：{exc}") from exc
            memory = single_payload.get("data") if isinstance(single_payload, dict) else None
            if isinstance(memory, dict) and _memory_id(memory) == memory_id:
                result.append(memory)
                returned_ids.add(memory_id)
        return result

    def list_memories(self, *, user_id: str, cube_id: str) -> list[dict[str, Any]]:
        """Read all text memories visible to one Topic scope from the dashboard route."""
        request = urllib.request.Request(
            f"{self.base_url}/product/get_memory_dashboard",
            data=json.dumps(
                {
                    "mem_cube_id": cube_id,
                    "user_id": user_id,
                    "include_preference": False,
                    "include_tool_memory": False,
                    "include_skill_memory": False,
                    "page": None,
                    "page_size": None,
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"读取 MemOS 记忆列表失败：{exc}") from exc
        if not isinstance(payload, dict) or payload.get("code", 200) != 200:
            raise RuntimeError("读取 MemOS 记忆列表失败：服务返回了错误响应")
        groups = (payload.get("data") or {}).get("text_mem", [])
        return [
            memory
            for group in groups
            if isinstance(group, dict)
            for memory in group.get("memories", [])
            if isinstance(memory, dict)
        ]


@dataclass(frozen=True)
class TopicModelConfig:
    api_base: str
    api_key: str
    model: str
    extra_body: dict[str, Any]

    @classmethod
    def from_env(cls) -> TopicModelConfig:
        direct_base = os.getenv("TOPIC_API_BASE", "").strip()
        direct_key = os.getenv("TOPIC_API_KEY", "").strip()
        direct_model = os.getenv("TOPIC_MODEL", "").strip()
        if direct_base and direct_key and direct_model:
            return cls(direct_base, direct_key, direct_model, {})

        raw = os.getenv("CHAT_MODEL_LIST", "").strip()
        try:
            configs = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "Topic 模型未配置。请设置 TOPIC_API_BASE、TOPIC_API_KEY、TOPIC_MODEL，"
                "或提供有效的 CHAT_MODEL_LIST。"
            ) from exc
        if not isinstance(configs, list) or not configs or not isinstance(configs[0], dict):
            raise RuntimeError("CHAT_MODEL_LIST 中没有可用模型配置。")
        config = configs[0]
        return cls(
            str(config.get("api_base", "")).rstrip("/"),
            str(config.get("api_key", "")),
            str(config.get("model_name_or_path", "")),
            dict(config.get("extra_body") or {}),
        )


class TopicLLM:
    def __init__(self, config: TopicModelConfig, timeout: int = 180) -> None:
        self.config = config
        self.timeout = timeout

    def _complete(self, system_prompt: str, user_content: str) -> dict[str, Any]:
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.1,
            **self.config.extra_body,
        }
        request = urllib.request.Request(
            f"{self.config.api_base}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
            content = result["choices"][0]["message"]["content"]
        except (KeyError, OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Topic 模型调用失败：{exc}") from exc
        return _parse_json_object(str(content))

    def analyze_memory(
        self,
        memory: dict[str, Any],
        catalog: list[dict[str, str]],
    ) -> dict[str, Any]:
        content = {
            "已有标签目录": catalog,
            "记忆": {
                "memory_id": _memory_id(memory),
                "memory": _memory_text(memory),
                "metadata": memory.get("metadata", {}),
            },
        }
        return self._complete(TAG_PROMPT, json.dumps(content, ensure_ascii=False, indent=2))

    def extract_tags(
        self,
        memory: dict[str, Any],
        catalog: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Compatibility alias for callers from the previous Topic version."""
        return self.analyze_memory(memory, catalog)

    def group_memories(
        self,
        *,
        memories: list[dict[str, Any]],
        assessments: list[dict[str, Any]],
        tags: list[dict[str, Any]],
    ) -> dict[str, Any]:
        content = {
            "候选记忆": [
                {
                    "memory_id": _memory_id(item),
                    "memory": _memory_text(item),
                    "metadata": item.get("metadata", {}),
                }
                for item in memories
            ],
            "单条记忆判断": assessments,
            "候选标签依据": tags,
        }
        return self._complete(GROUP_PROMPT, json.dumps(content, ensure_ascii=False, indent=2))

    def generate_topic(
        self,
        *,
        topic_key: str,
        evidence: list[TagEvidence],
        memories: list[dict[str, Any]],
        metrics: CandidateMetrics,
    ) -> dict[str, Any]:
        content = {
            "topic_key": topic_key,
            "标签命中依据": [asdict(item) for item in evidence],
            "程序计算的候选原因": metrics.candidate_reasons,
            "程序计算的分数明细": metrics.score_breakdown,
            "全部相关记忆": [
                {"memory_id": _memory_id(item), "memory": _memory_text(item)} for item in memories
            ],
        }
        return self._complete(TOPIC_PROMPT, json.dumps(content, ensure_ascii=False, indent=2))


def _memory_id(memory: dict[str, Any]) -> str:
    return str(memory.get("id") or memory.get("memory_id") or "").strip()


def _memory_text(memory: dict[str, Any]) -> str:
    return str(memory.get("memory") or memory.get("value") or "").strip()


def _memory_info(memory: dict[str, Any]) -> dict[str, Any]:
    metadata = memory.get("metadata")
    if not isinstance(metadata, dict):
        return {}
    info = metadata.get("info")
    if isinstance(info, dict):
        return dict(info)
    # Older MemOS graph records expose custom info as flat metadata. Support
    # those records while new writes keep the logical info object intact.
    return {
        key: metadata[key]
        for key in (
            "record_type",
            "event_group_id",
            "series_id",
            "event_type",
            "event_status",
            "event_time",
            "event_start_at",
            "event_end_at",
            "source_recorded_at",
            "source_type",
        )
        if key in metadata
    }


def _is_active_event_memory(memory: dict[str, Any]) -> bool:
    metadata = memory.get("metadata")
    status = metadata.get("status", "activated") if isinstance(metadata, dict) else "activated"
    return status == "activated" and _memory_info(memory).get("record_type") == "event"


def _memory_event_time(memory: dict[str, Any]) -> str | None:
    info = _memory_info(memory)
    value = info.get("event_time") or info.get("event_start_at") or info.get("event_end_at")
    return str(value) if value else None


def _memory_urgency_points(memory: dict[str, Any], now: datetime) -> float:
    info = _memory_info(memory)
    if str(info.get("event_status") or "uncertain").lower() in {"completed", "cancelled"}:
        return 0.0
    event_time = _parse_datetime(_memory_event_time(memory))
    if event_time is None:
        return 0.0
    reference_now = now
    if reference_now.tzinfo is None:
        reference_now = reference_now.replace(tzinfo=timezone.utc)
    hours = (event_time.astimezone(reference_now.tzinfo) - reference_now).total_seconds() / 3600
    if hours < -24:
        return 0.0
    if hours <= 24:
        return 20.0
    if hours <= 72:
        return 16.0
    if hours <= 24 * 7:
        return 12.0
    if hours <= 24 * 30:
        return 6.0
    return 2.0


def _memory_effort_points(memory: dict[str, Any]) -> float:
    info = _memory_info(memory)
    duration_ms = info.get("duration_ms")
    duration_minutes = info.get("duration_minutes")
    try:
        minutes = (
            float(duration_ms) / 60_000 if duration_ms is not None else float(duration_minutes or 0)
        )
    except (TypeError, ValueError):
        minutes = 0.0
    if minutes < 5:
        return 0.0
    if minutes < 20:
        return 1.0
    if minutes < 60:
        return 3.0
    return 5.0


def parse_memory_assessment(
    memory: dict[str, Any],
    raw: dict[str, Any],
    *,
    now: datetime | None = None,
) -> MemoryAssessment:
    """Validate model judgements and calculate one inspectable memory score."""
    memory_id = _memory_id(memory)
    if not memory_id:
        raise ValueError("Topic 不能评估没有 memory_id 的记忆")
    assessment = raw.get("assessment")
    if not isinstance(assessment, dict):
        raise TypeError("Topic 模型结果必须包含 assessment 对象")
    if not isinstance(assessment.get("eligible"), bool):
        raise TypeError("assessment.eligible 必须是布尔值")

    def validated_choice(field: str, choices: dict[str, float]) -> str:
        value = str(assessment.get(field) or "").strip().lower()
        if value not in choices:
            raise ValueError(f"不支持的 assessment.{field}：{value or '空值'}")
        return value

    agency = validated_choice("agency", AGENCY_POINTS)
    action_requirement = validated_choice("action_requirement", ACTION_POINTS)
    impact = validated_choice("impact", IMPACT_POINTS)
    explicit_priority = validated_choice("explicit_priority", PRIORITY_POINTS)
    effort = validated_choice("effort", EFFORT_POINTS)
    confidence = validated_choice("confidence", CONFIDENCE_FACTORS)
    eligible = assessment["eligible"] is True

    reference_now = now or datetime.now().astimezone()
    agency_points = AGENCY_POINTS[agency]
    action_points = ACTION_POINTS[action_requirement]
    urgency_points = _memory_urgency_points(memory, reference_now)
    impact_points = IMPACT_POINTS[impact]
    priority_points = PRIORITY_POINTS[explicit_priority]
    effort_points = max(EFFORT_POINTS[effort], _memory_effort_points(memory))
    confidence_factor = CONFIDENCE_FACTORS[confidence]
    raw_score = (
        agency_points
        + action_points
        + urgency_points
        + impact_points
        + priority_points
        + effort_points
    )
    score = round(min(100.0, raw_score) * confidence_factor, 2) if eligible else 0.0

    reasons_raw = assessment.get("reasons")
    if not isinstance(reasons_raw, dict):
        raise TypeError("assessment.reasons 必须是对象")
    reasons = {str(key): str(value) for key, value in reasons_raw.items()}
    return MemoryAssessment(
        memory_id=memory_id,
        eligible=eligible,
        agency=agency,
        action_requirement=action_requirement,
        impact=impact,
        explicit_priority=explicit_priority,
        confidence=confidence,
        score=score,
        score_breakdown={
            "agency_points": agency_points,
            "action_points": action_points,
            "urgency_points": urgency_points,
            "impact_points": impact_points,
            "priority_points": priority_points,
            "effort_points": effort_points,
            "confidence_factor": confidence_factor,
            "memory_score": score,
        },
        reasons=reasons,
        effort=effort,
        selection_version=TOPIC_SELECTION_VERSION,
    )


def _normalized_memory_text(memory: dict[str, Any]) -> str:
    return " ".join(_memory_text(memory).casefold().split())


def _refresh_memory_assessment_score(
    memory: dict[str, Any],
    assessment: MemoryAssessment,
    now: datetime,
) -> MemoryAssessment:
    """Recalculate deterministic time-sensitive points from saved model judgements."""
    if not assessment.eligible:
        return assessment
    breakdown = dict(assessment.score_breakdown)
    confidence_factor = float(
        breakdown.get("confidence_factor", CONFIDENCE_FACTORS.get(assessment.confidence, 0.5))
    )
    old_urgency = float(breakdown.get("urgency_points", 0.0))
    fixed_point_keys = (
        "agency_points",
        "action_points",
        "impact_points",
        "priority_points",
        "effort_points",
    )
    if all(key in breakdown for key in fixed_point_keys):
        fixed_points = sum(float(breakdown[key]) for key in fixed_point_keys)
    elif confidence_factor > 0:
        fixed_points = max(0.0, assessment.score / confidence_factor - old_urgency)
    else:
        fixed_points = 0.0
    urgency_points = _memory_urgency_points(memory, now)
    score = round(min(100.0, fixed_points + urgency_points) * confidence_factor, 2)
    breakdown.update(
        {
            "urgency_points": urgency_points,
            "confidence_factor": confidence_factor,
            "memory_score": score,
        }
    )
    return replace(
        assessment,
        score=score,
        score_breakdown=breakdown,
    )


def compute_topic_metrics(
    *,
    assessments: list[MemoryAssessment],
    memories: list[dict[str, Any]],
    now: datetime | None = None,
    threshold: float = TOPIC_SCORE_THRESHOLD,
) -> CandidateMetrics:
    """Score one confirmed memory group using strongest + half supporting evidence."""
    memory_by_id = {_memory_id(memory): memory for memory in memories}
    reference_now = now or datetime.now().astimezone()
    valid = [
        _refresh_memory_assessment_score(memory_by_id[item.memory_id], item, reference_now)
        for item in assessments
        if item.eligible and item.memory_id in memory_by_id
    ]
    if not valid:
        return CandidateMetrics(
            evidence_count=0,
            relationship_vote_sum=0.0,
            qualifies=False,
            supporting_memory_ids=[],
            latest_evidence_at=None,
            score_breakdown={
                "strongest_memory_score": 0.0,
                "supporting_memory_points": 0.0,
                "duplicate_memory_count": 0,
                "counted_memory_ids": [],
                "base_score": 0.0,
                "recency_factor": 0.25,
                "rank_score": 0.0,
            },
            candidate_reasons=[],
        )

    best_by_text: dict[str, MemoryAssessment] = {}
    for item in valid:
        text_key = _normalized_memory_text(memory_by_id[item.memory_id]) or item.memory_id
        current = best_by_text.get(text_key)
        if current is None or item.score > current.score:
            best_by_text[text_key] = item
    counted = sorted(best_by_text.values(), key=lambda item: item.score, reverse=True)
    scores = [item.score for item in counted]
    strongest = scores[0]
    supporting_points = round(sum(scores[1:]) * TOPIC_SUPPORTING_WEIGHT, 2)
    base_score = round(min(100.0, strongest + supporting_points), 2)

    observed = [
        parsed
        for item in valid
        if (parsed := _parse_datetime(_memory_observed_at(memory_by_id[item.memory_id])))
    ]
    latest = max(observed) if observed else None
    recency_factor = _recency_factor(latest, reference_now)
    rank_score = calculate_rank_score(base_score=base_score, recency_factor=recency_factor)
    status_observations = [
        (
            _parse_datetime(_memory_observed_at(memory_by_id[item.memory_id])),
            str(_memory_info(memory_by_id[item.memory_id]).get("event_status") or "uncertain"),
        )
        for item in valid
    ]
    dated_statuses = [item for item in status_observations if item[0] is not None]
    progress_status = (
        max(dated_statuses, key=lambda item: item[0])[1]
        if dated_statuses
        else status_observations[-1][1]
    )
    candidate_reasons = []
    if strongest >= threshold:
        candidate_reasons.append("有一条记忆单独达到 Topic 晋升门槛")
    if len(counted) > 1 and base_score >= threshold:
        candidate_reasons.append(
            f"有 {len(counted)} 条非重复记忆共同支持该 Topic，辅助记忆按半权计分"
        )
    return CandidateMetrics(
        evidence_count=len(counted),
        relationship_vote_sum=float(len(counted)),
        qualifies=base_score >= threshold,
        supporting_memory_ids=[item.memory_id for item in valid],
        latest_evidence_at=latest.isoformat() if latest else None,
        score_breakdown={
            "strongest_memory_score": strongest,
            "supporting_memory_points": supporting_points,
            "duplicate_memory_count": len(valid) - len(counted),
            "counted_memory_ids": [item.memory_id for item in counted],
            "base_score": base_score,
            "recency_factor": recency_factor,
            "rank_score": rank_score,
            "memory_scores": {item.memory_id: item.score for item in valid},
        },
        candidate_reasons=candidate_reasons,
        progress_status=progress_status,
    )


def _memory_observed_at(memory: dict[str, Any]) -> str:
    metadata = memory.get("metadata") if isinstance(memory.get("metadata"), dict) else {}
    info = _memory_info(memory)
    return str(
        info.get("source_recorded_at")
        or metadata.get("created_at")
        or memory.get("created_at")
        or _now_iso()
    )


def _topic_date(observed_at: str) -> str:
    parsed = _parse_datetime(observed_at)
    return (parsed or datetime.now().astimezone()).astimezone().date().isoformat()


def _evidence_unit(memory: dict[str, Any], topic_key: str, observed_at: str) -> str:
    info = _memory_info(memory)
    stable_group = info.get("event_group_id") or info.get("series_id")
    if stable_group:
        return str(stable_group)
    source_type = str(info.get("source_type", ""))
    if source_type in {"local_image", "mixed_media", "mixed_markdown", "oss_video"}:
        parsed = _parse_datetime(observed_at)
        if parsed:
            minute_bucket = 0 if parsed.minute < 30 else 30
            return f"continuous:{topic_key}:{parsed:%Y-%m-%dT%H}:{minute_bucket:02d}"
    return _memory_id(memory)


def parse_tag_evidence(memory: dict[str, Any], raw: dict[str, Any]) -> list[TagEvidence]:
    memory_id = _memory_id(memory)
    observed_at = _memory_observed_at(memory)
    tags = raw.get("tags")
    if not memory_id:
        raise ValueError("Topic 不能标注没有 memory_id 的记忆")
    if not isinstance(tags, list):
        raise TypeError("Topic 模型结果必须包含 tags 列表")
    result = []
    for item in tags[:5]:
        if not isinstance(item, dict):
            raise TypeError("tags 的每一项必须是对象")
        topic_key = str(item.get("topic_key", "")).strip().lower().replace(" ", "_")
        tag_name = str(item.get("tag_name", "")).strip()
        reason = str(item.get("reason", "")).strip()
        if not topic_key or not tag_name or not reason:
            raise ValueError("每个标签必须包含 topic_key、tag_name 和 reason")
        relationship = str(item.get("relationship", "")).strip().lower()
        if relationship not in RELATIONSHIP_WEIGHTS:
            raise ValueError(f"不支持的标签 relationship：{relationship or '空值'}")
        initiative_value = item.get("initiative_type")
        if not str(initiative_value or "").strip():
            assessment = raw.get("assessment")
            if isinstance(assessment, dict):
                initiative_value = assessment.get("agency")
        initiative_type = str(initiative_value or "").strip().lower()
        if initiative_type not in INITIATIVE_WEIGHTS:
            raise ValueError(f"不支持的标签 initiative_type：{initiative_type or '空值'}")
        info = _memory_info(memory)
        event_time_raw = (
            info.get("event_time") or info.get("event_start_at") or info.get("event_end_at")
        )
        event_status = str(info.get("event_status") or "uncertain").strip().lower()
        if event_status not in STATUS_WEIGHTS:
            event_status = "uncertain"
        result.append(
            TagEvidence(
                memory_id=memory_id,
                topic_key=topic_key,
                tag_name=tag_name,
                relationship=relationship,
                initiative_type=initiative_type,
                reason=reason,
                evidence_unit=_evidence_unit(memory, topic_key, observed_at),
                observed_at=observed_at,
                event_time=str(event_time_raw) if event_time_raw else None,
                event_status=event_status,
            )
        )
    return result


def parse_memory_groups(raw: dict[str, Any], allowed_memory_ids: list[str]) -> list[MemoryGroup]:
    """Validate the model split while keeping every input memory exactly once."""
    allowed = set(allowed_memory_ids)
    groups_raw = raw.get("groups")
    if not isinstance(groups_raw, list):
        raise TypeError("Topic 分组结果必须包含 groups 列表")

    result: list[MemoryGroup] = []
    seen: set[str] = set()
    for item in groups_raw:
        if not isinstance(item, dict):
            raise TypeError("Topic 分组的每一项必须是对象")
        raw_ids = item.get("memory_ids")
        if not isinstance(raw_ids, list) or not raw_ids:
            raise ValueError("Topic 分组必须包含非空 memory_ids")
        memory_ids = [str(value).strip() for value in raw_ids if str(value).strip()]
        if len(memory_ids) != len(set(memory_ids)):
            raise ValueError("Topic 分组在同一小组中重复使用了 memory_id")
        if not memory_ids:
            raise ValueError("Topic 分组必须包含有效 memory_id")
        unknown = set(memory_ids) - allowed
        if unknown:
            raise ValueError("Topic 分组引用了未知记忆：" + "、".join(sorted(unknown)))
        repeated = set(memory_ids) & seen
        if repeated:
            raise ValueError("Topic 分组重复使用了记忆：" + "、".join(sorted(repeated)))
        topic_kind = str(item.get("topic_kind") or "event").strip().lower()
        if topic_kind not in {"event", "pattern"}:
            raise ValueError(f"不支持的 topic_kind：{topic_kind}")
        reason = str(item.get("reason") or "").strip()
        if not reason:
            raise ValueError("Topic 分组必须说明 reason")
        result.append(MemoryGroup(memory_ids=memory_ids, topic_kind=topic_kind, reason=reason))
        seen.update(memory_ids)

    # A model omission must never make a memory disappear. Keep it as a safe
    # singleton instead of guessing which group it belongs to.
    for memory_id in allowed_memory_ids:
        if memory_id not in seen:
            result.append(
                MemoryGroup(
                    memory_ids=[memory_id],
                    topic_kind="event",
                    reason="模型未明确归组，按单条事件独立保留",
                )
            )
    return result


def build_candidate_components(
    assessments: dict[str, MemoryAssessment],
    tags_by_memory: dict[str, list[TagEvidence]],
    memory_by_id: dict[str, dict[str, Any]],
) -> list[list[str]]:
    """Use shared tags or exact text duplicates to make small grouping candidates."""
    memory_ids = sorted(
        memory_id for memory_id, assessment in assessments.items() if assessment.eligible
    )
    parent = {memory_id: memory_id for memory_id in memory_ids}

    def find(memory_id: str) -> str:
        while parent[memory_id] != memory_id:
            parent[memory_id] = parent[parent[memory_id]]
            memory_id = parent[memory_id]
        return memory_id

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    def union_all(related_memory_ids: list[str]) -> None:
        if len(related_memory_ids) < 2:
            return
        first_memory_id = related_memory_ids[0]
        for memory_id in related_memory_ids[1:]:
            union(first_memory_id, memory_id)

    ids_by_tag: dict[str, list[str]] = {}
    for memory_id in memory_ids:
        for item in tags_by_memory.get(memory_id, []):
            if item.relationship == "weak":
                continue
            ids_by_tag.setdefault(item.topic_key, []).append(memory_id)
    for tagged_ids in ids_by_tag.values():
        union_all(tagged_ids)

    ids_by_text: dict[str, list[str]] = {}
    for memory_id in memory_ids:
        text_key = _normalized_memory_text(memory_by_id[memory_id])
        if text_key:
            ids_by_text.setdefault(text_key, []).append(memory_id)
    for duplicate_ids in ids_by_text.values():
        union_all(duplicate_ids)

    components: dict[str, list[str]] = {}
    for memory_id in memory_ids:
        components.setdefault(find(memory_id), []).append(memory_id)
    return sorted(components.values(), key=lambda item: (item[0], len(item)))


def _assessment_judgements(
    assessment: MemoryAssessment,
) -> dict[str, Any]:
    return {
        "agency": assessment.agency,
        "action_requirement": assessment.action_requirement,
        "impact": assessment.impact,
        "explicit_priority": assessment.explicit_priority,
        "effort": assessment.effort,
        "confidence": assessment.confidence,
        "reasons": assessment.reasons,
    }


def _selection_fingerprint(
    memory_ids: list[str],
    memory_by_id: dict[str, dict[str, Any]],
    assessments_by_id: dict[str, MemoryAssessment],
    tags_by_memory: dict[str, list[TagEvidence]],
    *,
    topic_kind: str | None = None,
    grouping_reason: str | None = None,
) -> str:
    payload = {
        "memory_ids": sorted(memory_ids),
        "memories": [
            {
                "memory_id": memory_id,
                "memory": _memory_text(memory_by_id[memory_id]),
                "metadata": memory_by_id[memory_id].get("metadata", {}),
                "assessment": {
                    "eligible": assessments_by_id[memory_id].eligible,
                    **_assessment_judgements(assessments_by_id[memory_id]),
                },
                "tags": [asdict(item) for item in tags_by_memory.get(memory_id, [])],
            }
            for memory_id in sorted(memory_ids)
        ],
        "topic_kind": topic_kind,
        "grouping_reason": grouping_reason,
        "selection_version": TOPIC_SELECTION_VERSION,
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return uuid.uuid5(uuid.NAMESPACE_URL, serialized).hex


class TopicProcessor:
    def __init__(
        self,
        *,
        store: TopicStore,
        memos_client: MemOSMemoryClient,
        llm: TopicLLM,
        daily_limit: int = 15,
    ) -> None:
        self.store = store
        self.memos_client = memos_client
        self.llm = llm
        self.daily_limit = daily_limit

    def process_added_response(
        self,
        *,
        response: Any,
        user_id: str,
        cube_id: str,
    ) -> int:
        """Fetch and process every memory ID returned by one successful add call."""
        summaries = extract_added_memories(response)
        memory_ids = [str(item["memory_id"]) for item in summaries]
        if not memory_ids:
            return 0
        return self.process_memory_ids(
            memory_ids=memory_ids,
            user_id=user_id,
            cube_id=cube_id,
        )

    def process_memory_ids(
        self,
        *,
        memory_ids: list[str],
        user_id: str,
        cube_id: str,
        retire_legacy_topics: bool = False,
    ) -> int:
        with self.store.transaction():
            return self._process_memory_ids(
                memory_ids=memory_ids,
                user_id=user_id,
                cube_id=cube_id,
                retire_legacy_topics=retire_legacy_topics,
            )

    def _process_memory_ids(
        self,
        *,
        memory_ids: list[str],
        user_id: str,
        cube_id: str,
        retire_legacy_topics: bool = False,
    ) -> int:
        memories = self.memos_client.get_by_ids(memory_ids)
        found_ids = {_memory_id(item) for item in memories}
        missing_ids = [memory_id for memory_id in memory_ids if memory_id not in found_ids]
        if missing_ids:
            raise RuntimeError("MemOS 暂时没有返回这些记忆：" + "、".join(missing_ids))

        catalog = self.store.tag_catalog(user_id, cube_id)
        reference_now = datetime.now().astimezone()
        processed_memories = 0
        for memory in memories:
            if not _is_active_event_memory(memory):
                continue
            analyze = getattr(self.llm, "analyze_memory", None)
            if not callable(analyze):
                analyze = self.llm.extract_tags
            raw_analysis = analyze(memory, catalog)
            assessment = parse_memory_assessment(memory, raw_analysis, now=reference_now)
            evidence = parse_tag_evidence(memory, raw_analysis)
            if not assessment.eligible:
                evidence = []
            self.store.save_memory(user_id=user_id, cube_id=cube_id, memory=memory)
            self.store.replace_assessment(
                user_id=user_id,
                cube_id=cube_id,
                assessment=assessment,
            )
            self.store.replace_tags(
                user_id=user_id,
                cube_id=cube_id,
                memory_id=_memory_id(memory),
                evidence=evidence,
            )
            catalog.extend(
                {"topic_key": item.topic_key, "tag_name": item.tag_name} for item in evidence
            )
            processed_memories += 1

        self._rebuild_topics(
            user_id=user_id,
            cube_id=cube_id,
            now=reference_now,
            retire_legacy_topics=retire_legacy_topics,
        )
        self.store.rebalance(
            user_id=user_id,
            cube_id=cube_id,
            limit=self.daily_limit,
        )
        return processed_memories

    def backfill(
        self,
        *,
        user_id: str,
        cube_id: str,
        ingest_batch_id: str | None = None,
    ) -> BackfillResult:
        """Select untagged event memories already stored in MemOS and finish Topic updates."""
        memories = self.memos_client.list_memories(user_id=user_id, cube_id=cube_id)
        selected_ids: list[str] = []
        seen_memory_ids: set[str] = set()
        for memory in memories:
            if not _is_active_event_memory(memory):
                continue
            if (
                ingest_batch_id is not None
                and _memory_info(memory).get("ingest_batch_id") != ingest_batch_id
            ):
                continue
            memory_id = _memory_id(memory)
            if memory_id and memory_id not in seen_memory_ids:
                selected_ids.append(memory_id)
                seen_memory_ids.add(memory_id)
        tagged_ids = self.store.tagged_memory_ids(user_id, cube_id)
        assessed_ids = self.store.assessed_memory_ids(user_id, cube_id)
        processed_ids = tagged_ids & assessed_ids
        pending_ids = [memory_id for memory_id in selected_ids if memory_id not in processed_ids]
        processed = self.process_memory_ids(
            memory_ids=pending_ids,
            user_id=user_id,
            cube_id=cube_id,
            retire_legacy_topics=ingest_batch_id is None,
        )
        return BackfillResult(
            selected_memories=len(selected_ids),
            pending_memories=len(pending_ids),
            processed_memories=processed,
        )

    def reconcile(self, batch_size: int = 100) -> int:
        with self.store.transaction():
            return self._reconcile(batch_size=batch_size)

    def _reconcile(self, batch_size: int = 100) -> int:
        """Remove Topic votes whose source memory is gone or no longer active in MemOS."""
        active_ids = self.store.active_memory_ids()
        affected: set[tuple[str, str, str]] = set()
        removed = 0
        for start in range(0, len(active_ids), batch_size):
            batch = active_ids[start : start + batch_size]
            memories = self.memos_client.get_by_ids(batch)
            active_returned = {
                _memory_id(memory)
                for memory in memories
                if (memory.get("metadata") if isinstance(memory.get("metadata"), dict) else {}).get(
                    "status", "activated"
                )
                == "activated"
            }
            for memory_id in batch:
                if memory_id not in active_returned:
                    affected.update(self.store.deactivate_memory(memory_id))
                    removed += 1

        for user_id, cube_id, topic_key in affected:
            topic = self.store.topic_record(
                user_id=user_id,
                cube_id=cube_id,
                topic_key=topic_key,
            )
            if topic is not None and topic.get("selection_version") != TOPIC_SELECTION_VERSION:
                self._recompute_topic(
                    user_id=user_id,
                    cube_id=cube_id,
                    topic_key=topic_key,
                    regenerate_summary=True,
                )

        reference_now = datetime.now().astimezone()
        affected_scopes = {(user_id, cube_id) for user_id, cube_id, _ in affected}
        affected_scopes.update(self.store.scopes_due_for_score_refresh(now=reference_now))
        for user_id, cube_id in affected_scopes:
            self._rebuild_topics(
                user_id=user_id,
                cube_id=cube_id,
                now=reference_now,
            )
            self.store.rebalance(
                user_id=user_id,
                cube_id=cube_id,
                limit=self.daily_limit,
            )
        return removed

    @staticmethod
    def _candidate_tag_keys(
        memory_ids: list[str],
        tags_by_memory: dict[str, list[TagEvidence]],
    ) -> list[str]:
        counts: dict[str, tuple[int, float]] = {}
        for memory_id in memory_ids:
            strongest_for_memory: dict[str, float] = {}
            for item in tags_by_memory.get(memory_id, []):
                weight = RELATIONSHIP_WEIGHTS.get(item.relationship, 0.0)
                strongest_for_memory[item.topic_key] = max(
                    strongest_for_memory.get(item.topic_key, 0.0),
                    weight,
                )
            for topic_key, weight in strongest_for_memory.items():
                count, total_weight = counts.get(topic_key, (0, 0.0))
                counts[topic_key] = (count + 1, total_weight + weight)
        return [
            topic_key
            for topic_key, _ in sorted(
                counts.items(),
                key=lambda item: (-item[1][0], -item[1][1], item[0]),
            )
        ]

    @staticmethod
    def _match_existing_topic(
        memory_ids: list[str],
        existing_topics: list[dict[str, Any]],
        used_topic_keys: set[str],
    ) -> dict[str, Any] | None:
        wanted = set(memory_ids)
        candidates = []
        for topic in existing_topics:
            topic_key = str(topic.get("topic_key") or "")
            if not topic_key or topic_key in used_topic_keys:
                continue
            previous = {str(value) for value in topic.get("supporting_memory_ids", [])}
            overlap = len(wanted & previous)
            if overlap == 0:
                continue
            union_size = len(wanted | previous) or 1
            candidates.append(
                (
                    overlap,
                    overlap / union_size,
                    int(topic.get("selection_version") == TOPIC_SELECTION_VERSION),
                    int(topic.get("lifecycle_status") in {"active", "suppressed"}),
                    str(topic.get("last_evidence_at") or ""),
                    topic,
                )
            )
        if not candidates:
            return None
        return max(candidates, key=lambda item: item[:5])[5]

    @staticmethod
    def _new_topic_key(
        candidate_tag_keys: list[str],
        memory_ids: list[str],
        occupied_topic_keys: set[str],
    ) -> str:
        base = candidate_tag_keys[0] if candidate_tag_keys else "standalone_event"
        if base not in occupied_topic_keys:
            return base
        suffix = uuid.uuid5(
            uuid.NAMESPACE_URL,
            "memos-topic:" + "|".join(sorted(memory_ids)),
        ).hex[:8]
        candidate = f"{base}__{suffix}"
        index = 2
        while candidate in occupied_topic_keys:
            candidate = f"{base}__{suffix}_{index}"
            index += 1
        return candidate

    def _rebuild_topics(
        self,
        *,
        user_id: str,
        cube_id: str,
        now: datetime,
        retire_legacy_topics: bool = False,
    ) -> None:
        memories, assessments_by_id, tags_by_memory = self.store.active_selection_data(
            user_id,
            cube_id,
        )
        memory_by_id = {_memory_id(memory): memory for memory in memories}
        groups: list[MemoryGroup] = []
        cached_groupings = self.store.grouping_cache(user_id, cube_id)
        refreshed_groupings: dict[str, list[MemoryGroup]] = {}
        for component in build_candidate_components(
            assessments_by_id,
            tags_by_memory,
            memory_by_id,
        ):
            if len(component) == 1:
                groups.append(
                    MemoryGroup(
                        memory_ids=component,
                        topic_kind="event",
                        reason="单条记忆独立参与 Topic 评估",
                    )
                )
                continue
            component_fingerprint = _selection_fingerprint(
                component,
                memory_by_id,
                assessments_by_id,
                tags_by_memory,
            )
            cached_groups = cached_groupings.get(component_fingerprint)
            if cached_groups is not None:
                validated_groups = parse_memory_groups(
                    {"groups": [asdict(group) for group in cached_groups]},
                    component,
                )
                groups.extend(validated_groups)
                refreshed_groupings[component_fingerprint] = validated_groups
                continue
            component_memories = [memory_by_id[memory_id] for memory_id in component]
            component_tags = [
                asdict(item)
                for memory_id in component
                for item in tags_by_memory.get(memory_id, [])
            ]
            component_assessments = [
                {
                    "memory_id": assessments_by_id[memory_id].memory_id,
                    **_assessment_judgements(assessments_by_id[memory_id]),
                }
                for memory_id in component
            ]
            raw_groups = self.llm.group_memories(
                memories=component_memories,
                assessments=component_assessments,
                tags=component_tags,
            )
            validated_groups = parse_memory_groups(raw_groups, component)
            groups.extend(validated_groups)
            refreshed_groupings[component_fingerprint] = validated_groups

        qualified: list[tuple[MemoryGroup, CandidateMetrics, list[str], list[TagEvidence]]] = []
        for group in groups:
            group_memories = [memory_by_id[memory_id] for memory_id in group.memory_ids]
            metrics = compute_topic_metrics(
                assessments=[assessments_by_id[memory_id] for memory_id in group.memory_ids],
                memories=group_memories,
                now=now,
            )
            if not metrics.qualifies:
                continue
            candidate_tag_keys = self._candidate_tag_keys(group.memory_ids, tags_by_memory)
            evidence = [
                item for memory_id in group.memory_ids for item in tags_by_memory.get(memory_id, [])
            ]
            qualified.append((group, metrics, candidate_tag_keys, evidence))

        qualified.sort(
            key=lambda item: (
                -float(item[1].score_breakdown.get("base_score", 0.0)),
                item[0].memory_ids,
            )
        )
        existing_topics = self.store.current_topic_records(
            user_id,
            cube_id,
            include_retired=True,
        )
        occupied_topic_keys = self.store.stored_topic_keys(user_id, cube_id)
        kept_topic_keys: set[str] = set()
        for group, metrics, candidate_tag_keys, evidence in qualified:
            existing = self._match_existing_topic(
                group.memory_ids,
                existing_topics,
                kept_topic_keys,
            )
            topic_key = (
                str(existing["topic_key"])
                if existing is not None
                else self._new_topic_key(
                    candidate_tag_keys,
                    group.memory_ids,
                    occupied_topic_keys | kept_topic_keys,
                )
            )
            group_fingerprint = _selection_fingerprint(
                group.memory_ids,
                memory_by_id,
                assessments_by_id,
                tags_by_memory,
                topic_kind=group.topic_kind,
                grouping_reason=group.reason,
            )
            same_members = existing is not None and {
                str(value) for value in existing.get("supporting_memory_ids", [])
            } == set(metrics.supporting_memory_ids)
            if (
                same_members
                and existing is not None
                and existing.get("selection_version") == TOPIC_SELECTION_VERSION
                and existing.get("selection_fingerprint") == group_fingerprint
                and existing.get("lifecycle_status") in {"active", "suppressed"}
            ):
                self.store.refresh_topic_metrics(
                    user_id=user_id,
                    cube_id=cube_id,
                    topic_key=topic_key,
                    metrics=metrics,
                    topic_kind=group.topic_kind,
                    grouping_reason=group.reason,
                    candidate_tag_keys=candidate_tag_keys,
                    selection_fingerprint=group_fingerprint,
                )
            else:
                group_memories = [memory_by_id[memory_id] for memory_id in group.memory_ids]
                raw_draft = self.llm.generate_topic(
                    topic_key=topic_key,
                    evidence=evidence,
                    memories=group_memories,
                    metrics=metrics,
                )
                draft = parse_topic_draft(
                    raw_draft,
                    set(metrics.supporting_memory_ids),
                    set(metrics.score_breakdown.get("counted_memory_ids", [])),
                )
                self.store.upsert_topic(
                    user_id=user_id,
                    cube_id=cube_id,
                    topic_key=topic_key,
                    draft=draft,
                    metrics=metrics,
                    topic_kind=group.topic_kind,
                    grouping_reason=group.reason,
                    candidate_tag_keys=candidate_tag_keys,
                    selection_fingerprint=group_fingerprint,
                )
            kept_topic_keys.add(topic_key)
            occupied_topic_keys.add(topic_key)

        self.store.retire_unmatched_topics(
            user_id=user_id,
            cube_id=cube_id,
            kept_topic_keys=kept_topic_keys,
            include_legacy=retire_legacy_topics,
        )
        self.store.replace_grouping_cache(
            user_id=user_id,
            cube_id=cube_id,
            groups_by_fingerprint=refreshed_groupings,
        )

    def _recompute_topic(
        self,
        *,
        user_id: str,
        cube_id: str,
        topic_key: str,
        regenerate_summary: bool,
        now: datetime | None = None,
    ) -> None:
        evidence = self.store.evidence_for_topic(
            user_id=user_id,
            cube_id=cube_id,
            topic_key=topic_key,
        )
        metrics = compute_candidate_metrics(evidence, now=now)
        if not metrics.qualifies:
            self.store.retire_topic(
                user_id=user_id,
                cube_id=cube_id,
                topic_key=topic_key,
            )
            return

        if self.store.has_topic(user_id, cube_id, topic_key) and not regenerate_summary:
            self.store.refresh_topic_metrics(
                user_id=user_id,
                cube_id=cube_id,
                topic_key=topic_key,
                metrics=metrics,
            )
            return

        memories = self.store.memories_by_ids(metrics.supporting_memory_ids)
        raw_draft = self.llm.generate_topic(
            topic_key=topic_key,
            evidence=evidence,
            memories=memories,
            metrics=metrics,
        )
        draft = parse_topic_draft(raw_draft, set(metrics.supporting_memory_ids))
        self.store.upsert_topic(
            user_id=user_id,
            cube_id=cube_id,
            topic_key=topic_key,
            draft=draft,
            metrics=metrics,
        )


def process_runtime_topics(
    *,
    base_url: str,
    user_id: str,
    cube_id: str,
    add_response: Any,
    daily_limit: int = 15,
) -> dict[str, Any]:
    """Process one add response immediately and return the rolling Topic seats."""
    _load_project_env()
    store = TopicStore(default_store_path())
    processor = TopicProcessor(
        store=store,
        memos_client=MemOSMemoryClient(base_url),
        llm=TopicLLM(TopicModelConfig.from_env()),
        daily_limit=max(1, daily_limit),
    )
    processed_memories = processor.process_added_response(
        response=add_response,
        user_id=user_id,
        cube_id=cube_id,
    )
    return {
        "processed_memories": processed_memories,
        "rolling_limit": max(1, daily_limit),
        "topics": store.list_topics(
            user_id=user_id,
            cube_id=cube_id,
        ),
    }


def reconcile_runtime_topics(*, base_url: str, daily_limit: int = 15) -> int:
    """Reconcile the external Topic snapshot with active memories in MemOS."""
    _load_project_env()
    processor = TopicProcessor(
        store=TopicStore(default_store_path()),
        memos_client=MemOSMemoryClient(base_url),
        llm=TopicLLM(TopicModelConfig.from_env()),
        daily_limit=max(1, daily_limit),
    )
    return processor.reconcile()


def _load_project_env() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MemOS 外部 Topic 处理器")
    parser.add_argument(
        "command",
        choices=["backfill", "run-once", "watch", "reconcile", "list", "migrate-legacy"],
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--state", "--db", dest="state", default=None)
    parser.add_argument("--daily-limit", type=int, default=15)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--user", default="default")
    parser.add_argument("--cube", default="default_cube")
    parser.add_argument("--date", default=datetime.now().astimezone().date().isoformat())
    parser.add_argument("--include-suppressed", action="store_true")
    parser.add_argument("--legacy-db", default=None)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--ingest-batch-id", default=None)
    return parser.parse_args()


def main() -> int:
    _load_project_env()
    args = parse_args()
    if args.command == "migrate-legacy":
        legacy_path = args.legacy_db or (
            Path(__file__).resolve().parents[1] / ".memos" / "topic" / "topics.db"
        )
        summary = migrate_legacy_sqlite(
            legacy_path,
            args.state or default_store_path(),
            overwrite=args.overwrite,
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    store = TopicStore(args.state or default_store_path())
    if args.command == "list":
        topics = store.list_topics(
            user_id=args.user,
            cube_id=args.cube,
            include_suppressed=args.include_suppressed,
        )
        print(json.dumps(topics, ensure_ascii=False, indent=2))
        return 0

    processor = TopicProcessor(
        store=store,
        memos_client=MemOSMemoryClient(args.base_url),
        llm=TopicLLM(TopicModelConfig.from_env()),
        daily_limit=max(1, args.daily_limit),
    )
    if args.command == "backfill":
        result = processor.backfill(
            user_id=args.user,
            cube_id=args.cube,
            ingest_batch_id=args.ingest_batch_id,
        )
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        return 0
    if args.command in {"run-once", "reconcile"}:
        print(f"已移除 {processor.reconcile()} 条失效记忆的 Topic 贡献。")
        return 0

    print("Topic 对账监视器已启动；新记忆仍由 Runtime 自动处理。按 Ctrl+C 停止。")
    try:
        while True:
            processor.reconcile()
            time.sleep(max(1.0, args.interval))
    except KeyboardInterrupt:
        print("\nTopic 处理器已停止。")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
