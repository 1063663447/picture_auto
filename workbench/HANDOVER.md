# 生图工作台 · 项目交接文档

> 版本：**9.4.1 · 印墨与图案露底布料分离**（`web/index.html` 的 `?v=` 号）· 最后更新 2026-11-07
> 仓库：`https://github.com/1063663447/picture_auto`（分支 `main`）
> 目录：`E:\picture_auto`（`workbench/` = 工作台本体，`apparel-style-recolor/` = 生图 skill）
> 工作流修复沿用 [`CHANGELOG_WORKFLOW_9.4.0.md`](CHANGELOG_WORKFLOW_9.4.0.md)；当前图案补丁和分发版本为 9.4.1，旧包保留作历史，不包含最新修复。
> 9.4.1 针对圣诞人物腿部／图案周边黑块，区分真实黑墨与露底布料，允许原图纠正基准图黑块；专项及验证边界见 [`CHANGELOG_ARTWORK_9.4.1.md`](CHANGELOG_ARTWORK_9.4.1.md)。9.4.0 为上一版。

---

## 1. 这是什么

给服装电商用的**本地生图工作台**：上传一张衣服原图 → 选款式（10 个业务款＋3 个通用款）× 颜色 → 一次提交，
工作台把「客户原图 + 款式表里的官方效果图」一起发给图片模型，逐色出图、按款式分目录打包，
并记录到飞书多维表格（成品记录 / 账号 / 定价 / 并发队列）。

- 单个 FastAPI 服务 + 纯静态前端（无构建步骤），跑在本机 `127.0.0.1:8787`
- 后端约 **1 万行 Python**（`workbench/skillbridge/`），前端 `app.js` 约 2.6k 行
- 金额按人民币、3 位小数展示；当前工作台计价策略以接口配置为准，保留历史账本，不重新核价历史任务。

**现在能做的事**

| 功能 | 状态 |
|---|---|
| 多款式 × 多颜色批量出图（一份原图 → N 款 × M 色） | ✅ |
| 「原图 + 效果图」双参考，版型照官方效果图**重塑** | ✅ 8.0.x |
| 同款各色**版型一致**（先出基准色，其余基于它改色） | ✅ 内部固定行为 |
| 纯衣服平铺图**禁止生成人物**（主体判定 + 禁人约束 + 成品复查） | ✅ |
| 袖长/袖口工艺按款式钉死（T 恤平口 / 卫衣罗纹） | ✅ |
| 改图返工（挑成品 → 写需求 → 可换接口/换模型重画） | ✅ |
| 并发不限（a6api）／飞书表格当队列（65535 网关） | ✅ |
| 打包：按款式分文件夹、文件名 `24CXCR_WH[0]_20261105xxxx.png` | ✅ |
| 账号/配额（飞书账号表）、管理员看板（曲线/柱状图）、用量计费 | ✅ |
| 对话式需求沟通（DeepSeek agent + 流式输出，可看图） | ✅ |
| 飞书导出（成品记录表 + ZIP）、完成通知 | ✅ |

**9.0.0 正式网页**：纯黑＋深紫毛玻璃、屏幕平面粒子与微光、左右框对齐、异步上传预览、真实提交占位／闪卡显影、独立滚动画廊、完整大图与头像菜单。已保留本节全部业务；新增提交互斥、任务切换／轮询竞态、模型恢复和个人用量隔离修复。完整文件清单、真实单图费用、验证边界和回退说明见 [`CHANGELOG_BLACKGLASS_9.0.0.md`](CHANGELOG_BLACKGLASS_9.0.0.md)。

**9.1.0 调整**：主备接口角色命名与默认项、工作台计价策略、保留全部动效的滚动优化、项目完成总耗时、需求非空先走 harness、只有衣服的强优先级与主体透传。详见 [`CHANGELOG_BLACKGLASS_9.1.0.md`](CHANGELOG_BLACKGLASS_9.1.0.md)；9.0.0 中的默认接口／计价描述作为历史记录。

**9.1.1 修复**：改图 harness 同时读取原始上传图和当前成品，连续返工保留原图来源；普通生成／基准派生／返工保留同画布正反面及各自图案，修正尺寸提示与附件编号。已有真实在线对照和一张双面返工验证，见 [`CHANGELOG_BLACKGLASS_9.1.1.md`](CHANGELOG_BLACKGLASS_9.1.1.md)。

**9.2.0 正式主题与AI窗口**：采用用户选定的石墨银紫（主黑副紫），移除重复“需求沟通”入口；非空需求自动进入居中AI创作工作室，真实阶段、流式文字、工具记录、恢复会话和任务回执可见；空需求继续默认生成。详见 [`CHANGELOG_GRAPHITE_AI_9.2.0.md`](CHANGELOG_GRAPHITE_AI_9.2.0.md)。

**9.3.0 主体与分发**：原图模特在多色、基准派生、重试和连续返工中保持原任务主体；移除三处指定主题／计价文案。新增 Windows x64 内部便携包，自带 Python／依赖／Three.js 与已配置 API，双击自动检查、离线优先修复、国内源下载进度和自动打开。真实一张酒红色返工已恢复模特；详见 [`CHANGELOG_PORTABLE_9.3.0.md`](CHANGELOG_PORTABLE_9.3.0.md)。

---

## 2. 快速上手（接手第一件事）

```powershell
cd E:\picture_auto\workbench
.\run.bat                  # 首次会自动装依赖（fastapi/uvicorn/httpx/Pillow/python-multipart）
# 浏览器打开 http://127.0.0.1:8787  账号 admin / Asd123456.
# 想让同事在局域网里用：双击 run_share.bat（--share）
```

- Python：`C:\Users\admin\AppData\Local\Programs\Python\Python312\python.exe`（3.12，**没有 numpy/pandas**）
- 端口：默认 **8787**（`python serve.py --port 8899` 可换）；端口被占会起不来，先查占用
- 应急直连：`http://127.0.0.1:8787/?token=1_JiQgjmfST8`（跳过登录，只在本地救急用）
- 密钥都在 `workbench/.env`（**不进 git**）：`TERRA_API_KEY`（65535 网关）、`A6API_API_KEY`、
  `DEEPSEEK_API_KEY`、`FEISHU_APP_ID/SECRET`。模板见 `.env.example`
- **改完前端必须在 `web/index.html` 里把 `?v=` 号加一**，否则浏览器用旧缓存

---

## 3. 代码地图

```
workbench/
├── serve.py                 启动器（找端口、开浏览器、uvicorn.run）
├── cli.py                   命令行批量生图（不开浏览器）
├── config.json              所有配置：providers / defaults / 飞书 / 阀门
├── pricing.json             价格表（22 个模型，单位：元/张）
├── run.bat / run_share.bat  双击启动（GBK 编码，改的时候注意别存成 UTF-8）
├── skillbridge/             后端
│   ├── api.py      (1026)   HTTP 路由 + 登录 + SSE 对话流 + 预览
│   ├── service.py  (1672)   Workbench 门面：建任务 / 改图 / 导出 / 统计 / 账号
│   ├── runner.py    (935)   执行引擎：plan → 并发调度 → 写盘 → 打包
│   ├── prompts.py   (788)   全部提示词拼装（本项目的核心资产）
│   ├── job.py       (309)   Job/Task 数据模型 + 落盘 + 命名规则
│   ├── skill.py     (433)   skill 解析（SKILL.md）+ 款式/颜色数据类
│   ├── library.py   (165)   款式库 styles.json 加载（含效果图路径）
│   ├── subject.py   (200)   主体判定（有没有真人）+ 成品复查
│   ├── refine.py    (227)   改图提示词合成 + 需求整合
│   ├── feishu.py    (376)   飞书多维表格读写（记录/账号/定价）
│   ├── feishu_gate.py (547) 飞书表格当并发闸门 + 排队（15 个槽位行）
│   ├── feishu_export.py(192) 飞书导出打包（按款式分层）
│   ├── gate.py      (297)   门面：飞书闸门 / Cloudflare 阀门
│   ├── auth.py      (370)   登录、会话、账号权限
│   ├── pricing.py   (376)   计价 + 用量账本（支持按接口覆盖单价）
│   ├── agent.py     (421)   对话 agent（工具调用：改图/提交/看模型）
│   ├── harness.py   (315)   流式对话 + 需求改写
│   ├── packaging.py  (91)   2MB 压缩 + ZIP
│   └── providers/           接口适配：openai_compat / ark / gemini / fal / local_recolor / mock
├── web/                     前端（无构建）
│   ├── index.html           BLACK GLASS 正式首页（资源版本 9.0.0）
│   ├── app.js               真实业务交互、共享提交锁、任务轮询与模型选择
│   ├── blackglass-ui.js     展示模块：布局、显影、筛选、灯箱、头像菜单
│   ├── blackglass.css       正式业务适配与加载态
│   ├── blackglass/          本地视觉层、平面粒子、Three.js 依赖及授权
│   ├── admin.html/admin.js  独立管理看板（SVG 曲线/柱状图）
│   └── style.css / glass.css / aurora.css 旧基础与主题资源保留
├── library/                 款式库
│   ├── styles.json          10 款式 / 49 色（含 fit 版型描述、effect 效果图字段）
│   ├── refs/<slug>/<color>.jpg    颜色参考图（小色卡）
│   └── effects/<slug>/<码>.jpg    官方效果图（1500×1500，49 张，导入而来）
├── tools/                   50+ 个检查/运维脚本（见 §8）
├── data/                    **不进 git**：jobs/（每单一个目录）、drafts/、test/（验证产物）
└── .skills/                 可选的 ui-ux skill（不参与生图）
```

**一次生图的调用链**

```
浏览器 POST /api/jobs（multipart：files + selections + provider + subject …）
  → service.create_job()          建 Job + params，落地 data/jobs/<id>/
  → runner.plan()                 每个 (款式, 原图, 颜色) 一个 Task；判主体；排基准色
  → runner._run_single_pass()     ① 基准色（原图+效果图）② 其余色（基准成品+效果图）
  → provider.edit(EditRequest)    httpx → /v1/images/edits（multipart image[]）
  → runner._store()               staging/<款式英文码>/<款式_色号_批次>.png
  → packaging.package_outputs()   2MB 限制 + ZIP + 按款式分层
  → feishu.py                     写成品记录 / 扣账号用量 / 记价格
```

任务状态机：`pending → queued（排队）→ running（生成中）→ done | failed | skipped | canceled`

---

## 4. 必须懂的 8 个设计（改代码前务必看）

### 4.1 skill 是两层的
- `apparel-style-recolor/SKILL.md`：**生图规范原文**（版型描述、提示词模板、负面词、打包规则），
  `skill.load_skill()` 用 `> 引用块` 解析出 `prompt_template` / `negative_prompt`
- `workbench/library/styles.json`：**款式库叠加层**（10 个业务款式的代号、颜色、版型短语、袖型/袖口、fit、effect），
  `library.load_library()` 合并进 `skill.types`（同 slug 以 skill 为准）
- 本工作台实际用的是「款式库」那 10 个款式；skill 自带的 3 个通用款（短袖/圆领卫衣/连帽卫衣）仍可用

### 4.2 提示词是「拼」出来的，不是一个字符串
`prompts.build_single_pass()` 的顺序：skill 模板 → 袖长 → 袖口工艺 → 色值锚点 →
**效果图角色说明** → 需求块（或原话）→ 图案锁 → 材质锁 → keep 清单 → 主体句 →（纯衣服时）禁人段。
负面词 `full_negative()` 单独拼。**任何一段改动都会影响出图，改前先跑 `tools/check_*.py`。**

### 4.3 「原图 + 效果图」双参考（8.0 起）
- 每个颜色都有一张官方效果图（`library/effects/<款式>/<色号>.jpg`，来自《墨西哥-服装款式代号表.xlsx》）
- 请求固定 `image[0]=原图/基准`、`image[1]=该色效果图`，提示词用 `EFFECT_CLAUSE` 讲清分工：
  **IMAGE 1 = 场景 + 印花（不可动资产）**，**IMAGE 2 = 版型 + 工艺 + 颜色（权威）**
- **三个坑（都踩过，别再踩）**
  1. 只说「版型以效果图为准」→ 模型照搬原图轮廓。必须 `RESHAPE, DO NOT RECOLOUR` + 具体 `fit` 词
  2. `input_fidelity=high` 会把原图版型钉死 → **带效果图的首轮自动关掉**（派生任务仍 high 保形）
  3. 平口袖口原句 "sleeve stays at full width" 与紧身款冲突 → 带效果图时用 `CUFF_PLAIN_CLAUSE_REF`
- 接口只支持单图（`max_images<2`）或缺效果图 → 自动退回「原图 + 颜色参考图」

### 4.4 同款各色一致性（内部固定行为，界面无开关）
第一个颜色 = 基准（原图 + 效果图）；其余颜色 = **基准成品 + 该色效果图**，
提示词用 `MASTER_EFFECT_CLAUSE`（版型/印花/场景照基准，颜色照效果图）。
基准色失败 → 自动退回原图生成 + 写警告。想关掉：`shape_lock=off`（接口/命令行）。

### 4.5 纯衣服禁止生成人物
`subject.detect()` 只用「皮肤证据是否缺席」这一个可靠方向：皮肤占比 ≈0 → `garment`，
否则按 `person`（中性提示词，绝不擦掉模特）。`garment` 时追加 `NO_PERSON_CLAUSE` + 20 余条否定词，
出图后 `subject.person_alert()` 拿原图背景做遮罩复查，发现「原背景变皮肤色」→ 任务记 `person_alert`。
界面「画面主体」可强制 `auto / garment / person`。**深酒红/正红色相不在皮肤区间，不会误判。**

### 4.6 并发与排队（两套，按接口开关）
- `unlimited: true`（当前 a6api）：不排队、不限并发，一次全推（可选 `batch_concurrency` 软上限）
- 其余（65535 网关等）：本机并发上限 `max_concurrency`（terra=20）+ **飞书闸门**（15 个永久槽位行，
  原子抢行 + 回读确认 + FIFO ticket），界面上显示「前方还有 N 个任务」
- **千万不要**给闸门表增删行；provider 对象按名字共享，**不要**在单任务里 `close()`

### 4.7 价格与用量
`pricing.json` 是默认价目；`providers.<name>.model_prices` 可按模型覆盖，`fixed_unit_price` 可按接口统一覆盖所有模型／档位，优先级最高。网页预估、harness、配额及新用量记录必须使用同一接口策略；历史账本保持原金额。
后台「今日 N 张 · ¥X」及账号汇总读取 `UsageLedger`。不要把上游实际价格重新覆盖工作台配置的接口级固定策略。

9.0.0 起 `/api/usage` 是**当前认证账号的个人用量**（管理员本人也只看自己）；今日／累计、recent、accounts 均同范围过滤。管理员全员汇总继续通过管理看板／账号接口读取，不要把个人抽屉绑定到全员定价用量。

### 4.8 命名与打包（客户明确要求）
- 成品名：`{款式英文码}_{色号}[_补充]_{YYYYMMDD+job尾4位}.png`，例 `24CXCR_WH[0]_202611052219.png`
- 目录：`staging/<款式英文码>/`，ZIP 内同样分层（**曾经平铺，客户投诉过**）
- 单图 ≥2MB 会压缩到严格小于 2MB

---

## 5. 接口一览（都在 `skillbridge/api.py`）

| 类别 | 接口 |
|---|---|
| 登录 | `GET/POST /login`、`POST /api/logout`、`GET /api/session` |
| 元数据 | `GET /api/health`、`/api/skill`、`/api/providers`、`/api/models`、`/api/pricing`、`/api/gate`、`/api/usage` |
| 草稿 | `POST /api/drafts` |
| 生图 | `POST /api/jobs`、`GET /api/jobs`、`GET /api/jobs/{id}`、`POST /api/jobs/{id}/{start,cancel,retry}` |
| 成品 | `GET /api/jobs/{id}/image/{task}`、`/thumb/{task}`、`/download`、`/export?tasks=&fmt=` |
| 改图 | `POST /api/jobs/{id}/rework`、`POST /api/rework/preview` |
| 参考图 | `GET /api/refs/{款式}/{文件}`、`GET /api/effects/{款式}/{色号}.jpg` |
| 对话 | `POST /api/harness/stream`（SSE，带 `provider` / `model` 走对话链） |
| 飞书 | `GET /api/feishu/records`、`POST /api/jobs/{id}/feishu`、`/api/feishu/export`、`/api/feishu/sync-pending` |
| 管理 | `GET /admin`、`/api/admin/overview`、`/api/accounts*` |
| 预览 | `POST /api/preview`（返回本轮完整提示词与负面词） |

---

## 6. 配置要点（`config.json`）

```jsonc
{
  "providers": {
    "a6api":  { "unlimited": true, "skip_gate": true, "max_concurrency": 0, "max_images": 4,
                "model_prices": { "gpt-image-2.5-sunburst": 0.005978 } },
    "terra":  { "max_concurrency": 20, "input_fidelity": "high" }   // 65535 网关，走飞书闸门排队
  },
  "defaults": {
    "subject": "auto",          // auto / garment（纯衣服，禁止加人）/ person
    "shape_lock": "master",     // 内部行为：先出基准色（界面上没有开关）
    "retry_on_person": false,   // 成品复查发现有人时是否自动加约束重画一次
    "gate": { "provider": "feishu", "limit": 15, "table_id": "tblYydJOE5r6KAow" },
    "harness": { "provider": "deepseek", "model": "deepseek-flash", "vision": true }
  }
}
```

- `ui.*` 控制界面显示哪些高级项（`hide_rpm` / `hide_mode_options` / `show_price` …）
- `defaults.notify`：飞书群机器人 webhook（出图完成通知，默认关）

---

## 6.5 备用接口（65535）的模型与报价

- 网关 `/v1/models` 现有 36 个 id、27 个图片模型；工作台只给有报价的 8 个显示单价，其余显示「未定价」。
- 2026-11-09 新增 **`gpt-image-2.6-flare` / `gpt-image-2.6-sunburst`，按次 ¥0.038/张**（报价口径，与 2.5 系列同价），
  已各跑 1 张真机验证（`data/test/model-26/`）。见 `CHANGELOG_MODELS_9.4.2.md`。
- **价格解析顺序：接口级 `model_prices` → 飞书「模型定价」表 → `pricing.json`**。
  飞书表存在时会把本地价目整份替换掉，所以新增模型**必须**在 `config.json` 的接口 `model_prices` 里也写一份，
  否则飞书刷新后会回落到「未定价」。
- 主接口 a6api 用 `fixed_unit_price=0.038` 覆盖所有模型；备用接口不设 fixed，按模型逐条给价。

## 7. 关键数据（实测，引用时注意日期 2026-11）

| 项 | 数字 |
|---|---|
| 当前工作台计价 | 以接口配置为准；旧上游成本数据仅作历史实测，不能用于覆盖当前策略 |
| a6api 单张耗时 | 1 并发 22.4s；10 并发 22–25s（wall 39s）；30 并发 p50 33 / p95 39.6（wall 75s）；100 并发 p50 51 / p95 84 / max 130（wall 246s） |
| 并发甜点 | 单批 **20–30 张**；再往上只是尾巴变长 |
| 版型漂移（修前） | 同款 3 色轮廓相似度只有 0.3–0.6 |
| 款式库 | 10 款式 / 49 色 / 49 张效果图（19MB） |

---

## 8. 自检与运维脚本（`tools/`，50+ 个，常用的这些）

**不花钱、改完必跑**

```powershell
python tools/selftest.py            # 离线全量自检（解析/命名/重试/压缩/多款式/改图/编码）
python tools/check_effect_ref.py    # 原图+效果图：导入、图片顺序、提示词分工、fit、fidelity
python tools/check_no_person.py     # 主体判定与禁人约束
python tools/check_cuff.py          # 袖口工艺（T 恤平口 / 卫衣罗纹）
python tools/check_shape_lock.py    # 同款多色基准派生
python tools/check_multi_style.py   # 多款式任务 + 导出分层
python tools/check_thumb_package.py # 打包不再被缓存目录搞挂
python tools/check_gate.py / check_feishu_gate.py   # 并发闸门 / 队列
python tools/check_blackglass_static.py # 正式资源、DOM、业务路由、无模拟接线
python tools/check_usage_scope.py       # 临时双账号账本／认证 HTTP，用量隔离
```

**会花钱（真接口，用前确认）**

```powershell
python tools/check_effect_ref_real.py --yes            # 3 色 ≈ ¥0.02，验证版型贴合效果图
python tools/check_no_person.py --live --source=...    # 1 张 ≈ ¥0.006
python tools/check_cuff_real.py --yes                  # 2 张 ≈ ¥0.08
python tools/check_real_job.py --base http://127.0.0.1:8787   # 跑一单并核对用量
python tools/check_backup_50.py --provider a6api --n 50       # 50 并发实测
```

**运维**

```powershell
python tools/import_style_catalog.py     # 从 library/source/*.xlsx 重新导入效果图
python tools/repair_job.py --dry-run     # 找打包失败/缓存目录错位的历史任务（--all 修）
python tools/cleanup_local_data.py       # 清理 data/ 里的旧任务
python tools/feishu_account_admin.py list|add|passwd|del   # 飞书账号表维护
python tools/show_library.py             # 打印款式库现状
python tools/make_shape_compare.py       # 生成版型 A/B 对照图
```

> 每个 check 脚本失败时会打印 `[x] 断言的肯定句描述`，按那句话找代码即可。

---

## 9. 已知限制 / 待办

1. **效果图只覆盖 10 个款式**（款式表导入），skill 自带的通用款（短袖/圆领卫衣/连帽卫衣）没有效果图，
   走的是颜色参考图老路径。
2. **`fit` 字段是人工按效果图填的**（`library/styles.json`），若某款描述不准，直接改这个词，
   提示词会立刻跟着变。理想做法是让模型读效果图自动生成版型描述。
3. **版型重塑仍受模型上限**：紧身款偶尔还会画出略宽松的袖子（比修前好很多，但不是 100% 一致）；
   需要像素级一致时用 `provider=local`（本地换色，零漂移、免费，见 README §7.5）。
4. **主体判定保守**：只断言「皮肤缺席=纯衣服」，暖白/米色平铺会被当成 person（不影响出图，只是少禁人）。
5. 100 并发时 p95 会到 84s、尾巴 130s；飞书表格 2s 轮询下，队列数字有轻微延迟。
6. `data/` 目录不进 git，换机器要自己带上（或重新跑任务）。
7. 前端无构建、无类型检查；`app.js` 已经 100KB+，再涨建议拆模块。

---

## 10. 常见故障 → 处理

| 现象 | 原因 / 处理 |
|---|---|
| 页面 404 / 打开是别的服务 | 8787 被占（历史上被无关脚本占过）。`Get-NetTCPConnection -LocalPort 8787` 查 PID → 杀 → 重开 `run.bat` |
| 改了前端没生效 | `index.html` 里 `?v=` 没加一；按 Ctrl+F5 |
| 中文乱码（脚本/README） | `run.bat`/`run_share.bat` 是 **GBK**；Python/JSON/Markdown 是 UTF-8。**别用 PowerShell `Get-Content\|Set-Content` 改非 ASCII 文件** |
| 控制台中文乱码但文件没问题 | 临时 `$env:PYTHONIOENCODING="utf-8"` |
| git push "Connection was reset" | 网络抖动，重试 3–5 次即可（`for` 循环重试已用过） |
| 出图卡在「排队中」不动 | 飞书闸门表没有空槽（15 行全占）或有陈旧租约；`python tools/feishu_gate_status.py` 看状态，>600s 的租约会被回收 |
| 图片被改成了模特图 | 原图是纯衣服但没判成 garment：界面把「画面主体」设成 `只有衣服` |
| 同款各色版型不一 | 确认 `defaults.shape_lock` 没被改成 `off`；看卡片角标有没有「基准版型/跟随基准」 |
| 改图发到了别的接口 | 已修（8.0 前对话通道写死默认接口）；现在看 `job.json → params.provider` 即可确认 |

---

## 11. 交接清单（拿到项目后按顺序做）

1. 拿密钥：`workbench/.env`（65535 网关、a6api、DeepSeek、飞书 AppID/Secret），或向原负责人索取
2. `.\run.bat` 起服务，登 `admin / Asd123456.`，跑 `python tools/selftest.py` 确认全绿
3. 打开「查看本轮提示词」，读一遍 `prompts.py` 拼出来的完整提示词（这是最容易出问题的地方）
4. 用一个 1 款 1 色的小单（≈¥0.006）走通：上传 → 提交 → 成品 → 下载 → 飞书记录
5. 读本文 §4 的 8 个设计，特别是 §4.3（效果图三个坑）和 §4.6（并发/闸门）
6. 需要改行为时：先改 `prompts.py` / `config.json` → 跑对应 `check_*.py` → 再真接口小样本验证

---

## 12. 近期版本（git log 摘要）

| 提交 | 内容 |
|---|---|
| `9.0.0（本轮工作区，未提交）` | BLACK GLASS 正式网页改造、交互性能与提交／轮询安全、个人用量账号隔离；见独立改动记录 |
| `9d9d6f6` | 效果图真正决定版型（RESHAPE + fit + 首轮关 `input_fidelity`） |
| `e331935` | 修 UI：款式面板不再被效果图缩略撑变形 |
| `046b961` | 生图改为「原图 + 效果图」双参考，版型一致性内置、去掉界面开关 |
| `2a10907` | 同款多色版型锁定（先出基准色，其余派生） |
| `f7f39e4` | 纯衣服不再被生成出人物（主体判定 + 禁人 + 成品复查） |
| `bd620f4` | 改图按对话框里选的接口/模型走 |
| `75a9bc3` | 按后台真实单价计账 + 解释「后台 48s、界面等 100 多秒」 |
| `a519d38` | a6api 接入为「不限并发、不排队」的备用接口 |
| `99cb634` / `39e98d6` | 打包按款式分层 + 成品名改成款式表英文编码 |
| `c1ef4b1` | 去掉「并发数」，默认全推 + 自动排队 |

完整历史：`git log --oneline`（678 次提交）。

---

## 13. 文档索引

- [`CHANGELOG_GRAPHITE_AI_9.2.0.md`](CHANGELOG_GRAPHITE_AI_9.2.0.md)：当前石墨银紫正式主题、AI动效弹窗、异步交互修复与回归结果
- [`CHANGELOG_BLACKGLASS_9.1.1.md`](CHANGELOG_BLACKGLASS_9.1.1.md)：原图／成品双来源、正反面构图保留、真实返工样本及验证边界
- [`CHANGELOG_BLACKGLASS_9.1.0.md`](CHANGELOG_BLACKGLASS_9.1.0.md)：当前六项需求调整、滚动优化数据、禁人物边界与回归结果
- [`CHANGELOG_BLACKGLASS_9.0.0.md`](CHANGELOG_BLACKGLASS_9.0.0.md)：本次正式网页改造文件清单、业务保留、真实接口与回归结果、启用／回退及已知限制
- `workbench/README.md`（85KB）：**主手册**，逐版本记录了做法、实测数据和踩坑过程，遇到细节先翻它
- `workbench/deploy/`：飞书接入、Cloudflare 隧道、闸门 Worker 的部署说明
- `apparel-style-recolor/SKILL.md`：生图规范原文（提示词模板 / 负面词 / 打包与核对规则）
- 本文件：`workbench/HANDOVER.md`
