# MemOS 服务器部署（47.99.110.248）

这套配置会启动六个服务：MemOS、Neo4j、Qdrant、Topic 连接服务、认证前端和 Caddy HTTPS 网关。

公网只开放 `80` 和 `443`。`8000`、`8011`、`6333`、`6334`、`7474`、`7687` 都不能加入阿里云安全组。

## 1. 准备服务器

推荐 Ubuntu 24.04 LTS，至少 4 核、16 GB 内存和 50 GB 可用磁盘。

在阿里云安全组中只添加：

- TCP 22：来源限制为你自己的公网 IP。
- TCP 80：来源 `0.0.0.0/0`。
- TCP 443：来源 `0.0.0.0/0`。

登录服务器：

```bash
ssh root@47.99.110.248
cat /etc/os-release
```

下面的安装命令只适用于 Ubuntu。若输出是 Alibaba Cloud Linux、CentOS 或其他系统，请先停止并按对应系统安装 Docker。

## 2. 安装 Docker

```bash
sudo apt update
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

cat <<EOF | sudo tee /etc/apt/sources.list.d/docker.sources
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo docker run --rm hello-world
```

## 3. 从 Windows 上传当前代码

这里不能重新克隆官方仓库，因为当前项目包含你已经做过的 Topic、图片、视频和认证改动。

在 Windows PowerShell 中执行：

```powershell
cd D:\project-memo

tar.exe -czf memos-stack.tar.gz `
  --exclude=MemOS/.git `
  --exclude=MemOS/.venv `
  --exclude=MemOS/.memos `
  --exclude=MemOS/.env `
  --exclude=MemOS_frontend/.git `
  --exclude=MemOS_frontend/node_modules `
  --exclude=MemOS_frontend/.env.local `
  --exclude=MemOS_frontend/dist `
  --exclude=MemOS_frontend/.vinext `
  MemOS MemOS_frontend

scp .\memos-stack.tar.gz root@47.99.110.248:/tmp/
scp .\MemOS\.env root@47.99.110.248:/tmp/memos.env
```

回到服务器执行：

```bash
sudo mkdir -p /opt/memos-stack
sudo tar -xzf /tmp/memos-stack.tar.gz -C /opt/memos-stack
sudo mv /tmp/memos.env /opt/memos-stack/MemOS/.env
sudo chmod 600 /opt/memos-stack/MemOS/.env
```

确认 `/opt/memos-stack/MemOS/.env` 中已经填写真实模型和向量模型配置，不能保留 `you_bailian_api_key`。

## 4. 配置服务器密码

先在 Windows 上运行：

```powershell
cd D:\project-memo\MemOS_frontend
.\change-password.ps1 -NoRestart
```

然后在服务器创建配置：

```bash
cd /opt/memos-stack/MemOS/deploy/server
cp .server.env.example .server.env
nano .server.env
```

需要修改四项：

- `ACME_EMAIL`：你的邮箱。
- `NEO4J_PASSWORD`：新生成的长随机密码。
- `MEMOS_ACCESS_PASSWORD_HASH`：复制 Windows `MemOS_frontend/.env.local` 中同名值。
- `MEMOS_SESSION_SECRET`：复制 Windows `MemOS_frontend/.env.local` 中同名值。

`PUBLIC_HOST` 保持 `47.99.110.248`。保存后执行：

```bash
chmod 600 .server.env
```

不要把 `.server.env`、MemOS 的 `.env` 或 API Key 上传到公开 Git 仓库。

## 5. 构建并启动

```bash
cd /opt/memos-stack/MemOS/deploy/server
sudo docker compose --env-file .server.env up -d --build
sudo docker compose --env-file .server.env ps
```

首次构建和拉取 Neo4j、Qdrant 镜像可能需要较长时间。查看日志：

```bash
sudo docker compose --env-file .server.env logs -f --tail=100
```

服务正常后，在浏览器打开：

```text
https://47.99.110.248/login
```

手机 App 中的服务器地址同样填写：

```text
https://47.99.110.248
```

这里必须是 `https://`。不能改成 HTTP，也不要在手机上关闭证书校验。

## 6. 验证服务

```bash
curl -I https://47.99.110.248/login
sudo docker compose --env-file .server.env ps
sudo docker compose --env-file .server.env logs --tail=100 caddy frontend companion memos
```

如果 HTTPS 证书暂时未签发，先检查阿里云安全组的 80/443、服务器时间和 Caddy 日志：

```bash
sudo timedatectl status
sudo docker compose --env-file .server.env logs --tail=200 caddy
```

不要临时改成 HTTP 传输密码。IP 证书为短期证书，Caddy 会自动续期；如果当前 Caddy 镜像无法签发，最稳妥的替代方案是把一个域名的 A 记录指向 `47.99.110.248`，再把 `PUBLIC_HOST` 改成该域名。

## 7. 日常操作

查看状态：

```bash
cd /opt/memos-stack/MemOS/deploy/server
sudo docker compose --env-file .server.env ps
```

重启：

```bash
sudo docker compose --env-file .server.env restart
```

更新代码后重新构建：

```bash
sudo docker compose --env-file .server.env up -d --build
```

停止服务但保留所有数据：

```bash
sudo docker compose --env-file .server.env down
```

不要执行 `down -v`，它会删除 Neo4j、Qdrant、Topic 和上传文件的数据卷。
