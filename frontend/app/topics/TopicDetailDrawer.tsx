import { LoaderCircle } from "lucide-react";
import { type ReactElement, useEffect, useMemo, useState } from "react";

import { ApiError, appApi } from "@/lib/api-client.ts";
import type {
  MemoryDetail,
  Topic,
  TopicEvidence,
  TopicSelectionTrace,
} from "@/lib/api-client.ts";
import {
  formatTopicScore,
  formatTopicTime,
  getTopicMemoryScoreState,
  getTopicStatusLabel,
} from "@/lib/topic-display.ts";
import TopicProcessTrace from "./TopicProcessTrace.tsx";

type TopicDetailDrawerProps = {
  topic: Topic;
  onClose: () => void;
};

type ScoreMetric = {
  label: string;
  value: number;
  display: string;
  max?: number;
};

type EvidenceRow = {
  memoryId: string;
  evidence?: TopicEvidence;
};

type EvidenceError = {
  error: string;
  missing: boolean;
};

type EvidencePreview = MemoryDetail | EvidenceError;

type EvidenceMemoryPreviewProps = {
  preview: EvidencePreview;
};

function percent(value: number): number {
  return Math.round(Math.max(0, Math.min(100, value)));
}

function boundedScoreMetric(label: string, value: number, max = 100): ScoreMetric {
  return {
    label,
    value,
    display: `${formatTopicScore(value)} / ${max}`,
    max,
  };
}

function appendScoreMetric(
  metrics: ScoreMetric[],
  label: string,
  value: number | null,
  max = 100,
): void {
  if (value === null) {
    return;
  }
  metrics.push(boundedScoreMetric(label, value, max));
}

function topicScoreMetrics(topic: Topic): ScoreMetric[] {
  const overall = boundedScoreMetric("排名分", topic.score);
  const breakdown = topic.score_breakdown;

  switch (breakdown.model) {
    case "memory_importance_v2": {
      const recencyPercent = breakdown.recency_factor * 100;
      return [
        overall,
        boundedScoreMetric("基础分", breakdown.base_score),
        boundedScoreMetric("最强单条", breakdown.strongest_memory_score),
        {
          label: "其他记忆加分",
          value: breakdown.supporting_memory_points,
          display: `+${formatTopicScore(breakdown.supporting_memory_points)}`,
          max: 100,
        },
        {
          label: "新鲜系数",
          value: recencyPercent,
          display: `${formatTopicScore(recencyPercent)}%`,
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
          display: `${formatTopicScore(breakdown.duplicate_memory_count)} 条`,
        },
      ];
    }
    case "legacy_evidence_v1": {
      const metrics: ScoreMetric[] = [overall];
      appendScoreMetric(metrics, "基础", breakdown.base_score);
      appendScoreMetric(metrics, "证据", breakdown.evidence_points, 30);
      appendScoreMetric(metrics, "主动", breakdown.initiative_points, 25);
      appendScoreMetric(metrics, "紧急", breakdown.urgency_points, 20);
      appendScoreMetric(metrics, "持续", breakdown.continuity_points, 15);
      appendScoreMetric(metrics, "状态", breakdown.status_points, 10);
      if (breakdown.recency_factor !== null) {
        metrics.push(boundedScoreMetric("新鲜", breakdown.recency_factor * 100));
      }
      return metrics;
    }
    case "partial": {
      const metrics: ScoreMetric[] = [overall];
      appendScoreMetric(metrics, "基础分", breakdown.base_score);
      if (breakdown.recency_factor !== null) {
        metrics.push(boundedScoreMetric("新鲜系数", breakdown.recency_factor * 100));
      }
      return metrics;
    }
  }
}

function evidenceRows(topic: Topic): EvidenceRow[] {
  const evidenceById = new Map(topic.evidence.map((item) => [item.memory_id, item]));
  const memoryIds = [...topic.supporting_memory_ids];
  const knownIds = new Set(memoryIds);
  for (const evidence of topic.evidence) {
    if (!knownIds.has(evidence.memory_id)) {
      memoryIds.push(evidence.memory_id);
      knownIds.add(evidence.memory_id);
    }
  }
  return memoryIds.map((memoryId) => ({ memoryId, evidence: evidenceById.get(memoryId) }));
}

function scoreModelLabel(topic: Topic): string {
  switch (topic.score_breakdown.model) {
    case "memory_importance_v2":
      return "重要性评分";
    case "legacy_evidence_v1":
      return "历史评分";
    case "partial":
      return "评分信息不完整";
  }
}

function evidenceError(reason: unknown): EvidenceError {
  const missing = reason instanceof ApiError && reason.status === 404;
  if (missing) {
    return {
      missing: true,
      error: "这条原记忆已不存在，或者不在当前记忆库中。请点击页面上方“校准证据”清理失效引用。",
    };
  }
  return {
    missing: false,
    error: reason instanceof Error ? reason.message : "读取证据失败",
  };
}

function EvidenceMemoryPreview({ preview }: EvidenceMemoryPreviewProps): ReactElement {
  if ("error" in preview) {
    return (
      <article className={`evidence-memory-preview ${preview.missing ? "missing" : ""}`}>
        <code>证据状态</code>
        <strong>{preview.error}</strong>
      </article>
    );
  }
  return (
    <article className="evidence-memory-preview">
      <code>{preview.id}</code>
      <strong>{preview.content}</strong>
    </article>
  );
}

export default function TopicDetailDrawer({
  topic,
  onClose,
}: TopicDetailDrawerProps): ReactElement {
  const [trace, setTrace] = useState<TopicSelectionTrace | null>(null);
  const [traceLoading, setTraceLoading] = useState(true);
  const [traceError, setTraceError] = useState("");
  const [evidenceMemory, setEvidenceMemory] = useState<EvidencePreview | null>(null);
  const [evidenceLoading, setEvidenceLoading] = useState("");

  useEffect(() => {
    let activeRequest = true;
    void appApi
      .topicTrace(topic.id)
      .then((result) => {
        if (activeRequest) {
          setTrace(result);
        }
      })
      .catch((reason: unknown) => {
        if (activeRequest) {
          setTraceError(reason instanceof Error ? reason.message : "无法读取 Topic 形成过程");
        }
      })
      .finally(() => {
        if (activeRequest) {
          setTraceLoading(false);
        }
      });
    return () => {
      activeRequest = false;
    };
  }, [topic.id]);

  const evidence = useMemo(() => evidenceRows(topic), [topic]);

  function openEvidence(memoryId: string): void {
    setEvidenceLoading(memoryId);
    setEvidenceMemory(null);
    void appApi
      .memory(memoryId)
      .then((result) => setEvidenceMemory(result.memory))
      .catch((reason: unknown) => setEvidenceMemory(evidenceError(reason)))
      .finally(() => setEvidenceLoading(""));
  }

  return (
    <div className="drawer-layer" role="dialog" aria-modal="true" aria-labelledby="topic-detail-title">
      <button className="drawer-backdrop" onClick={onClose} aria-label="关闭详情" />
      <aside className="detail-drawer topic-drawer">
        <header className="drawer-header">
          <div>
            <p className="section-kicker">TOPIC DETAIL</p>
            <h2 id="topic-detail-title">{topic.title}</h2>
          </div>
          <button className="close-button" onClick={onClose} aria-label="关闭">×</button>
        </header>
        <div className="drawer-body">
          <div className="detail-badges">
            <span className={`lifecycle ${topic.status}`}>{getTopicStatusLabel(topic.status)}</span>
            <span className="source-pill">最近证据 {formatTopicTime(topic.last_evidence_at)}</span>
            <span className="type-pill">{getTopicStatusLabel(topic.progress)}</span>
            <span className="type-pill">{scoreModelLabel(topic)}</span>
          </div>

          <TopicProcessTrace
            trace={trace}
            loading={traceLoading}
            error={traceError}
          />

          <section className="detail-section topic-result-section">
            <p>当前 Topic 生成理由</p>
            <blockquote>{topic.reason}</blockquote>
          </section>
          <section className="score-grid">
            {topicScoreMetrics(topic).map((metric) => {
              const width = metric.max ? percent((metric.value / metric.max) * 100) : null;
              return (
                <div key={metric.label}>
                  <span>{metric.label}</span>
                  <strong>{metric.display}</strong>
                  {width !== null && <i style={{ width: `${width}%` }} />}
                </div>
              );
            })}
          </section>
          {topic.score_breakdown.model === "memory_importance_v2" && (
            <p className="score-explanation">
              基础分取最强单条记忆，并叠加其他参与计分记忆的半权分；排名分再乘以新鲜系数。
            </p>
          )}
          {topic.score_breakdown.model === "partial" && (
            <p className="score-explanation warning">
              这条 Topic 只有部分评分字段，页面不会把缺失字段显示成 0。
            </p>
          )}
          <dl className="topic-time-grid">
            <div><dt>首次出现</dt><dd>{formatTopicTime(topic.first_seen_at)}</dd></div>
            <div><dt>最近证据</dt><dd>{formatTopicTime(topic.last_evidence_at)}</dd></div>
          </dl>
          {!!topic.candidate_reasons.length && (
            <section className="detail-section candidate-section">
              <p>为什么进入候选池</p>
              <ul>{topic.candidate_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
            </section>
          )}
          <section className="detail-section evidence-section">
            <p>最终总结引用的记忆 · {topic.supporting_memory_ids.length} 条</p>
            {evidence.map(({ memoryId, evidence: topicEvidence }) => {
              const scoring = getTopicMemoryScoreState(topic.score_breakdown, memoryId);
              return (
                <button type="button" onClick={() => openEvidence(memoryId)} key={memoryId}>
                  <code>{memoryId}</code>
                  <strong>{topicEvidence?.fact || "这条来源记忆没有保存单独的事实说明。"}</strong>
                  <span>{topicEvidence?.contribution || scoring.description}</span>
                  <span className="evidence-score-line">
                    {scoring.label}
                    {typeof scoring.score === "number"
                      ? ` · 单条重要性 ${formatTopicScore(scoring.score)} / 100`
                      : ""}
                  </span>
                  {evidenceLoading === memoryId
                    ? <LoaderCircle className="spin" size={14} />
                    : <i>查看原记忆 ↗</i>}
                </button>
              );
            })}
            {!evidence.length && <div className="runtime-empty">这条 Topic 没有返回来源记忆。</div>}
            {evidenceMemory && <EvidenceMemoryPreview preview={evidenceMemory} />}
          </section>
          {!!topic.versions.length && (
            <details className="topic-history">
              <summary>查看历史版本 · {topic.versions.length}</summary>
              <div>
                {[...topic.versions].reverse().map((version, index) => (
                  <article key={`${version.version ?? index}-${version.updated_at || index}`}>
                    <b>v{version.version ?? "—"}</b>
                    <span>{version.title || "旧版快照"}</span>
                    <time>{formatTopicTime(version.updated_at)}</time>
                  </article>
                ))}
              </div>
            </details>
          )}
        </div>
      </aside>
    </div>
  );
}
