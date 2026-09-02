# MemOS 前后端一体化服务器部署

这套配置会从同一个 MemOS 仓库构建并启动完整系统：

- Caddy：唯一公网入口，提供 HTTPS；
- Frontend：静态管理页面；
- App Backend：登录、上传、Topic 和 `/api/v1`；
- MemOS：记忆解析、存储和检索；
- Neo4j：关系和结构化数据；
- Qdrant：向量检索。

```text
浏览器或手机
      │
      ▼ https://服务器地址/
   Caddy :443
      │
      ├─ /* ───────────► frontend:80
      │
      └─ /api/v1/* ───► app-backend:8011
                              │
                              ▼
                           memos:8000
                              │
                       neo4j + qdrant
```

公网只开放 80 和 443。前端容器、3000、8000、8011 和数据库端口全部留在 Docker 私有网络中。

## 1. 服务器要求

- 推荐 Ubuntu 24.04 LTS；
- Docker Engine 和 Docker Compose Plugin；
- 至少 4 核 CPU、16 GB 内存和 50 GB 可用磁盘；
- 一个公网域名或公网 IP。

安全组：

| 端口 | 用途 | 来源 |
| --- | --- | --- |
| 22 | SSH | 最好只允许管理员 IP |
| 80 | HTTP 跳转和证书签发 | 公网 |
| 443 | HTTPS 页面和 API | 公网 |

不要开放：

```text
3000 8000 8011 6333 6334 7474 7687
```

## 2. 获取一体化代码

服务器只需要克隆一个仓库：

```bash
sudo mkdir -p /opt/memos
sudo chown -R "$USER":"$USER" /opt/memos
cd /opt/memos
git clone -b lwm_dev https://github.com/noabt187/MemOS_my.git MemOS
cd MemOS
```

确认前端已经包含在仓库中：

```bash
test -f frontend/package.json && echo "frontend found"
test -f frontend/Dockerfile && echo "frontend Dockerfile found"
```

不需要再克隆 `MemOS_frontend`，也不需要在服务器宿主机运行 `npm run dev`。

## 3. 配置 MemOS 和登录密码

```bash
cd /opt/memos/MemOS
cp docker/.env.example .env
chmod 600 .env
nano .env
```

至少填写：

- 文本和对话模型；
- 记忆解析模型；
- Embedding 模型；
- 需要视频时的视频模型和 OSS；
- 登录密码哈希和会话密钥。

在可信的 Windows 电脑生成登录配置：

```powershell
cd <仓库目录>
.\scripts\set_memos_access_password.ps1
```

脚本会把下面两项写入根目录 `.env`：

```dotenv
MEMOS_ACCESS_PASSWORD_HASH=<密码哈希>
MEMOS_SESSION_SECRET=<随机会话密钥>
```

它不会保存明文密码。通过安全方式把 `.env` 传到服务器，不能把它提交到 GitHub。

如果服务器已经安装 `uv`，也可以直接在服务器执行：

```bash
uv run --frozen python scripts/memos_app_auth.py
```

## 4. 配置公网地址和数据库密码

```bash
cd /opt/memos/MemOS/deploy/server
cp .server.env.example .server.env
chmod 600 .server.env
nano .server.env
```

填写：

```dotenv
PUBLIC_HOST=memos.example.com
ACME_EMAIL=admin@example.com
NEO4J_PASSWORD=使用独立的长随机密码
MEMOS_ACCESS_PASSWORD_HASH=密码工具生成的哈希
MEMOS_SESSION_SECRET=密码工具生成的至少32位随机密钥
MEMOS_CORS_ALLOWED_ORIGINS=
MEMOS_TOPIC_SCHEDULER_ENABLED=true
MEMOS_HTTP_PORT=80
MEMOS_HTTPS_PORT=443
PIP_INDEX_URL=https://pypi.org/simple
```

说明：

- `PUBLIC_HOST`：浏览器最终访问的域名或公网 IP，不带 `https://` 和路径；
- `ACME_EMAIL`：HTTPS 证书通知邮箱，不是网页登录账号；
- `NEO4J_PASSWORD`：只用于容器内部 Neo4j 数据库，不是网页登录密码；
- `MEMOS_ACCESS_PASSWORD_HASH`、`MEMOS_SESSION_SECRET`：由密码工具生成；把对应值安全地复制到
  `.server.env`，Compose 会将它们传入应用后端；
- `MEMOS_CORS_ALLOWED_ORIGINS`：前后端同源部署时保持为空；只有其他网站需要直接调用 API 时，
  才填写完整来源，多个来源用英文逗号分隔；
- `MEMOS_TOPIC_SCHEDULER_ENABLED`：保持为 `true`，由唯一的 `app-backend` 进程运行 Topic
  scheduler；
- `MEMOS_HTTP_PORT`、`MEMOS_HTTPS_PORT`：Caddy 发布到宿主机的端口。默认使用 80/443；
  如果当前机器的 80 端口已被占用，可以只把 `MEMOS_HTTP_PORT` 改成 8080；
- `PIP_INDEX_URL`：构建后端镜像时使用的 Python 包索引。默认使用 PyPI；网络受限的服务器
  可以在 `.server.env` 中设置可信镜像，而不需要修改 Dockerfile；
- `.server.env` 包含密钥，不能提交到 GitHub。

如果确实有另一个网页需要跨域调用 API，可以填写完整来源：

```dotenv
MEMOS_CORS_ALLOWED_ORIGINS=https://dashboard.example.com,https://admin.example.com
```

不能填写 `*`。本仓库的一体化前端与 API 使用同一个地址，因此通常保持为空。

## 5. 一条命令启动全部服务

```bash
cd /opt/memos/MemOS/deploy/server
sudo docker compose --env-file .server.env up -d --build --wait
```

第一次需要构建前后端并下载镜像，时间会比较长。查看状态：

```bash
sudo docker compose --env-file .server.env ps
```

正常应看到：

```text
frontend
app-backend
memos
neo4j
qdrant
caddy
```

只有 Caddy 应显示宿主机端口 `80:80` 和 `443:443`。

### Topic 唯一写入者约束

`app-backend` 是 `topic_data` 的唯一 Topic writer，也是唯一挂载 Topic 状态目录的服务。
`MEMOS_TOPIC_SCHEDULER_ENABLED=true` 会让 scheduler 在这个单一进程内运行。为避免多个进程同时
改写 `topics.json`，部署时必须遵守：

- 不能扩容 `app-backend`，也不能设置 `deploy.replicas` 或执行
  `docker compose up --scale app-backend=2`；
- 不能把 `app-backend` 改成多 worker 启动；
- 不能新增 Topic scheduler sidecar、第二个 Topic worker，或让其他服务挂载并写入
  `topic_data`。

如果以后需要高可用或多副本，必须先把 Topic 状态迁移到支持并发写入和分布式锁的存储，不能直接
复制当前容器。

## 6. 访问页面

浏览器或手机直接打开：

```text
https://你的域名或公网IP/
```

直接访问下面地址也应正常显示，不应出现 404：

```text
https://你的地址/login
https://你的地址/upload
https://你的地址/runtime
https://你的地址/topics
```

API 健康检查：

```bash
curl https://你的域名或公网IP/api/v1/health
```

浏览器页面和 API 使用同一个域名：

- 页面请求由 Caddy 转发给 `frontend:80`；
- `/api/v1/*` 由 Caddy 转发给 `app-backend:8011`；
- 浏览器不知道 8011 和 8000 的真实地址；
- 登录 Cookie 也是同源 Cookie，不需要跨域配置。

### 手机 App 或其他客户端认证

仓库内网页使用登录 Cookie。手机 App、Agent 或其他独立客户端应先用管理密码换取 Bearer Token：

```bash
curl -X POST https://你的域名或公网IP/api/v1/auth/mobile/login \
  -H 'Content-Type: application/json' \
  -d '{"password":"你的管理密码"}'
```

从响应中读取 `session_token`，后续请求带上：

```http
Authorization: Bearer <session_token>
```

管理密码和 Token 都不能写进前端代码、提交到 GitHub 或输出到公开日志。

## 7. 查看日志

```bash
cd /opt/memos/MemOS/deploy/server
sudo docker compose --env-file .server.env logs --tail=100 caddy frontend app-backend memos
```

持续查看：

```bash
sudo docker compose --env-file .server.env logs -f --tail=100 caddy frontend app-backend memos
```

排查顺序：

1. 整个网站打不开：检查 `caddy`、安全组 80/443 和 `PUBLIC_HOST`；
2. 页面能打开但按钮失败：检查 `app-backend`；
3. 上传后没有记忆：检查 `memos` 和模型配置；
4. 页面路径刷新后 404：检查 `frontend` 的 Nginx 配置是否包含 SPA 回退。

## 8. 更新代码

```bash
cd /opt/memos/MemOS
git switch lwm_dev
git pull --ff-only
cd deploy/server
sudo docker compose --env-file .server.env up -d --build --wait
```

因为前端已经位于同一个仓库，`git pull` 会同时更新前端和后端。

## 9. 重启和停止

重启：

```bash
cd /opt/memos/MemOS/deploy/server
sudo docker compose --env-file .server.env restart
```

停止但保留数据：

```bash
sudo docker compose --env-file .server.env down
```

不要执行：

```bash
sudo docker compose --env-file .server.env down -v
```

`-v` 会删除 Neo4j、Qdrant、Topic、计划追踪、上传文件和 Caddy 证书数据卷。

## 10. 数据卷

```text
neo4j_data   Neo4j 记忆和关系
neo4j_logs   Neo4j 日志
qdrant_data  向量索引
topic_data   Topic 状态（仅 app-backend 挂载并写入）
plan_tracker_data  事件计划与到期检查状态
upload_data  前端上传文件
caddy_data   HTTPS 证书
caddy_config Caddy 运行配置
```

更新镜像和重新创建容器不会删除这些卷。删除卷前必须先做备份。
