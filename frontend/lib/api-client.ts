export type MemorySummary = {
  id: string;
  title: string;
  content: string;
  memory_type: string;
  source: "video" | "image" | "mixed" | "text" | "conversation" | "direct";
  category: "event" | "contact" | "media" | "other";
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
  score_breakdown: Record<string, number>;
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

const APPLICATION_API = "/api/v1";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${APPLICATION_API}${path}`, {
    ...init,
    cache: "no-store",
    credentials: "same-origin",
  });
  const payload = await response.json().catch(() => ({ detail: "后端返回了无效数据。" }));
  if (!response.ok) {
    if (
      response.status === 401 &&
      !path.startsWith("/auth/") &&
      typeof window !== "undefined" &&
      window.location.pathname !== "/login"
    ) {
      const returnTo = `${window.location.pathname}${window.location.search}${window.location.hash}`;
      window.location.replace(`/login?return_to=${encodeURIComponent(returnTo)}`);
    }
    throw new Error(payload.detail || "应用后端处理失败。");
  }
  return payload as T;
}

function json(body: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

export const appApi = {
  login: (password: string) => request<{ ok: true }>("/auth/login", json({ password })),
  logout: () => request<{ ok: true }>("/auth/logout", { method: "POST" }),
  session: () => request<{ authenticated: boolean }>("/auth/session"),
  dashboard: () => request<Dashboard>("/dashboard"),
  memories: () => request<MemoryList>("/memories"),
  memory: (memoryId: string) =>
    request<{ memory: MemoryDetail }>(`/memories/${encodeURIComponent(memoryId)}`),
  deleteMemory: (memoryId: string) =>
    request<DeleteMemoryResult>(`/memories/${encodeURIComponent(memoryId)}`, {
      method: "DELETE",
    }),
  topics: () => request<TopicList>("/topics?include_suppressed=true"),
  reconcileTopics: () =>
    request<{ ok: true; removed_memories: number }>("/topics/reconcile", json({})),
  rememberText: (text: string) =>
    request<IngestionResult>("/ingestions/text", json({ text })),
  chat: (query: string, sessionId: string) =>
    request<{ response: string; session_id: string }>(
      "/chat",
      json({ query, session_id: sessionId }),
    ),
  search: (query: string) => request<SearchResult>("/search", json({ query })),
  ingestFile: (file: File, instruction: string) => {
    const body = new FormData();
    body.append("file", file);
    body.append("instruction", instruction);
    return request<IngestionResult>("/ingestions", { method: "POST", body });
  },
  ingestVideo: (url: string, instruction: string) =>
    request<IngestionResult>(
      "/ingestions/video",
      json({ url, instruction: instruction || null }),
    ),
};
