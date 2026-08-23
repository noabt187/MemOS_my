# MemOS 图文记忆与 Topic 测试集

这个目录用于测试三层能力：

1. Markdown 中的文字和图片能否按原始顺序联合解析。
2. 第二轮能否把相关碎片合并成完整事件，并把固定字段放在 `metadata.info`。
3. 外部 Topic 系统能否基于最终事件记忆累积证据、生成 Topic、更新 Topic，并列出逐条理由。

## 测试文件和预期主题

| 顺序 | 文件 | 主要预期 |
|---|---|---|
| 1 | `01-final-exam-schedule.md` | 生成数据结构考试安排事件 |
| 2 | `02-final-exam-review.md` | 增加复习进度证据，形成或更新“数据结构期末考试” Topic |
| 3 | `03-badminton-plan.md` | 生成校赛训练计划，并为林誉恒联系人增加历史事件 |
| 4 | `04-badminton-training.md` | 增加已完成训练证据，形成或更新“校赛羽毛球训练” Topic |
| 5 | `05-memos-video-parser-plan.md` | 生成视频解析器开发计划 |
| 6 | `06-memos-video-parser-progress.md` | 增加开发进度证据，形成或更新“MemOS视频解析器开发” Topic |
| 7 | `07-negative-public-image.md` | 不应产生有效个人事件，不应生成 Topic |

Topic 的实际文字可以不同，但正常情况下应出现三个主要主题：

- 用户正在准备数据结构期末考试，考试安排已明确并且正在持续复习。
- 用户正在为校赛进行羽毛球双打训练，已经有计划和一次训练记录。
- 用户正在开发 MemOS 视频解析器，已经从计划推进到初版关键帧抽取。

每个 Topic 的 `reason_evidence` 必须列出真实记忆 ID、对应事实和该事实对 Topic 的贡献，不能只说“多条记忆显示”。

## 推荐测试步骤

启动终端客户端即可。Topic 会在每次记忆导入成功后自动处理，不需要第二个 PowerShell：

```powershell
cd D:\project-memo\MemOS
.\start_memos_chat.ps1
```

在 MemOS 终端里按顺序导入：

```text
:import "D:\project-memo\MemOS\test-data\topic-mixed-tests\01-final-exam-schedule.md"
:import "D:\project-memo\MemOS\test-data\topic-mixed-tests\02-final-exam-review.md"
:import "D:\project-memo\MemOS\test-data\topic-mixed-tests\03-badminton-plan.md"
:import "D:\project-memo\MemOS\test-data\topic-mixed-tests\04-badminton-training.md"
:import "D:\project-memo\MemOS\test-data\topic-mixed-tests\05-memos-video-parser-plan.md"
:import "D:\project-memo\MemOS\test-data\topic-mixed-tests\06-memos-video-parser-progress.md"
:import "D:\project-memo\MemOS\test-data\topic-mixed-tests\07-negative-public-image.md"
```

每次导入后，当前终端会直接打印最新 Topic、理由和记忆证据。也可以另行查询当天 Topic：

```powershell
cd D:\project-memo\MemOS
.\start_memos_topic.ps1 list --user default --cube default_cube --date 2026-08-20
```

如果自动处理曾因网络或模型配置失败，可以手动补处理一次队列：

```powershell
.\start_memos_topic.ps1 run-once
```

## 判定重点

- 同一文件里的文字、图片说明和图片证据应共同生成完整事件，而不是机械地“一张图片一条记忆”。
- 考试、羽毛球、视频解析器三个主题不能互相串线。
- Topic 至少有两条相关记忆时，应综合全部相关证据重新总结。
- 新事件到来后，Topic 的 `topic_text`、执行状态和理由可以更新。
- 林誉恒只应有一条联系人记忆，历史事件列表可以包含训练计划和实际训练。
- `07-negative-public-image.md` 不应被公开图片诱导成“用户今天学习”的事件。
- 最终事件 `metadata.info` 中不应出现 `event_purpose` 或 `purpose_basis`。

## 图片来源

为了避免网络图片防盗链和导入时超时，图片已下载到 `images` 目录，Markdown 使用相对路径。

- `study-desk.jpg`：[Desk with textbooks.jpg](https://commons.wikimedia.org/wiki/File:Desk_with_textbooks.jpg)，Wikimedia Commons，CC BY-SA 4.0。
- `laptop-desk.jpg`：[Laptop on a desk.jpg](https://commons.wikimedia.org/wiki/File:Laptop_on_a_desk.jpg)，Wikimedia Commons，CC0。
- `badminton-racket.jpg`：[Badminton racket.jpg](https://commons.wikimedia.org/wiki/File:Badminton_racket.jpg)，Wikimedia Commons，CC BY-SA 4.0。
- `badminton-court.jpg`：[Badminton court view.jpg](https://commons.wikimedia.org/wiki/File:Badminton_court_view.jpg)，Wikimedia Commons，CC0。

这些图片只是合成测试素材，不代表用户真实经历。
