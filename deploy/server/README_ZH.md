# MemOS 后端服务器部署

本目录用于部署独立的 MemOS 后端 API。服务器不包含前端代码，也不需要 Node.js。

部署包含：

- Caddy：提供公网 HTTPS API
- 应用后端：认证、上传、Topic 和稳定的 `/api/v1` 接口
- MemOS：记忆解析、存储和检索
- Neo4j：图数据存储
- Qdrant：向量数据存储

```text
任意前端或客户端
        │ HTTPS
        ▼
      Caddy
        │
        ▼
   应用后端 :8011
        │
        ▼
     MemOS :8000
        │
   Neo4j + Qdrant
```

公网只开放 80 和 443。8000、8011 和数据库端口只存在于 Docker 私有网络中。

## 服务器要求

- 推荐 Ubuntu 24.04 LTS
- Docker Engine 和 Docker Compose Plugin
- 至少 4 核 CPU、16 GB 内存和 50 GB 可用磁盘
- 一个指向服务器的域名，或可公开访问的服务器 IP

安全组建议：

| 端口 | 用途 | 来源 |
| --- | --- | --- |
| 22 | SSH | 仅管理员 IP |
| 80 | HTTP 和证书签发 | 公网 |
| 443 | HTTPS API | 公网 |

不要开放 8000、8011、6333、6334、7474 或 7687。

## 1. 获取后端代码

服务器只需要 MemOS 仓库：

```bash
sudo mkdir -p /opt/memos
sudo chown -R "$USER":"$USER" /opt/memos
cd /opt/memos
git clone -b lwm_dev https://github.com/noabt187/MemOS_my.git MemOS
cd MemOS
```

## 2. 配置 MemOS

```bash
cp docker/.env.example .env
chmod 600 .env
nano .env
```

至少填写文本模型、记忆解析模型和 Embedding 配置。需要视频解析时，再填写视频模型和 OSS 配置。

生产环境必须设置登录密码。可以在可信的 Windows 管理电脑上执行：

```powershell
.\scripts\set_memos_access_password.ps1
```

然后通过安全方式把生成的 `.env` 传到服务器。服务器安装了 `uv` 时，也可以直接执行：

```bash
uv run --frozen python scripts/memos_app_auth.py
```

`.env` 包含模型密钥和认证配置，不能提交到 Git。

## 3. 配置公网 API

```bash
cd /opt/memos/MemOS/deploy/server
cp .server.env.example .server.env
chmod 600 .server.env
nano .server.env
```

填写：

```dotenv
PUBLIC_HOST=api.example.com
ACME_EMAIL=admin@example.com
NEO4J_PASSWORD=使用独立的长随机密码
MEMOS_CORS_ALLOWED_ORIGINS=
MEMOS_HTTP_PORT=80
MEMOS_HTTPS_PORT=443
PIP_INDEX_URL=https://pypi.org/simple
```

- `PUBLIC_HOST`：API 域名或公网 IP。
- `ACME_EMAIL`：HTTPS 证书通知邮箱。
- `NEO4J_PASSWORD`：只用于服务器数据库的长随机密码。
- `MEMOS_CORS_ALLOWED_ORIGINS`：允许浏览器直接调用 API 的前端来源，多个来源用英文逗号分隔。
- `MEMOS_HTTP_PORT`、`MEMOS_HTTPS_PORT`：Caddy 发布到宿主机的端口。默认使用 80/443；
  如果当前机器的 80 端口已被占用，可以只把 `MEMOS_HTTP_PORT` 改成 8080。
- `PIP_INDEX_URL`：构建后端镜像时使用的 Python 包索引。默认使用 PyPI；网络受限的服务器
  可以在 `.server.env` 中设置可信镜像，而不需要修改 Dockerfile。

例如允许两个独立前端站点直接调用：

```dotenv
MEMOS_CORS_ALLOWED_ORIGINS=https://dashboard.example.com,https://admin.example.com
```

不能填写 `*`。如果本地前端通过 Vite 代理访问后端，可以保持为空。

## 4. 启动

```bash
cd /opt/memos/MemOS/deploy/server
sudo docker compose --env-file .server.env up -d --build --wait
sudo docker compose --env-file .server.env ps
```

API 根地址：

```text
https://api.example.com/api/v1/
```

健康检查：

```bash
curl https://api.example.com/api/v1/health
```

Caddy 对 `/api/v1/*` 之外的路径返回 404，不提供网页或静态文件。

## 5. 客户端认证

同源代理可以使用网页登录接口和 HttpOnly Cookie。独立前端、手机应用和其他 Agent 推荐使用 Bearer Token：

```http
POST /api/v1/auth/mobile/login
Content-Type: application/json

{"password":"管理密码"}
```

返回的 `session_token` 用于后续请求：

```http
Authorization: Bearer <session_token>
```

Token 和管理密码都不能写入前端仓库或公开日志。

## 6. 本地前端连接服务器

前端可以位于任意本地目录，不需要上传到服务器：

```powershell
cd D:\your-projects\MemOS_frontend
$env:MEMOS_APP_API_URL="https://api.example.com"
npm ci
npm run dev
```

浏览器访问 `http://localhost:3000`。Vite 把本地 `/api/v1` 请求代理到远程服务器。

## 日常维护

查看状态：

```bash
cd /opt/memos/MemOS/deploy/server
sudo docker compose --env-file .server.env ps
```

查看日志：

```bash
sudo docker compose --env-file .server.env logs -f --tail=100 caddy app-backend memos
```

更新：

```bash
cd /opt/memos/MemOS
git pull
cd deploy/server
sudo docker compose --env-file .server.env up -d --build --wait
```

停止并保留数据：

```bash
sudo docker compose --env-file .server.env down
```

不要添加 `-v`，否则会删除 Neo4j、Qdrant、Topic、上传文件和 Caddy 数据卷。
