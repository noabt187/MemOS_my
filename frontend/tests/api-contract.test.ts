import assert from "node:assert/strict";
import test from "node:test";

import {
  ApiContractError,
  formatApiErrorDetail,
  parseAuthResult,
  parseChatResult,
  parseDashboard,
  parseDeleteMemoryResult,
  parseHealthResult,
  parseIngestionResult,
  parseMemoryList,
  parseMemoryResponse,
  parseMobileLoginResult,
  parseReconcileResult,
  parseSearchResult,
  parseSessionResult,
  parseTopicList,
  parseTopicSelectionTrace,
} from "../lib/api-contract.ts";
import { getTopicMemoryScoreState } from "../lib/topic-display.ts";

const memoryFixture = {
  id: "memory-1",
  title: "准备面试",
  content: "用户计划准备一次技术面试。",
  memory_type: "LongTermMemory",
  source: "text",
  category: "event",
  created_at: "2026-08-26T10:00:00+08:00",
  updated_at: null,
  tags: ["面试"],
};

const topicFixture = {
  id: "topic-1",
  key: "interview",
  title: "用户正在准备技术面试。",
  reason: "这条记忆本身具有较强主动性。",
  status: "active",
  progress: "planned",
  score: 88.2,
  supporting_memory_ids: ["memory-1", "memory-2"],
  evidence: [
    {
      memory_id: "memory-1",
      fact: "用户计划准备技术面试。",
      contribution: "主动计划。",
    },
  ],
  candidate_reasons: ["单条记忆超过晋升阈值"],
  score_breakdown: {
    strongest_memory_score: 69,
    supporting_memory_points: 29,
    duplicate_memory_count: 0,
    counted_memory_ids: ["memory-1", "memory-2"],
    base_score: 98,
    recency_factor: 0.9,
    rank_score: 88.2,
    memory_scores: { "memory-1": 69, "memory-2": 58 },
  },
  first_seen_at: "2026-08-24T20:52:00+08:00",
  last_evidence_at: "2026-08-24T20:52:00+08:00",
  version: 2,
  updated_at: "2026-08-26T10:36:00+08:00",
  versions: [],
};

test("parses the current Topic importance-score contract without inventing zero dimensions", function (): void {
  const result = parseTopicList({ total: 1, items: [topicFixture] });
  const breakdown = result.items[0].score_breakdown;

  assert.equal(breakdown.model, "memory_importance_v2");
  if (breakdown.model !== "memory_importance_v2") {
    assert.fail("expected v2 breakdown");
  }
  assert.equal(breakdown.strongest_memory_score, 69);
  assert.equal(breakdown.supporting_memory_points, 29);
  assert.deepEqual(breakdown.counted_memory_ids, ["memory-1", "memory-2"]);
  assert.deepEqual(breakdown.memory_scores, { "memory-1": 69, "memory-2": 58 });
});

test("rejects a malformed current Topic score instead of silently rendering false values", function (): void {
  const malformedTopic = structuredClone(topicFixture);
  malformedTopic.score_breakdown.counted_memory_ids = "memory-1" as unknown as string[];

  function parseMalformedTopic(): void {
    parseTopicList({ total: 1, items: [malformedTopic] });
  }

  function isCountedMemoryContractError(error: unknown): boolean {
    return error instanceof ApiContractError && /counted_memory_ids/.test(error.message);
  }

  assert.throws(parseMalformedTopic, isCountedMemoryContractError);
});

test("keeps legacy Topic scores explicitly separate from the current score model", function (): void {
  const legacyTopic = structuredClone(topicFixture);
  legacyTopic.score_breakdown = {
    base_score: 70,
    recency_factor: 1,
    rank_score: 70,
    evidence_points: 20,
    initiative_points: 15,
    urgency_points: 10,
    continuity_points: 5,
    status_points: 8,
  } as unknown as typeof topicFixture.score_breakdown;

  const breakdown = parseTopicList({ total: 1, items: [legacyTopic] }).items[0].score_breakdown;
  assert.equal(breakdown.model, "legacy_evidence_v1");
  if (breakdown.model !== "legacy_evidence_v1") {
    assert.fail("expected legacy breakdown");
  }
  assert.equal(breakdown.initiative_points, 15);
  assert.deepEqual(getTopicMemoryScoreState(breakdown, "memory-1"), {
    state: "unknown",
    label: "计分状态未知",
    description: "历史评分未提供单条记忆的计分状态。",
    score: undefined,
  });
});

test("parses a transparent Topic selection trace without recalculating scores", function (): void {
  const trace = parseTopicSelectionTrace({
    topic_id: "topic-1",
    topic_key: "interview",
    available: true,
    unavailable_reason: null,
    selection_version: 2,
    policy: {
      topic_threshold: 60,
      supporting_weight: 0.5,
      seat_limit: 15,
      memory_formula: "min(100, 维度分合计) × 置信系数",
      topic_formula: "最强单条 + 其他非重复记忆 × 0.5",
      rank_formula: "Topic 基础分 × 新鲜系数",
      rubric: [
        {
          key: "agency",
          title: "主动程度",
          score_unit: "points",
          options: [{ label: "acting", score_value: 19 }],
        },
      ],
    },
    grouping: {
      topic_kind: "event",
      reason: "同一次面试安排。",
      candidate_tag_keys: ["interview"],
      memory_ids: ["memory-1"],
    },
    decision: {
      qualifies: true,
      base_score: 80,
      recency_factor: 0.9,
      rank_score: 72,
      rank_position: 1,
      seat_status: "active",
      candidate_reasons: ["单条记忆达到门槛"],
    },
    memories: [
      {
        memory_id: "memory-1",
        text: "用户确认参加面试。",
        active: true,
        assessed_at: "2026-08-25T10:00:00+08:00",
        eligible: true,
        initial_score: 80,
        current_score: 80,
        counting_status: "counted",
        raw_points: 80,
        confidence_factor: 1,
        dimensions: [
          {
            key: "agency",
            title: "主动程度",
            label: "committed",
            score_value: 25,
            score_unit: "points",
            max_value: 25,
            source: "model",
            reason: "用户明确确认参加。",
          },
        ],
        tags: [
          {
            topic_key: "interview",
            tag_name: "面试",
            relationship: "direct",
            reason: "与面试直接相关。",
          },
        ],
      },
    ],
  });

  assert.equal(trace.memories[0].dimensions[0].label, "committed");
  assert.equal(trace.memories[0].dimensions[0].score_value, 25);
  if (!trace.available) {
    assert.fail("expected an available trace");
  }
  assert.equal(trace.decision.rank_score, 72);
});

test("keeps unavailable historical Topic traces explicit instead of inventing scores", function (): void {
  const trace = parseTopicSelectionTrace({
    topic_id: "topic-legacy",
    topic_key: "legacy_topic",
    available: false,
    unavailable_reason: "历史 Topic 未保存单条记忆初评过程。",
    selection_version: null,
    policy: null,
    grouping: null,
    decision: null,
    memories: [],
  });

  assert.equal(trace.available, false);
  if (trace.available) {
    assert.fail("expected an unavailable trace");
  }
  assert.equal(trace.policy, null);
  assert.equal(trace.unavailable_reason, "历史 Topic 未保存单条记忆初评过程。");
});

test("rejects incomplete Topic trace dimensions instead of defaulting their score", function (): void {
  const malformedTrace = {
    topic_id: "topic-1",
    topic_key: "interview",
    available: true,
    unavailable_reason: null,
    selection_version: 2,
    policy: {
      topic_threshold: 60,
      supporting_weight: 0.5,
      seat_limit: 15,
      memory_formula: "memory formula",
      topic_formula: "topic formula",
      rank_formula: "rank formula",
      rubric: [],
    },
    grouping: {
      topic_kind: "event",
      reason: "same event",
      candidate_tag_keys: [],
      memory_ids: ["memory-1"],
    },
    decision: {
      qualifies: true,
      base_score: 80,
      recency_factor: 1,
      rank_score: 80,
      rank_position: 1,
      seat_status: "active",
      candidate_reasons: [],
    },
    memories: [
      {
        memory_id: "memory-1",
        text: "用户准备面试。",
        active: true,
        assessed_at: null,
        eligible: true,
        initial_score: 80,
        current_score: 80,
        counting_status: "counted",
        raw_points: 80,
        confidence_factor: 1,
        dimensions: [
          {
            key: "agency",
            title: "主动程度",
            label: "acting",
            score_unit: "points",
            max_value: 25,
            source: "model",
            reason: "用户正在行动。",
          },
        ],
        tags: [],
      },
    ],
  };

  function parseMalformedTrace(): void {
    parseTopicSelectionTrace(malformedTrace);
  }

  function isDimensionScoreError(error: unknown): boolean {
    return error instanceof ApiContractError && /score_value/.test(error.message);
  }

  assert.throws(parseMalformedTrace, isDimensionScoreError);
});

test("parses all dashboard, ingestion, and search structures consumed by pages", function (): void {
  const dashboard = parseDashboard({
    backend_status: "online",
    service_version: "1.2.3",
    fetched_at: "2026-08-26T10:36:00+08:00",
    scope: { user_id: "default", cube_id: "default_cube" },
    counts: {
      memories: 1,
      preferences: 0,
      skills: 0,
      queue_total: 0,
      queue_running: 0,
      queue_waiting: 0,
      active_topics: 1,
    },
    topics: [topicFixture],
    memories: [memoryFixture],
  });
  assert.equal(dashboard.memories[0].id, "memory-1");

  const ingestion = parseIngestionResult({
    ok: true,
    memories_created: 1,
    topic: { processed_memories: 1, active_topics: 1, error: null },
  });
  assert.equal(ingestion.topic.active_topics, 1);

  const search = parseSearchResult({ results: [{ ...memoryFixture, score: 0.91 }], total: 1 });
  assert.equal(search.results[0].score, 0.91);
});

test("covers every other public application API response consumed by the frontend", function (): void {
  assert.deepEqual(parseAuthResult({ ok: true }), { ok: true });
  assert.deepEqual(parseSessionResult({ authenticated: true }), { authenticated: true });
  assert.deepEqual(
    parseMobileLoginResult({
      ok: true,
      token_type: "Bearer",
      session_token: "test-session-token",
      expires_in: 604800,
    }),
    {
      ok: true,
      token_type: "Bearer",
      session_token: "test-session-token",
      expires_in: 604800,
    },
  );

  const health = parseHealthResult({
    status: "healthy",
    dependencies: { memos: "online", topics: "online" },
    service_version: 2,
  });
  assert.equal(health.service_version, "2");

  const memoryList = parseMemoryList({
    scope: { user_id: "default", cube_id: "default_cube" },
    total: 1,
    items: [memoryFixture],
  });
  assert.equal(memoryList.items.length, 1);

  const detail = parseMemoryResponse({
    memory: {
      ...memoryFixture,
      confidence: 0.9,
      background: null,
      structured: { record_type: "event" },
    },
  });
  assert.equal(detail.memory.structured.record_type, "event");

  assert.equal(
    parseDeleteMemoryResult({
      ok: true,
      memory_id: "memory-1",
      topic_sync: "updated",
      removed_topic_memories: 1,
    }).topic_sync,
    "updated",
  );
  assert.equal(parseReconcileResult({ ok: true, removed_memories: 1 }).removed_memories, 1);
  assert.equal(
    parseChatResult({ response: "你正在准备面试。", session_id: "web-test" }).response,
    "你正在准备面试。",
  );
});

test("formats FastAPI validation arrays as readable text", function (): void {
  assert.equal(
    formatApiErrorDetail(
      {
        detail: [
          { loc: ["body", "query"], msg: "Field required", type: "missing" },
          { loc: ["body", "url"], msg: "Input should be a valid URL", type: "url_parsing" },
        ],
      },
      422,
    ),
    "query：Field required；url：Input should be a valid URL",
  );
});
