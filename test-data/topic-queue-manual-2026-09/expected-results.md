# 预期结果对照表

这份对照表用于人工验收。Topic 文案不要求逐字一致，但事件身份、状态和队列行为必须一致。

| 文件 | 预期记忆 | 预期 Topic 行为 |
|---|---|---|
| `01-interview-plan.md` | 面试计划或面试准备事件 | 高重要度；9 月 2 日带临近加分 |
| `02-project-delivery.md` | 客户演示包交付事件 | 高重要度；9 月 3 日带临近加分 |
| `03-visa-appointment.md` | 签证材料面谈事件 | 高重要度；9 月 4 日带临近加分 |
| `04-course-registration.md` | 课程报名事件 | 中等重要度候选 |
| `05-lease-renewal.md` | 续租材料提交事件 | 中等重要度候选 |
| `06-club-presentation.md` | 社团分享准备事件 | 中等重要度候选 |
| `07-interview-progress.md` | 同一场面试的新进展 | 更新 `TQ-INTERVIEW-0902`，不新建第二个面试 Topic |
| `08-project-completed.md` | 同一交付事件的完成结果 | `TQ-DELIVERY-0903` 退出在线队列，候选补位 |
| `09-expense-reimbursement.md` | 报销提交计划 | 第一次可生成候选；第二次导入不得重复加权 |
| `10-completed-standalone.md` | 已经闭环的护照材料提交 | 可以保留为事件记忆，但不进入 3+27 |
| `11-negative-observation.md` | 低价值观察或不生成个人事件 | 不进入 3+27 |
| `12-interview-rescheduled.md` | 面试改期更新 | 仍是原面试 Topic，事件时间更新，临近加分下降 |

## 第一阶段硬性检查

固定重排后：

```text
核心 Topic：最多 3 条
候选 Topic：至少 3 条
隐藏候选：0 条
```

正常情况下，前三名应来自：

- `TQ-INTERVIEW-0902`
- `TQ-DELIVERY-0903`
- `TQ-VISA-0904`

模型如果把某个中等事件判断得更重要，具体前三顺序可以变化。但以下必须成立：

```text
queue_score = importance_score + approaching_bonus - decay_penalty
```

第一阶段的新 Topic 应满足：

- 核心的 `candidate_source` 为 `null`。
- 候选的 `candidate_source` 通常为 `new`。
- 新候选的 `decay_penalty` 为 `0`。
- 核心和候选各自的 `queue_rank` 从 1 开始连续编号。

## 第二阶段硬性检查

### 面试进展

面试 Topic 的：

- `topic_id` 或稳定 `topic_key` 不应变化成第二个独立面试。
- `supporting_memory_ids` 应增加。
- `reason_evidence` 应分别说明计划和准备进展的贡献。
- 如果面试此前在候选队列，新证据可使 `candidate_source` 变成 `refreshed`。

### 项目完成

如果事件更新链路正确：

- 最新 `event_status` 应为 `completed`。
- 原交付 Topic 应变成退休状态或不再出现在在线 3+27 列表。
- 如果存在合格候选，核心空位应立即补齐。

如果原计划仍继续占据核心，先检查 MemOS 是否把完成结果和原计划识别成了同一个事件。这是记忆更新链路的问题，不是 Topic 文案问题。

### 重复报销

连续两次导入完全相同文件后，允许两种正确结果：

1. MemOS 已经去重，只留下一个有效记忆。
2. MemOS 留下两条相同正文，但 Topic 将它们视为重复证据。

无论哪种情况，都不允许：

- 产生两个报销 Topic。
- 仅仅因为重复导入，就明显提高 `importance_score`。

## 负例硬性检查

`10-completed-standalone.md` 和 `11-negative-observation.md` 不应增加在线 Topic 总数。

如果负例生成了 Topic，检查单条记忆的评估：

- `eligible` 是否被错误标为 `true`。
- `agency` 是否被错误判断为主动行动。
- `action_requirement` 是否凭空出现必须完成的动作。
- `event_status=completed` 是否没有被正确排除。

## 时间字段检查

先查看对应记忆的 `metadata.info`：

- `event_start_time`、`event_start_at` 或 `event_time` 应保存事件实际时间。
- `source_recorded_at` 是文件被记录或导入的时间，不应冒充事件时间。
- 时间不完整时不能制造不存在的分钟精度。

只有事件时间被正确提取，临近加分才有意义。
