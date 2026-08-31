# 事件生命周期追踪器设计

## 目标

为 MemOS 增加一个无感的事件生命周期追踪器，使带有计划时间的事件在时间经过后仍能保持
准确、可解释的状态，并在新证据到来时更新原事件，而不是不断新增相似记忆。

本设计解决三个问题：

1. 计划时间到了，但系统没有证据判断任务是否完成。
2. 新导入的进展、完成、取消或延期证据需要更新原事件。
3. 相似事件可能属于不同任务，不能仅凭向量相似度合并。

## 不在本期实现的内容

- 不根据时间经过自动推断任务已完成、已失败或已取消。
- 不主动向用户发起确认问题。
- 不计算任务完成百分比。
- 不把 Topic 当作事件状态的事实来源。
- 不新建第二套事件数据库。
- 不把所有长期目标、偏好或习惯当作待办任务轮询。
- 不引入 Redis、消息队列或新的常驻容器。

## 两种状态必须分开

### 记忆节点状态

`metadata.status` 表示记忆节点能否参与检索，继续使用现有取值：

- `activated`
- `resolving`
- `archived`
- `deleted`

事件已经完成时，记忆节点仍可保持 `activated`，用于历史检索。

### 事件生命周期状态

`metadata.info.event_status` 表示现实事件处于哪个阶段。现有状态为：

- `planned`
- `ongoing`
- `completed`
- `cancelled`
- `uncertain`

新增：

```text
due_unverified
```

中文显示为“到期未确认”。它只表示事件的计划或结束时间已经到达，但系统没有证据判断其
真实结果。它不表示逾期失败，也不同于导入时无法判断事件阶段的 `uncertain`。

## 核心原则

1. MemOS 中的原事件是唯一事实来源。
2. 追踪器只保存可重建的运行索引，不复制事件正文。
3. 时钟只能触发 `planned` 或 `ongoing` 到 `due_unverified` 的确定性转换。
4. `completed`、`cancelled`、延期和重新开始必须来自新证据。
5. 定时检查不调用大模型，不执行向量检索。
6. 新证据可以用相似度查找候选，但相似度不能决定合并。
7. 事件更新必须复用原 `memory_id`，增加版本并保存历史快照。
8. 证据不足时保守新增，不能错误覆盖可能属于另一任务的事件。
9. 相同状态的重复观察不新增记忆，也不制造新版本。
10. Topic 只消费最新事件状态，不拥有事件生命周期。

## 最终架构

```text
新材料导入
    │
    ▼
8000 MemOS 解析并生成事件
    │
    ├── 新证据匹配旧事件：ADD / UPDATE / NONE
    │
    ▼
Neo4j + Qdrant：唯一事件事实
    ▲
    │ 精确 memory_id + expected_version
    │
8011 应用后端
    ├── PlanTrackerWorker 定时检查
    ├── 轻量追踪索引
    └── Topic 刷新
```

追踪器实现为独立模块，由现有 8011 `app-backend` 的 FastAPI 生命周期启动。当前部署只运行
一个 8011 进程，因此首版不增加独立容器、端口或启动命令。以后需要横向扩容时，同一模块
可以拆成单独的 sidecar worker，而不改变事件规则。

现有 MemOS Scheduler 不承载此功能。它处理导入、查询、工作记忆和激活记忆等系统作业，
不具备按事件 ID 持久化未来检查时间的语义。

## 追踪范围

### 进入定时队列

满足以下条件的 `planned` 事件进入定时队列：

```text
metadata.status == activated
record_type == event
event_status == planned
至少存在一个可解析的绝对时间字段
```

`planned` 的检查时间按以下顺序选择：

1. `event_end_time`
2. `event_time`
3. `event_start_time`

满足以下条件的 `ongoing` 事件进入定时队列：

```text
metadata.status == activated
record_type == event
event_status == ongoing
event_end_time 可以解析
```

已经由 `planned` 更新成 `ongoing`、但没有明确结束时间的事件可以继续保留在追踪索引中，
其运行状态为 `waiting_evidence`，但不能仅凭时钟改变事件状态。

### 不执行定时转换

- `completed` 和 `cancelled` 立即退出追踪索引。
- `uncertain` 默认不进入追踪索引。
- 没有可解析时间的 `planned` 保留在 MemOS；`unscheduled` 只作为追踪索引的运行状态，
  不写入 `event_status`，也不能触发自动到期。
- 没有结束时间、也不是从已追踪计划更新而来的普通 `ongoing` 不进入追踪索引。
- `due_unverified` 保留为 `waiting_evidence`，等待新材料，不重复产生定时更新。

这样不会把“用户长期学习编程”或稳定偏好误当成需要检查完成状态的任务。

## 时间边界

- 完整日期时间按其时区解析。
- 只有 `YYYY-MM-DD` 时，使用应用配置时区当天的结束时刻作为检查边界。
- 首版应用时区使用现有系统本地时区，部署默认 `Asia/Shanghai`。
- 不能解析、缺少年份或仍是“明天”“后天”等相对时间时，不进入定时队列。
- 追踪器不修复错误时间；绝对时间规范化仍由记忆导入链路负责。

## 轻量追踪索引

默认状态文件：

```text
本地：.memos/plan_tracker/tracker.json
容器：/data/plan-tracker/tracker.json
```

环境变量：

```text
MEMOS_PLAN_TRACKER_ENABLED=true
MEMOS_PLAN_TRACKER_STATE=/data/plan-tracker/tracker.json
MEMOS_PLAN_TRACKER_INTERVAL_SECONDS=60
MEMOS_PLAN_TRACKER_RECONCILE_SECONDS=900
```

追踪索引的运行状态只允许 `scheduled`、`waiting_evidence`、`unscheduled` 和 `error`。这些值
不属于事件事实状态，不写入 MemOS。

状态文件顶层包含 `schema_version`，每条索引只保存运行信息：

```json
{
  "schema_version": 1,
  "events": {
    "memory-id": {
      "memory_id": "memory-id",
      "user_id": "default",
      "cube_id": "default_cube",
      "last_seen_version": 3,
      "check_at": "2026-09-01T10:00:00+08:00",
      "next_check_at": "2026-09-01T10:00:00+08:00",
      "last_checked_at": null,
      "tracking_state": "scheduled",
      "failure_count": 0,
      "topic_sync_pending": false
    }
  }
}
```

索引不得保存记忆正文、Embedding、来源全文或 Topic 内容。文件丢失后，追踪器可以分页扫描
MemOS 中的活动事件并重建。状态文件使用进程内锁和原子替换写入，避免部分写入。

## 启动与增量注册

8011 启动时执行一次 `startup_reconcile`：

1. 分页读取 MemOS 中的活动事件。
2. 根据状态和时间字段重建追踪索引。
3. 移除已经完成、取消、删除或归档的索引项。
4. 对已经越过检查时间的事件执行到期检查。

每次通过现有 8011 导入接口成功写入记忆后，使用返回的 `memory_id` 增量刷新索引，不等待
下一次全量扫描。每隔十五分钟再执行一次轻量 reconcile，捕获绕过 8011、直接写入 8000
的事件以及删除操作。

## 到期检查

定时器每轮只读取 `next_check_at <= now` 的索引项：

1. 按准确 `memory_id` 重新读取最新记忆。
2. 校验记忆仍为 `activated event`。
3. 校验当前版本等于索引中的 `last_seen_version`。
4. 重新计算最新检查时间，防止任务已经延期。
5. 如果状态已经是 `completed` 或 `cancelled`，出队。
6. 如果时间尚未到，更新索引时间，不写记忆。
7. 如果仍为可到期的 `planned` 或 `ongoing`，将原事件精确更新为 `due_unverified`。
8. 更新成功后刷新索引，并按同一 `memory_id` 刷新 Topic。

到期更新只修改 `event_status`、记忆版本、更新时间、历史快照和内部操作轨迹。记忆正文、事件
对象、时间和来源均保持不变。

已经是 `due_unverified` 的事件再次检查时必须返回 `NONE`：不增加版本、不写历史、不刷新
Topic。

## 精确状态更新接口

8011 和 8000 是不同进程。追踪器不得直写 Neo4j 或 Qdrant，也不得伪造一条“系统观察”交给
`/product/add`。8000 增加一个只在 Docker 私网使用的窄接口：

```text
POST /product/event_lifecycle/transition
```

请求只允许：

```json
{
  "user_id": "default",
  "cube_id": "default_cube",
  "memory_id": "memory-id",
  "expected_version": 3,
  "to_status": "due_unverified",
  "observed_at": "2026-09-01T10:00:00+08:00"
}
```

首版接口不接受任意正文、任意 metadata 或任意目标状态。自动转换只允许：

```text
planned -> due_unverified
ongoing -> due_unverified
```

接口按 `memory_id + expected_version` 执行乐观版本校验：

- 版本相同且转换合法：更新原 ID，版本加一。
- 状态已经是 `due_unverified`：返回 `no_op`。
- 版本不同：返回 `conflict`，追踪器重新读取后再判断。
- 记忆已完成、取消、删除或归档：返回 `no_op`，不得重新打开。
- ID 不存在：返回 `not_found` 并移除索引。

8000 继续不向公网暴露。前端不直接调用该接口。

## 新证据更新原事件

真实的完成、取消、延期和进行中状态只能由新导入材料触发。处理流程为：

```text
新材料生成候选事件
    ↓
从活动事件中召回少量候选
    ↓
确定性身份校验
    ↓
模型判断 ADD / UPDATE / NONE
    ↓
程序验证操作和状态转换
    ↓
按准确 memory_id 写入
```

### 相似度的边界

向量相似度只用于召回候选，不能作为合并依据。以下宽泛相似性不能单独支持更新：

- 都是“任务”或“项目”。
- 都包含“截止日期”。
- 都是“面试”“训练”或“数据采集”。
- 标签相同但没有共同的具体对象。

### 同一事件依据

更新原事件必须满足：

1. 不存在明确冲突；并且
2. 至少存在一个可靠身份锚点。

可靠身份锚点包括：

- 相同且非空的 `event_group_id`。
- 相同的具体任务或项目名称。
- 相同人物或机构，并且时间范围兼容。
- 相同具体事件对象，并且绝对时间范围兼容。
- 新材料中的明确指代能够由同一来源上下文唯一解析到旧事件。

明确冲突包括：

- 不同项目、公司、机构或任务对象。
- 不兼容的绝对日期或时间范围。
- 不同的关键参与者。
- 新材料明确描述另一场独立事件。

存在冲突或可靠依据不足时必须 `ADD`，不能更新候选。该策略允许暂时保留两条待判断记忆，
以避免把不同现实任务错误合并。

### 允许的新证据转换

```text
planned -> ongoing | completed | cancelled
ongoing -> completed | cancelled
due_unverified -> ongoing | completed | cancelled
due_unverified -> planned，仅限材料明确说明延期且给出新的绝对时间
```

重复导入“仍在进行”，但正文、状态、时间和其他事实均未变化时返回 `NONE`。旧来源不得把较新
状态倒退；终态不得仅凭模糊或更早材料重新打开。

## 版本与历史

所有有效更新继续使用原 `memory_id`：

- `metadata.version` 加一。
- 旧正文作为当前节点的 `metadata.history` 快照保存。
- 当前节点保持 `activated`。
- 普通检索和 Topic 只使用当前版本。
- 历史快照不作为第二条记忆计分。

追踪器读取版本 3 后，如果新证据已经将事件更新为版本 4，版本 3 的到期操作必须失败并
重新读取，绝不能覆盖版本 4。

## Topic 联动

Topic 不参与事件状态判断。事件状态变化成功后，按同一 `memory_id` 刷新对应 Topic 快照：

- 覆盖原支持记忆的 revision，不新增一票。
- 只改变状态或时间时复用原标签和语义判断，不再次调用 Topic 大模型。
- 正文、对象或标签发生语义变化时，才重新调用已有 Topic 分析流程。
- `completed` 和 `cancelled` 按现有 Topic 规则退出当前关注队列。
- `due_unverified` 作为未闭环事件参与 Topic 的派生注意力判断。

本设计以 `event_status=due_unverified` 作为 MemOS 的事实状态，取代旧 Topic 设计中仅依赖
`past_unconfirmed` 推断时间已过的做法。Topic 如需继续展示 `past_unconfirmed`，只能将它
作为由 `due_unverified` 派生的 `attention_status`，不能与事件事实冲突。

本设计只覆盖 `2026-08-31-topic-3-plus-27-queue-design.md` 中的事件状态来源，不改变其中的
3＋27 席位、重要度和队列分规则。该文档中的 `actionable_overdue` 不作为 MemOS 的规范
`event_status`；如仍需展示，只能作为 Topic 派生注意力标签。规范事件状态以本设计中的
`planned`、`ongoing`、`due_unverified`、`completed`、`cancelled` 和 `uncertain` 为准。

状态更新后若 Topic 刷新失败，记忆更新不回滚。索引设置 `topic_sync_pending=true`，下一轮
reconcile 重试，确保事件事实优先。

## 应用后端与部署

新增模块：

```text
scripts/memos_plan_tracker.py
```

主要职责分为：

- `PlanTrackerStore`：轻量索引、原子写入和恢复。
- `PlanTracker`：事件筛选、检查时间计算、到期处理和 reconcile。
- `PlanTrackerWorker`：由 FastAPI lifespan 启停的循环。

修改 `scripts/memos_frontend_api.py`，在现有单进程 8011 服务中启动 worker。测试创建应用时
默认注入禁用的 tracker 或 fake tracker，避免测试进程启动真实后台线程。

本地 Compose 增加：

```text
../.memos/plan_tracker:/data/plan-tracker
```

服务器 Compose 增加独立 named volume `plan_tracker_data`。追踪索引不得与 `topics.json`
共用文件。

仍使用原来的启动命令：

```text
docker compose up -d --build
```

## 故障处理

- 8000 暂时不可用：保留索引并退避重试，不修改记忆。
- 模型服务不可用：不影响纯时间到期检查。
- 时间无法解析：保留原事件，记录 `unscheduled` 原因。
- 版本冲突：重新读取；不覆盖新版本。
- Topic 刷新失败：记录待同步，下一轮修复。
- 索引文件损坏：从 MemOS 重建；原事件不受影响。
- 进程重启：根据绝对时间重新计算，不依赖进程运行时累计时间。
- 相同到期任务被重复调度：精确接口返回 `no_op`，不制造版本风暴。

当前部署只允许一个 8011 worker 写追踪索引。未来扩展为多个 8011 副本前，必须先将相同
模块拆成单实例 sidecar 或增加跨进程租约；首版不实现分布式锁。

## 测试策略

遵循 TDD，先写失败测试，再实现。

### 纯规则单元测试

- `planned` 检查时间优先使用 `event_end_time`。
- `planned` 回退使用 `event_time`、再使用 `event_start_time`。
- `ongoing` 只有明确 `event_end_time` 才执行定时转换。
- 只有日期时按本地当天结束处理。
- 相对时间和无年份时间不进入队列。
- `completed`、`cancelled` 和普通无结束时间 `ongoing` 不执行定时转换。

### 追踪器单元测试

- 启动扫描可以建立和清理索引。
- 未到期事件不写记忆。
- 到期事件按原 ID 变成 `due_unverified`。
- 重复扫描 `due_unverified` 返回 `NONE`。
- 版本冲突后重新读取，不能覆盖新证据。
- 8000 故障后保留任务并退避。
- 重启后可以从 MemOS 重建索引。

### 事件更新测试

- 相似但具体项目不同的事件必须 `ADD`。
- 相同具体对象和兼容时间的新进展可以 `UPDATE`。
- 只有宽泛标签相同不能 `UPDATE`。
- 重复 `ongoing` 观察返回 `NONE`。
- 明确完成更新同一 ID 为 `completed`。
- 明确延期更新同一 ID、状态回到 `planned` 并重排检查时间。
- 更早来源不能把终态倒退。

### API 与并发测试

- 精确接口只接受允许的目标状态。
- `expected_version` 不匹配返回冲突。
- 已完成、取消、删除或归档事件不能被定时器重新打开。
- 同一请求重复执行保持幂等。
- 8000 接口不通过 8011 公网 API 暴露。

### Topic 与部署测试

- 同一事件状态变化后 Topic 证据数量不增加。
- 纯状态变化不调用 Topic 大模型。
- `completed` 和 `cancelled` 退出关注队列。
- `due_unverified` 显示为到期未确认，并保持未闭环语义。
- 本地与服务器 Compose 都持久化独立追踪索引。
- 原有一条命令启动方式不变。

## 验收场景

### 面试计划

1. 导入“用户计划于 2026 年 9 月 1 日 10:00 参加 A 公司面试”。
2. 保存一个 `planned` 事件并注册检查时间。
3. 10:00 后没有新证据，同一 ID 更新为 `due_unverified`。
4. 导入“用户正在参加 A 公司面试”，同一 ID 更新为 `ongoing`。
5. 连续导入相同的进行中观察，不新增记忆。
6. 导入“用户已经结束 A 公司面试”，同一 ID 更新为 `completed` 并退出追踪。

### 不同面试

已有“A 公司面试”计划时导入“用户计划参加 B 公司面试”。两者即使语义相似，也因为机构
冲突保存为两个事件，不得覆盖。

### 延期

一个 `due_unverified` 事件收到“面试延期至 2026 年 9 月 10 日 15:00”的明确新证据后，
同一 ID 更新为 `planned`，保存新绝对时间并重新进入定时队列。

### 并发更新

追踪器准备将版本 3 标记为 `due_unverified` 时，新证据先把原事件更新成版本 4 的
`completed`。追踪器收到版本冲突，重新读取后退出，不得覆盖 `completed`。

## 预计改动范围

新增：

- `scripts/memos_plan_tracker.py`
- `tests/scripts/test_memos_plan_tracker.py`
- `src/memos/memories/textual/event_lifecycle.py`
- `src/memos/api/handlers/event_lifecycle_handler.py`
- 事件生命周期规则和精确更新接口的对应测试文件

修改：

- `src/memos/memories/textual/relationship.py`
- `src/memos/memories/textual/event_upsert.py`
- `src/memos/templates/memory_info_prompts.py`
- `src/memos/api/product_models.py`
- `src/memos/api/routers/server_router.py`
- `scripts/memos_chat.py`
- `scripts/memos_frontend_api.py`
- `scripts/memos_topic.py`
- 前端事件状态显示代码
- 本地与服务器 Docker Compose
- OpenAPI 文档和相关测试

该范围包含一个新的 8000 内部路由和请求模型。实施前必须以本规格作为接口变更批准依据，
并在实现后重新生成和验证 OpenAPI。
