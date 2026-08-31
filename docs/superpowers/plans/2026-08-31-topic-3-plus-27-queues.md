# Topic 3＋27 双队列实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把当前 15 个同级 Topic 席位改为 3 个核心 Topic、27 个可见候选 Topic，并实现可解释的临近加分、分状态衰减、固定升降级和每天 00:00／12:00 自动重排。

**Architecture:** MemOS 仍是原始记忆的唯一来源；`scripts/memos_topic.py` 继续维护 Topic JSON 快照和状态流转；新增一个无数据库、无模型调用的纯规则模块负责分数计算；唯一的 `app-backend` 进程负责定时重排；应用 API 只返回 3＋27 可见快照；React 前端分区展示核心和候选。定时重排不访问大模型，也不修改原始记忆。

**Tech Stack:** Python 3.12、标准库 `zoneinfo`／`asyncio`、FastAPI、JSON 原子文件写入、pytest、React 19、TypeScript 5.9、Vite 8、Node 22、Docker Compose。

**Spec:** `docs/superpowers/specs/2026-08-31-topic-3-plus-27-queue-design.md`

## 全局约束

- 这是 Topic 队列改造，不实现记忆状态更新，不回写 MemOS 原始记忆。
- 不增加任务进度百分比、剩余时间预测和手动置顶。
- 模型只继续做离散判断、标签、分组和 Topic 摘要；模型不能输出最终分数。
- `importance_score` 只能由现有五个维度和置信度计算，不能包含时间分。
- 27 是前端可见候选上限，不是 JSON 存储上限；第 28 名以后仍保存并参与重排。
- 定时重排只能在现有单个 `app-backend` 进程运行。禁止新增 Topic sidecar、cron、第二个 writer、`memos_topic.py watch` 或 Uvicorn 多 worker。
- Docker `app-backend` 运行并挂载同一 `.memos/topic` 时，不得同时运行会直接触发 Topic hook 的
  `start_memos_chat.ps1`／`memos_chat.py`。现有锁不能跨进程；本期用“唯一 writer”部署约束解决，
  不声称已经实现跨进程文件锁。
- 继续使用 Topic JSON 的进程内共享 `RLock` 和临时文件＋`os.replace`。一次全量定时重排只读一次、写一次。
- 不新增 Python 或 npm 依赖；时区使用标准库 `zoneinfo.ZoneInfo("Asia/Shanghai")`。
- 保留 `lifecycle_status=active/suppressed/retired`，分别兼容核心、候选、退出队列。
- 保留 `rank_score` 作为 `queue_score` 的兼容别名；新代码和新前端只把 `queue_score` 当成队列排序分。
- 保留当前 `TOPIC_SELECTION_VERSION = 3`。队列算法单独使用 `TOPIC_QUEUE_POLICY_VERSION = 1`，避免触发不必要的全量模型重跑。
- 当前正式事件范围字段是 `event_start_time/event_end_time`；读取时同时兼容旧的 `event_start_at/event_end_at`，本任务不再改记忆 schema。
- 只有“新证据的记录时间晚于旧证据”，或“同一 memory ID 的 revision 改变了状态／时间”，才算有效刷新。新导入一张很旧的历史截图不能清除衰减。
- 当前工作区已有未提交的事件合并、revision 指纹、`shared_anchor` 和前端 Trace 修改。不得执行 `git reset`、`git checkout --`、整文件覆盖或把这些改动丢掉。
- 公共路由保持 `/api/v1/topics` 和 `/api/v1/topics/{topic_id}/trace` 不变。只增加经过设计确认的响应字段；若实施时需要改字段名、删除字段或新增路由，必须先再次征得用户同意。
- 每个任务都执行红－绿 TDD：先加入失败测试并确认失败，再写最小实现，再确认通过，最后单独提交。

---

### Task 0：保护当前未提交基线

**Files:**

- Preserve: `scripts/memos_topic.py`
- Preserve: `tests/scripts/test_memos_topic.py`
- Preserve: `scripts/memos_chat.py`
- Preserve: `frontend/app/page.tsx`
- Preserve: `frontend/app/topics/TopicProcessTrace.tsx`
- Preserve: `frontend/lib/api-contract.ts`
- Preserve: `frontend/tests/api-contract.test.ts`
- Preserve: 当前 `git status --short` 中的其他用户改动

**Interfaces:**

- Consumes: 已实现但尚未提交的 `TOPIC_SELECTION_VERSION=3`、`shared_anchor`、同 ID revision 重处理和事件字段调整
- Produces: 一个可验证且可恢复的实施起点，不把本计划建立在过期 HEAD 上

- [ ] **Step 1：记录实际脏文件，不做清理**

Run:

```powershell
cd D:\project-memo\MemOS
git status --short
git diff --check
git diff --stat
```

Expected: 能看到现有未提交文件；`git diff --check` 不报告冲突标记或空白错误。

- [ ] **Step 2：验证当前 Topic 基线**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -m pytest `
  tests\scripts\test_memos_topic.py `
  tests\scripts\test_memos_chat.py `
  tests\scripts\test_memos_frontend_api.py `
  -q -p no:cacheprovider
```

Expected: 当前相关测试全部通过。审计时 `test_memos_topic.py` 单独结果为 `41 passed`。

- [ ] **Step 3：把重叠的当前修改单独提交成恢复点**

先完成并测试这些修改原本所属的任务，再把它们作为独立提交保存。禁止在仍然修改着
`memos_topic.py`、相关测试或四个前端文件时开始 Task 1。不要 stash，也不要用恢复命令。

Expected: 本计划将修改的文件全部干净；其他不重叠的用户文件可以继续存在，但后续只按文件或
`git add -p` 暂存。

- [ ] **Step 4：固定计划比较基线**

Run:

```powershell
git branch checkpoint/topic-queue-base
git status --short
git rev-parse checkpoint/topic-queue-base
```

Expected: 本地 checkpoint 分支指向开始 Queue 改造前的提交。若同名分支已存在，先核对它是否
正好指向当前 HEAD；不相同则停止，不得强行覆盖。

---

### Task 1：建立纯 Topic 队列规则模块

**Files:**

- Create: `scripts/memos_topic_queue.py`
- Create: `tests/scripts/test_memos_topic_queue.py`
- Modify: `tests/scripts/test_memos_topic.py:12-20`（统一把 `scripts` 加入 `sys.path`）

**Interfaces:**

- Produces: `TopicEventWindow`
- Produces: `TopicTimeEvidence`
- Produces: `TopicQueueScore`
- Produces: `TopicQueuePolicy`
- Produces: `resolve_topic_event_window(evidence) -> TopicEventWindow`
- Produces: `calculate_approaching_bonus(window, now, timezone_name) -> float`
- Produces: `calculate_core_stagnation_penalty(core_entered_at, last_evidence_at, current_penalty, now) -> float`
- Produces: `calculate_demoted_candidate_penalty(demoted_at, penalty_at_demotion, now) -> float`
- Produces: `calculate_queue_score(importance_score, approaching_bonus, decay_penalty) -> TopicQueueScore`
- Produces: `latest_scheduled_slot(now, timezone_name) -> datetime`
- Produces: `next_scheduled_slot(now, timezone_name) -> datetime`

- [ ] **Step 1：先写时间、衰减和队列公式的失败测试**

`tests/scripts/test_memos_topic_queue.py` 使用固定的 `+08:00` 时间，不读取系统当前时间。至少覆盖：

测试名称固定为：

- `test_precise_time_approaching_bonus_uses_all_boundaries`
- `test_event_calendar_day_keeps_twenty_points_until_local_midnight`
- `test_date_only_approaching_bonus_does_not_invent_an_hour`
- `test_active_date_range_receives_twenty_points`
- `test_month_year_unknown_and_conflicting_times_receive_zero`
- `test_new_and_refreshed_candidates_never_decay`
- `test_core_decay_uses_later_of_core_entry_and_last_evidence`
- `test_demoted_decay_preserves_existing_penalty_and_caps_at_twenty`
- `test_queue_score_is_importance_plus_bonus_minus_decay_clamped_to_120`
- `test_latest_and_next_slots_are_midnight_and_noon_in_shanghai`

精确时间表必须逐项断言：`>168h=0`、`72~168h=4`、`48~72h=8`、`24~48h=12`、
`0~24h=16`、事件本地日期 `=20`、结束日期下一天 `=0`。日期精度表必须逐项断言今天、
明天、后天、3 天后、4～7 天和更远日期。

- [ ] **Step 2：运行测试并确认模块尚不存在**

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -m pytest `
  tests\scripts\test_memos_topic_queue.py `
  -q -p no:cacheprovider
```

Expected: collection 失败或测试因缺少 `memos_topic_queue` 及其接口而失败。

测试加载方式必须先统一为：

```python
SCRIPTS_DIR = Path(__file__).parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
SCRIPT_PATH = SCRIPTS_DIR / "memos_topic.py"
```

新的 `test_memos_topic_queue.py` 也显式加入同一个 `SCRIPTS_DIR`。规则模块不得反向导入
`memos_topic.py`；它只接收规范化数据，避免循环依赖。

- [ ] **Step 3：实现无副作用的规则接口**

`scripts/memos_topic_queue.py` 的公共骨架固定为：

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Any

TOPIC_CORE_LIMIT = 3
TOPIC_VISIBLE_CANDIDATE_LIMIT = 27
TOPIC_SCORE_THRESHOLD = 60.0
TOPIC_SCHEDULED_PROMOTION_MARGIN = 5.0
TOPIC_IMMEDIATE_PROMOTION_MARGIN = 10.0
TOPIC_QUEUE_SCORE_MAX = 120.0
TOPIC_TIMEZONE = "Asia/Shanghai"
TOPIC_QUEUE_POLICY_VERSION = 1


@dataclass(frozen=True)
class TopicTimeEvidence:
    memory_id: str
    source_recorded_at: str | None
    created_at: str | None
    event_start_time: str | None
    event_start_at: str | None
    event_time: str | None
    event_end_time: str | None
    event_end_at: str | None


@dataclass(frozen=True)
class TopicEventWindow:
    start_at: str | None
    end_at: str | None
    precision: str
    source_memory_id: str | None
    conflict: bool = False


@dataclass(frozen=True)
class TopicQueueScore:
    importance_score: float
    approaching_bonus: float
    decay_penalty: float
    queue_score: float


@dataclass(frozen=True)
class TopicQueuePolicy:
    core_limit: int = TOPIC_CORE_LIMIT
    visible_candidate_limit: int = TOPIC_VISIBLE_CANDIDATE_LIMIT
    scheduled_promotion_margin: float = TOPIC_SCHEDULED_PROMOTION_MARGIN
    immediate_promotion_margin: float = TOPIC_IMMEDIATE_PROMOTION_MARGIN
    timezone_name: str = TOPIC_TIMEZONE


DEFAULT_TOPIC_QUEUE_POLICY = TopicQueuePolicy()
```

`TopicTimeEvidence` 只是进程内传递数据的类型，不写入 Topic JSON，也不增加 MemOS 的 info
字段。`memos_topic.py` 负责用现有 `_memory_info()` 把每条记忆转换为该类型；纯规则模块只接收
`list[TopicTimeEvidence]`，不得读取 MemOS 的 `metadata.info` 层级。这样字段兼容逻辑仍集中在一处。
`resolve_topic_event_window()` 根据时间字符串本身判断精度：只有 `YYYY-MM-DD` 时是 `day`，带
小时／分钟时是精确时间；无法可靠解析或证据互相冲突时，不制造精度。

时间解析顺序必须是：

```text
event_start_time → event_start_at → event_time
event_end_time   → event_end_at
```

同一 Topic 的多条时间证据只能选择“最新且明确”的一条。两条同样新的明确证据冲突时，
`conflict=True` 且临近加分为 0。`YYYY-MM-DD` 保留为日期精度；不得自动补成当天 00:00 后再
按小时计算。判断“哪条证据更新”时优先使用 `source_recorded_at`，缺失后才使用记忆
`created_at`；两者都无效时保留来源顺序并在时间解释中标记为回退，不能制造记录时间。

衰减函数必须使用完整经过日数 `floor(seconds / 86400)`，并实现设计文档中的两张阶梯表。
所有函数只接收参数并返回值，不读环境变量、不读 JSON、不访问 MemOS、不调用模型。
生产代码统一传 `DEFAULT_TOPIC_QUEUE_POLICY`；测试可以注入不同 policy 验证边界，但部署配置
不开放修改 3／27 和上海时区，避免同一系统四处出现不同策略。

- [ ] **Step 4：验证全部纯规则测试通过**

Run Step 2 again.

Expected: 所有边界值、日期精度、时区和分数上限测试通过。

- [ ] **Step 5：提交纯规则层**

```powershell
git add scripts/memos_topic_queue.py tests/scripts/test_memos_topic_queue.py `
  tests/scripts/test_memos_topic.py
git diff --cached --check
git commit -m "feat(topic): add deterministic queue policy"
```

---

### Task 2：把“静态重要度”与“时间临近”彻底拆开

**Files:**

- Modify: `scripts/memos_topic.py:192-232`（`TagEvidence`、`CandidateMetrics`、`MemoryAssessment`）
- Modify: `scripts/memos_topic.py:294-350`（现有评分常量和旧紧迫度表）
- Modify: `scripts/memos_topic.py:2104-2172`（事件字段兼容和旧 `_memory_urgency_points`）
- Modify: `scripts/memos_topic.py:2194-2314`（单记忆重要度）
- Modify: `scripts/memos_topic.py:2317-2405`（Topic 重要度聚合）
- Modify: `scripts/memos_topic.py:404-495`（旧候选评分兼容路径）
- Modify: `scripts/memos_topic.py:1635-1850`（SQLite 迁移兼容路径）
- Test: `tests/scripts/test_memos_topic.py:209-375`
- Test: `tests/scripts/test_memos_topic.py:1268-1312`
- Test: `tests/scripts/test_memos_topic.py:1439-1600`

**Interfaces:**

- `MemoryAssessment.score` 改为单条记忆静态重要度
- `CandidateMetrics.importance_score: float` 成为候选资格的唯一分数
- `score_breakdown.base_score` 暂时作为 `importance_score` 的兼容别名
- 时间临近只由 Task 1 的纯函数计算，不再写入单记忆重要度

- [ ] **Step 1：将旧测试改成新语义，并先确认失败**

新增或改名为：

测试名称固定为：

- `test_memory_importance_excludes_approaching_bonus`
- `test_time_bonus_cannot_make_subthreshold_topic_eligible`
- `test_existing_v3_assessment_recalculates_static_importance_without_llm`
- `test_topic_importance_uses_strongest_plus_half_of_unique_supporting_memories`
- `test_duplicate_memory_text_is_counted_once`
- `test_legacy_sqlite_migration_keeps_compatible_score_fields`
- `test_legacy_topic_without_assessment_excludes_old_urgency_from_importance`

删除旧断言“临近后 `MemoryAssessment.score` 上升”和“`base_score × recency_factor` 等于排名分”。
保留当前 revision 回归测试和 `shared_anchor` 测试，不得删改其目的。

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -m pytest `
  tests\scripts\test_memos_topic.py `
  -q -p no:cacheprovider -k "importance or subthreshold or duplicate or legacy_sqlite"
```

Expected: 旧实现因为仍将 `urgency_points` 加入分数而失败。

- [ ] **Step 2：修改单条记忆重要度公式**

`parse_memory_assessment()` 的唯一在线公式改为：

```python
static_points = (
    agency_points
    + action_points
    + impact_points
    + priority_points
    + effort_points
)
score = round(min(100.0, static_points) * confidence_factor, 2) if eligible else 0.0
```

新的 `score_breakdown` 不再生成在线 `urgency_points`：

```python
{
    "agency_points": agency_points,
    "action_points": action_points,
    "impact_points": impact_points,
    "priority_points": priority_points,
    "effort_points": effort_points,
    "confidence_factor": confidence_factor,
    "memory_importance": score,
}
```

旧 JSON assessment 如果还带 `urgency_points`，按已保存的离散字段重新算静态分；字段齐全时
不重新调用模型。删除在线 `_recency_factor()` 和 `_refresh_memory_assessment_score()` 的参与，
但旧 SQLite 迁移代码可以保留只读兼容转换。

- [ ] **Step 3：修改 Topic 聚合结果**

`compute_topic_metrics()` 固定返回：

```python
importance_score = min(
    100.0,
    strongest_memory_importance
    + sum(other_unique_memory_importance) * TOPIC_SUPPORTING_WEIGHT,
)
qualifies = importance_score >= TOPIC_SCORE_THRESHOLD
```

`CandidateMetrics` 增加显式 `importance_score`。`score_breakdown` 保留最强记忆、半权支持记忆、
去重计数和 `memory_scores`，并同时写：

```python
{
    "importance_score": importance_score,
    "base_score": importance_score,
}
```

这里不生成 `queue_score`，不做候选／核心状态判断。

- [ ] **Step 4：更新旧候选和 SQLite 迁移兼容代码**

`compute_candidate_metrics()` 只作为旧数据转换辅助，不得再进入在线排名。迁移出的旧 Topic
保留历史说明，第一次 Queue Policy v1 重排会用保存的离散 assessment 重算真实重要度。
更新旧测试中 `rank_score <= 100` 的假设：历史迁移值可以保留，但新 `queue_score` 的上限是 120。

旧 SQLite Topic 可能根本没有离散 assessment。此时不得调用模型，使用确定性降级值：

```python
legacy_importance = max(
    0.0,
    float(legacy_base_score) - float(legacy_urgency_points),
)
```

并标记 `importance_model="legacy_partial"`。后续 MemOS 对账真正拿到记忆和离散判断后，再替换成
`static_importance_v3`。

- [ ] **Step 5：运行完整 Topic 回归**

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -m pytest `
  tests\scripts\test_memos_topic.py `
  tests\scripts\test_memos_topic_queue.py `
  -q -p no:cacheprovider
```

Expected: 旧的分组、revision、Base64 清理、迁移测试和新的静态重要度测试全部通过。

- [ ] **Step 6：提交分数拆分**

```powershell
git add scripts/memos_topic.py tests/scripts/test_memos_topic.py
git diff --cached --check
git commit -m "refactor(topic): separate importance from time priority"
```

---

### Task 3：给 Topic JSON 增加 Queue Policy v1 状态

**Files:**

- Modify: `scripts/memos_topic.py:725-801`（状态读取、scope 初始化、兼容迁移）
- Modify: `scripts/memos_topic.py:1260-1296`（指标刷新）
- Modify: `scripts/memos_topic.py:1377-1482`（退休和 upsert）
- Modify: `scripts/memos_topic.py:1484-1555`（替换旧 Top-N rebalance 和查询）
- Test: `tests/scripts/test_memos_topic.py:1196-1251`
- Test: `tests/scripts/test_memos_topic.py:1345-1406`
- Test: `tests/scripts/test_memos_topic.py`（新增 Queue Store 状态测试）
- Create: `tests/fixtures/topic_queue_v0.json`（完全虚构、无私人内容的旧快照）

**Interfaces:**

- Produces in `scripts/memos_topic.py`: `QueueRebalanceResult`
- Produces: `TopicStore.mutate_queue_state(callback) -> Any`
- Produces: `TopicStore.rebalance_queue(user_id, cube_id, now, mode, policy, affected_topic_keys=None)`
- Produces: `TopicStore.rebalance_all_scopes(now, scheduled_slot, policy) -> dict[str, Any]`
- Produces: `TopicStore.list_queue_snapshot(user_id, cube_id, policy) -> dict[str, Any]`
- Produces: `_memory_queue_evidence_revision(memory) -> str`

`QueueRebalanceResult` 字段固定为：

```python
@dataclass(frozen=True)
class QueueRebalanceResult:
    core_topic_ids: list[str]
    visible_candidate_topic_ids: list[str]
    hidden_candidate_count: int
    promoted_topic_ids: list[str]
    demoted_topic_ids: list[str]
    retired_topic_ids: list[str]
    calculated_at: str
```

- [ ] **Step 1：先加入旧 JSON 迁移和状态机失败测试**

至少新增：

测试名称固定为：

- `test_old_snapshot_is_initialized_to_queue_policy_v1_without_llm`
- `test_old_active_topics_select_only_three_new_cores_without_demotion_penalty`
- `test_queue_migration_is_idempotent`
- `test_new_topic_starts_as_suppressed_new_candidate`
- `test_refreshed_candidate_clears_decay_only_for_valid_new_evidence`
- `test_old_historical_image_does_not_clear_decay`
- `test_text_only_revision_does_not_clear_decay`
- `test_status_or_event_time_revision_clears_decay`
- `test_promoted_topic_keeps_existing_penalty`
- `test_rank_score_is_kept_as_queue_score_alias`
- `test_queue_mutation_reads_and_writes_state_once`
- `test_one_topic_calculation_failure_preserves_its_previous_snapshot`
- `test_failed_atomic_replace_leaves_previous_json_readable`

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -m pytest `
  tests\scripts\test_memos_topic.py `
  -q -p no:cacheprovider -k "queue_policy or candidate or migration or decay"
```

Expected: 因 Topic 记录缺少 Queue Policy 字段和新状态接口而失败。

- [ ] **Step 2：增加 Topic 记录字段和懒迁移**

继续保持根 JSON：

```json
{"schema_version": 1, "updated_at": "2026-09-01T12:00:00+08:00", "scopes": {}}
```

每条在线 Topic 稳定增加：

```json
{
  "queue_policy_version": 1,
  "importance_score": 70,
  "approaching_bonus": 16,
  "decay_penalty": 10,
  "queue_score": 76,
  "rank_score": 76,
  "queue_score_breakdown": {
    "importance_score": 70,
    "approaching_bonus": 16,
    "decay_penalty": 10,
    "queue_score": 76
  },
  "candidate_source": "demoted",
  "attention_status": "open",
  "core_entered_at": null,
  "demoted_at": "2026-09-03T00:00:00+08:00",
  "penalty_at_demotion": 6,
  "last_evidence_revision": "queue-evidence-sha256",
  "calculated_at": "2026-09-04T00:00:00+08:00"
}
```

每个 scope 额外保存 `queue_calculated_at`，表示最近一次完整队列快照计算完成时间；根对象保存
`last_scheduled_slot`，只用于 00:00／12:00 幂等判定。HTTP 请求不得修改这两个时间。

第一次迁移旧快照时：

1. 用新静态公式重算所有有效 Topic。
2. 按新稳定排序选前三个为核心。
3. 其余旧 `active/suppressed` 都初始化为 `candidate_source=new`、`decay_penalty=0`。
4. 不把原 Top 15 中落到第 4～15 的 Topic 当成“刚降级”，也不追溯核心陈旧天数。
5. 旧 `retired` 保持退出状态。

- [ ] **Step 3：实现一次读、一次写的状态入口**

`mutate_queue_state()` 必须在一个共享 `RLock` 内完成：

```python
with self._lock:
    state = self._read_unlocked()
    result = callback(state)
    self._write_unlocked(state)
    return result
```

将现有 `_read/_write` 拆出不重复加锁的私有版本，保留对外行为。全量重排不得对每条 Topic
循环调用会独立落盘的 `refresh_topic_metrics()`。
`TopicStore.__init__` 的“文件不存在则创建空状态”也必须放进同一个共享 `RLock`，避免全新
安装时 scheduler 与第一个请求同时构造 Store，后创建的空文件覆盖已写状态。

全量重排以 Topic 为故障边界：一条 Topic 的时间或分数计算抛错时，保留它上一轮的全部队列
字段，并写入内部 `last_queue_error`、`last_queue_error_at`；其他 Topic 继续计算。无效或无法
解析的事件时间不是异常，按临近加分 0 处理。整个 JSON 写入失败时，`os.replace` 之前的旧文件
必须仍可读取。

- [ ] **Step 4：实现明确的纯状态转换**

在 `memos_topic.py` 中使用 Task 1 的纯规则，增加并测试以下私有转换：

固定私有接口为：

- `_promote_topic(topic: dict[str, Any], *, now: datetime) -> dict[str, Any]`
- `_demote_topic(topic: dict[str, Any], *, now: datetime, attention_status: str = "open") -> dict[str, Any]`
- `_refresh_topic_evidence(topic: dict[str, Any], *, previous_last_evidence_at: str | None, new_last_evidence_at: str | None, queue_evidence_revision_changed: bool, now: datetime) -> dict[str, Any]`
- `_retire_topic_from_queue(topic: dict[str, Any], *, reason: str, now: datetime) -> dict[str, Any]`

完成或取消必须令 `queue_score=rank_score=0`，但保留 `importance_score`。仅刷新文案或导入旧
历史证据不能清衰减。

`_memory_queue_evidence_revision()` 只包含 `event_status`、`event_time`、事件开始／结束字段和
`source_recorded_at`。普通正文润色虽然会改变现有 `_memory_revision()`，但不能清除 Topic 衰减。
降级时把当时已有扣分写入 `penalty_at_demotion`；重启后的快速衰减始终按
`max(penalty_at_demotion, target)` 计算。

进入核心后固定 `candidate_source=null`；降回候选时改为 `demoted`；新建候选为 `new`；候选
收到有效新证据为 `refreshed`。`queue_rank` 是各自队列内的排名：核心为 1～3，候选从 1
开始单独编号，不是全局 1～30。

- [ ] **Step 5：验证 JSON 状态测试通过**

Run Step 1 command again.

Expected: 迁移幂等、状态字段完整、旧数据不误罚、单次原子写测试通过。

- [ ] **Step 6：提交 Queue Policy 状态层**

```powershell
git add scripts/memos_topic.py tests/scripts/test_memos_topic.py `
  tests/fixtures/topic_queue_v0.json
git diff --cached --check
git commit -m "feat(topic): persist queue policy state"
```

---

### Task 4：实现 3 个核心、全部候选和升降级规则

**Files:**

- Modify: `scripts/memos_topic.py:1484-1555`（Queue Manager、可见快照和排序）
- Modify: `scripts/memos_topic.py:2667-2771`（新增记忆后的模式选择）
- Modify: `scripts/memos_topic.py:2819-2885`（删除／revision 对账后的队列刷新）
- Modify: `scripts/memos_topic.py:2964-3140`（重建结果和受影响 Topic）
- Modify: `scripts/memos_topic.py:3194-3235`（Runtime 兼容返回）
- Test: `tests/scripts/test_memos_topic.py`
- Test: `tests/scripts/test_memos_chat.py`

**Interfaces:**

- `mode="scheduled"`: 固定重排，替换差值为 5
- `mode="ingest"`: 普通新增只进候选；紧急事件使用 10 分差值
- `mode="vacancy"`: 完成／取消后补核心空位，不要求差值
- `past_unconfirmed` 候选不能在无新证据时晋升

- [ ] **Step 1：先写 3＋27 和稳定排序失败测试**

新增：

测试名称固定为：

- `test_scheduled_rebalance_keeps_three_core_topics`
- `test_snapshot_returns_only_twenty_seven_visible_candidates`
- `test_candidate_twenty_eight_remains_in_persistent_pool`
- `test_hidden_candidate_reappears_when_approaching_bonus_rises`
- `test_scheduled_promotion_requires_five_point_margin`
- `test_demoted_candidate_can_reenter_core_from_approaching_bonus`
- `test_vacant_core_seat_is_filled_without_margin`
- `test_equal_scores_use_importance_time_evidence_and_id_tie_breakers`
- `test_repeated_rebalance_produces_identical_order`
- `test_below_threshold_topic_is_hidden_but_not_retired`

稳定排序必须严格验证：

```text
queue_score 降序 → importance_score 降序 → 事件时间升序
→ last_evidence_at 降序 → topic_id 升序
```

无法解析的事件时间排在有明确事件时间之后；不得用 1970 或当前时间代替未知时间。

将旧 `test_json_store_keeps_yesterday_topic_and_rolls_only_top_fifteen` 改成跨日 3＋27 测试。

- [ ] **Step 2：运行并确认旧 Top-N 算法失败**

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -m pytest `
  tests\scripts\test_memos_topic.py `
  -q -p no:cacheprovider -k "scheduled_rebalance or twenty_seven or candidate_twenty_eight or tie_breakers"
```

Expected: 旧 `rebalance(limit=15)` 无法满足 3＋27、隐藏候选和 5 分差规则。

- [ ] **Step 3：实现固定重排**

`TopicStore.rebalance_queue(user_id=user_id, cube_id=cube_id, now=now, mode="scheduled", policy=policy)`
执行顺序固定为：

1. 先处理完成、取消和 `past_unconfirmed`。
2. 重算全部未结束 Topic 的 `approaching_bonus`、衰减和 `queue_score`。
3. 保留仍有资格的现有核心。
4. 核心不足三席时从可晋升候选直接补位。
5. 核心满三席时，候选必须 `>= lowest_core + 5` 才替换。
6. 每次替换后重新寻找最低核心，直到稳定。
7. 给核心和候选分别写真实 `queue_rank`。
8. 返回核心 3 条、可见候选 27 条和隐藏候选数量；不删除第 28 名。

重要度低于 60 的 Topic 写 `qualifies=False` 并从可见队列隐藏，但不能仅因低分直接删除记录。
`retire_unmatched_topics()` 必须区分“低于门槛”和真正退休原因；只有明确完成／取消／放弃、
重复合并、全部支持记忆失效或明确永久错误才写 `retired`，并保存 `retired_reason`。

- [ ] **Step 4：先写终态和结果未知失败测试**

新增：

测试名称固定为：

- `test_completed_topic_retires_and_immediately_fills_core_vacancy`
- `test_cancelled_topic_retires_and_immediately_fills_core_vacancy`
- `test_past_unconfirmed_core_demotes_before_candidate_ranking`
- `test_past_unconfirmed_candidate_cannot_repromote_without_new_evidence`
- `test_rescheduled_topic_clears_past_unconfirmed_and_decay`
- `test_new_non_actionable_historical_event_never_enters_candidate_pool`
- `test_ordinary_ingest_does_not_replace_a_full_core`
- `test_immediate_promotion_requires_ten_point_margin`
- `test_immediate_promotion_requires_event_before_next_scheduled_slot`
- `test_date_only_event_does_not_trigger_immediate_replacement`

第一版只能按明确结构化证据判断：

- `completed/cancelled`：可靠退出。
- 最新有效证据为 `ongoing`，并且至少一条有效 assessment 的 `action_requirement` 为
  `ongoing/clear_next_action/must_do`：确定性映射为 `actionable_overdue`，允许继续竞争。
- 其余曾经处于关注范围、时间已过但无结果：`past_unconfirmed`。
- 不得根据“面试”“作业”等词语猜测状态。

- [ ] **Step 5：实现终态、候选衰减和候选保留**

完成／取消使用 `mode="vacancy"` 立即退休并补位。时间已过但结果未知的核心先转为：

```json
{
  "lifecycle_status": "suppressed",
  "candidate_source": "demoted",
  "attention_status": "past_unconfirmed",
  "approaching_bonus": 0
}
```

它使用快速衰减，但没有新证据时永远不能升回核心。新证据若明确改期或带来新进展，改为
`candidate_source=refreshed`、`attention_status=open`、`decay_penalty=0`。

从未进入核心的 `new` 候选若时间后来经过，只标记 `past_unconfirmed` 并禁止晋升；它仍遵守
“新增候选不做陈旧衰减”，不能伪装成“核心降级”获得快速扣分。它可能自然跌出可见前 27，
但记录继续保留。若实践证明这类候选过多，再单独设计结果确认机制，不在本期偷加规则。

- [ ] **Step 6：实现新增记忆的即时规则**

`_rebuild_topics()` 返回新建、有效刷新、终态变化的 Topic key，而不是在内部直接执行旧 Top-N。
处理完成后调用 `mode="ingest"`：

- 普通新 Topic 只成为 `suppressed/new`。
- 普通刷新只更新受影响 Topic，不立即替换核心。
- 只有 `importance_score >= 60`、事件早于下一固定重排、且比分最低核心高至少 10 分，才即时挑战。
- 核心为空时，只有上述真正紧急候选可在两次固定重排之间直接补位；应用启动的漏跑补偿会负责初始化正常核心席位。

`process_runtime_topics()` 暂时保留 `rolling_limit`，但兼容值改为 3，并增加核心／候选计数；
`daily_limit` 参数不再能把核心上限调回 15。
`process_runtime_topics()` 必须使用 `DEFAULT_TOPIC_QUEUE_POLICY`，并把 policy 显式传给 Queue
Manager；API、Trace、即时刷新和 scheduler 都复用这一个固定的 3＋27／上海时区策略，不能再
让旧 `daily_limit` 改变核心数量。

- [ ] **Step 7：运行 Topic 与写入联动回归**

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -m pytest `
  tests\scripts\test_memos_topic_queue.py `
  tests\scripts\test_memos_topic.py `
  tests\scripts\test_memos_chat.py `
  -q -p no:cacheprovider
```

Expected: 设计文档 14 个验收场景均有测试覆盖，Runtime 写入仍能自动触发 Topic。

- [ ] **Step 8：提交队列管理器**

```powershell
git add scripts/memos_topic.py tests/scripts/test_memos_topic.py tests/scripts/test_memos_chat.py
git diff --cached --check
git commit -m "feat(topic): implement 3 plus 27 queues"
```

---

### Task 5：在唯一 app-backend 进程加入每天两次重排

**Files:**

- Modify: `scripts/memos_topic.py:1203-1230`（移除 15 分钟在线刷新语义）
- Modify: `scripts/memos_topic.py:3194-3235`（新增纯队列 wrapper）
- Modify: `scripts/memos_frontend_api.py:10-28`（`asyncio`、lifespan）
- Modify: `scripts/memos_frontend_api.py:400-430`（可注入的 scheduler）
- Modify: `scripts/memos_frontend_api.py`（应用 shutdown 附近）
- Test: `tests/scripts/test_memos_frontend_api.py`

**Interfaces:**

- Produces: `rebalance_runtime_topic_queues(*, now, scheduled_slot, policy) -> dict[str, Any]`
- Produces: `run_topic_scheduler(*, maintainer, stop_event, clock, wait_until, policy) -> None`
- Persists: root `last_scheduled_slot`

- [ ] **Step 1：先写时段、漏跑补偿和生命周期失败测试**

新增：

测试名称固定为：

- `test_scheduler_uses_asia_shanghai_midnight_and_noon`
- `test_scheduler_catches_up_exactly_one_missed_slot_on_startup`
- `test_scheduler_does_not_run_twice_for_the_same_slot`
- `test_lifespan_starts_and_stops_one_topic_scheduler_task`
- `test_scheduler_enabled_environment_controls_production_app`
- `test_scheduled_rebalance_does_not_construct_memos_client_or_topic_llm`
- `test_ingest_refresh_does_not_consume_the_scheduled_slot`

测试注入假 clock、假 wait 和假 maintainer；不得真实等待到 00:00 或 12:00。

- [ ] **Step 2：确认当前应用没有后台任务**

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -m pytest `
  tests\scripts\test_memos_frontend_api.py `
  -q -p no:cacheprovider -k "scheduler or scheduled_slot or lifespan"
```

Expected: 因缺少 scheduler 接口和 lifespan task 而失败。

- [ ] **Step 3：增加不依赖模型的重排 wrapper**

禁止复用 `reconcile_runtime_topics()`，因为 reconcile 会访问 MemOS，并可能因 revision 变化调用
模型。新增 wrapper 只能构造 `TopicStore` 和 Queue Manager：

```python
def rebalance_runtime_topic_queues(
    *,
    now: datetime | None = None,
    scheduled_slot: datetime | None = None,
    policy: TopicQueuePolicy | None = None,
) -> dict[str, Any]:
    effective_policy = policy or DEFAULT_TOPIC_QUEUE_POLICY
    reference_now = now or datetime.now(ZoneInfo(effective_policy.timezone_name))
    slot = scheduled_slot or latest_scheduled_slot(
        reference_now,
        effective_policy.timezone_name,
    )
    store = TopicStore(default_store_path())
    return store.rebalance_all_scopes(
        now=reference_now,
        scheduled_slot=slot,
        policy=effective_policy,
    )
```

`scheduled_slot` 必须传具体的 aware datetime，不能传布尔值。Store 在同一把锁、同一次原子
修改中比较和写入 `last_scheduled_slot`；相同槽位重复调用直接返回 `already_applied`。只有整次
状态写入成功后才推进槽位。

- [ ] **Step 4：使用 FastAPI lifespan 启动唯一任务**

应用启动时：

1. 用有效 policy 的时区计算最近一个 00:00／12:00 槽位。
2. 启动时把最近槽位交给幂等 wrapper；Store 自己判断是否补跑，避免先读后写竞态。
3. 之后睡眠到下一个槽位，并用 `asyncio.to_thread()` 执行同步 JSON 重排。
4. shutdown 设置停止事件并等待任务结束。

增加 `wait_until(target_slot, stop_event)` 注入点。生产实现使用
`asyncio.wait_for(stop_event.wait(), timeout=seconds)`；测试实现立即推进假 clock。每次调用
maintainer 必须使用
`await asyncio.to_thread(maintainer, now=clock(), scheduled_slot=slot, policy=policy)`，不能把
keyword-only 参数当位置参数。

`create_app()` 增加可测试注入：

```python
def create_app(
    *,
    store_factory: StoreFactory | None = None,
    client_factory: ClientFactory | None = None,
    importer: Importer = import_memory_file,
    reconciler: Reconciler = reconcile_runtime_topics,
    upload_dir: Path | None = None,
    auth_required: bool | None = False,
    topic_scheduler_enabled: bool | None = False,
    topic_policy: TopicQueuePolicy | None = None,
    topic_maintainer: TopicMaintainer = rebalance_runtime_topic_queues,
    topic_wait_until: TopicWaitUntil = wait_until_topic_slot,
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    """Create the application API and optionally own the Topic scheduler lifespan."""
```

`TopicMaintainer` 和 `TopicWaitUntil` 用 `Protocol` 明确 keyword 参数；Protocol 方法体用
`raise NotImplementedError`，不要使用不明确的 `Callable` 位置参数签名。

```python
class TopicMaintainer(Protocol):
    def __call__(
        self,
        *,
        now: datetime,
        scheduled_slot: datetime,
        policy: TopicQueuePolicy,
    ) -> dict[str, Any]:
        raise NotImplementedError


class TopicWaitUntil(Protocol):
    async def __call__(self, target_slot: datetime, stop_event: asyncio.Event) -> bool:
        raise NotImplementedError
```

FastAPI lifespan 的实现方式固定为：导入 `asynccontextmanager` 和 `AsyncIterator`，在
`create_app()` 内定义 `@asynccontextmanager async def lifespan(app: FastAPI) -> AsyncIterator[None]`，
并传给 `FastAPI(title="MemOS Application API", version="2.0.0", lifespan=lifespan)`。shutdown
时先 `stop_event.set()`，再等待当前很短的
`to_thread` 重排完成；不要取消后遗留仍在写 JSON 的后台线程。

模块级生产应用使用：

```python
app = create_app(auth_required=None, topic_scheduler_enabled=None)
```

`topic_scheduler_enabled=None` 表示由 `_topic_scheduler_enabled()` 读取
`MEMOS_TOPIC_SCHEDULER_ENABLED`；测试创建的 app 默认 `False`，除非该测试明确开启。
生产环境变量缺失时 `_topic_scheduler_enabled()` 默认返回 `True`，确保直接运行
`memos_frontend_api.py` 也具备每天两次重排；显式设为 `false` 才关闭。
`topic_policy=None` 只解析为固定的 `DEFAULT_TOPIC_QUEUE_POLICY`，随后 scheduler、API 快照和
Trace 共用该实例。

- [ ] **Step 5：替换旧 15 分钟刷新**

删除 `TOPIC_SCORE_REFRESH_SECONDS=15*60` 对在线排名的影响。`reconcile()` 继续负责删除和 revision
对账，但不能把普通对账误记成 00:00／12:00 已执行。手动“校准证据”完成后允许重算受影响
Topic，不推进 `last_scheduled_slot`。

- [ ] **Step 6：验证 scheduler 测试和 API 回归**

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -m pytest `
  tests\scripts\test_memos_topic_queue.py `
  tests\scripts\test_memos_topic.py `
  tests\scripts\test_memos_frontend_api.py `
  -q -p no:cacheprovider
```

Expected: 定时重排测试无真实 sleep；旧上传、认证、删除和 Runtime API 测试继续通过。

- [ ] **Step 7：提交单进程调度器**

```powershell
git add scripts/memos_topic.py scripts/memos_frontend_api.py `
  tests/scripts/test_memos_frontend_api.py
git diff --cached --check
git commit -m "feat(topic): schedule twice daily queue rebalance"
```

---

### Task 6：稳定应用 API 的 3＋27 契约

**Files:**

- Modify: `scripts/memos_frontend_api.py:142-150`（3／27 配置读取）
- Modify: `scripts/memos_frontend_api.py:347-384`（`_topic_item`）
- Modify: `scripts/memos_frontend_api.py:506-580`（Dashboard Topic）
- Modify: `scripts/memos_frontend_api.py:649-673`（Topic list、trace、reconcile）
- Test: `tests/scripts/test_memos_frontend_api.py:314-520`

**Interfaces:**

- Keeps: `GET /api/v1/topics`
- Keeps: `GET /api/v1/topics/{topic_id}/trace`
- Keeps: flat `items` for frontend compatibility
- Adds: queue metadata and transparent score fields

- [ ] **Step 1：先写 BFF 契约失败测试**

新增：

测试名称固定为：

- `test_v1_topics_returns_three_core_and_twenty_seven_visible_candidates`
- `test_v1_topics_reports_hidden_candidates_without_returning_them`
- `test_v1_topic_item_exposes_queue_score_breakdown_and_real_rank`
- `test_v1_dashboard_returns_only_three_core_topics`
- `test_v1_topic_trace_exposes_queue_policy_and_decision`
- `test_v1_topics_without_suppressed_returns_only_core_and_keeps_pool_counts`
- `test_legacy_topics_route_limits_response_without_truncating_persistent_pool`
- `test_both_reconcile_routes_cannot_restore_fifteen_core_seats`

固定响应构造方式：

```python
return {
    "total": len(items),
    "returned": len(items),
    "pool_total": snapshot["pool_total"],
    "candidate_pool_total": snapshot["candidate_pool_total"],
    "core_limit": 3,
    "visible_candidate_limit": 27,
    "core_count": len(core_items),
    "visible_candidate_count": len(candidate_items),
    "hidden_candidate_count": snapshot["hidden_candidate_count"],
    "calculated_at": snapshot["queue_calculated_at"],
    "items": items,
}
```

`items` 顺序必须是核心 1～3，再候选 1～27。为兼容旧接口，`total` 继续等于实际返回条数；
`pool_total` 才是全部仍参与竞争的 Topic 数。所有总数必须由 Store 的完整 snapshot 返回，不能
从旧的 `list_all_topics(limit=500)` 截断结果猜测。

`include_suppressed=false` 时只返回核心，`total/returned` 等于核心实际条数；`pool_total`、
`candidate_pool_total` 和 `hidden_candidate_count` 仍描述完整持久池。`calculated_at` 来自 scope
保存的 `queue_calculated_at`，没有完成过重排时为 `null`；绝不能用 HTTP 请求时间冒充。

- [ ] **Step 2：确认旧扁平返回缺少字段**

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -m pytest `
  tests\scripts\test_memos_frontend_api.py `
  -q -p no:cacheprovider -k "three_core or hidden_candidate or queue_score or queue_policy"
```

Expected: 旧 API 因缺少 3＋27 元数据和队列分字段而失败。

- [ ] **Step 3：实现向后兼容 Topic 项**

每个 `_topic_item()` 返回现有标题、理由、证据、版本字段，并增加：

```json
{
  "status": "active",
  "queue_rank": 1,
  "candidate_source": null,
  "attention_status": "open",
  "importance_score": 70,
  "approaching_bonus": 16,
  "decay_penalty": 10,
  "queue_score": 76,
  "score": 76,
  "queue_score_breakdown": {
    "importance_score": 70,
    "approaching_bonus": 16,
    "decay_penalty": 10,
    "queue_score": 76
  },
  "core_entered_at": "2026-09-01T00:00:00+08:00",
  "demoted_at": null,
  "calculated_at": "2026-09-01T12:00:00+08:00"
}
```

保留旧 `score` 和 `score_breakdown`；`score` 是 `queue_score` 的别名。前端不得用数组下标伪造
排名，必须使用 `queue_rank`。该字段是各自队列内排名：核心 1～3，候选也从 1 开始；必须
结合 `status` 理解，不是扁平列表的全局 1～30。

为了让后端与前端可以按两个提交落地，兼容期 BFF 在旧 `score_breakdown` 中保留已弃用的
`recency_factor=1.0` 和 `rank_score=queue_score`，同时输出新的 `queue_score_breakdown`。旧前端
只会短暂显示“100% 新鲜度”，最终 Task 7 会彻底停止使用这两个字段。不要让 Task 6 单独提交
后导致现有前端契约解析直接失败。

- [ ] **Step 4：扩展透明 Trace，不改变路由**

Trace policy 增加：

```json
{
  "queue_policy_version": 1,
  "core_limit": 3,
  "visible_candidate_limit": 27,
  "scheduled_promotion_margin": 5,
  "immediate_promotion_margin": 10,
  "queue_formula": "importance_score + approaching_bonus - decay_penalty"
}
```

Trace decision 增加重要度、临近加分、衰减扣分、队列分、真实队列排名、候选来源和注意力状态。
保留现有记忆初评、标签、分组 `shared_anchor` 和证据解释。

Trace 兼容期继续返回 `seat_limit=3`，以及已弃用的 `base_score=importance_score`、
`recency_factor=1.0`、`rank_score=queue_score`。`TopicStore.topic_selection_trace()` 可以保留旧
`seat_limit` 参数但必须夹到 3，或改为接收 policy 并同步修改全部调用和测试。

`reconcile_runtime_topics(base_url, daily_limit=None)` 暂时保留 `daily_limit` 参数以兼容现有两个
路由和 fake reconciler，但内部忽略大于 3 的值。删除 `_topic_daily_limit()` 时，三处调用和测试
必须在同一个任务内同步；任何 reconcile 都不能恢复 15 个核心。

- [ ] **Step 5：验证完整应用 API**

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -m pytest `
  tests\scripts\test_memos_frontend_api.py `
  tests\scripts\test_memos_app_auth.py `
  -q -p no:cacheprovider
```

Expected: 新 Topic 契约通过，登录、Bearer Token、上传和删除契约不变。

- [ ] **Step 6：提交 API 契约**

```powershell
git add scripts/memos_frontend_api.py tests/scripts/test_memos_frontend_api.py
git diff --cached --check
git commit -m "feat(api): expose Topic 3 plus 27 queues"
```

---

### Task 7：更新 TypeScript 契约和人话解释工具

**Files:**

- Modify: `frontend/lib/api-contract.ts:41-195`
- Modify: `frontend/lib/api-contract.ts:448-558`
- Modify: `frontend/lib/api-contract.ts:797-804`
- Modify: `frontend/lib/api-client.ts`
- Modify: `frontend/lib/topic-display.ts`
- Modify: `frontend/tests/api-contract.test.ts`
- Create: `frontend/tests/topic-display.test.ts`
- Modify: `frontend/package.json`

**Interfaces:**

- Produces: `TopicQueueBreakdown`
- Produces: `TopicImportanceBreakdown`
- Produces: `TopicCandidateSource`
- Produces: `TopicAttentionStatus`
- Produces: `formatTopicQueueExplanation(topic) -> string`
- Produces: `partitionTopicQueues(items) -> {core, candidates}`
- Produces: `filterTopicQueues(items, query, status) -> Topic[]`
- Extends: `TopicList` with 3＋27 metadata

- [ ] **Step 1：先写契约解析和解释文本失败测试**

新增测试：

```text
parses the Topic 3+27 queue contract with transparent score breakdown
rejects importance, bonus, decay and queue scores outside their ranges
preserves backend queue_rank after filtering
formats importance plus approaching minus decay in plain Chinese
labels demoted, refreshed and past-unconfirmed candidates
```

范围固定为：重要度 `0～100`、临近 `0～20`、衰减 `0～20`、队列分 `0～120`。
同时验证：

- `queue_rank` 必须是正整数，并且核心和候选分别从 1 编号。
- `status` 只能是 `active/suppressed`。
- `active` 数量不超过 3，返回的 `suppressed` 不超过 27。
- `returned === total === items.length`。
- `pool_total - core_count - candidate_pool_total === 0`。
- `candidate_pool_total - min(candidate_pool_total, visible_candidate_limit) === hidden_candidate_count`。
- 核心的 `candidate_source` 必须为 `null`；候选必须为 `new/demoted/refreshed`。
- `attention_status=past_unconfirmed` 必须同时是 `status=suppressed`；前端不得悄悄纠正后端错误。

更新 `package.json`：

```json
{
  "scripts": {
    "test": "node --experimental-strip-types --test tests/api-contract.test.ts tests/topic-display.test.ts && npm run build"
  }
}
```

- [ ] **Step 2：运行测试并确认旧契约失败**

```powershell
cd D:\project-memo\MemOS\frontend
node --experimental-strip-types --test `
  tests/api-contract.test.ts tests/topic-display.test.ts
```

Expected: 新测试因缺少 Queue 类型、范围校验和解释函数而失败。

- [ ] **Step 3：实现新类型，同时保留旧评分细节**

新增：

```typescript
export type TopicImportanceBreakdown = {
  model: "static_importance_v3";
  strongest_memory_score: number;
  supporting_memory_points: number;
  duplicate_memory_count: number;
  counted_memory_ids: string[];
  importance_score: number;
  base_score: number;
  memory_scores: Record<string, number>;
  legacy_recency_factor?: number;
  legacy_rank_score?: number;
};

export type TopicQueueBreakdown = {
  importance_score: number;
  approaching_bonus: number;
  decay_penalty: number;
  queue_score: number;
};

export type TopicCandidateSource = "new" | "demoted" | "refreshed" | null;
export type TopicAttentionStatus = "open" | "past_unconfirmed";
```

`Topic` 增加 `queue_rank`、四个分数、Queue breakdown、状态来源和时间字段。新的
`static_importance_v3` 不要求 `recency_factor/rank_score`；若 BFF 仍返回兼容字段，只映射到
`legacy_recency_factor/legacy_rank_score`，绝不能参与当前解释。解析器继续接受旧
`memory_importance_v2` 和 legacy 快照。`TopicList` 按 Task 6 的 JSON 精确解析。

在 `topic-display.ts` 实现 `partitionTopicQueues()` 和 `filterTopicQueues()`；Task 8 页面只调用
这些纯函数，所以“过滤不改排名”和“错误状态组合被拒绝”能够由当前 Node 测试环境验证。

- [ ] **Step 4：实现可复用的人话解释**

`formatTopicQueueExplanation()` 输出类似：

```text
重要度 70 分；事件临近增加 16 分；从核心降级后的陈旧衰减扣除 10 分；当前队列分 76 分。
```

`new/refreshed` 且扣分为 0 时，不输出“陈旧衰减扣 0 分”的废话。`past_unconfirmed` 必须附加
“事件时间已过，结果仍待确认，暂时只能留在候选队列”。

- [ ] **Step 5：验证契约和工具测试**

Run `npm test` again.

Expected: 两个 Node 测试文件和 TypeScript／Vite build 全部通过。

- [ ] **Step 6：提交前端契约层**

```powershell
cd D:\project-memo\MemOS
git add frontend/lib/api-contract.ts frontend/lib/api-client.ts `
  frontend/lib/topic-display.ts frontend/tests/api-contract.test.ts `
  frontend/tests/topic-display.test.ts frontend/package.json
git diff --cached --check
git commit -m "feat(frontend): support Topic queue contract"
```

---

### Task 8：把前端改成核心区、候选区和透明分数详情

**Files:**

- Modify: `frontend/app/topics/page.tsx:23-185`
- Modify: `frontend/app/topics/TopicDetailDrawer.tsx:72-128`
- Modify: `frontend/app/topics/TopicDetailDrawer.tsx:240-325`
- Modify: `frontend/app/topics/TopicProcessTrace.tsx`
- Modify: `frontend/app/page.tsx`（首页 Topic 区）
- Modify: `frontend/app/globals.css:490-650` 及对应响应式区段
- Test: `frontend/tests/topic-display.test.ts`

**Interfaces:**

- Core section: 最多 3 张大卡片
- Candidate section: 最多 27 张紧凑卡片
- Dashboard: 只显示核心 3 条
- Detail: 固定解释四项分数

- [ ] **Step 1：用纯展示测试锁定分区和真实排名**

在 `topic-display.test.ts` 增加纯函数测试，要求：

- `active` 只进入核心区。
- `suppressed` 只进入候选区。
- 搜索和筛选不会重写后端 `queue_rank`。
- `past_unconfirmed` 永远不出现在核心区。
- 空核心和空候选分别返回可展示的空状态。

先运行下面的 Node 测试确认红灯，不把 Vite build 错误和单元测试错误混在一起：

```powershell
cd D:\project-memo\MemOS\frontend
node --experimental-strip-types --test `
  tests/api-contract.test.ts tests/topic-display.test.ts
```

当前项目没有 React Testing Library、jsdom 或截图回归环境；卡片列数、手机布局和点击行为由
后面的 build＋手工视觉验收验证，不伪装成组件自动化测试。

- [ ] **Step 2：重写 Topic 页面分区**

顶部摘要显示：

```text
核心 Topic：2 / 3
可见候选：27 / 27
隐藏候选：8
最近重排：2026/09/01 12:00
```

页面主体明确分为：

1. “今天最重要的 Topic”——最多三张大卡片。
2. “候选 Topic”——最多二十七张紧凑卡片。

卡片显示后端 `queue_rank`，不再使用 `map((topic, index) => index + 1)`。核心卡片展示：

```text
队列分 76
重要度 70 + 临近 16 - 衰减 10
```

候选卡片增加“新增候选／核心降级／新证据刷新／结果待确认”标签。第 28 名以后不渲染卡片，
只显示隐藏数量。

- [ ] **Step 3：更新详情抽屉**

删除“基础分 × 新鲜系数”的当前解释，固定展示：

```text
重要度      70 / 100
事件临近    +16 / 20
陈旧衰减    -10 / 20
当前队列分  76 / 120
```

显示 `formatTopicQueueExplanation()` 的人话说明，并补充最近证据、核心进入、降级、本次计算时间。
现有逐条证据、原记忆跳转和 Topic 文案版本继续保留。

- [ ] **Step 4：更新透明 Trace 和首页**

`TopicProcessTrace.tsx` 保留当前 `shared_anchor`、离散维度和标签证据，替换旧
`rank_score=base_score×recency_factor` 说明为新公式和 5／10 分升降规则。

首页把“滚动 Top 15 席位”改为“今日核心 3 席”，只渲染 Dashboard 返回的核心 Topic，不把
候选混入首页。

- [ ] **Step 5：增加响应式样式**

- 桌面核心区固定三列，候选区使用更紧凑的三列。
- 中等宽度候选区两列。
- 390px 手机宽度两个区域都单列。
- `demoted/refreshed/past_unconfirmed` 使用不同但不过度鲜艳的标签。
- 详情抽屉在手机宽度下完整显示四项分数，不出现横向滚动。

- [ ] **Step 6：运行前端完整检查**

```powershell
cd D:\project-memo\MemOS\frontend
npm test
npm run lint
```

Expected: Node tests、TypeScript、Vite build 和 ESLint 全部通过。

- [ ] **Step 7：手工视觉验收**

在 1440px、900px、390px 三种宽度检查 `/topics` 和首页：

- 核心最多 3 条，候选最多 27 条。
- 搜索后编号保持原真实排名。
- 隐藏候选只显示数量。
- 四项分数与人话解释一致。
- `past_unconfirmed` 只在候选区。
- 详情中的来源记忆仍能打开。

- [ ] **Step 8：提交页面改造**

```powershell
cd D:\project-memo\MemOS
git add frontend/app/topics/page.tsx `
  frontend/app/topics/TopicDetailDrawer.tsx `
  frontend/app/topics/TopicProcessTrace.tsx `
  frontend/app/page.tsx frontend/app/globals.css `
  frontend/tests/topic-display.test.ts
git diff --cached --check
git commit -m "feat(frontend): render core and candidate Topics"
```

---

### Task 9：更新 Docker 配置并禁止第二个 Topic writer

**Files:**

- Modify: `docker/docker-compose.yml:37-67`
- Modify: `deploy/server/docker-compose.yml:37-70`
- Modify: `deploy/server/.server.env.example`
- Modify: `deploy/server/README_ZH.md`
- Create: `tests/scripts/test_topic_deployment.py`

**Interfaces:**

- Adds: `MEMOS_TOPIC_SCHEDULER_ENABLED=true`
- Removes: `MEMOS_TOPIC_DAILY_LIMIT=15`
- Keeps in code: fixed 3／27／`Asia/Shanghai` policy confirmed by the user

- [ ] **Step 1：先写 Compose 静态失败测试**

`tests/scripts/test_topic_deployment.py` 读取两份 Compose 文本或 YAML 渲染结果，并断言：

```python
assert topic_env["MEMOS_TOPIC_SCHEDULER_ENABLED"] == "${MEMOS_TOPIC_SCHEDULER_ENABLED:-true}"
assert "MEMOS_TOPIC_DAILY_LIMIT" not in topic_env
assert "topic-watch" not in services
assert "--workers" not in app_backend_command
assert services_with_topic_state == ["app-backend"]
assert services_mounting_data_topic == ["app-backend"]
assert app_backend_replicas in (None, 1)
```

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -m pytest `
  tests\scripts\test_topic_deployment.py `
  -q -p no:cacheprovider
```

Expected: 当前 Compose 仍包含 `MEMOS_TOPIC_DAILY_LIMIT=15`，所以失败。

- [ ] **Step 2：替换本地和服务器环境变量**

两份 Compose 删除旧 `MEMOS_TOPIC_DAILY_LIMIT`，并在 `app-backend.environment` 增加：

```yaml
MEMOS_TOPIC_SCHEDULER_ENABLED: ${MEMOS_TOPIC_SCHEDULER_ENABLED:-true}
```

3、27 和上海时区继续是代码中的已确认固定策略，不开放三个多余环境变量。不新增服务，不改变
Topic volume，不开放新的公网端口，不给 Uvicorn 增加 workers。

`.server.env.example` 和服务器 README 说明：

- Scheduler 已内置在 `app-backend`。
- 只允许一个 `app-backend` 副本写 Topic JSON。
- 不再启动 `python scripts/memos_topic.py watch`。
- `app-backend` 运行时不要同时运行会直写同一 Topic 文件的本地 `start_memos_chat.ps1`。
- 禁止 `docker compose --scale app-backend=2`。
- 重启会依据 `last_scheduled_slot` 补跑最近一次漏掉的固定重排。

- [ ] **Step 3：验证测试与 Compose 渲染**

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -m pytest `
  tests\scripts\test_topic_deployment.py `
  tests\scripts\test_server_deployment.py `
  -q -p no:cacheprovider

docker compose -f docker\docker-compose.yml config --services
```

Expected: 静态断言通过；本地服务仍为 `memos/app-backend/frontend/neo4j/qdrant`，没有 Topic
sidecar。服务器 Compose 的私密 `.server.env` 不存在时，不强行渲染，也不打印任何密钥。

- [ ] **Step 4：提交部署配置**

```powershell
git add docker/docker-compose.yml deploy/server/docker-compose.yml `
  deploy/server/.server.env.example deploy/server/README_ZH.md `
  tests/scripts/test_topic_deployment.py
git diff --cached --check
git commit -m "chore(topic): configure single process queue scheduler"
```

---

### Task 10：全量验证和真实场景验收

**Files:**

- Verify: Tasks 1～9 的全部文件
- Preserve: `.env`、`.server.env`、`.memos`、Neo4j／Qdrant volume、上传文件和真实 Topic JSON

**Interfaces:**

- Consumes: 完整 3＋27 实现
- Produces: 可部署、可解释、不会丢候选的最终版本

- [ ] **Step 1：运行所有相关 Python 测试**

```powershell
cd D:\project-memo\MemOS
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -m pytest `
  tests\scripts\test_memos_topic_queue.py `
  tests\scripts\test_memos_topic.py `
  tests\scripts\test_memos_chat.py `
  tests\scripts\test_memos_frontend_api.py `
  tests\scripts\test_memos_app_auth.py `
  tests\scripts\test_topic_deployment.py `
  tests\scripts\test_server_deployment.py `
  -q -p no:cacheprovider
```

Expected: zero failures。

- [ ] **Step 2：运行前端检查**

```powershell
cd D:\project-memo\MemOS\frontend
npm test
npm run lint
```

Expected: tests、TypeScript、Vite build、ESLint 全部退出 0。

- [ ] **Step 3：运行仓库格式化要求**

先对本任务 Python 文件做定向 Ruff，便于把格式变化限制在计划范围：

```powershell
.\.venv\Scripts\ruff.exe check --fix `
  scripts\memos_topic.py scripts\memos_topic_queue.py scripts\memos_frontend_api.py `
  tests\scripts\test_memos_topic.py tests\scripts\test_memos_topic_queue.py `
  tests\scripts\test_memos_frontend_api.py tests\scripts\test_topic_deployment.py
.\.venv\Scripts\ruff.exe format `
  scripts\memos_topic.py scripts\memos_topic_queue.py scripts\memos_frontend_api.py `
  tests\scripts\test_memos_topic.py tests\scripts\test_memos_topic_queue.py `
  tests\scripts\test_memos_frontend_api.py tests\scripts\test_topic_deployment.py
```

然后从具备 `make` 和 Poetry 的环境执行仓库要求。Git Bash：

```bash
cd /d/project-memo/MemOS
make format
```

WSL：

```bash
cd /mnt/d/project-memo/MemOS
make format
```

若当前 Windows 环境没有 `make`，先执行 Makefile 中完全等价的命令，再在 CI 或具备 make 的
环境补跑正式目标：

```powershell
poetry run ruff check --fix
poetry run ruff format
```

Expected: 格式化只触及本计划列出的文件；重新运行 Steps 1～2 仍全部通过。
如果格式化触及计划外文件，先逐项确认来源，不得顺手暂存，也不得用恢复命令覆盖用户改动。

- [ ] **Step 4：用隔离的临时 Topic JSON 验证 14 个场景**

不要直接拿真实 `.memos/topic/topics.json` 做破坏性测试。使用 pytest `tmp_path` 或临时环境变量
创建隔离状态，依次验证设计文档的 14 个验收场景，特别检查：

- 新候选等待 30 天仍无衰减。
- 核心 3／5／8／15 天衰减边界。
- 降级候选快速衰减到 20 后停止。
- 第 28 名仍存在，并可因事件临近回到前 27。
- 完成／取消立即退出并补核心空位。
- `past_unconfirmed` 先降候选，且无证据不回核心。
- 同一状态重复重排顺序完全一致。

- [ ] **Step 5：用脱敏旧快照 fixture 验证迁移**

运行 Task 3 创建的 `tests/fixtures/topic_queue_v0.json` 迁移测试。fixture 只包含虚构考试、面试和
项目 Topic，不复制、读取或输出真实 `.memos/topic/topics.json`。确认：

- 原 Top 15 中只有新算法前三名成为核心。
- 其他旧 Topic 没有被误加快速衰减。
- 第一次迁移不调用 Topic LLM。
- fixture 原文件保持不变，迁移结果只写 pytest 临时目录。

- [ ] **Step 6：验证部署进程和接口**

```powershell
docker compose -f docker\docker-compose.yml up -d --build `
  memos app-backend frontend neo4j qdrant
docker compose -f docker\docker-compose.yml ps
curl.exe http://127.0.0.1:8011/api/v1/health
curl.exe http://127.0.0.1:8011/api/v1/topics
```

Expected: 只有一个 app-backend；健康检查成功；Topic API 最多返回 3 个核心和 27 个候选；
`hidden_candidate_count` 正确；不需要另起 watch。

- [ ] **Step 7：审计最终改动和敏感文件**

```powershell
git status --short
git diff --check checkpoint/topic-queue-base..HEAD
git diff --name-status checkpoint/topic-queue-base..HEAD
git log --oneline -10
```

Expected: 只包含计划列出的代码、测试和文档。不得出现 `.env`、`.server.env`、Topic 真实 JSON、
上传文件、数据库、API key、密码哈希或 Token。

- [ ] **Step 8：完成验收提交（仅在格式化产生受控修改时）**

如果 `make format` 产生了计划范围内的格式调整，逐块暂存实际格式变化：

```powershell
git add -p
git diff --cached --check
git commit -m "style: format Topic queue implementation"
```

如果没有新修改，不创建空提交。

## 完成定义

只有同时满足以下条件，才能声明实施完成：

- 静态重要度与时间临近完全拆开，60 分门槛不受时间加分影响。
- JSON 保存全部有效候选，前端只展示前三个核心和前二十七个候选。
- 核心、降级候选、新候选、刷新候选使用各自正确的衰减规则。
- 完成／取消立即退出，时间已过但结果未知先降候选。
- 00:00／12:00 在同一个 app-backend 进程确定性重排，重启可补跑且不调用模型。
- API、Trace 和页面能解释 `重要度 + 临近 - 衰减 = 队列分`。
- 当前未提交的 revision、事件聚合、`shared_anchor` 和前端 Trace 功能没有回归。
- 所有相关 Python 测试、前端测试、构建、lint、`make format` 和真实部署 smoke test 通过。
