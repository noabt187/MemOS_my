# Topic 3+27 人工测试集（2026 年 9 月）

这套资料专门测试新的 Topic 队列，不测试长期陈旧衰减。

它主要验证：

1. 重要事件能进入 Topic 候选池。
2. 固定重排后恰好最多有 3 个核心 Topic。
3. 其余合格事件保留在候选队列。
4. 临近事件能得到可解释的临近加分。
5. 同一事件的后续进展会更新原 Topic，而不是产生两个 Topic。
6. 事件完成后，相应 Topic 能退出并由候选补位。
7. 重复导入不会重复抬高重要度。
8. 已完成的孤立事件和低价值观察不会进入 Topic。

所有内容都是虚构测试资料，不代表用户真实经历。

## 一、不要导入这两个说明文件

下面两个文件只供人查看，不要执行 `:import`：

- `README.md`
- `expected-results.md`

## 二、选择测试范围

### 方式 A：隔离测试，推荐

不会与 `default` 用户的 Topic 混在一起：

```powershell
cd <仓库目录>
.\start_memos_chat.ps1 --user topic_queue_test_20260901 --cube default_cube
```

这种方式主要在终端和 Topic CLI 中查看结果。当前前端默认读取 `default/default_cube`，不会自动显示这个隔离用户。

### 方式 B：直接在前端查看

如果你已经清空测试记忆，并且愿意把这些虚构资料写入默认用户：

```powershell
cd <仓库目录>
.\start_memos_chat.ps1 --user default --cube default_cube
```

导入后打开：

- 记忆页面：<http://127.0.0.1:3000/memories>
- Topic 页面：<http://127.0.0.1:3000/topics>

## 三、第一阶段：建立三个核心和三个候选

在 `你>` 提示符后，按顺序执行：

```text
:import "<仓库目录>\test-data\topic-queue-manual-2026-09\01-interview-plan.md"
:import "<仓库目录>\test-data\topic-queue-manual-2026-09\02-project-delivery.md"
:import "<仓库目录>\test-data\topic-queue-manual-2026-09\03-visa-appointment.md"
:import "<仓库目录>\test-data\topic-queue-manual-2026-09\04-course-registration.md"
:import "<仓库目录>\test-data\topic-queue-manual-2026-09\05-lease-renewal.md"
:import "<仓库目录>\test-data\topic-queue-manual-2026-09\06-club-presentation.md"
```

普通新 Topic 会先进入候选。系统会在上海时间每天 `00:00` 和 `12:00`自动重排。

如果不想等待下一次自动重排，可以在另一个 PowerShell 中执行下面的测试命令。

隔离用户：

```powershell
cd <仓库目录>
uv run --link-mode copy --frozen --extra skill-mem python -c "import sys; sys.path.insert(0, 'scripts'); from datetime import datetime; from zoneinfo import ZoneInfo; from memos_topic import DEFAULT_TOPIC_QUEUE_POLICY, TopicStore, _load_project_env, default_store_path; _load_project_env(); result=TopicStore(default_store_path()).rebalance_queue(user_id='topic_queue_test_20260901', cube_id='default_cube', now=datetime.now(ZoneInfo('Asia/Shanghai')), mode='scheduled', policy=DEFAULT_TOPIC_QUEUE_POLICY); print(result)"
```

默认用户：

```powershell
cd <仓库目录>
uv run --link-mode copy --frozen --extra skill-mem python -c "import sys; sys.path.insert(0, 'scripts'); from datetime import datetime; from zoneinfo import ZoneInfo; from memos_topic import DEFAULT_TOPIC_QUEUE_POLICY, TopicStore, _load_project_env, default_store_path; _load_project_env(); result=TopicStore(default_store_path()).rebalance_queue(user_id='default', cube_id='default_cube', now=datetime.now(ZoneInfo('Asia/Shanghai')), mode='scheduled', policy=DEFAULT_TOPIC_QUEUE_POLICY); print(result)"
```

重排后重点检查：

- `core_count` 应为 3。
- 其余合格 Topic 应为 `suppressed`，并有候选排名。
- 9 月 2 日、3 日、4 日的事件应有逐级降低的临近加分。
- 所有队列分都应满足：`queue_score = importance_score + approaching_bonus - decay_penalty`。

## 四、第二阶段：测试更新、完成和重复

继续按顺序导入：

```text
:import "<仓库目录>\test-data\topic-queue-manual-2026-09\07-interview-progress.md"
:import "<仓库目录>\test-data\topic-queue-manual-2026-09\08-project-completed.md"
:import "<仓库目录>\test-data\topic-queue-manual-2026-09\09-expense-reimbursement.md"
:import "<仓库目录>\test-data\topic-queue-manual-2026-09\09-expense-reimbursement.md"
```

检查：

- `TQ-INTERVIEW-0902` 应仍然只有一个 Topic，但证据和理由会增加。
- `TQ-DELIVERY-0903` 完成后应退出在线队列，并由候选补位。
- 报销文件连续导入两次后，不应出现两个报销 Topic。
- 如果 MemOS 保留了两条相同记忆，Topic 的 `duplicate_memory_count` 可以增加，但 `importance_score` 不应被重复内容抬高。

## 五、第三阶段：测试不应入选的内容

```text
:import "<仓库目录>\test-data\topic-queue-manual-2026-09\10-completed-standalone.md"
:import "<仓库目录>\test-data\topic-queue-manual-2026-09\11-negative-observation.md"
```

这两个文件不应增加核心或候选 Topic 数量。

## 六、可选：测试改期

```text
:import "<仓库目录>\test-data\topic-queue-manual-2026-09\12-interview-rescheduled.md"
```

预期仍是同一个 `TQ-INTERVIEW-0902` Topic。事件时间应改为 9 月 8 日，临近加分下降，不应再出现第二个面试 Topic。

## 七、查询完整结果

隔离用户：

```powershell
cd <仓库目录>
.\start_memos_topic.ps1 list --user topic_queue_test_20260901 --cube default_cube --include-suppressed
```

默认用户：

```powershell
.\start_memos_topic.ps1 list --user default --cube default_cube --include-suppressed
```

## 八、结果允许哪些变化

模型生成的 Topic 句子可以与说明文件不同，具体重要度也可能有小幅差异。

下面这些不能随意变化：

- 事件编号、日期、状态不能被编造或串线。
- 同一编号的计划、进展和结果应属于同一具体事件。
- 固定重排后核心不能超过 3 个。
- 新候选不应因为刚进入候选队列而产生陈旧扣分。
- 完成事件不能继续长期占据核心或候选席位。
- 重复输入不能重复增加重要度。
- 每个 Topic 的理由必须列出真实的支持记忆 ID。

更详细的逐文件预期见 `expected-results.md`。

## 九、这套数据不测试什么

陈旧衰减需要跨越多天，不能靠立即连续导入可靠验证。该机制已经由可注入时间的自动化测试覆盖，不需要真的等待 3 天或 15 天。

这套资料有效期以 2026 年 9 月初为主。如果以后再次使用，应整体平移所有事件日期，避免系统把它们当成历史事件。
