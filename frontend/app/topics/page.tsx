"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowUpRight, LoaderCircle, RefreshCw, Search } from "lucide-react";
import AppRail from "../components/AppRail";
import {
  ApiError,
  appApi,
  MemoryDetail,
  Topic,
  TopicEvidence,
  TopicList,
} from "@/lib/api-client";
import { getTopicMemoryScoreState } from "@/lib/topic-display";

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
    cancelled: "已取消",
    planned: "计划中",
    uncertain: "状态待定",
    unknown: "状态未知",
  };
  return labels[value] || value;
}

function score(value: number) {
  return String(Number(value.toFixed(2)));
}

type ScoreMetric = {
  label: string;
  value: number;
  display: string;
  max?: number;
};

function topicScoreMetrics(topic: Topic): ScoreMetric[] {
  const overall: ScoreMetric = {
    label: "排名分",
    value: topic.score,
    display: `${score(topic.score)} / 100`,
    max: 100,
  };
  const breakdown = topic.score_breakdown;

  if (breakdown.model === "memory_importance_v2") {
    return [
      overall,
      {
        label: "基础分",
        value: breakdown.base_score,
        display: `${score(breakdown.base_score)} / 100`,
        max: 100,
      },
      {
        label: "最强单条",
        value: breakdown.strongest_memory_score,
        display: `${score(breakdown.strongest_memory_score)} / 100`,
        max: 100,
      },
      {
        label: "其他记忆加分",
        value: breakdown.supporting_memory_points,
        display: `+${score(breakdown.supporting_memory_points)}`,
        max: 100,
      },
      {
        label: "新鲜系数",
        value: breakdown.recency_factor * 100,
        display: `${score(breakdown.recency_factor * 100)}%`,
        max: 100,
      },
      {
        label: "参与计分",
        value: breakdown.counted_memory_ids.length,
        display: `${breakdown.counted_memory_ids.length} 条`,
      },
      {
        label: "重复忽略",
        value: breakdown.duplicate_memory_count,
        display: `${score(breakdown.duplicate_memory_count)} 条`,
      },
    ];
  }

  if (breakdown.model === "legacy_evidence_v1") {
    const legacy = [
      { label: "基础", value: breakdown.base_score, max: 100 },
      { label: "证据", value: breakdown.evidence_points, max: 30 },
      { label: "主动", value: breakdown.initiative_points, max: 25 },
      { label: "紧急", value: breakdown.urgency_points, max: 20 },
      { label: "持续", value: breakdown.continuity_points, max: 15 },
      { label: "状态", value: breakdown.status_points, max: 10 },
      {
        label: "新鲜",
        value: breakdown.recency_factor === null ? null : breakdown.recency_factor * 100,
        max: 100,
      },
    ];
    return [
      overall,
      ...legacy
        .filter((item): item is { label: string; value: number; max: number } => item.value !== null)
        .map((item) => ({
          ...item,
          display: `${score(item.value)} / ${item.max}`,
        })),
    ];
  }

  const partial = [
    { label: "基础分", value: breakdown.base_score, max: 100 },
    {
      label: "新鲜系数",
      value: breakdown.recency_factor === null ? null : breakdown.recency_factor * 100,
      max: 100,
    },
  ];
  return [
    overall,
    ...partial
      .filter((item): item is { label: string; value: number; max: number } => item.value !== null)
      .map((item) => ({
        ...item,
        display: `${score(item.value)} / ${item.max}`,
      })),
  ];
}

function evidenceRows(topic: Topic): Array<{ memoryId: string; evidence?: TopicEvidence }> {
  const byId = new Map(topic.evidence.map((item) => [item.memory_id, item]));
  const ids = [...topic.supporting_memory_ids];
  for (const item of topic.evidence) {
    if (!ids.includes(item.memory_id)) ids.push(item.memory_id);
  }
  return ids.map((memoryId) => ({ memoryId, evidence: byId.get(memoryId) }));
}

type EvidencePreview = MemoryDetail | { error: string; missing: boolean };

export default function TopicsPage() {
  const [payload, setPayload] = useState<TopicList | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [reconciling, setReconciling] = useState(false);
  const [notice, setNotice] = useState("");
  const [evidenceMemory, setEvidenceMemory] = useState<EvidencePreview | null>(null);
  const [evidenceLoading, setEvidenceLoading] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const nextPayload = await appApi.topics();
      setPayload(nextPayload);
      setSelectedId((current) =>
        current && !nextPayload.items.some((topic) => topic.id === current) ? null : current,
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取 Topic");
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
      const missing = reason instanceof ApiError && reason.status === 404;
      setEvidenceMemory({
        missing,
        error: missing
          ? "这条原记忆已不存在，或者不在当前记忆库中。请点击页面上方“校准证据”清理失效引用。"
          : reason instanceof Error ? reason.message : "读取证据失败",
      });
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
      return [topic.key, topic.title, topic.reason, ...topic.candidate_reasons]
        .join(" ")
        .toLowerCase()
        .includes(normalized);
    });
  }, [payload, query, status]);

  const selected = useMemo(
    () => payload?.items.find((topic) => topic.id === selectedId) || null,
    [payload, selectedId],
  );
  const selectedEvidence = useMemo(
    () => selected ? evidenceRows(selected) : [],
    [selected],
  );

  const openTopic = (topicId: string) => {
    setSelectedId(topicId);
    setEvidenceMemory(null);
  };

  const closeTopic = () => {
    setSelectedId(null);
    setEvidenceMemory(null);
  };

  const activeCount = (payload?.items || []).filter((item) => item.status === "active").length;
  const suppressedCount = (payload?.items || []).filter((item) => item.status === "suppressed").length;
  const latestEvidence = (payload?.items || []).reduce(
    (latest, item) => (item.last_evidence_at || "") > latest ? item.last_evidence_at || "" : latest,
    "",
  );

  return (
    <main className="shell page-shell">
      <AppRail active="topics" />
      <section className="workspace inner-page">
        <header className="page-header">
          <div>
            <p className="eyebrow">ROLLING TOPIC STORE</p>
            <h1>滚动 Topic</h1>
            <p className="subhead">通过应用后端读取 Topic 当前状态，查看入选理由、计分记忆和历史版本。</p>
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
            <strong>Topic 加载失败</strong>
            <span>{error}</span>
            <code>数据来源：8011 应用后端 /api/v1/topics</code>
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
          {!loading && !error && topics.length === 0 && <div className="empty-state panel">当前没有符合条件的 Topic。</div>}
          <div className="topic-card-grid rolling-grid">
            {topics.map((topic, index) => {
              const counted = topic.score_breakdown.model === "memory_importance_v2"
                ? topic.score_breakdown.counted_memory_ids.length
                : topic.evidence.length;
              return (
                <button className="topic-card" key={topic.id} onClick={() => openTopic(topic.id)}>
                  <div className="topic-card-top">
                    <span className={`lifecycle ${topic.status}`}>{statusLabel(topic.status)}</span>
                    <b>#{String(index + 1).padStart(2, "0")}</b>
                  </div>
                  <h2>{topic.title}</h2>
                  <p>{topic.reason}</p>
                  <div className="topic-score-row">
                    <span>排名分 <b>{score(topic.score)}</b></span>
                    <span>计分记忆 <b>{counted}</b></span>
                    <span>版本 <b>v{topic.version ?? "—"}</b></span>
                  </div>
                  <div className="topic-card-foot"><code>{topic.key}</code><ArrowUpRight size={16} /></div>
                </button>
              );
            })}
          </div>
        </section>
      </section>

      {selected && (
        <div className="drawer-layer" role="dialog" aria-modal="true" aria-labelledby="topic-detail-title">
          <button className="drawer-backdrop" onClick={closeTopic} aria-label="关闭详情" />
          <aside className="detail-drawer topic-drawer">
            <header className="drawer-header">
              <div><p className="section-kicker">TOPIC DETAIL</p><h2 id="topic-detail-title">{selected.title}</h2></div>
              <button className="close-button" onClick={closeTopic} aria-label="关闭">×</button>
            </header>
            <div className="drawer-body">
              <div className="detail-badges">
                <span className={`lifecycle ${selected.status}`}>{statusLabel(selected.status)}</span>
                <span className="source-pill">最近证据 {formatTime(selected.last_evidence_at || undefined)}</span>
                <span className="type-pill">{statusLabel(selected.progress)}</span>
                <span className="type-pill">
                  {selected.score_breakdown.model === "memory_importance_v2"
                    ? "重要性评分"
                    : selected.score_breakdown.model === "legacy_evidence_v1" ? "历史评分" : "评分信息不完整"}
                </span>
              </div>
              <section className="detail-section">
                <p>生成理由</p><blockquote>{selected.reason}</blockquote>
              </section>
              <section className="score-grid">
                {topicScoreMetrics(selected).map((item) => {
                  const width = item.max ? percent((item.value / item.max) * 100) : 0;
                  return (
                    <div key={item.label}>
                      <span>{item.label}</span>
                      <strong>{item.display}</strong>
                      {item.max && <i style={{ width: `${width}%` }} />}
                    </div>
                  );
                })}
              </section>
              {selected.score_breakdown.model === "memory_importance_v2" && (
                <p className="score-explanation">
                  基础分取最强单条记忆，并叠加其他参与计分记忆的半权分；排名分再乘以新鲜系数。
                </p>
              )}
              {selected.score_breakdown.model === "partial" && (
                <p className="score-explanation warning">
                  这条 Topic 只有部分评分字段，页面不会把缺失字段显示成 0。
                </p>
              )}
              <dl className="topic-time-grid">
                <div><dt>首次出现</dt><dd>{formatTime(selected.first_seen_at || undefined)}</dd></div>
                <div><dt>最近证据</dt><dd>{formatTime(selected.last_evidence_at || undefined)}</dd></div>
              </dl>
              {!!selected.candidate_reasons.length && (
                <section className="detail-section candidate-section">
                  <p>为什么进入候选池</p>
                  <ul>{selected.candidate_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
                </section>
              )}
              <section className="detail-section evidence-section">
                <p>来源记忆 · {selected.supporting_memory_ids.length} 条</p>
                {selectedEvidence.map(({ memoryId, evidence }) => {
                  const scoring = getTopicMemoryScoreState(selected.score_breakdown, memoryId);
                  return (
                    <button type="button" onClick={() => void openEvidence(memoryId)} key={memoryId}>
                      <code>{memoryId}</code>
                      <strong>{evidence?.fact || "这条来源记忆没有保存单独的事实说明。"}</strong>
                      <span>{evidence?.contribution || scoring.description}</span>
                      <span className="evidence-score-line">
                        {scoring.label}
                        {typeof scoring.score === "number" ? ` · 单条重要性 ${score(scoring.score)} / 100` : ""}
                      </span>
                      {evidenceLoading === memoryId ? <LoaderCircle className="spin" size={14} /> : <i>查看原记忆 ↗</i>}
                    </button>
                  );
                })}
                {!selectedEvidence.length && <div className="runtime-empty">这条 Topic 没有返回来源记忆。</div>}
                {evidenceMemory && (
                  <article className={`evidence-memory-preview ${"error" in evidenceMemory && evidenceMemory.missing ? "missing" : ""}`}>
                    <code>{"error" in evidenceMemory ? "证据状态" : evidenceMemory.id}</code>
                    <strong>{"error" in evidenceMemory ? evidenceMemory.error : evidenceMemory.content}</strong>
                  </article>
                )}
              </section>
              {!!selected.versions.length && (
                <details className="topic-history">
                  <summary>查看历史版本 · {selected.versions.length}</summary>
                  <div>
                    {[...selected.versions].reverse().map((version, index) => (
                      <article key={`${version.version ?? index}-${version.updated_at || index}`}>
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
