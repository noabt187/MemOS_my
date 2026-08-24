# 本地 Docker Compose

这套 Compose 用于在本机运行完整的 MemOS 后端：

- `memos`：记忆核心，地址 `127.0.0.1:8000`
- `app-backend`：应用 API、认证、Topic 和文件上传，地址 `127.0.0.1:8011`
- `neo4j`：图数据存储
- `qdrant`：向量数据存储

所有端口只监听本机，不向局域网或公网开放。

## 启动

先在仓库根目录准备 `.env`，然后执行：

```powershell
.\start.ps1 -Build
```

日常启动：

```powershell
.\start.ps1
```

也可以直接使用 Compose：

```powershell
docker compose -f .\docker\docker-compose.yml up -d --wait
```

## 状态和日志

```powershell
docker compose -f .\docker\docker-compose.yml ps
docker compose -f .\docker\docker-compose.yml logs -f --tail=100 memos app-backend
curl.exe http://127.0.0.1:8000/health
curl.exe http://127.0.0.1:8011/api/v1/health
```

## 停止

```powershell
docker compose -f .\docker\docker-compose.yml down
```

该命令保留 Neo4j 和 Qdrant 数据卷。不要添加 `-v`。
