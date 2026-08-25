# GitHub 双仓库同步与服务器收敛设计

## 目标

将 GitHub 作为唯一源码来源，同时满足以下约束：

1. 后端受版本控制的源码只以 `noabt187/MemOS_my` 的 `lwm_dev` 分支为准。
2. 前端受版本控制的源码只以 `noabt187/MemOS_front` 的 `main` 分支为准。
3. 服务器只部署后端；前端在开发电脑或其他独立静态托管环境运行。
4. 服务器上已经验证有效的记忆提取、Topic 和部署修复必须保留。
5. 密钥、数据库、上传文件、Topic 状态和机器专用端口配置不得进入 Git。

## 当前状态

### 后端

服务器仓库位于 `/home/Lwm/memos-stack/MemOS`，当前分支为 `lwm_dev`。
服务器工作树基于提交 `82d0d99c`，而远端 `origin/lwm_dev` 已领先三个提交并到达
`1c746b6e`。远端提交已经将应用重构为独立后端，删除了前端容器依赖，公开接口统一为
`/api/v1`。

服务器工作树另外包含尚未提交的有效修复：

- 将记忆提取模型默认输出上限从 8000 调整为 16000。
- 第二轮记忆归并优先保留原始时间段边界。
- 模型输出被截断时，保存已经完整返回的事件前缀。
- 为已经入库但尚未处理的记忆增加可断点续跑的 Topic 回填入口。
- 上述行为的回归测试。
- 当前机器为解决镜像下载和端口占用而做的部署调整。

### 前端

服务器目录 `/home/Lwm/memos-stack/MemOS_frontend` 是一个没有提交、没有远端的旧版
Next/Vinext 混合项目。GitHub `noabt187/MemOS_front/main` 已经是独立的 React + Vite
静态前端，并通过稳定的 `/api/v1` 合同调用后端。

GitHub 新前端已经覆盖记忆总览、详情、删除、写入、搜索、对话、文件和视频上传、Topic、
登录以及普通锚点导航。服务器旧前端不能整体推回 GitHub，否则会重新引入后端路由、数据库、
服务端认证和 Vinext 导航依赖，破坏前后端分离。

## 最终架构

```text
开发电脑或独立静态托管
noabt187/MemOS_front (main)
React + Vite
        │ HTTPS /api/v1
        ▼
后端服务器
Caddy -> app-backend -> MemOS -> Neo4j + Qdrant
noabt187/MemOS_my (lwm_dev)
```

服务器不构建、不运行、也不反向代理前端页面。Caddy 只公开后端 `/api/v1/*`；MemOS 的
8000、应用后端的 8011、Neo4j 和 Qdrant 端口继续只存在于 Docker 私有网络。

本地 Vite 前端使用 `MEMOS_APP_API_URL` 将本地 `/api/v1` 代理到公网后端。手机应用通过
`/api/v1/auth/mobile/login` 获取 Bearer Token 后直接访问后端。

## 首次归并策略

### 1. 保存恢复点

- 在远端最新 `origin/lwm_dev` 上创建独立同步分支。
- 保留服务器后端原始工作树不动，从其生成只包含已确认修改的补丁。
- 将服务器旧前端目录移动到带日期的归档目录；不删除，不推送到 GitHub。
- `.env`、`.server.env`、`.memos`、数据库、上传文件和构建缓存不进入补丁或归档提交。

### 2. 后端归并

以 `origin/lwm_dev` 为基线选择性应用服务器修复，不整体合并旧部署文件：

- 保留 16000 tokens、时间段归并规则、截断前缀恢复、Topic 回填及测试。
- `docker-compose.yml` 保留远端的纯后端服务结构和 `app-backend` 名称，不恢复
  `frontend` 服务。
- `Caddyfile` 保留远端的 `/api/v1` API-only 路由和 TLS 配置，不恢复网页代理。
- 公共 Compose 默认继续使用 80/443。当前服务器的 8080 端口映射放入被 Git 忽略的
  本机覆盖文件。
- PyPI 镜像不硬编码为所有用户的默认值。若服务器仍需要阿里云镜像，则通过可选构建参数
  和本机配置提供；超时、重试等通用健壮性参数可以保留在公共 Dockerfile 中。
- `deploy/server/docker-compose.local.yml` 中已经过时的 `frontend` 覆盖项不提交。

冲突只允许在部署文件中手工解决。业务代码若出现意外重叠，停止归并并重新核对远端实现，
不使用 `ours` 或 `theirs` 整体覆盖。

### 3. 前端收敛

- GitHub `MemOS_front/main` 是前端基线和唯一可推送版本。
- 对服务器旧前端按功能清单核对，而不是按文件对比搬运。
- 新前端已经实现的功能不迁移旧代码。
- 只有确认新前端缺失且仍需要的用户可见行为，才在 `MemOS_front` 新分支中重新实现、测试并
  推送；不得复制服务器路由、SQLite/Drizzle、密码哈希或后端环境变量。
- 服务器不再保留一个用于运行的前端工作树；旧目录只作为临时可恢复归档。

## 验证要求

### 后端

- 运行 Topic、记忆提取、MemReader、API 配置和服务器部署相关测试。
- 运行格式化、Ruff 或项目现有静态检查。
- 验证 `.env`、`.server.env`、Topic JSON、数据库、缓存和真实密钥均未暂存。
- 使用 Compose 配置检查确认最终服务中没有 `frontend`，且 Caddy 只代理 `/api/v1`。
- 验证后端健康检查、登录、记忆列表、上传和 Topic 接口。

### 前端

- 运行 `npm ci`、`npm run lint` 和 `npm run build`。
- 使用本地 Vite 代理连接服务器 API，验证登录、导航、上传、记忆详情和 Topic 页面。
- 检查生产构建中不存在服务器地址、密码、Token 或模型密钥。

## 推送与部署

后端验证通过后，将同步分支以普通快进方式合入并推送到 `origin/lwm_dev`，禁止强制推送。
前端仅在确有缺失功能需要移植时向 `origin/main` 推送；否则保持现有远端提交不变。

服务器更新只执行：

```bash
cd /home/Lwm/memos-stack/MemOS
git pull --ff-only
cd deploy/server
sudo docker compose --env-file .server.env up -d --build --wait --remove-orphans
```

`--remove-orphans` 用于停止旧前端容器，但不得使用 `down -v` 或其他删除数据卷的命令。
部署前先检查本机端口覆盖配置仍然生效。

## 后续同步规则

1. 所有功能修改先在开发机分支完成、测试并推送 GitHub。
2. 服务器的代码工作树必须保持干净，只允许 `git pull --ff-only`。
3. 服务器专用配置使用被忽略的文件或环境变量，不直接修改受 Git 跟踪的文件。
4. 紧急服务器修复必须立即建立 Git 分支、测试并推送；推送完成后服务器回到远端提交。
5. 每次部署记录前后端提交号。前端未部署到服务器时，只记录后端提交号和前端兼容的 API
   版本。

## 回滚

- 后端归并前保留原工作树和同步分支，不删除现有数据卷。
- 部署失败时回到部署前的已知提交并重新构建，不回滚或覆盖 Neo4j、Qdrant、Topic 和上传
  数据卷。
- 服务器旧前端归档至少保留到新版前端完成一次端到端验证；之后是否删除由用户另行决定。
