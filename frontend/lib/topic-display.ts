import type {
  Topic,
  TopicAttentionStatus,
  TopicCandidateSource,
  TopicQueueStatus,
  TopicScoreBreakdown,
} from "./api-contract.ts";

const STATUS_LABELS: Readonly<Record<string, string>> = {
  active: "当前席位",
  suppressed: "候选保留",
  retired: "已结束",
  ongoing: "进行中",
  completed: "已完成",
  cancelled: "已取消",
  planned: "计划中",
  due_unverified: "到期未确认",
  uncertain: "状态待定",
  unknown: "状态未知",
};

const TRACE_VALUE_LABELS: Readonly<Record<string, Readonly<Record<string, string>>>> = {
  agency: {
    observed: "仅观察",
    consumed: "被动浏览或消费",
    participated: "参与其中",
    acting: "主动行动",
    committed: "明确决定或承诺",
  },
  action_requirement: {
    none: "无后续行动",
    optional: "可选行动",
    ongoing: "持续进行",
    clear_next_action: "下一步明确",
    must_do: "必须完成",
  },
  impact: {
    trivial: "影响很小",
    limited: "影响有限",
    meaningful: "有明显影响",
    high: "影响较高",
  },
  explicit_priority: {
    none: "未明确强调",
    interested: "明确感兴趣",
    important: "明确重要",
    must_or_remind: "必须处理或提醒",
  },
  effort: {
    none: "尚无明确投入",
    some: "已有一定投入",
    substantial: "已有大量投入",
  },
  confidence: {
    low: "低置信",
    medium: "中置信",
    high: "高置信",
  },
  urgency: {
    no_event_time: "没有可用事件时间",
    invalid_event_time: "事件时间无法识别",
    completed_or_cancelled: "事件已结束",
    expired_over_24_hours: "事件已过去超过 24 小时",
    later_than_30_days: "距事件超过 30 天",
    within_30_days: "30 天内",
    within_7_days: "7 天内",
    within_72_hours: "72 小时内",
    within_24_hours: "24 小时内",
  },
};

const TRACE_SOURCE_LABELS: Readonly<Record<string, string>> = {
  model: "模型判断",
  time_rule: "时间规则",
  duration_rule: "持续时间规则",
  model_or_duration_rule: "模型与持续时间规则",
};

const COUNTING_STATUS_LABELS: Readonly<Record<string, string>> = {
  counted: "参与计分",
  duplicate: "重复记忆，不重复计分",
  not_counted: "未参与计分",
  excluded: "不参与 Topic 选择",
};

export type TopicMemoryScoreState = {
  state: "counted" | "not_counted" | "unknown";
  label: string;
  description: string;
  score: number | undefined;
};

export function getTopicMemoryScoreState(
  breakdown: TopicScoreBreakdown,
  memoryId: string,
): TopicMemoryScoreState {
  switch (breakdown.model) {
    case "static_importance_v3":
    case "memory_importance_v2": {
      const score = breakdown.memory_scores[memoryId];
      if (breakdown.counted_memory_ids.includes(memoryId)) {
        return {
          state: "counted",
          label: "参与计分",
          description: "参与当前 Topic 计分。",
          score,
        };
      }
      return {
        state: "not_counted",
        label: "未重复计分",
        description: "作为来源保留，但没有重复计分。",
        score,
      };
    }
    case "legacy_evidence_v1":
      return {
        state: "unknown",
        label: "计分状态未知",
        description: "历史评分未提供单条记忆的计分状态。",
        score: undefined,
      };
    case "partial":
      return {
        state: "unknown",
        label: "计分状态未知",
        description: "当前评分字段不完整，无法判断这条记忆是否参与计分。",
        score: undefined,
      };
  }
}

export function partitionTopicQueues(items: readonly Topic[]): {
  core: Topic[];
  candidates: Topic[];
} {
  return {
    core: items.filter((topic) => topic.status === "active"),
    candidates: items.filter((topic) => topic.status === "suppressed"),
  };
}

export function filterTopicQueues(
  items: readonly Topic[],
  query: string,
  status: "all" | TopicQueueStatus,
): Topic[] {
  const normalized = query.trim().toLowerCase();
  return items.filter((topic) => {
    if (status !== "all" && topic.status !== status) {
      return false;
    }
    if (!normalized) {
      return true;
    }
    return [topic.key, topic.title, topic.reason, ...topic.candidate_reasons]
      .join(" ")
      .toLowerCase()
      .includes(normalized);
  });
}

export function getTopicCandidateSourceLabel(value: TopicCandidateSource): string {
  const labels: Readonly<Record<Exclude<TopicCandidateSource, null>, string>> = {
    new: "新增候选",
    demoted: "核心降级",
    refreshed: "新证据刷新",
  };
  return value === null ? "核心 Topic" : labels[value];
}

export function getTopicAttentionStatusLabel(value: TopicAttentionStatus): string {
  return value === "past_unconfirmed" ? "结果待确认" : "正常竞争";
}

function getDecayPenaltyLabel(topic: Topic): string {
  if (topic.status === "active") {
    return "核心席位陈旧衰减";
  }
  if (topic.candidate_source === "demoted") {
    return "从核心降级后的陈旧衰减";
  }
  return "陈旧衰减";
}

export function formatTopicQueueExplanation(topic: Topic): string {
  const parts = [
    `重要度 ${formatTopicScore(topic.importance_score)} 分`,
    `事件临近增加 ${formatTopicScore(topic.approaching_bonus)} 分`,
  ];
  if (topic.decay_penalty > 0) {
    const prefix = getDecayPenaltyLabel(topic);
    parts.push(`${prefix}扣除 ${formatTopicScore(topic.decay_penalty)} 分`);
  }
  parts.push(`当前队列分 ${formatTopicScore(topic.queue_score)} 分`);
  let result = `${parts.join("；")}。`;
  if (topic.attention_status === "past_unconfirmed") {
    result += "事件时间已过，结果仍待确认，暂时只能留在候选队列。";
  }
  return result;
}

export function formatTopicScore(value: number): string {
  return String(Number(value.toFixed(2)));
}

export function formatTopicTime(value: string | null | undefined): string {
  if (!value) {
    return "时间未知";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

export function getTopicStatusLabel(value: string): string {
  return STATUS_LABELS[value] || value;
}

export function getTopicTraceValueLabel(key: string, value: string): string {
  return TRACE_VALUE_LABELS[key]?.[value] || value;
}

export function getTopicTraceSourceLabel(value: string): string {
  return TRACE_SOURCE_LABELS[value] || value;
}

export function getTopicCountingStatusLabel(value: string): string {
  return COUNTING_STATUS_LABELS[value] || value;
}

export function getTopicRelationshipLabel(value: string): string {
  const labels: Readonly<Record<string, string>> = {
    direct: "直接相关",
    related: "间接相关",
    weak: "弱相关",
  };
  return labels[value] || value;
}

export function getTopicKindLabel(value: string): string {
  if (value === "event") {
    return "具体事件";
  }
  if (value === "pattern") {
    return "持续模式";
  }
  return value;
}
