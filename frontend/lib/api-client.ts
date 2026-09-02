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
} from "./api-contract.ts";
import type {
  AuthResult,
  ChatResult,
  Dashboard,
  DeleteMemoryResult,
  HealthResult,
  IngestionResult,
  MemoryList,
  MemoryResponse,
  MobileLoginResult,
  ReconcileResult,
  SearchResult,
  SessionResult,
  TopicList,
  TopicSelectionTrace,
} from "./api-contract.ts";

export type {
  Dashboard,
  DeleteMemoryResult,
  HealthResult,
  IngestionResult,
  MemoryDetail,
  MemoryList,
  MemorySummary,
  MobileLoginResult,
  SearchResult,
  Topic,
  TopicAttentionStatus,
  TopicCandidateSource,
  TopicEvidence,
  TopicImportanceBreakdown,
  TopicList,
  TopicQueueBreakdown,
  TopicQueueStatus,
  TopicSelectionTrace,
  TopicTraceDecision,
  TopicTraceDimension,
  TopicTraceGrouping,
  TopicTraceMemory,
  TopicTracePolicy,
  TopicTraceRubric,
  TopicTraceRubricOption,
  TopicTraceScoreUnit,
  TopicTraceTag,
  TopicScoreBreakdown,
  TopicScoreBreakdownLegacy,
  TopicScoreBreakdownPartial,
  TopicScoreBreakdownV2,
  TopicUpdate,
  TopicVersion,
} from "./api-contract.ts";

const APPLICATION_API = "/api/v1";

export type ApiErrorKind = "network" | "http" | "invalid-response";

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;

  constructor(kind: ApiErrorKind, message: string, status: number | null = null) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = status;
  }
}

type Decoder<T> = (payload: unknown) => T;

function redirectToLogin(path: string, status: number): void {
  if (
    status !== 401 ||
    path.startsWith("/auth/") ||
    typeof window === "undefined" ||
    window.location.pathname === "/login"
  ) {
    return;
  }

  const returnTo = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  window.location.replace(`/login?return_to=${encodeURIComponent(returnTo)}`);
}

function parseResponsePayload(rawBody: string, response: Response): unknown {
  if (!rawBody) {
    return null;
  }

  try {
    return JSON.parse(rawBody) as unknown;
  } catch {
    if (!response.ok) {
      throw new ApiError(
        "http",
        `应用后端请求失败（HTTP ${response.status}），但没有返回可读的错误信息。`,
        response.status,
      );
    }
    throw new ApiError(
      "invalid-response",
      "应用后端返回的不是 JSON。请确认前端连接的是 8011 应用接口，而不是 8000 MemOS 内部接口。",
      response.status,
    );
  }
}

function decodeResponse<T>(payload: unknown, decode: Decoder<T>, response: Response): T {
  try {
    return decode(payload);
  } catch (reason) {
    if (!(reason instanceof ApiContractError)) {
      throw reason;
    }
    throw new ApiError(
      "invalid-response",
      `应用后端返回的数据与当前前端不一致：${reason.message} 请同步前后端版本。`,
      response.status,
    );
  }
}

async function request<T>(path: string, decode: Decoder<T>, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${APPLICATION_API}${path}`, {
      ...init,
      cache: "no-store",
      credentials: "same-origin",
    });
  } catch {
    throw new ApiError(
      "network",
      "无法连接应用后端。请确认 8011 服务已启动，并检查 MEMOS_APP_API_URL。",
    );
  }

  const rawBody = await response.text();
  const payload = parseResponsePayload(rawBody, response);

  if (!response.ok) {
    redirectToLogin(path, response.status);
    throw new ApiError("http", formatApiErrorDetail(payload, response.status), response.status);
  }

  return decodeResponse(payload, decode, response);
}

function json(body: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

export const appApi = {
  login(password: string): Promise<AuthResult> {
    return request("/auth/login", parseAuthResult, json({ password }));
  },
  mobileLogin(password: string): Promise<MobileLoginResult> {
    return request("/auth/mobile/login", parseMobileLoginResult, json({ password }));
  },
  logout(): Promise<AuthResult> {
    return request("/auth/logout", parseAuthResult, { method: "POST" });
  },
  session(): Promise<SessionResult> {
    return request("/auth/session", parseSessionResult);
  },
  health(): Promise<HealthResult> {
    return request("/health", parseHealthResult);
  },
  dashboard(): Promise<Dashboard> {
    return request("/dashboard", parseDashboard);
  },
  memories(): Promise<MemoryList> {
    return request("/memories", parseMemoryList);
  },
  memory(memoryId: string): Promise<MemoryResponse> {
    return request(`/memories/${encodeURIComponent(memoryId)}`, parseMemoryResponse);
  },
  deleteMemory(memoryId: string): Promise<DeleteMemoryResult> {
    return request(`/memories/${encodeURIComponent(memoryId)}`, parseDeleteMemoryResult, {
      method: "DELETE",
    });
  },
  topics(): Promise<TopicList> {
    return request("/topics?include_suppressed=true", parseTopicList);
  },
  topicTrace(topicId: string): Promise<TopicSelectionTrace> {
    return request(
      `/topics/${encodeURIComponent(topicId)}/trace`,
      parseTopicSelectionTrace,
    );
  },
  reconcileTopics(): Promise<ReconcileResult> {
    return request("/topics/reconcile", parseReconcileResult, json({}));
  },
  rememberText(text: string): Promise<IngestionResult> {
    return request("/ingestions/text", parseIngestionResult, json({ text }));
  },
  chat(query: string, sessionId: string): Promise<ChatResult> {
    return request("/chat", parseChatResult, json({ query, session_id: sessionId }));
  },
  search(query: string): Promise<SearchResult> {
    return request("/search", parseSearchResult, json({ query }));
  },
  ingestFile(file: File, instruction: string): Promise<IngestionResult> {
    const body = new FormData();
    body.append("file", file);
    body.append("instruction", instruction);
    return request("/ingestions", parseIngestionResult, { method: "POST", body });
  },
  ingestVideo(url: string, instruction: string): Promise<IngestionResult> {
    return request(
      "/ingestions/video",
      parseIngestionResult,
      json({ url, instruction: instruction || null }),
    );
  },
};
