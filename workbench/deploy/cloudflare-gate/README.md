# 全局并发阀门（Cloudflare Worker + Durable Object）

给多台电脑共用一把生图 Key 时用：**所有机器共用同一个并发额度**，本地只做排队，
真正的闸门在 Cloudflare 上。网关实测 20 并发，就把额度设 20。

```
本机 A ─┐
本机 B ─┼─► https://gate.你的域名.workers.dev  (Durable Object, 全局唯一)
本机 C ─┘         └─ 有牌就发，没牌就排队（最多等 wait_timeout_sec）
                   └─ 租约 180s 自动过期，机器崩了也不会永久占坑
```

## 1. 部署（一次性，约 3 分钟）

需要 Node 18+ 和 Cloudflare 账号（免费计划够用：Durable Object 免费额度 + Worker 10 万次/天）。

```powershell
npm install -g wrangler          # 或者 npx wrangler
cd E:\picture_auto\workbench\deploy\cloudflare-gate
wrangler login                   # 浏览器里点一下授权

# 生成一把闸门令牌（随便一串长的随机字符）
$token = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 40 | % { [char]$_ })
wrangler secret put GATE_TOKEN   # 粘贴这把 token

wrangler deploy
```

部署完会打印地址，形如 `https://workbench-gate.<你的子域>.workers.dev`。

自检（不用先配客户端）：

```powershell
curl.exe -X POST https://workbench-gate.xxx.workers.dev/health
curl.exe -X POST https://workbench-gate.xxx.workers.dev/ `
  -H "Authorization: Bearer $token" -H "Content-Type: application/json" `
  -d '{\"action\":\"status\"}'
# → {"ok":true,"in_use":0,"limit":20,"leases":[]}
```

## 2. 每台电脑配置

`workbench/config.json`：

```json
"defaults": {
  "gate": {
    "enabled": true,
    "url": "https://workbench-gate.xxx.workers.dev/",
    "token_env": "GATE_TOKEN",
    "limit": 20,
    "poll_ms": 700,
    "wait_timeout_sec": 1800,
    "fail_open": true
  }
}
```

`workbench/.env`（打包给同事时每人一份，token 相同）：

```ini
GATE_TOKEN=上一步那把 token
```

接口上还能单独收额度，例如某个接口只给 4 个并发：

```json
"providers": { "terra": { "max_concurrency": 20, "gate_limit": 20 } }
```

- `max_concurrency`：**本机**最多同时发几个（多余的排队，不会真发出去）。
- `gate_limit`：**所有机器合计**的额度（不填用 `defaults.gate.limit`）。
- `fail_open: true`：阀门连不上/5xx 时记一条 warn，退回「只用本机并发」继续出图；
  连续 3 次失败会熔断 60 秒，避免每张图都先等一次超时。

## 3. 怎么确认生效

```powershell
python -c "import asyncio,sys;sys.path.insert(0,'.');from skillbridge.service import Workbench;print(asyncio.run(Workbench('config.json').gate_status()))"
```

界面上「生成设定 → 并发数」下面的小字会显示 `全局阀门 3/20`；
任务日志里排队时会写 `排队中：全局并发 20/20（其他电脑也在跑），已等 12s`。

## 4. 不想上云也行

`gate.enabled: false` 时完全不用 Cloudflare，只按 `max_concurrency` 在本机排队。
但要注意：**多台电脑各跑 20，网关实际就是 20×N**，这正是这个阀门要解决的问题。

## 5. 费用与边界

- 免费额度：Worker 10 万请求/天，Durable Object 100 万次/月 —— 出图量远到不了。
- 一次出图 = 2 次 Worker 请求（acquire + release），20 张图 40 次。
- 阀门只协调「同时发几个」，不代理图片、不存 Key、不碰提示词；
  Worker 里只有租约（holder/job/task 三个短字符串 + 过期时间）。
