# MemOS 个人记忆系统

这是一个面向个人 AI 助手和 Agent 的长期记忆系统。它把文字、文档、图片和视频转换为可检索的记忆，并根据记忆证据维护用户近期关注的 Topic。

项目由两部分组成：

- **MemOS 后端**：负责记忆解析、存储、检索、对话、Topic、上传和认证。
- **MemOS Frontend**：独立的静态管理页面，负责展示和操作，不保存模型密钥，也不直接访问数据库。

## 功能

- 写入明文记忆，并通过自然语言搜索或对话召回。
- 导入 TXT、Markdown、图片和视频文件。
- 解析包含本地图片引用的 Markdown 文档。
- 使用独立的视频理解模型按时间顺序提取视频中的界面变化、用户行为和可见结果。
- 为记忆保留时间、来源和结构化信息，支持时间流式的活动记录。
- 根据记忆证据生成和维护 Topic，包括评分、状态、证据、版本和历史记录。
- 查看记忆总览、完整内容、类型、标签和结构化字段。
- 删除记忆，并同步清理 Topic 中对应的证据。
- 使用密码登录和签名会话保护管理页面与应用 API。
- 通过统一的 `/api/v1` 接口为网页、手机应用或其他 Agent 提供服务。

## 系统结构

```text
浏览器或手机应用
        │
        ▼ HTTPS
服务器 Caddy :443
        │
        ▼
应用后端 :8011
  ├─ 登录与会话
  ├─ 文件上传
  ├─ Topic 管理
  └─ 稳定的 /api/v1 接口
        │
        ▼
MemOS 核心 :8000
  ├─ 记忆解析与检索
  ├─ 文本、图片和视频理解
  └─ 调度与记忆处理
        │
        ├─ Neo4j：记忆关系和结构
        └─ Qdrant：向量检索
```

本地开发时，8000、8011 和数据库端口只监听 `127.0.0.1`。服务器只部署后端，由 Caddy 提供 HTTPS，公网只开放 80 和 443。前端可以位于本机、另一台服务器或任意客户端项目中。

## 环境要求

- Windows 10/11 和 PowerShell
- Docker Desktop，包含 Docker Compose
- Node.js 22 或更高版本，用于运行前端
- `uv`，用于生成登录密码配置和运行 Python 测试
- 一个兼容 OpenAI API 的文本模型和 Embedding 服务
- 可选：支持视频输入的视觉模型
- 可选：阿里云 OSS，用于把本地视频安全地提供给远程视频模型

前端和后端是两个独立仓库，不要求位于同一目录，也不要求部署在同一台机器。

## 首次配置

### 1. 准备环境变量

在 MemOS 根目录执行：

```powershell
Copy-Item .\docker\.env.example .\.env
```

编辑 `.env`，至少填写文本模型、记忆解析模型和 Embedding 配置：

```dotenv
OPENAI_API_KEY=...
OPENAI_API_BASE=...
MOS_CHAT_MODEL=...

MEMRADER_MODEL=...
MEMRADER_API_KEY=...
MEMRADER_API_BASE=...

MOS_EMBEDDER_MODEL=...
MOS_EMBEDDER_API_BASE=...
MOS_EMBEDDER_API_KEY=...
```

`.env` 已被 Git 忽略，不能提交到仓库。

### 2. 设置管理页面密码

```powershell
.\scripts\set_memos_access_password.ps1
```

密码至少 12 个字符。脚本只把密码哈希和随机会话密钥写入 `.env`，不会保存明文密码。

### 3. 配置视频解析（可选）

如果需要解析视频，在 `.env` 中填写独立的视频模型：

```dotenv
VIDEO_PARSER_MODEL=支持视频输入的模型名称
VIDEO_API_KEY=...
VIDEO_API_BASE=...
```

如果从网页上传本地视频，还需要配置 OSS：

```dotenv
OSS_REGION=cn-shanghai
OSS_BUCKET=你的Bucket名称
OSS_ACCESS_KEY_ID=...
OSS_ACCESS_KEY_SECRET=...
OSS_ENDPOINT=https://oss-cn-shanghai.aliyuncs.com
```

直接导入视频 HTTPS URL 时，不会重复上传本地文件。

## 启动后端

首次启动或后端代码发生变化时：

```powershell
.\start.ps1 -Build
```

日常启动：

```powershell
.\start.ps1
```

脚本会启动并等待以下服务：

- MemOS 核心：`http://127.0.0.1:8000`
- 应用后端：`http://127.0.0.1:8011`
- Neo4j
- Qdrant

验证服务：

```powershell
curl.exe http://127.0.0.1:8000/health
curl.exe http://127.0.0.1:8011/api/v1/health
```

查看日志：

```powershell
docker compose -f .\docker\docker-compose.yml logs -f --tail=100 memos app-backend
```

停止服务并保留数据：

```powershell
docker compose -f .\docker\docker-compose.yml down
```

不要执行 `docker compose down -v`，该命令会删除数据库卷。

## 启动前端

在本地的 `MemOS_frontend` 仓库中执行：

```powershell
$env:MEMOS_APP_API_URL="http://127.0.0.1:8011"
npm ci
npm run dev
```

打开 [http://localhost:3000/login](http://localhost:3000/login)。连接远程服务器时，把 `MEMOS_APP_API_URL` 改成服务器的 HTTPS API 地址。

## 页面功能

| 页面 | 地址 | 用途 |
| --- | --- | --- |
| 记忆总览 | `/` | 查看统计、记忆列表、详情和运行状态 |
| 记忆交互 | `/runtime` | 写入文字、搜索记忆和进行记忆对话 |
| Topic | `/topics` | 查看 Topic 排名、状态、依据和版本 |
| 上传 | `/upload` | 导入文档、图片、本地视频或视频 URL |
| 登录 | `/login` | 建立受保护的管理会话 |

## 应用 API

网页和其他客户端统一使用应用后端，不应直接依赖 MemOS 内部接口。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/v1/health` | 服务和依赖状态 |
| `GET` | `/api/v1/dashboard` | 管理台汇总数据 |
| `GET` | `/api/v1/memories` | 记忆列表 |
| `GET` | `/api/v1/memories/{id}` | 记忆详情 |
| `DELETE` | `/api/v1/memories/{id}` | 删除记忆并同步 Topic |
| `POST` | `/api/v1/ingestions/text` | 写入明文记忆 |
| `POST` | `/api/v1/ingestions` | 上传文件并写入记忆 |
| `POST` | `/api/v1/ingestions/video` | 通过视频 URL 写入记忆 |
| `POST` | `/api/v1/search` | 搜索记忆 |
| `POST` | `/api/v1/chat` | 基于记忆对话 |
| `GET` | `/api/v1/topics` | 获取 Topic |
| `POST` | `/api/v1/topics/reconcile` | 校验 Topic 证据 |

浏览器直接跨域调用时，需要在服务器配置允许的精确来源：

```dotenv
MEMOS_CORS_ALLOWED_ORIGINS=https://dashboard.example.com,http://localhost:3000
```

不能使用 `*`。独立客户端通过 `POST /api/v1/auth/mobile/login` 获取 Bearer Token，并在后续请求中发送 `Authorization: Bearer <token>`。本仓库提供的本地前端默认使用 Vite 代理，因此不需要浏览器直接跨域。

## 数据位置

| 数据 | 本地位置 |
| --- | --- |
| Neo4j 数据 | Docker volume `neo4j_data` |
| Qdrant 数据 | Docker volume `qdrant_data` |
| Topic 状态 | `.memos/topic/` |
| 上传文件 | `.memos/uploads/` |
| 模型和认证配置 | `.env` |

`.env`、`.memos/`、数据库卷和上传原文件都不应提交到 Git。

## 服务器部署

服务器只部署 Caddy、应用后端、MemOS、Neo4j 和 Qdrant，不需要前端仓库或 Node.js。完整步骤见 [deploy/server/README_ZH.md](deploy/server/README_ZH.md)。

## 开发检查

后端：

```powershell
uv run --frozen pytest tests\scripts -q
uv run --frozen ruff check scripts tests\scripts
```

前端：

```powershell
npm run lint
npm run build
```

## 上游项目与许可

本项目基于 [MemTensor/MemOS](https://github.com/MemTensor/MemOS) 构建。MemOS 使用 Apache-2.0 License，许可内容见 [LICENSE](LICENSE)。
