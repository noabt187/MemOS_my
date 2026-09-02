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
  queue_rank: 1,
  candidate_source: null,
  attention_status: "open",
  importance_score: 88.2,
  approaching_bonus: 0,
  decay_penalty: 0,
  queue_score: 88.2,
  queue_score_breakdown: {
    importance_score: 88.2,
    approaching_bonus: 0,
    decay_penalty: 0,
    queue_score: 88.2,
  },
  core_entered_at: "2026-08-26T00:00:00+08:00",
  demoted_at: null,
  calculated_at: "2026-08-26T12:00:00+08:00",
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

function topicListFixture(items: Record<string, unknown>[] = [topicFixture]) {
  const coreCount = items.filter((item) => item.status === "active").length;
  const visibleCandidateCount = items.filter((item) => item.status === "suppressed").length;
  const candidatePoolTotal = visibleCandidateCount;
  return {
    total: items.length,
    returned: items.length,
    pool_total: coreCount + candidatePoolTotal,
    candidate_pool_total: candidatePoolTotal,
    core_limit: 3,
    visible_candidate_limit: 27,
    core_count: coreCount,
    visible_candidate_count: visibleCandidateCount,
    hidden_candidate_count: 0,
    calculated_at: "2026-08-26T12:00:00+08:00",
    items,
  };
}

test("parses the current Topic importance-score contract without inventing zero dimensions", function (): void {
  const result = parseTopicList(topicListFixture());
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
    parseTopicList(topicListFixture([malformedTopic]));
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

  const breakdown = parseTopicList(topicListFixture([legacyTopic])).items[0].score_breakdown;
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

test("accepts an explicit partial score contract for incomplete historical importance data", function (): void {
  const partialTopic = structuredClone(topicFixture);
  partialTopic.score_breakdown = {
    model: "partial",
    base_score: null,
    recency_factor: 1,
    rank_score: 88.2,
  } as unknown as typeof topicFixture.score_breakdown;

  const breakdown = parseTopicList(topicListFixture([partialTopic])).items[0].score_breakdown;

  assert.equal(breakdown.model, "partial");
  if (breakdown.model !== "partial") {
    assert.fail("expected partial breakdown");
  }
  assert.equal(breakdown.base_score, null);
  assert.equal(breakdown.recency_factor, 1);
  assert.equal(breakdown.rank_score, 88.2);
});

test("parses the Topic 3+27 queue contract with transparent score breakdown", function (): void {
  const candidate = {
    ...structuredClone(topicFixture),
    id: "topic-2",
    key: "project",
    title: "用户正在推进课程项目。",
    status: "suppressed",
    score: 76,
    queue_rank: 1,
    candidate_source: "demoted",
    importance_score: 70,
    approaching_bonus: 16,
    decay_penalty: 10,
    queue_score: 76,
    queue_score_breakdown: {
      importance_score: 70,
      approaching_bonus: 16,
      decay_penalty: 10,
      queue_score: 76,
    },
    core_entered_at: null,
    demoted_at: "2026-08-25T12:00:00+08:00",
    score_breakdown: {
      strongest_memory_score: 70,
      supporting_memory_points: 0,
      duplicate_memory_count: 0,
      counted_memory_ids: ["memory-1"],
      importance_score: 70,
      base_score: 70,
      recency_factor: 1,
      rank_score: 76,
      memory_scores: { "memory-1": 70 },
    },
  };

  const result = parseTopicList(topicListFixture([topicFixture, candidate]));

  assert.equal(result.core_limit, 3);
  assert.equal(result.visible_candidate_limit, 27);
  assert.equal(result.items[1].queue_rank, 1);
  assert.equal(result.items[1].candidate_source, "demoted");
  assert.deepEqual(result.items[1].queue_score_breakdown, {
    importance_score: 70,
    approaching_bonus: 16,
    decay_penalty: 10,
    queue_score: 76,
  });
  const breakdown = result.items[1].score_breakdown;
  assert.equal(breakdown.model, "static_importance_v3");
  if (breakdown.model !== "static_importance_v3") {
    assert.fail("expected static importance v3 breakdown");
  }
  assert.equal(breakdown.legacy_recency_factor, 1);
  assert.equal(breakdown.legacy_rank_score, 76);
});

test("parses core-only Topic responses while preserving complete pool statistics", function (): void {
  const coreOnly = topicListFixture();
  coreOnly.pool_total = 33;
  coreOnly.candidate_pool_total = 32;
  coreOnly.visible_candidate_count = 0;
  coreOnly.hidden_candidate_count = 5;

  const result = parseTopicList(coreOnly);

  assert.equal(result.total, 1);
  assert.equal(result.visible_candidate_count, 0);
  assert.equal(result.candidate_pool_total, 32);
  assert.equal(result.hidden_candidate_count, 5);
  assert.deepEqual(result.items.map((item) => item.status), ["active"]);
});

test("rejects queue scores outside their documented ranges", function (): void {
  const invalidCases = [
    ["importance_score", 101],
    ["approaching_bonus", 21],
    ["decay_penalty", -1],
    ["queue_score", 121],
  ] as const;

  for (const [field, value] of invalidCases) {
    const malformed = structuredClone(topicFixture) as Record<string, unknown>;
    malformed[field] = value;
    assert.throws(
      () => parseTopicList(topicListFixture([malformed])),
      (error: unknown) => error instanceof ApiContractError && error.message.includes(field),
    );
  }
});

test("rejects contradictory Topic queue score aliases, breakdowns and formulas", function (): void {
  const mismatchedImportance = structuredClone(topicFixture);
  mismatchedImportance.queue_score_breakdown.importance_score = 80;
  assert.throws(
    () => parseTopicList(topicListFixture([mismatchedImportance])),
    (error: unknown) =>
      error instanceof ApiContractError && /queue_score_breakdown\.importance_score/.test(error.message),
  );

  const mismatchedAlias = structuredClone(topicFixture);
  mismatchedAlias.score = 87;
  assert.throws(
    () => parseTopicList(topicListFixture([mismatchedAlias])),
    (error: unknown) => error instanceof ApiContractError && /score/.test(error.message),
  );

  const impossibleFormula = structuredClone(topicFixture);
  impossibleFormula.queue_score = 99;
  impossibleFormula.score = 99;
  impossibleFormula.queue_score_breakdown.queue_score = 99;
  assert.throws(
    () => parseTopicList(topicListFixture([impossibleFormula])),
    (error: unknown) => error instanceof ApiContractError && /queue_score/.test(error.message),
  );
});

test("rejects inconsistent Topic queue counts, ranks and state combinations", function (): void {
  const wrongRank = { ...structuredClone(topicFixture), queue_rank: 2 };
  assert.throws(
    () => parseTopicList(topicListFixture([wrongRank])),
    (error: unknown) => error instanceof ApiContractError && /queue_rank/.test(error.message),
  );

  const activeCandidate = { ...structuredClone(topicFixture), candidate_source: "new" };
  assert.throws(
    () => parseTopicList(topicListFixture([activeCandidate])),
    (error: unknown) => error instanceof ApiContractError && /candidate_source/.test(error.message),
  );

  const activePast = { ...structuredClone(topicFixture), attention_status: "past_unconfirmed" };
  assert.throws(
    () => parseTopicList(topicListFixture([activePast])),
    (error: unknown) => error instanceof ApiContractError && /past_unconfirmed/.test(error.message),
  );

  const wrongTotal = { ...topicListFixture(), total: 2 };
  assert.throws(
    () => parseTopicList(wrongTotal),
    (error: unknown) => error instanceof ApiContractError && /total/.test(error.message),
  );

  const wrongPool = { ...topicListFixture(), pool_total: 2 };
  assert.throws(
    () => parseTopicList(wrongPool),
    (error: unknown) => error instanceof ApiContractError && /pool_total/.test(error.message),
  );

  const candidateOne = {
    ...structuredClone(topicFixture),
    id: "candidate-1",
    status: "suppressed",
    queue_rank: 1,
    candidate_source: "new",
    core_entered_at: null,
  };
  const candidateTwo = {
    ...structuredClone(candidateOne),
    id: "candidate-2",
    queue_rank: 2,
  };
  const visibleCandidatesExceedPool = {
    ...topicListFixture([candidateOne, candidateTwo]),
    candidate_pool_total: 1,
    pool_total: 1,
  };
  assert.throws(
    () => parseTopicList(visibleCandidatesExceedPool),
    (error: unknown) =>
      error instanceof ApiContractError && /candidate_pool_total/.test(error.message),
  );
});

test("dashboard accepts at most three ordered core Topics", function (): void {
  const dashboard = {
    backend_status: "online",
    service_version: "1.2.3",
    fetched_at: "2026-08-26T10:36:00+08:00",
    scope: { user_id: "default", cube_id: "default_cube" },
    counts: {
      memories: 0,
      preferences: 0,
      skills: 0,
      queue_total: 0,
      queue_running: 0,
      queue_waiting: 0,
      active_topics: 1,
    },
    topics: [topicFixture],
    memories: [],
  };

  const candidate = {
    ...structuredClone(topicFixture),
    status: "suppressed",
    candidate_source: "new",
    core_entered_at: null,
  };
  assert.throws(
    () => parseDashboard({ ...dashboard, topics: [candidate] }),
    (error: unknown) => error instanceof ApiContractError && /response\.topics/.test(error.message),
  );

  const wrongRank = { ...structuredClone(topicFixture), queue_rank: 2 };
  assert.throws(
    () => parseDashboard({ ...dashboard, topics: [wrongRank] }),
    (error: unknown) => error instanceof ApiContractError && /queue_rank/.test(error.message),
  );

  const fourTopics = Array.from({ length: 4 }, (_, index) => ({
    ...structuredClone(topicFixture),
    id: `core-${index + 1}`,
    queue_rank: index + 1,
  }));
  assert.throws(
    () => parseDashboard({ ...dashboard, topics: fourTopics }),
    (error: unknown) => error instanceof ApiContractError && /response\.topics/.test(error.message),
  );
});

test("parses a transparent Topic selection trace without recalculating scores", function (): void {
  const tracePayload = {
    topic_id: "topic-1",
    topic_key: "interview",
    available: true,
    unavailable_reason: null,
    selection_version: 3,
    policy: {
      topic_threshold: 60,
      supporting_weight: 0.5,
      seat_limit: 15,
      memory_formula: "min(100, 维度分合计) × 置信系数",
      topic_formula: "最强单条 + 其他非重复记忆 × 0.5",
      rank_formula: "Topic 基础分 × 新鲜系数",
      queue_policy_version: 1,
      core_limit: 3,
      visible_candidate_limit: 27,
      scheduled_promotion_margin: 5,
      immediate_promotion_margin: 10,
      queue_formula: "importance_score + approaching_bonus - decay_penalty",
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
      shared_anchor: "A公司技术面试",
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
      importance_score: 80,
      approaching_bonus: 0,
      decay_penalty: 8,
      queue_score: 72,
      queue_rank: 1,
      candidate_source: null,
      attention_status: "open",
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
  };
  const trace = parseTopicSelectionTrace(tracePayload);

  assert.equal(trace.memories[0].dimensions[0].label, "committed");
  assert.equal(trace.memories[0].dimensions[0].score_value, 25);
  if (!trace.available) {
    assert.fail("expected an available trace");
  }
  assert.equal(trace.decision.rank_score, 72);
  assert.equal(trace.grouping.shared_anchor, "A公司技术面试");

  const activeDemoted = {
    ...tracePayload,
    decision: { ...tracePayload.decision, candidate_source: "demoted" },
  };
  assert.throws(
    () => parseTopicSelectionTrace(activeDemoted),
    (error: unknown) => error instanceof ApiContractError && /candidate_source/.test(error.message),
  );

  const activePastUnconfirmed = {
    ...tracePayload,
    decision: { ...tracePayload.decision, attention_status: "past_unconfirmed" },
  };
  assert.throws(
    () => parseTopicSelectionTrace(activePastUnconfirmed),
    (error: unknown) => error instanceof ApiContractError && /attention_status/.test(error.message),
  );
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
    selection_version: 3,
    policy: {
      topic_threshold: 60,
      supporting_weight: 0.5,
      seat_limit: 15,
      memory_formula: "memory formula",
      topic_formula: "topic formula",
      rank_formula: "rank formula",
      queue_policy_version: 1,
      core_limit: 3,
      visible_candidate_limit: 27,
      scheduled_promotion_margin: 5,
      immediate_promotion_margin: 10,
      queue_formula: "importance + approaching - decay",
      rubric: [],
    },
    grouping: {
      topic_kind: "event",
      reason: "same event",
      shared_anchor: null,
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
      importance_score: 80,
      approaching_bonus: 0,
      decay_penalty: 0,
      queue_score: 80,
      queue_rank: 1,
      candidate_source: null,
      attention_status: "open",
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
