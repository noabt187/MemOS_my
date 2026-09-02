# 当前 MemOS 记忆系统机制说明

本文只说明这套仓库当前实际实现了什么、数据如何流动、哪些地方仍有限制。

先说结论：

- MemOS 是记忆事实库。最终个人记忆只保留“事件记忆”和“联系人记忆”两类。
- 一次导入会经过两轮模型处理。第一轮理解原始文字、图片、图文或视频；第二轮同时查看原始材料、第一轮结果和召回到的旧事件，再决定新增、更新还是忽略。
- ADD、UPDATE、NONE 的语义判断由模型负责。程序不代替模型理解事件，只检查输出结构、目标范围和较旧来源不能覆盖较新证据等安全边界。
- Neo4j 保存记忆正文、结构化字段、版本和关系；Qdrant 保存向量索引。它们不是 JSON 文件。
- Topic 是记忆之上的派生注意力层，不是第三类记忆。当前采用 3 个核心席位和 27 个可见候选席位。
- Plan Tracker 只负责“计划时间已经到了但结果未知”的确定性状态转换。它不会猜测事情已经完成或取消。
- 浏览器只应访问前端。前端通过应用后端访问 MemOS，8000 和 8011 不应直接暴露到公网。

---

## 1. 整体结构

当前系统可以理解为四层：

~~~text
用户
  │
  ├─ 命令行 Runtime
  └─ 浏览器前端
         │
         ▼
应用后端 8011
  ├─ 认证和会话
  ├─ 上传、对话、搜索、删除
  ├─ Topic 生成和 3+27 队列维护
  └─ Plan Tracker
         │
         ▼
MemOS 核心 API 8000
  ├─ 第一轮多模态解析
  ├─ 第二轮个人记忆整理
  ├─ ADD / UPDATE / NONE
  └─ 检索
         │
         ├─ Neo4j：记忆事实、结构、版本和关系
         └─ Qdrant：向量索引
~~~

这里最重要的边界是：

- MemOS 记忆是事实来源。
- Topic 只是从记忆计算出来的“当前关注事项”。
- Plan Tracker 只是运行索引。
- Topic 文件或 Plan Tracker 文件损坏时，可以根据 MemOS 记忆重新生成。
- 反过来，不能拿 Topic 或 Plan Tracker 当作原始记忆恢复 Neo4j。

### 1.1 Memory Cube 是什么

Memory Cube 可以理解为“一组记忆的命名空间”。

每次写入和检索至少会带：

- user_id：这是谁的记忆。
- cube_id：这批记忆属于哪个 Cube。

当前应用后端默认使用：

~~~text
user_id = default
cube_id = default_cube
~~~

同一个 Neo4j 和 Qdrant 可以服务多个用户或多个 Cube，但查询、事件更新和 Topic 处理必须保持在同一 user_id 与 cube_id 范围内。Memory Cube 不是某个单独 JSON 文件，也不是另一套数据库；它是 MemOS 用来隔离和组织记忆的逻辑范围。

---

## 2. 输入入口和自动路由

### 2.1 命令行 Runtime

入口脚本：

~~~powershell
cd <仓库目录>
.\start_memos_chat.ps1
~~~

进入交互界面后，最简单的导入方式是：

~~~text
:import "D:\资料\一条记忆.md"
~~~

也可以把文件直接拖进 PowerShell，再在路径前输入 :import。

主要实现：

- scripts/memos_chat.py
- scripts/start_memos_chat.ps1

### 2.2 前端上传

前端上传页接收一个文件，然后调用：

~~~text
POST /api/v1/ingestions
~~~

应用后端先把文件保存到上传目录，再复用 scripts/memos_chat.py 中的 import_memory_file。

因此，命令行导入和前端上传最终走的是同一套文件识别与 MemOS 写入逻辑，不是两套互不相干的解析器。

### 2.3 文件如何自动识别

| 输入文件 | 自动走的路径 |
|---|---|
| .txt、.text | 纯文字 |
| 不含图片的 .md、.markdown | 纯文字 |
| .jpg、.jpeg、.png、.webp、.gif、.bmp | 单张图片 |
| 含文字和图片的 Markdown | 有序图文混合 |
| 只有一张图片且没有正文的 Markdown | 单张图片 |
| 含多张图片的 Markdown | 有序图文混合 |
| .mp4、.mov、.mkv、.avi、.webm、.mpeg、.mpg、.m4v | 视频 |

运行时会自动选择 text、image、mixed 或 video 路径，用户不需要手写底层 JSON。

### 2.4 Markdown 图文格式

推荐目录：

~~~text
一次活动/
  记忆.md
  images/
    01.jpg
    02.jpg
~~~

记忆.md 示例：

~~~markdown
# 2026 年 9 月 1 日项目讨论

下午和张三讨论了毕业项目的接口设计。

![会议白板](images/01.jpg)

张三建议先固定 API 输入格式，再处理前端展示。

![聊天补充](images/02.jpg)
~~~

系统会保留原始顺序：

~~~text
文字 1 → 图片 1 → 文字 2 → 图片 2
~~~

模型收到的每个部分都有 part_id。它需要联合理解整段序列，而不是机械地“一张图片生成一条记忆”。

Markdown 规则：

- 文件按 UTF-8 读取，也支持 UTF-8 BOM。
- 本地图片路径相对于 Markdown 文件所在目录解析。
- 图片说明文字会作为图片上下文。
- HTML 注释不会作为记忆正文。
- 当前不接受 Markdown 中的网络图片地址。

### 2.5 当前前端上传的一个限制

前端上传页目前一次只上传一个文件。

如果上传的 Markdown 引用了 images/01.jpg，应用后端只收到 Markdown 本身，没有收到 images 文件夹。文件被保存到上传目录后，相对图片通常找不到。

所以当前建议：

- 纯文字 Markdown 可以直接从前端上传。
- 带相对本地图片的 Markdown，优先用本机 Runtime 的 :import。
- 如果后续要让网页稳定上传 Markdown 加附件包，需要新增“多文件上传”或“ZIP 包导入”。当前还没有实现。

---

## 3. 一次导入的完整处理链路

Runtime 的 text、image、mixed、video 导入都使用：

~~~text
async_mode = sync
mode = fine
~~~

这很重要。sync + fine 会等待精细解析完成，并进入第二轮个人记忆整理。

完整链路：

~~~text
原始文件或文字
  │
  ▼
Runtime 自动识别输入类型
  │
  ▼
POST /product/add
  │
  ▼
第一轮：按模态理解材料，生成候选记忆片段
  │
  ▼
召回最多 5 条可能相关的旧事件
  │
  ▼
第二轮：原始材料 + 第一轮片段 + 候选旧事件
  │
  ▼
模型输出：
  ├─ events：ADD / UPDATE / NONE
  ├─ contact_updates
  └─ discarded
  │
  ▼
程序安全校验
  │
  ├─ 合格：写入 Neo4j 和 Qdrant
  └─ 不合格：拒绝危险更新或丢弃无效结果
  │
  ▼
应用后端/Runtime 把成功生成的记忆交给 Topic
~~~

---

## 4. 第一轮：理解原始材料

第一轮的任务是“看懂输入，并提取候选事实”。

### 4.1 纯文字

普通文字解析器会提取：

- 已经发生的事情
- 正在发生的事情
- 计划
- 决定
- 明确态度
- 稳定偏好或持续状态

第一轮不应为了增加条数而把一句完整事实拆成多个失去上下文的片段。

### 4.2 单张图片

图片解析器会结合可见内容和用户提供的说明，识别：

- 图片中的人物、物体、场景
- OCR 文字和界面数据
- 与用户记忆有关的事实

图片说明只用于提供上下文，不应覆盖图片中的可见证据。

### 4.3 有序图文

图文解析器会把文字与图片当作一个有顺序的整体：

- 前文可以解释后面的图片。
- 后文可以补充或纠正前面的图片。
- 相邻图片可以是同一事件的连续画面。
- 日期、时间、地点或主要行为不同的内容仍要拆开。
- 每条候选结果需要记录支持它的 part_id。

这解决了“先说一句话、发两张图、再说一句话、再发两张图”的连续输入问题。

### 4.4 视频

视频先经过视频输入路径，再根据时间顺序理解关键画面、界面变化和行为过程。

它与图片链路保持相同目标：最终仍然产出文字候选记忆，再进入第二轮整理和同一套存储，不新建视频专用记忆数据库。

当前本地视频处理会先把视频上传到配置的私有 OSS，再把可访问的签名地址交给模型。长期来源信息保存 OSS URI、文件名、哈希等，不保存整段视频 Base64。

### 4.5 对话

Runtime 中普通聊天默认是“查询和回答”，不会把每一句聊天自动写入记忆。

当前配置相当于：

~~~text
add_message_on_answer = false
~~~

只有显式执行记忆导入或记忆写入时，内容才进入 MemOS。

---

## 5. 第二轮：整理成最终个人记忆

第二轮是当前定制系统的关键。

它不只看第一轮生成的简短记忆，还会同时拿到：

1. 本次导入的原始材料。
2. 第一轮候选记忆。
3. 每条候选的来源对应关系。
4. 从数据库召回的候选旧事件。
5. 调用者明确提供的可信 info，例如 source_recorded_at。

因此，第二轮可以从原图、原文或原视频证据中补回第一轮遗漏的时间、人物、地点和对象。它不是只拿“已经有损压缩过的记忆句子”盲目补字段。

核心实现：

- src/memos/templates/memory_info_prompts.py
  - PERSONAL_MEMORY_NORMALIZE_PROMPT_ZH
- src/memos/memories/textual/relationship.py
  - PersonalMemoryNormalizer
- src/memos/multi_mem_cube/single_cube.py
  - 把读取、召回、整理、写入连接起来

### 5.1 第二轮必须解决的事情

- 把同一现实事件的碎片合成一条完整事件。
- 不让“此次聚餐”“这次约定”“20:10 结束”单独成为无法理解的孤立记忆。
- 保留不同时间段、不同人物、不同地点或不同主要行为的独立事件。
- 把“用户喜欢吃西红柿”这类稳定偏好保存为 ongoing 事件，而不是再增加第三种偏好记忆。
- 把纯人物关系事实交给联系人更新，不重复保存成事件。
- 把无法构成完整事件、也不是联系人证据的内容放入 discarded。
- 判断新事实应当 ADD、UPDATE 还是 NONE。

### 5.2 第二轮失败时

fine 模式下，如果第二轮模型调用失败或结果无法通过解析，系统不会把第一轮未经整理的碎片偷偷写入最终个人记忆。

常见表象是：

~~~text
Memory added successfully
本次共生成 0 条记忆
~~~

这通常表示请求本身完成了，但第二轮没有产出可写入的合格事件或联系人更新。此时应查看 MemOS 容器日志，而不是只看前端成功提示。

---

## 6. 最终只保留两类个人记忆

这里说的“两类”看 metadata.info.record_type：

- event
- person_relationship_summary

底层 metadata.memory_type 仍可能显示 LongTermMemory 或 UserMemory，这是 MemOS 引擎原有的存储类别，不代表又出现了第三种个人语义类型。

如果启用了原文件节点，图中还可能出现 RawFileMemory。它只是连接来源文件和最终记忆的技术节点，不是给用户检索和 Topic 计分的第三类个人记忆。

### 6.1 事件记忆

一条事件记忆必须离开原材料后仍能独立理解：

- 谁
- 做了什么或处于什么状态
- 对象是什么
- 有证据时的时间、地点和参与者

核心 info 字段：

~~~json
{
  "record_type": "event",
  "assertion_basis": "explicit_single",
  "event_group_id": null,
  "series_id": null,
  "event_type": "meeting",
  "event_status": "planned",
  "event_actor": "用户",
  "event_action": "参加面试",
  "event_target": "A公司",
  "participants": ["用户", "A公司面试官"],
  "participant_keys": ["用户的稳定人物键", "A公司面试官的稳定人物键"],
  "event_location": "杭州",
  "event_time": "2026-09-02T15:00:00+08:00",
  "event_start_time": null,
  "event_end_time": null,
  "event_time_text": "明天下午三点",
  "source_recorded_at": "2026-09-01T10:20:00+08:00"
}
~~~

几个时间字段不能混用：

| 字段 | 含义 |
|---|---|
| created_at | 这条记忆写入 MemOS 的时间，由系统生成 |
| source_recorded_at | 原始截图、消息或视频被记录的时间 |
| event_time | 事件主要发生时间、计划时间或截止时间 |
| event_start_time | 有明确时间范围时的开始 |
| event_end_time | 有明确时间范围时的结束 |
| event_time_text | 原始材料中的“明天、下周五、下午”等原话 |

没有证据时使用 null，不用“未知”、空字符串或伪造的具体分钟。

participant_keys 由程序根据 participants 生成，用于稳定关联联系人。模型只负责提供证据支持的人名，不直接编造人物键。

Runtime 导入本地文件时，source_recorded_at 默认取文件修改时间；直接输入文字时取当前记录时间。这个时间只用来说明材料何时产生，并帮助解析“今天、明天”等相对表达，不能直接冒充事件发生时间。

当前事件状态：

| event_status | 含义 |
|---|---|
| planned | 已计划，尚未有开始或完成证据 |
| ongoing | 正在进行，或稳定偏好、长期状态、持续目标 |
| due_unverified | 计划时间已经到达，但结果没有证据确认 |
| completed | 有明确完成证据 |
| cancelled | 有明确取消证据 |
| uncertain | 材料不足，无法可靠判断阶段 |

注意：事件 completed 后仍可以保持 activated，用于历史检索。“记忆节点可检索状态”和“现实事件是否完成”是两套不同含义。

### 6.2 联系人记忆

联系人记忆是一人一条关系摘要，不是“所有人放在一条大记忆”。

核心 info 字段：

~~~json
{
  "record_type": "person_relationship_summary",
  "relation_key": "稳定联系人键",
  "person_key": "稳定人物键",
  "person_name": "张三",
  "person_aliases": ["小张"],
  "relations": ["classmate", "project_partner"],
  "relation_status": "active",
  "historical_events": [
    {
      "event_id": "对应事件 memory_id",
      "summary": "2026年9月1日，用户与张三讨论毕业项目接口设计。"
    }
  ],
  "last_observed_at": "最近证据时间",
  "history_checked_at": "最近关系历史检查时间"
}
~~~

联系人节点保存全量历史事件的单向引用：

- event_id 用于精确打开原事件。
- summary 让人不打开事件也能快速知道发生过什么。
- 同一个事件可以被多个联系人引用。
- 事件本身不需要反向保存所有联系人摘要，避免双向冗余。

系统会定期检查 historical_events：

- 如果事件已被硬删除，移除失效引用。
- 根据仍有效的全部历史事件重新生成关系摘要。
- 新事件可以更新联系人关系类型和摘要。

当前默认每累计 5 条新的关联事件，或出现明确关系事实时，刷新联系人摘要；失效历史引用默认每 10 天检查一次。联系人更新失败不会回滚已经成功写入的事件，错误会进入 MemOS 日志。

联系人关系判断仍以事件证据为基础。一次多人活动不会自动证明两个人是朋友。

---

## 7. ADD、UPDATE、NONE 到底由谁决定

这是最容易被误解的一部分。

### 7.1 模型负责语义判断

第二轮模型查看：

- 新的原始材料
- 第一轮候选
- 召回的旧事件

然后为每个最终事件输出：

| 操作 | 含义 |
|---|---|
| ADD | 这是新的独立事件 |
| UPDATE | 这是同一旧事件的重要变化 |
| NONE | 旧事件已经完整覆盖，本次只是重复观察 |

模型还需要输出：

- target_memory_id
- changed_fields
- decision_reason
- 完整事件正文
- 完整 info

程序不会自己用关键词决定“这是同一场面试”。向量相似度也只用于召回旧候选，不负责最终合并。

### 7.2 程序只做安全边界

模型输出后，程序检查：

- JSON 和字段结构是否有效。
- source_indices 是否指向本次真实候选。
- operation 是否只能是 ADD、UPDATE、NONE。
- UPDATE 或 NONE 的 target_memory_id 是否真的存在于本轮召回的旧候选范围。
- 时间和 info 是否满足基本格式。
- 新材料的 source_recorded_at 是否早于旧事件已经使用的来源时间；更旧来源不能覆盖较新证据。
- 更新后是否与旧状态完全相同；完全相同就按 NONE 处理，不制造版本。

这是一道结构和旧证据安全闸门，不是第二个语义模型。程序不会再独立判断“人物、公司和日期是否真的表示同一事件”，也不会替模型重做事件身份推理。

### 7.3 为什么相似事件不会直接合并

以下内容都不足以单独证明是同一个事件：

- 都叫“面试”。
- 都叫“考试”。
- 都属于“项目任务”。
- 标签相同。
- 向量相似。
- 只有日期相同，但没有更具体对象。

可靠身份锚点包括：

- 相同 event_group_id。
- 相同的具体项目、任务或机构名称。
- 相同人物或机构，并且时间范围兼容。
- 相同具体对象，并且绝对时间范围兼容。
- 原文中的明确指代能唯一指向旧事件。

这些身份锚点是第二轮 Prompt 对模型的要求，不是程序里的关键词匹配规则。模型如果无法找到可靠锚点，应当输出 ADD。

### 7.4 更新如何写入

UPDATE 不会新建第二个活动事件。

程序会：

1. 保留原 memory_id。
2. 版本号加一。
3. 把旧正文和旧 info 放入 history。
4. 合并来源证据。
5. 用新正文和新 info 覆盖当前版本。
6. 同步更新 Neo4j 和 Qdrant。

NONE 不新增记忆，也不增加版本。

### 7.5 一个正确更新示例

旧记忆：

~~~text
用户计划于 2026 年 9 月 2 日 15:00 参加 A 公司面试。
~~~

新材料：

~~~text
A 公司通知用户，原定 2026 年 9 月 2 日 15:00 的面试已取消。
~~~

预期结果：

- operation = UPDATE
- target_memory_id = 原面试 ID
- event_status = cancelled
- 原 memory_id 不变
- version 加一
- history 保存原 planned 版本
- MemOS 返回同一个 memory_id 的新版本，供 Topic 和 Plan Tracker 刷新

注意：上面前六项是当前事件写入机制。Topic 的终态联动目前还有一个已知缺口：取消或完成事件被 Topic 重建过滤后，旧 Topic 有时只会变成 suppressed，没有立即 retired 和补位。详见 11.10 和 16.13。

不推荐只写：

~~~text
原定 9 月 2 日的面试取消了。
~~~

因为仅有“面试”和日期，可能无法证明它一定是数据库中的那一场。第二轮 Prompt 要求模型在不确定时选择 ADD。当前程序不会再独立做一次语义身份判断。

### 7.6 UPDATE 仍可能失败的原因

- 正确旧事件没有进入本轮最多 5 条候选。
- 新材料缺少机构、对象、人物等可靠身份锚点。
- 模型选择了 ADD。
- 模型返回的 target_memory_id 不在候选范围。
- 新材料的来源时间更旧，试图把较新状态倒退。
- 直接使用 fast 路径，绕过了正常 fine 语义判断。

日期、地点、对象、参与者或状态有冲突时，模型应结合完整上下文判断是“同一事件发生变化”还是“另一件事件”。程序不会再把这些字段做成硬编码的第二次语义否决。

当前 Runtime 导入默认 fine，所以正常 :import 会走更新判断。

当前还存在一个明确风险：如果模型错误地把两个事件判断为同一事件，并且它指定的 target_memory_id 正好在本轮候选范围内、新来源时间也不比旧来源更早，程序会接受 UPDATE。旧版本仍保存在 history 中，可以追溯，但当前事件会被错误改写。现阶段主要依靠第二轮 Prompt、具体身份锚点和测试样例降低这个风险，程序没有第二套语义判断来兜底。

---

## 8. 原文件、Base64 和来源证据

### 8.1 图片

本地图片在请求模型时会临时编码成 Base64。这只是传输方式。

持久化前会去掉内联 Base64，长期保存：

- 原文件路径
- 文件名
- 哈希
- 修改时间等来源信息

因此，Neo4j、Qdrant 和 Topic 不应该长期保存整段图片 Base64。

### 8.2 视频

视频不把完整 Base64 写入记忆。当前路径保存：

- OSS 媒体 URI
- 临时签名访问地址或来源地址
- 文件名
- 哈希
- 解析得到的事件证据

签名 URL 可能过期；长期引用应以 OSS URI 和稳定媒体标识为准。

### 8.3 本机 Runtime 与网页上传的区别

本机 Runtime：

- 原文件仍在用户原来的目录。
- MemOS 保存来源路径，但不会自动复制整个文件。
- 如果以后移动或删除原文件，来源路径会失效。

网页上传：

- 应用后端把文件复制到 .memos/uploads 或容器上传卷。
- 即使浏览器本机文件离开原位置，服务器仍保留上传副本。

---

## 9. 数据到底存在哪里

### 9.1 逻辑存储

| 数据 | 存储位置 | 保存内容 |
|---|---|---|
| 记忆事实 | Neo4j | 正文、key、metadata、info、状态、版本、history、关系 |
| 向量索引 | Qdrant | embedding 和检索 payload |
| 原图/原文来源 | 本机路径、网页上传目录或 OSS | 原始文件或媒体 |
| Topic 状态 | topics.json | 派生 Topic、证据 ID、理由、分数和队列状态 |
| Plan Tracker | tracker.json | memory_id、版本、检查时间和重试状态 |

Neo4j 和 Qdrant 都不是“项目目录里的一堆 JSON 文件”。

### 9.2 Windows 本地 Docker

Compose 中的主要卷：

~~~text
neo4j_data    → /data
neo4j_logs    → /logs
qdrant_data   → /qdrant/storage
~~~

这些数据在 Docker Desktop 的 Linux 虚拟磁盘里。不要期待在 `<仓库目录>` 下直接找到 Neo4j 数据文件。

查看实际卷：

~~~powershell
docker volume ls
docker volume inspect memos-dev_neo4j_data
docker volume inspect memos-dev_qdrant_data
~~~

Topic、Plan Tracker 和网页上传在本地使用目录映射：

~~~text
<仓库目录>\.memos\topic\topics.json
<仓库目录>\.memos\plan_tracker\tracker.json
<仓库目录>\.memos\uploads\
~~~

### 9.3 服务器 Docker

服务器 Compose 使用命名卷：

~~~text
neo4j_data
neo4j_logs
qdrant_data
topic_data
plan_tracker_data
upload_data
~~~

查看：

~~~bash
cd ~/memos-stack/MemOS/deploy/server
sudo docker compose --env-file .server.env config --volumes
sudo docker volume ls
sudo docker volume inspect memos-server_neo4j_data
~~~

注意：只备份 Git 仓库不等于备份记忆数据库。服务器迁移或重装前必须单独备份 Docker 卷和上传文件。

### 9.4 info 在 Neo4j 里的样子

API 逻辑上把自定义事件字段放在 metadata.info 下。

Neo4j 属性有类型限制，写库时部分 info 会被序列化或展开为节点属性；Qdrant payload 也会带检索需要的元数据。所以在 Neo4j 管理界面中看到字段“平铺”，不等于 API 的逻辑 schema 失效。

判断最终结构应以 MemOS API 返回的 metadata.info 为准，不要只看 Neo4j 浏览器的视觉排布。

---

## 10. 检索机制

### 10.1 Runtime 默认检索

Runtime 的 :search 和普通对话默认：

~~~text
mode = fast
top_k = 5
~~~

含义是“最多返回最相关的 5 条”，不是“只保存 5 条”。

如果数据库有 20 条相关记忆：

- 默认这次返回前 5 条。
- 其余 15 条仍在数据库。
- 换查询词、增大 top_k 或分页查看时仍可出现。

### 10.2 三种检索模式

| 模式 | 作用 |
|---|---|
| fast | 主要依靠向量和图召回，速度快 |
| fine | 查询扩展、重排等精细流程，速度更慢 |
| mixture | 先快速返回，再由异步精细流程补充 |

当前 Runtime 对话使用 fast，导入使用 fine。不要把“导入模式”和“搜索模式”混为一谈。

### 10.3 两个 top 5 不是一回事

系统里有两个容易混淆的 top 5：

1. 用户搜索 top_k=5：决定这次给用户显示几条。
2. 事件更新候选 top_k=5：第二轮最多给模型几条可能相关的旧事件。

前者只影响显示；后者会影响 UPDATE 能否找到正确旧事件。

### 10.4 精确读取

已经知道 memory_id 时，应按 ID 读取详情，不要再做语义搜索。

应用后端提供：

~~~text
GET /api/v1/memories/{memory_id}
~~~

前端总览和记忆页还支持分页或按创建时间读取近期记忆。

---

## 11. Topic：3 个核心加 27 个可见候选

Topic 是独立的派生系统，主要实现：

- scripts/memos_topic.py
- scripts/memos_topic_queue.py
- docs/superpowers/specs/2026-08-31-topic-3-plus-27-queue-design.md

### 11.1 Topic 从什么生成

Topic 只基于 MemOS 最终写入的记忆生成。

它不会直接拿原始截图、视频帧或 Base64 绕过 MemOS 做 Topic。正确顺序是：

~~~text
图片、视频、文字
  → MemOS 提取最终事件
  → Topic 读取这些事件
  → 生成候选主题、理由和分数
~~~

### 11.2 写入后如何自动触发

Runtime 或应用后端调用 /product/add 成功后：

1. 读取本次真正生成的 memory_id。
2. 把这些最终记忆交给 Topic 处理。
3. 提取可解释的主题标签和事件分组。
4. 对同一具体事项的记忆进行聚合。
5. 生成一句 Topic。
6. 生成 reason_summary。
7. 为每条计分记忆生成 reason_evidence，并引用真实 memory_id。
8. 由程序计算重要度和队列分。
9. 更新 3+27 队列。

如果 MemOS 最终生成 0 条记忆，Topic 没有合格输入，也不会凭原文件自己生成 Topic。

Topic 失败不会回滚已经写入的记忆。记忆是主事实，Topic 可以稍后对账修复。

### 11.3 为什么 Topic 理由是可核验的

Topic 不只保存一句笼统理由。

每个 Topic 至少包含：

- topic_text：一句话主题。
- reason_summary：总体原因。
- reason_evidence：逐条证据。
- supporting_memory_ids：支持该 Topic 的记忆 ID。
- grouping_reason：为什么这些记忆属于同一具体事项。
- score_breakdown：程序的分数明细。

例如，不应只写：

~~~text
考试临近，并且用户正在复习。
~~~

应当能够看到：

~~~text
记忆 A：考试时间为 9 月 5 日，说明截止时间临近。
记忆 B：用户 8 月 31 日完成了第一轮复习。
记忆 C：用户 9 月 1 日开始练习历年真题。
因此：考试时间已经明确，并且用户正在持续复习。
~~~

代码会校验 reason_evidence 引用的 memory_id 是否真实存在，并要求覆盖参与计分的记忆。

### 11.4 3+27 不是存储上限

- 3 个核心 Topic：lifecycle_status = active。
- 27 个页面可见候选：lifecycle_status = suppressed。
- 第 28 名以后的合格候选仍保存在 Topic 状态中，只是页面默认不显示。
- 队列规则把完成或取消的 Topic 定义为 retired，历史记录仍保留；但当前事件更新到 Topic 的终态同步存在 16.13 所述缺口。

所以“27 个候选”不是数据库最多只能有 27 个。

### 11.5 重要度门槛

一个 Topic 先看本身是否重要，再看时间临近。

资格门槛：

~~~text
importance_score >= 60
~~~

时间临近不能把本身低于 60 的小事强行抬进队列。

单条记忆重要度由模型给出离散语义标签，程序按固定表换算：

~~~text
memory_importance =
  (主动程度分
   + 行动要求分
   + 影响程度分
   + 用户明确优先级分
   + 已投入精力分)
  × 置信度系数
~~~

模型不直接输出最终数字。

当前固定换算表：

| 维度 | 模型标签 → 程序分值 |
|---|---|
| 主动程度 agency | observed 0，consumed 5，participated 12，acting 19，committed 25 |
| 行动要求 action_requirement | none 0，optional 5，ongoing 15，clear_next_action 20，must_do 25 |
| 影响 impact | trivial 0，limited 5，meaningful 10，high 15 |
| 明确优先级 explicit_priority | none 0，interested 3，important 7，must_or_remind 10 |
| 已投入精力 effort | none 0，some 3，substantial 5 |
| 置信度 confidence | low ×0.5，medium ×0.8，high ×1.0 |

如果记忆带有明确时长，程序还会按时长规则校正 effort：少于 5 分钟为 0，5 到 19 分钟为 1，20 到 59 分钟为 3，60 分钟及以上为 5。模型标签分和时长分取较高值，但最高仍为 5 分。时间紧迫不计入 importance_score，而是单独进入 approaching_bonus。

同一 Topic 有多条不重复证据时：

~~~text
importance_score =
  min(
    100,
    最强记忆重要度 + 其他不重复记忆重要度之和 × 0.5
  )
~~~

### 11.6 队列分

~~~text
queue_score = clamp(
  importance_score
  + approaching_bonus
  - decay_penalty,
  0,
  120
)
~~~

- importance_score：事情本身的重要性，0 到 100。
- approaching_bonus：事件临近加分，0 到 20。
- decay_penalty：陈旧扣分，0 到 20。
- queue_score：此刻在队列中的排序分，0 到 120。

所有数字由固定程序规则计算。模型负责理解语义和给出证据标签，但不能随意说“这件事 87 分”。

### 11.7 临近加分

事件时间读取顺序：

~~~text
event_start_time → event_time → event_end_time
~~~

有精确时刻时：

| 距离事件 | 加分 |
|---|---:|
| 超过 7 天 | 0 |
| 3 到 7 天 | 4 |
| 2 到 3 天 | 8 |
| 1 到 2 天 | 12 |
| 24 小时内 | 16 |
| 事件当天 | 20 |

只有日期时：

| 距离日期 | 加分 |
|---|---:|
| 今天 | 20 |
| 明天 | 16 |
| 后天 | 12 |
| 3 天后 | 8 |
| 4 到 7 天后 | 4 |
| 7 天以后或日期已过去 | 0 |

时间不明确就加 0 分，不制造分钟精度。

### 11.8 衰减

新候选和收到新证据的候选不做陈旧衰减。

核心 Topic 长期没有新证据时缓慢扣分：

| 无新证据天数 | 扣分 |
|---|---:|
| 0 到 2 天 | 0 |
| 3 到 4 天 | 3 |
| 5 到 7 天 | 6 |
| 8 到 14 天 | 10 |
| 15 天以上 | 15 |

从核心降级的候选快速扣分：

| 降级后天数 | 目标扣分 |
|---|---:|
| 0 到 1 天 | 5 |
| 2 到 3 天 | 10 |
| 4 到 7 天 | 15 |
| 8 天以上 | 20 |

新证据会清除衰减。降级候选仍可以因为事件临近而重新上升。

### 11.9 重排时间和升降级

系统按 Asia/Shanghai 每天重排两次：

~~~text
00:00
12:00
~~~

固定重排：

- 核心不足 3 个时，最高候选直接补位。
- 核心已满时，候选至少高出最低核心 5 分才替换。

两次固定重排之间：

- 普通新 Topic 只进入候选。
- 只有重要度至少 60、事件早于下一次固定重排发生、并且高出最低核心至少 10 分，才允许即时挑战核心。
- Topic 记录正确收到明确完成或取消状态时，会立即退出，并由最高候选补位。

定时重排不调用模型。模型服务临时不可用，不影响已有 Topic 的时间加分和衰减。

### 11.10 完成、取消和时间已过

队列规则定义如下：

| 情况 | Topic 处理 |
|---|---|
| completed | 立即 retired，queue_score 归零 |
| cancelled | 立即 retired，queue_score 归零 |
| 时间已过但结果未知 | 从核心降到候选，attention_status = past_unconfirmed |
| 明确改期并有新时间 | 清除衰减，按新时间重新计算 |

Topic 不会把“时间到了”直接解释成“已经完成”。

当前需要特别区分“队列规则”和“跨系统联动”：

- Topic 自己拿到 completed/cancelled 状态时，退休和清零规则可以执行。
- 但 MemOS 中同 ID 事件更新为 completed/cancelled 后，当前 Topic 刷新路径会先过滤终态记忆，旧 Topic 可能只被标成 suppressed。
- 这种情况下 retired_reason 为空、旧 progress_status 仍可能是 planned，也不会立即触发候选补位。

所以“取消一条记忆后旧 Topic 立即消失”目前不能当作已经稳定实现的能力。这是已确认的待修复问题，不是模型是否正确判断同一事件的问题。

### 11.11 Topic 页面里的版本号

页面可能同时显示几种版本，它们不是多条相同 Topic：

- version：这一条 Topic 文案或证据集合自己的修订次数。证据变化后会递增。
- selection_version：标签提取、重要度和 Topic 选择算法版本，当前为 3。
- queue_policy_version：3+27、临近加分、衰减和升降级规则版本，当前为 1。

旧状态文件中的 v1、v2 数据可以被迁移保留，用于历史追溯。当前重算和新生成结果按 selection_version=3 与 queue_policy_version=1 执行。

---

## 12. Plan Tracker：只处理到期未确认

主要实现：

- scripts/memos_plan_tracker.py
- src/memos/memories/textual/event_lifecycle.py
- src/memos/api/handlers/event_lifecycle_handler.py

### 12.1 它做什么

追踪满足条件的 planned 或 ongoing 事件。

当计划时间或明确结束时间到达后，如果没有完成或取消证据，它只允许：

~~~text
planned → due_unverified
ongoing → due_unverified
~~~

中文意思是“到期了，但结果还没确认”。

### 12.2 它不做什么

Plan Tracker 不会：

- 猜测用户已经完成。
- 猜测用户失败。
- 猜测用户取消。
- 根据 Topic 改写记忆。
- 用自然语言相似度寻找另一条事件。
- 保存一份事件正文副本。

真正的 completed、cancelled、延期或重新开始，必须来自新导入材料，并走第二轮 ADD、UPDATE、NONE。

### 12.3 精确更新

追踪器使用：

- memory_id
- expected_version
- 允许的目标状态

调用 8000 私网中的窄接口。它只能修改到 due_unverified，不能接受任意正文或任意目标状态。

版本不一致时，说明新证据已经先更新了记忆。追踪器会重新读取，不能覆盖新版本。

### 12.4 当前运行频率

当前 Compose 配置：

~~~text
MEMOS_PLAN_TRACKER_INTERVAL_SECONDS = 60
MEMOS_PLAN_TRACKER_RECONCILE_SECONDS = 900
~~~

60 秒是检查已经登记的到期项，不代表每分钟调用大模型。

900 秒是轻量全量对账，用于捕获绕过 8011、直接写入 8000 的事件或删除操作。

追踪器本身不调用模型。

---

## 13. 前端、应用后端和 MemOS 的边界

### 13.1 三个服务

| 服务 | 本地端口 | 主要职责 |
|---|---:|---|
| frontend | 3000 | 页面展示和用户操作 |
| app-backend | 8011 | 认证、稳定 API、上传、Topic、Tracker |
| memos | 8000 | 记忆解析、写入、更新和检索 |

前端不应直接连接 Neo4j、Qdrant 或 MemOS 8000。

浏览器请求：

~~~text
/api/v1/...
~~~

由前端 Nginx 反向代理到 app-backend:8011。

这样前端只依赖稳定应用 API，不需要理解 MemOS 内部复杂响应。

### 13.2 当前前端主要页面

- /：记忆总览、最近记忆、删除。
- /runtime：对话、搜索和手动写入。
- /upload：文件上传和解析结果。
- /topics：3 个核心 Topic、27 个候选、分数明细和证据链。
- /login：密码认证。

### 13.3 删除记忆

前端删除调用：

~~~text
DELETE /api/v1/memories/{memory_id}
~~~

流程：

1. 应用后端请求 MemOS 硬删除记忆。
2. 删除成功后重新对账 Topic。
3. 如果 Topic 同步暂时失败，记忆删除仍然成立。
4. Topic 会标记待同步，并在后续对账中清理失效证据。

### 13.4 公网部署

服务器部署只公开：

~~~text
80
443
~~~

Caddy 负责 HTTPS 和统一入口。frontend、app-backend、memos、Neo4j、Qdrant 都留在 Docker 私网。

推荐访问链路：

~~~text
手机或电脑
  → HTTPS 443
  → Caddy
  → frontend
  → /api/v1
  → app-backend 8011
  → memos 8000
~~~

不要把 8000、8011、7474、7687、6333 直接开放到公网。

### 13.5 认证

密码检查在应用后端，不在前端。

服务器关键配置位于：

~~~text
deploy/server/.server.env
~~~

字段：

~~~text
MEMOS_ACCESS_PASSWORD_HASH
MEMOS_SESSION_SECRET
NEO4J_PASSWORD
PUBLIC_HOST
ACME_EMAIL
~~~

- MEMOS_ACCESS_PASSWORD_HASH 是访问密码的哈希，不是现实邮箱密码。
- MEMOS_SESSION_SECRET 用于签名登录会话，不能放进前端代码。
- NEO4J_PASSWORD 是数据库内部密码。
- ACME_EMAIL 只用于证书通知，可以使用自己可接收通知的邮箱。

修改访问密码使用：

~~~powershell
# 本地开发：默认修改项目根目录 .env
.\scripts\set_memos_access_password.ps1

# 为服务器生成 deploy/server/.server.env 中的认证项
.\scripts\set_memos_access_password.ps1 --env-file .\deploy\server\.server.env
~~~

脚本会要求输入至少 12 个字符的新明文密码，只把哈希写入环境文件，同时生成新的会话密钥。明文密码不会保存。修改后需要重建 app-backend 容器才生效。

---

## 14. 主要配置

### 14.1 模型

模型配置主要放在项目根目录 .env。

关键变量：

| 变量 | 作用 |
|---|---|
| CHAT_MODEL_LIST | 可用聊天或多模态模型列表 |
| MEM_READER_BACKEND | 记忆读取器，当前默认 multimodal_struct |
| MEM_READER_MEM_VERSION_SWITCH | 旧版记忆重写开关，当前默认 off |
| MOS_EMBEDDER_BACKEND | 向量模型后端 |
| MOS_EMBEDDER_PROVIDER | OpenAI 兼容提供方 |
| MOS_EMBEDDER_API_BASE | 向量接口地址 |
| MOS_EMBEDDER_API_KEY | 向量接口密钥 |
| MOS_EMBEDDER_MODEL | 向量模型名称 |

图片、图文和视频需要实际支持视觉输入的聊天模型。仅名称看起来像 Qwen 并不能证明它支持图片，必须以所调用模型接口的能力为准。

MEM_READER_MEM_VERSION_SWITCH=off 只表示旧版 reader 内部的记忆重写功能关闭，不会关闭本文所说的新事件 ADD、UPDATE、NONE。新事件更新在 SingleCube 的第二轮整理后单独执行。

### 14.2 数据库

| 变量 | 作用 |
|---|---|
| GRAPH_DB_BACKEND | 图数据库后端 |
| NEO4J_BACKEND | 兼容旧名称 |
| NEO4J_URI | Neo4j Bolt 地址 |
| NEO4J_USER | Neo4j 用户 |
| NEO4J_PASSWORD | Neo4j 密码 |
| NEO4J_DB_NAME | 数据库名 |
| QDRANT_HOST | Qdrant 地址 |
| QDRANT_PORT | Qdrant 端口 |
| QDRANT_URL | 可选完整 URL |
| QDRANT_API_KEY | 可选认证密钥 |

Docker 内部会覆盖主机名，例如 qdrant-docker 或 qdrant、neo4j-docker 或 neo4j。不要把容器内部地址误改成 127.0.0.1。

### 14.3 Topic 和 Tracker

| 变量 | 作用 |
|---|---|
| MEMOS_TOPIC_ENABLED | Runtime 写入后是否自动处理 Topic |
| MEMOS_TOPIC_STATE | topics.json 路径 |
| MEMOS_TOPIC_SCHEDULER_ENABLED | 是否运行 00:00/12:00 重排 |
| MEMOS_PLAN_TRACKER_ENABLED | 是否启用计划事件追踪 |
| MEMOS_PLAN_TRACKER_STATE | tracker.json 路径 |
| MEMOS_PLAN_TRACKER_INTERVAL_SECONDS | 到期项检查间隔 |
| MEMOS_PLAN_TRACKER_RECONCILE_SECONDS | 全量对账间隔 |

### 14.4 应用后端

| 变量 | 作用 |
|---|---|
| MEMOS_API_BASE_URL | 8011 访问 MemOS 8000 的地址 |
| MEMOS_APP_USER_ID | 默认用户范围 |
| MEMOS_APP_CUBE_ID | 默认 Memory Cube |
| MEMOS_WEB_UPLOAD_DIR | 网页上传保存目录 |
| MEMOS_AUTH_REQUIRED | 是否强制认证 |
| MEMOS_ACCESS_PASSWORD_HASH | 访问密码哈希 |
| MEMOS_SESSION_SECRET | 会话签名密钥 |
| MEMOS_AUTH_COOKIE_SECURE | Cookie 是否只允许 HTTPS |

### 14.5 修改 .env 后是否需要重启

需要。

环境变量在进程启动时读取。修改 .env 后，已运行容器不会自动重新加载。

本地：

~~~powershell
cd <仓库目录>
docker compose -f .\docker\docker-compose.yml up -d --force-recreate memos app-backend
~~~

如果同时修改了代码或镜像依赖：

~~~powershell
docker compose -f .\docker\docker-compose.yml up -d --build
~~~

服务器：

~~~bash
cd ~/memos-stack/MemOS/deploy/server
sudo docker compose --env-file .server.env up -d --force-recreate memos app-backend
~~~

---

## 15. 常用启动和检查命令

### 15.1 本地整套启动

~~~powershell
cd <仓库目录>
docker compose -f .\docker\docker-compose.yml up -d --build
docker compose -f .\docker\docker-compose.yml ps
~~~

浏览器：

~~~text
http://127.0.0.1:3000
~~~

### 15.2 本地只重建某个服务

~~~powershell
docker compose -f .\docker\docker-compose.yml up -d --build --no-deps frontend
docker compose -f .\docker\docker-compose.yml up -d --force-recreate app-backend
docker compose -f .\docker\docker-compose.yml up -d --force-recreate memos
~~~

### 15.3 服务器整套启动

~~~bash
cd ~/memos-stack/MemOS/deploy/server
sudo docker compose --env-file .server.env up -d --build
sudo docker compose --env-file .server.env ps
~~~

### 15.4 健康检查

本机：

~~~powershell
curl.exe http://127.0.0.1:8000/health
curl.exe http://127.0.0.1:8011/api/v1/health
curl.exe http://127.0.0.1:3000/healthz
~~~

服务器容器内部：

~~~bash
sudo docker compose --env-file .server.env exec memos curl -f http://127.0.0.1:8000/health
sudo docker compose --env-file .server.env exec app-backend curl -f http://127.0.0.1:8011/api/v1/health
~~~

### 15.5 日志

本机：

~~~powershell
docker compose -f .\docker\docker-compose.yml logs --tail=200 memos
docker compose -f .\docker\docker-compose.yml logs --tail=200 app-backend
docker compose -f .\docker\docker-compose.yml logs --tail=200 frontend
~~~

服务器：

~~~bash
sudo docker compose --env-file .server.env logs --tail=200 memos
sudo docker compose --env-file .server.env logs --tail=200 app-backend
sudo docker compose --env-file .server.env logs --tail=200 frontend
sudo docker compose --env-file .server.env logs --tail=200 caddy
~~~

---

## 16. 已知问题和排障

### 16.1 显示“成功”但生成 0 条记忆

可能原因：

- 第一轮没有提取到候选事实。
- 第二轮认为内容不是完整事件或联系人证据。
- 第二轮模型输出 JSON 不合法。
- 模型没有遵循固定下划线字段名。
- fine 模式模型调用超时或失败。
- 所有结果都是 NONE。

先查：

~~~powershell
docker compose -f .\docker\docker-compose.yml logs --tail=300 memos
~~~

重点搜索：

~~~text
PersonalMemoryNormalizer
memory list
JSON
timeout
event_upsert
~~~

### 16.2 一条取消通知没有更新旧事件

先确认新材料是否包含可靠身份锚点：

- 公司或机构
- 具体项目或任务名
- 人物
- 时间
- 地点
- event_group_id

“9 月 2 日面试取消”可能不够；“A 公司原定 9 月 2 日 15:00 的面试取消”更可靠。

还要检查旧事件是否进入 event_upsert 的 5 条召回候选。

### 16.3 事件重复

当前有三层减少重复：

1. 第一轮合并同一输入中的重复或互补片段。
2. 第二轮对旧候选输出 NONE 或 UPDATE。
3. Topic 对完全相同的重复证据只计一次。

但系统没有无限范围的全库语义去重。连续截图分批、一张一张导入时，旧事件召回失败或上下文不足仍可能产生重复。

较稳的做法：

- 同一段连续聊天截图放进同一个 Markdown，一次导入。
- 在 Markdown 中保留顺序和必要人物、时间、场景说明。
- 不要把一组连续截图拆成几十次独立请求。

“跨多批聊天截图再次做全局事件聚合”目前仍是待完成能力。

### 16.4 Base64 太大或终端被挤满

Base64 应只存在于模型请求阶段，不应长期写库。

如果 API 返回、Neo4j、Qdrant 或 Topic 中仍出现完整 Base64：

1. 确认运行的是当前代码和新容器。
2. 查看 image_parser 是否执行了持久化来源清洗。
3. 重建 memos 和 app-backend。
4. 不要在日志中打印完整请求体。

单张本地图片当前还受 10 MiB 上限约束。过大的截图应先合理压缩，不必降低到看不清 OCR。

### 16.5 图片导入超时

可能原因：

- 图片过大。
- 视觉模型响应慢。
- 模型接口不支持图片。
- 网络或代理不稳定。
- fine 模式需要两轮模型调用。

先用一张 1 到 3 MiB、文字清晰的图片测试，再查看 memos 日志。

### 16.6 视频无法解析

检查：

- OSS 凭证是否配置。
- 视频是否成功上传。
- 签名 URL 是否能被模型服务访问。
- 模型是否支持视频或多帧视觉输入。
- 文件类型和大小是否受支持。

不要把仅支持文本的模型当成视频模型。

### 16.7 前端能打开，但按钮全部没反应

大多不是纯页面样式问题，而是 /api/v1 无法到达 app-backend。

依次检查：

1. 浏览器开发者工具 Network 中 /api/v1 请求状态。
2. frontend 容器是否健康。
3. app-backend 是否健康。
4. frontend/nginx.conf 是否把 /api/v1/ 代理到 app-backend:8011。
5. 认证 Cookie 是否因 HTTP/HTTPS 设置不一致而未发送。

服务器上 MEMOS_AUTH_COOKIE_SECURE 应为 true，并通过 HTTPS 访问；本地 HTTP 开发应为 false。

### 16.8 127.0.0.1:8000 返回 Empty reply

这通常表示：

- 容器仍在启动。
- Uvicorn 进程崩溃。
- 模型或配置初始化异常。
- Docker Desktop 后端刚恢复，服务尚未就绪。

不要只重复 curl。先运行：

~~~powershell
docker compose -f .\docker\docker-compose.yml ps
docker compose -f .\docker\docker-compose.yml logs --tail=200 memos
~~~

### 16.9 Topic 没更新

确认：

- 本次 MemOS 是否真的生成了 memory_id。
- MEMOS_TOPIC_ENABLED 是否开启。
- app-backend/Runtime 是否拿到了 /product/add 返回的 data。
- Topic 模型分析是否失败。
- topics.json 是否可写。
- 是否有两个进程同时写同一个 Topic 文件。

当前设计要求应用后端是 Topic JSON 的单一写入者。不要同时长期运行另一个独立 Topic 写入进程。

可以通过应用后端触发对账：

~~~text
POST /api/v1/topics/reconcile
~~~

### 16.10 Topic 分数看起来不合理

打开 Topic 详情，按顺序检查：

1. supporting_memory_ids 是否是正确事件。
2. reason_evidence 是否逐条解释这些事件。
3. 离散重要度标签是否符合原记忆。
4. importance_score 固定换算是否正确。
5. approaching_bonus 是否使用正确事件时间。
6. decay_penalty 是否符合 core、new、refreshed、demoted 状态。
7. queue_score 是否符合加减公式。

先检查证据，再看最终分数。不要只看一个黑盒总分。

### 16.11 Topic 中还显示已删除记忆

记忆硬删除成功后，Topic 同步可能暂时失败。

- 原记忆已经删除，不应回滚。
- Topic 会标记待同步。
- 后续 reconcile 会清掉失效 supporting_memory_ids。

手动调用 /api/v1/topics/reconcile 后再刷新。

### 16.12 复制项目目录后记忆不见了

Git 仓库不包含 Neo4j、Qdrant 命名卷。

复制以下目录并不能完整迁移数据库：

~~~text
MemOS\
frontend\
.env
~~~

必须另外迁移：

- neo4j_data
- qdrant_data
- topic_data 或 .memos/topic
- plan_tracker_data 或 .memos/plan_tracker
- upload_data 或 .memos/uploads

### 16.13 事件已取消，但旧 Topic 仍占候选

这是当前已经复现的联动缺口：

1. MemOS 可以把原事件按同一个 memory_id 更新为 cancelled 或 completed。
2. Topic 重建候选时会过滤这条终态记忆。
3. 但 Topic 状态文件中的旧记录仍可能保持 active=true 和旧 progress_status。
4. 旧 Topic 因此只变成 suppressed，而不是 retired；queue_score 也可能没有归零。
5. 因为没有产生 retired_topic_ids，最高候选不会立即补位。

这不表示事件更新失败。应先分别检查：

- MemOS API 中原 memory_id 的 event_status 和 version 是否已经更新。
- topics.json 中对应 Topic 的 lifecycle_status、progress_status、retired_reason 和 queue_score。

当前需要补的是 `UPDATE → Topic refresh → 终态退休 → 候选补位` 的完整联动。单独运行队列公式测试不能覆盖这个问题。

---

## 17. 当前明确没有实现的能力

- 网页一次上传 Markdown 和它引用的整个本地图片目录。
- 直接导入任意 ZIP 并安全展开成图文序列。
- 对分多次上传的大量聊天截图做全库级连续会话重组。
- 仅凭时间经过判断事件已经完成、失败或取消。
- 自动计算任务完成百分比和是否来得及。
- 用户手动置顶 Topic。
- 无限范围地查找所有旧事件后再决定 UPDATE。
- 把本机 Runtime 原文件自动复制到永久媒体库。
- 让模型在 Topic 定时重排时重新阅读全部原图或视频。

这些不是“配置没打开”，而是当前代码边界。

---

## 18. 推荐验收方法

### 18.1 测试一条新计划

导入：

~~~text
用户计划于 2026 年 9 月 2 日 15:00 到杭州参加 A 公司后端开发岗位面试。
~~~

检查：

- 只新增一条事件。
- record_type = event。
- event_status = planned。
- event_target = A公司后端开发岗位。
- event_time 是带时区的绝对时间。
- Topic 能引用该 memory_id。
- Plan Tracker 能登记检查时间。

### 18.2 测试重复观察

再次导入同义内容，不增加新事实。

检查：

- 模型输出 NONE。
- 记忆条数不增加。
- version 不增加。
- Topic 不重复计分。

### 18.3 测试明确取消

导入：

~~~text
A 公司通知用户，原定 2026 年 9 月 2 日 15:00 的后端开发岗位面试已经取消。
~~~

检查：

- 更新原 memory_id。
- event_status 变成 cancelled。
- version 加一。
- history 中有旧 planned 快照。
- 不产生第二条活动面试记忆。
- Plan Tracker 移除该项。

Topic 的目标验收结果是：立即 retired、queue_score 归零并由候选补位。但当前版本存在 16.13 的已知缺口；如果前五项正确而 Topic 仍为 suppressed，应判断为 Topic 联动问题，不能误判成 MemOS 没有更新原事件。

### 18.4 测试到期但结果未知

不要导入完成或取消证据，让计划时间自然经过。

检查：

- 同一 memory_id 变成 due_unverified。
- 正文和事件对象不被改写。
- 版本只增加一次。
- 重复检查不制造版本风暴。
- Topic 从核心降到候选或显示结果待确认。

### 18.5 测试联系人

同一次输入包含：

~~~text
张三是用户的大学同学。2026 年 9 月 1 日，用户与张三讨论毕业项目接口设计。
~~~

检查：

- 一条完整事件记忆。
- 一条张三联系人摘要或对现有张三联系人更新。
- 联系人的 historical_events 引用该事件 ID。
- 不额外生成“张三是同学”的孤立事件。

### 18.6 测试图文

把两段文字和两张相关图片放进同一个 Markdown。

检查：

- 保留原始顺序。
- 同一事件的图片和文字被联合理解。
- 不按四个输入项机械生成四条记忆。
- 记忆 sources 不含完整 Base64。
- 每条最终事件的时间、人物和对象能在原材料中找到证据。

---

## 19. 代码位置索引

| 机制 | 主要文件 |
|---|---|
| Runtime 命令和自动导入 | scripts/memos_chat.py |
| 应用后端 API | scripts/memos_frontend_api.py |
| 第一轮多模态读取 | src/memos/mem_reader/read_multi_modal/ |
| 图片解析和来源清洗 | src/memos/mem_reader/read_multi_modal/image_parser.py |
| 个人记忆第二轮 Prompt | src/memos/templates/memory_info_prompts.py |
| 事件和联系人 schema | src/memos/memories/textual/relationship.py |
| ADD/UPDATE/NONE 安全写入 | src/memos/memories/textual/event_upsert.py |
| 记忆主写入链路 | src/memos/multi_mem_cube/single_cube.py |
| 事件到期状态规则 | src/memos/memories/textual/event_lifecycle.py |
| 到期内部接口 | src/memos/api/handlers/event_lifecycle_handler.py |
| Topic 分析和持久化 | scripts/memos_topic.py |
| Topic 队列纯规则 | scripts/memos_topic_queue.py |
| Plan Tracker | scripts/memos_plan_tracker.py |
| 前端页面 | frontend/app/ |
| 前端 API 客户端 | frontend/lib/api-client.ts |
| 前端反向代理 | frontend/nginx.conf |
| 本地 Compose | docker/docker-compose.yml |
| 服务器 Compose | deploy/server/docker-compose.yml |
| Topic 3+27 规格 | docs/superpowers/specs/2026-08-31-topic-3-plus-27-queue-design.md |
| 事件生命周期规格 | docs/superpowers/specs/2026-08-31-event-lifecycle-tracker-design.md |

---

## 20. 一句话理解各组件

- MemOS：把原始材料整理成可更新、可检索的个人事实。
- 事件记忆：一件独立、完整、离开原文仍能理解的事。
- 联系人记忆：某个人与用户关系的当前摘要，以及相关事件索引。
- Neo4j：保存记忆事实和关系。
- Qdrant：帮助按语义找到相关记忆。
- Topic：从记忆中选出当前最值得关注的 3 件事和 27 个候选。
- Plan Tracker：到时间后把“计划中”改成“到期未确认”，绝不猜完成。
- 应用后端：把认证、上传、Topic、Tracker 和 MemOS 包装成稳定接口。
- 前端：只负责用户操作和展示，不直接处理模型、数据库或密钥。

这套系统的核心原则是：原始材料经过两轮理解，最终事实写入 MemOS；后续 Topic 和计划追踪都围绕同一条 memory_id 工作，不再各自建立一套互相冲突的事实。
