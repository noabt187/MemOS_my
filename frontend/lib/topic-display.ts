import type { TopicScoreBreakdown } from "./api-contract.ts";

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
  if (breakdown.model === "memory_importance_v2") {
    const counted = breakdown.counted_memory_ids.includes(memoryId);
    return {
      state: counted ? "counted" : "not_counted",
      label: counted ? "参与计分" : "未重复计分",
      description: counted
        ? "参与当前 Topic 计分。"
        : "作为来源保留，但没有重复计分。",
      score: breakdown.memory_scores[memoryId],
    };
  }

  if (breakdown.model === "legacy_evidence_v1") {
    return {
      state: "unknown",
      label: "计分状态未知",
      description: "历史评分未提供单条记忆的计分状态。",
      score: undefined,
    };
  }

  return {
    state: "unknown",
    label: "计分状态未知",
    description: "当前评分字段不完整，无法判断这条记忆是否参与计分。",
    score: undefined,
  };
}
