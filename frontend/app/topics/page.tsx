"use client";

import { ArrowUpRight, RefreshCw, Search } from "lucide-react";
import { type ReactElement, useCallback, useEffect, useMemo, useState } from "react";

import { appApi } from "@/lib/api-client.ts";
import type { Topic, TopicList } from "@/lib/api-client.ts";
import {
  formatTopicScore,
  formatTopicTime,
  getTopicStatusLabel,
} from "@/lib/topic-display.ts";
import AppRail from "../components/AppRail.tsx";
import TopicDetailDrawer from "./TopicDetailDrawer.tsx";

function countedMemoryCount(topic: Topic): number {
  if (topic.score_breakdown.model === "memory_importance_v2") {
    return topic.score_breakdown.counted_memory_ids.length;
  }
  return topic.evidence.length;
}

export default function TopicsPage(): ReactElement {
  const [payload, setPayload] = useState<TopicList | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [reconciling, setReconciling] = useState(false);
  const [notice, setNotice] = useState("");

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
  function openTopic(topicId: string): void {
    setSelectedId(topicId);
  }

  function closeTopic(): void {
    setSelectedId(null);
  }

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
            <p className="subhead">查看 Topic 当前状态、入选理由、计分记忆和历史版本。</p>
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
          <div><span>最近证据</span><strong className="summary-time">{latestEvidence ? formatTopicTime(latestEvidence) : "—"}</strong></div>
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
              const counted = countedMemoryCount(topic);
              return (
                <button className="topic-card" key={topic.id} onClick={() => openTopic(topic.id)}>
                  <div className="topic-card-top">
                    <span className={`lifecycle ${topic.status}`}>{getTopicStatusLabel(topic.status)}</span>
                    <b>#{String(index + 1).padStart(2, "0")}</b>
                  </div>
                  <h2>{topic.title}</h2>
                  <p>{topic.reason}</p>
                  <div className="topic-score-row">
                    <span>排名分 <b>{formatTopicScore(topic.score)}</b></span>
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
        <TopicDetailDrawer key={selected.id} topic={selected} onClose={closeTopic} />
      )}
    </main>
  );
}
