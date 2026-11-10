# 生图工作台（本地版）

把 `apparel-style-recolor`（原本在 codex/GPT 上按对话跑）封装成**本地工作台 + 第三方生图接口多并发**的生产线：
上传一批原图 → 选款式和颜色 → 自动拆成 N×M 个任务并发调用生图接口 → 失败自动重试 → 按 skill 规则校验、压到 2MB 以内并打包 ZIP。

```
原图 2 张 × 23 索罗娜短袖 10 色 = 20 个任务 → 并发 6 → 约 3~4 分钟出 20 张成品 + 1 个 ZIP
```

> **2026-11 起只跑本地，不再上云。** Render 上的实例已 `suspended`、`autoDeploy` 已关（push 不会再触发重建），
> 需要重新上线在 Render 控制台点 Resume 即可，代码和 `deploy/` 脚本都还在。

- **skill 仍是唯一事实来源**：工作台启动时解析 `SKILL.md` / `references/color-reference.md` / `assets/color-references/`，提示词、版型描述、负面约束、颜色清单、2MB 限制全部从 skill 读取。改 markdown，工作台自动跟随。
- **款式库叠加**：《墨西哥-服装款式代号表》解析出的 10 个款式（49 色）以 `library/` 叠加在原 skill 上，原 skill 不动。
- **不绑定某一家模型**：内置 OpenAI 兼容中转站、火山方舟、Gemini、fal.ai、自定义 JSON 五种适配器，换接口只改 `config.json`。
- **为批量而生**：任务级并发、RPM 节流、指数退避重试、单任务超时、结果有效性校验、失败单独重试、随时取消、历史任务可回溯下载。
- **成品落本地 + 飞书留痕**：成品图存 `data/jobs/`，同时（压缩后）写进飞书多维表格，换机/重装也能从飞书回拉。

> **接手项目先看 [`HANDOVER.md`](HANDOVER.md)**：一页纸讲清架构、数据流、8 个关键设计（效果图双参考 /
> 版型重塑 / 主体判定 / 并发闸门 …）、自检脚本、已知限制和故障处理。本 README 是逐版本的详细手册，当字典查。

> **2026-11-06 · 正式网页 9.0.0 BLACK GLASS**：已接入确认的纯黑／深紫毛玻璃与屏幕平面粒子设计，保留真实多款多色、双参考、版型派生、队列、返工、分层下载、飞书和账号业务，并修复提交／轮询竞态与个人用量范围。详见 [`CHANGELOG_BLACKGLASS_9.0.0.md`](CHANGELOG_BLACKGLASS_9.0.0.md)（文件清单、真实单图测试、回归结果、启用与回退）。前端无构建步骤，旧浏览器 Ctrl+F5；本轮后端用量修复需重启服务加载。

> **2026-11-11 · 当前版本 9.6.9 第 2 轮之后还能接着改**：被改出来的那张图本身就是下一次框选的底图——点「接着改（第 3 轮）」直接在刚出的图上继续框，主任务一直停在待确认，想改几轮改几轮，最后一轮满意了再「这张可以」。详见 [`CHANGELOG_CHAIN_9.6.9.md`](CHANGELOG_CHAIN_9.6.9.md)。

> **2026-11-11 · 9.6.7 卡片重排 + 框跟手 + 直接改/可并发**：审核条改成自适应网格（几张并排都不挤压，已确认的卡不再被按钮挤矮）；框选框层贴到图片上（原来图片两侧留白让框整体偏移，现在鼠标按哪就从哪起）；点「交给 AI 改」**直接发送并回到审核条**，那张卡转圈等第 2 轮，出图后变「第 2 轮 · 待确认」可一键采纳，等待期间能同时框选改别的基准图（每张一条任务，并发跑）。顺带修了多款同批时「只改这一张」会把所有基准图一起返工的问题。详见 [`CHANGELOG_REVIEW_965_9.6.7.md`](CHANGELOG_REVIEW_965_9.6.7.md)。

> **2026-11-11 · 9.6.6 框选改图**：基准图有歧义时，直接**点那张图**打开大图，在图上**拖框**标出要改的部分，每个框下面写一句「这里要改成什么 / 什么问题」，点「交给 AI 改」→ 标注图（框线 + 编号）存盘，随改图一起作为 **IMAGE 2** 发给生图接口，框位与要求也写进 harness 上下文，AI 据此改提示词。框外默认不动。详见 [`CHANGELOG_ANNOTATE_9.6.6.md`](CHANGELOG_ANNOTATE_9.6.6.md)。

> **2026-11-11 · 9.6.5 基准图白色优先 + 单张确认**：基准图固定先做白色；出一张就能先确认（不等全部出完），卡片标「款式 · 颜色」，可只确认/只改这一张；确认回到画布审核条，只有点「改这张」才开对话框（只针对那一张），对齐与记忆写入仍后台完成。详见 [`CHANGELOG_WHITE_MASTER_9.6.5.md`](CHANGELOG_WHITE_MASTER_9.6.5.md)。

> **2026-11-11 · 9.6.4 修对话状态显示**：基准图还没出时对话框曾错误显示「基准已确认、正在改色」且不返图；现在只有「出完基准 + 用户确认」才进入改色，生成期间只显示「基准图生成中 i/N」。回归 `python tools/check_master_chat.py`。详见 [`CHANGELOG_MASTER_WAIT_9.6.4.md`](CHANGELOG_MASTER_WAIT_9.6.4.md)。

> **2026-11-11 · 9.6.3 沉淀改成「拍板后静默评估」**：确认基准图后 harness 在后台逐条评判「这条要求是这个款式的通病，还是只对这一张的微调」——判成通病的写进该款式规则（重要的直接升级为**款式级硬规则**，生图提示词自动带上），微调只留档不注入。款式统计（批次/返工轮次/沉淀条数）一并记账，用来观察基准图返工率。详见 [`CHANGELOG_DISTILL_9.6.3.md`](CHANGELOG_DISTILL_9.6.3.md)。

> **2026-11-11 · 9.6.2 对话优先 + 30 扎染T**：放原图选款式点生成后**直接拉出对话框**，基准图生成中 → AI 把图返到对话里 → 满意点「就按这张改色」，要改就说哪张改哪儿、改完再返图，直到确认才开始改色。确认时**自动**把这次对齐的要求记进该款式规则（不用用户说「记住」）。另补 30 扎染T（白/藕粉/奶茶），款式库 25 款 / 115 色。详见 [`CHANGELOG_CHAT_FIRST_9.6.2.md`](CHANGELOG_CHAT_FIRST_9.6.2.md)。

> **2026-11-11 · 9.6.0 先出基准图 + 对话确认**：生成改成两段——先出基准图，运营在对话框里确认或提修改，确认后才按这张基准图给其余颜色改色；
> 每次确认 / 修改都会沉淀成该款式的长期规则（`library/prompts/<slug>.json` 的 `lessons`）。对话框新增 5 个工具：看状态 / 确认 / 打回 / 看规则 / 记规则。
> 详见 [`CHANGELOG_MASTER_GATE_9.6.0.md`](CHANGELOG_MASTER_GATE_9.6.0.md)。

> **2026-11-11 · 9.5.0 菲律宾款式表整合**：《菲律宾-服装款式代号表》25 个款式 / 115 个色号全部建档，
> 每款一套专属提示词（`library/prompts/`，共 28 份），新增「品类」维度（上衣 / 背心；下装代码保留但本批没有裤子）——背心不再被补袖子、
> 短裤不再被当成 T 恤。详见 [`CHANGELOG_STYLES_9.5.0.md`](CHANGELOG_STYLES_9.5.0.md)。

> **2026-11-07 · 9.4.1**：在 9.4.0 的粘贴、任务收尾、人物保护和 AI 多款多色上下文基础上，补齐印墨／露底布料分离规则，针对圣诞人物腿部和图案周边的黑块。分发 `releases/PictureWorkbench-9.4.1-win-x64-internal.zip`；同图白／粉测试及视觉验收限制见 [`CHANGELOG_ARTWORK_9.4.1.md`](CHANGELOG_ARTWORK_9.4.1.md)。9.4.0 工作流说明见 [`CHANGELOG_WORKFLOW_9.4.0.md`](CHANGELOG_WORKFLOW_9.4.0.md)，下面旧版为历史记录。

> **2026-11-06 · 9.3.0 模特一致性与内部便携包**：修复跨颜色／重试／返工丢失原模特，移除指定文案。给同事分发 `releases/PictureWorkbench-9.3.0-win-x64-internal.zip`，完整解压后双击“一键启动.bat”；自带 Python、依赖、Three.js 和已配置 API，无需手动安装。适用 Windows 10/11 x64，修复使用国内源和 SHA256 校验。详见 [`CHANGELOG_PORTABLE_9.3.0.md`](CHANGELOG_PORTABLE_9.3.0.md)。

---

## 0. 本地启动（默认方式）

双击 **`run.bat`**（或命令行 `python serve.py --open`）：

1. 自动找 Python → 缺依赖就装 `requirements.txt` → 启动服务 → **自动打开浏览器**；
2. 地址 `http://127.0.0.1:8787`，登录用飞书「账号」表里的账号（默认 `admin / Asd123456.`）；
3. 应急入口：`http://127.0.0.1:8787/?token=<config.json 里的 auth_token>`（机器访问/忘记密码时用）；
4. 关掉命令行窗口即停止服务；换端口 `python serve.py --port 8899`。

启动时会打印一段清单，方便确认路径对不对：

```
生图工作台（本地）
地址    : http://127.0.0.1:8787
应急口令: http://127.0.0.1:8787/?token=...
登录    : 飞书「账号」表的账号（默认 admin / Asd123456.）
skill   : E:\picture_auto\apparel-style-recolor
款式库  : library
数据    : E:\picture_auto\workbench\data（成品、日志、本地缓存都在这）
飞书    : 已接入（app_token ...）
```

本机数据都在 `workbench/data/`（成品、日志、用量、缩略图/压缩版缓存）；磁盘涨了用：

```powershell
python tools/cleanup_local_data.py --days 30 --dry-run   # 预览
python tools/cleanup_local_data.py --days 30             # 删 30 天前的任务
python tools/cleanup_local_data.py --derived-only         # 只清缩略图/压缩版缓存
```

---

## 1. 手工安装（首次/换机）

```powershell
python -m pip install -r requirements.txt
copy .env.example .env        # 然后填入你的 Key（TERRA / DEEPSEEK / FEISHU）
python serve.py               # --open 自动开浏览器，--port 8899 换端口
```

先用 `本地模拟（不发请求）` 这个接口跑一遍，确认上传、并发、命名、ZIP 都正常，再接真实接口。

> **已预置的默认接口**：`65535 网关 · gpt-image-2`（`https://sub-proxy-us.65535.space/v1`，Key 在 `.env` 的 `TERRA_API_KEY`）。启动后界面默认就选它，模型框里点「拉取」即可看到该网关的 30 个模型，图片模型排在最前。
>
> 注意：同一个 Key 在 `https://api2.65535.space` 上调用图片接口会返回 `403 this group is not served by this gateway`，**图片请求要走 `sub-proxy-us.65535.space`**（两份 host 的 `/models` 都能列模型，但只有前者服务图片分组）。codex 里配的 `gpt-5.6-terra` 是纯文本模型（`wire_api = responses`），不能改图，工作台里别选它。

---

## 1.5 可选：临时分享给别人看（仍然只跑本机）

不想上云、但需要让不在同一台电脑上的人看一眼时，用隧道把**本机服务**临时暴露出去：

```powershell
.\deploy\tunnel.ps1          # 本地服务 + Cloudflare 隧道，打印 https://xxx.trycloudflare.com
```

- 访问要**账号登录**（飞书「账号」表，见 3 节）；`config.json → server.auth_token` 保留为机器/应急口令，首次访问可用 `?token=...` 换 Cookie。
- **计算、存储、成品图全在这台机器上**，隧道只转发 HTTP；关掉窗口即失效，地址每次重开都会变。
- 记录库 / 定价 / 账号在**飞书多维表格**（三张表），换机也不丢：`python -m skillbridge.feishu --check`，配置见 [deploy/feishu.md](deploy/feishu.md)。
- 长期给别人用就别开隧道了，让他自己 `git clone` 后双击 `run.bat`，各自跑本地、共用同一张飞书表。

---

## 1.6 已停用的云端部署（留档，可恢复）

早先试过 Render 常驻（`deploy/render.yaml` + `deploy/Render` 脚本），现已停用：

- Render 服务 `srv-dbkok1nf3r2c73fggbt0` 已 `suspended`，`autoDeploy` 已设为 `no` —— **现在 `git push` 不会触发重建**。
- 站点 `https://apparel-recolor-workbench.onrender.com` 已不可访问（返回 503/suspended）。
- 要恢复：Render 控制台点 **Resume**，或调 API `POST /v1/services/srv-dbkok1nf3r2c73fggbt0/resume`，再 `PATCH {"autoDeploy":"yes"}`。
- 云端不再使用的原因：免费实例有月出口流量上限（图片最费），冷启动 30s+，且 `data/` 会随重启丢失；本地跑更快、无流量上限、成品留在自己盘上。
- 相关文件（`deploy/Dockerfile`、`render.yaml`、`deploy/render_deploy.py` 等）保留不删，需要时还能一键回到云上。

> 之前那份「Cloudflare Workers / HF Spaces」方案也一并搁置，原因见 [deploy/cloudflare.md](deploy/cloudflare.md)。

---

## 2. 目录结构

```text
picture_auto/
├─ apparel-style-recolor/            ← 原 skill，不改动
│  ├─ SKILL.md
│  ├─ references/color-reference.md
│  ├─ assets/color-references/{crewneck-sweatshirt,hoodie,short-sleeve}/*.png
│  └─ scripts/package_outputs.py     ← 打包校验脚本，被工作台原样调用
└─ workbench/
   ├─ run.bat                        ← 一键启动
   ├─ serve.py                       ← Web 工作台
   ├─ cli.py                         ← 命令行批量跑（无浏览器）
   ├─ config.json                    ← 接口 / 默认参数
   ├─ .env.example                   ← API Key
   ├─ requirements.txt
   ├─ skillbridge/
   │  ├─ skill.py                    ← 解析 skill：类型、颜色、提示词、2MB 限制
   │  ├─ prompts.py                  ← 组装提示词（图案锁 / 材质锁 / 负面词）
   │  ├─ refine.py                   ← 改图返工提示词与交接 brief
   │  ├─ harness.py                  ← 本地分析师（降级用）+ SSE 帧
   │  ├─ agent.py                    ← DeepSeek 工具型 agent（看图/查价/开工）
   │  ├─ pricing.py                  ← 单价表 + 用量账本
   │  ├─ job.py                      ← 任务模型与 job.json 持久化
   │  ├─ runner.py                   ← 任务展开、并发调度、重试、落盘
   │  ├─ packaging.py                ← 调用 skill 的 package_outputs.py
   │  ├─ service.py                  ← 任务注册表 / 启动 / 取消 / 重试
   │  ├─ api.py                      ← FastAPI 接口
   │  └─ providers/                  ← openai_compat / ark / gemini / fal / custom_json / mock
   ├─ web/                           ← 工作台前端（原生 JS，无构建）
   ├─ pricing.json                   ← 每模型单价（可改；null = 未定价）
   ├─ tools/selftest.py              ← 自检（不需要 Key）
   ├─ docs/uiux/DESIGN-SYSTEM.md     ← UI 3.0 设计系统定稿（ui-ux-pro-max 生成）
   ├─ .skills/ui-ux-pro-max/         ← 导入的 UI/UX 设计 skill（可继续用）
   └─ data/jobs/<job_id>/            ← 每个任务的全部产物
```

每次任务的数据：

```text
data/jobs/20261103-100717-986c/
├─ job.json                 # 任务状态、参数、日志（前端轮询这个状态）
├─ inputs/item-01/…         # 你上传的原图
├─ work/item-01.png         # 两段式模式的改款中间图（不会进 ZIP）
├─ staging/item-01/item-01_black.png
└─ outputs/short-sleeve-style-recolor-batch.zip
```

---

## 3. 界面上怎么用（UI 7.0 · 星雾 AURORA）

界面按 ui-ux-pro-max skill 生成的设计系统重建（记录见 docs/uiux/DESIGN-SYSTEM.md，skill 已导入 .skills/ui-ux-pro-max/）：
Data-Dense Dashboard 布局、语义 token、深浅色自适应、内联 SVG 图标、44px 触控目标、可见焦点环、toast 代替 alert。

**5.5 精简**（按运营反馈）：站名改为 **生图工作台**、左上角配了新 logo（绿色渐变方块 + 白色双星）；
「目标款式」压成一个下拉菜单（原来全量平铺，款式库接入后太占屏）；
删掉只有一个选项的「生成方式」卡片（步骤变成 1 上传 / 2 款式 / 3 颜色 / 4 接口与并发）；
删掉模型下方的两行统计说明、`同时发送颜色参考图` 复选框与其说明（**现在固定发送颜色参考图**）；左上角不再显示 skill 路径。

**7.0 视觉重做：直接复用 `01-aurora.html` 原型**

界面按原型「星雾 AURORA」重做，壳和组件都是照原型搬的，只把内容换成工作台的真实数据：

| 原型元素 | 工作台里是什么 |
|---|---|
| 左侧图标栏 `.rail` | 画廊 / 上传原图（直接触发 file input）/ 日志+历史（切视图）/ 需求沟通；底部是账号用量、管理后台、头像（按权限显示） |
| 顶栏 `.topbar` | 品牌 + 四个入口（创作空间 / 任务记录 / 飞书历史 / 需求沟通）+ 运行状态点、用量、账号、后台、当前账号 |
| 开场 `.intro` | 「让灵感，拥有更多可能。」+ 右侧 session 卡（当前任务名 + 阶段，就是原来的 `jobTitle/jobStage`） |
| 控制台 `.controls` | 01 原始素材（拖拽上传 + 缩略图）· 02 款式与色彩（下拉 + 色卡胶囊）· 03 生成设定（接口/模型/并发/比例/档位/质量）+ 预计花费 + 查看提示词 |
| 画布 `.canvas` | 成品画廊（KPI 四块 + 进度条 + 3 列卡片）/ 运行日志 + 历史任务（切视图） |
| composer | 需求输入框 + 快捷灵感 + 「换个灵感」+「直接开工」/「开始生成」；Ctrl/Cmd+Enter 也能开工，写的内容会带进需求沟通 |
| 灯箱 `.lightbox` | 点成品图看原图大图 + 「下载这张」 |
| toast / 光斑 / 极光 | 原样保留；鼠标跟随光斑只在精细指针且没开「减弱动效」时才跑 |

**动过的按钮/功能**（原型里是演示占位，这里换成真功能或删掉）：

- 加：左侧图标栏、composer 需求输入 + 「直接开工」（跳过对话直接跑）、大图灯箱、「换个灵感」、`#logs` / `#gallery` 深链、视图标签（成品画廊 / 运行日志）。
- 删：原型的「方案导航条」（01/02/03 演示切换）、收藏心形、素材中心、原型说明——这些在原型的演示里也只会弹提示。
- 留：需求沟通（DeepSeek 对话 + 流式）、提示词抽屉、飞书历史（直链 / 打包下载）、账号用量、批量改图 / 批量导出、下载 ZIP、KPI、进度、运行日志、历史任务、骨架屏、顶部请求进度条。

**7.1 按反馈继续收（当前版本）**

- **去掉开场大字**（`IMAGINATION, IN EVERY SHADE.` / 让灵感，拥有更多可能 / 一句说明）和右侧 session 卡：
  任务名 + 阶段改成画布顶部的一条细条（`成品画廊 / 运行日志` 右边），省下一整屏高度。
- **去掉页脚右侧**「LOCAL WORKBENCH · 数据留在本机」，只留左边一行。
- **去掉对话框里的「将提交的提示词」面板**：提示词仍会算、仍会随任务提交（`/api/preview` 的「查看本轮提示词」抽屉照常可看），
  只是不再塞在对话框底部挤占聊天区。
- **对话框补上加载反馈**：
  - 发出后立刻出现三点跳动气泡（收到第一个字自动撤掉）；
  - 每次工具调用先是一颗转圈的胶囊（「正在查看这批图片…」），结果回来变成 ✓ 并可点开看返回内容；
  - 流式输出时气泡尾部有光标；发送按钮同时转圈禁用。
- **对话框有上下文了**（之前每轮都从零开始，才会反复问同一件事）：
  - 前端把最近几轮问答（`history`）随 `/api/harness/stream` 一起发；
  - 后端把它插在 system 和当前这句之间；system prompt 也加了硬规矩：
    「这是同一段连续对话，问过的不要再问；用户说『是的/开始/就这样』= 确认，直接执行；
    上一轮已经定了方向（例如『改成冬日冰霜』），这轮只确认就直接照它提交，不要回头再问要改哪儿」。
  - 实测：`换个图案 → 改成冬日冰霜 → 是的，开始` 现在第二轮就会动工具，
    直接 `set_requirements` + `submit_rework` 提交，不再反复确认。

**7.2 多款式：一次出多个款式 × 各自全色**

需求：一张原图，要一次出「多个款式，每个款式出它的全色」。

- **界面**：原来一个「目标款式 + 一排颜色」换成**款式卡片列表**——每张卡片是一个圆角框，
  里面是「款式下拉 + 该款式的颜色（切换款式即刷新，默认全选）+ 已选 N/N 色 + 全选/清空 + × 移除」；
  卡片下方是虚线圆角的 **「＋ 添加款式」**，点一下再加一张（自动挑一个还没用过的款式）。
  选择记在浏览器里，下次打开还在。
- **计数/报价**：预计调用 = 图片数 × **所有款式颜色之和**（5 色 + 3 色 = 8，两张图就是 16 次）。
- **任务**：`POST /api/jobs` 增加 `selections`（JSON 数组 `[{garment_type, colors}]`），
  `garment_type`/`colors` 仍保留为第一款的兼容写法；服务端把「同款式加两次」合并成一份。
- **产出结构**：多款式任务的 staging / ZIP 里，每个「原图 × 款式」一个目录
  （`item-01__06-hoodie/`、`item-01__23-sorona-tee/`），同名颜色不会互相覆盖；
  单款式仍然沿用老的 `item-01/`。
- **其它**：卡片上会标出款式（`白色 · 连帽卫衣`）；任务标题显示「2 个款式 · 5 张」；
  多款式时打包不再强校验「每目录固定张数」（各款式颜色数可以不同）。
- **提示词**：每个任务按**自己款式**的版型短语组装（卫衣走卫衣、短袖走短袖），不是复用第一款。
- **回归测试**：`python tools/check_multi_style.py`（2 款式 3+2 色 → 5 任务、ZIP 分桶正确、老单款式路径不变）。

> **7.2.1 修一个多款式的坑**：出图时取颜色参考原来是 `self.garment`（= 第一个款式），
> 于是第 2 个及以后款式里、第一卷没有的颜色全部报「颜色参考缺失」；
> 而那些「名字撞车」的颜色更隐蔽 —— 会拿第一个款式的参考图去出第二个款式的图。
> 现在按任务的 `task.garment` 找回自己的款式（`garment_for_task()`），
> 参考图文件真的缺失时只记一条 warn 并跳过参考，不再整张失败。

样式文件：新增 `web/aurora.css`（原型配色 + 组件 + 工作台适配层），`web/style.css` 仍作几何底；
主界面不再加载 `glass.css`（管理后台仍用，且 `aurora.css` 会把它的玻璃变量一起改暗，所以后台也是星雾色）。

**6.0 定稿（本次）**：界面又收掉了一批「固定值」选项 —— 它们现在由代码固定为下面这套，
不再占屏幕，也不需要每次确认：

| 已隐藏的选项 | 固定值 |
|---|---|
| 图案处理 | **自动 · 按需求判断**（空值，由后端按需求关键词决定；见「图案策略」） |
| 实际出图尺寸 | **auto · 跟随原图**（`画面比例` 选具体比例时才用档位换算） |
| 输出格式 | **PNG** |
| 图案策略提示 | 按需求自动判断（不显示） |
| 下载格式 | **原图 PNG**（保真；压缩版接口仍在，界面不再用） |
| 下载说明 | 不显示 |

仍然可见并可调：生图接口、模型（含单价）、并发数（自动）、画面比例、分辨率档位、质量。

**5.6 再精简**：
- 原生的「圆领卫衣 / 连帽卫衣 / 短袖」不再出现在下拉里（款式库已有 03/06/23 等对应款式）。
  它们没有被删掉——`config.json → library.hide_original_types: true` 只是把它们移出界面，
  命令行 `--garment-type 短袖` 这类老写法照样能用；同名别名会优先命中界面上能看到的那一款。
- 图片接口始终带 `Cache-Control: private, max-age=31536000, immutable`，并给每张成品一个
  **任务级版本号**（`task.version`，完成时间/文件 mtime）：图片内容不变 → URL 不变 → 浏览器 HTTP 缓存长期命中，
  重新出图（重试/返工）版本号变化 → 自动换新，不会看到旧图。
- 早期用过的浏览器 IndexedDB 图片缓存（`web/imgcache.js`）已停用：网格直接读原图，
  交给浏览器 HTTP 缓存就够了（`imgcache.js` 文件留在仓库里但页面不再加载）。

### 款式库（《墨西哥-服装款式代号表》已接入）

原 skill（`apparel-style-recolor`）保持不动，工作台用**叠加层**把款式表并进「目标款式」：

| 位置 | 内容 |
|---|---|
| `workbench/library/styles.json` | 10 个款式 → 代号 / 展示名 / 版型英文短语 / 别名 / 颜色（label、中文名、色号、HEX、参考图） |
| `workbench/library/refs/<slug>/<label>.jpg` | 每种颜色一张效果图（从 xlsx 里抽出来、缩到长边 900），既当界面缩略图，也作为「参考图」发给模型 |
| `config.json → library` | `enabled` + `dir`（可关闭叠加） |

已接入的 10 个款式（共 49 个颜色）：

| 代号 | 款式 | 颜色数 | 版型短语（喂给模型） |
|---|---|---|---|
| 03 | 03 圆领卫衣 | 5 | crewneck sweatshirt, ribbed collar/cuffs/hem, brushed fleece |
| 06 | 06 连帽卫衣 | 3 | pullover hoodie, drawstrings, kangaroo pocket |
| 07 | 07 水洗T恤 | 1 | oversized washed / vintage-finish t-shirt |
| 10 | 10 弹力短袖 | 7 | stretch short-sleeve t-shirt (poly-spandex jersey) |
| 21 | 21 童装索罗娜 | 9 | kids short-sleeve tee in Sorona blend |
| 23 | 23 索罗娜短袖 | 10 | relaxed-fit short-sleeve tee in Sorona blend |
| 24 | 24 长袖成人 | 5 | long-sleeve t-shirt (adult), **平口** |
| 25 | 25 长袖童装 | 5 | kids long-sleeve t-shirt, **平口** |
| 28 | 28 卫衣童装 | 3 | kids sweatshirt, crewneck, brushed fleece |
| 29 | 29 水洗童装 | 1 | kids washed / vintage-finish tee |

- 颜色按色号取名字（`BK[0]` 黑色 / `FGY[0]` 花灰色 …），HEX 是从效果图中间区域剔除背景后算出的主色，界面上的色点、提示词里的色值都用它。
- 中文名、代号（`23#SLNDX`）、英文名都能被 `type_by_alias` 识别；命令行 `--garment-type "23 索罗娜短袖"` 同样可用。
- 换新的 xlsx 重新生成：`python tools/build_library_from_xlsx.py <新表.xlsx>`（幂等，会重建 `library/refs` 与清单）；`python tools/show_library.py` 核对清单，`python tools/check_library.py` 检查线上/本机接口。

#### 袖口工艺：T 恤平口 / 卫衣螺纹（7.6 起强制区分）

生图模型最容易犯的错：把长袖 T 恤画成卫衣的**罗纹袖口**——客户一眼就看出不是这件衣服。

| 归属 | 款式 | `cuff` | 工艺 |
|---|---|---|---|
| T 恤系 | 07 / 10 / 21 / 23 / 24 长袖 / 25 长袖童装 / 29 | `plain` | 袖口、下摆都是**同布平车折边**，袖筒到边保持原宽度；罗纹只在领口 |
| 卫衣系 | 03 圆领卫衣 / 06 连帽卫衣 / 28 卫衣童装 | `ribbed` | 袖口、下摆是**真实 1x1 罗纹带**（可见竖向罗纹），比袖身略窄 |

- 数据：`styles.json` 每款一个 `"cuff": "plain" | "ribbed"`；没写的按版型描述/款式名推断（`skill.infer_cuff`，宁可默认平口）。
- 提示词：`prompts.cuff_clause()` 注入 `CUFF & HEM CONSTRUCTION —` 段；否定词按款追加（平口款：`Do not add ribbed cuffs…`，卫衣款：`Do not replace the ribbed cuffs…`）。
- 自相矛盾会被自动纠正：平口款式若在描述里残留 `ribbed cuffs`，`library.phrase_for_cuff()` 改成平口并在 `skill.warnings` 里记一条。
- 界面：款式下拉和卡片标题上直接标 `平口 / 螺纹`，删图前不用再猜。
- 回归：`python tools/check_cuff.py`（离线，秒级）；真接口对照 `python tools/check_cuff_real.py --yes`（约 0.08 元，出 `data/test/cuff/real-cuffs.png` 拼图，一眼看袖口）。

### 视觉层 5.0（`web/glass.css`，独立于 style.css 便于回退）

**出图不卡的三条硬规则**（5.2 起，改动请别破坏）：

1. **结果区增量更新**：`renderJob()` 不再 `grid.innerHTML = ""`，改成按 `task.id` 复用卡片（`state.cards`），只改状态文字/图片 URL/按钮，图片只有在 `thumb` 真的变化时才换 `src` —— 以前每 1.2s 轮询会把所有已出图的 `<img>` 全部重建，10 张图就会触发 10 次重新解码。
2. **成品卡不用 `backdrop-filter`**：几十个毛玻璃层同时重绘会掉帧；成品卡用半透明底 + 内高光近似质感，另外 `content-visibility: auto` 让屏幕外的卡跳过绘制。
3. **轮询降频**：任务 >6 张 1.6s、>12 张 2.2s、页面在后台 5s；出图 >12 张时 `<html>` 自动加 `.perf-lite`，一键关掉毛玻璃与装饰动画。
4. **服务端不堵事件循环**：读定价/用量、建任务、存草稿、飞书读表这些同步 HTTP 全部走 `asyncio.to_thread`，否则 SSE（harness）和出图轮询会互相卡住（"聊着聊着突然跳到出图"就是这个原因）。
5. **提交即渲染**：`POST /api/jobs`、`/rework` 的响应里已经带完整任务列表，前端 `watchJob(id, seedJob)` 立刻渲染一帧，不用等第一次轮询，改图也不会"等很久突然跳"。

| 项 | 做法 |
|---|---|
| 背景 | 极光渐变（emerald/amber 三团径向光斑，28s 缓慢漂移）；浅色模式额外一层顶部柔光 |
| 玻璃拟态 | **6.0 改了**：大面积卡片不再用 `backdrop-filter`（滚动掉帧的主因），改为更实的底色 + 内高光 + 细边框 + 分层投影；模糊只留顶栏(12px)/抽屉(16px)/toast(12px) 这些小块区域 |
| 层次 | 卡片顶部渐变发丝线、卡头微光泽、序号徽章改为渐变发光胶囊、KPI 右上角柔光晕 |
| 动效 | 卡片/缩略图错峰入场、KPI 数字滚动 + bump、进度条流光 + 完成扫光、运行中状态点呼吸 + 卡片扫光、抽屉弹性滑入、toast 弹簧入场 + 存活进度条、按钮流光/回弹/提交转圈、拖放区呼吸、空态插画漂浮、图表条形图生长 |
| 反馈 | 顶栏滚动进度条（`--scroll`，6.0 起走 `transform: scaleX` 不触发布局）+ 滚动后加阴影、**顶部请求进度条 `.netbar`**、**骨架屏 `.sk`**、按钮 `.loading` 转圈、按钮 active 缩放、chip/历史行悬浮位移、选中成品卡勾选框填充 |
| 可关 | 所有装饰动画都在 `prefers-reduced-motion: reduce` 下静止；深色模式单独一套玻璃透明度与光斑强度 |

调参入口都在 `glass.css` 顶部 `:root`：`--glass / --glass-strong / --glass-hi / --aurora-1..3 / --shadow-3 / --ease-spring`。

1. **上传原图**：拖拽多张。默认 *一张图 = 一个 item*；同一件衣服多角度命名 item-01_view-01.png / item-01_view-02.png 会归到同一个 item-01/。
2. **目标款式**：圆领卫衣 / 连帽卫衣 / 短袖，右侧标出参考色数量。
3. **颜色**：默认全选该款式全部内置参考色，色卡显示按参考图实测的 HEX。
4. **生成方式**：只有 一次到位。
5. **接口与并发**：界面只有 **65535 网关** 一个入口；**模型下拉列出该网关全部图片模型并显示单价**（如 `gpt-image-2 · ¥0.038/张`、`gpt-image-2-auto · ¥0.065/2K`），不做分类，末尾留「其他（手填模型名）」；并发数自动 = 预计调用次数（只读）。参数按职责分成三层：
   - **画面比例**：自动（跟原图）/ 1:1 / 3:4 / 4:3 —— 只决定横竖，不影响价格；
   - **分辨率档位**：1K（1024 级）/ 2K（2048 级）/ 4K（3072 级）—— **只有它决定单价**，下拉里直接标出当前模型这一档多少钱（`2K · 2048 级 · ¥0.065/张`）；
   - **质量**：high / medium / low / auto —— 只影响细节与耗时，**不改变档位与价格**（实测同一 size 下 quality=low 与 high 出图都是 1024×1024，网关按次+分辨率档计费，见 `tools/probe_quality.py`）。
   - 实际出图尺寸 / 输出格式（PNG）/ 图案处理 / 下载格式这几项 6.0 起都**收进代码固定值、界面不显示**（见上面 6.0 那张表）；RPM、重试次数、超时（180s）同样不暴露。

### 价格与用量

- 单价表**以飞书「模型定价」表为准**（`price_table_id`，人工可改，60 秒内生效；读不到时回退 `pricing.json`），**金额单位是人民币元（CNY）**（网关没有价格接口，实测 `/api/pricing` → 404）。**按网关控制台的真实报价填**，并且**一律保留三位小数**（飞书数字列已设 `0.000` 格式：`0.150`、`0.038`、`15.000`）。
- `单价元` = 按次统一价；**按分辨率分档的模型**填 `1K档 / 2K档 / 4K档 / 其他档`，档位由**分辨率档位 × 画面比例**决定的实际像素判定（≤1.6MP→1K、≤4.2MP→2K、更大→4K/其他）。工作台会**跟着所选档位显示对应价格**，而不是一个统一单价：
  - 模型下拉每一项显示当前档位的价（`gpt-image-2-auto · ¥0.065/2K`）；
  - 分辨率档位下拉每项带价（`1K · 1024 级 · ¥0.045/张`、`2K · ¥0.065/张`、`4K · ¥0.095/张`）；
  - 下拉下方小字列出该模型的全部档位价（`分档价：1K ¥0.045 · 2K ¥0.065 · 4K ¥0.095 · 其他 ¥0.095`），并标出「当前尺寸 1536×2048 · 3.15MP → 2K 档」；
  - 预估行同时带上 `档位 + 尺寸 + 质量`，例如 `预计花费 ¥0.390 · 6 张 × ¥0.065 · 2K 档（gpt-image-2-auto · 1536x2048 · high）`；
  - 该档位没有单独报价时，明确提示「正用其他分辨率价 ¥X」；比例为「自动」时档位由原图决定、按 1K 估算并在提示里说明。
- **未核实到价格的模型一律 `null`**：界面显示「未定价」、不计入预估、账单里单独标「未定价」——宁可显示没有，也不显示错价。
- 已按控制台逐项核对（三位小数）：`gpt-image-2` / `-n` / `-2.5` / `-2.5-flare` / `-2.5-sunburst` **0.038**；`gpt-image-2-eco` / `-2.5-flare-eco` / `-2.5-sunburst-eco` / `gemini-3.1-flash-image` / `gemini-nano-banana-2.1` **0.150**；`gemini-3-pro-image` **0.180**；`grok-imagine-image` **0.042**；`gpt-image-2-auto` **0.045/1K · 0.065/2K · 0.095/4K · 其他 0.095**；`grok-imagine-image-2.0` **0.100/1K · 0.120/2K · 其他 0.160**；`grok-imagine-image-quality` **0.100/1K · 0.140/2K · 其他 0.160**；`seedvr2-7b` **0.006/1M · 其他 0.008**；`gpt-image-2-native` 是 **token 计价（输入 15.000/1M、输出 60.000/1M）→ 未定价**；5 个控制台未展示的 `-auto` / `-native` 变体未定价。
- 控制台报价符号是 `$`：若你的实际结算按美元，把 `pricing.json` 的 `currency` 改成 `USD`（或在 `config.json` 里设 `defaults.pricing_currency: "USD"`），界面符号随之变成 `$`，数字不变。
- 左下角实时显示 **预计花费**：`张数 × 单价`（模型/尺寸/质量/颜色变化即时重算）。
- 任务卡里的「本次花费」= 已完成张数 × 单价，旁边小字是整批预估。
- 顶栏「今日 N 张 · ¥X」芯片是**用量账本**：每完成一个任务写一条到 `data/usage.json`（幂等，重试不会重复计费），点一下（管理员）打开**账号用量**抽屉。
6. **开始生成 → 先问需求**（见下）→ 生成中右侧实时更新 KPI、进度条、成品卡（含骨架扫光）、日志。
7. **单张**：卡片右下 下载（附件下载）、改图；失败的卡片多一个 重试（只重跑这一张）。
8. **批量**：卡片左上勾选或「全选成品」→ 批量改图 / 批量导出。

### 账号登录与用量（飞书「账号」表）

- 打开站点先到 `/login`：账号密码都在飞书 **账号** 表里（`account_table_id`），密码支持 PBKDF2 哈希；运营直接把密码写进「明文密码」列也能登，**首次登录成功后自动转成哈希并清空明文**。
- 当前管理员：**admin / Asd123456.**
- 角色 `admin` 才能进 **管理后台**（`/admin`）并看到全站用量；普通成员访问会被弹回并提示（接口 403）。
- 会话 Cookie `wb_session`（HMAC 签名 + 12h 过期）；`?token=<auth_token>` 仍可作机器/应急入口（等同管理员）。
- 停用账号：把「状态」改成 `停用` 即立刻登不上（下一次请求就失效）。
- 每个任务会把自己的发起账号写进记录表「账号」列，所以飞书里能按人筛选、求和。

### 管理后台（/admin，仅管理员）

右上角 **管理后台**（或直接 `/admin`）：

| 区域 | 内容 |
|---|---|
| 总面板 | 总图片 / 总花费 / 账号数 / 账号池额度（总额+已用）/ 今日（含「较昨日 ±N%」） |
| 用量趋势 | **折线图 + 面积图**：近 14 天，左轴张数（实线+渐变面积）、右轴花费（虚线），鼠标悬停出十字线和当日明细 |
| 今日每小时产出 | **柱状图**：0–23 点逐小时张数，悬停看该小时张数与花费；标题栏给今日总量与峰值 |
| 按款式 / 按颜色 | **横向柱状图**：柱长 = 张数，右侧标花费，取前 8 名（飞书「款式」「颜色」字段聚合） |
| 账号管理 | 新建子账号（账号+密码+显示名+角色+额度）；就地改角色、启用/停用、额度元；改密码、删除；已用 / 剩余额度进度条 / 张数 / 次数 |
| 用量排行（按账号） | 横向条形图，数据来自飞书记录 |
| 模型用量 | **横向柱状图**：柱长 = 张数，右侧标花费 |
| 最近记录 | 时间 / 账号 / **款式** / 成品 / 模型 / 张数 / 金额 / 任务ID |

- 图表是**纯 SVG + CSS，不引任何外部库**（离线也能用、不占流量）：`admin.js` 里 `renderTrend`（折线/面积、双 Y 轴、悬停十字线）、`renderHourBars`（竖向柱状）、`hbars`（横向柱状）。
- 轴刻度用 1/2/2.5/5/10 取整（206 张的轴顶是 250，不会一跳就 500）；近 14 天会在后端补齐空白天为 0，曲线不会跳。
- **注意**：`aurora.css` 有一条给图标用的全局 `svg { fill:none; stroke:currentColor }`。图表里的形状都用 `.c-bar / .c-line-a / .c-text` 这类类名接管样式，别直接写 `fill="..."` 属性（会被那条全局规则盖掉，柱子会变成白描边空框）。
- **额度是真的会拦**：成员（`额度元 > 0`）建任务时，`已用 + 本次预估 > 额度` 直接 400 拒绝，并告诉差多少；管理员不限。额度按飞书记录里该账号的「金额元」累计。
- 账号增删改全部落到飞书「账号」表，所以人工在表里改也一样生效；`tools/feishu_account_admin.py` 可在命令行做同样的事。

### 图案策略（`pattern_mode`，用户说了算）

| 值 | 含义 | 提示词里怎么写 |
|---|---|---|
| `preserve`（默认） | 图案逐像素照抄，只换面料底色 | `PRINT LOCK` + 图案锁否定词 |
| `per_color_variant` | **每个颜色一套不同图案** | 撤销 `PRINT LOCK`，写 `PATTERN VARIETY`（点名当前颜色，要求原创、风格同族、不得复用同一张） |
| `replace` | 整批换成需求里描述的新图案 | `PATTERN REPLACEMENT — replace the existing artwork with: <需求>` |
| `free` | 图案可自由重绘 | `PATTERN FLEXIBILITY`，其余锁定不变 |

- 选法有三条：界面「图案处理」下拉；harness 里直接说（会 `set_requirements(pattern_mode=...)` 或从原话自动识别）；`POST /api/jobs` 的 `pattern_mode` 字段。留空 = 后端按需求关键词自动判断。
- 否定词也跟着换：允许重画时不再说"不要重绘图案"，改成"不要只是给原图案换色 / 两个颜色不能复用同一张图"。
- 实测：同一批 3 个颜色 → 3 条互不相同的提示词，每条都点名自己的颜色、都无 `PRINT LOCK`（`tools/check_pattern_mode.py`）。
- 预览接口也会跟着变：`POST /api/preview {garment_type,color,requirement,pattern_mode}` 返回合并后的 `single_pass` 与 `negative`，界面「提示词」抽屉会显示当前图案策略与本次需求。

### 历史与成品：以飞书为准

- 每个成品一条记录，**成品图 / 参考图**存在记录的附件字段里（见 `deploy/feishu.md`）。
- 界面「历史任务」卡片右上角 **飞书历史 · 打包下载**：列出飞书里的历史成品 → 勾选 → **打包下载 ZIP**
  （图片是从飞书附件实时下载的，不依赖本机 `data/`，所以清理了本地任务目录、甚至换台电脑，历史成品照样能拉回来）。
- 接口：`GET /api/feishu/records?limit=200&job_id=&color=`、`POST /api/feishu/export {record_ids:[...]}`。

### 开工前先问需求（DeepSeek agent · 对话框 + 流式输出）

> **用户要求优先于 skill 的默认规则。** 默认规范是「图案逐像素保留、只换面料底色」，
> 但只要用户说「每个颜色不同图案 / 换成 XX 图案 / 重画印花」，就按用户说的做：
> 默认的图案锁会被显式撤销（提示词里根本没有 `PRINT LOCK`，改成 `PATTERN VARIETY` 正向策略），
> 其余未提及的约束继续锁死。harness 只做一句风险提示，不会用「跟硬规则冲突」来回绝。
>
> **提示词是真的重写，不是把原话贴末尾。** 需求会被归类（图案/背景/光线/删除/尺寸/字距/
> 颜色/文字/清晰度…），写进 `OPERATOR REQUEST (authoritative)` 段落，同时把冲突的那条
> 锁定从句子里删掉、换成对应的正向指令；`KEEP UNCHANGED` 只列没被要求改的部分。

> harness 不是"脚本填模板"：它是**带工具调用的 DeepSeek agent**（默认 `deepseek-flash`，支持图片输入），
> 会真的读你上传的图、查模型价、算总价，并在你同意后**直接提交生图任务**。
> Key 在 `.env → DEEPSEEK_API_KEY`；`config.json → defaults.harness` 可改模型/URL/最大工具轮数。
> 在线模型不可用时自动降级成本地分析师，对话框永远有结果。

工具清单（本地执行，agent 只做决策）：

| 工具 | 作用 |
|---|---|
| `inspect_images` | 看这批图：尺寸/比例/是否低分辨率/面料主色（配合图片输入，模型真的"看见"图） |
| `list_image_models` | 列模型 + 单价 |
| `estimate_cost` | N 张 × 单价 |
| `submit_generation` | 正式开工（写进任务队列，并发自动） |
| `submit_rework` | 以选中的成品为参考图返工 |
| `job_status` / `get_prompt` | 查进度 / 读当前提示词 |

行为约定：

- 用户说「跑 / 开工 / 确认 / 就这样」就**直接提交**，不再追问；信息不足才问一句。
- **提问不是要求**：问"这图能改吗""大概多少钱""会不会糊"时，它只会解释与报价，**不会**把问题原样写进提示词。要写进提示词的要求由 agent 用 `set_requirements` 提炼后记录，或由 `submit_generation` 的参数携带。

点「开始生成」不会立刻出图，而是打开右侧 **需求沟通** 面板（应用栏的「需求沟通」按钮同源）：

1. harness 先说明这批的规模（款式 × 颜色 × 图数 = 预计调用次数），问你**有没有特殊要求**，并给出快捷标签（图案必须原样 / 背景别动 / 整体提亮 / 只要正面平铺…）。
2. 你写一句需求 → 立刻以**流式输出**（SSE，逐字显示）给出分析：命中的关注点、每点的执行方式、锁定不动的清单，并展示将提交的提示词片段。
3. 两种收尾：
   - **按默认生成** → 不带任何额外约束，直接用 skill 默认提示词生成；
   - **按分析结果生成** → 把你的需求作为 OPERATOR REQUIREMENTS 追加在默认提示词末尾（skill 的所有硬约束仍然生效）。
4. 没有在线模型也能跑：默认走本地分析师（`refiner=local`，确定性规则）；若在 `config.json → defaults.harness` 配了可用的模型，则改为流式转发模型输出，失败自动降级。

### 改图返工（对话框 + 流式）

单张 改图 或勾选后 批量改图，同一个对话框切换成返工模式：

1. 说明要改几张，等你写需求（或点快捷标签）；
2. harness 流式输出「会动什么 / 锁死什么」，并给出**将提交的完整提示词**（以该成品为参考图的 REVISION REQUEST）；
3. 确认返工 → 提交 kind=rework 的独立任务（`refiner=harness`，提示词即上一步的成品），只重跑选中的那几张，结果可继续下载 / 再改图 / 批量导出。

**改图可以换模型（7.6.5 起）**：对话框里多了一行「改图接口 / 改图模型」，默认跟原任务一样，
换成别的接口或模型时：

- 成品图会作为**参考图**发给新模型，提示词追加 `CROSS-MODEL HANDOFF`——明确「这张图是上一步成品/参考」，
  要求复现它的版型、图案套准、面料颜色、人物与构图，只改需求里提到的那一处，不许整张重画；
- 提示行会算出**预计花费**（按新模型单价 × 张数），换模型时高亮成琥珀色；
- 任务参数记录 `source_provider` / `source_model` / `model_switched`，日志写一行
  `换模型重画：mock / mock-image → terra / gpt-image-2（成品作为参考图交给新模型）`，方便事后追。

## 4. 生成方式


界面只剩 `一次到位 single_pass`：改款 + 换色在一次调用里完成，调用次数 = N 图 × M 色。

> 代码里仍保留 `two_phase`（两段式）与 `recolor_only`（仅换色）两种模式，需要时用命令行 `--mode two_phase|recolor_only` 调用，界面不暴露。

## 4.5 观感与手感（6.0）

### 看图和下载：都回到原图

之前的「省流量」那套（缩略图网格、`w=1600` 预览、JPG 压缩下载）**已经撤掉**：
本地跑没有出口流量成本，压来压去只会让成品细节打折。

| 位置 | 现在怎么做 |
|---|---|
| 成品网格 | 直接加载**原图** `/api/jobs/{id}/image/{task}?v=<版本号>`；用 `IntersectionObserver` 只加载视口附近的卡（默认 500px 预取），屏幕外的等滚到再取 |
| 点开大图 / 下载 | 都是原图（`?download=true`），PNG 完整保真 |
| 批量导出 ZIP | 原图打包（`/api/jobs/{id}/export`，不带 `fmt`），SKILL 的 2MB 规则照旧生效 |
| 飞书历史 | 每行「直链」= 浏览器直接从飞书下载；「打包下载 ZIP」在本机合并 |

> `/thumb/{task}?w=` 和 `/image?...&fmt=jpg` 这两个接口仍然保留（后端没删，命令行/脚本还能用），
> 只是界面上不再用了。缓存文件位置也顺手修正了：`<job>/thumbs/`、`<job>/derived/`
> —— 之前错写进 `<job>/staging/thumbs`，会被 skill 的打包脚本当成一个 item 数图，
> 导致「contains 2 images; expected 3」整批打包失败（详见 §11 常见问题）。

### 滑动更顺 + 反馈更明确

**为什么之前卡**：整页极光层有 `filter: blur(10px)` 而且一直在动，页面上十几个卡片又各自带
`backdrop-filter: blur(18px)`。滚动时浏览器每帧都要为每个卡片重新采样背景模糊 —— 这是掉帧的主因。

| 改法 | 效果 |
|---|---|
| 卡片**去掉 `backdrop-filter`**，改用更实一点的底色 + 内高光 + 细边框 + 分层阴影 | 观感几乎一致，滚动回到 60fps；模糊只留在一小块区域（顶栏 / 抽屉 / toast） |
| 极光层去掉 `filter: blur()`，只留渐变本身 | 少一层全屏滤镜，滚动不再抖 |
| 顶栏阅读进度条从改 `width` 换成 `transform: scaleX()` | 不再触发布局，只走合成层 |
| 成品卡 `content-visibility: auto` + `contain: layout paint` | 屏幕外的卡片不参与布局/绘制 |
| 成品图 `opacity` 淡入（`.loaded`）+ `.shot` 固定 `aspect-ratio` | 图片加载不会引起跳动 |
| `.perf-lite` / `body.busy` 仍然保留 | 出图多、任务在跑时自动关掉装饰动画 |

**反馈更明确**（这次新加）：

| 场景 | 反馈 |
|---|---|
| 任何接口调用 | 顶部 2px 进度条（>180ms 才亮，本机快请求不闪）；飞书/打包/拉模型这类慢请求会一直亮着 |
| 历史任务 / 飞书历史 / 账号用量 | **骨架屏**占位（不再是一片空白） |
| 重试失败 / 取消 / 提示词 / 补写飞书 / 飞书刷新 / 账号刷新 | 按钮转圈 + 禁用 + 文案（「重试中…」「打包中…」），结束后自动复原 |
| 模型下拉 | 先显示「正在读取模型…」并禁用，拿到列表后恢复 |
| 出图期间 | 卡片状态文字 + 进度条 + KPI 数字滚动；`jobStage` 显示当前阶段（生成中 / 校验并打包 / 完成） |

---

## 5. 接口配置（`config.json`）

Key 放 `.env`，`config.json` 只写变量名，避免代理/共享文件时泄露：

```ini
TERRA_API_KEY=sk-...      # 65535 网关（已预置）
A6API_API_KEY=sk-...      # a6api 备用接口（已预置）
OPENAI_API_KEY=...
ARK_API_KEY=...
GEMINI_API_KEY=...
```

### 已预置的三档 65535 网关（同一个 Key）

| provider | 模型 | 说明 |
|---|---|---|
| `a6api`（**主接口**，9.1.0 起） | `gpt-image-2.5-sunburst` | `https://api.a6api.vip/v1`，窗口只显示「主接口」；不限并发、不排队；报价见下 |
| `terra`（**备用接口**） | `gpt-image-2` 默认，另有 `gpt-image-2.6-flare` / `gpt-image-2.6-sunburst` | 65535 网关，实测 1 图 ~30s；**要排队**（飞书闸门 15 槽位）；6 个型号的报价见下 |
| `terra-nano` | `gemini-nano-banana-2.1` | 同类里改图保真度通常更好，人物/图案更稳，适合“必须保留原图案”的单子 |
| `terra-eco` | `gpt-image-2-eco` | 便宜快速，适合先小批量试提示词 |
| `local` | 本地算法 | 不调接口，只换颜色、图案零失真，秒出图（见 §7.5） |

### 按模型收敛参数：grok 只认 low / medium / auto（9.4.4）

备用接口用 grok 生图时报过
`upstream_error: This model only supports the following quality value(s): low, medium, auto.`
—— 工作台默认发 `quality=high`，被上游直接打回。

现在 `config.json → defaults.quality_rules` 里按**模型前缀**声明允许值：

```jsonc
"quality_rules": [
  { "match": "grok-imagine", "allowed": ["low", "medium", "auto"], "default": "auto" }
]
```

- 建单时 `Provider.quality_for()` 就把 `high` 收敛成 `auto`（任务参数/界面/预估一致）；
- `openai_compat._form()` 发请求前再兜一次，历史任务重试、返工同样安全；
- 收敛成 `auto` 时**不发送** quality 字段（auto 是上游默认）；
- 别的模型（gpt-image 系列、主接口 a6api）完全不受影响；
- 以后别的模型也有参数限制，加一条规则即可，不用改代码。

回归：`python tools/check_model_params.py`。

### 模型下拉只列「有报价」的（9.4.3）

65535 网关有 36 个模型，其中二十多个没报价。现在 `/api/models` 默认只返回**能算出单价**的
（价格解析链：接口 `model_prices` → 飞书「模型定价」表 → `pricing.json`），并保留当前正在用的那个；
`defaults.models_only_priced`（默认 `true`）可以关掉，排查时用
`/api/models?provider=terra&include_unpriced=true` 看全量。实测备用接口从 36 个收敛到 9 个。

### 哪些接口要排队/限流：按接口开关（7.6.8）

飞书闸门（15 个槽位 + 排队号）和并发上限**只对需要限流的接口生效**。
配置里给 provider 加 `"unlimited": true`（等价于 `"skip_gate": true` + 不限并发）：

| provider | `unlimited` | 行为 |
|---|---|---|
| `terra`（65535 网关） | 否 | 并发按 `max_concurrency`（20），超额进飞书队列，界面显示「前方还有 xx 个任务」 |
| `a6api`（备用） | **是** | **不占闸门槽位、不排队、并发=任务数**（实测 100 并发 100/100、0 限流） |

```json
"a6api": { "unlimited": true, "max_concurrency": 0, "global_concurrency": 0 }
```

- `service.gate_for()` 遇到 unlimited 直接返回 `None` → 整条闸门链路（取号/抢坑/轮询）都不走；
- `service.create_job()` 把并发直接设成任务数，日志写「全部提交：N 张一次发完（a6api 不限并发、不排队）」；
- `service._provider_sem()` 对 unlimited 返回 `None` → 多任务同机也不再共享上限；
- `runner` 对 unlimited 放开本机 clamp（其它接口仍是 128 安全上限）；
- 界面「队列」那行会显示 **「该接口不排队 · 并发不限（一次全推）」**，切换接口即时刷新。

注意：`unlimited` 只该给「实测能扛」的接口。65535 网关实测有并发墙，所以保持限流排队。

### 备用接口（65535）新增 gpt-image-2.6-flare / gpt-image-2.6-sunburst（按次 0.038）

网关 `/v1/models` 里本来就有这两个 id（连同 `-eco` / `-native` 变体），工作台之前只是**没给价**，
下拉里显示「未定价」。2026-11-09 按运营给的报价补上：**按次调用 ¥0.038/张**（与 2.5-flare / 2.5-sunburst 同价）。

- 价目写在两处：`pricing.json → models`（默认价目）+ `config.json → providers.terra.model_prices`
  （按接口覆盖）。**两处都要有**：飞书「模型定价」表存在时以飞书为准，而飞书表里目前没有 2.6 的行，
  只改 `pricing.json` 会在飞书刷新后变回「未定价」；接口级覆盖是唯一稳的那一层。
- 真机各跑 1 张（10 弹力短袖 · 深酒红，原图带 06 印花，1024×1024、约 1.6MB）：
  `data/test/model-26/2.6-sunburst.png`、`data/test/model-26/2.6-flare.png` —— 版型贴合效果图、
  印花与袖标保留、无人物。
- 两个模型都要走备用接口的并发墙（闸门排队），不像主接口那样不限并发。
- `-eco`（0.15 量级）与 `-native`（按 token）没有报价，保持 `null` → 界面显示「未定价」，不计入预估。

> 注意：飞书「模型定价」表里 `gpt-image-2.6-sunburst` 已被人工填成 **0.08**。
> 现在接口级覆盖 0.038 优先，所以界面/账本按 0.038 走；如果 0.08 才是对外报价，
> 把 `config.json` 里这条覆盖去掉（或改飞书表），两处口径要一致，否则两个页面会打架。

### 备用接口 a6api：真实单价、耗时分布与并发甜点

**真实单价（以网关后台消费记录为准）：`¥0.005978 / 张`**（每条约 48s 一单）。
工作台已按这个价填在 `a6api.model_prices`，预计花费/账本/飞书「金额元」都按它算；
想改就改 `config.json` 里这一行：

```json
"a6api": { "model_prices": { "gpt-image-2.5-sunburst": 0.005978 } }
```

> 早先我用 `/v1/dashboard/billing/usage` 的增量反推过单价，那个 `total_usage` 的单位和后台
> 「模型价格」不是一个口径，反推结果偏大十几倍——**以后以后台消费记录为准**。

**为什么后台 48s、界面却等 100 多秒？** 实测拆开看（同一张图、同样 quality）：

| 并发 | 单张 p50 | p95 | 总墙钟 | 说明 |
|---|---|---|---|---|
| 1 | 22.4s | — | 22.4s | 上传 87KB + 推理 + 回传 2.4MB |
| 10 | 22~25s | 37~39s | 39.1s | 基本等于推理时间 |
| 30 | 33.0s | 39.6s | 74.9s | 轻微排队 |
| 100 | 51.0s | 84.0s | 246.2s | 网关内部排队，最长单张 130s |

结论：**网关后台的「耗时」只算它自己推理那一段（约 45s）**；我们端到端还包括
上传原图+参考图、网关排队、回传 2.4MB。并发越高，网关排队越久——100 并发时单张会被拖到 80~130s，
这就是你看到「一百多秒」的原因（不是我们本地在排队，`unlimited` 之后本地队列已经没有了）。

**甜点**：单批 20~30 张。吞吐仍有 ~24 张/分钟，单张等待回到 33~40s。
如果嫌单张慢又不想放弃全推，可以给这个接口设软上限（不排队，只是分批发）：

```json
"a6api": { "unlimited": true, "batch_concurrency": 25 }
```

`batch_concurrency: 0`（默认）= 一次全推；设了 N = 同时发 N 张，其余等前一批，仍然不进飞书队列。

### 袖长也要按款式钉死（7.6.7）

上一轮 100 张里出现过「长袖款跑出短袖」，所以袖长和袖口一样做成了款式属性：

| 归属 | 款式 | `sleeve` |
|---|---|---|
| 长袖 | 24 / 25 长袖、03 卫衣、06 帽衫、28 卫衣童装 | `long`（必须到手腕、盖住小臂） |
| 短袖 | 23 / 21 索罗娜、07 水洗、10 弹力、29 水洗童装 | `short`（必须止于肘上、露小臂） |

提示词插入 `SLEEVE LENGTH — …` 段，否定词也跟着换（长袖款禁止短袖/挽袖/七分袖，
短袖款禁止长袖/挽袖）。`tools/check_cuff.py` 里同时断言袖长，`styles.json` 每款一个 `"sleeve"`。

网关上还有 `gemini-3-pro-image`、`gpt-image-2.5*`、`grok-imagine-image*` 等，界面「拉取」后随手换一个模型即可对比效果（`grok-imagine-video` / `veo-*` / `seedance-*` 是视频模型，不能用于改图）。

适配器一览：

| `type` | 用途 | 关键字段 |
|---|---|---|
| `openai_compat` | OpenAI `gpt-image-1`、各类 one-api / new-api / 中转站 | `base_url`、`model`、`image_field`、`max_images`、`response_format` |
| `ark` | 火山方舟 Seedream 4.0 / SeedEdit | `model`、`multi_image`、`image_encoding` |
| `gemini` | Gemini 图片编辑（nano-banana） | `model`、`response_modalities` |
| `fal` | fal.ai（flux-kontext、qwen-image-edit…） | `model`、`images_field` |
| `custom_json` | 其它任意 HTTP 接口（模板驱动，支持异步轮询） | `submit_url`、`body`、`result_b64_path`/`result_url_path`、`poll` |
| `mock` | 本地占位图，验证流程用 | `mock_delay_sec`、`mock_fail_rate` |

通用字段：

| 字段 | 说明 |
|---|---|
| `label` | 界面上显示的名字 |
| `enabled` | `false` 时界面显示「未启用」 |
| `api_key` / `api_key_env` | 直接写 Key，或写 `.env` 里的变量名（推荐后者） |
| `model` | 该接口默认模型；界面上可临时改成任意模型（每次任务单独生效） |
| `max_images` | 单次调用最多带几张图。≥2 时会把**颜色参考图**和原图一起发送 |
| `concurrency` | 该接口的默认并发（界面上的并发数初值） |
| `rpm` | 默认每分钟请求上限，`0` 为不限 |
| `global_concurrency` | 多个任务同时跑时，该接口**共享**的并发上限（`0` 不限制）。上游有硬限制就设它 |
| `auto_size_presets` | 该模型支持的固定尺寸列表（如 `["1024x1024","1536x1024","1024x1536"]`）。设了以后 `size=auto` 会按**原图宽高比自动挑最接近的一档**，避免接口默认出方图破坏构图 |
| `models_url` / `no_model_list` | 模型列表地址覆盖 / 关闭模型拉取 |
| `proxy` / `trust_env` | 走代理时指定；`trust_env=false` 可忽略系统代理 |
| `extra_form` / `extra_body` / `extra_headers` | 透传给网关的私有参数 / 请求头 |

### 拉模型、换模型

```powershell
python cli.py --provider terra --list-models          # 列出该接口全部模型，图片模型排最前
python cli.py --input D:\in --garment-type 短袖 --provider terra-nano --model gemini-nano-banana-2.1
```

界面上是同一个能力：选接口 → 点「拉取」→ 下拉里挑模型（或直接手填）→ 开跑。模型选择按接口分别记忆在浏览器里。

**把中转站接进来看这里**：把 `providers.gateway.base_url` 改成你的中转站地址（例如 `https://your-gateway.com/v1`），`model` 改成中转站支持的模型名，`API Key` 填 `OPENAI_API_KEY` 即可。若网关只接受单图字段，把 `image_field` 从 `image[]` 改成 `image`。

**异步轮询接口**（提交返回 task_id、再查状态）用一个 `custom_json` 例子说明：

```json
"myapi": {
  "type": "custom_json",
  "label": "某云图片编辑",
  "enabled": true,
  "submit_url": "https://HOST/v1/images/edit",
  "method": "POST",
  "model": "MODEL_NAME",
  "api_key_env": "CUSTOM_API_KEY",
  "headers": { "Authorization": "Bearer ${api_key}" },
  "body": {
    "model": "${model}",
    "prompt": "${prompt}",
    "image": "${image_data_url}",
    "size": "${size}"
  },
  "poll": {
    "url": "https://HOST/v1/tasks/${task_id}",
    "id_path": "data.task_id",
    "status_path": "data.status",
    "done_values": ["succeeded"],
    "fail_values": ["failed"],
    "image_url_path": "data.result.url",
    "interval_sec": 2,
    "max_sec": 180
  },
  "multi_image": true,
  "max_images": 2
}
```

可用占位符：`${model} ${prompt} ${negative} ${size} ${quality} ${seed} ${image_data_url} ${image_b64} ${ref_data_url} ${ref_b64} ${image_count} ${api_key}`，轮询里额外有 `${task_id}`。

---

## 5.9 并发与排队（接口实测 20 并发）

**界面上没有「并发数」了（7.6.2 起）**：默认**全部一起提交**，超过上限的自己排队，不需要人算。

**两层限制**，缺一不可：

| 层 | 配置 | 作用 |
|---|---|---|
| 本机 | `providers.<name>.max_concurrency`（terra 三个网关都设 20） | 单个工作台最多同时发几个；一次提交超过这个数 → 多出来的**排队**而不是丢掉 |
| 跨机器 | `defaults.gate`（飞书表队列 / Cloudflare 阀门） | 多台电脑共用一个 Key 时，**合计**不超过额度；富余的任务在表里取号排队 |

**排队位置怎么来的**：任务拿不到槽位时会往闸门表写一行「排队号」，界面按 `poll_ms` 轮询这张表，
数出「正在跑的 + 排在我前面的票」= **前方还有 xx 个任务**，显示在任务条（橙色徽标）和进度行里。
数字全部来自飞书表的实时行，所以**别的电脑插进来排队也算得进去**；闸门没启用时退回「本批还没出图的任务数」，含义一样。

- 任务状态分成三段：**等待 → 排队中 → 生成中**。只有真正发出请求时才是「生成中」；
  在队列里等的那几张，卡片上写的是 **`排队中，还有 N 个`**（N 来自飞书表的实时位置）。
- 本机还会自己记「我手里还占着几个槽位」（`_local_leases` + `_local_reserved`）：
  飞书 search 是最终一致的，刚写的租约可能读不到，只信表会「都以为有空位」→ 一起放行超发
  （真机踩过：limit=3 跑出 5 张同时在跑）。加了本地额度判断后，超出的任务**一定进飞书队列取号**，
  界面也就一定有排队数字。

任务日志会写清楚发生了什么：

```
全部提交：30 张，同时最多 20 张在跑，闸门额度 15，其余 10 张排队（界面会显示「前方还有 xx 个任务」）
排队中：前方还有 12 个任务（闸门 15/15，已等 18s）
全局并发阀门不可用（HTTP 502），本次只用本机并发 20
```

### 最省事的排队方式：一台当服务端，大家用浏览器提交

**不用云、不用装任何东西**：把工作台跑在一台常开的电脑上，别人用浏览器访问它。
所有任务都进这**一个**队列，自动按 20 并发排队执行，出图存这一台，谁都能在「历史任务」里下载。

```powershell
双击 run_share.bat            # = python serve.py --share --open
```

启动横幅会直接打印给同事用的地址（并把本机局域网 IP 找出来）：

```
共享模式：同一局域网的同事用下面的地址访问这台机器
  http://192.168.1.23:8787
· 任务、并发、出图全在这一台机器上排队执行，别人只是用浏览器提交
· 第一次可能要在 Windows 防火墙放行：
  netsh advfirewall firewall add rule name="workbench 8787" dir=in action=allow protocol=TCP localport=8787
```

- 提交的人用飞书「账号」表里的账号登录，额度/用量照旧统计；管理员在 `/admin` 看所有人。
- **跨网络**（不同办公室/在家）：装一个 [Tailscale](https://tailscale.com)（免费 3 用户/100 设备），
  两台机器都登录同一账号，然后用 `100.x.y.z:8787` 访问，不用开公网端口。
- 电脑休眠会停服：把「电源选项 → 睡眠」设为永不，或指定一台台式机当常开节点。

**三种做法怎么选**

| 做法 | 成本 | 适合 | 代价 |
|---|---|---|---|
| **A. 一台当服务端 + 浏览器共享**（本节，推荐） | 0 | 同事都在一个局域网/公司网 | 那台机器得开着 |
| B. A + Tailscale | 0 | 有人在外网 | 每人装一个 Tailscale 客户端 |
| C. 各自本地跑 + Cloudflare 阀门（§ 下面） | 0 | 各人用自己的机器跑，只共用一把 Key | 只能限并发，**不是队列**：任务分散在各自电脑上 |

> C 的定位是「防止大家一起把网关打爆」，A 才是「提交后统一排队、统一返图」。

### 出图完成通知（可选，免费）

不想守着屏幕时，配一个飞书群机器人，跑完自动推一条：

1. 飞书群 → 设置 → 群机器人 → **添加机器人 → 自定义机器人** → 复制 webhook 地址；
2. `config.json`：

```json
"defaults": {
  "notify": { "enabled": true, "feishu_webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/xxxx" }
}
```

（也可以把地址放 `.env` 的 `FEISHU_WEBHOOK=`，配置留空即可。）

推送内容：`【出图完成】2 个款式 · 12 张 / 成功 12 · 失败 0 · 花费 ¥0.456 / 任务号 xxx / 取图 http://192.168.1.23:8787/`。
共享模式下这个链接自动换成局域网 IP，同事点开就能下载。
通知失败只写一条 warn，不影响任务状态。

### 推荐：飞书多维表格当「并发闸门 + 队列」（零额外服务）

不用 Cloudflare，就用大家已经在用的飞书表：**表里有几行占位，就有几张图在跑。**

```
每要发一张图 → 往「并发闸门」表写一条占位（占位/主机/说明/开始/租约）
占位 < 15  → 直接调接口；出图结束（成功或失败）立刻删掉这条占位
占位 ≥ 15  → 进队列：每 ~2.5s 轮询一次，别人删了就能进
```

**两个坑都处理了**

### 真机实测记录（2 台机器、30 张图、额度 15）

第一版用「一行一个任务、数行数」的做法，实测**超发到 12（额度 6）** —— 飞书搜索是最终一致的，
多个客户端同时读到旧快照就一起放行。于是改成现在的**固定槽位 + 原子抢占**：

```
表里固定 15 行（槽位 01..15），抢坑 = 找空闲行 → 写我的租约 → 回读这一行确认还是我的
同一行只能留一个人的租约 → 同时在跑的数量结构上不可能超过 15，与索引延迟无关
没空位就取一个「排队号」行（Q 开头），按服务端 created_time 先到先得
```

实测（修复后，`python tools/check_queue_30.py`）：30 张先提交、5 秒后再插 5 张

| 指标 | 结果 |
|---|---|
| 同时占用槽位峰值 | **8~10 / 15**（从没超） |
| 35 张全部完成 | 35/35，0 失败，0 限流 |
| 先到先得 | 后插的 5 张 `started_at` **全部晚于**前 30 张 → FIFO 成立 |
| 收尾 | 占用 0、排队号 0 残留 |
| 真接口旁证（6 张） | 2 台机各 3 张真 gpt-image-2 → 6/6 成功、峰值 6、无 429 |
| **真接口全量（35 张）** | 峰值 **13/15**、**33/35 首次成功**、后插的 5 张仍排最后 → FIFO 成立；2 张因下面那个 bug 失败，修好后重试补回（10/10、3/3） |

顺手修掉的 5 个问题（都在日志/埋点里暴露出来的）：

| 现象 | 原因 | 修法 |
|---|---|---|
| 一开始超发 12/6 | 数行数 + 索引延迟 | 固定槽位 + 单行写确认（结构上不可能超） |
| 互相让位死循环 | 回读一次不一致就判失败 | `_verify` 重试 3 次（等索引追平） |
| FIFO 不生效 | 排队号存在 gate 实例上（被多个任务共享） | 改成 `acquire` 的局部变量 |
| 30 张要 80s 才全部开跑 | settle 在锁里 + 整表搜索 N 次 + 线程池只有 cpu+4 | 锁外等索引、单条 `GET /records/{id}` 回读、线程池 64 |
| 飞书每次调用 ~0.7s | 每次都新建 TLS | 复用长连接（keep-alive） |
| 真接口跑完偶发 `provider 未初始化`（35 张里坏 2 张） | **同一台机器上两个任务共用一个 provider，谁先跑完谁 `close()`**，把另一个正在发的请求打断 | `runner` 不再 close provider，统一在服务退出时关；`open()` 本来就幂等 |

> 代价：**每张图多花 ~2~3 秒**（飞书一次往返 ~0.7s × 3~4 次）。出图本身 30~120s，这点开销可以接受；
> 如果嫌慢，把 `settle_ms` 调到 400、`poll_ms` 调到 1000 会快一些，但索引延迟大的网络下别低于 400。

| 坑 | 处理 |
|---|---|
| 飞书搜索索引有延迟（刚写的行可能查不到，误判成「还有空位」→ 并发超发） | 写完租约后**等 `settle_ms` 再回读确认**（重试 3 次），确认不到就让位重新排队 |
| 进程崩溃会留下僵尸占位，把队列永久堵死 | 每条占位带 `开始` 时间；超过 `stale_sec`（默认 10 分钟）的记录被任何客户端发现就顺手删掉 |

**启用（三步）**

```powershell
# 1) 建表（自动建好「并发闸门」表 + 5 个字段），把打印的 table_id 填进 config.json
python -m skillbridge.feishu_gate --init

# 2) config.json
"defaults": { "gate": { "enabled": true, "provider": "feishu", "table_id": "tblXXXX", "limit": 15 } }
#   （或者只写 "feishu": { "gate_table_id": "tblXXXX" }，defaults.gate 里不重复填）

# 3) 重启工作台；界面「并发数」下面会显示 队列 3/15
python tools/feishu_gate_status.py      # 或在命令行看当前占位： python -m skillbridge.feishu_gate --check
```

**实现要点**（`skillbridge/feishu_gate.py`）

- 与 Cloudflare 阀门同一个接口（`acquire / release / status`），runner 不用改；
- **排队超时不会放行**：等满 `wait_timeout_sec`（默认 30 分钟）就抛 `GateBusy`，
  这张图标记成「被限流」，稍后自动重试（退避 5s、10s…），也可以点下面的一键重试；
- 阀门本身挂了（读/写表失败）且 `fail_open: true` 时退回本机并发，不耽误出图；
- 日志会写 `排队中：全局并发 15/15（其他电脑也在跑），已等 12s`。

### 一键重试「被限流」的图

网关被打回（429 / 并发超限 / 频率过高）或闸门排队超时的图，会被单独标成 `rate_limit`：

- 任务标题旁会多出一个按钮 **「重试被限流 N 张」**（只有真的有这类失败时才出现）；
- 点它只把这几张重新排队，别的失败（内容审核、图片过大等）**不会**被重跑，不浪费钱；
- 接口：`POST /api/jobs/{id}/retry`，表单加 `only=rate_limit`。

### 为什么本机限制不够

三个人各开 20 并发，网关实际就是 60 并发 —— 照样被限、照样一起变慢。
所以并发额度必须放在**一处**：Cloudflare Worker + 单个 Durable Object（actor 天然串行）。

```
本机 A ─┐
本机 B ─┼─► https://workbench-gate.xxx.workers.dev   ← 唯一那把并发锁
本机 C ─┘      有牌就发，没牌按 retry_after 排队；租约 180s 自动过期
```

一次出图 = 2 次请求（acquire + release），免费额度完全够。部署 3 分钟，
见 [deploy/cloudflare-gate/README.md](deploy/cloudflare-gate/README.md)：
`wrangler secret put GATE_TOKEN` → `wrangler deploy` → 每台机器 `.env` 里加
`GATE_URL=...` 和 `GATE_TOKEN=...`。

**阀门挂了不耽误出图**：`fail_open: true`（默认）时记一条 warn、退回本机并发继续跑；
连续 3 次失败熔断 60 秒，避免每张图都先等一次超时。

界面「生成设定 → 并发数」下面会显示 `● 阀门 3/20 · 本机 ≤20`（可直接点一下刷新）；
`GET /api/gate` 返回同样信息，方便脚本探活。

不想上云：把 `gate.enabled` 留 `false`（默认），就只用本机 20 并发排队。

**测试**：`python tools/check_gate.py`（离线假阀门，验证「两台机器合计 ≤ 额度」、
租约全部归还、阀门挂了降级仍出图）；想在本机看效果跑 `python tools/gate_local.py --port 8795 --limit 2`，
再按它打印的两行 `.env` 配上即可。

---

## 6. 并发与稳定性

| 机制 | 行为 |
|---|---|
| 任务级并发 | 界面**自动**把并发设成预计调用次数（N 图 × M 色，上限 64），即一次把能发的都发出去；命令行可手填 |
| RPM 节流 | 每分钟最多 N 次调用，超出自动排队等待（防 429） |
| 全局并发 | `global_concurrency` 让多个任务共享同一接口的上限 |
| 重试 | 429 / 5xx / 网络错误按 1→2→4→8s 指数退避 + 抖动；优先遵守 `Retry-After`；4xx（Key 错、参数错）立即失败不浪费额度 |
| 结果校验 | 返回内容必须是能解码的图片，否则当作可重试错误（防网关 200 + 错误 JSON） |
| 单任务超时 | 默认 **180s**。**超时不自动重试**：网关超时/504 时上游往往已出图（控制台能看到成品），自动重试等于重复计费；失败卡片上点「重试」按需补跑。要恢复自动重试就加 `"retry_on_timeout": true` |
| 断点友好 | 每张成品落盘即写 `job.json`，服务重启后历史任务仍在，失败可单独重试 |
| 取消 | 立即中断在飞请求，未完成任务标记为「取消」，已完成的成品保留 |

---

## 7. 提示词是怎么来的

全部由 `skillbridge/prompts.py` 从 skill 生成，不靠手写：

- **一次到位**：用 `SKILL.md` 的「改款换色提示词」模板，把 `[TARGET_GARMENT_TYPE]` 替换成该款式在 skill 里的版型英文描述，末尾追加实测色值（如 `#130f0c`）作为数值锚点。
- **两段式**：第一阶段「只改款、保持原底色」，第二阶段「只改面料底色到指定色值」，两段都带 `保留原有图案/文字/logo` 与 `保留人物、姿势、背景、光线` 的约束。
- **PRINT LOCK（图案锁）**：三种模式的提示词都会附一段硬约束——印花必须**逐像素照抄**：同样的字形/字重/字距/大小/位置/旋转，同样的立体浮雕、光泽、边缘高光与投影；明确禁止重新打字、重排字距、拉伸、压平立体字、缩放或移动图案。同时在工作台的负面词里追加了对应的 ~20 条禁止项（不换字体、不改字距、不把平面印变立体、不丢斜面高光、不改领标…）。
- **MATERIAL（材质锁）**：针对“图案像贴上去”的问题——要求印花保留源图的**物理材质与摄影质感**：同样的表面工艺（磨砂胶印/发泡/刺绣/植绒）、字母内部的微纹理与胶片颗粒、同样的边缘柔度、同方向的高光、与面料交界处的接触阴影与厚度，并且衣服的褶皱和光线要**穿过**图案继续延伸。明确禁止：干净矢量 logo、铬合金渐变、贴纸/贴花感、CGI 渲染感、均匀平涂、比周围照片更锐利的边缘。负面词同步补了对应条目。
- **原图保真度 `input_fidelity`**：`gpt-image` 系列支持该参数，`high` 会让模型尽量沿用原图细节（人物、文字、图案）。`terra*` 三个接口已默认写死 `high`；需要临时改动时用命令行 `--fidelity low`，或在 `config.json` 里改 provider 的 `input_fidelity`。
- **主体（画面里有没有人）· 7.8.0**：原图是「纯衣服平铺/挂拍」还是「真人上身」，决定提示词完全不同。
  以前 skill 模板里写死 *Keep the exact same person, face, hair, skin, hands…*，**平铺图等于被要求造一个人出来**，
  实测同一批 3 张平铺里就有 1 张变成女生穿着卖。现在改成三层：
  1. **中性主体句**（默认）：`SUBJECT — the source image alone decides what the subject is`，
     原图有模特就保持同一个人、原图是空衣服就保持空衣服，**两者都不许新增原图没有的人和身体部位**；
  2. **禁人硬约束**（判定为纯衣服时）：`NO PERSON — …only the garment…` + 否定词 `no person / no human / no face / no hands / no neck / no skin …`，
     并且领口只能露背景或领内暗部、袖筒与衣身必须是空的；
  3. **成品复查**：出图后拿原图背景做遮罩，若「原背景区域」变成大片皮肤色 → 任务记 `person_alert` 并写日志；
     `retry_on_person=true` 时会加 `NO_PERSON_REINFORCE` 自动重画一次。
  判定规则见 `skillbridge/subject.py`：皮肤像素占比≈0 才断言「纯衣服」（缺席才可靠，有皮肤一律按真人/中性处理，绝不把模特擦掉）。
  界面上「生成设定 → 画面主体」可选 `自动 / 只有衣服 / 真人上身`，也可以给 `/api/jobs` 传 `subject=garment` 强制。
  回归脚本：`python tools/check_no_person.py`（加 `--live --source=…` 会真跑一张验证成品干净）。
- **对话优先：生成后直接在对话里确认（9.6.2）**：多色批次点「开始生成」后自动拉出对话框，
  对话里先显示「基准图生成中…」，出图后由 AI **把图返到对话里**（可点开看大图），
  按钮是「就按这张改色 / 我要改…」；说「领口小一点」→ 生成改图任务 → 出图后**再返图**
  + 「用这张当基准，开始改色」，可以反复到满意；确认后改色进度也在对话里更新。
  关掉对话就停轮询。画布顶部的基准图审核条仍然保留（两条路都能确认）。

- **沉淀按「重要性」分层，不再是照单全收（9.6.3）**：拍板（确认基准图）后，
  `skillbridge/align_judge.py` 在后台逐条评判这次的对齐要求 ——
  `scope=style`（这个款式的通病）/ `one_off`（只对这一张）、`kind`（版型/图案/颜色/面料/画面）、
  `importance`、`promote`（是否升级成款式级硬规则）。先本地启发式判，再让 harness 模型复核（可关）。
  - **硬规则**（promote）→ 提示词里的 `SKILL-LEVEL RULES FOR THIS STYLE`（靠前、强制）
  - **款式经验** → `STYLE RULES LEARNED …`（补充）
  - **一次性微调** → 只留档，不进提示词
  款式统计 `stats={batches, approves, review_rounds, promoted, learned}` 记账，用来看返工率。
  接口：`GET /api/jobs/{job_id}/distill`（评估结果）、`GET /api/styles/{slug}/lessons`（分层+统计）。
  回归：`python tools/check_silent_distill.py`

- **主动沉淀：不用用户说「记住」（9.6.2）**：确认基准图时自动把这次对齐过的要求
  （对话里每次修改 + 确认备注）写进该款式的 `lessons`，任务日志写「已自动记进款式规则 N 条」。
  开关 `defaults.auto_learn`（默认开）；重复要求只累加命中次数。
  回归：`python tools/check_auto_learn.py`（多款式批量 + 两次对齐 + note 留空也自动记住）。

- **30 扎染T（9.6.2）**：工作簿一直没有它的 sheet，客户给了款式图（尺寸表 + 3 张颜色实拍），
  于是做成「手工建档」：`tools/add_style_30_tiedye.py` 裁图 → `import_ph_catalog.py` 的
  `MANUAL_STYLES` 建档（白 `WH[0]` / 藕粉 `LPK[0]` / 奶茶 `MTC[0]`）。款式库 **25 款 / 115 色**。

- **先出基准图 → 对话确认 → 按基准改色（9.6.0）**：客户要求「先出基准图，通过 harness 与用户对齐，
  最终再以基准图改色减少错误率，并把对齐结果沉淀成该款式的 skill」。现在多色批次走两段：
  1. **只出基准图**：任务停在 `review`（不打包、不派生），界面顶部出现基准图审核条；
  2. **确认或修改**：点「确定，按它改色」→ 其余颜色以**这张基准成品**为参考图改色（派生色第 2 张发面料色卡）；
     不满意就点「说要改哪里（对话）」，对着基准图说一句 → 生成改图任务；改好后「采纳」回来当新基准。
  确认前不花派生色那笔钱；已出图的任务不会被重复购买（`_run_single_pass` 只跑没出图的）。
  单色批次没有派生色，闸门自动关闭；`POST /api/jobs` 可传 `review_master=false` 关掉。
  - 接口：`POST /api/jobs/{id}/approve`（确认 + 改色）、`/reject`（打回改图）、`job.review`（状态）
  - 回归：`python tools/check_master_gate.py`（记录型接口，不发真实请求）
  - 界面验收：`python tools/make_review_fixture.py` 造一个「基准待确认」假任务（不花钱、不写飞书）

- **款式记忆 lessons：对齐结果变成长效规则（9.6.0）**：审核时写的确认备注、对话里说「记住这条」，
  都会写进该款式 `library/prompts/<slug>.json` 的 `lessons`（含原话、英文约束、来源任务、命中次数）。
  生成时 `prompts.style_positive()` 把它拼成 `STYLE RULES LEARNED FROM PREVIOUS APPROVED BATCHES`
  （最多 6 条），所以同一句要求只说一次。翻译用 harness 的对话模型；模型不可用时回落成
  `Operator-confirmed rule …: 原话`，绝不因为翻译失败丢掉规则。
  - 接口：`GET/POST /api/styles/{slug}/lessons`、`DELETE /api/styles/{slug}/lessons/{id}`
  - 上限 12 条 / 每次注入 6 条，重复只累加命中次数

- **AI 助手：对话框里能确认和打回基准图（9.6.0）**：harness agent 新增 5 个工具 ——
  `master_status`（到哪一步了）、`approve_master`（确认并要求改色）、`reject_master`（说哪里要改）、
  `get_style_skill`（这个款式现在的规则）、`save_style_rule`（把要求记成长期规则）。
  新模式 `mode="master"` 会把「基准待确认 + 其余颜色进度」写进开场上下文，并附上基准图与原始上传图；
  系统提示词明确「不要替用户确认、不要催、一次只问一句、一次性要求不进规则」。
  - 回归：`python tools/check_assistant.py`

- **24 个款式全部建档，一款一套「skill」（9.5.0）**：客户给了《菲律宾-服装款式代号表》
  （24 个款式 sheet + 目录，112 个色号，每款每色一张官方效果图），要求「整合到每个款式的 skill 里去」。
  以前款式库只有 10 款 49 色，且**只按上衣建模**（袖长 + 袖口工艺两个维度）。
  现在：
  1. **建档 24 款 / 112 色**：`tools/import_ph_catalog.py` 一次解析整张表 —— 旧款式补色号、新款式建档
     （`phrase` / `fit` / 袖长 / 袖口 / 品类 / 别名 / 英文代号），每色效果图落 `library/effects/<slug>/<色号>.jpg`，
     缩略图落 `library/refs/<slug>/<label>.jpg`，hex 从效果图面料区取样。**重跑幂等**（`--dry-run` 先看计划）。
  2. **新增「品类」（`kind`）维度**：`top` / `vest`（背心、挂脖）；`bottom`（下装）的能力保留，但这批 24 款里没有裤子，不建档。
     背心没有袖子、短裤没有袖口 —— 以前那些「袖长 / 袖口平折边」的句子套上去就是自相矛盾，
     模型会照着补一只袖子。现在 `prompts.py` 按品类换整段：
     `SLEEVE_NONE_CLAUSE`（袖笼包边 + 肩带）、`LEG_SHORTS_CLAUSE`（腰头 + 两条裤腿，不是上衣）、
     `CUFF_HEM_VEST_CLAUSE` / `CUFF_HEM_BOTTOM_CLAUSE`（袖笼/下摆、腰头/裤脚）。
  3. **17 个新款式各写一段「结构细节」**（`tools/build_style_prompts.py` 的 `DETAIL`）：
     插肩双色（11/14 身子白、袖子和领口才是这个色）、露脐（05/17/26）、弧形下摆（12）、
     U 领（19）、挂脖（18）、水墨面料（31）、酸洗做旧（20）、牛奶丝丝光（04）、
     纯棉哑光（01/09）、运动短裤白色滚边（16）、中短裤抽绳口袋（15）。
     phrase 里说不清的结构，必须单独点名，否则模型只会「换个颜色」。
  4. **顺手修掉一个老病句**：款式转换段原来是 `it must not gain no hood, no drawstrings…`
     （字面意思是「必须长出帽兜」），现在改成 `none of these may appear on it: …`。
  5. **界面**：24 款下拉按品类分组（`<optgroup>` 上衣/背心）；背心不再标「平口」，
     改标「无袖」，免得运营以为要罗纹。
  - 导表：`python tools/import_ph_catalog.py`（`--dry-run` 只看计划）
  - 生成提示词：`python tools/build_style_prompts.py --all`（缺的才写，`--force` 覆盖）
  - 看某款最终提示词：`python tools/show_prompt.py 16-sports-shorts black`
  - 回归：`python tools/check_catalog.py`（24 款/112 色/建档/提示词/品类/细节/回退 七组断言）

- **每个款式一套专属提示词（9.4.9）**：以前所有款式共用一套「按属性拼装」逻辑（袖长/袖口/帽兜分支），
  改一个款会连带影响别的款，属性组合也会爆炸。现在拆成两层：
  **共享骨架**（代码：图案锁 / 材质锁 / 效果图与色卡分工 / 主体规则）+
  **款式专属核心**（`library/prompts/<slug>.json` 的 `positive` / `negative`，一款一份，可独立编辑）。
  没文件的款式自动回落到按属性拼装，所以新款式可以先跑再补。9.5.0 起已生成 30 份（27 业务款 + 3 个 skill 通用款）。
  - 看覆盖：`python tools/build_style_prompts.py --list`
  - 新款式：`python tools/build_style_prompts.py --new <slug>`，再改 `library/prompts/<slug>.json`
  - 回归：`python tools/check_style_prompts.py`（覆盖/生效/隔离/新增/回退）

- **派生色改用「面料色卡」（9.4.8）**：飞书 1526~1542 出现「基准色对、个别派生色跑款」
  （21 童装索罗娜·黑色 变成了带帽卫衣）。根因是派生色还在发整张效果图 —— 效果图带版型，
  模型偶尔会把版型也一起搬过来。
  现在 `skillbridge/swatch.py` 从效果图裁一块 **256×256 纯面料色卡**（按 sha256 缓存）：
  基准色发「原图+效果图」，派生色发「基准成品+面料色卡（+原图）」，提示词用 `SWATCH_CLAUSE`
  明确「色卡只取颜色、不含任何版型」。
  真机对照 `data/test/style-drift/`，回归 `tools/check_swatch.py`。

- **帽绳要同色系（9.4.7）**：客户说「连帽卫衣的带子容易串」= **颜色串色**（黑卫衣画出白帽绳），
  不是绳子交叉。连帽款现在注入 `HOOD CORD COLOUR`（放在提示词靠前）：
  绳色必须跟**本件衣身**同色系（黑卫衣→黑绳、白卫衣→米白绳），
  并明确「源图那根绳的颜色属于它自己的旧色系，必须被本批次颜色替换」；
  否定词禁止跳出衣身色系/黑卫衣白绳/串到别的颜色。
  一次到位、派生、只换色、两段式第一阶段都覆盖；非连帽款继续写「no drawstrings」。
  真机对照 `data/test/hood-cords/`，回归 `tools/check_hood_cords.py`。

- **款式转换段 + 负面约束真正生效（9.4.5）**：2026-11-10 运营反馈「圣诞卫衣 promo 图改成 25 长袖童装，
  出图还是卫衣、袖口还是罗纹」。查出两个真问题：
  1. **negative 从来没发出去**：65535/a6api/fal 这类接口没有 negative_prompt 字段，
     adapter 里 `supports_negative=False` 就把那 3~4KB 否定词整段丢掉了（skill 原文要求合并到主提示词末尾）。
     现在请求边界（`runner._guarded_call`）会判断接口能力，不支持就用 `prompts.with_negative()` 合并进提示词。
  2. **「换款式」没说清**：新增 `CONVERT THE GARMENT TYPE` 转换段（`prompts.convert_clause()`）写在提示词靠前：
     平口款 → "源图若有罗纹袖口/松紧收口/罗纹下摆全部去掉，改成同布平折边"；罗纹款 → 反过来要求补罗纹带；
     并列出该款式不许出现的部件。一次到位、两段式第一阶段、改图都注入。
     改图里还新增 `garment_change` 意图：命中就把「保持原服装类型/剪裁/罗纹袖口」从 KEEP 列表撤掉。
  真机对照见 `data/test/kid-cuff/compare-3.png`；回归 `tools/check_request_boundary.py` + `tools/check_cuff.py`。
- **款式锁 `GARMENT TYPE LOCK`（9.4.3）**：飞书改款记录 1382~1390 出现「基准色对、派生色变成卫衣」。
  根因是**派生色**（跟随基准只改颜色）的提示词里既没有版型描述、也没有 no hood —— 模型只被要求
  「别动形状、改颜色」，一部分颜色就自己发挥成帽兜卫衣。现在基准/派生/只换色/两阶段/返工统一注入
  `GARMENT TYPE LOCK`：写明目标款式短语 + 按款式列出不许出现的部件（非卫衣 → no hood / no drawstrings /
  no kangaroo pocket；平口 → no rib-knit cuffs；短袖 → no long sleeves），否定词同步追加；
  卫衣款式自动不加 no hood。真机用「连帽卫衣原图 → 24 长袖 T 恤」验证通过，
  回归 `python tools/check_garment_lock.py`。
- **原图 + 效果图（8.0.0）**：款式表《墨西哥-服装款式代号表.xlsx》里每个款式每个颜色都有一张
  **效果图**（1500×1500 白底官方平铺产品图）。现在生图**必定带着效果图**：
  1. 导入：`python tools/import_style_catalog.py`（读 `library/source/*.xlsx`，把每色效果图落到
     `library/effects/<款式>/<色号>.jpg`，并在 `library/styles.json` 的每个颜色写 `effect` 字段；
     已导入 10 款式 49 色，见 `library/effects/manifest.json` 记录的源文件 sha256）；
  2. 角色分工 `prompts.EFFECT_CLAUSE`：**IMAGE 1 = 客户原图**（场景/机位/光线 + 印花、文字、logo、刺绣
     是不可动的资产）；**IMAGE 2 = 该色效果图**（版型、肩线、衣宽、下摆、袖长袖型、领型、罗纹/平口工艺、
     面料克重垂感、缝线、精确布色都以它为准）。明确禁止借用效果图的印花/logo/人物/白底；
  3. 同款其余颜色 `prompts.MASTER_EFFECT_CLAUSE`：**IMAGE 1 = 基准成品**（版型+印花+场景），
     **IMAGE 2 = 该色效果图**（只管颜色）—— 先出第一个颜色当基准，其余颜色只换布色，
     同款各色版型天然一致，不再靠开关；
  4. 界面**没有**「同款版型」这个选项了（一致性是内部固定行为）；要退回「每色独立生成」可传
     `shape_lock=off`（接口/命令行）；
  5. 接口只支持单图（`max_images<2`）或该色缺效果图时，自动退回「原图 + 颜色参考图」，提示词也不再提两张图。
  **光带图不够，还要逼模型重塑版型**（实测三条坑，8.0.2 修掉）：
  - 只说「版型以效果图为准」→ 模型照样把原图的宽松大落肩搬过来。必须写 `RESHAPE, DO NOT RECOLOUR`：
    原图的 fit/肩线/袖长袖型/下摆**不许保留**，并且给出**具体版型词**（`styles.json` 的 `fit` 字段，
    如 10TLDX = `slim, body-hugging … short fitted sleeves that hug the upper arm …`）；
  - `input_fidelity=high` 会把原图版型钉死：**带效果图的首轮自动关掉**（派生任务仍为 high 保形）；
  - 平口袖口那段原来的「the sleeve stays at full width all the way to its edge」会跟紧身款打架，
    带效果图时换成 `CUFF_PLAIN_CLAUSE_REF`（只说折边、不提袖宽）。
  对照图：`python tools/make_shape_compare.py` → `data/test/effect-ref/shape-fit-compare.png`
  （效果图 / 修之前照搬原图版型 / 修之后贴合效果图）。
  回归：`python tools/check_effect_ref.py`（离线）；真链路 3 色 `python tools/check_effect_ref_real.py --yes`
  （≈¥0.02，出 `data/test/effect-ref/effect-ref-real.png`）。
- **同款版型兜底 `shape_lock`（7.9.0，界面已移除）**：没有效果图的款式仍可退到「先出基准色再改色」
  （`SILHOUETTE_LOCK_CLAUSE`）；`python tools/check_shape_lock.py` 覆盖这条路径。
- **改图返工（rework）**：见 §3 末尾——把不满意的成品挑出来，用「本次需求 + 原始提示词」合成新提示词，并以该成品为参考图重跑，只影响挑中的那几张。改图的主体**继承源任务**（平铺图返工不会被顺手补个人上去）。
- **负面约束**：接口有 `negative` 字段就单独传（`supports_negative: true`），没有就合并到提示词末尾——和 skill 的要求一致。
- **颜色参考图 / 效果图**：`max_images >= 2` 时优先发「原图 + 该色效果图」，没有效果图才发「原图 + 参考色图」；
  只支持单图的接口靠提示词里的色值 + 颜色名约束，仍然可用。
- 每次任务实际发送的提示词可在界面点 **查看提示词**，或看 `job.json` 里每个 `task.prompt`。

### 印花失真的四层防线（实测总结）

模型改图时“重画 logo”是最常见的失真来源。按下面的顺序选手段：

1. **只换颜色 → 用「本地换色」接口**（`provider = local`）：完全不调模型，图案像素原样保留（见下一节）。
2. **需要改款，但图案重要 → 接口模型 + `input_fidelity=high` + 模式「仅换色」或「两段式」**，并尽量喂**原始分辨率**的原图；喂小图（例如几百像素宽）模型只能凭想象补细节，字形一定漂。
3. **图案质感仍不真实（像贴上去） → 用「改图」逐张返工**：把不可接受的那几张挑出来，写清需求（例：印刷颗粒保留、去掉贴纸感），只重跑这几张。
4. **仍不满意 → 先模型改款、再本地换色出全色**：先用模型把版型改对（一次），再用本地换色出全部颜色，第二次不再经过模型。

---

## 7.5 本地换色（免模型 · 图案零失真）

`provider = local`（配置里叫 `local`，类型 `local_recolor`）：**不调用任何接口、不花钱、秒出图**。

原理：按颜色把面料分割出来（高饱和面料用色相窗口 + 饱和度下限，近中性面料用 RGB 距离），
再只替换面料底色（保留原图明暗比例，所以褶皱、罗纹、纹理都还在），
而低饱和/极暗/极亮的**图案像素被整块排除并加了 2px 保护带**，因此印花、刺绣、立体贴布、领标都逐像素保留。

适用与不适用：

| 情况 | 本地换色 | 说明 |
|---|---|---|
| 款式已经对，只换颜色 | ✅ 最佳 | 图案零失真、版型零变化、免费 |
| 白底 + 换白色 | ⚠️ | 白衣服贴白背景会缺阴影，模型能补光影，本地不行 |
| 深色印在黑衣服上 | ⚠️ | 图案本来就看不清，任何方法都一样 |
| 要改款（长袖→短袖、加帽、改领型） | ❌ | 它会直接报错提示改用生图接口 |
| 图案与面料同色、半透明叠印 | ⚠️ | 分割可能吃边，建议用模型 + `input_fidelity=high` |

调参（`config.json → providers.local`）：`sat_ratio`（默认 0.62，面料与图案的饱和度分界）、`hue_tolerance`（色相窗口）、`halo_alpha`（图案边缘过渡带的重着色强度，默认 190）。
如果真的分割失败（面料占比 <3% 或 >94%），它会**直接报错而不是产出废图**，这时换用生图接口。

---

## 8. 输出与打包

完全复用 skill 的 `scripts/package_outputs.py`（子进程调用，规则不重复实现）：

- **按款式分层**（7.6.3 起）：ZIP 里每个款式一个文件夹，文件夹名 = 款式英文代号，
  如 `24-long-sleeve-style-recolor-batch/24CXCR/...`；同款式的所有颜色都在同一个文件夹里。
- **成品文件名 = `款式英文代号_色号编码[_补充]_批次号.ext`**：

  ```
  24CXCR_WH[0]_202611052219.png        # 24 长袖成人 · 白色 · 2026-11-05 那单
  03YLWY_FGY[0]_202611052219.png       # 03 圆领卫衣 · 花灰色
  24CXCR_WH[0]_item-02_202611052219.png  # 同款同色出现多张（多原图/多视角）才补这一段
  ```

  - 款式代号、色号编码都来自《墨西哥-服装款式代号表》（`styles.json` 的 `name_en` / `color_code`）：
    款式 `24#CXCR` → `24CXCR`，白色 `WH[0]`、黑色 `BK[0]`、花灰色 `FGY[0]` …
  - 批次号 = `YYYYMMDD` + job id 后 4 位，同一天多单不会撞名，重试仍是同一个名字（覆盖写）。
  - 文件名里只保留英文/数字/`[]`，空格和 `< > : " / \ | ? *` 会被去掉；界面上卡片右下角、
    单张下载的 `Content-Disposition`、ZIP 内路径、飞书「成品」列用的是同一个名字。
  - 兜底：款式表里没有的款式（skill 自带的 `short-sleeve` 等）用 `SHORT` / `03YLWY` 这类映射，
    颜色没有 code 时按通用色号表（`skill.COLOR_CODES`）换算，再不行退回 `label` 大写。
- 每张图**严格 < 2,000,000 字节**：小于就原样复制，超了才压（PNG 先最大压缩 + 减色，JPG/WEBP 先降质量，都不行才缩尺寸）；
- ZIP 保留目录层级；`work/` 中间图绝不进包；
- **三个下载入口结构一致**（都是 `<批次>/<款式代号>/<文件名>`，解压后同款式的图在一个文件夹里）：
  | 入口 | 走哪条路 | 备注 |
  |---|---|---|
  | 「下载 ZIP」 | skill 的 `package_outputs.py` 打包 `staging/` | 完整批次 |
  | 「批量导出 / 导出所选」 | `service.export_zip()` | 只导选中的，结构照旧分层；`fmt=jpg` 时用压缩版 |
  | 「飞书历史 → 打包下载」 | `feishu_export.build_zip()` | 按成品名里的款式前缀分文件夹，老记录进 `others/` |
- 有失败任务时按现有成品打包并在日志里标注；`package_outputs.py` 缺失/跑不起来时退化为内置 ZIP 并提示。

> **多视角不会互相覆盖**（7.6 修）：上传 `item-01.jpg` + `item-01_view-02.jpg` 时，落盘后第二个文件名只剩
> `view-02.jpg`，早期版本因此把它当成「同一个 item、无视角」，两张算出同一个任务 id、成品写进同一个文件，
> 最后只剩一张。现在 `job.view_of()` 认得单独的 `view-NN`，任务 id 区分视角、撞名时文件名中间补 `item-02` / `view-02`。

> **staging 的硬规矩**：`<job>/staging/` 下**只能有「每个款式一个目录」**（目录名 = 款式英文代号）。
> 缩略图、压缩版这类缓存一律写 `<job>/thumbs/`、`<job>/derived/`；
> 打包前还会 `clean_staging()` 兜底清一遍（`thumbs` / `derived` / `exports` / `.cache`），
> 因为 skill 的脚本会把 staging 里**每个子目录**当成一个 item 数图 —— 数不对就整批报错。
> 打包失败也不再让整批变 `failed`：图片都在，任务转 `partial`，单张下载 / 批量导出照样可用。

---

## 9. 命令行批量（不开浏览器）

```powershell
# 整个目录，短袖全 10 色，并发 6，接口用 gateway
python cli.py --input D:\拍摄\2026-11-03 --garment-type 短袖 --provider gateway --concurrency 6

# 指定颜色 + 两段式 + 6 个并发
python cli.py --input a.jpg b.jpg --garment-type 圆领卫衣 --colors black white --mode two_phase --concurrency 6

# 输出 JPG、独立重试 5 次、单次超时 300s
python cli.py --input D:\in --garment-type hoodie --format jpg --retries 5 --timeout 300
```

跑完会打印失败清单和 ZIP 路径，退出码 0 = 全部成功。

---

## 10. 自检

```powershell
python tools/selftest.py          # 离线全量自检（不需要 Key）
python tools/check_accounts.py    # 飞书账号表 / 定价表 / 密码哈希 / 会话签名
python tools/check_login_http.py  # 登录页、会话 Cookie、账号用量接口
python tools/check_account_e2e.py --sync   # mock 跑一单，验证「账号」写进飞书记录
python tools/check_real_job.py --base http://127.0.0.1:8787   # 真接口跑一单并核对用量（会花 ¥0.038）
python tools/check_backup_50.py --provider a6api --n 50       # 备用接口并发实测（50 张 ≈ ¥1.9，查会不会限流）
python tools/probe_quality.py --model gpt-image-2 --size 1024x1024   # 验证 quality 不影响分辨率/计价
python tools/check_incremental.py --base http://127.0.0.1:8789       # 10 张任务：验证轮询不会重复加载图片
python tools/check_rework_flow.py --base http://127.0.0.1:8789 <任务ID>  # 改图提交耗时 + 响应自带可渲染任务
python tools/check_package_thumbs.py      # 回归：staging 里有缓存目录时打包仍成功（离线）
python tools/check_thumb_package.py       # 端到端：临时端口 mock 跑一单，边跑边取图，验证打包不再失败
python tools/repair_job.py --dry-run      # 列出需要修复的历史任务（打包失败/缓存目录错位）
python tools/repair_job.py --all          # 搬回缓存目录 + 重新打包，能救回没 ZIP 的旧任务
python tools/check_price_table.py    # 打印飞书定价表现状（三位小数 / 分档）
python tools/check_cuff.py           # 袖口工艺 + 款式转换段回归（离线）
python tools/check_request_boundary.py  # 请求边界：不支持 negative 的接口要把否定词并进提示词（离线）
python tools/check_hood_cords.py     # 连帽卫衣帽绳：跟衣身同色系（离线）
python tools/check_swatch.py         # 派生色只发面料色卡（不带版型）（离线）
python tools/check_style_prompts.py  # 每个款式一套专属提示词：覆盖/生效/隔离（离线）
python tools/build_style_prompts.py --list   # 查看哪些款式已有专属提示词
python tools/check_no_person.py      # 主体约束回归：平铺图禁止补模特 / 真人图保留同一个人（离线）
python tools/check_no_person.py --live --source=library/refs/23-sorona-tee/white.jpg  # 真跑一张（≈¥0.006）看成品有没有人
python tools/import_style_catalog.py  # 从《墨西哥-服装款式代号表》导入每色效果图（library/effects）
python tools/check_effect_ref.py     # 原图+效果图回归：导入、图片顺序、提示词分工（离线）
python tools/check_effect_ref_real.py --yes   # 真链路 3 色（≈¥0.02）
python tools/check_shape_lock.py     # 版型锁定回归：同款多色 → 先出基准色、其余跟随（离线）
python tools/check_shape_lock_real.py --yes   # 真接口 A/B：每色独立 vs 版型锁定（6 张 ≈ ¥0.04）
python tools/check_cuff_real.py --yes # 同上但真接口出 2 张对照图（约 ¥0.08，看 data/test/cuff/real-cuffs.png）
python tools/feishu_account_admin.py list|add|passwd|del      # 飞书账号表维护
```

不需要 Key：验证 skill 解析、颜色扫描、一次到位与两段式的目录命名、失败重试、2MB 压缩、**并发峰值是否被限流**、本地换色图案保真、非法输入拦截。

---

## 11. 常见问题

| 现象 | 处理 |
|---|---|
| 接口显示「未配置」 | `.env` 里填对应变量名，或 `config.json` 里直接写 `api_key` |
| `403 this group is not served by this gateway` | Key 的图片分组不在这个 host：`base_url` 用 `https://sub-proxy-us.65535.space/v1`（不是 `api2.…`） |
| 模型拉不到 | 该网关不支持 `/models` 或 Key 无权限：工作台会自动回退上次快照 → `pricing.json` 清单，下拉不会空；也可手填模型名 |
| 模型下拉卡在「正在读取模型…」或 502/504 | 网关 `/models` 偶发 504（实测），已做两级回退；刷新即可 |
| 任务显示失败但网关控制台已出图 | 超时策略见 §6：**超时不自动重试**（避免重复计费），点该卡片「重试」按需补跑 |
| 模型单价不对 | 改飞书「模型定价」表（`单价元` 按次写数字，分档写 `1K档/2K档/4K档/其他档`；留空 = 未定价不计入预估）；飞书读不到时改 `pricing.json` |
| 打包失败 `contains N images; expected M` | 是缩略图/压缩版缓存被写进了 `staging/`，被 skill 打包脚本当成一个 item 数图。已修复（缓存改到 `<job>/thumbs`、`<job>/derived`，打包前还会兜底清理）；旧任务用 `python tools/repair_job.py --all` 搬回并重新打包 |
| 打包失败但图片都在 | 6.0 起不再整批判失败：任务变 `partial`，成品照样能单张下载 / 批量导出；日志里有失败原因 |
| 页面滑动不顺、掉帧 | 6.0 已去掉大面积 `backdrop-filter` 和极光层的 `filter: blur()`；如果还是卡，说明设备吃力 → 任务多时自动进入 `.perf-lite`，也可以在浏览器缩放小一点用 |
| 点了按钮像没反应 | 现在有顶部进度条 + 按钮转圈；如果长时间没动静，看「运行日志」和 `jobStage`（飞书写入、打包这类会在日志里留时间点） |
| 大量 429 / 5xx | 降并发（建议 3~6）、设 `rpm`、或把 `global_concurrency` 设成账号真实上限 |
| 401 / 403 | Key 或 `base_url` 不对；中转站务必确认 `/v1` 前缀与模型名 |
| 出图比例变了 | 用 `size=auto`，让接口跟随原图；固定 `1024x1024` 会裁掉比例 |
| 图案像"贴上去"、没有布料质感 | 模型把 logo 重画成了干净矢量图：用「改图」针对那几张返工（写清材质/颗粒要求）；只换颜色的单子直接用 local 本地换色；并确认喂的是高清原图 |
| 长袖 T 恤的袖口画成了卫衣罗纹（或卫衣丢了罗纹） | 7.6 起按款式强制区分：T 恤系 `plain`、卫衣系 `ribbed`，提示词与否定词都按款注入，界面也标 `平口/螺纹`。自检 `python tools/check_cuff.py`；真接口对照 `python tools/check_cuff_real.py --yes`。新款式记得在 `styles.json` 写 `"cuff"` |
| 图案被改了（字形/字距/立体感变了） | 这是模型在“重画”logo：改用「仅换色」模式 + `input_fidelity=high`，喂原始分辨率原图；只换颜色的单子直接用 `local` 本地换色 |
| 出图被压得很小 | 这是 skill 的 2MB 硬规则（`max_bytes` 可在 `config.json` 里调 `defaults`，或改 skill 的数值） |
| 端口占用 | `python serve.py --port 8899`（或改 `config.json → server.port`） |
| 双击 `run.bat` 窗口一闪而过 | 在命令行里跑 `run.bat` 看报错；常见是没装 Python 或 `pip` 装依赖失败（脚本会自动退到清华镜像） |
| 换台电脑后历史任务空了 | 正常：`data/` 是本地目录。用「历史任务 → 飞书历史」按记录回拉成品，或把旧机器的 `data/` 整个拷过来 |
| 成品文件名想自定义 | 7.6.3 起固定为 `款式英文代号_色号编码_批次号`（如 `24CXCR_WH[0]_202611052219.png`），ZIP 内按款式分文件夹。要换命名规则改 `skillbridge/job.py` 的 `output_stem()`；款式代号/色号来自 `styles.json` 的 `name_en` / `color_code` |
| 「并发数」去哪了 | 7.6.2 起去掉了：一次全推，超额的自动排队。要看排队情况看任务条上的橙色徽标「前方还有 xx 个任务」和设置里的「队列 x/15 · 排队 n 张」 |
| 任务卡住 | 界面点「取消」，或重启服务（历史任务会标记为中断，可重试失败项） |

---

## 12. 从 codex 改到工作台，快在哪

| | codex 对话式 | 本工作台 |
|---|---|---|
| 触发 | 每次贴规则 / 依赖模型记忆 | skill 解析一次，固定生效 |
| 并发 | 单线程、一次一张 | N 路并发 + 节流 + 重试 |
| 数量核对 | 人工数图片 | 自动按 N×M 展开、缺一张就标出来还能重试 |
| 打包 | 手工压 | 自动 2MB 校验 + 目录结构 + ZIP |
| 成本 | 难以控制 | 预估调用次数、失败立即止损、只重试失败项 |
| 复用 | 对话易丢失 | 历史任务 + `job.json` + CLI 可脚本化 |

---

## 13. 注意

- `.env`、`data/` 属于本地私密数据（含客户原图与成品），不要提交到仓库；`.gitignore` 已忽略。
- `data/` 会越来越大（原图 + 成品 + 缩略图/压缩版缓存）：定期跑 `python tools/cleanup_local_data.py --days 30`；
  删本地任务不影响飞书里的记录与附件，历史成品仍可从「飞书历史 · 打包下载」拉回。
- 原图请用你有权商用/改图的素材；接口返回的图片版权以对应服务条款为准。
- 每次调用都计费：先用 `本地模拟` 打通流程、用小批量（1 图 × 1 色）验证接口和提示词效果，再全量跑。
