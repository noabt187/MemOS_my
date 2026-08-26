# MemOS 前端

这里是 MemOS 仓库内置的管理前端。它使用 React、TypeScript 和 Vite，只通过同源的 `/api/v1` 接口访问应用后端，不直接连接 MemOS 核心、Neo4j 或 Qdrant。

## 目录结构

```text
frontend/
├─ app/                 页面和页面组件
│  ├─ components/      共用界面组件
│  ├─ login/           登录页
│  ├─ runtime/         记忆写入、搜索和对话
│  ├─ topics/          Topic 页面
│  └─ upload/          文件上传页
├─ lib/                 API 客户端和共用工具
├─ public/              静态资源
├─ src/main.tsx         浏览器入口和前端路由
├─ Dockerfile           生产镜像构建
├─ nginx.conf           静态页面、SPA 回退和本地 API 代理
├─ package.json         前端依赖和命令
└─ vite.config.ts       本地开发配置
```

## 正常使用

不需要单独进入本目录启动。项目根目录的本地 Compose 和 `deploy/server` 的服务器 Compose 都会自动构建前端。

本地整套启动：

```powershell
cd D:\project-memo\MemOS
docker compose -f .\docker\docker-compose.yml up -d --build --wait
docker compose -f .\docker\docker-compose.yml ps
```

浏览器打开：

```text
http://127.0.0.1:3000
```

服务器整套启动：

```bash
cd /opt/memos/MemOS/deploy/server
sudo docker compose --env-file .server.env up -d --build --wait
```

浏览器打开：

```text
https://你的域名或公网IP/
```

生产环境只有 Caddy 的 80/443 对公网开放。前端、应用后端 8011、MemOS 8000 和数据库端口都只存在于 Docker 私有网络中。

## 单独开发前端

只有修改前端页面时才需要：

```powershell
cd D:\project-memo\MemOS\frontend
npm ci
$env:MEMOS_APP_API_URL="http://127.0.0.1:8011"
npm run dev
```

开发页面地址：

```text
http://127.0.0.1:3000
```

Vite 会把 `/api/v1/*` 代理到 `MEMOS_APP_API_URL`。这个变量只用于本地开发与预览代理，不会写入生产浏览器代码，也不能包含密码或 Token。

## 检查

```powershell
npm run lint
npm test
```

构建产物位于 `dist/`，不提交 Git。生产 Docker 镜像会自动执行 `npm ci` 和 `npm run build`，然后由 Nginx 提供静态页面。

## 页面

| 地址 | 用途 |
| --- | --- |
| `/login` | 密码登录 |
| `/` | 记忆总览、详情和删除 |
| `/runtime` | 写入、搜索和记忆对话 |
| `/topics` | Topic 排名、证据和版本 |
| `/upload` | 文字、图片、Markdown 和视频上传 |

所有请求统一使用 `lib/api-client.ts` 中的 `/api/v1` 客户端，并由 `lib/api-contract.ts` 校验后端响应。页面组件不要直接写 8000、8011、服务器 IP 或模型密钥。

## 隐私

本目录不保存：

- `.env` 或服务器配置；
- 模型 API Key；
- 登录明文密码；
- 用户上传文件；
- Topic 和数据库数据。

这些内容全部由服务器后端和持久化数据卷管理。
