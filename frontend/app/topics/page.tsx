"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowUpRight, LoaderCircle, RefreshCw, Search } from "lucide-react";
import AppRail from "../components/AppRail";
import { appApi, MemoryDetail, Topic, TopicList } from "@/lib/api-client";

function percent(value: number | undefined) {
  return Math.round(Math.max(0, Math.min(100, value || 0)));
}

function formatTime(value?: string) {
  if (!value) return "时间未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function statusLabel(value: string) {
  const labels: Record<string, string> = {
    active: "当前席位",
    suppressed: "候选保留",
    retired: "已结束",
    ongoing: "进行中",
    completed: "已完成",
    planned: "计划中",
    uncertain: "状态待定",
  };
  return labels[value] || value;
}

export default function TopicsPage() {
  const [payload, setPayload] = useState<TopicList | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [selected, setSelected] = useState<Topic | null>(null);
  const [reconciling, setReconciling] = useState(false);
  const [notice, setNotice] = useState("");
  const [evidenceMemory, setEvidenceMemory] = useState<MemoryDetail | { error: string } | null>(null);
  const [evidenceLoading, setEvidenceLoading] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setPayload(await appApi.topics());
    } catch {
      setError("无法读取外部 Topic 状态文件，请先启动本地连接服务。");
    } finally {
      setLoading(false);
    }
  }, []);

  const reconcile = useCallback(async () => {
    setReconciling(true);
    setError("");
    setNotice("");
    try {
      const result = await appApi.reconcileTopics();
      setNotice(result.removed_memories
        ? `已移除 ${result.removed_memories} 条失效的 Topic 证据。`
        : "Topic 证据与 MemOS 当前数据一致。");
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Topic 对账失败");
    } finally {
      setReconciling(false);
    }
  }, [load]);

  const openEvidence = async (memoryId: string) => {
    setEvidenceLoading(memoryId);
    setEvidenceMemory(null);
    try {
      const result = await appApi.memory(memoryId);
      setEvidenceMemory(result.memory);
    } catch (reason) {
      setEvidenceMemory({ error: reason instanceof Error ? reason.message : "读取证据失败" });
    } finally {
      setEvidenceLoading("");
    }
  };

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const topics = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return (payload?.items || []).filter((topic) => {
      if (status !== "all" && topic.status !== status) return false;
      if (!normalized) return true;
      return [topic.key, topic.title, topic.reason, ...(topic.candidate_reasons || [])]
        .join(" ")
        .toLowerCase()
        .includes(normalized);
    });
  }, [payload, query, status]);

  const activeCount = (payload?.items || []).filter((item) => item.status === "active").length;
  const suppressedCount = (payload?.items || []).filter((item) => item.status === "suppressed").length;
  const latestEvidence = (payload?.items || []).reduce(
    (latest, item) => (item.last_evidence_at || "") > latest ? item.last_evidence_at || "" : latest,
    "",
  );

  return (
    <main className="shell page-shell">
      <AppRail active="topics" serviceHealthy={!error} />
      <section className="workspace inner-page">
        <header className="page-header">
          <div>
            <p className="eyebrow">ROLLING TOPIC STORE</p>
            <h1>滚动 Topic</h1>
            <p className="subhead">直接读取外部 JSON 快照，跨日期维护当前 15 个席位和候选 Topic。</p>
          </div>
          <div className="header-button-group">
            <button className="secondary-action" onClick={() => void reconcile()} disabled={reconciling}>
              <RefreshCw size={14} className={reconciling ? "spin" : ""} />
              {reconciling ? "正在对账" : "校准证据"}
            </button>
            <button className="refresh-button" onClick={() => void load()} disabled={loading}>
              <RefreshCw size={14} className={loading ? "spin" : ""} />
              {loading ? "读取中" : "刷新 Topic"}
            </button>
          </div>
        </header>

        {error && (
          <div className="service-callout error">
            <strong>Topic 连接服务尚未启动</strong>
            <span>{error}</span>
            <code>请检查服务器服务状态，或联系管理员</code>
          </div>
        )}
        {notice && <div className="service-callout success"><span>{notice}</span></div>}

        <section className="topic-summary-strip">
          <div><span>当前池</span><strong>{payload?.total ?? 0}</strong></div>
          <div><span>当前席位</span><strong>{activeCount}</strong></div>
          <div><span>候选保留</span><strong>{suppressedCount}</strong></div>
          <div><span>最近证据</span><strong className="summary-time">{latestEvidence ? formatTime(latestEvidence) : "—"}</strong></div>
        </section>

        <section className="topic-toolbar panel">
          <label className="search-box topic-search">
            <Search size={15} />
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索 Topic、理由或候选依据" />
          </label>
          <label className="date-select">
            <select value={status} onChange={(event) => setStatus(event.target.value)} aria-label="按 Topic 状态筛选">
              <option value="all">全部状态</option>
              <option value="active">当前席位</option>
              <option value="suppressed">候选保留</option>
            </select>
          </label>
          <span>{topics.length} 条结果</span>
        </section>

        <section className="topic-groups">
          {!loading && !error && topics.length === 0 && <div className="empty-state panel">没有找到 Topic。</div>}
          <div className="topic-card-grid rolling-grid">
            {topics.map((topic, index) => (
              <button className="topic-card" key={topic.id} onClick={() => setSelected(topic)}>
                <div className="topic-card-top">
                  <span className={`lifecycle ${topic.status}`}>{statusLabel(topic.status)}</span>
                  <b>#{String(index + 1).padStart(2, "0")}</b>
                </div>
                <h2>{topic.title}</h2>
                <p>{topic.reason}</p>
                <div className="topic-score-row">
                  <span>综合分 <b>{percent(topic.score)}</b></span>
                  <span>证据 <b>{topic.supporting_memory_ids.length}</b></span>
                  <span>版本 <b>v{topic.version}</b></span>
                </div>
                <div className="topic-card-foot"><code>{topic.key}</code><ArrowUpRight size={16} /></div>
              </button>
            ))}
          </div>
        </section>
      </section>

      {selected && (
        <div className="drawer-layer" role="dialog" aria-modal="true" aria-labelledby="topic-detail-title">
          <button className="drawer-backdrop" onClick={() => setSelected(null)} aria-label="关闭详情" />
          <aside className="detail-drawer topic-drawer">
            <header className="drawer-header">
              <div><p className="section-kicker">TOPIC DETAIL</p><h2 id="topic-detail-title">{selected.title}</h2></div>
              <button className="close-button" onClick={() => setSelected(null)} aria-label="关闭">×</button>
            </header>
            <div className="drawer-body">
              <div className="detail-badges">
                <span className={`lifecycle ${selected.status}`}>{statusLabel(selected.status)}</span>
                <span className="source-pill">最近证据 {formatTime(selected.last_evidence_at || undefined)}</span>
                <span className="type-pill">{statusLabel(selected.progress)}</span>
              </div>
              <section className="detail-section">
                <p>生成理由</p><blockquote>{selected.reason}</blockquote>
              </section>
              <section className="score-grid">
                {[
                  { label: "综合", value: selected.score, max: 100 },
                  { label: "基础", value: selected.score_breakdown?.base_score || 0, max: 100 },
                  { label: "证据", value: selected.score_breakdown?.evidence_points || 0, max: 30 },
                  { label: "主动", value: selected.score_breakdown?.initiative_points || 0, max: 25 },
                  { label: "紧急", value: selected.score_breakdown?.urgency_points || 0, max: 20 },
                  { label: "持续", value: selected.score_breakdown?.continuity_points || 0, max: 15 },
                  { label: "状态", value: selected.score_breakdown?.status_points || 0, max: 10 },
                  { label: "新鲜", value: (selected.score_breakdown?.recency_factor || 0) * 100, max: 100 },
                ].map((item) => {
                  const width = percent((item.value / item.max) * 100);
                  return <div key={item.label}><span>{item.label}</span><strong>{Number(item.value.toFixed(2))}<small> / {item.max}</small></strong><i style={{ width: `${width}%` }} /></div>;
                })}
              </section>
              <dl className="topic-time-grid">
                <div><dt>首次出现</dt><dd>{formatTime(selected.first_seen_at || undefined)}</dd></div>
                <div><dt>最近证据</dt><dd>{formatTime(selected.last_evidence_at || undefined)}</dd></div>
              </dl>
              <section className="detail-section candidate-section">
                <p>为什么进入候选池</p>
                <ul>{(selected.candidate_reasons || []).map((reason) => <li key={reason}>{reason}</li>)}</ul>
              </section>
              <section className="detail-section evidence-section">
                <p>记忆证据 · {selected.evidence.length} 条</p>
                {selected.evidence.map((evidence) => (
                  <button type="button" onClick={() => void openEvidence(evidence.memory_id)} key={`${evidence.memory_id}-${evidence.fact}`}>
                    <code>{evidence.memory_id}</code>
                    <strong>{evidence.fact}</strong>
                    <span>{evidence.contribution}</span>
                    {evidenceLoading === evidence.memory_id ? <LoaderCircle className="spin" size={14} /> : <i>查看原记忆 ↗</i>}
                  </button>
                ))}
                {evidenceMemory && (
                  <article className="evidence-memory-preview">
                    <code>{"error" in evidenceMemory ? "证据详情" : evidenceMemory.id}</code>
                    <strong>{"error" in evidenceMemory ? evidenceMemory.error : evidenceMemory.content}</strong>
                  </article>
                )}
              </section>
              {!!selected.versions?.length && (
                <details className="topic-history">
                  <summary>查看历史版本 · {selected.versions.length}</summary>
                  <div>
                    {[...selected.versions].reverse().map((version, index) => (
                      <article key={`${version.version || index}-${version.updated_at || index}`}>
                        <b>v{version.version ?? "—"}</b>
                        <span>{version.title || "旧版快照"}</span>
                        <time>{formatTime(version.updated_at || undefined)}</time>
                      </article>
                    ))}
                  </div>
                </details>
              )}
            </div>
          </aside>
        </div>
      )}
    </main>
  );
}
