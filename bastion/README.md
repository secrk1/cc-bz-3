# 轻量级堡垒机系统

基于 **Python 3.12 + asyncssh + FastAPI** 的轻量堡垒机，提供：

- 🖥️ **Web SSH 终端**：浏览器 xterm.js ↔ WebSocket ↔ asyncssh PTY，全双工、高并发；
- 🖼️ **RDP 远程桌面中继**：浏览器 guacamole-common-js ↔ 自研 Guacamole 协议桥 ↔ guacd ↔ 目标 RDP；另提供协议无关的裸 TCP 中继网关；
- 🎬 **双协议会话回放**：SSH 会话双向 I/O 经**独立异步队列**实时落盘为
  asciicast v2；RDP 会话由 **guacd 原生图形录制**（带 sync 时间戳的指令流）。
  会话审计页可直接**在线交互回放**（播放/暂停/拖动/倍速/跳空闲，RDP 为桌面
  画面回放，SSH 为终端回放），录像文件也可下载；
- 🧾 **指令审计**：命令级审计入库（含命令响应摘要）；
- 🗂️ **资产管理 / 会话记录 / Redis 会话与凭证缓存 / PostgreSQL 持久化**；
- 🎯 内置 **SSH 靶机**与 **RDP 靶机**，`docker compose up -d` 后即可在浏览器端到端验证。

## 架构

```
浏览器 (xterm.js / guacamole-common-js)
        │  WebSocket (/ws/ssh, /ws/rdp, /ws/tcp)
        ▼
nginx:1.27-alpine (托管 Vue3 静态包 + 反代)
        ▼
backend  python:3.12-slim  ── FastAPI + asyncssh
   ├── SSH:  直连目标 22/2222（PTY 双向流转 + 审计 + 录屏）
   ├── RDP:  Guacamole 协议桥 ── guacd ── 目标 3389
   └── TCP:  裸 TCP 中继（/ws/tcp，可承载任意 RDP 客户端）
        ├── redis:7-alpine      活跃会话索引 + 临时凭证缓存
        ├── postgres:16-alpine  用户 / 资产 / 会话 / 审计
        ├── ssh-target          linuxserver/openssh-server:latest
        └── rdp-target          linuxserver/rdesktop:ubuntu-xfce (xrdp)
```

## 目录结构

```
bastion/
├── docker-compose.yml          一键编排
├── .env.example
├── backend/                    python:3.12-slim
│   ├── Dockerfile / entrypoint.sh / requirements.txt
│   ├── alembic.ini
│   ├── migrations/             数据库迁移（Alembic，启动自动 upgrade）
│   └── app/
│       ├── main.py             FastAPI 入口
│       ├── config.py  database.py  redis_client.py
│       ├── models.py  schemas.py  security.py  crypto.py  seed.py
│       ├── api/                auth / assets / sessions / WebSocket 路由
│       ├── proxy/
│       │   ├── ssh_tunnel.py   ★ asyncssh PTY 桥 + 指令审计 + 录像喂入 + 响应摘要
│       │   ├── guac.py         ★ Guacamole 协议桥（服务端握手 + 二进制安全透传）
│       │   └── tcp_relay.py    ★ 原生 TCP 中继网关
│       └── services/
│           ├── audit.py        会话生命周期 / 审计行 / 摘要回写
│           └── recorder.py     ★ 异步无阻塞 asciicast v2 录屏引擎
└── frontend/                   node:20-alpine 构建 → nginx:1.27-alpine
    ├── Dockerfile  nginx.conf  vite.config.js
    └── src/ (登录 / 资产 / SSH 终端 / RDP 桌面 / 会话审计与 CastPlayer 回放)
```

## 快速开始

```bash
cd bastion
docker compose up -d --build      # 旧版 Docker 可用: docker-compose up -d --build
```

启动后：

- 打开 <http://localhost:8080>
- 默认账号 **admin / admin123**（可用 `.env` / 环境变量 `ADMIN_PASSWORD` 覆盖）
- 首次启动自动执行数据库迁移并写入两个内置靶机资产

> 首次构建会拉取基础镜像；RDP 靶机（Ubuntu XFCE + xrdp）体积较大，请耐心等待。

## 端到端验证

### 1. SSH 终端

1. 资产页可见「内置SSH靶机」`testuser@ssh-target:2222`，点击 **SSH 终端 ↗**
   （在**新标签页**打开全屏终端）；
2. 数秒内出现 shell 提示符，直接执行命令，例如：

   ```bash
   whoami        # testuser
   hostname
   ls -al
   ```

3. 回到 **会话审计** 页，点开最新会话的「审计」，可看到每一条回车提交的命令及其
   **响应摘要**（输出静默 0.3s 后自动去 ANSI 截取）；离线 SSH 会话点击 **▶ 回放**
   即可在页面内按原始时间轴交互播放（暂停/拖动进度/0.5–4 倍速/跳过空闲/按命令定位），
   也可下载后用 [asciinema](https://asciinema.org/) 回放：
   `asciinema play <会话id>.cast`。

### 2. RDP 远程桌面

1. 资产页可见「内置RDP靶机」`abc@rdp-target:3389`，点击 **RDP 桌面 ↗**（新标签页）；
2. 顶栏状态依次为 `连接中 → 等待中 → 已连接`，页面呈现 XFCE 桌面
   （xrdp 已用资产中保存的 abc/abc 自动登录，无需再输密码）；
3. 可在桌面内打开终端操作；关闭页面即断开，会话与连接事件记入审计。
4. 回到 **会话审计** 页，离线的 RDP 会话点击 **▶ 回放**，可在页面内按原始
   时间轴播放桌面操作画面（guacamole-common-js `SessionRecording` 渲染 guacd
   录制的图形指令流，支持暂停/拖动/倍速），也可下载 `.guac` 录像留档。
4. **若连接失败**：页面会弹出后端回传的 guacd 真实错误原因；也可在资产页点
   **连通性**——后端会对 SSH 做真实握手鉴权、对 RDP 经 guacd 走完整登录握手，
   并把成功/失败原因直接显示在资产行下方。排障时配合
   `docker compose logs -f backend` 查看逐步握手日志（`select/args/connect/ready`）。

> 浏览器中的 RDP 数据流：`浏览器 → /ws/rdp → Guacamole 桥 → guacd → rdp-target:3389`。
> 安全模式留空，由 freerdp 与目标自动协商 NLA/TLS/RDP（注意 Guacamole 没有
> `security=any` 这一取值，传错会直接握手失败）。
> 如需对接本地原生 RDP 客户端（mstsc/remmina），后端另暴露了协议无关的
> 裸 TCP 中继 `/ws/tcp/{asset_id}`，可据此扩展本地客户端 sidecar。

## 核心设计说明

- **异步高并发**：所有会话在事件循环中以协程运行，单进程即可承载大量并发
  SSH/Guacamole 会话；SSH 以 bytes 收发（`encoding=None`），`top`/`vim` 等
  全屏二进制程序不乱码。
- **指令审计与响应摘要**：在输入流维护行缓冲（处理退格、Ctrl-C/U），回车提交时
  抽取最终命令行写库并拿到审计行 ID；随后累积该命令的 PTY 输出，输出静默
  0.3s（或下一条命令提交、或断连）后做 ANSI 剥离/提示符剔除/截断，按行 ID
  精确回写响应摘要，杜绝快速连敲命令时摘要串挂。
- **异步录屏引擎**（`services/recorder.py`）：转发热路径对每个 I/O 块只做一次
  有界 `asyncio.Queue.put_nowait`（纯内存 O(1)，实测 10 万次入队 < 55ms），
  独立 worker 协程攒批（40ms 窗口）后经 `asyncio.to_thread` 阻塞 write/flush，
  磁盘再慢也不拖慢终端；队列满时丢输出帧并计数告警（不无限吃内存），输入帧
  退化为最多 50ms 异步入队；事件偏移在入队时用 monotonic 盖戳，关闭时限时 5s
  排空落盘。文件为标准 asciicast v2（`i`/`o` 双向事件）。
- **在线回放（双协议）**：仅离线会话可取录像（在线返回 409）。SSH 用
  `CastPlayer.vue`（xterm.js 自包含解析 v2，rAF 驱动虚拟时间轴，倍速/跳空闲/
  按命令定位）；RDP 用 `GuacPlayer.vue`（guacamole-common-js `SessionRecording`
  把 guacd 录制的带 `sync` 时间戳指令流作为 Blob 读入，内部回放 Client 逐帧
  重绘桌面，1x 走原生 play()、非 1x 以 ~8fps 定时 seek() 步进实现倍速）。
- **RDP 图形录制**：握手时经 `recording-path`/`recording-name`/
  `create-recording-path`/`recording-include-keys` 让 guacd 在服务端直接把
  图形指令流写进与 backend 共享的命名卷（同路径 `/data/recordings`），无需
  guacenc/ffmpeg；guacd 以 uid 1000 运行而目录由 root(0755) 创建会写不进，
  故启动及每次会话前把共享目录 chmod 1777。连通性探测 `probe_rdp` 不录制，
  避免产生空录像。
- **凭证安全**：资产口令/密钥用 Fernet 对称加密入库（密钥由 `JWT_SECRET`
  派生），运行时解密后写入 Redis 并设短 TTL（`CREDENTIAL_TTL` 默认 300s）。
- **Guacamole 桥**：严格实现 guacd 服务端隧道握手
  （首帧内部 UUID 指令 → `select/args/size/audio/video/image/connect/ready`），
  ready 后按**长度前缀字节流**双向透传，保证 img/blob 中 PNG 二进制不被破坏；
  浏览器隧道的空操作码保活帧会被剥离，避免污染 guacd 协议。
- **迁移**：`alembic upgrade head` 在容器入口自动执行，初始迁移建齐四张表。

## 环境变量（backend）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DATABASE_URL` | postgresql+asyncpg://…/bastion | 异步 PG 连接串 |
| `REDIS_URL` | redis://redis:6379/0 | Redis 连接串 |
| `JWT_SECRET` | please-change-this-secret | **生产必改**，同时派生凭证加密密钥 |
| `ADMIN_USER` / `ADMIN_PASSWORD` | admin / admin123 | 初始管理员 |
| `CREDENTIAL_TTL` | 300 | 凭证明文在 Redis 的缓存秒数 |
| `RECORDING_DIR` | /data/recordings | 录屏落盘目录（已挂卷） |
| `GUACD_HOST` / `GUACD_PORT` | guacd / 4822 | RDP 网关守护进程 |

## 常用运维命令

```bash
docker compose logs -f backend      # 查看后端日志
docker compose restart backend      # 重启（迁移幂等，自动执行）
docker compose down                 # 停止并移除容器（保留数据卷）
docker compose down -v              # 同时清空数据库/Redis/录屏数据
```

## 安全提示

本项目面向内网实验/演示，正式上线前请至少：修改 `JWT_SECRET` 与管理员密码、
为 SSH 资产改用密钥认证、在网关启用 HTTPS/WSS、按需收敛 `known_hosts` 策略
（当前为代连便利关闭了主机密钥校验）。
