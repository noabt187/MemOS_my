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
} from "../lib/api-contract.ts";
import { getTopicMemoryScoreState } from "../lib/topic-display.ts";

const memory = {
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

const topic = {
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

test("parses the current Topic importance-score contract without inventing zero dimensions", () => {
  const result = parseTopicList({ total: 1, items: [topic] });
  const breakdown = result.items[0].score_breakdown;

  assert.equal(breakdown.model, "memory_importance_v2");
  if (breakdown.model !== "memory_importance_v2") assert.fail("expected v2 breakdown");
  assert.equal(breakdown.strongest_memory_score, 69);
  assert.equal(breakdown.supporting_memory_points, 29);
  assert.deepEqual(breakdown.counted_memory_ids, ["memory-1", "memory-2"]);
  assert.deepEqual(breakdown.memory_scores, { "memory-1": 69, "memory-2": 58 });
});

test("rejects a malformed current Topic score instead of silently rendering false values", () => {
  const malformed = structuredClone(topic);
  malformed.score_breakdown.counted_memory_ids = "memory-1" as unknown as string[];

  assert.throws(
    () => parseTopicList({ total: 1, items: [malformed] }),
    (error) => error instanceof ApiContractError && /counted_memory_ids/.test(error.message),
  );
});

test("keeps legacy Topic scores explicitly separate from the current score model", () => {
  const legacy = structuredClone(topic);
  legacy.score_breakdown = {
    base_score: 70,
    recency_factor: 1,
    rank_score: 70,
    evidence_points: 20,
    initiative_points: 15,
    urgency_points: 10,
    continuity_points: 5,
    status_points: 8,
  } as unknown as typeof topic.score_breakdown;

  const breakdown = parseTopicList({ total: 1, items: [legacy] }).items[0].score_breakdown;
  assert.equal(breakdown.model, "legacy_evidence_v1");
  if (breakdown.model !== "legacy_evidence_v1") assert.fail("expected legacy breakdown");
  assert.equal(breakdown.initiative_points, 15);
  assert.deepEqual(getTopicMemoryScoreState(breakdown, "memory-1"), {
    state: "unknown",
    label: "计分状态未知",
    description: "历史评分未提供单条记忆的计分状态。",
    score: undefined,
  });
});

test("parses all dashboard, ingestion, and search structures consumed by pages", () => {
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
    topics: [topic],
    memories: [memory],
  });
  assert.equal(dashboard.memories[0].id, "memory-1");

  const ingestion = parseIngestionResult({
    ok: true,
    memories_created: 1,
    topic: { processed_memories: 1, active_topics: 1, error: null },
  });
  assert.equal(ingestion.topic.active_topics, 1);

  const search = parseSearchResult({ results: [{ ...memory, score: 0.91 }], total: 1 });
  assert.equal(search.results[0].score, 0.91);
});

test("covers every other public application API response consumed by the frontend", () => {
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
    items: [memory],
  });
  assert.equal(memoryList.items.length, 1);

  const detail = parseMemoryResponse({
    memory: {
      ...memory,
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

test("formats FastAPI validation arrays as readable text", () => {
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
