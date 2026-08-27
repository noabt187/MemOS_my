import { AlertCircle, CircleDashed, LoaderCircle } from "lucide-react";
import type { ReactElement } from "react";

import type {
  TopicSelectionTrace,
  TopicTraceDimension,
  TopicTraceMemory,
  TopicTraceRubric,
} from "@/lib/api-client.ts";
import {
  formatTopicScore,
  formatTopicTime,
  getTopicCountingStatusLabel,
  getTopicKindLabel,
  getTopicRelationshipLabel,
  getTopicStatusLabel,
  getTopicTraceSourceLabel,
  getTopicTraceValueLabel,
} from "@/lib/topic-display.ts";

type TopicProcessTraceProps = {
  trace: TopicSelectionTrace | null;
  loading: boolean;
  error: string;
};

type TraceDimensionProps = {
  dimension: TopicTraceDimension;
};

type TraceMemoryProps = {
  memory: TopicTraceMemory;
  policyFormula: string;
};

type TraceRubricProps = {
  rubric: TopicTraceRubric[];
};

function scoreValue(dimension: TopicTraceDimension): string {
  const value = formatTopicScore(dimension.score_value);
  if (dimension.score_unit === "multiplier") {
    return `× ${value}`;
  }
  return `+${value} / ${formatTopicScore(dimension.max_value)}`;
}

function rubricOptionValue(rubric: TopicTraceRubric, score: number): string {
  const value = formatTopicScore(score);
  if (rubric.score_unit === "multiplier") {
    return `×${value}`;
  }
  return `+${value}`;
}

function TraceDimension({ dimension }: TraceDimensionProps): ReactElement {
  return (
    <article className="trace-dimension-card">
      <div className="trace-dimension-heading">
        <span>{dimension.title}</span>
        <b>{scoreValue(dimension)}</b>
      </div>
      <div className="trace-dimension-label">
        <strong>{getTopicTraceValueLabel(dimension.key, dimension.label)}</strong>
        <code>{dimension.label}</code>
      </div>
      <p>{dimension.reason || "这一维没有保存额外的判断依据。"}</p>
      <small>{getTopicTraceSourceLabel(dimension.source)}</small>
    </article>
  );
}

function TraceMemory({ memory, policyFormula }: TraceMemoryProps): ReactElement {
  const scoreChanged = Math.abs(memory.current_score - memory.initial_score) > 0.001;
  return (
    <details className="trace-memory-card">
      <summary>
        <span className="trace-memory-copy">
          <code>{memory.memory_id}</code>
          <strong>{memory.text}</strong>
        </span>
        <span className="trace-memory-score">
          <b>{formatTopicScore(memory.current_score)}</b>
          <small>
            {memory.active ? "仍是有效证据" : "证据已失效"}
            {` · ${getTopicCountingStatusLabel(memory.counting_status)}`}
          </small>
        </span>
      </summary>
      <div className="trace-memory-body">
        <div className="trace-memory-meta">
          <div>
            <span>保存的单条初评分</span>
            <strong>{formatTopicScore(memory.initial_score)} / 100</strong>
          </div>
          <div>
            <span>Topic 快照使用分</span>
            <strong>{formatTopicScore(memory.current_score)} / 100</strong>
          </div>
          <div>
            <span>维度分合计</span>
            <strong>{formatTopicScore(memory.raw_points)}</strong>
          </div>
          <div>
            <span>置信系数</span>
            <strong>× {formatTopicScore(memory.confidence_factor)}</strong>
          </div>
        </div>
        <p className="trace-formula">
          {policyFormula}。初评时间：{formatTopicTime(memory.assessed_at)}。
          {memory.eligible ? "这条记忆允许参与 Topic 选择。" : "这条记忆被初评排除。"}
          {scoreChanged ? " 当前分与初评分不同，表示时间等固定规则后来刷新过。" : ""}
        </p>
        <div className="trace-dimension-grid">
          {memory.dimensions.map((dimension) => (
            <TraceDimension dimension={dimension} key={dimension.key} />
          ))}
        </div>
      </div>
    </details>
  );
}

function TraceRubric({ rubric }: TraceRubricProps): ReactElement {
  return (
    <details className="trace-rubric">
      <summary>查看完整评分量表 · {rubric.length} 个维度</summary>
      <div className="trace-rubric-list">
        {rubric.map((item) => (
          <article key={item.key}>
            <header>
              <strong>{item.title}</strong>
              <code>{item.key}</code>
            </header>
            <div>
              {item.options.map((option) => (
                <span key={option.label}>
                  {getTopicTraceValueLabel(item.key, option.label)}
                  <code>{option.label}</code>
                  <b>{rubricOptionValue(item, option.score_value)}</b>
                </span>
              ))}
            </div>
          </article>
        ))}
        {!rubric.length && <p>后端没有返回这套评分规则的量表。</p>}
      </div>
    </details>
  );
}

export default function TopicProcessTrace({
  trace,
  loading,
  error,
}: TopicProcessTraceProps): ReactElement {
  if (loading) {
    return (
      <section className="trace-state loading">
        <LoaderCircle className="spin" size={18} />
        <span>正在读取 Topic 形成过程…</span>
      </section>
    );
  }

  if (error) {
    return (
      <section className="trace-state error">
        <AlertCircle size={18} />
        <div>
          <strong>形成过程读取失败</strong>
          <span>{error}</span>
        </div>
      </section>
    );
  }

  if (!trace) {
    return (
      <section className="trace-state unavailable">
        <CircleDashed size={18} />
        <span>后端没有返回 Topic 过程数据。</span>
      </section>
    );
  }

  if (!trace.available) {
    return (
      <section className="trace-state unavailable">
        <CircleDashed size={18} />
        <div>
          <strong>这条 Topic 没有可回放的形成过程</strong>
          <span>{trace.unavailable_reason}</span>
          <small>页面不会用当前规则或零分冒充历史数据。</small>
        </div>
      </section>
    );
  }

  return (
    <section className="topic-process-trace">
      <div className="trace-section-heading">
        <div>
          <p>SELECTION TRACE</p>
          <h3>本条 Topic 的证据与计分详情</h3>
        </div>
        <span>选择规则 v{trace.selection_version}</span>
      </div>

      <details className="trace-detail-section" open>
        <summary>第一步 · 单条记忆初评</summary>
        <div className="trace-detail-body">
          <p>
            这里展示状态文件保存的规范化单条初评，不冒充模型原始回复，也不冒充多次重评历史。
            标签由模型判断，分值由后端固定规则换算；紧迫性来自事件时间规则，不是模型猜测。
          </p>
          <div className="trace-memory-list">
            {trace.memories.map((memory) => (
              <TraceMemory
                key={memory.memory_id}
                memory={memory}
                policyFormula={trace.policy.memory_formula}
              />
            ))}
          </div>
          <TraceRubric rubric={trace.policy.rubric} />
        </div>
      </details>

      <details className="trace-detail-section">
        <summary>第二步 · 候选标签</summary>
        <div className="trace-detail-body trace-tag-groups">
          {trace.memories.map((memory) => (
            <article key={memory.memory_id}>
              <code>{memory.memory_id}</code>
              <div>
                {memory.tags.map((tag) => (
                  <span className="trace-tag" key={`${tag.topic_key}-${tag.relationship}`}>
                    <strong>{tag.tag_name}</strong>
                    <code>{tag.topic_key}</code>
                    <small>
                      {getTopicRelationshipLabel(tag.relationship)} · {tag.relationship}
                    </small>
                    <p>{tag.reason}</p>
                  </span>
                ))}
                {!memory.tags.length && <p>这条记忆没有提取出候选标签。</p>}
              </div>
            </article>
          ))}
        </div>
      </details>

      <details className="trace-detail-section">
        <summary>第三步 · 语义分组</summary>
        <div className="trace-detail-body trace-grouping">
          <dl>
            <div><dt>分组类型</dt><dd>{getTopicKindLabel(trace.grouping.topic_kind)}</dd></div>
            <div><dt>组内记忆</dt><dd>{trace.grouping.memory_ids.length} 条</dd></div>
          </dl>
          <blockquote>{trace.grouping.reason}</blockquote>
          <div className="trace-key-list">
            {trace.grouping.candidate_tag_keys.map((key) => <code key={key}>{key}</code>)}
            {!trace.grouping.candidate_tag_keys.length && <span>没有候选标签键。</span>}
          </div>
        </div>
      </details>

      <details className="trace-detail-section" open>
        <summary>第四至六步 · 聚合、总结与滚动席位</summary>
        <div className="trace-detail-body">
          <div className="trace-decision-grid">
            <div><span>晋升门槛</span><strong>{formatTopicScore(trace.policy.topic_threshold)}</strong></div>
            <div><span>Topic 基础分</span><strong>{formatTopicScore(trace.decision.base_score)}</strong></div>
            <div><span>新鲜系数</span><strong>× {formatTopicScore(trace.decision.recency_factor)}</strong></div>
            <div><span>最终排名分</span><strong>{formatTopicScore(trace.decision.rank_score)}</strong></div>
            <div><span>当前排名</span><strong>#{trace.decision.rank_position}</strong></div>
            <div><span>滚动席位</span><strong>{getTopicStatusLabel(trace.decision.seat_status)}</strong></div>
          </div>
          <div className="trace-formula-stack">
            <p><b>聚合：</b>{trace.policy.topic_formula}</p>
            <p><b>排序：</b>{trace.policy.rank_formula}</p>
            <p>
              <b>结果：</b>
              {trace.decision.qualifies
                ? `基础分达到 ${formatTopicScore(trace.policy.topic_threshold)} 分门槛，进入滚动池。`
                : `基础分没有达到 ${formatTopicScore(trace.policy.topic_threshold)} 分门槛。`}
            </p>
          </div>
          {!!trace.decision.candidate_reasons.length && (
            <ul className="trace-decision-reasons">
              {trace.decision.candidate_reasons.map((reason) => <li key={reason}>{reason}</li>)}
            </ul>
          )}
          <p className="trace-seat-note">
            当前滚动池最多保留 {trace.policy.seat_limit} 个席位；排名变化由分数和新鲜度共同决定。
          </p>
        </div>
      </details>
    </section>
  );
}
