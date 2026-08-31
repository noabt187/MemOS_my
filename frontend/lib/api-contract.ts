export type MemorySource = "video" | "image" | "mixed" | "text" | "conversation" | "direct";
export type MemoryCategory = "event" | "contact" | "media" | "other";

export type Scope = {
  user_id: string;
  cube_id: string;
};

export type MemorySummary = {
  id: string;
  title: string;
  content: string;
  memory_type: string;
  source: MemorySource;
  category: MemoryCategory;
  created_at: string | null;
  updated_at: string | null;
  tags: string[];
  score?: number | null;
};

export type MemoryDetail = MemorySummary & {
  confidence: number | string | null;
  background: string | null;
  structured: Record<string, unknown>;
};

export type TopicEvidence = {
  memory_id: string;
  fact: string;
  contribution: string;
};

export type TopicVersion = {
  version: number | null;
  title: string;
  reason: string;
  updated_at: string | null;
};

export type TopicScoreBreakdownV2 = {
  model: "memory_importance_v2";
  strongest_memory_score: number;
  supporting_memory_points: number;
  duplicate_memory_count: number;
  counted_memory_ids: string[];
  base_score: number;
  recency_factor: number;
  rank_score: number;
  memory_scores: Record<string, number>;
};

export type TopicScoreBreakdownLegacy = {
  model: "legacy_evidence_v1";
  base_score: number | null;
  recency_factor: number | null;
  rank_score: number | null;
  evidence_points: number | null;
  initiative_points: number | null;
  urgency_points: number | null;
  continuity_points: number | null;
  status_points: number | null;
};

export type TopicScoreBreakdownPartial = {
  model: "partial";
  base_score: number | null;
  recency_factor: number | null;
  rank_score: number | null;
};

export type TopicScoreBreakdown =
  | TopicScoreBreakdownV2
  | TopicScoreBreakdownLegacy
  | TopicScoreBreakdownPartial;

export type Topic = {
  id: string;
  key: string;
  title: string;
  reason: string;
  status: string;
  progress: string;
  score: number;
  supporting_memory_ids: string[];
  evidence: TopicEvidence[];
  candidate_reasons: string[];
  score_breakdown: TopicScoreBreakdown;
  first_seen_at: string | null;
  last_evidence_at: string | null;
  version: number | null;
  updated_at: string | null;
  versions: TopicVersion[];
};

export type TopicTraceScoreUnit = "points" | "multiplier";

export type TopicTraceRubricOption = {
  label: string;
  score_value: number;
};

export type TopicTraceRubric = {
  key: string;
  title: string;
  score_unit: TopicTraceScoreUnit;
  options: TopicTraceRubricOption[];
};

export type TopicTracePolicy = {
  topic_threshold: number;
  supporting_weight: number;
  seat_limit: number;
  memory_formula: string;
  topic_formula: string;
  rank_formula: string;
  rubric: TopicTraceRubric[];
};

export type TopicTraceGrouping = {
  topic_kind: string;
  reason: string;
  shared_anchor: string | null;
  candidate_tag_keys: string[];
  memory_ids: string[];
};

export type TopicTraceDecision = {
  qualifies: boolean;
  base_score: number;
  recency_factor: number;
  rank_score: number;
  rank_position: number;
  seat_status: string;
  candidate_reasons: string[];
};

export type TopicTraceDimension = {
  key: string;
  title: string;
  label: string;
  score_value: number;
  score_unit: TopicTraceScoreUnit;
  max_value: number;
  source: string;
  reason: string;
};

export type TopicTraceTag = {
  topic_key: string;
  tag_name: string;
  relationship: string;
  reason: string;
};

export type TopicTraceMemory = {
  memory_id: string;
  text: string;
  active: boolean;
  assessed_at: string | null;
  eligible: boolean;
  initial_score: number;
  current_score: number;
  counting_status: string;
  raw_points: number;
  confidence_factor: number;
  dimensions: TopicTraceDimension[];
  tags: TopicTraceTag[];
};

export type AvailableTopicSelectionTrace = {
  topic_id: string;
  topic_key: string;
  available: true;
  unavailable_reason: null;
  selection_version: number;
  policy: TopicTracePolicy;
  grouping: TopicTraceGrouping;
  decision: TopicTraceDecision;
  memories: TopicTraceMemory[];
};

export type UnavailableTopicSelectionTrace = {
  topic_id: string;
  topic_key: string;
  available: false;
  unavailable_reason: string;
  selection_version: number | null;
  policy: null;
  grouping: null;
  decision: null;
  memories: [];
};

export type TopicSelectionTrace =
  | AvailableTopicSelectionTrace
  | UnavailableTopicSelectionTrace;

export type Dashboard = {
  backend_status: "online" | "degraded";
  service_version: string | null;
  fetched_at: string;
  scope: Scope;
  counts: {
    memories: number;
    preferences: number;
    skills: number;
    queue_total: number;
    queue_running: number;
    queue_waiting: number;
    active_topics: number;
  };
  topics: Topic[];
  memories: MemorySummary[];
};

export type HealthResult = {
  status: "healthy" | "degraded";
  dependencies: {
    memos: "online" | "offline";
    topics: "online" | "empty" | "error";
  };
  service_version: string | null;
};

export type MemoryList = {
  scope: Scope;
  total: number;
  items: MemorySummary[];
};

export type TopicList = { total: number; items: Topic[] };

export type TopicUpdate = {
  processed_memories: number;
  active_topics: number;
  error: string | null;
};

export type IngestionResult = {
  ok: true;
  kind?: string;
  filename?: string;
  file_size?: number;
  memories_created: number;
  topic: TopicUpdate;
};

export type SearchResult = { results: MemorySummary[]; total: number };

export type DeleteMemoryResult = {
  ok: true;
  memory_id: string;
  topic_sync: "updated" | "pending";
  removed_topic_memories: number;
};

export type MobileLoginResult = {
  ok: true;
  token_type: "Bearer";
  session_token: string;
  expires_in: number;
};

export type AuthResult = { ok: true };
export type SessionResult = { authenticated: boolean };
export type MemoryResponse = { memory: MemoryDetail };
export type ReconcileResult = { ok: true; removed_memories: number };
export type ChatResult = { response: string; session_id: string };

export class ApiContractError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ApiContractError";
  }
}

type JsonObject = Record<string, unknown>;

const MEMORY_SOURCES = new Set<MemorySource>([
  "video",
  "image",
  "mixed",
  "text",
  "conversation",
  "direct",
]);
const MEMORY_CATEGORIES = new Set<MemoryCategory>(["event", "contact", "media", "other"]);
const TOPIC_SCORE_V2_FIELDS = [
  "strongest_memory_score",
  "supporting_memory_points",
  "duplicate_memory_count",
  "counted_memory_ids",
  "memory_scores",
] as const;
const TOPIC_SCORE_LEGACY_FIELDS = [
  "evidence_points",
  "initiative_points",
  "urgency_points",
  "continuity_points",
  "status_points",
] as const;

function fail(path: string, expected: string): never {
  throw new ApiContractError(`${path} 应为${expected}。`);
}

function expectObject(value: unknown, path: string): JsonObject {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return fail(path, "对象");
  }
  return value as JsonObject;
}

function expectArray(value: unknown, path: string): unknown[] {
  if (!Array.isArray(value)) {
    return fail(path, "数组");
  }
  return value;
}

function expectString(value: unknown, path: string): string {
  if (typeof value !== "string") {
    return fail(path, "字符串");
  }
  return value;
}

function expectNullableString(value: unknown, path: string): string | null {
  if (value === null) {
    return null;
  }
  return expectString(value, path);
}

function expectDisplayString(value: unknown, path: string): string | null {
  if (value === null || value === undefined) {
    return null;
  }
  if (["string", "number", "boolean"].includes(typeof value)) {
    return String(value);
  }
  return fail(path, "可显示的标量或 null");
}

function expectNumber(value: unknown, path: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return fail(path, "有限数字");
  }
  return value;
}

function expectNullableNumber(value: unknown, path: string): number | null {
  if (value === null || value === undefined) {
    return null;
  }
  return expectNumber(value, path);
}

function expectNull(value: unknown, path: string): null {
  if (value !== null) {
    return fail(path, "null");
  }
  return null;
}

function expectBoolean(value: unknown, path: string): boolean {
  if (typeof value !== "boolean") {
    return fail(path, "布尔值");
  }
  return value;
}

function expectTrue(value: unknown, path: string): true {
  if (value !== true) {
    return fail(path, "true");
  }
  return true;
}

function expectStringArray(value: unknown, path: string): string[] {
  return expectArray(value, path).map((item, index) =>
    expectString(item, `${path}[${index}]`),
  );
}

function has(row: JsonObject, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(row, key);
}

function expectLiteral<T extends string>(value: unknown, allowed: readonly T[], path: string): T {
  if (typeof value !== "string" || !allowed.includes(value as T)) {
    return fail(path, allowed.map((item) => `“${item}”`).join(" 或 "));
  }
  return value as T;
}

function parseScope(value: unknown, path: string): Scope {
  const row = expectObject(value, path);
  return {
    user_id: expectString(row.user_id, `${path}.user_id`),
    cube_id: expectString(row.cube_id, `${path}.cube_id`),
  };
}

function parseMemorySummary(value: unknown, path: string): MemorySummary {
  const row = expectObject(value, path);
  const source = expectString(row.source, `${path}.source`);
  const category = expectString(row.category, `${path}.category`);
  if (!MEMORY_SOURCES.has(source as MemorySource)) {
    fail(`${path}.source`, "受支持的记忆来源");
  }
  if (!MEMORY_CATEGORIES.has(category as MemoryCategory)) {
    fail(`${path}.category`, "受支持的记忆分类");
  }

  const result: MemorySummary = {
    id: expectString(row.id, `${path}.id`),
    title: expectString(row.title, `${path}.title`),
    content: expectString(row.content, `${path}.content`),
    memory_type: expectString(row.memory_type, `${path}.memory_type`),
    source: source as MemorySource,
    category: category as MemoryCategory,
    created_at: expectNullableString(row.created_at, `${path}.created_at`),
    updated_at: expectNullableString(row.updated_at, `${path}.updated_at`),
    tags: expectStringArray(row.tags, `${path}.tags`),
  };
  if (has(row, "score")) {
    result.score = expectNullableNumber(row.score, `${path}.score`);
  }
  return result;
}

function parseMemoryDetail(value: unknown, path: string): MemoryDetail {
  const row = expectObject(value, path);
  const confidence = row.confidence;
  if (confidence !== null && typeof confidence !== "number" && typeof confidence !== "string") {
    fail(`${path}.confidence`, "数字、字符串或 null");
  }
  return {
    ...parseMemorySummary(row, path),
    confidence: confidence as number | string | null,
    background: expectNullableString(row.background, `${path}.background`),
    structured: expectObject(row.structured, `${path}.structured`),
  };
}

function parseTopicEvidence(value: unknown, path: string): TopicEvidence {
  const row = expectObject(value, path);
  return {
    memory_id: expectString(row.memory_id, `${path}.memory_id`),
    fact: expectString(row.fact, `${path}.fact`),
    contribution: expectString(row.contribution, `${path}.contribution`),
  };
}

function parseTopicVersion(value: unknown, path: string): TopicVersion {
  const row = expectObject(value, path);
  return {
    version: expectNullableNumber(row.version, `${path}.version`),
    title: expectString(row.title, `${path}.title`),
    reason: expectString(row.reason, `${path}.reason`),
    updated_at: expectNullableString(row.updated_at, `${path}.updated_at`),
  };
}

function parseNumberRecord(value: unknown, path: string): Record<string, number> {
  const row = expectObject(value, path);
  return Object.fromEntries(
    Object.entries(row).map(([key, item]) => [key, expectNumber(item, `${path}.${key}`)]),
  );
}

function parseTopicScoreBreakdown(value: unknown, path: string): TopicScoreBreakdown {
  const row = expectObject(value, path);
  if (TOPIC_SCORE_V2_FIELDS.some((field) => has(row, field))) {
    return {
      model: "memory_importance_v2",
      strongest_memory_score: expectNumber(
        row.strongest_memory_score,
        `${path}.strongest_memory_score`,
      ),
      supporting_memory_points: expectNumber(
        row.supporting_memory_points,
        `${path}.supporting_memory_points`,
      ),
      duplicate_memory_count: expectNumber(
        row.duplicate_memory_count,
        `${path}.duplicate_memory_count`,
      ),
      counted_memory_ids: expectStringArray(
        row.counted_memory_ids,
        `${path}.counted_memory_ids`,
      ),
      base_score: expectNumber(row.base_score, `${path}.base_score`),
      recency_factor: expectNumber(row.recency_factor, `${path}.recency_factor`),
      rank_score: expectNumber(row.rank_score, `${path}.rank_score`),
      memory_scores: parseNumberRecord(row.memory_scores, `${path}.memory_scores`),
    };
  }

  if (TOPIC_SCORE_LEGACY_FIELDS.some((field) => has(row, field))) {
    return {
      model: "legacy_evidence_v1",
      base_score: expectNullableNumber(row.base_score, `${path}.base_score`),
      recency_factor: expectNullableNumber(row.recency_factor, `${path}.recency_factor`),
      rank_score: expectNullableNumber(row.rank_score, `${path}.rank_score`),
      evidence_points: expectNullableNumber(row.evidence_points, `${path}.evidence_points`),
      initiative_points: expectNullableNumber(
        row.initiative_points,
        `${path}.initiative_points`,
      ),
      urgency_points: expectNullableNumber(row.urgency_points, `${path}.urgency_points`),
      continuity_points: expectNullableNumber(
        row.continuity_points,
        `${path}.continuity_points`,
      ),
      status_points: expectNullableNumber(row.status_points, `${path}.status_points`),
    };
  }

  return {
    model: "partial",
    base_score: expectNullableNumber(row.base_score, `${path}.base_score`),
    recency_factor: expectNullableNumber(row.recency_factor, `${path}.recency_factor`),
    rank_score: expectNullableNumber(row.rank_score, `${path}.rank_score`),
  };
}

function parseTopic(value: unknown, path: string): Topic {
  const row = expectObject(value, path);
  return {
    id: expectString(row.id, `${path}.id`),
    key: expectString(row.key, `${path}.key`),
    title: expectString(row.title, `${path}.title`),
    reason: expectString(row.reason, `${path}.reason`),
    status: expectString(row.status, `${path}.status`),
    progress: expectString(row.progress, `${path}.progress`),
    score: expectNumber(row.score, `${path}.score`),
    supporting_memory_ids: expectStringArray(
      row.supporting_memory_ids,
      `${path}.supporting_memory_ids`,
    ),
    evidence: expectArray(row.evidence, `${path}.evidence`).map((item, index) =>
      parseTopicEvidence(item, `${path}.evidence[${index}]`),
    ),
    candidate_reasons: expectStringArray(
      row.candidate_reasons,
      `${path}.candidate_reasons`,
    ),
    score_breakdown: parseTopicScoreBreakdown(row.score_breakdown, `${path}.score_breakdown`),
    first_seen_at: expectNullableString(row.first_seen_at, `${path}.first_seen_at`),
    last_evidence_at: expectNullableString(row.last_evidence_at, `${path}.last_evidence_at`),
    version: expectNullableNumber(row.version, `${path}.version`),
    updated_at: expectNullableString(row.updated_at, `${path}.updated_at`),
    versions: expectArray(row.versions, `${path}.versions`).map((item, index) =>
      parseTopicVersion(item, `${path}.versions[${index}]`),
    ),
  };
}

function parseTopicTraceRubricOption(
  value: unknown,
  path: string,
): TopicTraceRubricOption {
  const row = expectObject(value, path);
  return {
    label: expectString(row.label, `${path}.label`),
    score_value: expectNumber(row.score_value, `${path}.score_value`),
  };
}

function parseTopicTraceRubric(value: unknown, path: string): TopicTraceRubric {
  const row = expectObject(value, path);
  return {
    key: expectString(row.key, `${path}.key`),
    title: expectString(row.title, `${path}.title`),
    score_unit: expectLiteral(
      row.score_unit,
      ["points", "multiplier"] as const,
      `${path}.score_unit`,
    ),
    options: expectArray(row.options, `${path}.options`).map((item, index) =>
      parseTopicTraceRubricOption(item, `${path}.options[${index}]`),
    ),
  };
}

function parseTopicTracePolicy(value: unknown, path: string): TopicTracePolicy {
  const row = expectObject(value, path);
  return {
    topic_threshold: expectNumber(row.topic_threshold, `${path}.topic_threshold`),
    supporting_weight: expectNumber(row.supporting_weight, `${path}.supporting_weight`),
    seat_limit: expectNumber(row.seat_limit, `${path}.seat_limit`),
    memory_formula: expectString(row.memory_formula, `${path}.memory_formula`),
    topic_formula: expectString(row.topic_formula, `${path}.topic_formula`),
    rank_formula: expectString(row.rank_formula, `${path}.rank_formula`),
    rubric: expectArray(row.rubric, `${path}.rubric`).map((item, index) =>
      parseTopicTraceRubric(item, `${path}.rubric[${index}]`),
    ),
  };
}

function parseTopicTraceGrouping(value: unknown, path: string): TopicTraceGrouping {
  const row = expectObject(value, path);
  return {
    topic_kind: expectString(row.topic_kind, `${path}.topic_kind`),
    reason: expectString(row.reason, `${path}.reason`),
    shared_anchor: expectNullableString(row.shared_anchor, `${path}.shared_anchor`),
    candidate_tag_keys: expectStringArray(
      row.candidate_tag_keys,
      `${path}.candidate_tag_keys`,
    ),
    memory_ids: expectStringArray(row.memory_ids, `${path}.memory_ids`),
  };
}

function parseTopicTraceDecision(value: unknown, path: string): TopicTraceDecision {
  const row = expectObject(value, path);
  return {
    qualifies: expectBoolean(row.qualifies, `${path}.qualifies`),
    base_score: expectNumber(row.base_score, `${path}.base_score`),
    recency_factor: expectNumber(row.recency_factor, `${path}.recency_factor`),
    rank_score: expectNumber(row.rank_score, `${path}.rank_score`),
    rank_position: expectNumber(row.rank_position, `${path}.rank_position`),
    seat_status: expectString(row.seat_status, `${path}.seat_status`),
    candidate_reasons: expectStringArray(
      row.candidate_reasons,
      `${path}.candidate_reasons`,
    ),
  };
}

function parseTopicTraceDimension(value: unknown, path: string): TopicTraceDimension {
  const row = expectObject(value, path);
  return {
    key: expectString(row.key, `${path}.key`),
    title: expectString(row.title, `${path}.title`),
    label: expectString(row.label, `${path}.label`),
    score_value: expectNumber(row.score_value, `${path}.score_value`),
    score_unit: expectLiteral(
      row.score_unit,
      ["points", "multiplier"] as const,
      `${path}.score_unit`,
    ),
    max_value: expectNumber(row.max_value, `${path}.max_value`),
    source: expectString(row.source, `${path}.source`),
    reason: expectString(row.reason, `${path}.reason`),
  };
}

function parseTopicTraceTag(value: unknown, path: string): TopicTraceTag {
  const row = expectObject(value, path);
  return {
    topic_key: expectString(row.topic_key, `${path}.topic_key`),
    tag_name: expectString(row.tag_name, `${path}.tag_name`),
    relationship: expectString(row.relationship, `${path}.relationship`),
    reason: expectString(row.reason, `${path}.reason`),
  };
}

function parseTopicTraceMemory(value: unknown, path: string): TopicTraceMemory {
  const row = expectObject(value, path);
  return {
    memory_id: expectString(row.memory_id, `${path}.memory_id`),
    text: expectString(row.text, `${path}.text`),
    active: expectBoolean(row.active, `${path}.active`),
    assessed_at: expectNullableString(row.assessed_at, `${path}.assessed_at`),
    eligible: expectBoolean(row.eligible, `${path}.eligible`),
    initial_score: expectNumber(row.initial_score, `${path}.initial_score`),
    current_score: expectNumber(row.current_score, `${path}.current_score`),
    counting_status: expectString(row.counting_status, `${path}.counting_status`),
    raw_points: expectNumber(row.raw_points, `${path}.raw_points`),
    confidence_factor: expectNumber(row.confidence_factor, `${path}.confidence_factor`),
    dimensions: expectArray(row.dimensions, `${path}.dimensions`).map((item, index) =>
      parseTopicTraceDimension(item, `${path}.dimensions[${index}]`),
    ),
    tags: expectArray(row.tags, `${path}.tags`).map((item, index) =>
      parseTopicTraceTag(item, `${path}.tags[${index}]`),
    ),
  };
}

function parseTopicUpdate(value: unknown, path: string): TopicUpdate {
  const row = expectObject(value, path);
  return {
    processed_memories: expectNumber(row.processed_memories, `${path}.processed_memories`),
    active_topics: expectNumber(row.active_topics, `${path}.active_topics`),
    error: expectNullableString(row.error, `${path}.error`),
  };
}

export function parseAuthResult(value: unknown): AuthResult {
  const row = expectObject(value, "response");
  return { ok: expectTrue(row.ok, "response.ok") };
}

export function parseMobileLoginResult(value: unknown): MobileLoginResult {
  const row = expectObject(value, "response");
  return {
    ok: expectTrue(row.ok, "response.ok"),
    token_type: expectLiteral(row.token_type, ["Bearer"] as const, "response.token_type"),
    session_token: expectString(row.session_token, "response.session_token"),
    expires_in: expectNumber(row.expires_in, "response.expires_in"),
  };
}

export function parseSessionResult(value: unknown): SessionResult {
  const row = expectObject(value, "response");
  return { authenticated: expectBoolean(row.authenticated, "response.authenticated") };
}

export function parseHealthResult(value: unknown): HealthResult {
  const row = expectObject(value, "response");
  const dependencies = expectObject(row.dependencies, "response.dependencies");
  return {
    status: expectLiteral(row.status, ["healthy", "degraded"] as const, "response.status"),
    dependencies: {
      memos: expectLiteral(
        dependencies.memos,
        ["online", "offline"] as const,
        "response.dependencies.memos",
      ),
      topics: expectLiteral(
        dependencies.topics,
        ["online", "empty", "error"] as const,
        "response.dependencies.topics",
      ),
    },
    service_version: expectDisplayString(row.service_version, "response.service_version"),
  };
}

export function parseDashboard(value: unknown): Dashboard {
  const row = expectObject(value, "response");
  const counts = expectObject(row.counts, "response.counts");
  return {
    backend_status: expectLiteral(
      row.backend_status,
      ["online", "degraded"] as const,
      "response.backend_status",
    ),
    service_version: expectDisplayString(row.service_version, "response.service_version"),
    fetched_at: expectString(row.fetched_at, "response.fetched_at"),
    scope: parseScope(row.scope, "response.scope"),
    counts: {
      memories: expectNumber(counts.memories, "response.counts.memories"),
      preferences: expectNumber(counts.preferences, "response.counts.preferences"),
      skills: expectNumber(counts.skills, "response.counts.skills"),
      queue_total: expectNumber(counts.queue_total, "response.counts.queue_total"),
      queue_running: expectNumber(counts.queue_running, "response.counts.queue_running"),
      queue_waiting: expectNumber(counts.queue_waiting, "response.counts.queue_waiting"),
      active_topics: expectNumber(counts.active_topics, "response.counts.active_topics"),
    },
    topics: expectArray(row.topics, "response.topics").map((item, index) =>
      parseTopic(item, `response.topics[${index}]`),
    ),
    memories: expectArray(row.memories, "response.memories").map((item, index) =>
      parseMemorySummary(item, `response.memories[${index}]`),
    ),
  };
}

export function parseMemoryList(value: unknown): MemoryList {
  const row = expectObject(value, "response");
  return {
    scope: parseScope(row.scope, "response.scope"),
    total: expectNumber(row.total, "response.total"),
    items: expectArray(row.items, "response.items").map((item, index) =>
      parseMemorySummary(item, `response.items[${index}]`),
    ),
  };
}

export function parseMemoryResponse(value: unknown): MemoryResponse {
  const row = expectObject(value, "response");
  return { memory: parseMemoryDetail(row.memory, "response.memory") };
}

export function parseDeleteMemoryResult(value: unknown): DeleteMemoryResult {
  const row = expectObject(value, "response");
  return {
    ok: expectTrue(row.ok, "response.ok"),
    memory_id: expectString(row.memory_id, "response.memory_id"),
    topic_sync: expectLiteral(
      row.topic_sync,
      ["updated", "pending"] as const,
      "response.topic_sync",
    ),
    removed_topic_memories: expectNumber(
      row.removed_topic_memories,
      "response.removed_topic_memories",
    ),
  };
}

export function parseTopicList(value: unknown): TopicList {
  const row = expectObject(value, "response");
  return {
    total: expectNumber(row.total, "response.total"),
    items: expectArray(row.items, "response.items").map((item, index) =>
      parseTopic(item, `response.items[${index}]`),
    ),
  };
}

export function parseTopicSelectionTrace(value: unknown): TopicSelectionTrace {
  const row = expectObject(value, "response");
  const topicId = expectString(row.topic_id, "response.topic_id");
  const topicKey = expectString(row.topic_key, "response.topic_key");
  const available = expectBoolean(row.available, "response.available");

  if (!available) {
    const unavailableReason = expectString(
      row.unavailable_reason,
      "response.unavailable_reason",
    );
    if (!unavailableReason.trim()) {
      fail("response.unavailable_reason", "非空字符串");
    }
    const memories = expectArray(row.memories, "response.memories");
    if (memories.length > 0) {
      fail("response.memories", "空数组");
    }
    return {
      topic_id: topicId,
      topic_key: topicKey,
      available: false,
      unavailable_reason: unavailableReason,
      selection_version: expectNullableNumber(
        row.selection_version,
        "response.selection_version",
      ),
      policy: expectNull(row.policy, "response.policy"),
      grouping: expectNull(row.grouping, "response.grouping"),
      decision: expectNull(row.decision, "response.decision"),
      memories: [],
    };
  }

  expectNull(row.unavailable_reason, "response.unavailable_reason");
  return {
    topic_id: topicId,
    topic_key: topicKey,
    available: true,
    unavailable_reason: null,
    selection_version: expectNumber(row.selection_version, "response.selection_version"),
    policy: parseTopicTracePolicy(row.policy, "response.policy"),
    grouping: parseTopicTraceGrouping(row.grouping, "response.grouping"),
    decision: parseTopicTraceDecision(row.decision, "response.decision"),
    memories: expectArray(row.memories, "response.memories").map((item, index) =>
      parseTopicTraceMemory(item, `response.memories[${index}]`),
    ),
  };
}

export function parseReconcileResult(value: unknown): ReconcileResult {
  const row = expectObject(value, "response");
  return {
    ok: expectTrue(row.ok, "response.ok"),
    removed_memories: expectNumber(row.removed_memories, "response.removed_memories"),
  };
}

export function parseIngestionResult(value: unknown): IngestionResult {
  const row = expectObject(value, "response");
  const result: IngestionResult = {
    ok: expectTrue(row.ok, "response.ok"),
    memories_created: expectNumber(row.memories_created, "response.memories_created"),
    topic: parseTopicUpdate(row.topic, "response.topic"),
  };
  if (has(row, "kind")) {
    result.kind = expectString(row.kind, "response.kind");
  }
  if (has(row, "filename")) {
    result.filename = expectString(row.filename, "response.filename");
  }
  if (has(row, "file_size")) {
    result.file_size = expectNumber(row.file_size, "response.file_size");
  }
  return result;
}

export function parseChatResult(value: unknown): ChatResult {
  const row = expectObject(value, "response");
  return {
    response: expectString(row.response, "response.response"),
    session_id: expectString(row.session_id, "response.session_id"),
  };
}

export function parseSearchResult(value: unknown): SearchResult {
  const row = expectObject(value, "response");
  return {
    results: expectArray(row.results, "response.results").map((item, index) =>
      parseMemorySummary(item, `response.results[${index}]`),
    ),
    total: expectNumber(row.total, "response.total"),
  };
}

function validationMessage(value: unknown): string | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  const row = value as JsonObject;
  if (typeof row.msg !== "string") {
    return null;
  }
  const locations = Array.isArray(row.loc) ? row.loc.map(String) : [];
  let fieldParts = locations;
  if (["body", "query", "path"].includes(locations[0])) {
    fieldParts = locations.slice(1);
  }
  const field = fieldParts.join(".");
  if (field) {
    return `${field}：${row.msg}`;
  }
  return row.msg;
}

export function formatApiErrorDetail(payload: unknown, status: number): string {
  if (payload && typeof payload === "object" && !Array.isArray(payload)) {
    const detail = (payload as JsonObject).detail;
    if (typeof detail === "string" && detail.trim()) {
      return detail.trim();
    }
    if (Array.isArray(detail)) {
      const messages = detail.map(validationMessage).filter((item): item is string => Boolean(item));
      if (messages.length) {
        return messages.join("；");
      }
    }
  }
  return `应用后端请求失败（HTTP ${status}）。`;
}
