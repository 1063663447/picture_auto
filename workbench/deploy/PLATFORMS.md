# 用免费 PaaS 部署（Render / Koyeb / Railway / Cloud Run）

> **重要更正**：Hugging Face Spaces 的 Docker / Gradio Space 现在**需要 PRO**（实测创建返回
> `402 Payment Required`："hosting Gradio and Docker Spaces on free cpu-basic requires a PRO
> subscription"，只有 **Static Space 仍免费**，但静态托管跑不了 Python 后端）。所以上一版给的
> "HF Spaces 免费方案" 已经不成立。
>
> 结论：**免费跑 Python + Pillow + 长任务的后端，平台都要求你提供「Git 仓库 / 容器镜像 / SSH」三者之一**。
> 这台机器原本连 git 和 docker 都没有，我已经装了 Git；剩下要么给我一个 GitHub 仓库，要么装 Docker。

## 现状对照

| 平台 | 免费额度 | 跑这套代码 | 入口方式 | 备注 |
|---|---|---|---|---|
| **Render（Free Web Service）** | 免费实例，512MB | ✅ | **Git 仓库**（或容器镜像） | 无持久盘、闲置 15 分钟休眠；`deploy/render.yaml` 已写好 |
| **Koyeb（Free）** | 1 个免费服务 | ✅ | **Git 仓库**或镜像 | 缩容到 0；免费无持久卷 |
| **Railway** | 一次性试用额度 | ✅ | Git 仓库 | 之后按量付费 |
| **Google Cloud Run** | 每月免费额度 | ✅ | **容器镜像 / 源码 + Cloud Build** | 需要 gcloud 登录；无持久盘 |
| **Fly.io** | 无长期免费 | ✅ | flyctl + Docker | 有持久卷，最适合生产，但付费 |
| **Oracle Cloud Always Free** | 永久免费 VM | ✅ | **SSH** | 最稳；要信用卡验证 + 自己装环境 |
| Hugging Face Spaces | 仅 Static 免费 | ❌（Docker 需 PRO） | Hub API | 实测 402 |
| Cloudflare Workers | 大 | ❌ | — | 无 Python/Pillow/磁盘 |
| Vercel / Netlify | 免费 | ❌ | — | 函数 10~60s 上限 |
| PythonAnywhere Free | 免费 | ❌ | — | 出站白名单，调不了生图网关 |

## 路径 1：Render 免费实例（推荐，够用且不用管服务器）

```powershell
# 1) 代码推到 GitHub（已装好 git；需要一个能 push 的仓库 + Personal Access Token）
.\deploy\push_github.ps1 -Repo https://github.com/你的账号/picture_auto.git -Token ghp_xxx

# 2) 打开 Render → New → Blueprint → 选这个仓库 → Apply
#    deploy/render.yaml 已经配好 Dockerfile 路径、健康检查、端口与环境变量
# 3) 在 Render 的 Environment 里填 TERRA_API_KEY / DEEPSEEK_API_KEY
```

访问地址形如 `https://apparel-recolor-workbench.onrender.com/?token=<config.json 里的 auth_token>`。

注意：免费实例休眠后再访问要等 30~60s；**重启会清空 `data/`**。记录库已改用**飞书多维表格**
（任务/模型/张数/金额/成品编号都留痕，见 `deploy/feishu.md`），图本身请及时下载 ZIP，
或把 `feishu.upload_zip` 打开让 ZIP 进飞书附件。

## 路径 2：Google Cloud Run（无服务器，按量免费额度）

```bash
gcloud auth login
gcloud run deploy apparel-workbench \
  --source . \
  --region asia-east1 \
  --allow-unauthenticated \
  --port 7860 \
  --timeout 900 \
  --set-env-vars WORKBENCH_HOST=0.0.0.0,WORKBENCH_PORT=7860,WORKBENCH_DEPLOYED=1
```
- 需要仓库根目录有 `Dockerfile`：把 `workbench/deploy/Dockerfile` 复制到根目录即可（或 `--source workbench`＋把 skill 一起包进去）。
- `--timeout 900`：单张生图最多 120s，留足余量。
- 无持久盘：`data/` 随实例销毁。

## 路径 3：Oracle Cloud 永久免费 VM（最像正式服务器）

1. 开 Always Free ARM 实例（Ubuntu 24.04），放行 8787
2. `sudo apt install -y python3-venv libjpeg62 zlib1g-dev`
3. 上传 `workbench/` + `apparel-style-recolor/`，`pip install -r workbench/requirements.txt`
4. systemd 常驻（`Environment=WORKBENCH_HOST=0.0.0.0 WORKBENCH_PORT=8787`）
5. 前面套 Cloudflare（DNS 代理）就有正式域名 + HTTPS

## 不迁移也能用的方案（零成本、已验证）

Cloudflare 隧道已经在跑：

```text
https://creatures-except-laugh-heights.trycloudflare.com/?token=1_JiQgjmfST8
```

入口在 Cloudflare、算力在你本机：不花钱、不用改代码、数据都在本地。缺点是**这台机器要开着**；
需要固定域名就跑 `deploy/tunnel.ps1` 换具名隧道（见 `deploy/cloudflare.md`）。

## 部署前统一检查

1. `config.json → server.auth_token` 必须保留（公网无口令 = 别人烧你的额度）
2. 有持久卷就设 `WORKBENCH_DATA_DIR`，否则接受 `data/` 会丢
3. 平台 request timeout 必须 > 120s
4. Key 只放平台的环境变量/Secret，不要提交进仓库（`.gitignore` 已覆盖 `.env`、`data/`、`.bin/`）
