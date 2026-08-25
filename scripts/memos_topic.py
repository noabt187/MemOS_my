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

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TAG_PROMPT = """你是个人记忆主题证据分类器。

请只根据给出的单条记忆，判断它可以为哪些主题提供证据。标签不是最终 Topic，也不负责打分。

规则：
1. 提取 1 到 5 个真正相关的标签；无主题价值时可以返回空列表。
2. topic_key 必须简短、稳定，使用小写英文和下划线，例如 final_exam。
3. 优先复用已有标签目录，避免把“考试、考试周、准备考试”拆成多个标签。
4. relationship 只能是 direct、related、weak：直接证据、相关背景、弱关联。
5. initiative_type 只能是 initiated、acting、participated、observed：用户主动发起、正在行动、参与其中、仅被动看到。
6. reason 必须写出这条记忆支持该标签的具体事实，不能只说“与主题相关”。
7. 不得输出相关度、重要度、紧急度、执行度或任何数字分数。
8. 不得添加记忆中不存在的事实。
9. 只输出 JSON。

输出格式：
{
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
8. 排名分数已由程序根据证据数量、主动程度、事件时间、持续天数和状态算好；你不得重新打分。
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
    score_breakdown: dict[str, float]
    candidate_reasons: list[str]
    progress_status: str = "uncertain"


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
    "acting": 0.75,
    "participated": 0.4,
    "observed": 0.0,
}
STATUS_WEIGHTS = {
    "ongoing": 1.0,
    "planned": 0.7,
    "uncertain": 0.3,
    "completed": 0.2,
    "cancelled": 0.0,
}


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
        item.initiative_type == "initiated" and item.event_status in {"planned", "ongoing"}
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


def parse_topic_draft(raw: dict[str, Any], allowed_memory_ids: set[str]) -> TopicDraft:
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

    return TopicDraft(
        topic_text=topic_text,
        reason_summary=reason_summary,
        reason_evidence=reason_evidence,
    )


class TopicStore:
    """Small JSON snapshot for rolling Topics; MemOS remains the memory database."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        if not self.path.exists():
            self._write(self._empty_state())

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
        minimal_memory = {
            "id": memory_id,
            "memory": _memory_text(memory),
            "memory_type": memory.get("memory_type"),
            "metadata": {
                "status": original_metadata.get("status", "activated"),
                "created_at": original_metadata.get("created_at") or memory.get("created_at"),
                "info": _memory_info(memory),
            },
        }
        state = self._read()
        scope = self._scope(state, user_id, cube_id, create=True)
        assert scope is not None
        scope["memories"][memory_id] = {
            "memory": minimal_memory,
            "observed_at": _memory_observed_at(memory),
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

    def tag_catalog(self, user_id: str, cube_id: str, limit: int = 100) -> list[dict[str, str]]:
        state = self._read()
        scope = self._scope(state, user_id, cube_id)
        if scope is None:
            return []
        counts: dict[str, tuple[int, str]] = {}
        for memory_id, tags in scope["tags"].items():
            if not scope["memories"].get(memory_id, {}).get("active", False):
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
                str(item.get("last_evidence_at", "")),
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
                str(item.get("last_evidence_at", "")),
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
                str(item.get("last_evidence_at", "")),
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

    def extract_tags(self, memory: dict[str, Any], catalog: list[dict[str, str]]) -> dict[str, Any]:
        content = {
            "已有标签目录": catalog,
            "记忆": {
                "memory_id": _memory_id(memory),
                "memory": _memory_text(memory),
                "metadata": memory.get("metadata", {}),
            },
        }
        return self._complete(TAG_PROMPT, json.dumps(content, ensure_ascii=False, indent=2))

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
    if not memory_id or not isinstance(tags, list):
        return []
    result = []
    for item in tags[:5]:
        if not isinstance(item, dict):
            continue
        topic_key = str(item.get("topic_key", "")).strip().lower().replace(" ", "_")
        tag_name = str(item.get("tag_name", "")).strip()
        reason = str(item.get("reason", "")).strip()
        if not topic_key or not tag_name or not reason:
            continue
        relationship = str(item.get("relationship", "weak")).strip().lower()
        if relationship not in RELATIONSHIP_WEIGHTS:
            relationship = "weak"
        initiative_type = str(item.get("initiative_type", "observed")).strip().lower()
        if initiative_type not in INITIATIVE_WEIGHTS:
            initiative_type = "observed"
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
    ) -> int:
        memories = self.memos_client.get_by_ids(memory_ids)
        found_ids = {_memory_id(item) for item in memories}
        missing_ids = [memory_id for memory_id in memory_ids if memory_id not in found_ids]
        if missing_ids:
            raise RuntimeError("MemOS 暂时没有返回这些记忆：" + "、".join(missing_ids))

        affected: set[str] = set()
        catalog = self.store.tag_catalog(user_id, cube_id)
        for memory in memories:
            metadata = memory.get("metadata") if isinstance(memory.get("metadata"), dict) else {}
            if metadata.get("status", "activated") != "activated":
                continue
            if _memory_info(memory).get("record_type") != "event":
                continue
            self.store.save_memory(user_id=user_id, cube_id=cube_id, memory=memory)
            evidence = parse_tag_evidence(memory, self.llm.extract_tags(memory, catalog))
            self.store.replace_tags(
                user_id=user_id,
                cube_id=cube_id,
                memory_id=_memory_id(memory),
                evidence=evidence,
            )
            affected.update(item.topic_key for item in evidence)
            catalog.extend(
                {"topic_key": item.topic_key, "tag_name": item.tag_name} for item in evidence
            )

        reference_now = datetime.now().astimezone()
        for topic_key in self.store.topic_keys(user_id, cube_id):
            self._recompute_topic(
                user_id=user_id,
                cube_id=cube_id,
                topic_key=topic_key,
                regenerate_summary=topic_key in affected,
                now=reference_now,
            )
        self.store.rebalance(
            user_id=user_id,
            cube_id=cube_id,
            limit=self.daily_limit,
        )
        return len(memories)

    def backfill(
        self,
        *,
        user_id: str,
        cube_id: str,
        ingest_batch_id: str | None = None,
    ) -> BackfillResult:
        """Select untagged event memories already stored in MemOS and finish Topic updates."""
        memories = self.memos_client.list_memories(user_id=user_id, cube_id=cube_id)
        selected_ids = list(
            dict.fromkeys(
                memory_id
                for memory in memories
                if (
                    (
                        memory.get("metadata") if isinstance(memory.get("metadata"), dict) else {}
                    ).get("status", "activated")
                    == "activated"
                    and _memory_info(memory).get("record_type") == "event"
                    and (
                        ingest_batch_id is None
                        or _memory_info(memory).get("ingest_batch_id") == ingest_batch_id
                    )
                    and (memory_id := _memory_id(memory))
                )
            )
        )
        tagged_ids = self.store.tagged_memory_ids(user_id, cube_id)
        pending_ids = [memory_id for memory_id in selected_ids if memory_id not in tagged_ids]
        processed = self.process_memory_ids(
            memory_ids=pending_ids,
            user_id=user_id,
            cube_id=cube_id,
        )
        return BackfillResult(
            selected_memories=len(selected_ids),
            pending_memories=len(pending_ids),
            processed_memories=processed,
        )

    def reconcile(self, batch_size: int = 100) -> int:
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

        affected_scopes: set[tuple[str, str]] = set()
        for user_id, cube_id, topic_key in affected:
            self._recompute_topic(
                user_id=user_id,
                cube_id=cube_id,
                topic_key=topic_key,
                regenerate_summary=True,
            )
            affected_scopes.add((user_id, cube_id))
        for user_id, cube_id in affected_scopes:
            self.store.rebalance(
                user_id=user_id,
                cube_id=cube_id,
                limit=self.daily_limit,
            )
        return removed

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
