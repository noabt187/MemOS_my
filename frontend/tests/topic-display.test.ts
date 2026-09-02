import assert from "node:assert/strict";
import test from "node:test";

import type { Topic } from "../lib/api-contract.ts";
import {
  filterTopicQueues,
  formatTopicQueueExplanation,
  getTopicAttentionStatusLabel,
  getTopicCandidateSourceLabel,
  getTopicStatusLabel,
  partitionTopicQueues,
} from "../lib/topic-display.ts";

function topicFixture(overrides: Partial<Topic> = {}): Topic {
  return {
    id: "topic-1",
    key: "interview",
    title: "用户正在准备技术面试。",
    reason: "多条记忆显示面试临近。",
    status: "active",
    progress: "planned",
    score: 76,
    queue_rank: 1,
    candidate_source: null,
    attention_status: "open",
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
    supporting_memory_ids: ["memory-1"],
    evidence: [],
    candidate_reasons: ["面试时间临近"],
    score_breakdown: {
      model: "static_importance_v3",
      strongest_memory_score: 70,
      supporting_memory_points: 0,
      duplicate_memory_count: 0,
      counted_memory_ids: ["memory-1"],
      importance_score: 70,
      base_score: 70,
      memory_scores: { "memory-1": 70 },
    },
    first_seen_at: "2026-08-20T10:00:00+08:00",
    last_evidence_at: "2026-08-26T10:00:00+08:00",
    core_entered_at: "2026-08-25T00:00:00+08:00",
    demoted_at: null,
    calculated_at: "2026-08-26T12:00:00+08:00",
    version: 2,
    updated_at: "2026-08-26T12:00:00+08:00",
    versions: [],
    ...overrides,
  };
}

test("partitions core and candidate queues without changing backend ranks", function (): void {
  const core = topicFixture({ id: "core", queue_rank: 1 });
  const candidate = topicFixture({
    id: "candidate",
    status: "suppressed",
    queue_rank: 7,
    candidate_source: "new",
    core_entered_at: null,
    decay_penalty: 0,
    queue_score: 86,
    score: 86,
    queue_score_breakdown: {
      importance_score: 70,
      approaching_bonus: 16,
      decay_penalty: 0,
      queue_score: 86,
    },
  });

  const result = partitionTopicQueues([candidate, core]);

  assert.deepEqual(result.core.map((topic) => topic.id), ["core"]);
  assert.deepEqual(result.candidates.map((topic) => topic.id), ["candidate"]);
  assert.equal(result.candidates[0].queue_rank, 7);
});

test("filters Topic queues without replacing their saved queue ranks", function (): void {
  const topics = [
    topicFixture({ id: "core", queue_rank: 1 }),
    topicFixture({
      id: "candidate",
      key: "course_project",
      title: "用户正在推进课程项目。",
      status: "suppressed",
      queue_rank: 12,
      candidate_source: "refreshed",
      core_entered_at: null,
    }),
  ];

  const result = filterTopicQueues(topics, "课程", "suppressed");

  assert.equal(result.length, 1);
  assert.equal(result[0].id, "candidate");
  assert.equal(result[0].queue_rank, 12);
});

test("formats importance plus approaching minus decay in plain Chinese", function (): void {
  assert.equal(
    formatTopicQueueExplanation(topicFixture()),
    "重要度 70 分；事件临近增加 16 分；核心席位陈旧衰减扣除 10 分；当前队列分 76 分。",
  );

  const refreshed = topicFixture({
    status: "suppressed",
    candidate_source: "refreshed",
    core_entered_at: null,
    decay_penalty: 0,
    queue_score: 86,
    score: 86,
    queue_score_breakdown: {
      importance_score: 70,
      approaching_bonus: 16,
      decay_penalty: 0,
      queue_score: 86,
    },
  });
  assert.equal(
    formatTopicQueueExplanation(refreshed),
    "重要度 70 分；事件临近增加 16 分；当前队列分 86 分。",
  );
});

test("labels demoted, refreshed and past-unconfirmed candidates", function (): void {
  assert.equal(getTopicCandidateSourceLabel("demoted"), "核心降级");
  assert.equal(getTopicCandidateSourceLabel("refreshed"), "新证据刷新");
  assert.equal(getTopicAttentionStatusLabel("past_unconfirmed"), "结果待确认");

  const pending = topicFixture({
    status: "suppressed",
    candidate_source: "demoted",
    attention_status: "past_unconfirmed",
    core_entered_at: null,
    demoted_at: "2026-08-26T12:00:00+08:00",
  });
  assert.match(
    formatTopicQueueExplanation(pending),
    /事件时间已过，结果仍待确认，暂时只能留在候选队列/,
  );
});

test("keeps past-unconfirmed Topics in the candidate lane and exposes separate empty lanes", function (): void {
  assert.deepEqual(partitionTopicQueues([]), { core: [], candidates: [] });

  const pending = topicFixture({
    id: "pending",
    status: "suppressed",
    queue_rank: 1,
    candidate_source: "demoted",
    attention_status: "past_unconfirmed",
    core_entered_at: null,
    demoted_at: "2026-08-26T12:00:00+08:00",
  });
  const result = partitionTopicQueues([pending]);

  assert.deepEqual(result.core, []);
  assert.deepEqual(result.candidates.map((topic) => topic.id), ["pending"]);
});

test("labels due-unverified events as unconfirmed after their due time", function (): void {
  assert.equal(getTopicStatusLabel("due_unverified"), "到期未确认");
});
