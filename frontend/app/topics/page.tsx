"use client";

import { ArrowUpRight, RefreshCw, Search } from "lucide-react";
import { type ReactElement, useCallback, useEffect, useMemo, useState } from "react";

import { appApi } from "@/lib/api-client.ts";
import type { Topic, TopicList } from "@/lib/api-client.ts";
import {
  filterTopicQueues,
  formatTopicScore,
  formatTopicTime,
  getTopicAttentionStatusLabel,
  getTopicCandidateSourceLabel,
  getTopicStatusLabel,
  partitionTopicQueues,
} from "@/lib/topic-display.ts";
import AppRail from "../components/AppRail.tsx";
import TopicDetailDrawer from "./TopicDetailDrawer.tsx";

function countedMemoryCount(topic: Topic): number {
  if (
    topic.score_breakdown.model === "static_importance_v3" ||
    topic.score_breakdown.model === "memory_importance_v2"
  ) {
    return topic.score_breakdown.counted_memory_ids.length;
  }
  return topic.evidence.length;
}

export default function TopicsPage(): ReactElement {
  const [payload, setPayload] = useState<TopicList | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<"all" | Topic["status"]>("all");
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
    return filterTopicQueues(payload?.items || [], query, status);
  }, [payload, query, status]);

  const queues = useMemo(() => partitionTopicQueues(topics), [topics]);

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
          <div><span>核心 Topic</span><strong>{payload?.core_count ?? 0} / 3</strong></div>
          <div><span>可见候选</span><strong>{payload?.visible_candidate_count ?? 0} / 27</strong></div>
          <div><span>隐藏候选</span><strong>{payload?.hidden_candidate_count ?? 0}</strong></div>
          <div><span>最近重排</span><strong className="summary-time">{formatTopicTime(payload?.calculated_at)}</strong></div>
        </section>

        <section className="topic-toolbar panel">
          <label className="search-box topic-search">
            <Search size={15} />
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索 Topic、理由或候选依据" />
          </label>
          <label className="date-select">
            <select
              value={status}
              onChange={(event) => setStatus(event.target.value as "all" | Topic["status"])}
              aria-label="按 Topic 状态筛选"
            >
              <option value="all">全部状态</option>
              <option value="active">当前席位</option>
              <option value="suppressed">候选保留</option>
            </select>
          </label>
          <span>{topics.length} 条结果</span>
        </section>

        <section className="topic-groups">
          {!loading && !error && topics.length === 0 && <div className="empty-state panel">当前没有符合条件的 Topic。</div>}
          {(status === "all" || status === "active") && (
            <section className="topic-queue-section core-queue-section">
              <header className="topic-queue-heading">
                <div><p>CORE TOPICS</p><h2>今天最重要的 Topic</h2></div>
                <span>{queues.core.length} / 3</span>
              </header>
              {!loading && !error && queues.core.length === 0 && (
                <div className="topic-lane-empty panel">当前没有达到核心席位要求的 Topic。</div>
              )}
              <div className="topic-card-grid core-topic-grid">
                {queues.core.map((topic) => {
                  const counted = countedMemoryCount(topic);
                  return (
                    <button className="topic-card core-topic-card" key={topic.id} onClick={() => openTopic(topic.id)}>
                      <div className="topic-card-top">
                        <span className={`lifecycle ${topic.status}`}>{getTopicStatusLabel(topic.status)}</span>
                        <b>核心 #{String(topic.queue_rank).padStart(2, "0")}</b>
                      </div>
                      <h2>{topic.title}</h2>
                      <p>{topic.reason}</p>
                      <div className="queue-score-formula">
                        <strong>队列分 {formatTopicScore(topic.queue_score)}</strong>
                        <span>
                          重要度 {formatTopicScore(topic.importance_score)} ＋ 临近 {formatTopicScore(topic.approaching_bonus)} － 衰减 {formatTopicScore(topic.decay_penalty)}
                        </span>
                      </div>
                      <div className="topic-score-row">
                        <span>计分记忆 <b>{counted}</b></span>
                        <span>版本 <b>v{topic.version ?? "—"}</b></span>
                      </div>
                      <div className="topic-card-foot"><code>{topic.key}</code><ArrowUpRight size={16} /></div>
                    </button>
                  );
                })}
              </div>
            </section>
          )}

          {(status === "all" || status === "suppressed") && (
            <section className="topic-queue-section candidate-queue-section">
              <header className="topic-queue-heading">
                <div><p>CANDIDATE TOPICS</p><h2>候选 Topic</h2></div>
                <span>{queues.candidates.length} / 27</span>
              </header>
              {!loading && !error && queues.candidates.length === 0 && (
                <div className="topic-lane-empty panel">当前没有符合筛选条件的候选 Topic。</div>
              )}
              <div className="topic-card-grid candidate-topic-grid">
                {queues.candidates.map((topic) => {
              const counted = countedMemoryCount(topic);
              return (
                    <button className="topic-card candidate-topic-card" key={topic.id} onClick={() => openTopic(topic.id)}>
                  <div className="topic-card-top">
                        <div className="candidate-badges">
                          <span className={`candidate-source ${topic.candidate_source || "core"}`}>
                            {getTopicCandidateSourceLabel(topic.candidate_source)}
                          </span>
                          {topic.attention_status === "past_unconfirmed" && (
                            <span className="attention-status past-unconfirmed">
                              {getTopicAttentionStatusLabel(topic.attention_status)}
                            </span>
                          )}
                        </div>
                        <b>候选 #{String(topic.queue_rank).padStart(2, "0")}</b>
                  </div>
                  <h2>{topic.title}</h2>
                  <p>{topic.reason}</p>
                      <div className="queue-score-formula compact">
                        <strong>队列分 {formatTopicScore(topic.queue_score)}</strong>
                        <span>
                          {formatTopicScore(topic.importance_score)} ＋ {formatTopicScore(topic.approaching_bonus)} － {formatTopicScore(topic.decay_penalty)}
                        </span>
                      </div>
                  <div className="topic-score-row">
                    <span>计分记忆 <b>{counted}</b></span>
                    <span>版本 <b>v{topic.version ?? "—"}</b></span>
                  </div>
                  <div className="topic-card-foot"><code>{topic.key}</code><ArrowUpRight size={16} /></div>
                </button>
              );
                })}
              </div>
              {!!payload?.hidden_candidate_count && !query.trim() && (
                <p className="hidden-candidate-note">
                  另有 {payload.hidden_candidate_count} 个候选仍保存在池中，会在分数上升后重新进入可见候选。
                </p>
              )}
            </section>
          )}
        </section>
      </section>

      {selected && (
        <TopicDetailDrawer key={selected.id} topic={selected} onClose={closeTopic} />
      )}
    </main>
  );
}
