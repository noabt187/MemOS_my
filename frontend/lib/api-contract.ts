export type MemorySource = "video" | "image" | "mixed" | "text" | "conversation" | "direct";
export type MemoryCategory = "event" | "contact" | "media" | "other";

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

export type Dashboard = {
  backend_status: "online" | "degraded";
  service_version: string | null;
  fetched_at: string;
  scope: { user_id: string; cube_id: string };
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
  scope: { user_id: string; cube_id: string };
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

function fail(path: string, expected: string): never {
  throw new ApiContractError(`${path} 应为${expected}。`);
}

function object(value: unknown, path: string): JsonObject {
  if (!value || typeof value !== "object" || Array.isArray(value)) return fail(path, "对象");
  return value as JsonObject;
}

function array(value: unknown, path: string): unknown[] {
  if (!Array.isArray(value)) return fail(path, "数组");
  return value;
}

function string(value: unknown, path: string): string {
  if (typeof value !== "string") return fail(path, "字符串");
  return value;
}

function nullableString(value: unknown, path: string): string | null {
  if (value === null) return null;
  return string(value, path);
}

function displayString(value: unknown, path: string): string | null {
  if (value === null || value === undefined) return null;
  if (["string", "number", "boolean"].includes(typeof value)) return String(value);
  return fail(path, "可显示的标量或 null");
}

function number(value: unknown, path: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return fail(path, "有限数字");
  return value;
}

function nullableNumber(value: unknown, path: string): number | null {
  if (value === null || value === undefined) return null;
  return number(value, path);
}

function boolean(value: unknown, path: string): boolean {
  if (typeof value !== "boolean") return fail(path, "布尔值");
  return value;
}

function trueValue(value: unknown, path: string): true {
  if (value !== true) return fail(path, "true");
  return true;
}

function stringArray(value: unknown, path: string): string[] {
  return array(value, path).map((item, index) => string(item, `${path}[${index}]`));
}

function has(row: JsonObject, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(row, key);
}

function literal<T extends string>(value: unknown, allowed: readonly T[], path: string): T {
  if (typeof value !== "string" || !allowed.includes(value as T)) {
    return fail(path, allowed.map((item) => `“${item}”`).join(" 或 "));
  }
  return value as T;
}

function parseScope(value: unknown, path: string) {
  const row = object(value, path);
  return {
    user_id: string(row.user_id, `${path}.user_id`),
    cube_id: string(row.cube_id, `${path}.cube_id`),
  };
}

function parseMemorySummary(value: unknown, path: string): MemorySummary {
  const row = object(value, path);
  const source = string(row.source, `${path}.source`);
  const category = string(row.category, `${path}.category`);
  if (!MEMORY_SOURCES.has(source as MemorySource)) fail(`${path}.source`, "受支持的记忆来源");
  if (!MEMORY_CATEGORIES.has(category as MemoryCategory)) fail(`${path}.category`, "受支持的记忆分类");

  const result: MemorySummary = {
    id: string(row.id, `${path}.id`),
    title: string(row.title, `${path}.title`),
    content: string(row.content, `${path}.content`),
    memory_type: string(row.memory_type, `${path}.memory_type`),
    source: source as MemorySource,
    category: category as MemoryCategory,
    created_at: nullableString(row.created_at, `${path}.created_at`),
    updated_at: nullableString(row.updated_at, `${path}.updated_at`),
    tags: stringArray(row.tags, `${path}.tags`),
  };
  if (has(row, "score")) result.score = nullableNumber(row.score, `${path}.score`);
  return result;
}

function parseMemoryDetail(value: unknown, path: string): MemoryDetail {
  const row = object(value, path);
  const confidence = row.confidence;
  if (confidence !== null && typeof confidence !== "number" && typeof confidence !== "string") {
    fail(`${path}.confidence`, "数字、字符串或 null");
  }
  return {
    ...parseMemorySummary(row, path),
    confidence: confidence as number | string | null,
    background: nullableString(row.background, `${path}.background`),
    structured: object(row.structured, `${path}.structured`),
  };
}

function parseTopicEvidence(value: unknown, path: string): TopicEvidence {
  const row = object(value, path);
  return {
    memory_id: string(row.memory_id, `${path}.memory_id`),
    fact: string(row.fact, `${path}.fact`),
    contribution: string(row.contribution, `${path}.contribution`),
  };
}

function parseTopicVersion(value: unknown, path: string): TopicVersion {
  const row = object(value, path);
  return {
    version: nullableNumber(row.version, `${path}.version`),
    title: string(row.title, `${path}.title`),
    reason: string(row.reason, `${path}.reason`),
    updated_at: nullableString(row.updated_at, `${path}.updated_at`),
  };
}

function numberRecord(value: unknown, path: string): Record<string, number> {
  const row = object(value, path);
  return Object.fromEntries(
    Object.entries(row).map(([key, item]) => [key, number(item, `${path}.${key}`)]),
  );
}

function parseTopicScoreBreakdown(value: unknown, path: string): TopicScoreBreakdown {
  const row = object(value, path);
  const v2Keys = [
    "strongest_memory_score",
    "supporting_memory_points",
    "duplicate_memory_count",
    "counted_memory_ids",
    "memory_scores",
  ];
  if (v2Keys.some((key) => has(row, key))) {
    return {
      model: "memory_importance_v2",
      strongest_memory_score: number(
        row.strongest_memory_score,
        `${path}.strongest_memory_score`,
      ),
      supporting_memory_points: number(
        row.supporting_memory_points,
        `${path}.supporting_memory_points`,
      ),
      duplicate_memory_count: number(
        row.duplicate_memory_count,
        `${path}.duplicate_memory_count`,
      ),
      counted_memory_ids: stringArray(row.counted_memory_ids, `${path}.counted_memory_ids`),
      base_score: number(row.base_score, `${path}.base_score`),
      recency_factor: number(row.recency_factor, `${path}.recency_factor`),
      rank_score: number(row.rank_score, `${path}.rank_score`),
      memory_scores: numberRecord(row.memory_scores, `${path}.memory_scores`),
    };
  }

  const legacyKeys = [
    "evidence_points",
    "initiative_points",
    "urgency_points",
    "continuity_points",
    "status_points",
  ];
  if (legacyKeys.some((key) => has(row, key))) {
    return {
      model: "legacy_evidence_v1",
      base_score: nullableNumber(row.base_score, `${path}.base_score`),
      recency_factor: nullableNumber(row.recency_factor, `${path}.recency_factor`),
      rank_score: nullableNumber(row.rank_score, `${path}.rank_score`),
      evidence_points: nullableNumber(row.evidence_points, `${path}.evidence_points`),
      initiative_points: nullableNumber(row.initiative_points, `${path}.initiative_points`),
      urgency_points: nullableNumber(row.urgency_points, `${path}.urgency_points`),
      continuity_points: nullableNumber(row.continuity_points, `${path}.continuity_points`),
      status_points: nullableNumber(row.status_points, `${path}.status_points`),
    };
  }

  return {
    model: "partial",
    base_score: nullableNumber(row.base_score, `${path}.base_score`),
    recency_factor: nullableNumber(row.recency_factor, `${path}.recency_factor`),
    rank_score: nullableNumber(row.rank_score, `${path}.rank_score`),
  };
}

function parseTopic(value: unknown, path: string): Topic {
  const row = object(value, path);
  return {
    id: string(row.id, `${path}.id`),
    key: string(row.key, `${path}.key`),
    title: string(row.title, `${path}.title`),
    reason: string(row.reason, `${path}.reason`),
    status: string(row.status, `${path}.status`),
    progress: string(row.progress, `${path}.progress`),
    score: number(row.score, `${path}.score`),
    supporting_memory_ids: stringArray(
      row.supporting_memory_ids,
      `${path}.supporting_memory_ids`,
    ),
    evidence: array(row.evidence, `${path}.evidence`).map((item, index) =>
      parseTopicEvidence(item, `${path}.evidence[${index}]`),
    ),
    candidate_reasons: stringArray(row.candidate_reasons, `${path}.candidate_reasons`),
    score_breakdown: parseTopicScoreBreakdown(row.score_breakdown, `${path}.score_breakdown`),
    first_seen_at: nullableString(row.first_seen_at, `${path}.first_seen_at`),
    last_evidence_at: nullableString(row.last_evidence_at, `${path}.last_evidence_at`),
    version: nullableNumber(row.version, `${path}.version`),
    updated_at: nullableString(row.updated_at, `${path}.updated_at`),
    versions: array(row.versions, `${path}.versions`).map((item, index) =>
      parseTopicVersion(item, `${path}.versions[${index}]`),
    ),
  };
}

function parseTopicUpdate(value: unknown, path: string): TopicUpdate {
  const row = object(value, path);
  return {
    processed_memories: number(row.processed_memories, `${path}.processed_memories`),
    active_topics: number(row.active_topics, `${path}.active_topics`),
    error: nullableString(row.error, `${path}.error`),
  };
}

export function parseAuthResult(value: unknown): { ok: true } {
  const row = object(value, "response");
  return { ok: trueValue(row.ok, "response.ok") };
}

export function parseMobileLoginResult(value: unknown): MobileLoginResult {
  const row = object(value, "response");
  return {
    ok: trueValue(row.ok, "response.ok"),
    token_type: literal(row.token_type, ["Bearer"] as const, "response.token_type"),
    session_token: string(row.session_token, "response.session_token"),
    expires_in: number(row.expires_in, "response.expires_in"),
  };
}

export function parseSessionResult(value: unknown): { authenticated: boolean } {
  const row = object(value, "response");
  return { authenticated: boolean(row.authenticated, "response.authenticated") };
}

export function parseHealthResult(value: unknown): HealthResult {
  const row = object(value, "response");
  const dependencies = object(row.dependencies, "response.dependencies");
  return {
    status: literal(row.status, ["healthy", "degraded"] as const, "response.status"),
    dependencies: {
      memos: literal(
        dependencies.memos,
        ["online", "offline"] as const,
        "response.dependencies.memos",
      ),
      topics: literal(
        dependencies.topics,
        ["online", "empty", "error"] as const,
        "response.dependencies.topics",
      ),
    },
    service_version: displayString(row.service_version, "response.service_version"),
  };
}

export function parseDashboard(value: unknown): Dashboard {
  const row = object(value, "response");
  const counts = object(row.counts, "response.counts");
  return {
    backend_status: literal(
      row.backend_status,
      ["online", "degraded"] as const,
      "response.backend_status",
    ),
    service_version: displayString(row.service_version, "response.service_version"),
    fetched_at: string(row.fetched_at, "response.fetched_at"),
    scope: parseScope(row.scope, "response.scope"),
    counts: {
      memories: number(counts.memories, "response.counts.memories"),
      preferences: number(counts.preferences, "response.counts.preferences"),
      skills: number(counts.skills, "response.counts.skills"),
      queue_total: number(counts.queue_total, "response.counts.queue_total"),
      queue_running: number(counts.queue_running, "response.counts.queue_running"),
      queue_waiting: number(counts.queue_waiting, "response.counts.queue_waiting"),
      active_topics: number(counts.active_topics, "response.counts.active_topics"),
    },
    topics: array(row.topics, "response.topics").map((item, index) =>
      parseTopic(item, `response.topics[${index}]`),
    ),
    memories: array(row.memories, "response.memories").map((item, index) =>
      parseMemorySummary(item, `response.memories[${index}]`),
    ),
  };
}

export function parseMemoryList(value: unknown): MemoryList {
  const row = object(value, "response");
  return {
    scope: parseScope(row.scope, "response.scope"),
    total: number(row.total, "response.total"),
    items: array(row.items, "response.items").map((item, index) =>
      parseMemorySummary(item, `response.items[${index}]`),
    ),
  };
}

export function parseMemoryResponse(value: unknown): { memory: MemoryDetail } {
  const row = object(value, "response");
  return { memory: parseMemoryDetail(row.memory, "response.memory") };
}

export function parseDeleteMemoryResult(value: unknown): DeleteMemoryResult {
  const row = object(value, "response");
  return {
    ok: trueValue(row.ok, "response.ok"),
    memory_id: string(row.memory_id, "response.memory_id"),
    topic_sync: literal(
      row.topic_sync,
      ["updated", "pending"] as const,
      "response.topic_sync",
    ),
    removed_topic_memories: number(
      row.removed_topic_memories,
      "response.removed_topic_memories",
    ),
  };
}

export function parseTopicList(value: unknown): TopicList {
  const row = object(value, "response");
  return {
    total: number(row.total, "response.total"),
    items: array(row.items, "response.items").map((item, index) =>
      parseTopic(item, `response.items[${index}]`),
    ),
  };
}

export function parseReconcileResult(value: unknown): { ok: true; removed_memories: number } {
  const row = object(value, "response");
  return {
    ok: trueValue(row.ok, "response.ok"),
    removed_memories: number(row.removed_memories, "response.removed_memories"),
  };
}

export function parseIngestionResult(value: unknown): IngestionResult {
  const row = object(value, "response");
  const result: IngestionResult = {
    ok: trueValue(row.ok, "response.ok"),
    memories_created: number(row.memories_created, "response.memories_created"),
    topic: parseTopicUpdate(row.topic, "response.topic"),
  };
  if (has(row, "kind")) result.kind = string(row.kind, "response.kind");
  if (has(row, "filename")) result.filename = string(row.filename, "response.filename");
  if (has(row, "file_size")) result.file_size = number(row.file_size, "response.file_size");
  return result;
}

export function parseChatResult(value: unknown): { response: string; session_id: string } {
  const row = object(value, "response");
  return {
    response: string(row.response, "response.response"),
    session_id: string(row.session_id, "response.session_id"),
  };
}

export function parseSearchResult(value: unknown): SearchResult {
  const row = object(value, "response");
  return {
    results: array(row.results, "response.results").map((item, index) =>
      parseMemorySummary(item, `response.results[${index}]`),
    ),
    total: number(row.total, "response.total"),
  };
}

function validationMessage(value: unknown): string | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const row = value as JsonObject;
  if (typeof row.msg !== "string") return null;
  const locations = Array.isArray(row.loc) ? row.loc.map(String) : [];
  const fieldParts = ["body", "query", "path"].includes(locations[0])
    ? locations.slice(1)
    : locations;
  const field = fieldParts.join(".");
  return field ? `${field}：${row.msg}` : row.msg;
}

export function formatApiErrorDetail(payload: unknown, status: number): string {
  if (payload && typeof payload === "object" && !Array.isArray(payload)) {
    const detail = (payload as JsonObject).detail;
    if (typeof detail === "string" && detail.trim()) return detail.trim();
    if (Array.isArray(detail)) {
      const messages = detail.map(validationMessage).filter((item): item is string => Boolean(item));
      if (messages.length) return messages.join("；");
    }
  }
  return `应用后端请求失败（HTTP ${status}）。`;
}
