"use client";

import { type ReactElement, useCallback, useEffect, useMemo, useState } from "react";

import { appApi } from "@/lib/api-client.ts";
import type { Dashboard, MemoryDetail, MemorySummary } from "@/lib/api-client.ts";
import Link from "@/lib/link.tsx";
import AppRail from "./components/AppRail.tsx";

const MEMORY_FILTERS = [
  ["all", "全部"],
  ["event", "事件"],
  ["contact", "联系人"],
  ["media", "媒体"],
] as const;

const MEMORY_TYPE_LABELS: Readonly<Record<string, string>> = {
  LongTermMemory: "长期记忆",
  UserMemory: "用户记忆",
  PreferenceMemory: "偏好",
  SkillMemory: "技能",
  ToolSchemaMemory: "工具",
  ToolTrajectoryMemory: "工具轨迹",
};

const SOURCE_LABELS: Readonly<Record<string, string>> = {
  video: "视频",
  image: "图片",
  mixed: "图文",
  text: "文字",
  conversation: "对话",
  direct: "直接写入",
};

function toText(value: unknown): string[] {
  if (typeof value === "string" && value.trim()) return [value.trim()];
  if (Array.isArray(value)) return value.flatMap(toText);
  if (value && typeof value === "object") {
    return Object.values(value as Record<string, unknown>).flatMap(toText);
  }
  return [];
}

function formatTime(value?: string, includeYear = false): string {
  if (!value) return "时间未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    ...(includeYear ? { year: "numeric" as const } : {}),
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: includeYear ? "2-digit" : undefined,
    hour12: false,
  }).format(date);
}

function memoryTypeLabel(type?: string): string {
  return MEMORY_TYPE_LABELS[type || ""] || type || "未分类";
}

function sourceLabel(source?: string): string {
  return SOURCE_LABELS[source || ""] || "直接写入";
}

function getServiceLabel(
  loading: boolean,
  dashboard: Dashboard | null,
  serviceHealthy: boolean,
): string {
  if (loading && !dashboard) return "正在检查";
  if (serviceHealthy) return "后端在线";
  return "后端异常";
}

export default function Home(): ReactElement {
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [selectedMemory, setSelectedMemory] = useState<MemoryDetail | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [deletedMemoryIds, setDeletedMemoryIds] = useState<Set<string>>(() => new Set());
  const [deleteTarget, setDeleteTarget] = useState<MemorySummary | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const [deleteNotice, setDeleteNotice] = useState<{ kind: "success" | "warning"; text: string } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setDashboard(await appApi.dashboard());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取记忆总览");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  useEffect(() => {
    if (!autoRefresh) return;
    const timer = window.setInterval(() => void load(), 30_000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, load]);

  useEffect(() => {
    function closeOnEscape(event: KeyboardEvent): void {
      if (event.key !== "Escape" || deleting) return;
      if (deleteTarget) setDeleteTarget(null);
      else setSelectedMemory(null);
    }
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [deleteTarget, deleting]);

  const allMemories = useMemo(
    () =>
      [...(dashboard?.memories || [])]
        .filter((item) => !deletedMemoryIds.has(item.id))
        .sort(
          (a, b) =>
            new Date(b.created_at || 0).getTime() -
            new Date(a.created_at || 0).getTime(),
        ),
    [dashboard, deletedMemoryIds],
  );

  async function openMemory(memory: MemorySummary): Promise<void> {
    setError("");
    try {
      const result = await appApi.memory(memory.id);
      setSelectedMemory(result.memory);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取记忆详情");
    }
  }

  function askToDelete(memory: MemorySummary): void {
    setDeleteError("");
    setDeleteTarget(memory);
  }

  async function confirmDelete(): Promise<void> {
    if (!deleteTarget || deleting) return;
    setDeleting(true);
    setDeleteError("");
    setDeleteNotice(null);
    try {
      const result = await appApi.deleteMemory(deleteTarget.id);

      const deletedId = deleteTarget.id;
      setDeletedMemoryIds((current) => new Set(current).add(deletedId));
      setSelectedMemory(null);
      setDeleteTarget(null);
      setDeleteNotice({
        kind: result.topic_sync === "updated" ? "success" : "warning",
        text: result.topic_sync === "updated"
          ? "记忆已从 MemOS 永久删除，Topic 证据已同步。"
          : "记忆已从 MemOS 永久删除；Topic 暂未同步，将在下次对账时自动清理。",
      });
      void load();
    } catch (deleteFailure) {
      setDeleteError(deleteFailure instanceof Error ? deleteFailure.message : "删除失败，请稍后重试。");
    } finally {
      setDeleting(false);
    }
  }

  const filteredMemories = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return allMemories.filter((item) => {
      const matchesFilter =
        filter === "all" ||
        item.category === filter;
      if (!matchesFilter) return false;
      if (!normalizedQuery) return true;
      const searchable = [item.title, item.content, item.tags]
        .flatMap(toText)
        .join(" ")
        .toLowerCase();
      return searchable.includes(normalizedQuery);
    });
  }, [allMemories, filter, query]);

  const currentTopics = dashboard?.topics || [];
  const counts = dashboard?.counts;
  const serviceHealthy = dashboard?.backend_status === "online";
  const serviceLabel = getServiceLabel(loading, dashboard, serviceHealthy);

  return (
    <main className="shell" id="top">
      <AppRail active="overview" />

      <section className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">MEMOS OBSERVATORY</p>
            <h1>记忆运行台</h1>
            <p className="subhead">把记忆、主题与调度状态压缩在一张可展开的视图里。</p>
          </div>
          <div className="top-actions">
            <button
              className={`auto-pill ${autoRefresh ? "active" : ""}`}
              onClick={() => setAutoRefresh((value) => !value)}
              aria-pressed={autoRefresh}
            >
              <span />30 秒自动刷新
            </button>
            <div className={`connection-pill ${serviceHealthy ? "online" : ""}`}>
              <span />
              {serviceLabel}
            </div>
            <button className="refresh-button" onClick={() => void load()} disabled={loading}>
              {loading ? "同步中" : "刷新数据"}
            </button>
          </div>
        </header>

        <nav className="home-shortcuts" aria-label="常用功能">
          <Link className="home-shortcut upload" href="/upload">
            <span>01</span>
            <div>
              <strong>上传内容</strong>
              <small>导入图片、文字、Markdown 和视频</small>
            </div>
            <b aria-hidden="true">进入 ↗</b>
          </Link>
          <Link className="home-shortcut runtime" href="/runtime">
            <span>02</span>
            <div>
              <strong>记忆交互</strong>
              <small>写入内容、对话并搜索已有记忆</small>
            </div>
            <b aria-hidden="true">进入 ↗</b>
          </Link>
          <Link className="home-shortcut topics" href="/topics">
            <span>03</span>
            <div>
              <strong>查看 Topic</strong>
              <small>查看当前主题、生成依据和历史版本</small>
            </div>
            <b aria-hidden="true">进入 ↗</b>
          </Link>
          <Link className="home-shortcut memories" href="/#memories">
            <span>04</span>
            <div>
              <strong>记忆索引</strong>
              <small>浏览、筛选和管理已经保存的记忆</small>
            </div>
            <b aria-hidden="true">进入 ↓</b>
          </Link>
        </nav>

        {error && <div className="error-banner">{error}</div>}
        {deleteNotice && (
          <div className={`delete-notice ${deleteNotice.kind}`} role="status">
            {deleteNotice.text}
            <button type="button" onClick={() => setDeleteNotice(null)} aria-label="关闭提示">×</button>
          </div>
        )}

        <section className="metrics" aria-label="核心指标">
          <article className="metric-card primary">
            <div className="metric-top"><span>记忆总量</span><b>01</b></div>
            <strong>{counts?.memories ?? allMemories.length}</strong>
            <p>当前记忆库中的可检索文本节点</p>
          </article>
          <article className="metric-card">
            <div className="metric-top"><span>偏好 / 技能</span><b>02</b></div>
            <strong>{(counts?.preferences || 0) + (counts?.skills || 0)}</strong>
            <p>{counts?.preferences || 0} 条偏好 · {counts?.skills || 0} 条技能</p>
          </article>
          <article className="metric-card">
            <div className="metric-top"><span>调度队列</span><b>03</b></div>
            <strong>{counts?.queue_total ?? 0}</strong>
            <p>{counts?.queue_running || 0} 运行中 · {counts?.queue_waiting || 0} 等待中</p>
          </article>
          <article className="metric-card accent">
            <div className="metric-top"><span>当前 Topic</span><b>04</b></div>
            <strong>{currentTopics.length}</strong>
            <p>{dashboard ? "滚动 Top 15 席位" : "Topic 状态尚未连接"}</p>
          </article>
        </section>

        <section className="content-grid">
          <div className="memory-panel panel" id="memories">
            <div className="panel-heading memory-heading">
              <div><p className="section-kicker">MEMORY INDEX</p><h2>记忆索引</h2></div>
              <span>{filteredMemories.length} / {allMemories.length} 条</span>
            </div>
            <div className="memory-tools">
              <label className="search-box">
                <span>⌕</span>
                <input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="搜索记忆内容、标签、人物或地点"
                  aria-label="搜索记忆"
                />
              </label>
              <div className="filter-tabs" aria-label="记忆分类">
                {MEMORY_FILTERS.map(([value, label]) => (
                  <button
                    key={value}
                    className={filter === value ? "active" : ""}
                    onClick={() => setFilter(value)}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
            <div className="memory-list">
              {loading && !dashboard ? (
                <div className="empty-state loading-stack"><span /><span /><span /></div>
              ) : filteredMemories.length === 0 ? (
                <div className="empty-state">没有找到符合当前条件的记忆。</div>
              ) : (
                filteredMemories.map((item, index) => (
                  <button className="memory-row" key={item.id} onClick={() => void openMemory(item)}>
                    <div className="memory-index">{String(index + 1).padStart(2, "0")}</div>
                    <span className="memory-copy">
                      <span className="memory-meta">
                        <span className="type-pill">{memoryTypeLabel(item.memory_type)}</span>
                        <span className="source-pill">{sourceLabel(item.source)}</span>
                        <time>{formatTime(item.created_at || undefined)}</time>
                      </span>
                      <strong>{item.title || "未命名记忆"}</strong>
                      <span className="memory-preview">{item.content}</span>
                      <span className="tag-line">
                        {item.tags.slice(0, 4).map((tag: string) => (
                          <span key={tag}>#{tag}</span>
                        ))}
                      </span>
                    </span>
                    <span className="detail-button" aria-hidden="true">↗</span>
                  </button>
                ))
              )}
            </div>
          </div>

          <aside className="side-stack">
            <section className="panel topic-panel" id="topics">
              <div className="panel-heading compact">
                <div><p className="section-kicker">ROLLING TOPIC</p><h2>当前 Topic</h2></div>
                <span>{currentTopics.length} 条</span>
              </div>
              <div className="topic-section">
                <div className="topic-title-line">
                  <p className="topic-label">跨日期滚动席位</p>
                  <span>{dashboard ? "真实数据" : "未连接"}</span>
                </div>
                {currentTopics.length ? (
                  <div className="dashboard-topic-list">
                    {currentTopics.slice(0, 5).map((topic) => (
                      <article key={topic.id}>
                        <strong>{topic.title}</strong>
                        <span>{topic.reason || "暂无生成理由"}</span>
                      </article>
                    ))}
                  </div>
                ) : (
                  <div className="topic-empty">
                    <b>当前没有 Topic</b>
                    <span>记忆经过 Topic 处理并达到入选条件后，会显示在这里。</span>
                  </div>
                )}
              </div>
              <Link className="topic-page-link" href="/topics">查看完整排名、依据和历史版本 <span>↗</span></Link>
            </section>

            <section className="panel runtime-panel" id="runtime">
              <div className="panel-heading compact">
                <div><p className="section-kicker">RUNTIME</p><h2>服务状态</h2></div>
                <span className={serviceHealthy ? "status-good" : "status-bad"}>{serviceHealthy ? "健康" : "异常"}</span>
              </div>
              <dl>
                <div><dt>服务版本</dt><dd>{dashboard?.service_version || "—"}</dd></div>
                <div><dt>用户 / 记忆库</dt><dd>{dashboard?.scope.user_id || "—"} / {dashboard?.scope.cube_id || "—"}</dd></div>
                <div><dt>调度任务</dt><dd>{counts?.queue_total ?? 0}</dd></div>
                <div><dt>最后同步</dt><dd>{formatTime(dashboard?.fetched_at, true)}</dd></div>
              </dl>
            </section>
          </aside>
        </section>
      </section>

      {selectedMemory && (
        <div className="drawer-layer" role="dialog" aria-modal="true" aria-labelledby="memory-detail-title">
          <button className="drawer-backdrop" onClick={() => setSelectedMemory(null)} aria-label="关闭详情" />
          <aside className="detail-drawer">
            <header className="drawer-header">
              <div>
                <p className="section-kicker">MEMORY DETAIL</p>
                <h2 id="memory-detail-title">{selectedMemory.title || "记忆详情"}</h2>
              </div>
              <button className="close-button" onClick={() => setSelectedMemory(null)} aria-label="关闭">×</button>
            </header>
            <div className="drawer-body">
              <div className="detail-badges">
                <span className="type-pill">{memoryTypeLabel(selectedMemory.memory_type)}</span>
                <span className="source-pill">{sourceLabel(selectedMemory.source)}</span>
              </div>
              <section className="detail-section">
                <p>完整记忆</p>
                <blockquote>{selectedMemory.content}</blockquote>
              </section>
              {!!Object.keys(selectedMemory.structured).length && (
                <section className="detail-section">
                  <p>结构化信息</p>
                  <dl className="structured-info-grid">
                    {[
                      ["记录类型", selectedMemory.structured.record_type],
                      ["事件标题", selectedMemory.structured.event_title],
                      ["事件时间", selectedMemory.structured.event_time || selectedMemory.structured.event_start_at],
                      ["事件状态", selectedMemory.structured.event_status],
                      ["行动者", selectedMemory.structured.event_actor],
                      ["行为", selectedMemory.structured.event_action],
                      ["对象", selectedMemory.structured.event_target],
                      ["参与者", selectedMemory.structured.participants],
                      ["地点", selectedMemory.structured.event_location],
                      ["来源记录时间", selectedMemory.structured.source_recorded_at],
                      ["联系人", selectedMemory.structured.contact_name],
                      ["关系", selectedMemory.structured.relations || selectedMemory.structured.relationship],
                    ].filter(([, value]) => value !== null && value !== undefined && value !== "").map(([label, value]) => (
                      <div key={String(label)}><dt>{String(label)}</dt><dd>{toText(value).join("、") || String(value)}</dd></div>
                    ))}
                  </dl>
                </section>
              )}
              <dl className="detail-grid">
                <div><dt>写入时间</dt><dd>{formatTime(selectedMemory.created_at || undefined, true)}</dd></div>
                <div><dt>更新时间</dt><dd>{formatTime(selectedMemory.updated_at || undefined, true)}</dd></div>
                <div><dt>记忆 ID</dt><dd className="mono breakable">{selectedMemory.id}</dd></div>
                <div><dt>置信度</dt><dd>{selectedMemory.confidence ?? "未提供"}</dd></div>
              </dl>
              {selectedMemory.background && (
                <section className="detail-section detail-copy">
                  <p>背景</p><span>{selectedMemory.background}</span>
                </section>
              )}
              {!!selectedMemory.tags.length && (
                <section className="detail-section">
                  <p>标签</p>
                  <div className="detail-tags">
                    {selectedMemory.tags.map((tag: string) => <span key={tag}>#{tag}</span>)}
                  </div>
                </section>
              )}
              <details className="raw-section">
                <summary>查看可展示的结构化信息</summary>
                <pre>{JSON.stringify(selectedMemory.structured, null, 2)}</pre>
              </details>
              <section className="danger-zone">
                <div>
                  <strong>删除这条记忆</strong>
                  <span>确认后会永久删除 MemOS 中的正文、索引和图谱节点，并同步清理 Topic 证据。</span>
                </div>
                <button type="button" onClick={() => askToDelete(selectedMemory)}>永久删除记忆</button>
              </section>
            </div>
          </aside>
        </div>
      )}

      {deleteTarget && (
        <div className="delete-confirm-layer" role="alertdialog" aria-modal="true" aria-labelledby="delete-memory-title" aria-describedby="delete-memory-description">
          <button
            className="delete-confirm-backdrop"
            type="button"
            onClick={() => !deleting && setDeleteTarget(null)}
            aria-label="取消删除"
          />
          <section className="delete-confirm-card">
            <div className="delete-warning-mark" aria-hidden="true">!</div>
            <p className="section-kicker">PERMANENT DELETE</p>
            <h2 id="delete-memory-title">确定永久删除这条记忆吗？</h2>
            <strong>{deleteTarget.title || "未命名记忆"}</strong>
            <p id="delete-memory-description">该操作不能撤销。删除后，这条记忆将无法被搜索、对话或 Topic 再次使用。</p>
            {deleteError && <div className="delete-confirm-error" role="alert">{deleteError}</div>}
            <div className="delete-confirm-actions">
              <button type="button" className="cancel-delete" onClick={() => setDeleteTarget(null)} disabled={deleting}>取消</button>
              <button type="button" className="confirm-delete" onClick={() => void confirmDelete()} disabled={deleting}>
                {deleting ? "正在删除…" : "确认永久删除"}
              </button>
            </div>
          </section>
        </div>
      )}
    </main>
  );
}
