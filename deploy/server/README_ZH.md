# MemOS 服务器部署

本目录用于把 MemOS 部署为一套 HTTPS 服务。部署包含：

- Caddy：提供 HTTPS、静态前端和反向代理
- 应用后端：认证、上传、Topic 和 `/api/v1`
- MemOS：记忆解析、存储与检索
- Neo4j：图数据存储
- Qdrant：向量数据存储

公网只需要开放 80 和 443。MemOS、应用后端和数据库均位于 Docker 私有网络中。

## 服务器要求

- 推荐 Ubuntu 24.04 LTS
- Docker Engine 和 Docker Compose Plugin
- 至少 4 核 CPU、16 GB 内存和 50 GB 可用磁盘
- 一个域名，或支持公开访问的服务器 IP
- 已构建的 `MemOS_frontend/dist` 静态文件

安全组建议：

| 端口 | 用途 | 来源 |
| --- | --- | --- |
| 22 | SSH | 仅管理员 IP |
| 80 | HTTP 与证书签发 | 公网 |
| 443 | HTTPS | 公网 |

不要开放 8000、8011、6333、6334、7474 或 7687。

## 目录结构

建议把两个仓库放在同一目录：

```text
/opt/memos-stack/
├─ MemOS/
└─ MemOS_frontend/
```

## 1. 准备 MemOS 配置

```bash
cd /opt/memos-stack/MemOS
cp docker/.env.example .env
chmod 600 .env
```

编辑 `.env`，填写文本模型、记忆解析模型和 Embedding 配置。需要视频解析时，再填写视频模型和 OSS 配置。

生产环境必须启用登录保护。可以在可信的管理电脑上执行：

```powershell
.\scripts\set_memos_access_password.ps1
```

然后通过安全方式把生成的 `.env` 传到服务器。也可以在已安装 `uv` 的服务器上执行：

```bash
uv run --frozen python scripts/memos_app_auth.py
```

## 2. 构建前端

```bash
cd /opt/memos-stack/MemOS_frontend
npm ci
npm run build
```

构建结果位于 `MemOS_frontend/dist`。生产环境只需要这些静态文件，不运行 Node 服务。

## 3. 准备部署变量

```bash
cd /opt/memos-stack/MemOS/deploy/server
cp .server.env.example .server.env
chmod 600 .server.env
```

编辑 `.server.env`：

```dotenv
PUBLIC_HOST=memory.example.com
ACME_EMAIL=admin@example.com
NEO4J_PASSWORD=使用独立的长随机密码
MEMOS_FRONTEND_DIST=/opt/memos-stack/MemOS_frontend/dist
```

`PUBLIC_HOST` 可以填写域名或公开 IP。使用域名时，应先把 DNS 记录指向服务器。

`.env` 和 `.server.env` 都包含敏感配置，不能提交到 Git。

## 4. 启动

```bash
cd /opt/memos-stack/MemOS/deploy/server
sudo docker compose --env-file .server.env up -d --build --wait
sudo docker compose --env-file .server.env ps
```

浏览器访问：

```text
https://memory.example.com/login
```

手机应用和其他客户端使用同一个 HTTPS 根地址。应用 API 位于：

```text
https://memory.example.com/api/v1/
```

## 5. 验证

```bash
curl -I https://memory.example.com/login
curl https://memory.example.com/api/v1/health
sudo docker compose --env-file .server.env logs --tail=100 caddy app-backend memos
```

如果证书签发失败，检查 DNS、安全组、服务器时间和 Caddy 日志：

```bash
sudo timedatectl status
sudo docker compose --env-file .server.env logs --tail=200 caddy
```

## 日常维护

查看状态：

```bash
sudo docker compose --env-file .server.env ps
```

更新后端：

```bash
sudo docker compose --env-file .server.env up -d --build --wait
```

更新前端：

```bash
cd /opt/memos-stack/MemOS_frontend
npm ci
npm run build
```

Caddy 会直接读取新的 `dist` 文件，不需要重建后端容器。

停止并保留数据：

```bash
sudo docker compose --env-file .server.env down
```

不要添加 `-v`，否则会删除 Neo4j、Qdrant、Topic、上传文件和 Caddy 数据卷。
