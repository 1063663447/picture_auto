# 部署到 Cloudflare

> 结论先说：
> **能立刻用的是「Cloudflare Tunnel」**——工作台仍跑在这台 Windows 机器上，Cloudflare 只做公网入口
> （`https://xxx.trycloudflare.com`，自动 https）。**不能**把这个 FastAPI 后端直接搬进 Workers，
> 原因见下。R2 用来归档成品，但你账号的 R2 **还没开通**（实测返回 10042）。

## 0) 已经做好的

| 项目 | 状态 |
|---|---|
| 本地工作台 | 跑在 `127.0.0.1:8787` |
| Cloudflare 隧道 | 已建立，公网可访问（见 `data/public-url.txt`） |
| 访问口令 | 已启用：`config.json → server.auth_token`，无口令一律 401 |
| API Token | 已验证有效（`/accounts/{id}/tokens/verify` → active，账号 `a2b3dfdb…`） |
| Workers / Pages | 账号下为空（说明这把 Token 至少有读权限） |
| R2 | **未开通**：`GET /accounts/{id}/r2/buckets` → `10042 Please enable R2 through the Cloudflare Dashboard` |

公网地址（带口令才能打开）：

```text
https://<随机的 trycloudflare 域名>/?token=<config.json 里的 auth_token>
```

打开一次后浏览器会记住 Cookie，之后直接用域名即可。地址在 `data/public-url.txt`。

## 1) 启动 / 重启隧道

```powershell
# 一键：确保本地服务在跑 + 建隧道 + 打印公网地址 + 写 data\public-url.txt
.\deploy\tunnel.ps1
.\deploy\tunnel.ps1 -Port 8899      # 换端口
```

- 首次会自动下载 `cloudflared.exe` 到 `.bin\`（约 55MB）。
- 这是 **Quick Tunnel**：进程重启后域名会变。要固定域名见第 3 节。
- 关掉窗口 = 断开隧道；本地工作台不受影响。

## 2) 为什么不把后端直接部署到 Workers

这个项目用到的东西，Workers 运行时都不提供：

| 依赖 | 用途 | Workers 现状 |
|---|---|---|
| Python + FastAPI + uvicorn | 整个后端 | 只有 Python Workers（beta），不支持 FastAPI/ASGI 生态 |
| Pillow | <2MB 压图、面料取色、图案检测 | 无 C 扩展 |
| 本地文件系统 | `data/jobs/*` 任务目录、staging、ZIP | 无持久磁盘（要换 R2/D1/KV） |
| 长任务 + SSE | 生成要 30~120s，还有并发调度、重试 | Workers 单请求 CPU/时长限制，且官方建议用 Durable Objects/Queues 重构 |

要做「后端跑在 Cloudflare」需要重写成 Workers + R2 + Durable Objects/Queues，并把 Pillow 换成 Workers 图片处理或外部服务——那是一个独立工程，不是部署动作。现在这套的定位是：**本机算力 + Cloudflare 做入口**。

## 3) 换成固定域名（具名隧道，长期用这个）

```powershell
.bin\cloudflared.exe tunnel login                      # 浏览器授权，选你的域名
.bin\cloudflared.exe tunnel create apparel-workbench   # 得到隧道 UUID + 凭据 json
.bin\cloudflared.exe tunnel route dns apparel-workbench apparel.你的域名.com
.bin\cloudflared.exe tunnel run --url http://127.0.0.1:8787 apparel-workbench
```

跑通后可以用 `cloudflared service install` 注册成开机自启服务。

## 4) 记录库：飞书多维表格（不再是 R2）

已完成成品写进飞书多维表格，配置见 [feishu.md](feishu.md)；R2 相关代码已移除。

## 5) 安全须知

- 隧道把工作台放到公网，**任何人都能调用它的接口**，而每次调用都在烧你的生图额度。所以：
  - 已默认启用 `server.auth_token`（无口令 401），口令别外发；
  - 需要更严格的话，在 Cloudflare 侧加 **Cloudflare Access**（Zero Trust）做邮箱/OTP 校验，或在 `.env` 之外再加一层反向代理 Basic Auth；
  - 长期不用就把隧道窗口关掉。
- `.env` 里有网关 Key、DeepSeek Key、Cloudflare Token，**不要提交到仓库**（`.gitignore` 已忽略 `.env`、`data/`、`.bin/`）。
